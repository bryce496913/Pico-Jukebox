from machine import Pin, PWM, SPI, I2C, ADC
import sdcard
import os
import time
import random
from i2c_lcd import I2cLcd
from rgb_led import RGB_LED

# ----------------------------------------------------
# HARDWARE SETUP
# ----------------------------------------------------

# Slide switch (Power control)
slide = Pin(22, Pin.IN, Pin.PULL_UP)

# Buttons
btn_play = Pin(16, Pin.IN, Pin.PULL_UP)
btn_stop = Pin(17, Pin.IN, Pin.PULL_UP)
btn_next = Pin(19, Pin.IN, Pin.PULL_UP)
btn_prev = Pin(21, Pin.IN, Pin.PULL_UP)

# RGB LED (R=GP5, G=GP8, B=GP9)
led = RGB_LED(5, 8, 9)

# Speaker (PWM on GP18)
speaker = PWM(Pin(18))
speaker.freq(40000)
speaker.duty_u16(0)

# Potentiometer volume control (ADC0 / GP26)
pot = ADC(26)

def get_volume8():
    """Return volume in 0-255 range."""
    return pot.read_u16() >> 8

# LCD (I2C0)
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=100000)

devices = i2c.scan()
print("I2C devices found:", devices)

if not devices:
    print("No I2C devices found")
    while True:
        time.sleep(1)

lcd_addr = 0x27
if lcd_addr not in devices:
    lcd_addr = devices[0]

lcd = I2cLcd(i2c, lcd_addr, 2, 16)

# ----------------------------------------------------
# BOOT TESTS
# ----------------------------------------------------

def boot_lcd_test():
    print("Starting LCD boot test...")

    lcd.backlight_on()

    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("HELLO BRYCE")
    lcd.move_to(0, 1)
    lcd.putstr("LCD WORKING")
    print("LCD test screen 1 written")
    time.sleep(2)

    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("PICO JUKEBOX")
    lcd.move_to(0, 1)
    lcd.putstr("ADDR 0x27")
    print("LCD test screen 2 written")
    time.sleep(2)

    lcd.clear()

def boot_speaker_test():
    print("Starting speaker boot test...")

    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr("SPEAKER TEST")
    lcd.move_to(0, 1)
    lcd.putstr("SWEEPING TONES")

    speaker.duty_u16(30000)

    for freq in range(200, 2000, 50):
        speaker.freq(freq)
        time.sleep(0.05)

    speaker.duty_u16(0)
    speaker.freq(40000)

    print("Speaker boot test done.")
    time.sleep(0.5)
    lcd.clear()

boot_lcd_test()
boot_speaker_test()

# ----------------------------------------------------
# SD SETUP
# ----------------------------------------------------

spi = SPI(1,
          baudrate=1_000_000,
          polarity=0,
          phase=0,
          bits=8,
          firstbit=SPI.MSB,
          sck=Pin(10),
          mosi=Pin(11),
          miso=Pin(12))

cs = Pin(13, Pin.OUT)
sd = sdcard.SDCard(spi, cs)
os.mount(sd, "/sd")

# ----------------------------------------------------
# LOAD TRACK LIST
# ----------------------------------------------------

tracks = [f for f in os.listdir("/sd") if f.lower().endswith(".wav")]
tracks.sort()

if not tracks:
    lcd.clear()
    lcd.putstr("NO WAV FILES")
    while True:
        time.sleep(1)

# ----------------------------------------------------
# RANDOM / SHUFFLE HELPERS
# ----------------------------------------------------

def seed_random():
    seed = time.ticks_us() ^ pot.read_u16() ^ len(tracks)
    random.seed(seed)

def random_track_index(current_index=None):
    if len(tracks) <= 1:
        return 0

    new_index = current_index

    while new_index == current_index:
        new_index = random.getrandbits(16) % len(tracks)

    return new_index

seed_random()

index = random_track_index(None)
state = "STOPPED"

# ----------------------------------------------------
# LCD SCROLLING TITLE HELPERS
# ----------------------------------------------------

LCD_WIDTH = 16
SCROLL_INTERVAL_MS = 350

title_scroll_pos = 0
last_scroll_ms = 0

def pad_lcd(text):
    text = str(text)

    if len(text) > LCD_WIDTH:
        return text[:LCD_WIDTH]

    return text + (" " * (LCD_WIDTH - len(text)))

def clean_title(filename):
    title = filename

    if title.lower().endswith(".wav"):
        title = title[:-4]

    title = title.replace("_", " ")
    return title

def write_lcd_line(row, text):
    lcd.move_to(0, row)
    lcd.putstr(pad_lcd(text))

def current_title_window():
    title = clean_title(tracks[index])

    if len(title) <= LCD_WIDTH:
        return pad_lcd(title)

    padded = title + "   "
    looped = padded + padded
    start = title_scroll_pos % len(padded)

    return looped[start:start + LCD_WIDTH]

def reset_title_scroll():
    global title_scroll_pos, last_scroll_ms
    title_scroll_pos = 0
    last_scroll_ms = time.ticks_ms()

def update_title_scroll(force=False):
    global title_scroll_pos, last_scroll_ms

    title = clean_title(tracks[index])

    if len(title) <= LCD_WIDTH:
        if force:
            write_lcd_line(1, title)
        return

    now = time.ticks_ms()

    if force or time.ticks_diff(now, last_scroll_ms) >= SCROLL_INTERVAL_MS:
        write_lcd_line(1, current_title_window())
        title_scroll_pos += 1
        last_scroll_ms = now

# ----------------------------------------------------
# LED MUSIC REACTION
# ----------------------------------------------------

def update_music_led(chunk):
    if not chunk:
        return

    peak = 0

    for i in range(0, len(chunk), 8):
        amp = chunk[i] - 128
        if amp < 0:
            amp = -amp

        if amp > peak:
            peak = amp

    brightness = 10 + ((peak * 245) // 127)

    if brightness > 255:
        brightness = 255

    led.color(0, brightness, 0)

# ----------------------------------------------------
# WAV PLAYER
# ----------------------------------------------------

def play_wav(path):
    global state, index

    try:
        with open(path, "rb") as f:
            f.read(44)

            chunk = f.read(512)

            while chunk and state == "PLAYING" and slide.value() == 0:

                update_music_led(chunk)

                for b in chunk:
                    volume8 = pot.read_u16() >> 8
                    gain = volume8 // 25
                    level = b * gain

                    speaker.duty_u16(level)

                    time.sleep_us(10)

                    if btn_stop.value() == 0:
                        state = "STOPPED"
                        speaker.duty_u16(0)
                        return "stopped"

                    if btn_play.value() == 0:
                        state = "PAUSED"
                        speaker.duty_u16(0)
                        return "paused"

                    if btn_next.value() == 0:
                        index = random_track_index(index)
                        state = "PLAYING"
                        speaker.duty_u16(0)
                        return "track_change"

                    if btn_prev.value() == 0:
                        index = (index - 1) % len(tracks)
                        state = "PLAYING"
                        speaker.duty_u16(0)
                        return "track_change"

                update_title_scroll()

                chunk = f.read(512)

            return "ended"

    except Exception as e:
        print("WAV error:", e)
        state = "STOPPED"
        return "error"

# ----------------------------------------------------
# UI HELPERS
# ----------------------------------------------------

def update_display():
    lcd.clear()

    lcd.move_to(0, 0)
    lcd.putstr(pad_lcd(state))

    reset_title_scroll()
    update_title_scroll(force=True)

def update_led():
    if state == "PLAYING":
        led.green_on()
    elif state == "PAUSED":
        led.blue_on()
    elif state == "STOPPED":
        led.red_on()
    else:
        led.off()

# ----------------------------------------------------
# INITIAL UI
# ----------------------------------------------------

update_display()
update_led()

# ----------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------

power_off_displayed = False

while True:

    # -------------------------------
    # POWER OFF
    # -------------------------------
    if slide.value() == 1:
        speaker.duty_u16(0)
        led.off()

        if not power_off_displayed:
            lcd.clear()
            lcd.putstr("POWER OFF")
            power_off_displayed = True

        time.sleep(0.2)
        continue

    if power_off_displayed:
        power_off_displayed = False
        update_display()
        update_led()

    if state != "PLAYING":
        update_title_scroll()

    # -------------------------------
    # PLAY / PAUSE BUTTON
    # -------------------------------
    if btn_play.value() == 0:
        if state == "STOPPED":
            state = "PLAYING"
        elif state == "PLAYING":
            state = "PAUSED"
            speaker.duty_u16(0)
        elif state == "PAUSED":
            state = "PLAYING"

        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # STOP BUTTON
    # -------------------------------
    if btn_stop.value() == 0:
        speaker.duty_u16(0)
        state = "STOPPED"
        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # NEXT TRACK - RANDOM
    # -------------------------------
    if btn_next.value() == 0:
        index = random_track_index(index)
        state = "STOPPED"
        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # PREVIOUS TRACK
    # -------------------------------
    if btn_prev.value() == 0:
        index = (index - 1) % len(tracks)
        state = "STOPPED"
        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # PLAYBACK LOOP
    # -------------------------------
    if state == "PLAYING":
        result = play_wav("/sd/" + tracks[index])
        speaker.duty_u16(0)

        if result == "track_change":
            update_display()
            update_led()
            time.sleep(0.25)
            continue

        if result == "paused":
            update_display()
            update_led()
            time.sleep(0.25)
            continue

        if result == "stopped":
            update_display()
            update_led()
            time.sleep(0.25)
            continue

        if result == "ended":
            index = random_track_index(index)
            state = "PLAYING"
            update_display()
            update_led()
            time.sleep(0.1)
            continue

        state = "STOPPED"
        update_display()
        update_led()