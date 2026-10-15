"""Release integration checks. No physical timing or Pico heap assertions."""
import ast
import io
import unittest
from unittest.mock import Mock, patch
from support import Harness, ROOT, wav_bytes


class ReleaseIntegration(unittest.TestCase):
    def test_supported_entry_point_imports_only_deployment_modules(self):
        # Audit the transitive local import closure, not the unsupported prototype.
        required = {'main', 'buttons', 'wav_reader', 'sdcard', 'i2c_lcd', 'rgb_led'}
        builtins = {'machine', 'micropython', 'os', 'time', 'random'}
        visited, pending = set(), ['main']
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            tree = ast.parse((ROOT / (name + '.py')).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0)
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    self.assertIn(module, required | builtins)
                    if module in required:
                        pending.append(module)
        self.assertEqual(visited, required)
        self.assertFalse((ROOT / 'boot.py').exists())

    def test_no_literal_private_configuration_in_python_sources(self):
        # Narrow recurrence check, not a comprehensive secret scanner. Never
        # include literal values or whole source lines in assertion messages.
        private_names = {'ssid', 'password', 'passwd', 'secret', 'token', 'api_key',
                         'wifi_password', 'wifi_ssid', 'access_token'}
        findings = []
        for path in ROOT.rglob('*.py'):
            if '.git' in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str) or not value.value:
                    continue
                if any(isinstance(target, ast.Name) and target.id.lower() in private_names for target in targets):
                    findings.append((str(path.relative_to(ROOT)), node.lineno))
        self.assertFalse(findings, 'Private literal candidates (paths/lines only): ' + str(findings))

    def test_250_auto_advances_close_files_and_reuse_hardware(self):
        names = ['A_long_title_that_scrolls.wav', 'B.wav', 'C.wav']
        files = {name: wav_bytes(bytes(range(256)) * 2 + b'\x80') for name in names}
        h = Harness(files=files)
        m = h.boot()
        outputs = tuple(h.pwms)
        peripherals = tuple(h.buses)
        tracks = m['tracks']
        stats = dict(opened=0, closed=0, active=0, maximum=0)
        previous = None

        class TrackedFile(io.BytesIO):
            def close(self):
                if not self.closed:
                    stats['closed'] += 1
                    stats['active'] -= 1
                super().close()

        def open_song(path, mode):
            nonlocal previous
            self.assertEqual(mode, 'rb')
            self.assertNotEqual(path, previous)
            previous = path
            self.assertEqual(stats['active'], 0)
            stats['opened'] += 1
            stats['active'] += 1
            stats['maximum'] = max(stats['maximum'], stats['active'])
            return TrackedFile(files[path[4:]])

        m['open'] = open_song
        m['state'], m['index'] = 'PLAYING', 0
        h.run_loop(250)
        self.assertEqual(stats, dict(opened=250, closed=250, active=0, maximum=1))
        self.assertEqual(tuple(h.pwms), outputs)
        self.assertEqual(tuple(h.buses), peripherals)
        self.assertIs(m['tracks'], tracks)
        self.assertEqual(h.mounts, ['/sd'])
        self.assertFalse(h.unmounts)
        self.assertEqual(m['speaker'].frequencies.count(200), 1)
        self.assertEqual(m['speaker'].duties[-1], 0)
        self.assertEqual(m['state'], 'PLAYING')
        self.assertIsNone(m['resume_path'])
        self.assertIsNone(m['hardware_error'])
        self.assertEqual(len(m['lcd_lines']), 2)
        # Mocks retain duty/write histories deliberately; those histories are
        # test instrumentation, not allocations made by machine.PWM on Pico.

    def test_actual_lcd_driver_in_playback_preserves_sample_sequence(self):
        samples = bytes(range(256)) * 3
        h = Harness(files={'A_long_title_for_scroll.wav': wav_bytes(samples)})
        h.modules['i2c_lcd'] = h.load('i2c_lcd.py')
        m = h.boot()
        before = len(m['speaker'].duties)
        m['state'] = 'PLAYING'
        h.clock.sleep_ms(350)
        self.assertEqual(m['play_wav']('/sd/A_long_title_for_scroll.wav'), 'ended')
        self.assertEqual(m['speaker'].duties[before:], [sample * 10 for sample in samples] + [0])
        self.assertTrue(h.handles[-1].closed)
        self.assertEqual(m['title_scroll_pos'], 1)
        self.assertTrue(m['i2c'].writes)
        # LCD pulse sleeps are virtual; this does not establish physical speed.

    def test_manual_output_diagnostics_release_pwm_on_completion_and_interrupt(self):
        for script in ('test_speaker.py', 'test_led.py'):
            for interrupted in (False, True):
                with self.subTest(script=script, interrupted=interrupted):
                    h = Harness()
                    if interrupted:
                        h.clock.sleep = Mock(side_effect=KeyboardInterrupt)
                        with patch('builtins.print'), self.assertRaises(KeyboardInterrupt):
                            h.load(script)
                    else:
                        with patch('builtins.print'):
                            h.load(script)
                    self.assertTrue(h.pwms)
                    self.assertTrue(all(pwm.deinitialized and pwm.duties[-1] == 0 for pwm in h.pwms))

    def test_manual_sd_diagnostic_unmounts_with_only_memory_backed_io(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                h = Harness()
                h.os.VfsFat = lambda card: card
                if interrupted:
                    h.os.listdir = Mock(side_effect=KeyboardInterrupt)
                handles = []
                def memory_open(path, mode):
                    self.assertEqual(path, '/sd/hello.txt')
                    handle = io.StringIO('hello from pico!\n')
                    handles.append(handle)
                    return handle
                with patch('builtins.print'), patch('builtins.open', side_effect=memory_open):
                    if interrupted:
                        with self.assertRaises(KeyboardInterrupt):
                            h.load('test_sd.py')
                    else:
                        h.load('test_sd.py')
                self.assertEqual(h.mounts, ['/sd'])
                self.assertEqual(h.unmounts, ['/sd'])
                self.assertTrue(all(handle.closed for handle in handles))
