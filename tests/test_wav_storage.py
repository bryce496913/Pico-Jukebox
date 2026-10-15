"""RIFF parsing and storage failures, using only memory-backed files."""
import io
import struct
import unittest
from unittest.mock import Mock, patch
from support import Harness, wav_bytes, riff_chunk


class WavParsing(unittest.TestCase):
    def setUp(self):
        self.reader = Harness().load('wav_reader.py')

    def parse(self, data):
        stream = io.BytesIO(data)
        size = self.reader.audio_data(stream)
        return stream.read(size)

    def test_valid_canonical_wav(self):
        self.assertEqual(self.parse(wav_bytes()), b'\0\x80\xff')

    def test_extra_chunks_odd_padding_extended_fmt_and_trailing_metadata(self):
        fmt = struct.pack('<HHIIHHH', 1, 1, 8000, 8000, 1, 8, 0)
        body = (b'WAVE' + riff_chunk(b'JUNK', b'abc') + riff_chunk(b'fmt ', fmt) +
                riff_chunk(b'LIST', b'metadata') + riff_chunk(b'data', b'\x01\x02') +
                riff_chunk(b'LIST', b'not audio'))
        data = b'RIFF' + struct.pack('<I', len(body)) + body
        self.assertEqual(self.parse(data), b'\x01\x02')

    def test_reject_required_format_field_changes(self):
        for field, value in [('encoding', 3), ('channels', 2), ('rate', 44100),
                             ('byte_rate', 16000), ('align', 2), ('bits', 16)]:
            with self.subTest(field=field):
                with self.assertRaises(self.reader.WavError):
                    self.parse(wav_bytes(**{field: value}))

    def test_truncation_at_every_byte(self):
        valid = wav_bytes()
        for end in range(len(valid)):
            with self.subTest(end=end):
                with self.assertRaises(self.reader.WavError):
                    self.parse(valid[:end])

    def test_bad_signatures_missing_duplicate_empty_and_oversized_chunks(self):
        fmt = riff_chunk(b'fmt ', struct.pack('<HHIIHH', 1, 1, 8000, 8000, 1, 8))
        samples = riff_chunk(b'data', b'\x80')
        bodies = [b'WAVE' + fmt, b'WAVE' + samples,
                  b'WAVE' + fmt + fmt + samples,
                  b'WAVE' + fmt + samples + samples,
                  b'WAVE' + fmt + riff_chunk(b'data', b''),
                  b'WAVE' + riff_chunk(b'fmt ', b'\0' * 15) + samples,
                  b'WAVE' + fmt + b'data\xff\xff\xff\xff',
                  b'WAVE' + fmt + samples + b'JUNK']
        bad_files = [b'RIFF' + struct.pack('<I', len(b)) + b for b in bodies]
        bad_files += [b'NOPE' + wav_bytes()[4:], wav_bytes()[:8] + b'NOPE' + wav_bytes()[12:],
                      b'RIFF\0\0\0\0WAVE']
        for data in bad_files:
            with self.subTest(data=data):
                with self.assertRaises(self.reader.WavError):
                    self.parse(data)

    def test_short_file_reads_are_completed(self):
        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(2, size))
        stream = ShortReads(wav_bytes())
        self.assertEqual(self.reader.audio_data(stream), 3)
        self.assertEqual(self.reader.read_exact(stream, 3), b'\0\x80\xff')

    def test_read_failure_propagates(self):
        stream = Mock()
        stream.read.side_effect = OSError('card removed')
        with self.assertRaises(OSError):
            self.reader.audio_data(stream)


class StoragePlayback(unittest.TestCase):
    def start(self, h=None):
        self.h = h or Harness()
        self.m = self.h.boot()
        self.m['slide'].level = 0
        self.m['state'] = 'PLAYING'
        return self.m

    def test_discovery_filters_invalid_unsupported_directories_and_unreadable(self):
        h = Harness(files={'b.wav': wav_bytes(), 'A.WAV': wav_bytes(),
                           'bad.wav': b'not WAV', 'stereo.wav': wav_bytes(channels=2),
                           'folder.wav': wav_bytes(), 'missing.wav': wav_bytes(),
                           'ignore.mp3': wav_bytes()})
        def stat(path):
            if path.endswith('missing.wav'):
                raise OSError('missing')
            return (0x4000 if path.endswith('folder.wav') else 0x8000,)
        h.os.stat = stat
        m = h.boot()
        self.assertEqual(m['tracks'], ['A.WAV', 'b.wav'])
        self.assertTrue(all(f.closed for f in h.handles))

    def test_missing_card_initialization_mount_and_listing_failures_stay_muted(self):
        for stage in ('init', 'mount', 'listdir'):
            with self.subTest(stage=stage):
                h = Harness()
                fail = Mock(side_effect=OSError(stage))
                if stage == 'init':
                    h.modules['sdcard'].SDCard = fail
                else:
                    setattr(h.os, stage, fail)
                m = h.boot()
                self.assertEqual((m['storage_error'], m['state'], m['tracks']), ('SD ERROR', 'STOPPED', []))
                self.assertEqual(m['speaker'].duties[-1], 0)
                m['slide'].level = 0
                m['btn_play'].level = 0
                h.run_loop(3)
                self.assertEqual(fail.call_count, 1)  # no automatic retry
                self.assertEqual(m['lcd'].lines[0], 'SD ERROR')
                self.assertEqual(h.unmounts, ['/sd'] if stage == 'listdir' else [])
                self.assertEqual(m['speaker'].duties[-1], 0)

    def test_empty_or_all_invalid_library_remains_responsive_to_soft_power(self):
        for files in ({}, {'bad.wav': b'bad'}, {'unsupported.wav': wav_bytes(bits=16)}):
            with self.subTest(files=list(files)):
                h = Harness(files=files)
                m = h.boot()
                self.assertEqual(m['storage_error'], 'NO WAV FILES')
                self.assertEqual(m['lcd'].lines[0], 'NO WAV FILES')
                h.run_loop()
                self.assertEqual(m['lcd'].lines[0], 'POWER OFF')
                m['slide'].level = 0
                m['btn_next'].level = m['btn_play'].level = 0
                h.run_loop(2)
                self.assertEqual(m['lcd'].lines[0], 'NO WAV FILES')
                self.assertEqual(m['state'], 'STOPPED')
                self.assertEqual(m['speaker'].duties[-1], 0)

    def test_invalid_or_missing_file_after_discovery_stops_without_retry(self):
        for replacement in (None, b'bad', wav_bytes(rate=16000), wav_bytes()[:-1]):
            with self.subTest(replacement=replacement):
                m = self.start()
                m['index'] = m['tracks'].index('b.wav')
                if replacement is None:
                    del self.h.files['b.wav']
                else:
                    self.h.files['b.wav'] = replacement
                self.h.handles.clear()
                before = len(m['speaker'].duties)
                with patch('builtins.print'):
                    self.h.run_loop(4)
                self.assertEqual(m['state'], 'STOPPED')
                self.assertTrue(m['speaker'].duties[before:])
                self.assertTrue(all(duty == 0 for duty in m['speaker'].duties[before:]))
                self.assertLessEqual(len(self.h.handles), 1)
                self.assertTrue(all(f.closed for f in self.h.handles))
                # An explicit navigation still works after the failure.
                m['btn_prev'].level = 0
                self.h.run_loop()
                self.assertEqual(m['index'], 0)

    def test_data_eof_does_not_play_pad_or_metadata(self):
        h = Harness(files={'song.wav': wav_bytes(b'\x80\xff', extra=riff_chunk(b'JUNK', b'HEADER'),
                                              trailing=riff_chunk(b'LIST', b'NOT AUDIO'))})
        m = self.start(h)
        before = len(m['speaker'].duties)
        self.assertEqual(m['play_wav']('/sd/song.wav'), 'ended')
        self.assertEqual(m['speaker'].duties[before:], [1280, 2550, 0])
        self.assertTrue(h.handles[-1].closed)

    def test_midstream_io_failure_or_eof_closes_and_mutes(self):
        for failure in (OSError('card removed'), b''):
            with self.subTest(failure=failure):
                m = self.start()
                data = wav_bytes(b'\xff' * 700)
                class Broken(io.BytesIO):
                    def read(self, size=-1):
                        if self.tell() >= 44 + 512:
                            if isinstance(failure, Exception):
                                raise failure
                            return failure
                        return super().read(size)
                self.h.files['b.wav'] = lambda: Broken(data)
                before = len(m['speaker'].duties)
                with patch('builtins.print'):
                    self.assertEqual(m['play_wav']('/sd/b.wav'), 'error')
                self.assertEqual(m['state'], 'STOPPED')
                self.assertEqual(m['speaker'].duties[before:], [2550] * 512 + [0, 0])
                self.assertTrue(self.h.handles[-1].closed)

    def test_interrupt_still_closes_and_mutes(self):
        m = self.start()
        self.h.clock.sleep_us = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            m['play_wav']('/sd/b.wav')
        self.assertEqual(m['speaker'].duties[-1], 0)
        self.assertTrue(self.h.handles[-1].closed)

    def test_soft_off_during_chunk_keeps_current_track_in_main_loop(self):
        m = self.start(Harness(files={'A.WAV': wav_bytes(b'\xff' * 700), 'b.wav': wav_bytes()}))
        m['index'] = 0
        sleep_us = self.h.clock.sleep_us

        def power_off(us):
            m['slide'].level = 1
            sleep_us(us)

        self.h.clock.sleep_us = power_off
        self.h.run_loop(2)
        self.assertEqual((m['index'], m['state']), (0, 'PLAYING'))
        self.assertEqual(m['lcd'].lines[0], 'POWER OFF')
        self.assertEqual(m['speaker'].duties[-1], 0)
        self.assertTrue(self.h.handles[-1].closed)

    def test_soft_off_is_not_end_of_file(self):
        m = self.start()
        m['index'] = 0
        m['slide'].level = 1
        self.assertEqual(m['play_wav']('/sd/A.WAV'), 'interrupted')
        self.assertEqual(m['index'], 0)
        self.assertEqual(m['speaker'].duties[-1], 0)
        self.assertTrue(self.h.handles[-1].closed)


if __name__ == '__main__':
    unittest.main()
