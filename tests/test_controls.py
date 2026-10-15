"""Physical GPIO event sequences and state transitions under a virtual clock."""
import unittest
from unittest.mock import Mock, patch
from support import Harness, wav_bytes, riff_chunk


class Controls(unittest.TestCase):
    def setUp(self):
        self.samples = (bytes(range(256)) * 5)[:1200]
        self.h = Harness(files={name: wav_bytes(self.samples) for name in ('A.wav', 'B.wav', 'C.wav')})
        self.m = self.h.boot()
        self.m['index'] = 0
        self.m['update_display']()

    def poll(self):
        event = self.m['poll_controls']()
        if event is not None:
            self.m['apply_control'](event)
        return event

    def release(self):
        for name in ('stop', 'play', 'next', 'prev'):
            self.m['btn_' + name].level = 1
        self.poll()
        self.h.clock.sleep_ms(30)
        self.poll()

    def test_transition_table_and_ui_are_consistent(self):
        for state in ('STOPPED', 'PLAYING', 'PAUSED'):
            for event in ('stop', 'play', 'next', 'prev'):
                with self.subTest(state=state, event=event):
                    self.m['state'], self.m['index'] = state, 1
                    self.m['resume_path'], self.m['resume_offset'], self.m['resume_size'] = '/sd/B.wav', 10, 1200
                    self.m['random'].getrandbits = Mock(return_value=2)
                    self.m['apply_control'](event, '/sd/B.wav', 17, 1200)
                    expected = ('STOPPED' if event == 'stop' else
                                ('PAUSED' if state == 'PLAYING' else 'PLAYING') if event == 'play' else state)
                    expected_index = 2 if event == 'next' else 0 if event == 'prev' else 1
                    self.assertEqual((self.m['state'], self.m['index']), (expected, expected_index))
                    self.assertEqual(self.m['speaker'].duties[-1], 0)
                    self.assertEqual(self.m['lcd'].lines[0].rstrip(), expected)
                    led = self.m['led']
                    expected_rgb = {'STOPPED': [65535, 0, 0], 'PLAYING': [0, 65535, 0], 'PAUSED': [0, 0, 65535]}[expected]
                    self.assertEqual([p.duties[-1] for p in (led.red, led.green, led.blue)], expected_rgb)
                    if event in ('stop', 'next', 'prev') or (event == 'play' and state == 'STOPPED'):
                        self.assertIsNone(self.m['resume_path'])
                    if event == 'play' and state == 'PLAYING':
                        self.assertEqual(self.m['resume_offset'], 17)
                    if event == 'play' and state == 'PAUSED':
                        self.assertEqual(self.m['resume_offset'], 10)

    def test_bounce_and_long_hold_produce_only_one_next_action(self):
        self.m['random'].getrandbits = Mock(return_value=1)
        key = self.m['btn_next']
        key.level = 0
        self.assertEqual(self.poll(), 'next')
        for _ in range(5):
            key.level = 1
            self.poll()
            self.h.clock.sleep_ms(5)
            key.level = 0
            self.assertIsNone(self.poll())
        self.h.clock.sleep_ms(2000)
        self.assertIsNone(self.poll())
        self.assertEqual(self.m['index'], 1)
        self.assertEqual(self.m['random'].getrandbits.call_count, 1)
        self.release()
        self.m['random'].getrandbits.return_value = 2
        key.level = 0
        self.assertEqual(self.poll(), 'next')
        self.assertEqual(self.m['index'], 2)

    def test_release_bounce_restarts_qualification(self):
        key = self.m['btn_prev']
        key.level = 0
        self.poll()
        key.level = 1
        self.poll()
        self.h.clock.sleep_ms(29)
        self.poll()
        key.level = 0
        self.assertIsNone(self.poll())
        key.level = 1
        self.poll()
        self.h.clock.sleep_ms(29)
        self.poll()
        self.assertFalse(self.m['controls'].armed)
        self.h.clock.sleep_ms(1)
        self.poll()
        self.assertTrue(self.m['controls'].armed)

    def test_release_qualification_handles_tick_wrap(self):
        self.m['controls'].block()
        self.h.clock.us = ((1 << 30) - 10) * 1000
        self.poll()
        self.h.clock.sleep_ms(30)
        self.poll()
        self.assertTrue(self.m['controls'].armed)

    def test_simultaneous_controls_have_one_action_with_stop_priority(self):
        for name in ('stop', 'play', 'next', 'prev'):
            self.m['btn_' + name].level = 0
        self.m['state'] = 'PAUSED'
        self.assertEqual(self.poll(), 'stop')
        self.assertEqual(self.m['state'], 'STOPPED')
        self.m['btn_stop'].level = 1
        self.assertIsNone(self.poll())
        self.assertEqual(self.m['index'], 0)
        self.release()
        self.m['btn_play'].level = self.m['btn_next'].level = 0
        self.assertEqual(self.poll(), 'play')
        self.assertEqual((self.m['state'], self.m['index']), ('PLAYING', 0))
        self.m['btn_play'].level = 1
        self.assertIsNone(self.poll())

    def test_stop_remains_available_while_another_button_is_held(self):
        self.m['btn_next'].level = 0
        self.assertEqual(self.poll(), 'next')
        self.m['state'] = 'PLAYING'
        self.m['btn_stop'].level = 0
        self.assertEqual(self.poll(), 'stop')
        self.assertEqual(self.m['state'], 'STOPPED')
        self.assertIsNone(self.poll())

    def test_rapid_qualified_navigation_keeps_paused_state_and_resets_position(self):
        self.m['state'] = 'PAUSED'
        self.m['resume_path'], self.m['resume_offset'], self.m['resume_size'] = '/sd/A.wav', 33, 1200
        self.m['random'].getrandbits = Mock(side_effect=[1, 2])
        for event, index in [('next', 1), ('next', 2), ('prev', 1), ('prev', 0)]:
            self.release()
            self.m['btn_' + event].level = 0
            self.h.run_loop()
            self.assertEqual((self.m['state'], self.m['index']), ('PAUSED', index))
            self.assertIsNone(self.m['resume_path'])
            self.assertEqual(self.m['lcd'].lines[0].rstrip(), 'PAUSED')
            self.assertEqual(self.m['speaker'].duties[-1], 0)

    def test_pause_and_resume_at_every_chunk_edge_has_no_replay_or_skipped_samples(self):
        for pause_at in (1, 7, 511, 512, 513, 1199, 1200):
            with self.subTest(pause_at=pause_at):
                self.release()
                self.m['clear_resume']()
                self.m['state'] = 'PLAYING'
                # A non-44-byte data offset must not change bookmark semantics.
                self.h.files['A.wav'] = wav_bytes(self.samples, extra=riff_chunk(b'JUNK', b'abc'))
                count = 0
                sleep = self.h.clock.sleep_us
                def pause(us):
                    nonlocal count
                    if us == 10:
                        count += 1
                        if count == pause_at:
                            self.m['btn_play'].level = 0
                    sleep(us)
                self.h.clock.sleep_us = pause
                output = self.m['speaker']
                before = len(output.duties)
                self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'paused')
                first = output.duties[before:-2]
                self.assertEqual(first, [v * 10 for v in self.samples[:pause_at]])
                self.assertEqual(self.m['resume_offset'], pause_at)
                self.assertEqual(self.m['resume_size'], 1200)
                self.assertTrue(self.h.handles[-1].closed)
                self.h.clock.sleep_us = sleep
                self.release()
                self.m['btn_play'].level = 0
                self.assertEqual(self.poll(), 'play')
                before = len(output.duties)
                self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'ended')
                self.assertEqual(output.duties[before:-1], [v * 10 for v in self.samples[pause_at:]])
                self.assertIsNone(self.m['resume_path'])
                self.assertTrue(self.h.handles[-1].closed)

    def test_resume_failure_or_changed_payload_stops_and_discards_bookmark(self):
        for replacement in (None, wav_bytes(b'\xff' * 600), b'bad'):
            with self.subTest(replacement=replacement):
                self.release()
                self.m['state'] = 'PLAYING'
                self.m['btn_play'].level = 0
                self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'paused')
                self.release()
                self.m['apply_control']('play')
                if replacement is None:
                    del self.h.files['A.wav']
                else:
                    self.h.files['A.wav'] = replacement
                before = len(self.m['speaker'].duties)
                with patch('builtins.print'):
                    self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'error')
                self.assertEqual(self.m['state'], 'STOPPED')
                self.assertIsNone(self.m['resume_path'])
                self.assertTrue(all(v == 0 for v in self.m['speaker'].duties[before:]))
                self.h.files['A.wav'] = wav_bytes(self.samples)

    def test_off_from_each_state_clears_resume_and_on_never_autoplays(self):
        for state in ('STOPPED', 'PLAYING', 'PAUSED'):
            with self.subTest(state=state):
                self.m['state'] = state
                self.m['resume_path'], self.m['resume_offset'] = '/sd/A.wav', 99
                self.m['slide'].level = 1
                self.h.run_loop()
                self.assertEqual(self.m['state'], 'STOPPED')
                self.assertIsNone(self.m['resume_path'])
                self.assertEqual(self.m['lcd'].lines[0].rstrip(), 'POWER OFF')
                led = self.m['led']
                self.assertEqual([p.duties[-1] for p in (led.red, led.green, led.blue)], [0, 0, 0])
                writes = self.m['lcd'].writes[:]
                self.h.run_loop(3)
                self.assertEqual(self.m['lcd'].writes, writes)
                self.m['btn_play'].level = 0
                self.m['slide'].level = 0
                handles = len(self.h.handles)
                self.h.run_loop(3)
                self.assertEqual(self.m['state'], 'STOPPED')
                self.assertEqual(len(self.h.handles), handles)
                self.assertEqual(self.m['lcd'].lines[0].rstrip(), 'STOPPED')
                self.release()

    def test_off_during_read_prevents_any_buffered_audio(self):
        self.m['state'] = 'PLAYING'
        read = self.m['read_exact']
        def turn_off(stream, size):
            result = read(stream, size)
            self.m['slide'].level = 1
            return result
        self.m['read_exact'] = turn_off
        before = len(self.m['speaker'].duties)
        with patch('builtins.print'):
            self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'interrupted')
        self.assertTrue(all(v == 0 for v in self.m['speaker'].duties[before:]))
        self.assertEqual(self.m['state'], 'STOPPED')
        self.assertTrue(self.h.handles[-1].closed)

    def test_held_play_across_eof_cannot_pause_next_song(self):
        self.h.files['A.wav'] = wav_bytes(b'\xff')
        self.h.files['B.wav'] = wav_bytes(b'\x80')
        self.m['btn_play'].level = 0
        self.m['random'].getrandbits = Mock(side_effect=[1, 2])
        self.h.run_loop()
        self.assertEqual((self.m['state'], self.m['index']), ('PLAYING', 1))
        self.h.run_loop()
        self.assertEqual((self.m['state'], self.m['index']), ('PLAYING', 2))
        self.assertIsNone(self.m['resume_path'])

    def test_held_next_changes_only_once_while_playing_and_stop_can_interrupt_it(self):
        self.m['state'] = 'PLAYING'
        self.m['btn_next'].level = 0
        self.m['random'].getrandbits = Mock(return_value=1)
        self.h.run_loop()  # consume Next, select B
        self.assertEqual(self.m['index'], 1)
        count = 0
        sleep = self.h.clock.sleep_us
        def stop(us):
            nonlocal count
            if us == 10:
                count += 1
                if count == 17:
                    self.m['btn_stop'].level = 0
            sleep(us)
        self.h.clock.sleep_us = stop
        self.h.run_loop()
        self.assertEqual((self.m['state'], self.m['index']), ('STOPPED', 1))
        self.assertEqual(self.m['random'].getrandbits.call_count, 1)
        self.assertEqual(self.m['speaker'].duties[-1], 0)

    def test_idle_polling_has_no_quarter_second_debounce_sleep(self):
        self.m['btn_next'].level = 0
        before = len(self.h.clock.sleeps)
        self.h.run_loop()
        self.assertEqual(self.h.clock.sleeps[before:], [5000])
        self.assertEqual(self.m['state'], 'STOPPED')


    def test_paused_track_change_plays_selected_track_from_its_beginning(self):
        self.h.files['B.wav'] = wav_bytes(b'\xe0' * 20)
        self.m['state'] = 'PLAYING'
        self.m['btn_play'].level = 0
        self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'paused')
        self.assertEqual(self.m['resume_offset'], 1)
        self.release()
        self.m['btn_next'].level = 0
        self.assertEqual(self.poll(), 'next')
        self.assertEqual((self.m['state'], self.m['index']), ('PAUSED', 1))
        self.assertIsNone(self.m['resume_path'])
        handles = len(self.h.handles)
        self.h.run_loop(2)
        self.assertEqual(len(self.h.handles), handles)  # paused selection emits nothing
        self.release()
        self.m['btn_play'].level = 0
        self.assertEqual(self.poll(), 'play')
        before = len(self.m['speaker'].duties)
        self.assertEqual(self.m['play_wav']('/sd/B.wav'), 'ended')
        self.assertEqual(self.m['speaker'].duties[before:], [2240] * 20 + [0])

    def test_repeated_pause_positions_accumulate_from_the_resumed_offset(self):
        self.m['state'] = 'PLAYING'
        sleep = self.h.clock.sleep_us
        count = 0
        target = 7
        def pause(us):
            nonlocal count
            if us == 10:
                count += 1
                if count == target:
                    self.m['btn_play'].level = 0
            sleep(us)
        self.h.clock.sleep_us = pause
        self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'paused')
        self.assertEqual(self.m['resume_offset'], 7)
        self.release()
        self.m['apply_control']('play')
        target = 19
        self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'paused')
        self.assertEqual(self.m['resume_offset'], 19)
        self.assertEqual(self.m['state'], 'PAUSED')
        self.assertTrue(all(f.closed for f in self.h.handles))

    def test_release_is_qualified_during_audio_without_repeating_held_keys(self):
        self.m['state'] = 'PLAYING'
        self.m['controls'].block()
        sleep = self.h.clock.sleep_us
        count = 0
        def stop_after_release(us):
            nonlocal count
            if us == 10:
                count += 1
                if count == 2:
                    # A release clock check occurred at sample 1. Advance only
                    # virtual time, not production sample scheduling.
                    sleep(30000)
                if count == 40:
                    self.m['btn_play'].level = 0
            sleep(us)
        self.h.clock.sleep_us = stop_after_release
        self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'paused')
        self.assertEqual(self.m['resume_offset'], 40)
        self.assertEqual(self.m['state'], 'PAUSED')


    def test_rapid_playing_navigation_keeps_state_and_selects_correct_file(self):
        self.m['state'] = 'PLAYING'
        self.m['random'].getrandbits = Mock(side_effect=[1, 2])
        for event, index in [('next', 1), ('prev', 0), ('next', 2)]:
            self.release()
            self.m['btn_' + event].level = 0
            self.h.run_loop()
            self.assertEqual((self.m['state'], self.m['index']), ('PLAYING', index))
            self.assertEqual(self.m['lcd'].lines[1].rstrip(), self.m['tracks'][index][:-4])
            self.assertIsNone(self.m['resume_path'])
            self.assertTrue(all(f.closed for f in self.h.handles))
            self.assertEqual(self.m['speaker'].duties[-1], 0)

    def test_stop_after_pause_clears_position_before_replay(self):
        self.m['state'] = 'PLAYING'
        self.m['btn_play'].level = 0
        self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'paused')
        self.release()
        self.m['btn_stop'].level = 0
        self.assertEqual(self.poll(), 'stop')
        self.assertIsNone(self.m['resume_path'])
        self.release()
        self.m['apply_control']('play')
        before = len(self.m['speaker'].duties)
        self.assertEqual(self.m['play_wav']('/sd/A.wav'), 'ended')
        self.assertEqual(self.m['speaker'].duties[before:-1], [v * 10 for v in self.samples])

    def test_off_screen_keeps_power_state_visible_even_with_a_fault(self):
        self.m['hardware_error'] = 'ADC ERROR'
        self.m['slide'].level = 1
        self.h.run_loop()
        self.assertEqual(self.m['lcd'].lines[0].rstrip(), 'POWER OFF')
        self.assertEqual(self.m['lcd'].lines[1].rstrip(), 'ADC ERROR')
        self.assertEqual(self.m['state'], 'STOPPED')


    def test_off_at_eof_cleanup_takes_priority_over_auto_advance(self):
        self.h.files['A.wav'] = wav_bytes(b'\xff')
        self.m['state'] = 'PLAYING'
        self.m['random'].getrandbits = Mock(return_value=1)
        output = self.m['speaker']
        write = output.duty_u16
        seen_audio = False
        def off_on_mute(duty):
            nonlocal seen_audio
            if duty == 2550:
                seen_audio = True
            elif duty == 0 and seen_audio:
                self.m['slide'].level = 1
            write(duty)
        output.duty_u16 = off_on_mute
        self.h.run_loop()
        self.assertEqual((self.m['state'], self.m['index']), ('STOPPED', 0))
        self.assertEqual(self.m['lcd'].lines[0].rstrip(), 'POWER OFF')
        self.assertEqual(self.m['random'].getrandbits.call_count, 0)


if __name__ == '__main__':
    unittest.main()
