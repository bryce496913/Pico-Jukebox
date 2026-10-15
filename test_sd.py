"""Manual DESTRUCTIVE diagnostic: overwrites /sd/hello.txt. Use a spare card.

Stop main.py and reset the Pico before/after running; do not use on a music card.
"""
import os
from machine import Pin, SPI
import sdcard

# Your wiring:
# CS   GP13
# SCK  GP10
# MOSI GP11
# MISO GP12

cs = Pin(13, Pin.OUT)

# Start slow for reliability; driver will speed up after init
spi = SPI(1,
          baudrate=1_000_000,
          polarity=0,
          phase=0,
          bits=8,
          firstbit=SPI.MSB,
          sck=Pin(10),
          mosi=Pin(11),
          miso=Pin(12))

print("Initializing SD card...")
sd = sdcard.SDCard(spi, cs)

print("Mounting /sd ...")
vfs = os.VfsFat(sd)
os.mount(vfs, "/sd")

try:
    print("Mounted! Contents:")
    print(os.listdir("/sd"))

    # Write/read test
    test_path = "/sd/hello.txt"
    with open(test_path, "w") as f:
        f.write("hello from pico!\n")

    with open(test_path, "r") as f:
        print("Read back:", f.read().strip())

    print("Done.")
finally:
    os.umount("/sd")
