from machine import Pin
import time

# Slide switch on GP22 (active LOW)
slide = Pin(22, Pin.IN, Pin.PULL_UP)

print("Slide switch test running...")

while True:
    if slide.value() == 0:
        print("SWITCH: ON")
    else:
        print("SWITCH: OFF")
    time.sleep(0.5)
