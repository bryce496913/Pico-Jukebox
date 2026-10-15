from rgb_led import RGB_LED
import time

# LED pins: R=GP5, G=GP8, B=GP9
led = RGB_LED(5, 8, 9)

print("Cycling LED colors...")

led.red_on()
print("Red")
time.sleep(1)

led.green_on()
print("Green")
time.sleep(1)

led.blue_on()
print("Blue")
time.sleep(1)

led.white()
print("White")
time.sleep(1)

led.off()
print("Off")

print("LED test complete.")
