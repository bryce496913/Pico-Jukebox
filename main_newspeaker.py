from machine import Pin, SPI, I2C, ADC, I2S
import sdcard
import os
import time
import random
import ustruct
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

# Potentiometer volume control (ADC0 / GP26)
pot = ADC(26)

def get_volume8():
    """Return volume in 0–255 range."""
    return pot.read_u16() >> 8

# LCD (I2C0)
i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)
lcd_addr = i2c.scan()[0]
lcd = I2cLcd(i2c, lcd_addr, 2, 16)

# SD (SPI1)
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

# MAX98357A
amp_sd = Pin(6, Pin.OUT)
amp_sd.value(1)  # amp enabled

audio_out = None

# ----------------------------------------------------
# LOAD TRACK LIST + SHUFFLE ON BOOT
# ----------------------------------------------------

tracks = [f for f in os.listdir("/sd") if f.lower().endswith(".wav")]

if not tracks:
    lcd.clear()
    lcd.putstr("NO WAV FILES")
    while True:
        time.sleep(1)

# Seed random generator differently each boot
seed = time.ticks_us() ^ pot.read_u16() ^ len(tracks)
random.seed(seed)

def shuffle_list(lst):
    for i in range(len(lst) - 1, 0, -1):
        j = random.getrandbits(16) % (i + 1)
        lst[i], lst[j] = lst[j], lst[i]

shuffle_list(tracks)

index = 0
state = "STOPPED"

# ----------------------------------------------------
# UI HELPERS
# ----------------------------------------------------

def update_display():
    lcd.clear()
    lcd.move_to(0, 0)
    lcd.putstr(state[:16])
    lcd.move_to(0, 1)
    lcd.putstr(tracks[index][:16])

def update_led():
    if state == "PLAYING":
        led.green_on()
    elif state == "PAUSED":
        led.blue_on()
    elif state == "STOPPED":
        led.red_on()
    else:
        led.off()

def stop_audio():
    global audio_out
    if audio_out is not None:
        try:
            audio_out.deinit()
        except:
            pass
        audio_out = None

# ----------------------------------------------------
# WAV PLAYER (MAX98357A / I2S)
# Supports:
# - 8-bit unsigned PCM mono
# - 16-bit signed PCM mono
# ----------------------------------------------------

def play_wav(path):
    global state, index, audio_out

    try:
        with open(path, "rb") as f:
            header = f.read(44)

            channels = ustruct.unpack("<H", header[22:24])[0]
            sample_rate = ustruct.unpack("<I", header[24:28])[0]
            bits_per_sample = ustruct.unpack("<H", header[34:36])[0]

            if channels != 1:
                print("Unsupported WAV: only MONO supported")
                state = "STOPPED"
                return

            if bits_per_sample not in (8, 16):
                print("Unsupported WAV bits:", bits_per_sample)
                state = "STOPPED"
                return

            # Set up I2S for this file
            audio_out = I2S(
                0,
                sck=Pin(2),      # BCLK
                ws=Pin(3),       # LRC / WS
                sd=Pin(4),       # DIN
                mode=I2S.TX,
                bits=16,
                format=I2S.MONO,
                rate=sample_rate,
                ibuf=16000
            )

            amp_sd.value(1)

            while state == "PLAYING" and slide.value() == 0:
                in_buf = f.read(512)
                if not in_buf:
                    break

                volume8 = get_volume8()  # 0..255

                if bits_per_sample == 8:
                    # 8-bit unsigned PCM -> 16-bit signed PCM
                    out_buf = bytearray(len(in_buf) * 2)

                    for i, b in enumerate(in_buf):
                        sample16 = ((b - 128) * volume8)
                        out_buf[2 * i] = sample16 & 0xFF
                        out_buf[2 * i + 1] = (sample16 >> 8) & 0xFF

                else:
                    # 16-bit signed PCM -> scaled 16-bit signed PCM
                    samples = len(in_buf) // 2
                    out_buf = bytearray(len(in_buf))

                    for i in range(samples):
                        lo = in_buf[2 * i]
                        hi = in_buf[2 * i + 1]
                        sample16 = ustruct.unpack("<h", bytes([lo, hi]))[0]
                        sample16 = (sample16 * volume8) // 255
                        out_buf[2 * i] = sample16 & 0xFF
                        out_buf[2 * i + 1] = (sample16 >> 8) & 0xFF

                audio_out.write(out_buf)

                # --- BUTTONS DURING PLAYBACK ---
                if btn_stop.value() == 0:
                    state = "STOPPED"
                    stop_audio()
                    return

                if btn_play.value() == 0:
                    state = "PAUSED"
                    stop_audio()
                    return

                if btn_next.value() == 0:
                    index = (index + 1) % len(tracks)
                    state = "PLAYING"
                    stop_audio()
                    return

                if btn_prev.value() == 0:
                    index = (index - 1) % len(tracks)
                    state = "PLAYING"
                    stop_audio()
                    return

            stop_audio()

    except Exception as e:
        print("WAV error:", e)
        stop_audio()
        state = "STOPPED"

# ----------------------------------------------------
# INITIAL UI
# ----------------------------------------------------

update_display()
update_led()

# ----------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------

last_power_off = False

while True:

    # -------------------------------
    # POWER OFF (slide switch)
    # -------------------------------
    if slide.value() == 1:
        stop_audio()
        led.off()

        if not last_power_off:
            lcd.clear()
            lcd.putstr("POWER OFF")
            last_power_off = True

        time.sleep(0.2)
        continue

    if last_power_off:
        update_display()
        update_led()
        last_power_off = False

    # -------------------------------
    # PLAY / PAUSE BUTTON
    # -------------------------------
    if btn_play.value() == 0:
        if state == "STOPPED":
            state = "PLAYING"
        elif state == "PLAYING":
            state = "PAUSED"
            stop_audio()
        elif state == "PAUSED":
            state = "PLAYING"

        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # STOP BUTTON
    # -------------------------------
    if btn_stop.value() == 0:
        stop_audio()
        state = "STOPPED"
        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # NEXT TRACK
    # -------------------------------
    if btn_next.value() == 0:
        stop_audio()
        index = (index + 1) % len(tracks)
        state = "STOPPED"
        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # PREVIOUS TRACK
    # -------------------------------
    if btn_prev.value() == 0:
        stop_audio()
        index = (index - 1) % len(tracks)
        state = "STOPPED"
        update_display()
        update_led()
        time.sleep(0.25)

    # -------------------------------
    # PLAYBACK LOOP
    # -------------------------------
    if state == "PLAYING":
        play_wav("/sd/" + tracks[index])
        stop_audio()
        state = "STOPPED"
        update_display()
        update_led()