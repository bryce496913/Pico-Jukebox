from machine import Pin, PWM

class RGB_LED:
    def __init__(self, r_pin, g_pin, b_pin):
        self.red = self.green = self.blue = None
        self.pins = (r_pin, g_pin, b_pin)
        try:
            for name, number in zip(("red", "green", "blue"), self.pins):
                channel = PWM(Pin(number))
                setattr(self, name, channel)
                channel.duty_u16(0)  # mute each output before further setup
                channel.freq(1000)
        except BaseException:
            self.deinit()
            raise

    def deinit(self):
        # Release every allocated channel even if an earlier operation fails.
        for name in ("red", "green", "blue"):
            channel = getattr(self, name)
            if channel is not None:
                try:
                    channel.duty_u16(0)
                except Exception:
                    pass
                try:
                    channel.deinit()
                except Exception:
                    pass
                setattr(self, name, None)
        for number in self.pins:
            try:
                Pin(number, Pin.OUT, value=0)
            except Exception:
                pass

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
