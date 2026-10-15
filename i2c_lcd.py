"""
MicroPython I2C LCD driver for PCF8574 backpacks
Supports 16x2 and 20x4 LCDs
"""

import time
from machine import I2C

# LCD commands
LCD_CLR             = 0x01
LCD_HOME            = 0x02
LCD_ENTRY_MODE      = 0x04
LCD_DISPLAY_CTRL    = 0x08
LCD_CURSOR_SHIFT    = 0x10
LCD_FUNCTION_SET    = 0x20
LCD_SET_CGRAM_ADDR  = 0x40
LCD_SET_DDRAM_ADDR  = 0x80

# Entry modes
ENTRY_LEFT          = 0x02
ENTRY_SHIFT_DEC     = 0x00

# Display control flags
DISPLAY_ON          = 0x04
CURSOR_OFF          = 0x00
BLINK_OFF           = 0x00

# Function set flags
EIGHT_BIT_MODE      = 0x10
TWO_LINE            = 0x08
FIVE_BY_EIGHT_DOTS  = 0x00

# Enable bit
ENABLE = 0b00000100
RW     = 0b00000010
RS     = 0b00000001

class I2cLcd:
    """Driver for PCF8574 I2C LCD displays"""

    def __init__(self, i2c, addr, rows, cols):
        self.i2c = i2c
        self.addr = addr
        self.rows = rows
        self.cols = cols
        self.backlight = 0x08  # LCD backlight on

        # Initialize display
        self._init_lcd()

    def _i2c_write(self, data):
        self.i2c.writeto(self.addr, bytearray([data | self.backlight]))

    def _send_nibble(self, nibble, mode):
        data = nibble & 0xF0
        if mode:
            data |= RS
        # High pulse
        self._i2c_write(data | ENABLE)
        time.sleep_us(500)
        # Low pulse
        self._i2c_write(data & ~ENABLE)
        time.sleep_us(100)

    def _send_byte(self, value, mode=0):
        self._send_nibble(value & 0xF0, mode)
        self._send_nibble((value << 4) & 0xF0, mode)

    def _send_command(self, cmd):
        self._send_byte(cmd, mode=0)

    def _send_data(self, data):
        self._send_byte(data, mode=1)

    def _init_lcd(self):
        # Start in 4-bit mode
        time.sleep_ms(20)
        self._send_nibble(0x30, 0)
        time.sleep_ms(5)
        self._send_nibble(0x30, 0)
        time.sleep_us(150)
        self._send_nibble(0x30, 0)
        time.sleep_ms(5)
        self._send_nibble(0x20, 0)
        time.sleep_ms(5)

        # Function set
        self._send_command(LCD_FUNCTION_SET | TWO_LINE | FIVE_BY_EIGHT_DOTS)
        # Display control
        self._send_command(LCD_DISPLAY_CTRL | DISPLAY_ON | CURSOR_OFF | BLINK_OFF)
        # Entry mode
        self._send_command(LCD_ENTRY_MODE | ENTRY_LEFT | ENTRY_SHIFT_DEC)
        self.clear()

    def clear(self):
        self._send_command(LCD_CLR)
        time.sleep_ms(2)

    def home(self):
        self._send_command(LCD_HOME)
        time.sleep_ms(2)

    def move_to(self, col, row):
        row_offsets = [0x00, 0x40, 0x14, 0x54]  # support 16x4 or 20x4
        if row >= self.rows:
            row = self.rows - 1
        self._send_command(LCD_SET_DDRAM_ADDR | (col + row_offsets[row]))

    def putstr(self, string):
        for char in string:
            self._send_data(ord(char))

    def backlight_on(self):
        self.backlight = 0x08
        self._i2c_write(0)

    def backlight_off(self):
        self.backlight = 0x00
        self._i2c_write(0)
