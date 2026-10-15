"""Host-only fakes. Never import the hardware diagnostic scripts in discovery."""
import ast
import builtins
import io
import struct
from pathlib import Path
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def riff_chunk(name, data):
    return name + struct.pack('<I', len(data)) + data + (b'\0' if len(data) & 1 else b'')


def wav_bytes(samples=b'\0\x80\xff', extra=b'', trailing=b'', **format_fields):
    fields = dict(encoding=1, channels=1, rate=8000, byte_rate=8000, align=1, bits=8)
    fields.update(format_fields)
    fmt = struct.pack('<HHIIHH', *(fields[k] for k in ('encoding', 'channels', 'rate', 'byte_rate', 'align', 'bits')))
    body = b'WAVE' + extra + riff_chunk(b'fmt ', fmt) + riff_chunk(b'data', samples) + trailing
    return b'RIFF' + struct.pack('<I', len(body)) + body


class Pin:
    IN, OUT, PULL_UP = 0, 1, 2

    def __init__(self, number, mode=None, pull=None):
        self.number, self.mode, self.pull = number, mode, pull
        self.level = 1

    def value(self, value=None):
        if value is not None:
            self.level = value
        return self.level

    def init(self, mode, value=1):
        self.mode, self.level = mode, value


class PWM:
    def __init__(self, pin):
        self.pin, self.duties, self.frequency = pin, [], None

    def freq(self, value):
        self.frequency = value

    def duty_u16(self, value):
        self.duties.append(value)


class ADC:
    def __init__(self, number):
        self.number, self.reading = number, 65535

    def read_u16(self):
        return self.reading


class Bus:
    MSB = 0

    def __init__(self, number, **kwargs):
        self.number, self.kwargs, self.writes = number, kwargs, []

    def scan(self):
        return [0x27]

    def writeto(self, addr, data):
        self.writes.append((addr, bytes(data)))


class Clock:
    def __init__(self):
        self.us, self.sleeps = 0, []

    def sleep_us(self, us):
        self.us += us
        self.sleeps.append(us)

    def sleep_ms(self, ms):
        self.sleep_us(ms * 1000)

    def sleep(self, seconds):
        self.sleep_us(int(seconds * 1_000_000))

    def ticks_ms(self):
        return (self.us // 1000) % (1 << 30)

    def ticks_us(self):
        return self.us % (1 << 30)

    @staticmethod
    def ticks_diff(a, b):
        return ((a - b + (1 << 29)) % (1 << 30)) - (1 << 29)


class LCD:
    def __init__(self, *args):
        self.lines, self.row = {}, 0

    def clear(self):
        self.lines.clear()
        self.row = 0

    def move_to(self, col, row):
        self.row = row

    def putstr(self, text):
        self.lines[self.row] = text

    def backlight_on(self):
        pass


class EndLoop(BaseException):
    pass


class Harness:
    def __init__(self, files=None, devices=None):
        self.clock = Clock()
        self.machine = types.SimpleNamespace(Pin=Pin, PWM=PWM, ADC=ADC, SPI=Bus, I2C=Bus)
        self.files = files if files is not None else {'b.wav': wav_bytes(), 'A.WAV': wav_bytes(b'\x80'), 'ignore.txt': b''}
        self.handles, self.mounts, self.unmounts = [], [], []
        self.os = types.SimpleNamespace(listdir=lambda path: list(self.files), mount=lambda sd, path: self.mounts.append(path),
                                        umount=lambda path: self.unmounts.append(path),
                                        stat=lambda path: (0x8000,))
        self.random = types.SimpleNamespace(seed=lambda seed: None, getrandbits=lambda bits: 1)
        self.modules = {'machine': self.machine, 'time': self.clock, 'os': self.os,
                        'random': self.random, 'micropython': types.SimpleNamespace(const=lambda n: n)}
        self.modules['rgb_led'] = self.load('rgb_led.py')
        self.modules['wav_reader'] = self.load('wav_reader.py')
        self.modules['sdcard'] = types.SimpleNamespace(SDCard=lambda spi, cs: object())
        self.modules['i2c_lcd'] = types.SimpleNamespace(I2cLcd=LCD)
        if devices is not None:
            self.machine.I2C = lambda *a, **kw: types.SimpleNamespace(scan=lambda: devices)

    def load(self, name):
        module = types.ModuleType(name[:-3])
        with patch.dict('sys.modules', self.modules):
            exec(compile((ROOT / name).read_text(), name, 'exec'), module.__dict__)
        return module

    def open(self, path, mode):
        name = path.removeprefix('/sd/')
        if name not in self.files:
            raise OSError('File missing')
        data = self.files[name]
        handle = data() if callable(data) else io.BytesIO(data)
        self.handles.append(handle)
        return handle

    def boot(self):
        tree = ast.parse((ROOT / 'main.py').read_text())
        # Only the final module-level loop is deferred; startup and all function
        # bodies execute unchanged. No production import guard is introduced.
        self.loop = tree.body.pop()
        assert isinstance(self.loop, ast.While)
        self.env = {'open': self.open}
        with patch.dict('sys.modules', self.modules), patch.object(builtins, 'print'):
            exec(compile(tree, 'main.py', 'exec'), self.env)
        return self.env

    def run_loop(self, iterations=1):
        remaining = iterations

        def step():
            nonlocal remaining
            if remaining == 0:
                raise EndLoop()
            remaining -= 1

        tree = ast.parse((ROOT / 'main.py').read_text())
        loop = tree.body[-1]
        loop.body.insert(0, ast.Expr(ast.Call(ast.Name('_step', ast.Load()), [], [])))
        bounded = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
        self.env['_step'] = step
        try:
            exec(compile(bounded, 'main.py', 'exec'), self.env)
        except EndLoop:
            pass
