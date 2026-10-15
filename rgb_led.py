from machine import Pin, PWM

class RGB_LED:
    def __init__(self, r_pin, g_pin, b_pin):
        self.red = PWM(Pin(r_pin))
        self.green = PWM(Pin(g_pin))
        self.blue = PWM(Pin(b_pin))

        # PWM at 1 kHz
        self.red.freq(1000)
        self.green.freq(1000)
        self.blue.freq(1000)

        # Turn off initially
        self.off()

    def _duty(self, value):
        """Value 0-255 mapped to 0-65535."""
        return int(value * 257)

    def color(self, r, g, b):
        self.red.duty_u16(self._duty(r))
        self.green.duty_u16(self._duty(g))
        self.blue.duty_u16(self._duty(b))

    def off(self):
        self.color(0, 0, 0)

    def red_on(self):
        self.color(255, 0, 0)

    def green_on(self):
        self.color(0, 255, 0)

    def blue_on(self):
        self.color(0, 0, 255)

    def white(self):
        self.color(255, 255, 255)
