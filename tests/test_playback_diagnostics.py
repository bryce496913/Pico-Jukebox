"""Playback exit isolation; virtual clocks do not validate physical timing."""
import io
import unittest
from unittest.mock import Mock
from support import Harness, Pin, wav_bytes, riff_chunk
from test_sdcard import ScriptedSPI


class PlaybackDiagnostics(unittest.TestCase):
    def start(self, samples=b'\xff' * 1701, **wav_options):
        self.samples = samples
        self.h = Harness(files={'A.wav': wav_bytes(samples, **wav_options),
                                'B.wav': wav_bytes(b'\x80')})
        self.m = self.h.boot()
        self.m['state'], self.m['index'] = 'PLAYING', 0
        self.h.logs.clear()
        self.before = len(self.m['speaker'].duties)
        return self.m

    def reports(self):
        return [dict(zip(log[::2], log[1::2])) for log in self.h.logs
                if log and log[0] == 'PLAYBACK EXIT:']

    def report(self):
        reports = self.reports()
        self.assertEqual(len(reports), 1)
        return reports[0]

    def color(self):
        led = self.m['led']
        return tuple(pwm.duties[-1] for pwm in (led.red, led.green, led.blue))

    def test_full_multichunk_song_reports_once_after_close_and_mute_then_advances(self):
        # >3 seconds of source PCM at 8000 Hz. No inference from virtual duration.
        m = self.start(bytes(range(256)) * 94 + b'\x80', extra=riff_chunk(b'JUNK', b'abc'),
                       trailing=riff_chunk(b'LIST', b'not audio'))
        callback = m['report_playback']
        def report_after_cleanup(*args):
            self.assertTrue(all(handle.closed for handle in self.h.handles))
            self.assertEqual(m['speaker'].duties[-1], 0)
            callback(*args)
        m['report_playback'] = report_after_cleanup
        self.h.run_loop()
        report = self.report()
        self.assertEqual(report['PLAYBACK EXIT:'], 'ended')
        self.assertEqual(report['CAUSE:'], 'eof')
        self.assertEqual(report['FILE:'], repr('/sd/A.wav'))
        self.assertEqual(report['STATE:'], 'PLAYING')
        self.assertIsNone(report['HARDWARE:'])
        self.assertIsNone(report['STORAGE:'])
        self.assertEqual(report['SAMPLES_EMITTED:'], len(self.samples))
        self.assertEqual(report['SAMPLES_TOTAL:'], len(self.samples))
        self.assertEqual(report['SAMPLES_REMAINING:'], 0)
        self.assertEqual(m['speaker'].duties[self.before:], [b * 10 for b in self.samples] + [0, 0])
        self.assertEqual((m['state'], m['index']), ('PLAYING', 1))
        self.assertEqual(self.color(), (0, 65535, 0))
        self.h.run_loop()
        self.assertEqual(len(self.reports()), 2)
        self.assertEqual(self.reports()[1]['FILE:'], repr('/sd/B.wav'))
        self.assertEqual((m['state'], m['index']), ('PLAYING', 0))

    def test_single_low_stop_sample_is_accepted_even_while_play_is_held(self):
        m = self.start()
        m['controls'].press('play')
        m['btn_play'].level = 0
        m['btn_stop'].value = Mock(side_effect=[1] * 1024 + [0] + [1] * 10)
        self.h.run_loop(3)
        report = self.report()
        # Characterize the existing immediate-contact limitation, not a claim
        # that electrical noise caused the user's physical failure.
        self.assertEqual(report['PLAYBACK EXIT:'], 'stopped')
        self.assertEqual(report['CAUSE:'], 'button')
        self.assertEqual(report['CONTROL:'], 'stop')
        self.assertEqual(report['STATE:'], 'STOPPED')
        self.assertEqual(report['SAMPLES_EMITTED:'], 1025)
        self.assertEqual(report['SAMPLES_REMAINING:'], len(self.samples) - 1025)
        self.assertIsNone(m['playback_exception'])
        self.assertEqual(self.color(), (65535, 0, 0))
        self.assertEqual(m['speaker'].duties[-1], 0)

    def test_stop_bounce_and_hold_cannot_start_additional_playback_attempts(self):
        m = self.start()
        m['btn_stop'].value = Mock(side_effect=[0, 1, 0, 1, 0, 0, 0, 0, 0])
        self.h.run_loop(8)
        self.assertEqual(self.report()['CONTROL:'], 'stop')
        self.assertEqual(m['state'], 'STOPPED')
        self.assertFalse(m['controls'].armed)
        self.assertEqual(self.color(), (65535, 0, 0))
        self.assertEqual(len(self.h.handles), 3)  # discovery x2, playback x1

    def test_pause_resume_counts_are_per_attempt_and_preserve_offset(self):
        m = self.start()
        m['btn_play'].level = 0
        self.h.run_loop()
        report = self.report()
        self.assertEqual((report['PLAYBACK EXIT:'], report['CONTROL:']), ('paused', 'play'))
        self.assertEqual(report['SAMPLES_EMITTED:'], 1)
        self.assertEqual(self.color(), (0, 0, 65535))
        m['btn_play'].level = 1
        m['controls'].released(self.h.clock.ticks_ms())
        self.h.clock.sleep_ms(30)
        m['controls'].released(self.h.clock.ticks_ms())
        m['btn_play'].level = 0
        self.h.logs.clear()
        before = len(m['speaker'].duties)
        self.h.run_loop()
        report = self.report()
        self.assertEqual(report['PLAYBACK EXIT:'], 'ended')
        self.assertEqual(report['START_OFFSET:'], 1)
        self.assertEqual(report['SAMPLES_EMITTED:'], len(self.samples) - 1)
        self.assertEqual(report['SAMPLES_REMAINING:'], 0)
        self.assertIsNone(report['CONTROL:'])  # no stale pause event
        emitted = [duty for duty in m['speaker'].duties[before:] if duty]
        self.assertEqual(emitted, [2550] * (len(self.samples) - 1))

    def test_track_change_reports_original_filename_and_navigation_control(self):
        for event in ('next', 'prev'):
            with self.subTest(event=event):
                m = self.start()
                m['btn_' + event].level = 0
                self.h.run_loop()
                report = self.report()
                self.assertEqual(report['FILE:'], repr('/sd/A.wav'))
                self.assertEqual(report['PLAYBACK EXIT:'], 'track_change')
                self.assertEqual(report['CONTROL:'], event)
                self.assertEqual(report['SAMPLES_EMITTED:'], 1)
                self.assertEqual((m['state'], m['index']), ('PLAYING', 1))

    def test_read_failure_and_premature_eof_are_errors_not_ended(self):
        for failure in (OSError('SD read timeout'), b'', None):
            with self.subTest(failure=type(failure).__name__):
                m = self.start()
                data = self.h.files['A.wav']
                class Broken(io.BytesIO):
                    def read(self, size=-1):
                        if self.tell() >= 44 + 512:
                            if isinstance(failure, Exception):
                                raise failure
                            return failure
                        return super().read(size)
                self.h.files['A.wav'] = lambda: Broken(data)
                self.h.run_loop(3)
                report = self.report()
                self.assertEqual(report['PLAYBACK EXIT:'], 'error')
                self.assertEqual(report['CAUSE:'], 'read_error')
                self.assertEqual(report['PHASE:'], 'read')
                self.assertEqual(report['SAMPLES_EMITTED:'], 512)
                self.assertEqual(report['SAMPLES_REMAINING:'], len(self.samples) - 512)
                self.assertIsNone(report['CONTROL:'])
                self.assertTrue(any(log[0] == 'WAV error:' for log in self.h.logs))
                self.assertEqual((m['state'], m['index']), ('STOPPED', 0))
                self.assertEqual(self.color(), (65535, 0, 0))
                self.assertEqual(m['speaker'].duties[-1], 0)
                self.assertTrue(all(f.closed for f in self.h.handles))

    def test_invalid_truncated_or_missing_song_reports_header_or_open_error(self):
        for replacement, phase in [(b'bad', 'header'), (wav_bytes()[:-1], 'header'),
                                   (wav_bytes(rate=16000), 'header'), (None, 'open')]:
            with self.subTest(phase=phase):
                m = self.start()
                if replacement is None:
                    del self.h.files['A.wav']
                else:
                    self.h.files['A.wav'] = replacement
                self.h.run_loop(3)
                report = self.report()
                self.assertEqual(report['CAUSE:'], 'wav_or_file_error')
                self.assertEqual(report['PHASE:'], phase)
                self.assertEqual(report['STATE:'], 'STOPPED')
                self.assertIsNone(report['SAMPLES_EMITTED:'])
                self.assertTrue(all(duty == 0 for duty in m['speaker'].duties[self.before:]))

    def test_output_exception_does_not_invent_an_exact_sample_count(self):
        m = self.start()
        m['pot'].read_u16 = Mock(side_effect=[65535] * 3 + [OSError('ADC read failed')])
        self.h.run_loop()
        report = self.report()
        self.assertEqual(report['PLAYBACK EXIT:'], 'error')
        self.assertEqual(report['PHASE:'], 'output')
        self.assertEqual(report['CAUSE:'], 'playback_error')
        self.assertIsNone(report['SAMPLES_EMITTED:'])
        self.assertIsNone(report['SAMPLES_REMAINING:'])
        self.assertIn('ADC read failed', report['EXCEPTION:'])
        self.assertEqual(m['speaker'].duties[-1], 0)

    def test_latched_hardware_fault_is_distinct_from_stop_or_sd_failure(self):
        m = self.start()
        m['led'].color = Mock(side_effect=OSError('RGB output failed'))
        self.h.run_loop(3)
        report = self.report()
        self.assertEqual(report['CAUSE:'], 'hardware_fault')
        self.assertEqual(report['HARDWARE:'], 'RGB ERROR')
        self.assertEqual(report['STATE:'], 'STOPPED')
        self.assertIsNone(report['CONTROL:'])
        self.assertEqual(report['SAMPLES_EMITTED:'], 0)
        self.assertEqual(m['speaker_pin'].level, 0)

    def test_soft_off_is_reported_without_an_eof_advance(self):
        m = self.start()
        sleep = self.h.clock.sleep_us
        def turn_off(us):
            m['slide'].level = 1
            sleep(us)
        self.h.clock.sleep_us = turn_off
        self.h.run_loop(3)
        report = self.report()
        self.assertEqual(report['PLAYBACK EXIT:'], 'interrupted')
        self.assertEqual(report['CAUSE:'], 'soft_off')
        self.assertEqual(report['STATE:'], 'STOPPED')
        self.assertEqual(report['SAMPLES_EMITTED:'], 512)
        self.assertEqual(m['index'], 0)
        self.assertEqual(self.color(), (0, 0, 0))

    def test_diagnostics_do_not_add_sample_clocks_prints_or_change_samples(self):
        for count in (1024, 17001):
            outcomes, clock_counts = [], []
            for enabled in (False, True):
                m = self.start(bytes(range(256)) * (count // 256) + b'\x80' * (count % 256))
                m['PLAYBACK_DIAGNOSTICS'] = enabled
                ticks = self.h.clock.ticks_ms = Mock(wraps=self.h.clock.ticks_ms)
                before_sleeps = len(self.h.clock.sleeps)
                self.h.run_loop()
                clock_counts.append(ticks.call_count)
                self.assertEqual(len(self.reports()), 1 if enabled else 0)
                outcomes.append((m['speaker'].duties[self.before:],
                                 self.h.clock.sleeps[before_sleeps:]))
            # Existing UI clocks are shared. Only two whole-attempt diagnostic
            # clocks are added, independent of the sample/chunk count.
            self.assertEqual(clock_counts[1] - clock_counts[0], 2)
            self.assertEqual(outcomes[0], outcomes[1])

    def test_diagnostics_off_preserves_exception_output(self):
        m = self.start()
        m['PLAYBACK_DIAGNOSTICS'] = False
        del self.h.files['A.wav']
        self.h.run_loop(3)
        self.assertEqual(self.reports(), [])
        self.assertTrue(any(log[0] == 'WAV error:' for log in self.h.logs))
        self.assertEqual(m['state'], 'STOPPED')

    def test_elapsed_wrap_and_missing_start_clock(self):
        m = self.start(b'\xff' * 1000)
        self.h.clock.us = ((1 << 30) - 5) * 1000
        self.h.run_loop()
        self.assertEqual(self.report()['ELAPSED_MS:'], 10)
        m = self.start()
        ticks = self.h.clock.ticks_ms
        failed = False
        def fail_once():
            nonlocal failed
            if not failed:
                failed = True
                raise OSError('diagnostic clock')
            return ticks()
        self.h.clock.ticks_ms = fail_once
        self.h.run_loop()
        self.assertIsNone(self.report()['ELAPSED_MS:'])
        self.assertEqual(self.report()['PLAYBACK EXIT:'], 'ended')


class SDDataClock(unittest.TestCase):
    def test_default_and_temporary_data_clock_preserve_read_protocol(self):
        for rate in (10_000_000, 1_000_000):
            with self.subTest(rate=rate):
                h = Harness()
                driver = h.load('sdcard.py')
                self.assertEqual(driver.SD_DATA_BAUDRATE_HZ, 10_000_000)
                driver.SD_DATA_BAUDRATE_HZ = rate
                spi, cs = ScriptedSPI(h.clock), Pin(13)
                card = driver.SDCard(spi, cs)
                self.assertEqual(spi.speeds, [100_000, rate])
                buffer = bytearray(1024)
                self.assertEqual(card.readblocks(3, buffer), 0)
                self.assertEqual(buffer, b'\x03' * 512 + b'\x04' * 512)
                self.assertEqual(cs.level, 1)
