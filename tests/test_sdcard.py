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
                        return 0, bytearray([ccs, 0, 0, 0])
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


if __name__ == '__main__':
    unittest.main()
