import unittest
from unittest.mock import patch
from support import Harness, Pin, Bus


class Baseline(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.m = self.h.boot()

    def test_boot_hardware_contract(self):
        m = self.m
        self.assertEqual([m[n].number for n in ('slide', 'btn_play', 'btn_stop', 'btn_next', 'btn_prev', 'cs')], [22, 16, 17, 19, 21, 13])
        for n in ('slide', 'btn_play', 'btn_stop', 'btn_next', 'btn_prev'):
            self.assertEqual((m[n].mode, m[n].pull), (Pin.IN, Pin.PULL_UP))
        self.assertEqual(m['speaker'].pin.number, 18)
        self.assertEqual(m['speaker'].frequency, 40000)
        self.assertEqual(m['speaker'].duties[-1], 0)
        self.assertEqual(m['pot'].number, 26)
        self.assertEqual(m['spi'].number, 1)
        self.assertEqual({k: m['spi'].kwargs[k].number for k in ('sck', 'mosi', 'miso')}, {'sck': 10, 'mosi': 11, 'miso': 12})
        self.assertEqual({k: m['spi'].kwargs[k] for k in ('baudrate', 'polarity', 'phase', 'bits', 'firstbit')}, dict(baudrate=1000000, polarity=0, phase=0, bits=8, firstbit=Bus.MSB))
        self.assertEqual(m['i2c'].number, 0)
        self.assertEqual([m['i2c'].kwargs[k].number for k in ('sda', 'scl')], [0, 1])
        self.assertEqual(self.h.mounts, ['/sd'])
        self.assertEqual(m['tracks'], ['A.WAV', 'b.wav'])
        self.assertEqual(m['state'], 'STOPPED')

    def test_lcd_format_and_scroll(self):
        m = self.m
        self.assertEqual(m['clean_title']('A_B.WAV'), 'A B')
        self.assertEqual(m['pad_lcd']('Hi'), 'Hi' + ' ' * 14)
        self.assertEqual(m['pad_lcd']('x' * 20), 'x' * 16)
        m['tracks'] = ['abcdefghijklmnopq.wav']
        m['index'] = 0
        m['reset_title_scroll']()
        m['update_title_scroll'](True)
        self.assertEqual(m['lcd'].lines[1], 'abcdefghijklmnop')
        self.h.clock.sleep_ms(349)
        m['update_title_scroll']()
        self.assertEqual(m['title_scroll_pos'], 0)
        self.h.clock.sleep_ms(1)
        m['update_title_scroll']()
        self.assertEqual(m['lcd'].lines[1], 'bcdefghijklmnopq')

    def test_random_avoids_current_and_single_track(self):
        self.assertEqual(self.m['random_track_index'](0), 1)
        self.m['tracks'] = ['only.wav']
        self.assertEqual(self.m['random_track_index'](0), 0)

    def test_rgb_mapping_and_music_peak(self):
        led = self.m['led']
        self.assertEqual([p.pin.number for p in (led.red, led.green, led.blue)], [5, 8, 9])
        led.white()
        self.assertEqual([p.duties[-1] for p in (led.red, led.green, led.blue)], [65535] * 3)
        self.m['state'] = 'PLAYING'
        self.m['update_music_led'](bytes([128] * 16))
        self.assertEqual(led.green.duties[-1], 10 * 257)
        self.m['update_music_led'](b'\0')
        self.assertEqual(led.green.duties[-1], 65535)

    def start(self):
        self.m['state'] = 'PLAYING'
        self.m['slide'].level = 0

    def test_playback_parses_header_scales_samples_and_closes_file(self):
        self.start()
        before = len(self.m['speaker'].duties)
        self.assertEqual(self.m['play_wav']('/sd/b.wav'), 'ended')
        self.assertEqual(self.m['speaker'].duties[before:], [0, 1280, 2550, 0])
        self.assertTrue(self.h.handles[-1].closed)
        # Pass 2 now guarantees local PWM cleanup as well as caller cleanup.
        self.assertEqual(self.m['state'], 'PLAYING')

    def test_playback_button_results_and_priority(self):
        for button, result, state in [('btn_stop', 'stopped', 'STOPPED'), ('btn_play', 'paused', 'PAUSED'), ('btn_next', 'track_change', 'PLAYING'), ('btn_prev', 'track_change', 'PLAYING')]:
            with self.subTest(button=button):
                self.start()
                self.m['index'] = 0
                self.m[button].level = 0
                self.assertEqual(self.m['play_wav']('/sd/b.wav'), result)
                self.assertEqual(self.m['state'], state)
                self.assertEqual(self.m['index'], 1 if 'track_change' == result else 0)
                self.assertEqual(self.m['speaker'].duties[-1], 0)
                self.assertTrue(self.h.handles[-1].closed)
                self.m[button].level = 1
                self.m['controls'].released(self.h.clock.ticks_ms())
                self.h.clock.sleep_ms(30)
                self.m['controls'].released(self.h.clock.ticks_ms())
        self.start()
        self.m['btn_stop'].level = self.m['btn_play'].level = 0
        self.assertEqual(self.m['play_wav']('/sd/b.wav'), 'stopped')

    def test_file_error_stops(self):
        self.start()
        with patch('builtins.print'):
            self.assertEqual(self.m['play_wav']('/sd/missing.wav'), 'error')
        self.assertEqual(self.m['state'], 'STOPPED')

    def test_soft_off_stops_and_mutes(self):
        self.m['state'] = 'PAUSED'
        self.m['slide'].level = 1
        self.h.run_loop(2)
        self.assertEqual(self.m['state'], 'STOPPED')
        self.assertEqual(self.m['lcd'].lines[0].rstrip(), 'POWER OFF')
        self.assertEqual(self.m['speaker'].duties[-1], 0)
        self.assertEqual(self.m['led'].red.duties[-1], 0)

    def test_idle_navigation_wraps_and_stops(self):
        self.m['slide'].level = 0
        self.m['index'] = 0
        self.m['btn_prev'].level = 0
        self.h.run_loop()
        self.assertEqual((self.m['index'], self.m['state']), (1, 'STOPPED'))

    def test_end_of_track_selects_another(self):
        self.start()
        self.m['index'] = 0
        self.h.run_loop()
        self.assertEqual((self.m['index'], self.m['state']), (1, 'PLAYING'))
        self.assertEqual(self.m['speaker'].duties[-1], 0)

    def test_held_play_does_not_immediately_pause_again(self):
        self.m['state'] = 'STOPPED'
        self.m['btn_play'].level = 0
        event = self.m['poll_controls']()
        self.m['apply_control'](event)
        self.assertEqual(self.m['play_wav']('/sd/b.wav'), 'ended')
        self.assertEqual(self.m['state'], 'PLAYING')
        self.assertIsNone(self.m['poll_controls']())

    def test_pause_then_resume_starts_at_first_unplayed_sample(self):
        self.start()
        self.m['btn_play'].level = 0
        self.assertEqual(self.m['play_wav']('/sd/b.wav'), 'paused')
        self.m['btn_play'].level = 1
        self.m['state'] = 'PLAYING'
        before = len(self.m['speaker'].duties)
        self.assertEqual(self.m['play_wav']('/sd/b.wav'), 'ended')
        self.assertEqual(self.m['speaker'].duties[before:], [1280, 2550, 0])
        self.assertEqual(len(self.h.handles), 4)  # two discovery probes and two plays
        self.assertTrue(all(f.closed for f in self.h.handles))

    def test_volume_zero_and_adc_quantization(self):
        for reading, gain in [(0, 0), (24 << 8, 0), (25 << 8, 1), (65535, 10)]:
            with self.subTest(reading=reading):
                self.start()
                self.m['pot'].reading = reading
                before = len(self.m['speaker'].duties)
                self.m['play_wav']('/sd/b.wav')
                self.assertEqual(self.m['speaker'].duties[before:], [0, 128 * gain, 255 * gain, 0])

    def test_missing_lcd_is_a_muted_reported_hardware_fault(self):
        h = Harness(devices=[])
        m = h.boot()
        self.assertEqual(m['hardware_error'], 'LCD ERROR')
        self.assertIsNone(m['speaker'])
        self.assertFalse(h.mounts)
        h.run_loop(2)
        self.assertEqual(m['state'], 'STOPPED')

    def test_scroll_across_tick_wrap(self):
        m = self.m
        m['tracks'], m['index'] = ['abcdefghijklmnopq.wav'], 0
        self.h.clock.us = ((1 << 30) - 100) * 1000
        m['reset_title_scroll']()
        self.h.clock.sleep_ms(350)
        m['update_title_scroll']()
        self.assertEqual(m['title_scroll_pos'], 1)

    def test_real_lcd_driver_nibbles_and_addressing(self):
        driver = self.h.load('i2c_lcd.py')
        bus = Bus(0)
        lcd = driver.I2cLcd(bus, 0x27, 2, 16)
        bus.writes.clear()
        lcd.putstr('A')
        self.assertEqual(bus.writes, [(0x27, bytes([b])) for b in (0x4D, 0x49, 0x1D, 0x19)])
        bus.writes.clear()
        lcd.move_to(0, 1)
        self.assertEqual([b[1][0] for b in bus.writes], [0xCC, 0xC8, 0x0C, 0x08])


if __name__ == '__main__':
    unittest.main()
