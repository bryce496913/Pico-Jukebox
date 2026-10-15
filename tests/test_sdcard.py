import unittest
from unittest.mock import Mock
from support import Harness, Pin


class SDProtocol(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.driver = self.h.load('sdcard.py')
        self.sd = object.__new__(self.driver.SDCard)
        self.sd.spi = Mock()
        self.sd.cs = Pin(13)
        self.sd._cmd = bytearray(6)
        self.sd._b1 = bytearray(1)
        self.sd.cdv = 1

    def test_command_packet_and_release(self):
        self.sd._read1 = lambda: 0
        self.assertEqual(self.sd.cmd(17, 0x12345678, 1), (0, None))
        self.assertEqual(bytes(self.sd.spi.write.call_args_list[1].args[0]), b'\x51\x12\x34\x56\x78\x01')
        self.assertEqual(self.sd.cs.level, 1)

    def test_init_sdhc_and_sdsc_addressing(self):
        for ccs, multiplier in [(0x40, 1), (0, 512)]:
            with self.subTest(ccs=ccs):
                def cmd(number, arg, crc, **kwargs):
                    if number == 0:
                        return 1, None
                    if number == 8:
                        return 1, bytearray([0, 0, 1, 0xAA])
                    if number == 58:
                        return 0, bytearray([0x80 | ccs, 0xFF, 0x80, 0])
                    return 0, None
                self.sd.cmd = Mock(side_effect=cmd)
                self.sd.init_card()
                self.assertEqual(self.sd.cdv, multiplier)
                self.sd.cmd = Mock(return_value=(0, None))
                self.sd._wait_token = lambda: True
                self.sd._readinto_ff = lambda buf: buf.__setitem__(slice(None), b'X' * len(buf))
                buf = bytearray(1024)
                self.sd.readblocks(2, buf)
                self.assertEqual([c.args[1] for c in self.sd.cmd.call_args_list], [2 * multiplier, 3 * multiplier])
                self.assertEqual(buf, b'X' * 1024)
                self.assertEqual(self.sd.cs.level, 1)

    def test_read_timeout_releases_cs(self):
        self.sd.cmd = Mock(return_value=(0, None))
        self.sd._wait_token = lambda: False
        with self.assertRaisesRegex(OSError, 'timeout'):
            self.sd.readblocks(0, bytearray(512))
        self.assertEqual(self.sd.cs.level, 1)

    def test_write_rejection_releases_cs(self):
        self.sd.cmd = Mock(return_value=(0, None))
        self.sd._read1 = lambda: 0x0B
        with self.assertRaisesRegex(OSError, 'rejected'):
            self.sd.writeblocks(0, bytes(512))
        self.assertEqual(self.sd.cs.level, 1)

    def test_token_timeout_uses_elapsed_time(self):
        def read():
            self.h.clock.sleep_ms(1)
            return 0xFF
        self.sd._read1 = read
        self.assertFalse(self.sd._wait_token(timeout_ms=3))
        self.assertEqual(self.h.clock.ticks_ms(), 3)


class ScriptedSPI:
    """SD SPI byte stream; no filesystem, GPIO, or actual card writes."""
    def __init__(self, clock, legacy=False, high_capacity=True):
        self.clock, self.legacy, self.high_capacity = clock, legacy, high_capacity
        self.pending, self.writes, self.speeds = [], [], []
        self.ready_calls, self.command = 0, None
        self.csd = bytearray(16)
        self.csd[0] = 0x40
        self.csd[7:10] = b'\x01\x23\x45'

    def init(self, **kwargs):
        self.speeds.append(kwargs['baudrate'])

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        if len(data) == 6 and data[0] & 0xC0 == 0x40:
            self.command = data[0] & 0x3F
            responses = {0: [1], 8: [5, 255, 255, 255, 255] if self.legacy else [1, 0, 0, 1, 0xAA],
                         55: [1], 58: [0, 0xC0 if self.high_capacity else 0x80, 0xFF, 0x80, 0],
                         16: [0], 9: [0, 0xFE] + list(self.csd),
                         17: [0, 0xFE] + [data[4]] * 512, 24: [0]}
            if self.command == 41:
                self.pending = [1 if self.ready_calls == 0 else 0]
                self.ready_calls += 1
            else:
                self.pending = responses[self.command][:]
        elif self.command == 24 and len(data) == 512:
            self.pending = [5, 0, 0xFF]
        return None

    def readinto(self, buf, filler):
        self.clock.sleep_us(20)
        for i in range(len(buf)):
            buf[i] = self.pending.pop(0) if self.pending else 0xFF


class SDReliability(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.driver = self.h.load('sdcard.py')
        self.spi = ScriptedSPI(self.h.clock)
        self.cs = Pin(13)
        self.sd = self.driver.SDCard(self.spi, self.cs)

    def test_wire_level_initialization_supported_card_types(self):
        for legacy, high_capacity, multiplier in [(False, True, 1), (False, False, 512), (True, False, 512)]:
            with self.subTest(legacy=legacy, high_capacity=high_capacity):
                spi = ScriptedSPI(self.h.clock, legacy, high_capacity)
                sd = self.driver.SDCard(spi, self.cs)
                self.assertEqual(sd.cdv, multiplier)
                self.assertEqual(spi.speeds, [100000, 10000000])
                commands = [b for b in spi.writes if len(b) == 6]
                acmd41 = next(b for b in commands if b[0] == 0x69)
                self.assertEqual(acmd41[1:5], b'\0\0\0\0' if legacy else b'\x40\0\0\0')
                self.assertEqual(any(b[0] == 0x50 for b in commands), multiplier == 512)
                self.assertEqual(self.cs.level, 1)

    def test_ioctl_real_csd_capacity_and_operation_numbers(self):
        self.assertEqual(self.sd.ioctl(4, 0), (0x12345 + 1) * 1024)
        self.assertEqual(self.sd.ioctl(5, 0), 512)
        for operation in (1, 2, 3):
            self.assertEqual(self.sd.ioctl(operation, 0), 0)
        for operation in (6, 7, 8, 99):
            self.assertIsNone(self.sd.ioctl(operation, 0))
        csd = bytearray(16)
        csd[5], csd[7], csd[8], csd[9], csd[10] = 9, 255, 0xC0, 1, 0x80
        self.spi.csd = csd
        self.sd.cdv = 512
        self.assertEqual(self.sd.ioctl(4, 0), 32768)
        self.assertEqual(self.cs.level, 1)

    def test_invalid_csd_is_rejected_not_fabricated(self):
        for structure, read_length in [(0x80, 9), (0, 0)]:
            with self.subTest(structure=structure):
                self.spi.csd[0], self.spi.csd[5] = structure, read_length
                self.sd.cdv = 512 if structure == 0 else 1
                with self.assertRaises(OSError):
                    self.sd.ioctl(4, 0)
                self.assertEqual(self.cs.level, 1)

    def test_failed_capacity_read_releases_cs(self):
        self.sd._wait_token = lambda: False
        with self.assertRaisesRegex(OSError, 'CSD read timeout'):
            self.sd.ioctl(4, 0)
        self.assertEqual(self.cs.level, 1)

    def test_multiblock_read_modifies_original_buffer_with_distinct_blocks(self):
        buf = bytearray(1024)
        self.assertEqual(self.sd.readblocks(3, buf), 0)
        self.assertEqual(buf[:512], b'\x03' * 512)
        self.assertEqual(buf[512:], b'\x04' * 512)
        self.assertEqual(self.cs.level, 1)

    def test_multiblock_writes_preserve_every_byte_and_address(self):
        for multiplier in (1, 512):
            with self.subTest(multiplier=multiplier):
                self.sd.cdv = multiplier
                self.spi.writes.clear()
                buf = bytes(range(256)) * 4
                self.assertEqual(self.sd.writeblocks(2, buf), 0)
                commands = [b for b in self.spi.writes if len(b) == 6]
                self.assertEqual([int.from_bytes(b[1:5], 'big') for b in commands], [2 * multiplier, 3 * multiplier])
                self.assertEqual(b''.join(b for b in self.spi.writes if len(b) == 512), buf)
                self.assertEqual(self.cs.level, 1)

    def test_reject_partial_buffers_and_invalid_addresses_before_transfer(self):
        for method in (self.sd.readblocks, self.sd.writeblocks):
            for block, size in [(0, 1), (0, 511), (0, 513), (-1, 512), (0xFFFFFFFF, 1024)]:
                with self.subTest(method=method.__name__, block=block, size=size):
                    before = len(self.spi.writes)
                    with self.assertRaises(ValueError):
                        method(block, bytearray(size))
                    self.assertEqual(len(self.spi.writes), before)

    def test_missing_card_command_timeout_releases_cs(self):
        self.sd._read1 = lambda: 0xFF
        with self.assertRaisesRegex(OSError, 'command timeout'):
            self.sd.init_card()
        self.assertEqual(self.cs.level, 1)

    def test_write_busy_timeout_is_bounded_and_releases_cs(self):
        responses = iter([0, 5])
        self.sd._read1 = lambda: next(responses, 0)
        start = self.h.clock.ticks_ms()
        with self.assertRaisesRegex(OSError, 'busy timeout'):
            self.sd.writeblocks(0, bytes(512))
        self.assertLessEqual(self.h.clock.ticks_diff(self.h.clock.ticks_ms(), start), 1001)
        self.assertEqual(self.cs.level, 1)

    def test_spi_exceptions_release_cs_in_command_read_write_and_capacity(self):
        for operation in ('command', 'read', 'write', 'capacity'):
            with self.subTest(operation=operation):
                sd = self.driver.SDCard(ScriptedSPI(self.h.clock), self.cs)
                if operation == 'command':
                    sd.spi.write = Mock(side_effect=OSError('SPI write'))
                    action = lambda: sd.cmd(17, 0, 1, release=False)
                elif operation == 'read':
                    sd._wait_token = lambda: True
                    original = sd._readinto_ff
                    def read(buf):
                        if len(buf) == 512:
                            raise OSError('SPI data read')
                        original(buf)
                    sd._readinto_ff = read
                    action = lambda: sd.readblocks(0, bytearray(512))
                elif operation == 'write':
                    original = sd._write
                    def write(buf):
                        if len(buf) == 512:
                            raise OSError('SPI data write')
                        original(buf)
                    sd._write = write
                    action = lambda: sd.writeblocks(0, bytes(512))
                else:
                    sd._wait_token = lambda: True
                    sd._readinto_ff = Mock(side_effect=OSError('SPI CSD read'))
                    action = lambda: sd.ioctl(4, 0)
                with self.assertRaises(OSError):
                    action()
                self.assertEqual(self.cs.level, 1)

    def test_short_spi_transfers_raise_and_release_cs(self):
        self.spi.readinto = Mock(return_value=0)
        with self.assertRaisesRegex(OSError, 'Short SD SPI read'):
            self.sd.readblocks(0, bytearray(512))
        self.assertEqual(self.cs.level, 1)
        self.spi.write = Mock(return_value=0)
        with self.assertRaisesRegex(OSError, 'Short SD SPI write'):
            self.sd.cmd(17, 0, 1)
        self.assertEqual(self.cs.level, 1)

    def test_initialization_rejects_bad_responses_and_never_ready_card(self):
        for failing_command in (0, 8, 55, 41, 58, 16):
            with self.subTest(command=failing_command):
                def response(command, arg, crc, **kwargs):
                    if command == 0:
                        return (0 if failing_command == 0 else 1), None
                    if command == 8:
                        return 1, b'\0\0\x01\xAB' if failing_command == 8 else b'\0\0\x01\xAA'
                    if command == 58:
                        return 0, b'\0\0\0\0' if failing_command == 58 else b'\x80\xff\x80\0'
                    if command == failing_command:
                        return (1 if command == 41 else 4), None
                    return 0, None
                self.sd.cmd = Mock(side_effect=response)
                with self.assertRaises(OSError):
                    self.sd.init_card()
                self.assertEqual(self.cs.level, 1)


    def test_capacity_addressing_mismatch_is_rejected(self):
        self.sd.cdv = 512  # CSD fixture says SDHC while OCR says SDSC
        with self.assertRaisesRegex(OSError, 'CSD/addressing mismatch'):
            self.sd.ioctl(4, 0)
        self.assertEqual(self.cs.level, 1)

    def test_second_sector_read_failure_is_not_reported_as_success(self):
        original = self.sd._readinto_ff
        sectors = 0

        def read(buf):
            nonlocal sectors
            if len(buf) == 512:
                sectors += 1
                if sectors == 2:
                    raise OSError('Second sector read failed')
            original(buf)

        self.sd._readinto_ff = read
        buf = bytearray(1024)
        with self.assertRaisesRegex(OSError, 'Second sector'):
            self.sd.readblocks(3, buf)
        self.assertEqual(buf[:512], b'\x03' * 512)
        self.assertEqual(buf[512:], bytes(512))
        self.assertEqual(self.cs.level, 1)

    def test_command_elapsed_timeout_across_ticks_wrap(self):
        self.h.clock.us = ((1 << 30) - 50) * 1000
        def read():
            self.h.clock.sleep_ms(1)
            return 0xFF
        self.sd._read1 = read
        start = self.h.clock.ticks_ms()
        with self.assertRaisesRegex(OSError, 'command timeout'):
            self.sd.cmd(17, 0, 1)
        self.assertEqual(self.h.clock.ticks_diff(self.h.clock.ticks_ms(), start), 100)
        self.assertEqual(self.cs.level, 1)


if __name__ == '__main__':
    unittest.main()
