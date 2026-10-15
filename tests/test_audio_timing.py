"""Diagnostic arithmetic only. Virtual elapsed time is not an audio benchmark."""
import unittest
from support import Harness


class AudioTiming(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.module = self.h.load('tools/audio_timing.py')

    def test_completed_file_reports_known_samples_and_whole_call_average(self):
        calls = []
        def player(path):
            calls.append(path)
            self.h.clock.sleep_ms(2000)
            return 'ended'
        report = self.module.measure_playback(player, '/sd/test.wav', 8000)
        self.assertEqual(calls, ['/sd/test.wav'])
        self.assertEqual(report, {'result': 'ended', 'elapsed_ms': 2000,
                                 'completed_samples': 8000, 'source_duration_ms': 1000,
                                 'average_samples_per_second': 4000})

    def test_incomplete_playback_has_no_inferred_sample_count_or_rate(self):
        for result in ('stopped', 'paused', 'track_change', 'interrupted', 'error'):
            with self.subTest(result=result):
                def player(path):
                    self.h.clock.sleep_ms(10)
                    return result
                report = self.module.measure_playback(player, 'test.wav', 8000)
                self.assertEqual(report['result'], result)
                self.assertEqual(report['elapsed_ms'], 10)
                self.assertIsNone(report['completed_samples'])
                self.assertIsNone(report['source_duration_ms'])
                self.assertIsNone(report['average_samples_per_second'])

    def test_unknown_count_or_zero_elapsed_never_invents_a_rate(self):
        report = self.module.measure_playback(lambda path: 'ended', 'test.wav')
        self.assertIsNone(report['completed_samples'])
        self.assertIsNone(report['average_samples_per_second'])
        report = self.module.measure_playback(lambda path: 'ended', 'test.wav', 1)
        self.assertEqual(report['completed_samples'], 1)
        self.assertEqual(report['elapsed_ms'], 0)
        self.assertIsNone(report['average_samples_per_second'])

    def test_elapsed_time_handles_counter_wrap(self):
        self.h.clock.us = ((1 << 30) - 100) * 1000
        def player(path):
            self.h.clock.sleep_ms(250)
            return 'ended'
        report = self.module.measure_playback(player, 'test.wav', 8000)
        self.assertEqual(report['elapsed_ms'], 250)
        self.assertEqual(report['average_samples_per_second'], 32000)

    def test_invalid_known_count_is_rejected_before_playback(self):
        for count in (0, -1, 2.5, '8000'):
            with self.subTest(count=count):
                calls = []
                with self.assertRaises(ValueError):
                    self.module.measure_playback(lambda path: calls.append(path), 'test.wav', count)
                self.assertEqual(calls, [])

    def test_player_interrupts_propagate(self):
        def player(path):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.module.measure_playback(player, 'test.wav', 8000)


if __name__ == '__main__':
    unittest.main()
