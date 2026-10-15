# sdcard.py - SD card driver for MicroPython (SPI mode)
# Tested-style for RP2040/Pico ports.

from micropython import const
import time

_CMD_TIMEOUT = const(10000)
_TOKEN_DATA = const(0xFE)

class SDCard:
    def __init__(self, spi, cs):
        self.spi = spi
        self.cs = cs
        self.cs.init(self.cs.OUT, value=1)

        self.cdv = 512  # bytes per block multiplier for SDSC; becomes 1 for SDHC/SDXC
        self._cmd = bytearray(6)
        self._b1 = bytearray(1)

        self.init_card()

    def _cs(self, v):
        self.cs.value(v)

    def _clock(self, n=1):
        # send N dummy bytes (0xFF) to generate clocks
        self.spi.write(b"\xFF" * n)

    def _readinto_ff(self, buf):
        # read bytes while writing 0xFF
        self.spi.readinto(buf, 0xFF)

    def _read1(self):
        self._readinto_ff(self._b1)
        return self._b1[0]

    def cmd(self, cmd, arg, crc, resp_bytes=0, release=True):
        # Command packet: [0x40|cmd][arg(4)][crc]
        self._cs(0)
        self._clock(1)

        self._cmd[0] = 0x40 | cmd
        self._cmd[1] = (arg >> 24) & 0xFF
        self._cmd[2] = (arg >> 16) & 0xFF
        self._cmd[3] = (arg >> 8) & 0xFF
        self._cmd[4] = arg & 0xFF
        self._cmd[5] = crc

        self.spi.write(self._cmd)

        # wait for response (MSB cleared)
        for _ in range(_CMD_TIMEOUT):
            r = self._read1()
            if r & 0x80 == 0:
                break
        else:
            r = 0xFF

        data = None
        if resp_bytes:
            data = bytearray(resp_bytes)
            self._readinto_ff(data)

        if release:
            self._cs(1)
            self._clock(1)

        return r, data

    def init_card(self):
        # Provide at least 74 clocks with CS high
        self._cs(1)
        self._clock(10)

        # CMD0: go idle
        for _ in range(200):
            r, _ = self.cmd(0, 0, 0x95)
            if r == 1:
                break
        else:
            raise OSError("SD init failed: CMD0")

        # CMD8: check SD v2
        r, r7 = self.cmd(8, 0x1AA, 0x87, resp_bytes=4)
        v2 = (r == 1 and r7 is not None)

        # ACMD41 init loop
        hcs = 0x40000000 if v2 else 0
        for _ in range(2000):
            self.cmd(55, 0, 0x01)           # APP_CMD (crc doesn't matter after init)
            r, _ = self.cmd(41, hcs, 0x01)  # SD_SEND_OP_COND
            if r == 0:
                break
            time.sleep_ms(2)
        else:
            raise OSError("SD init failed: ACMD41")

        # CMD58: read OCR to detect SDHC/SDXC (CCS bit)
        r, ocr = self.cmd(58, 0, 0x01, resp_bytes=4)
        if r != 0:
            raise OSError("SD init failed: CMD58")
        ccs = (ocr[0] & 0x40) != 0  # CCS bit in OCR[0]
        self.cdv = 1 if ccs else 512

        # CMD16 for SDSC to set block length 512
        if self.cdv == 512:
            r, _ = self.cmd(16, 512, 0x01)
            if r != 0:
                raise OSError("SD init failed: CMD16")

        # Speed up after init (adjust if your wiring/module is flaky)
        try:
            self.spi.init(baudrate=10_000_000, polarity=0, phase=0)
        except TypeError:
            pass

    def _wait_token(self, token=_TOKEN_DATA, timeout_ms=300):
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
            if self._read1() == token:
                return True
        return False

    def readblocks(self, block_num, buf):
        # buf must be multiple of 512
        nblocks = len(buf) // 512
        mv = memoryview(buf)

        for i in range(nblocks):
            bn = (block_num + i) * self.cdv
            r, _ = self.cmd(17, bn, 0x01, release=False)
            if r != 0:
                self._cs(1); self._clock(1)
                raise OSError("SD read error CMD17 r=%d" % r)

            if not self._wait_token():
                self._cs(1); self._clock(1)
                raise OSError("SD read timeout (no data token)")

            self._readinto_ff(mv[i*512:(i+1)*512])

            # discard CRC
            self._clock(2)

            self._cs(1)
            self._clock(1)

    def writeblocks(self, block_num, buf):
        nblocks = len(buf) // 512
        mv = memoryview(buf)

        for i in range(nblocks):
            bn = (block_num + i) * self.cdv
            r, _ = self.cmd(24, bn, 0x01, release=False)
            if r != 0:
                self._cs(1); self._clock(1)
                raise OSError("SD write error CMD24 r=%d" % r)

            self.spi.write(b"\xFF")
            self.spi.write(b"\xFE")  # data token
            self.spi.write(mv[i*512:(i+1)*512])
            self.spi.write(b"\xFF\xFF")  # dummy CRC

            # data response
            resp = self._read1()
            if (resp & 0x1F) != 0x05:
                self._cs(1); self._clock(1)
                raise OSError("SD write rejected token=%02x" % resp)

            # wait while busy
            while self._read1() == 0x00:
                pass

            self._cs(1)
            self._clock(1)

    def ioctl(self, op, arg):
        # Block device interface for VfsFat
        if op == 4:   # INIT
            return 0
        if op == 5:   # DEINIT
            return 0
        if op == 6:   # SYNC
            return 0
        if op == 8:   # BLOCK_SIZE
            return 512
        if op == 7:   # BLOCK_COUNT
            # CMD9 read CSD
            r, _ = self.cmd(9, 0, 0x01, release=False)
            if r != 0:
                self._cs(1); self._clock(1)
                raise OSError("CMD9 failed")

            if not self._wait_token():
                self._cs(1); self._clock(1)
                raise OSError("CSD read timeout")

            csd = bytearray(16)
            self._readinto_ff(csd)
            self._clock(2)  # CRC
            self._cs(1); self._clock(1)

            csd_structure = (csd[0] >> 6) & 0x03
            if csd_structure == 1:
                # CSD v2.0 (SDHC/SDXC)
                c_size = ((csd[7] & 0x3F) << 16) | (csd[8] << 8) | csd[9]
                return (c_size + 1) * 1024  # number of 512-byte blocks
            else:
                # CSD v1.0 (SDSC)
                c_size = ((csd[6] & 0x03) << 10) | (csd[7] << 2) | ((csd[8] >> 6) & 0x03)
                c_mult = ((csd[9] & 0x03) << 1) | ((csd[10] >> 7) & 0x01)
                read_bl_len = csd[5] & 0x0F
                block_len = 1 << read_bl_len
                mult = 1 << (c_mult + 2)
                capacity = (c_size + 1) * mult * block_len
                return capacity // 512

        return 0
