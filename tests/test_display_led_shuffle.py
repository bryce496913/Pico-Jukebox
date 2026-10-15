"""Display/LED/shuffle software contracts; virtual time is not audio timing."""
import unittest
from unittest.mock import Mock
from support import Harness


class Polish(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.m = self.h.boot()

    def title(self, name):
        self.m['tracks'] = [name]
        self.m['index'] = 0
        self.m['update_title_scroll'](True)

    def test_padding_and_portable_character_fallback(self):
        for text, expected in [('', ' ' * 16), ('Hi', 'Hi' + ' ' * 14),
                               ('x' * 30, 'x' * 16), ('é\n', '? ' + ' ' * 14)]:
            self.assertEqual(self.m['pad_lcd'](text), expected)
        name = 'A_é_🎵\nB.WAV'
        self.title(name)
        self.assertEqual(self.m['title_text'], 'A ? ? B')
        self.assertEqual(self.m['tracks'], [name])

    def test_short_and_exact_width_titles_stay_static_without_writes(self):
        for name in ('short.wav', 'abcdefghijklmnop.wav'):
            self.title(name)
            writes = len(self.m['lcd'].writes)
            for _ in range(10):
                self.h.clock.sleep_ms(1000)
                self.m['update_title_scroll']()
                self.m['update_title_scroll'](True)
            self.assertEqual(len(self.m['lcd'].writes), writes)
            self.assertEqual(self.m['title_scroll_pos'], 0)

    def test_long_title_interval_force_refresh_and_status_preserve_position(self):
        self.title('abcdefghijklmnopq.wav')
        self.h.clock.sleep_ms(349)
        self.m['update_title_scroll']()
        self.assertEqual(self.m['title_scroll_pos'], 0)
        self.h.clock.sleep_ms(1)
        self.m['update_title_scroll']()
        self.assertEqual(self.m['lcd'].lines[1], 'bcdefghijklmnopq')
        for state in ('PAUSED', 'PLAYING', 'STOPPED'):
            self.m['state'] = state
            self.m['update_display']()
            self.assertEqual(self.m['title_scroll_pos'], 1)
        self.h.clock.sleep_ms(10000)
        self.m['update_title_scroll']()
        self.assertEqual(self.m['title_scroll_pos'], 2)  # no catch-up burst

    def test_track_change_resets_and_formats_once_per_filename(self):
        original = self.m['clean_title']
        self.m['clean_title'] = Mock(side_effect=original)
        self.title('abcdefghijklmnopq.wav')
        for _ in range(100):
            self.h.clock.sleep_ms(350)
            self.m['update_title_scroll']()
        self.assertEqual(self.m['clean_title'].call_count, 1)
        self.title('ZYXWVUTSRQPONMLKJ.wav')
        self.assertEqual(self.m['title_scroll_pos'], 0)
        self.assertEqual(self.m['lcd'].lines[1], 'ZYXWVUTSRQPONMLK')
        self.assertEqual(self.m['clean_title'].call_count, 2)

    def test_long_ring_wrap_has_three_spaces_and_bounded_window(self):
        self.title('a' * 1000 + '.wav')
        ring = self.m['title_ring']
        self.assertEqual(len(ring), 1003)
        for pos in (0, 999, 1000, 1002, 1003):
            self.m['title_scroll_pos'] = pos
            expected = (ring + ring)[pos % len(ring):pos % len(ring) + 16]
            self.assertEqual(self.m['current_title_window'](), expected)

    def test_music_peak_scaling_vectors(self):
        self.m['state'] = 'PLAYING'
        for sample, brightness in ((128, 10), (127, 11), (129, 11), (0, 255), (255, 253)):
            self.m['music_brightness'] = None
            self.m['update_music_led'](bytes([sample]))
            self.assertEqual(self.m['led'].green.duties[-1], brightness * 257)

    def test_identical_chunks_only_change_green_once(self):
        self.m['state'] = 'PLAYING'
        self.m['update_led']()
        channels = (self.m['led'].red, self.m['led'].green, self.m['led'].blue)
        before = [len(p.duties) for p in channels]
        for _ in range(100):
            self.m['update_music_led'](bytes([160]) * 512)
        self.assertEqual([len(p.duties) - n for p, n in zip(channels, before)], [0, 1, 0])

    def test_small_fluctuations_accumulate_against_last_applied_brightness(self):
        self.m['state'] = 'PLAYING'
        self.m['update_music_led'](bytes([160]))  # 71
        self.m['update_music_led'](bytes([161]))  # 73, suppressed
        self.assertEqual(self.m['music_brightness'], 71)
        self.m['update_music_led'](bytes([163]))  # 76, applied
        self.assertEqual(self.m['music_brightness'], 76)
        self.m['update_music_led'](bytes([128]))
        self.assertEqual(self.m['music_brightness'], 10)
        self.m['update_music_led'](bytes([0]))
        self.assertEqual(self.m['music_brightness'], 255)

    def test_status_colors_and_music_cannot_override_pause_or_stop(self):
        led = self.m['led']
        for state, expected in [('PLAYING', (0, 65535, 0)), ('PAUSED', (0, 0, 65535)),
                                ('STOPPED', (65535, 0, 0))]:
            self.m['state'] = state
            self.m['update_led']()
            if state != 'PLAYING':
                self.m['update_music_led'](b'\0')
            self.assertEqual(tuple(p.duties[-1] for p in (led.red, led.green, led.blue)), expected)
        self.m['enter_off']()
        self.assertEqual(tuple(p.duties[-1] for p in (led.red, led.green, led.blue)), (0, 0, 0))

    def test_driver_failed_write_is_retried_and_repeated_colors_are_cached(self):
        led = self.m['led']
        led.color(0, 100, 0)
        before = len(led.green.duties)
        led.color(0, 100, 0)
        self.assertEqual(len(led.green.duties), before)
        write = led.green.duty_u16
        led.green.duty_u16 = Mock(side_effect=OSError('PWM'))
        with self.assertRaises(OSError):
            led.color(0, 101, 0)
        led.green.duty_u16 = write
        led.color(0, 101, 0)
        self.assertEqual(led.green.duties[-1], 101 * 257)

    def test_single_and_two_track_navigation_never_need_retry(self):
        draw = self.m['random'].getrandbits = Mock(side_effect=AssertionError('unexpected draw'))
        self.m['tracks'] = ['one.wav']
        self.assertEqual(self.m['random_track_index'](), 0)
        self.assertEqual(self.m['random_track_index'](0), 0)
        self.m['tracks'] = ['one.wav', 'two.wav']
        for current in (0, 1) * 100:
            self.assertEqual(self.m['random_track_index'](current), 1 - current)
        draw.assert_not_called()

    def test_all_eligible_slots_reachable_with_one_draw_and_no_repeats(self):
        for count in (3, 4, 10):
            self.m['tracks'] = ['song.wav'] * count
            for current in range(count):
                results = []
                for raw in range(count - 1):
                    draw = self.m['random'].getrandbits = Mock(return_value=raw)
                    results.append(self.m['random_track_index'](current))
                    draw.assert_called_once_with(16)
                self.assertEqual(set(results), set(range(count)) - {current})

    def test_boot_draw_range_and_large_library(self):
        for count in (2, 3, 65537):
            self.m['tracks'] = ['song.wav'] * count
            draw = self.m['random'].getrandbits = Mock(return_value=count - 1)
            self.assertEqual(self.m['random_track_index'](), count - 1)
            draw.assert_called_once_with(32 if count > 65536 else 16)

    def test_seed_uses_existing_micropython_apis(self):
        self.m['random'].seed = Mock()
        expected = self.h.clock.ticks_us() ^ self.m['pot'].read_u16() ^ len(self.m['tracks'])
        self.m['seed_random']()
        self.m['random'].seed.assert_called_once_with(expected)

    def test_previous_wraps_sorted_index_and_preserves_each_state(self):
        for state in ('STOPPED', 'PLAYING', 'PAUSED'):
            self.m['state'], self.m['index'] = state, 0
            self.m['apply_control']('prev')
            self.assertEqual((self.m['state'], self.m['index']), (state, 1))

    def test_random_failure_during_audio_mutes_closes_and_stops(self):
        self.m['tracks'] = ['A.WAV', 'b.wav', 'c.wav']
        self.m['index'], self.m['state'] = 0, 'PLAYING'
        self.m['random'].getrandbits = Mock(side_effect=OSError('random unavailable'))
        self.m['btn_next'].level = 0
        self.assertEqual(self.m['play_wav']('/sd/A.WAV'), 'error')
        self.assertEqual(self.m['speaker'].duties[-1], 0)
        self.assertEqual(self.m['state'], 'STOPPED')
        self.assertTrue(self.h.handles[-1].closed)

    def test_scroll_clock_wrap_and_same_track_navigation_reset(self):
        self.h.clock.us = ((1 << 30) - 100) * 1000
        self.title('abcdefghijklmnopq.wav')
        self.h.clock.sleep_ms(350)
        self.m['update_title_scroll']()
        self.assertEqual(self.m['title_scroll_pos'], 1)
        self.m['apply_control']('next')
        self.assertEqual(self.m['title_scroll_pos'], 0)
        self.assertEqual(self.m['lcd'].lines[1], 'abcdefghijklmnop')

    def test_random_failure_at_eof_releases_outputs_without_retry(self):
        self.m['tracks'] = ['A.WAV', 'b.wav', 'c.wav']
        self.m['index'], self.m['state'] = 0, 'PLAYING'
        draw = self.m['random'].getrandbits = Mock(side_effect=OSError('random unavailable'))
        speaker = self.m['speaker']
        self.h.run_loop(3)
        draw.assert_called_once_with(16)
        self.assertEqual(self.m['state'], 'STOPPED')
        self.assertEqual(speaker.duties[-1], 0)
        self.assertTrue(speaker.deinitialized)
        self.assertTrue(all(handle.closed for handle in self.h.handles))
