from machine import Pin, PWM, SPI, I2C, ADC
import sdcard
import os
import time
import random
from i2c_lcd import I2cLcd
from rgb_led import RGB_LED
from wav_reader import audio_data, read_exact, WavError
from buttons import ButtonLatch

# ----------------------------------------------------
# STARTUP AND SAFE HARDWARE OPERATIONS
# ----------------------------------------------------
SPEAKER_CARRIER_HZ = 40000
BOOT_SPEAKER_DUTY = 2000  # ~3% duty; formerly 30000. Verify audibility on device.
LCD_I2C_HZ = 100000
LCD_ADDRESS = 0x27

slide = speaker_pin = speaker = led = pot = lcd = i2c = None
btn_play = btn_stop = btn_next = btn_prev = None
controls = None
resume_path = None
resume_offset = 0
resume_size = 0
hardware_error = storage_error = None
storage_mounted = storage_complete = False
spi = cs = sd = None
outputs_released = False
tracks = []
index = 0
state = "STOPPED"
startup_complete = False
lcd_test_attempted = speaker_test_attempted = False
lcd_lines = [None, None]


def clear_resume():
    global resume_path, resume_offset, resume_size
    resume_path = None
    resume_offset = 0
    resume_size = 0


def mute_speaker():
    """Best-effort silence, including a PWM API failure; never mask cleanup."""
    global speaker, hardware_error, state
    if speaker is None:
        return
    try:
        speaker.duty_u16(0)
        return
    except Exception as e:
        print("Speaker mute failed:", e)
        hardware_error = "SPEAKER ERROR"
        state = "STOPPED"
        clear_resume()
    try:
        speaker.deinit()
    except Exception as e:
        print("Speaker deinit failed:", e)
    speaker = None
    try:
        speaker_pin.init(Pin.OUT, value=0)
    except Exception as e:
        print("Speaker GPIO fallback failed:", e)


def shutdown_outputs():
    """Release outputs after an aborted startup or an interpreter interrupt."""
    global speaker, led, storage_mounted, outputs_released, state
    state = "STOPPED"
    clear_resume()
    if outputs_released:
        return
    outputs_released = True
    mute_speaker()
    if speaker is not None:
        try:
            speaker.deinit()
        except Exception as e:
            print("Speaker deinit failed:", e)
        speaker = None
    if speaker_pin is not None:
        try:
            speaker_pin.init(Pin.OUT, value=0)
        except Exception as e:
            print("Speaker GPIO fallback failed:", e)
    if led is not None:
        led.deinit()
        led = None
    if storage_mounted:
        try:
            os.umount("/sd")
        except Exception as e:
            print("SD unmount failed:", e)
        storage_mounted = False


def hardware_fault(message, error):
    global hardware_error, state
    # Latch the first fault. Never retry initialization/self-tests in a fault loop.
    if hardware_error is None:
        hardware_error = message
        print(message + ":", error)
    state = "STOPPED"
    clear_resume()
    mute_speaker()


def power_on():
    if slide is None or hardware_error == "GPIO ERROR":
        return False
    try:
        return slide.value() == 0
    except Exception as e:
        hardware_fault("GPIO ERROR", e)
        return False


def lcd_call(method, *args):
    global lcd
    if lcd is None:
        return False
    try:
        getattr(lcd, method)(*args)
        return True
    except Exception as e:
        lcd = None  # suppress repeated bus writes/logging after a failed display
        hardware_fault("LCD ERROR", e)
        return False


def led_call(method, *args):
    global led
    if led is None:
        return
    try:
        getattr(led, method)(*args)
    except Exception as e:
        led.deinit()
        led = None
        hardware_fault("RGB ERROR", e)


def boot_wait(milliseconds):
    # Bound OFF response during screens, sweep and its settling delay to 20 ms,
    # excluding time spent inside a synchronous hardware call.
    while milliseconds > 0:
        if not power_on() or hardware_error:
            return False
        delay = min(20, milliseconds)
        time.sleep_ms(delay)
        milliseconds -= delay
    return power_on() and hardware_error is None


def show_screen(first, second=""):
    write_lcd_line(0, first)
    write_lcd_line(1, second)


def boot_lcd_test():
    global lcd_test_attempted
    if lcd_test_attempted:
        return
    lcd_test_attempted = True
    print("Starting LCD boot test...")
    show_screen("HELLO BRYCE", "LCD WORKING")
    if boot_wait(2000):
        show_screen("PICO JUKEBOX", "ADDR 0x%02X" % lcd_addr)
        boot_wait(2000)


def boot_speaker_test():
    global speaker_test_attempted
    if speaker_test_attempted:
        return
    speaker_test_attempted = True
    print("Starting speaker boot test...")
    show_screen("SPEAKER TEST", "SWEEPING TONES")
    try:
        if power_on() and hardware_error is None:
            speaker.duty_u16(BOOT_SPEAKER_DUTY)
            for frequency in range(200, 2000, 50):
                if not power_on() or hardware_error:
                    break
                speaker.freq(frequency)
                if not boot_wait(50):
                    break
    finally:
        mute_speaker()
        if speaker is not None:
            try:
                speaker.freq(SPEAKER_CARRIER_HZ)
            except Exception as e:
                hardware_fault("SPEAKER ERROR", e)
                shutdown_outputs()
    print("Speaker boot test done.")
    boot_wait(500)


def initialize_storage():
    global spi, cs, sd, tracks, storage_error, storage_mounted, storage_complete
    if storage_complete:
        return True
    try:
        if spi is None:
            spi = SPI(1,
                      baudrate=1_000_000,
                      polarity=0,
                      phase=0,
                      bits=8,
                      firstbit=SPI.MSB,
                      sck=Pin(10),
                      mosi=Pin(11),
                      miso=Pin(12))
            cs = Pin(13, Pin.OUT, value=1)
        if not power_on():
            return False
        if sd is None:
            sd = sdcard.SDCard(spi, cs)
        if not power_on():
            return False
        if not storage_mounted:
            os.mount(sd, "/sd")
            storage_mounted = True
        if not power_on():
            return False

        # Keep case-insensitive WAV discovery and the original sorted index order.
        tracks = []
        for name in sorted(os.listdir("/sd")):
            if not power_on():
                return False
            if not name.lower().endswith(".wav"):
                continue
            path = "/sd/" + name
            try:
                if os.stat(path)[0] & 0xF000 != 0x8000:
                    continue
                with open(path, "rb") as music:
                    audio_data(music)
                tracks.append(name)
            except (OSError, WavError) as e:
                print("Skipping WAV:", name, e)
        if not tracks:
            storage_error = "NO WAV FILES"
    except Exception as e:
        print("SD storage error:", e)
        storage_error = "SD ERROR"
        tracks = []
        if storage_mounted:
            try:
                os.umount("/sd")
            except OSError:
                pass
            storage_mounted = False
    finally:
        mute_speaker()
    storage_complete = True
    return True


def initialize_hardware():
    """Resume deferred stages only on ON; completed stages/tests never repeat."""
    global i2c, lcd, lcd_addr, led, pot, speaker, startup_complete
    global btn_play, btn_stop, btn_next, btn_prev, index, outputs_released, controls
    if startup_complete or hardware_error or not power_on():
        return
    outputs_released = False
    stage = "RGB ERROR"
    try:
        if led is None:
            led = RGB_LED(5, 8, 9)
        if not power_on():
            return
        stage = "LCD ERROR"
        if lcd is None:
            i2c = I2C(0, sda=Pin(0), scl=Pin(1), freq=LCD_I2C_HZ)
            devices = i2c.scan()
            print("I2C devices found:", devices)
            if not devices:
                raise OSError("No I2C devices on I2C0 (SDA GP0, SCL GP1)")
            if LCD_ADDRESS in devices:
                lcd_addr = LCD_ADDRESS
            else:
                candidates = [addr for addr in devices
                              if 0x20 <= addr <= 0x27 or 0x38 <= addr <= 0x3F]
                if len(candidates) != 1:
                    raise OSError("Expected LCD 0x27; no unambiguous PCF8574 address")
                lcd_addr = candidates[0]
            lcd = I2cLcd(i2c, lcd_addr, 2, 16)
            lcd.backlight_on()
        if not power_on():
            return
        stage = "GPIO ERROR"
        if btn_play is None:
            btn_play = Pin(16, Pin.IN, Pin.PULL_UP)
            btn_stop = Pin(17, Pin.IN, Pin.PULL_UP)
            btn_next = Pin(19, Pin.IN, Pin.PULL_UP)
            btn_prev = Pin(21, Pin.IN, Pin.PULL_UP)
            controls = ButtonLatch()
        stage = "ADC ERROR"
        if pot is None:
            pot = ADC(26)
            pot.read_u16()  # verify before either self-test can produce sound
        if not power_on():
            return
        stage = "SPEAKER ERROR"
        if speaker is None:
            speaker = PWM(speaker_pin)
            speaker.duty_u16(0)  # silence before configuring a frequency
            speaker.freq(SPEAKER_CARRIER_HZ)
        stage = "LCD ERROR"
        boot_lcd_test()
        if not power_on() or hardware_error:
            return
        stage = "SPEAKER ERROR"
        boot_speaker_test()
        if not power_on() or hardware_error:
            return
        stage = "SD ERROR"
        if not initialize_storage() or not power_on() or hardware_error:
            return
        stage = "ADC ERROR"
        seed_random()
        index = random_track_index(None)
        startup_complete = True
        stage = "GPIO ERROR"
        # A key held through boot must be released before it can start playback.
        if any(pin.value() == 0 for pin in (btn_stop, btn_play, btn_next, btn_prev)):
            controls.block()
    except Exception as e:
        hardware_fault(stage, e)
    except BaseException:
        shutdown_outputs()
        raise
    finally:
        mute_speaker()
        if hardware_error:
            shutdown_outputs()


# Only safe GPIO levels and the soft-power input are configured before ON.
# In particular no PWM, LCD scan, ADC, SD mount or boot sound is started while OFF.
try:
    speaker_pin = Pin(18, Pin.OUT, value=0)
    for gpio in (5, 8, 9):
        Pin(gpio, Pin.OUT, value=0)
    slide = Pin(22, Pin.IN, Pin.PULL_UP)
except Exception as e:
    hardware_fault("GPIO ERROR", e)
    shutdown_outputs()

# ----------------------------------------------------
# RANDOM / SHUFFLE HELPERS
# ----------------------------------------------------

def get_volume8():
    """Return volume in 0-255 range after ADC initialization."""
    return pot.read_u16() >> 8


def seed_random():
    seed = time.ticks_us() ^ pot.read_u16() ^ len(tracks)
    random.seed(seed)

MAX_RANDOM_TRACK_ATTEMPTS = 32


def random_track_index(current_index=None):
    if len(tracks) <= 1:
        return 0

    # Preserve rejection sampling normally, but never hang audio on a stuck
    # generator. The existing playback error handler mutes/stops on exhaustion.
    for _ in range(MAX_RANDOM_TRACK_ATTEMPTS):
        new_index = random.getrandbits(16) % len(tracks)
        if new_index != current_index:
            return new_index
    raise OSError("Random track selection did not progress")

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
    line = pad_lcd(text)
    if lcd is not None and lcd_lines[row] != line:
        if lcd_call("move_to", 0, row) and lcd_call("putstr", line):
            lcd_lines[row] = line

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

    led_call("color", 0, brightness, 0)

# ----------------------------------------------------
# WAV PLAYER
# ----------------------------------------------------

def poll_controls():
    """Idle polling shares the same latch and priority as the sample loop."""
    if controls is None:
        return None
    if btn_stop.value() == 0:
        event = "stop"
    elif btn_play.value() == 0:
        event = "play"
    elif btn_next.value() == 0:
        event = "next"
    elif btn_prev.value() == 0:
        event = "prev"
    else:
        if not controls.armed:
            controls.released(time.ticks_ms())
        return None
    return event if controls.press(event) else None


def apply_control(event, path=None, position=0, data_size=0):
    """One transition path for idle and in-playback controls, always muted first."""
    global state, index, resume_path, resume_offset, resume_size
    mute_speaker()
    if hardware_error or storage_error or not tracks:
        state = "STOPPED"
        clear_resume()
        return
    if event == "stop":
        state = "STOPPED"
        clear_resume()
    elif event == "play":
        if state == "PLAYING":
            # Position is an emitted-sample count, not the buffered file cursor.
            resume_path, resume_offset, resume_size = path, position, data_size
            state = "PAUSED"
        else:
            if state == "STOPPED":
                clear_resume()
            state = "PLAYING"
    elif event in ("next", "prev"):
        index = random_track_index(index) if event == "next" else (index - 1) % len(tracks)
        clear_resume()
        # A paused selection stays paused at the new song's beginning.
        if state not in ("PLAYING", "PAUSED"):
            state = "STOPPED"
    update_display()
    update_led()


def enter_off():
    global state, power_off_displayed
    state = "STOPPED"
    clear_resume()
    if controls is not None:
        controls.block()
    if not power_off_displayed:
        mute_speaker()
        led_call("off")
        show_screen("POWER OFF", hardware_error or "")
        print("POWER OFF")
        power_off_displayed = True


def _play_pcm(f, remaining, path, position):
    """Emit validated PCM; the wrapper owns the file and final mute.

    A small integer tracks the exact emitted offset on pause. Release timing
    is checked every 32 samples while latched, never on every ordinary sample.
    ADC/gain/PWM math and the tuned sleep remain unchanged.
    """
    data_size = position + remaining
    while remaining and state == "PLAYING" and hardware_error is None:
        if not power_on():
            enter_off()
            return "interrupted"
        chunk = read_exact(f, min(512, remaining))
        remaining -= len(chunk)
        if not power_on():
            enter_off()
            return "interrupted"
        update_music_led(chunk)
        if hardware_error:
            return "error"

        offset = -1
        for b in chunk:
            offset += 1
            volume8 = pot.read_u16() >> 8
            gain = volume8 // 25
            level = b * gain
            speaker.duty_u16(level)
            time.sleep_us(10)

            if btn_stop.value() == 0:
                controls.released_since = None
                if (controls.armed or controls.last_event != "stop") and controls.press("stop"):
                    apply_control("stop")
                    return "stopped"
            elif btn_play.value() == 0:
                controls.released_since = None
                if controls.armed and controls.press("play"):
                    apply_control("play", path, position + offset + 1, data_size)
                    return "paused"
            elif btn_next.value() == 0:
                controls.released_since = None
                if controls.armed and controls.press("next"):
                    apply_control("next")
                    return "track_change"
            elif btn_prev.value() == 0:
                controls.released_since = None
                if controls.armed and controls.press("prev"):
                    apply_control("prev")
                    return "track_change"
            elif not controls.armed and offset & 31 == 0:
                controls.released(time.ticks_ms())

        position += len(chunk)
        if not power_on():
            enter_off()
            return "interrupted"
        update_title_scroll()

    return "ended" if remaining == 0 else "interrupted"


def play_wav(path):
    """Validate/open one song and contain failures around the PCM loop."""
    global state

    if hardware_error or speaker is None:
        mute_speaker()
        state = "STOPPED"
        clear_resume()
        return "error"
    try:
        with open(path, "rb") as f:
            remaining = audio_data(f)
            position = 0
            if resume_path == path:
                if resume_size != remaining or not 0 <= resume_offset <= remaining:
                    raise WavError("Saved playback position no longer matches WAV data")
                position = resume_offset
                f.seek(f.tell() + position)
                remaining -= position
            else:
                clear_resume()
            result = _play_pcm(f, remaining, path, position)
            if result == "ended":
                clear_resume()
            return result

    except Exception as e:
        mute_speaker()
        state = "STOPPED"
        clear_resume()
        print("WAV error:", e)
        return "error"
    finally:
        # Also covers EOF, parse/read failures and Ctrl-C/BaseException exits.
        mute_speaker()

# ----------------------------------------------------
# UI HELPERS
# ----------------------------------------------------

def update_display():
    if hardware_error or storage_error:
        show_screen(hardware_error or storage_error)
        return
    if not startup_complete:
        return
    write_lcd_line(0, state)

    reset_title_scroll()
    update_title_scroll(force=True)

def update_led():
    if state == "PLAYING":
        led_call("green_on")
    elif state == "PAUSED":
        led_call("blue_on")
    elif state == "STOPPED":
        led_call("red_on")
    else:
        led_call("off")

# ----------------------------------------------------
# INITIAL UI
# ----------------------------------------------------

initialize_hardware()
if power_on():
    update_display()
    update_led()

# ----------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------

power_off_displayed = False

while True:
    try:
        if not power_on():
            if hardware_error:
                shutdown_outputs()
            enter_off()
            time.sleep_ms(20)
            continue

        if not startup_complete and not hardware_error:
            power_off_displayed = False
            initialize_hardware()
            if not power_on():
                continue
            update_display()
            update_led()

        if power_off_displayed:
            power_off_displayed = False
            state = "STOPPED"
            clear_resume()
            update_display()
            update_led()

        if hardware_error or storage_error:
            state = "STOPPED"
            clear_resume()
            if hardware_error:
                shutdown_outputs()
            mute_speaker()
            time.sleep_ms(20)
            continue

        # While playing, the sample loop owns input polling and exact position.
        if state != "PLAYING":
            event = poll_controls()
            if event is not None:
                apply_control(event)

        if state == "PLAYING":
            result = play_wav("/sd/" + tracks[index])
            mute_speaker()
            if not power_on():
                enter_off()
                continue
            if hardware_error:
                state = "STOPPED"
                clear_resume()
                shutdown_outputs()
                update_display()
                continue
            if result == "ended":
                index = random_track_index(index)
                state = "PLAYING"
                update_display()
                update_led()
            elif result == "error":
                state = "STOPPED"
                clear_resume()
                update_display()
                update_led()
            elif result == "interrupted" and not power_on():
                enter_off()
        else:
            update_title_scroll()
            time.sleep_ms(5)
    except Exception as e:
        hardware_fault("HARDWARE ERROR", e)
        shutdown_outputs()
        update_display()
        time.sleep_ms(20)
    except BaseException:
        shutdown_outputs()
        clear_resume()
        raise
