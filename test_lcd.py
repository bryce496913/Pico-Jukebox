from machine import Pin, I2C
from i2c_lcd import I2cLcd
import time

print("Starting LCD visibility test...")

i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=100000)

devices = i2c.scan()
print("I2C devices found:", devices)

if not devices:
    print("No I2C devices found")
    while True:
        time.sleep(1)

lcd = I2cLcd(i2c, 0x27, 2, 16)

while True:
    lcd.backlight_on()
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("HELLO BRYCE")
    lcd.move_to(0, 1)
    lcd.putstr("LCD WORKING")
    print("Text written")
    time.sleep(2)

    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("PICO JUKEBOX")
    lcd.move_to(0, 1)
    lcd.putstr("ADDR 0x27")
    time.sleep(2)