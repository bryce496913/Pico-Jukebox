"""Startup state and safe-output assertions, with no physical GPIO devices."""
import unittest
from unittest.mock import Mock, patch
from support import Harness, LCD, Pin


class Startup(unittest.TestCase):
    def test_normal_boot_uses_working_lcd_configuration_and_one_conservative_sweep(self):
        h = Harness()
        m = h.boot()
        self.assertTrue(m['startup_complete'])
        self.assertIsNone(m['hardware_error'])
        self.assertEqual(m['i2c'].kwargs['freq'], 100000)
        self.assertEqual(m['lcd'].args[1:], (0x27, 2, 16))
        self.assertEqual(m['lcd'].backlights, 1)
        self.assertEqual(m['lcd'].lines[0].rstrip(), 'STOPPED')
        self.assertEqual(m['speaker'].frequencies, [40000] + list(range(200, 2000, 50)) + [40000])
        self.assertEqual([d for d in m['speaker'].duties if d], [2000])
        self.assertEqual(m['speaker'].duties[-1], 0)
        self.assertTrue(m['lcd_test_attempted'])
        self.assertTrue(m['speaker_test_attempted'])
        self.assertEqual(h.mounts, ['/sd'])

    def test_cold_off_defers_peripherals_and_first_on_runs_boot_once(self):
        h = Harness(slide_on=False)
        m = h.boot()
        self.assertIsNone(m['speaker'])
        self.assertIsNone(m['lcd'])
        self.assertIsNone(m['pot'])
        self.assertEqual(m['speaker_pin'].level, 0)
        self.assertFalse(h.pwms or h.buses or h.mounts)
        h.run_loop(3)
        self.assertFalse(h.pwms or h.buses or h.mounts)
        self.assertEqual(sum(log == ('POWER OFF',) for log in h.logs), 1)
        m['slide'].level = 0
        h.run_loop()
        self.assertTrue(m['startup_complete'])
        self.assertEqual([d for d in m['speaker'].duties if d], [2000])
        self.assertEqual(h.mounts, ['/sd'])
        self.assertEqual(m['lcd'].lines[0].rstrip(), 'STOPPED')

    def test_power_toggles_never_repeat_screens_sweep_or_mount(self):
        h = Harness()
        m = h.boot()
        speaker, lcd = m['speaker'], m['lcd']
        for _ in range(3):
            m['slide'].level = 1
            h.run_loop(2)
            m['slide'].level = 0
            h.run_loop(2)
        m['boot_lcd_test']()
        m['boot_speaker_test']()
        self.assertEqual(speaker.frequencies.count(200), 1)
        self.assertEqual([d for d in speaker.duties if d], [2000])
        self.assertEqual(sum(text.rstrip() == 'HELLO BRYCE' for _, text in lcd.writes), 1)
        self.assertEqual(h.mounts, ['/sd'])
        self.assertEqual(lcd.backlights, 1)

    def test_off_during_boot_screen_defers_sd_and_tones_until_next_on(self):
        h = Harness(slide_on=False)
        m = h.boot()
        h.run_loop()  # record initial OFF before the first ON transition
        m['slide'].level = 0
        sleep = h.clock.sleep_ms
        def turn_off(ms):
            m['slide'].level = 1
            sleep(ms)
        h.clock.sleep_ms = turn_off
        h.run_loop(2)
        self.assertFalse(m['startup_complete'])
        self.assertFalse(h.mounts)
        self.assertFalse(m['speaker_test_attempted'])
        self.assertFalse(any(m['speaker'].duties))
        self.assertEqual(m['lcd'].lines[0].rstrip(), 'POWER OFF')
        h.clock.sleep_ms = sleep
        m['slide'].level = 0
        h.run_loop()
        self.assertTrue(m['startup_complete'])
        self.assertEqual([d for d in m['speaker'].duties if d], [2000])
        self.assertEqual(sum(text.rstrip() == 'HELLO BRYCE' for _, text in m['lcd'].writes), 1)

    def test_off_during_sweep_mutes_restores_carrier_and_does_not_replay_sweep(self):
        h = Harness(slide_on=False)
        m = h.boot()
        sleep = h.clock.sleep_ms
        def turn_off_when_sweeping(ms):
            if m['speaker_test_attempted']:
                m['slide'].level = 1
            sleep(ms)
        h.clock.sleep_ms = turn_off_when_sweeping
        m['slide'].level = 0
        h.run_loop(2)
        self.assertEqual(m['speaker'].frequency, 40000)
        self.assertEqual(m['speaker'].duties[-1], 0)
        self.assertFalse(h.mounts)
        self.assertEqual([d for d in m['speaker'].duties if d], [2000])
        h.clock.sleep_ms = sleep
        m['slide'].level = 0
        h.run_loop()
        self.assertTrue(m['startup_complete'])
        self.assertEqual(m['speaker'].frequencies.count(200), 1)
        self.assertEqual([d for d in m['speaker'].duties if d], [2000])

    def test_missing_scan_or_lcd_constructor_fault_is_reported_without_sound(self):
        for failure in ('empty', 'unrelated', 'ambiguous', 'scan', 'constructor', 'backlight'):
            with self.subTest(failure=failure):
                devices = {'empty': [], 'unrelated': [0x40], 'ambiguous': [0x20, 0x3F]}.get(failure)
                h = Harness(devices=devices)
                if failure == 'scan':
                    h.machine.I2C = Mock(side_effect=OSError('I2C unavailable'))
                elif failure == 'constructor':
                    h.modules['i2c_lcd'].I2cLcd = Mock(side_effect=OSError('LCD init'))
                elif failure == 'backlight':
                    class BadBacklight(LCD):
                        def backlight_on(self):
                            raise OSError('LCD backlight')
                    h.modules['i2c_lcd'].I2cLcd = BadBacklight
                m = h.boot()
                self.assertEqual(m['hardware_error'], 'LCD ERROR')
                self.assertFalse(h.mounts)
                self.assertFalse(any(p.duties and any(p.duties) for p in h.pwms))
                self.assertTrue(all(p.deinitialized for p in h.pwms))
                self.assertEqual(m['speaker_pin'].level, 0)
                h.run_loop(3)
                self.assertEqual(sum(log[0] == 'LCD ERROR:' for log in h.logs), 1)

    def test_known_pcf_fallback_retained_and_preferred_address_wins(self):
        for devices, address in [([0x3F], 0x3F), ([0x40, 0x27, 0x3F], 0x27)]:
            with self.subTest(devices=devices):
                h = Harness(devices=devices)
                m = h.boot()
                self.assertEqual(m['lcd_addr'], address)
                self.assertTrue(m['startup_complete'])
                self.assertIn((1, 'ADDR 0x%02X' % address + ' ' * 7), m['lcd'].writes)

    def test_speaker_constructor_or_frequency_failure_is_contained_and_reported_on_lcd(self):
        for failure in ('constructor', 'frequency'):
            with self.subTest(failure=failure):
                h = Harness()
                pwm_factory = h.machine.PWM
                def pwm(pin):
                    if pin.number == 18 and failure == 'constructor':
                        raise OSError('PWM unavailable')
                    output = pwm_factory(pin)
                    if pin.number == 18:
                        output.freq = Mock(side_effect=OSError('PWM frequency'))
                    return output
                h.machine.PWM = pwm
                # RGB module has already imported its own PWM reference.
                m = h.boot()
                self.assertEqual(m['hardware_error'], 'SPEAKER ERROR')
                self.assertEqual(m['lcd'].lines[0].rstrip(), 'SPEAKER ERROR')
                self.assertIsNone(m['speaker'])
                self.assertEqual(m['speaker_pin'].level, 0)
                self.assertTrue(all(p.deinitialized for p in h.pwms))
                self.assertFalse(h.mounts)
                h.run_loop(3)
                self.assertFalse(m['speaker_test_attempted'])

    def test_sweep_exception_always_mutes_and_restores_40000_hz_before_release(self):
        h = Harness()
        pwm_factory = h.machine.PWM
        def pwm(pin):
            output = pwm_factory(pin)
            if pin.number == 18:
                freq = output.freq
                def fail(frequency):
                    if frequency == 500:
                        raise OSError('Injected sweep failure')
                    freq(frequency)
                output.freq = fail
            return output
        h.machine.PWM = pwm
        m = h.boot()
        output = next(p for p in h.pwms if p.pin.number == 18)
        self.assertEqual(m['hardware_error'], 'SPEAKER ERROR')
        self.assertEqual(output.frequency, 40000)
        self.assertEqual(output.duties[-1], 0)
        self.assertTrue(output.deinitialized)
        self.assertEqual(m['speaker_pin'].level, 0)
        self.assertFalse(h.mounts)
        before = output.frequencies[:]
        h.run_loop(3)
        self.assertEqual(output.frequencies, before)

    def test_mute_api_failure_deinitializes_and_drives_gpio_low(self):
        h = Harness()
        m = h.boot()
        speaker = m['speaker']
        speaker.duty_u16 = Mock(side_effect=OSError('PWM duty failed'))
        with patch('builtins.print'):
            m['mute_speaker']()
        self.assertTrue(speaker.deinitialized)
        self.assertIsNone(m['speaker'])
        self.assertEqual(m['speaker_pin'].level, 0)
        self.assertEqual(m['hardware_error'], 'SPEAKER ERROR')
        h.run_loop(2)
        self.assertEqual(m['state'], 'STOPPED')

    def test_other_initialization_faults_are_contained_before_tones(self):
        for failure in ('GPIO', 'ADC', 'RGB'):
            with self.subTest(failure=failure):
                h = Harness()
                if failure == 'GPIO':
                    pin_factory = h.machine.Pin
                    class BadButton(pin_factory):
                        def __init__(self, number, *args, **kwargs):
                            if number == 16:
                                raise OSError('GPIO init')
                            super().__init__(number, *args, **kwargs)
                    h.machine.Pin = BadButton
                elif failure == 'ADC':
                    h.machine.ADC = Mock(side_effect=OSError('ADC init'))
                else:
                    h.modules['rgb_led'].RGB_LED = Mock(side_effect=OSError('RGB init'))
                m = h.boot()
                self.assertEqual(m['hardware_error'], failure + ' ERROR')
                self.assertEqual(m['speaker_pin'].level, 0)
                self.assertFalse(any(any(p.duties) for p in h.pwms))
                self.assertFalse(h.mounts)

    def test_partial_rgb_initialization_releases_every_allocated_channel(self):
        h = Harness()
        pwm_factory = h.machine.PWM
        def fail_green(pin):
            output = pwm_factory(pin)
            if pin.number == 8:
                output.freq = Mock(side_effect=OSError('RGB frequency'))
            return output
        h.modules['rgb_led'].PWM = fail_green
        m = h.boot()
        self.assertEqual(m['hardware_error'], 'RGB ERROR')
        self.assertTrue(all(p.deinitialized for p in h.pwms))
        self.assertTrue(all(p.duties[-1] == 0 for p in h.pwms))
        self.assertEqual({p.number: p.level for p in h.pins if p.number in (5, 8, 9)}, {5: 0, 8: 0, 9: 0})

    def test_storage_fault_keeps_lcd_and_reports_without_repeating_sweep(self):
        h = Harness()
        h.modules['sdcard'].SDCard = Mock(side_effect=OSError('missing SD'))
        m = h.boot()
        self.assertEqual(m['storage_error'], 'SD ERROR')
        self.assertEqual(m['lcd'].lines[0].rstrip(), 'SD ERROR')
        self.assertEqual(m['speaker'].duties[-1], 0)
        h.run_loop(3)
        self.assertEqual(m['speaker'].frequencies.count(200), 1)
        self.assertEqual(h.modules['sdcard'].SDCard.call_count, 1)

    def test_unchanged_screens_do_not_clear_or_rewrite_lcd(self):
        h = Harness()
        m = h.boot()
        lcd = m['lcd']
        writes = lcd.writes[:]
        for _ in range(3):
            m['update_display']()
        self.assertEqual(lcd.writes, writes)
        self.assertEqual(lcd.clears, 0)  # application does not clear between screens
        m['slide'].level = 1
        h.run_loop()
        writes = lcd.writes[:]
        duties = m['speaker'].duties[:]
        h.run_loop(3)
        self.assertEqual(lcd.writes, writes)
        self.assertEqual(m['speaker'].duties, duties)

    def test_runtime_lcd_fault_is_latched_and_mutes_without_repeated_bus_writes(self):
        h = Harness()
        m = h.boot()
        lcd = m['lcd']
        lcd.putstr = Mock(side_effect=OSError('LCD disconnected'))
        m['state'] = 'PLAYING'
        with patch('builtins.print'):
            m['update_display']()
        self.assertEqual(m['hardware_error'], 'LCD ERROR')
        self.assertEqual(m['speaker'].duties[-1], 0)
        h.run_loop(3)
        self.assertEqual(lcd.putstr.call_count, 1)
        self.assertEqual(m['state'], 'STOPPED')
        self.assertEqual(h.unmounts, ['/sd'])

    def test_interrupt_during_sweep_cleans_outputs(self):
        h = Harness(slide_on=False)
        m = h.boot()
        sleep = h.clock.sleep_ms
        def interrupt(ms):
            if m['speaker_test_attempted']:
                raise KeyboardInterrupt
            sleep(ms)
        h.clock.sleep_ms = interrupt
        m['slide'].level = 0
        with self.assertRaises(KeyboardInterrupt):
            h.run_loop()
        output = next(p for p in h.pwms if p.pin.number == 18)
        self.assertEqual(output.frequency, 40000)
        self.assertEqual(output.duties[-1], 0)
        self.assertTrue(output.deinitialized)
        self.assertEqual(m['speaker_pin'].level, 0)
        self.assertTrue(all(p.deinitialized for p in h.pwms))


    def test_reset_after_lcd_is_restored_recovers_cleanly(self):
        h = Harness()
        h.modules['i2c_lcd'].I2cLcd = Mock(side_effect=OSError('missing LCD'))
        failed = h.boot()
        self.assertEqual(failed['hardware_error'], 'LCD ERROR')
        h.run_loop(3)
        self.assertEqual(h.modules['i2c_lcd'].I2cLcd.call_count, 1)
        h.modules['i2c_lcd'].I2cLcd = LCD
        restored = h.boot()  # simulate a fresh interpreter boot, after repair
        self.assertTrue(restored['startup_complete'])
        self.assertIsNone(restored['hardware_error'])
        self.assertEqual(restored['speaker'].duties[-1], 0)
        self.assertEqual(restored['speaker'].frequencies.count(200), 1)

    def test_off_during_sd_identification_defers_mount_without_reinitializing_card(self):
        h = Harness(slide_on=False)
        m = h.boot()
        def sd_init(spi, cs):
            m['slide'].level = 1
            return object()
        h.modules['sdcard'].SDCard = Mock(side_effect=sd_init)
        m['slide'].level = 0
        h.run_loop(2)
        self.assertFalse(h.mounts)
        self.assertFalse(m['startup_complete'])
        self.assertEqual(m['speaker'].duties[-1], 0)
        m['slide'].level = 0
        h.run_loop()
        self.assertTrue(m['startup_complete'])
        self.assertEqual(h.mounts, ['/sd'])
        self.assertEqual(h.modules['sdcard'].SDCard.call_count, 1)
        self.assertEqual(m['speaker'].frequencies.count(200), 1)

    def test_actual_lcd_driver_starts_with_backlight_on_and_clear(self):
        h = Harness()
        driver = h.load('i2c_lcd.py')
        bus = h.machine.I2C(0)
        lcd = driver.I2cLcd(bus, 0x27, 2, 16)
        self.assertEqual(lcd.backlight, 0x08)
        self.assertTrue(all(data[0] & 0x08 for _, data in bus.writes))
        self.assertEqual([data[0] for _, data in bus.writes[-4:]], [0x0C, 0x08, 0x1C, 0x18])


    def test_music_led_failure_cannot_emit_more_audio_after_fault(self):
        h = Harness()
        m = h.boot()
        speaker = m['speaker']
        m['led'].color = Mock(side_effect=OSError('RGB disconnected'))
        m['state'] = 'PLAYING'
        m['index'] = 0
        before = len(speaker.duties)
        h.run_loop(2)
        self.assertEqual(m['hardware_error'], 'RGB ERROR')
        self.assertEqual(m['state'], 'STOPPED')
        self.assertTrue(all(duty == 0 for duty in speaker.duties[before:]))
        self.assertTrue(speaker.deinitialized)
        self.assertTrue(all(f.closed for f in h.handles))

    def test_late_adc_seed_failure_unmounts_and_releases_initialized_outputs(self):
        h = Harness()
        factory = h.machine.ADC
        def adc(number):
            result = factory(number)
            result.read_u16 = Mock(side_effect=[65535, OSError('ADC read')])
            return result
        h.machine.ADC = adc
        m = h.boot()
        self.assertEqual(m['hardware_error'], 'ADC ERROR')
        self.assertEqual(h.mounts, ['/sd'])
        self.assertEqual(h.unmounts, ['/sd'])
        self.assertEqual(m['lcd'].lines[0].rstrip(), 'ADC ERROR')
        self.assertTrue(all(p.deinitialized for p in h.pwms))
        self.assertEqual(m['speaker_pin'].level, 0)
        self.assertTrue(all(f.closed for f in h.handles))

    def test_failed_soft_power_input_is_contained_and_reported(self):
        h = Harness()
        factory = h.machine.Pin
        class BadSlide(factory):
            def __init__(self, number, *args, **kwargs):
                if number == 22:
                    raise OSError('Slide input unavailable')
                super().__init__(number, *args, **kwargs)
        h.machine.Pin = BadSlide
        m = h.boot()
        self.assertEqual(m['hardware_error'], 'GPIO ERROR')
        self.assertFalse(h.pwms or h.mounts or h.buses)
        self.assertEqual(m['speaker_pin'].level, 0)
        h.run_loop(3)
        self.assertFalse(m['startup_complete'])


    def test_interrupt_in_main_playback_closes_file_unmounts_and_releases_outputs(self):
        h = Harness()
        m = h.boot()
        m['state'] = 'PLAYING'
        m['index'] = 0
        h.clock.sleep_us = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            h.run_loop()
        self.assertTrue(all(f.closed for f in h.handles))
        self.assertEqual(h.unmounts, ['/sd'])
        self.assertTrue(all(p.deinitialized for p in h.pwms))
        self.assertEqual(m['speaker_pin'].level, 0)
        speaker = next(p for p in h.pwms if p.pin.number == 18)
        self.assertEqual(speaker.duties[-1], 0)


if __name__ == '__main__':
    unittest.main()
