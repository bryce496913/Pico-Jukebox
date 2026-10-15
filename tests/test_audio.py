"""Known PCM vectors and bounded controls; no timing/audio-fidelity claims."""
import unittest
from unittest.mock import Mock, patch
from support import Harness, wav_bytes, riff_chunk


class Audio(unittest.TestCase):
    def start(self, samples=b'\xff' * 700):
        self.h = Harness(files={'a.wav': wav_bytes(samples), 'b.wav': wav_bytes(b'\x80')})
        self.m = self.h.boot()
        self.m['state'], self.m['index'] = 'PLAYING', 0
        self.output = self.m['speaker']
        self.before = len(self.output.duties)

    def play(self):
        with patch('builtins.print'):
            return self.m['play_wav']('/sd/a.wav')

    def emitted(self):
        return self.output.duties[self.before:]

    def test_known_pcm_extremes_midpoint_and_gain_boundaries(self):
        # Golden legacy duties for minimum, below midpoint, midpoint, above and max.
        cases = [(0, [0, 0, 0, 0, 0]),
                 ((24 << 8) + 255, [0, 0, 0, 0, 0]),
                 (25 << 8, [0, 127, 128, 129, 255]),
                 (125 << 8, [0, 635, 640, 645, 1275]),
                 (65535, [0, 1270, 1280, 1290, 2550])]
        for adc, expected in cases:
            with self.subTest(adc=adc):
                self.start(bytes([0, 127, 128, 129, 255]))
                self.m['pot'].reading = adc
                self.assertEqual(self.play(), 'ended')
                self.assertEqual(self.emitted(), expected + [0])
                self.assertEqual(self.output.frequency, 40000)
                self.assertTrue(self.h.handles[-1].closed)

    def test_every_unsigned_sample_and_gain_step_stays_in_legacy_range_without_clipping(self):
        for gain in range(11):
            with self.subTest(gain=gain):
                self.start(bytes(range(256)))
                self.m['pot'].reading = (gain * 25) << 8
                self.assertEqual(self.play(), 'ended')
                expected = list(range(0, gain * 256, gain)) if gain else [0] * 256
                self.assertEqual(self.emitted(), expected + [0])
                self.assertTrue(all(0 <= duty <= 2550 for duty in self.emitted()))
                self.assertLess(max(self.emitted()), 65535)

    def test_pcm_silence_keeps_existing_midpoint_and_mute_remains_zero(self):
        self.start(b'\x80' * 17)
        self.assertEqual(self.play(), 'ended')
        self.assertEqual(self.emitted(), [1280] * 17 + [0])
        # Constant 0x80 is PCM silence. It is intentionally not the GPIO mute.
        self.start(b'\0' * 17)
        self.assertEqual(self.play(), 'ended')
        self.assertEqual(self.emitted(), [0] * 18)

    def test_adc_is_still_read_for_each_sample(self):
        self.start(b'\xff' * 5)
        self.m['pot'].read_u16 = Mock(side_effect=[0, 24 << 8, 25 << 8, 125 << 8, 65535])
        self.assertEqual(self.play(), 'ended')
        self.assertEqual(self.emitted(), [0, 0, 255, 1275, 2550, 0])
        self.assertEqual(self.m['pot'].read_u16.call_count, 5)

    def test_sample_io_order_and_sleep_contract(self):
        self.start(b'\x80\xff')
        events = []
        write = self.output.duty_u16
        def pwm(duty):
            events.append(('pwm', duty))
            write(duty)
        self.output.duty_u16 = pwm
        self.m['pot'].read_u16 = lambda: events.append(('adc',)) or 65535
        self.h.clock.sleep_us = lambda us: events.append(('sleep_us', us))
        for name in ('stop', 'play', 'next', 'prev'):
            self.m['btn_' + name].value = lambda name=name: events.append(('button', name)) or 1
        self.assertEqual(self.play(), 'ended')
        expected = []
        for level in (1280, 2550):
            expected += [('adc',), ('pwm', level), ('sleep_us', 10)]
            expected += [('button', n) for n in ('stop', 'play', 'next', 'prev')]
        self.assertEqual(events, expected + [('pwm', 0)])
        # This checks calls, not the duration of ADC/GPIO/Python/PWM operations.

    def test_eof_at_chunk_boundaries_has_no_duplicate_or_metadata_samples(self):
        for size in (1, 511, 512, 513, 1025):
            with self.subTest(size=size):
                samples = (bytes(range(256)) * 5)[:size]
                self.start(samples)
                self.h.files['a.wav'] = wav_bytes(samples, trailing=riff_chunk(b'LIST', b'not audio'))
                read = self.m['read_exact']
                payload_reads = []
                def record(stream, amount):
                    payload_reads.append(amount)
                    return read(stream, amount)
                self.m['read_exact'] = record
                self.assertEqual(self.play(), 'ended')
                self.assertEqual(self.emitted(), [b * 10 for b in samples] + [0])
                self.assertEqual(payload_reads, [512] * (size // 512) + ([size % 512] if size % 512 else []))
                self.assertTrue(self.h.handles[-1].closed)

    def test_stop_mid_chunk_mutes_without_reading_the_next_chunk(self):
        self.start()
        self.m['btn_stop'].value = Mock(side_effect=[1] * 16 + [0])
        read = self.m['read_exact']
        self.m['read_exact'] = Mock(wraps=read)
        self.assertEqual(self.play(), 'stopped')
        self.assertEqual(self.emitted(), [2550] * 17 + [0, 0])
        self.assertEqual(self.m['state'], 'STOPPED')
        self.assertEqual(self.m['read_exact'].call_count, 1)
        self.assertEqual(self.m['btn_stop'].value.call_count, 17)
        self.assertTrue(self.h.handles[-1].closed)

    def test_pause_mid_chunk_closes_file_and_mutes(self):
        self.start()
        self.m['btn_play'].value = Mock(side_effect=[1] * 8 + [0])
        self.assertEqual(self.play(), 'paused')
        self.assertEqual(self.emitted(), [2550] * 9 + [0, 0])
        self.assertEqual(self.m['state'], 'PAUSED')
        self.assertTrue(self.h.handles[-1].closed)

    def test_track_controls_mid_chunk_keep_playing_and_close_old_file(self):
        for button in ('next', 'prev'):
            with self.subTest(button=button):
                self.start()
                self.m['btn_' + button].value = Mock(side_effect=[1] * 11 + [0])
                self.assertEqual(self.play(), 'track_change')
                self.assertEqual(self.emitted(), [2550] * 12 + [0, 0])
                self.assertEqual((self.m['state'], self.m['index']), ('PLAYING', 1))
                self.assertTrue(self.h.handles[-1].closed)

    def test_normal_shuffle_retry_still_avoids_current_track(self):
        self.start()
        self.m['btn_next'].level = 0
        self.m['random'].getrandbits = Mock(side_effect=[0, 0, 1])
        self.assertEqual(self.play(), 'track_change')
        self.assertEqual(self.m['index'], 1)
        self.assertEqual(self.m['random'].getrandbits.call_count, 3)

    def test_stuck_shuffle_during_audio_is_bounded_mutes_and_stops(self):
        self.start()
        self.m['btn_next'].level = 0
        self.m['random'].getrandbits = Mock(return_value=0)
        self.assertEqual(self.play(), 'error')
        self.assertEqual(self.m['random'].getrandbits.call_count, 32)
        self.assertEqual(self.emitted(), [2550, 0, 0])
        self.assertEqual((self.m['state'], self.m['index']), ('STOPPED', 0))
        self.assertTrue(self.h.handles[-1].closed)

    def test_stuck_shuffle_at_eof_does_not_restart_or_hold_audio(self):
        self.start(b'\xff')
        self.m['random'].getrandbits = Mock(return_value=0)
        self.h.run_loop(3)
        self.assertEqual(self.m['random'].getrandbits.call_count, 32)
        self.assertEqual(self.m['state'], 'STOPPED')
        self.assertTrue(self.output.deinitialized)
        self.assertEqual(self.output.duties[-1], 0)
        self.assertTrue(self.h.handles[-1].closed)

    def test_midstream_adc_gpio_and_pwm_failures_do_not_leave_last_duty_active(self):
        for failure in ('adc', 'gpio', 'pwm'):
            with self.subTest(failure=failure):
                self.start()
                if failure == 'adc':
                    self.m['pot'].read_u16 = Mock(side_effect=[65535] * 3 + [OSError('ADC failure')])
                elif failure == 'gpio':
                    self.m['btn_stop'].value = Mock(side_effect=[1] * 3 + [OSError('GPIO failure')])
                else:
                    write = self.output.duty_u16
                    calls = 0
                    def fail(duty):
                        nonlocal calls
                        if duty:
                            calls += 1
                            if calls == 3:
                                raise OSError('PWM failure')
                        write(duty)
                    self.output.duty_u16 = fail
                self.assertEqual(self.play(), 'error')
                self.assertEqual(self.m['state'], 'STOPPED')
                self.assertEqual(self.output.duties[-1], 0)
                self.assertTrue(self.h.handles[-1].closed)


if __name__ == '__main__':
    unittest.main()
