"""Manual GP18 PWM sweep; stop main.py first. Not a host test."""
from machine import Pin, PWM
import time

speaker = PWM(Pin(18))
try:
    speaker.duty_u16(0)
    print("Sweeping tones...")
    speaker.duty_u16(2000)  # conservative level, matching the boot test
    for freq in range(200, 2000, 50):
        speaker.freq(freq)
        time.sleep(0.05)
finally:
    try:
        speaker.duty_u16(0)
    finally:
        speaker.deinit()
print("Done.")
