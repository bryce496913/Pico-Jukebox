from machine import Pin, I2S
import math
import time

# -----------------------------
# PIN CONFIG
# -----------------------------
PIN_BCLK = 2
PIN_LRC = 3
PIN_DIN = 4
PIN_SD = 6

# Enable amplifier
amp_sd = Pin(PIN_SD, Pin.OUT)
amp_sd.value(1)

# -----------------------------
# I2S CONFIG
# -----------------------------
SAMPLE_RATE = 22050
BITS = 16
BUFFER_LEN = 256
TONE_FREQ = 440
AMPLITUDE = 12000

audio_out = I2S(
    0,
    sck=Pin(PIN_BCLK),
    ws=Pin(PIN_LRC),
    sd=Pin(PIN_DIN),
    mode=I2S.TX,
    bits=BITS,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=4000
)

# -----------------------------
# BUILD SINE BUFFER
# -----------------------------
buf = bytearray(BUFFER_LEN * 2)

for i in range(BUFFER_LEN):
    sample = int(AMPLITUDE * math.sin(2 * math.pi * TONE_FREQ * i / SAMPLE_RATE))
    buf[2*i] = sample & 0xff
    buf[2*i+1] = (sample >> 8) & 0xff

print("Playing test tone...")

end_time = time.ticks_add(time.ticks_ms(), 3000)

while time.ticks_diff(end_time, time.ticks_ms()) > 0:
    audio_out.write(buf)

audio_out.deinit()
amp_sd.value(0)

print("Done")