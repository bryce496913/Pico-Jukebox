from machine import Pin
import time

# Button pin setup
buttons = {
    "GREEN": Pin(16, Pin.IN, Pin.PULL_UP),
    "PURPLE": Pin(17, Pin.IN, Pin.PULL_UP),
    "BLUE1": Pin(19, Pin.IN, Pin.PULL_UP),
    "BLUE2": Pin(21, Pin.IN, Pin.PULL_UP)
}

print("Button test started. Press any button...")

def check_buttons():
    for name, pin in buttons.items():
        if pin.value() == 0:   # pressed (active-low)
            print(name, "pressed")
            time.sleep(0.2)    # debounce

while True:
    check_buttons()
    time.sleep(0.01)

