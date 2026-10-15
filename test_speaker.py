from machine import Pin, PWM
import time

speaker = PWM(Pin(18))

print("Sweeping tones...")

speaker.duty_u16(30000)

for freq in range(200, 2000, 50):
    speaker.freq(freq)
    time.sleep(0.05)

speaker.duty_u16(0)
print("Done.")
