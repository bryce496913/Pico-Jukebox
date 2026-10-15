# sdcard.py - SD card driver for MicroPython (SPI mode)
# Tested-style for RP2040/Pico ports.

from micropython import const
import time

_CMD_TIMEOUT = const(10000)
_TOKEN_DATA = const(0xFE)
_BLOCK_SIZE = const(512)
_COMMAND_TIMEOUT_MS = const(100)
_WRITE_TIMEOUT_MS = const(1000)

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
        self._write(b"\xFF" * n)

    def _readinto_ff(self, buf):
        # read bytes while writing 0xFF
        result = self.spi.readinto(buf, 0xFF)
        # MicroPython SPI returns None; count-returning implementations must not
        # silently accept a short transfer.
        if isinstance(result, int) and result != len(buf):
            raise OSError("Short SD SPI read")

    def _read1(self):
        self._readinto_ff(self._b1)
        return self._b1[0]

    def _write(self, buf):
        result = self.spi.write(buf)
        if isinstance(result, int) and result != len(buf):
            raise OSError("Short SD SPI write")

    def _release(self):
        # Deselect before the trailing clocks, even if SPI itself has failed.
        self._cs(1)
        self._clock(1)

    def cmd(self, cmd, arg, crc, resp_bytes=0, release=True):
        keep_selected = False
        try:
            self._cs(0)
            self._clock(1)
            self._cmd[0] = 0x40 | cmd
            self._cmd[1] = (arg >> 24) & 0xFF
            self._cmd[2] = (arg >> 16) & 0xFF
            self._cmd[3] = (arg >> 8) & 0xFF
            self._cmd[4] = arg & 0xFF
            self._cmd[5] = crc
            self._write(self._cmd)

            start = time.ticks_ms()
            for _ in range(_CMD_TIMEOUT):
                r = self._read1()
                if r & 0x80 == 0:
                    break
                if time.ticks_diff(time.ticks_ms(), start) >= _COMMAND_TIMEOUT_MS:
                    raise OSError("SD command timeout")
            else:
                raise OSError("SD command timeout")

            data = None
            if resp_bytes:
                data = bytearray(resp_bytes)
                self._readinto_ff(data)
            keep_selected = not release
            return r, data
        finally:
            if not keep_selected:
                self._release()

    def init_card(self):
        try:
            self._init_card()
        finally:
            self._release()

    def _init_card(self):
        # SD identification must run at <=400 kHz. The application retains its
        # historically working SPI1 keyword constructor and wiring.
        self.spi.init(baudrate=100_000, polarity=0, phase=0)
        # Provide at least 74 clocks with CS high
        self._cs(1)
        self._clock(10)

        # CMD0: go idle
        start = time.ticks_ms()
        for _ in range(200):
            r, _ = self.cmd(0, 0, 0x95)
            if r == 1:
                break
            if time.ticks_diff(time.ticks_ms(), start) >= 1000:
                raise OSError("SD init failed: CMD0 timeout")
        else:
            raise OSError("SD init failed: CMD0")

        # CMD8: check SD v2
        r, r7 = self.cmd(8, 0x1AA, 0x87, resp_bytes=4)
        if r == 1:
            if r7 != b"\x00\x00\x01\xAA":
                raise OSError("SD init failed: invalid CMD8 response")
            v2 = True
        elif r == 5:  # idle + illegal command: SD v1
            v2 = False
        else:
            raise OSError("SD init failed: CMD8")

        # ACMD41 init loop
        hcs = 0x40000000 if v2 else 0
        start = time.ticks_ms()
        for _ in range(2000):
            app_r, _ = self.cmd(55, 0, 0x01)           # APP_CMD (crc doesn't matter after init)
            if app_r not in (0, 1):
                raise OSError("SD init failed: CMD55")
            r, _ = self.cmd(41, hcs, 0x01)  # SD_SEND_OP_COND
            if r == 0:
                break
            if r != 1 or time.ticks_diff(time.ticks_ms(), start) >= 4000:
                raise OSError("SD init failed: ACMD41")
            time.sleep_ms(2)
        else:
            raise OSError("SD init failed: ACMD41")

        # CMD58: read OCR to detect SDHC/SDXC (CCS bit)
        r, ocr = self.cmd(58, 0, 0x01, resp_bytes=4)
        if r != 0 or not (ocr[0] & 0x80) or not (ocr[1] or ocr[2] & 0x80):
            raise OSError("SD init failed: CMD58/OCR")
        ccs = (ocr[0] & 0x40) != 0  # CCS bit in OCR[0]
        if ccs and not v2:
            raise OSError("SD init failed: invalid CCS")
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

    def _check_blocks(self, block_num, buf):
        if block_num < 0 or len(buf) % _BLOCK_SIZE:
            raise ValueError("SD transfers require whole 512-byte blocks")
        if len(buf) and (block_num + len(buf) // _BLOCK_SIZE - 1) * self.cdv > 0xFFFFFFFF:
            raise ValueError("SD block address out of range")

    def readblocks(self, block_num, buf):
        self._check_blocks(block_num, buf)
        mv = memoryview(buf)
        for i in range(len(buf) // _BLOCK_SIZE):
            try:
                r, _ = self.cmd(17, (block_num + i) * self.cdv, 0x01, release=False)
                if r != 0:
                    raise OSError("SD read error CMD17 r=%d" % r)
                if not self._wait_token():
                    raise OSError("SD read timeout (no data token)")
                # A memoryview slice writes into the caller's original buffer.
                self._readinto_ff(mv[i*_BLOCK_SIZE:(i+1)*_BLOCK_SIZE])
                self._clock(2)  # discard CRC
            finally:
                self._release()
        return 0

    def writeblocks(self, block_num, buf):
        self._check_blocks(block_num, buf)
        mv = memoryview(buf)
        for i in range(len(buf) // _BLOCK_SIZE):
            try:
                r, _ = self.cmd(24, (block_num + i) * self.cdv, 0x01, release=False)
                if r != 0:
                    raise OSError("SD write error CMD24 r=%d" % r)
                self._write(b"\xFF")
                self._write(b"\xFE")
                self._write(mv[i*_BLOCK_SIZE:(i+1)*_BLOCK_SIZE])
                self._write(b"\xFF\xFF")
                resp = self._read1()
                if (resp & 0x1F) != 0x05:
                    raise OSError("SD write rejected token=%02x" % resp)
                start = time.ticks_ms()
                while self._read1() != 0xFF:
                    if time.ticks_diff(time.ticks_ms(), start) >= _WRITE_TIMEOUT_MS:
                        raise OSError("SD write busy timeout")
                    time.sleep_ms(1)
            finally:
                self._release()
        return 0

    def ioctl(self, op, arg):
        # MicroPython's simple block protocol (VfsFat; no offset/erase API).
        if op == 1:  # initialized by constructor
            return 0
        if op == 2:  # deinit: caller owns the SPI peripheral
            self._release()
            return 0
        if op == 3:  # synchronous writes have already completed
            return 0
        if op == 5:
            return _BLOCK_SIZE
        if op == 4:
            return self._block_count()
        return None  # unsupported operation, never fabricated success/capacity

    def _block_count(self):
        try:
            r, _ = self.cmd(9, 0, 0x01, release=False)
            if r != 0:
                raise OSError("CMD9 failed")
            if not self._wait_token():
                raise OSError("CSD read timeout")
            csd = bytearray(16)
            self._readinto_ff(csd)
            self._clock(2)
        finally:
            self._release()

        csd_structure = (csd[0] >> 6) & 0x03
        if (csd_structure == 1 and self.cdv != 1) or (csd_structure == 0 and self.cdv != 512):
            raise OSError("CSD/addressing mismatch")
        if csd_structure == 1:
            c_size = ((csd[7] & 0x3F) << 16) | (csd[8] << 8) | csd[9]
            return (c_size + 1) * 1024
        if csd_structure == 0:
            c_size = ((csd[6] & 0x03) << 10) | (csd[7] << 2) | ((csd[8] >> 6) & 0x03)
            c_mult = ((csd[9] & 0x03) << 1) | ((csd[10] >> 7) & 0x01)
            read_bl_len = csd[5] & 0x0F
            if not 9 <= read_bl_len <= 11:
                raise OSError("Invalid SD CSD block length")
            return ((c_size + 1) * (1 << (c_mult + 2)) * (1 << read_bl_len)) // _BLOCK_SIZE
        raise OSError("Unsupported SD CSD structure")
