# Pico Jukebox

MicroPython jukebox for Raspberry Pi Pico WH, an SD card, a 16x2 PCF8574 I2C LCD,
a Monk Makes Amplified Speaker v2, an RGB LED, four buttons, a soft-power slide
switch and a volume potentiometer. **`main.py` is the supported entry point.**
There are no package dependencies or network configuration requirements.

## Wiring and approved settings

GPIO numbers below are **GP numbers**, not physical header positions. Preserve
existing verified power, grounding, resistors and level shifting; Pico GPIO/ADC
signals must remain within the board's 3.3 V limits.

| Device / control | GPIO / peripheral | Setting |
| --- | --- | --- |
| SD SCK / MOSI / MISO / CS | GP10 / GP11 / GP12 / GP13, **SPI1** | Keyword `SPI(1, ...)`; application 1 MHz, driver identification 100 kHz then 10 MHz |
| LCD SDA / SCL | GP0 / GP1, I2C0 | 100 kHz; 16x2; normally 0x27 |
| Amplified speaker signal | GP18 PWM | 40000 Hz normal carrier |
| RGB red / green / blue | GP5 / GP8 / GP9 PWM | 1000 Hz; existing RGB polarity |
| Play / Pause | GP16 | Active low, internal pull-up |
| Stop | GP17 | Active low, internal pull-up |
| Next / random | GP19 | Active low, internal pull-up |
| Previous | GP21 | Active low, internal pull-up |
| Soft power | GP22 | Active low = ON; internal pull-up |
| Volume wiper | GP26 ADC | Existing potentiometer circuit |

Important UI/carrier settings are together near the top of `main.py`. The retained
sample loop reads up to 512 bytes, uses `(ADC >> 8) // 25` gain and `sample * gain`
PWM duty, then `sleep_us(10)`. This is the established output mapping, not centered
PCM or a guaranteed 8000 samples/second scheduler. Keep these settings until
measured hardware comparison justifies a change. Driver protocol constants stay
with their respective drivers; there is no separate configuration module.

## Firmware and files

Use the official **MicroPython RP2 firmware for Pico W / WH**, retaining the
currently working build. The exact installed version and minimum verified build
have not been recorded; a specific version cannot yet be certified. Before a
release, record `sys.implementation` and `os.uname()` in Thonny and verify these
APIs on that build: `machine.Pin/PWM/ADC/SPI/I2C`, `micropython.const`,
`time.sleep_ms/sleep_us/ticks_ms/ticks_us/ticks_diff`, `random.seed/getrandbits`,
`os.mount/umount/listdir/stat` with FAT support, binary file seek/tell, memoryview,
and `int.from_bytes`. CPython compilation does not verify these firmware APIs.
No I2S API or Wi-Fi support is needed by the application.

Copy exactly these six files to the Pico's internal filesystem root:

- `main.py`
- `buttons.py`
- `wav_reader.py`
- `sdcard.py`
- `i2c_lcd.py`
- `rgb_led.py`

Do not copy `tests/`, `experiments/`, root `test_*.py`, or development tooling as
startup firmware. There is no required `boot.py`. Back up any existing custom
`boot.py` and confirm it does not launch other applications or diagnostics.
`experiments/main_newspeaker.py` is an unsupported, older I2S prototype, not an
alternative entry point for this hardware.

## SD card and songs

Use the known working card formatted **FAT16 or FAT32**, not exFAT. Back up music
before any PC-side reformat. The application mounts the existing card at `/sd`
and does not format it. Put songs in the **card root**, not subdirectories.

Supported files are RIFF/WAVE **PCM encoding 1, unsigned 8-bit, mono, 8000 Hz**
(byte rate 8000, block alignment 1), with a nonempty data chunk. `.wav` matching
is case insensitive. Extra RIFF chunks and odd-length padding are parsed; a fixed
44-byte header is not assumed. Unsupported/malformed/unreadable files are excluded
at discovery. Names use normal sorted order for Previous. Underscores display as
spaces, non-ASCII title characters as `?`, and control characters as spaces;
original filenames are used to open files.

## Install and start using Thonny

1. Back up the working Pico files and SD card. Connect by USB and select
   **MicroPython (Raspberry Pi Pico)** and the correct port in Thonny.
2. Stop the current program with Stop/Ctrl-C. Keep the existing working firmware;
   if installation is necessary, select the Pico W firmware and record its build.
3. Upload the six files listed above to the Pico root. Insert the prepared SD
   card using the existing hardware procedure, then reset the Pico.
4. With slide OFF, peripheral startup/self-tests wait until ON; the Pico still
   has electrical power. With ON, the LCD screens and one conservative speaker
   sweep run once per boot. The sweep uses 2000/65535 duty, frequencies 200 through
   1950 Hz in 50 Hz steps, then restores 40 kHz and silence.
5. A random song is selected and the jukebox becomes STOPPED, with red LED.
   Press Play to begin. OFF/ON does not rerun completed self-tests.

| Control | Expected action |
| --- | --- |
| Play / Pause | Start selected track; pause; resume at first unplayed sample |
| Stop | Silence and STOPPED; next Play starts selected track from the beginning |
| Next | Random different track when possible; retain playing/paused/stopped state |
| Previous | Previous sorted index, wrapping; retain playing/paused/stopped state |
| Volume | Existing quantized potentiometer response |
| Soft OFF | Stop, discard resume position, LED off, POWER OFF; ON returns STOPPED |

Navigation discards the old resume bookmark. EOF randomly advances and continues
playing. One-song libraries repeat that song; immediate repetition is avoided
with multiple songs. Previous follows sorted names, not playback history. Release
all buttons for at least 30 ms between actions; holds do not repeat. Stop has
priority. Green reacts to music, blue means paused, red means stopped. Row 1
shows status; row 2 shows the title, static up to 16 characters, otherwise scrolling
at the existing 350 ms polling interval. Track selection resets scrolling.

## Troubleshooting and manual diagnostics

| Symptom | Check / action |
| --- | --- |
| `SD ERROR` | Card seating, FAT format, power and SPI1 wiring. Repair and reset; initialization does not retry indefinitely. |
| `NO WAV FILES` | Root directory, nonempty files and exact supported format; see skipped-file messages in Thonny. Reset after correcting media. |
| `LCD ERROR` / blank LCD | GP0/1, power, contrast, backlight, normally 0x27. Startup accepts a sole unambiguous PCF8574 address fallback, not arbitrary I2C devices. Consult Thonny if no display is available. |
| `SPEAKER ERROR`, `ADC ERROR`, `GPIO ERROR`, `RGB ERROR` | Repair the named peripheral and reset. Faults latch and outputs are silenced; there is no automatic hardware retry flow. |
| Song stops unexpectedly | Read the Thonny error. A missing/changed/unreadable song stops safely; choose another track or repair media and reset. |
| Pitch, distortion or pauses | Compare against the previous firmware on the same WAV/card. LCD writes, SD reads, ADC/GPIO and GC can affect timing. Do not retune sleep/carrier from host tests. |
| OFF still powered | Expected: GP22 is software power control, not an electrical disconnect. |

Useful standalone scripts remain at the repository root: `test_buttons.py`,
`test_slide_switch.py`, `test_lcd.py`, `test_led.py`, `test_speaker.py`, and
`test_sd.py`. Stop `main.py`, reset before/after a diagnostic, and run only the
chosen script from Thonny. Button/slide/LCD scripts run until interrupted; the
LCD script intentionally clears repeatedly. Speaker/LED diagnostics now release
PWM even on Ctrl-C; the SD diagnostic unmounts on exit. **`test_sd.py` overwrites `/sd/hello.txt`: use only a spare
card, never the music card.** `test_max98357a.py` is for alternate I2S wiring and
is outside this supported PWM setup. No hardware diagnostic runs automatically.

## Development and release validation

Python 3.10+ and the standard library suffice; no GPIO devices or pip installs:

```sh
python -m unittest discover -s tests -v
python -m compileall -q .
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Use this explicit discovery directory: root `test_*.py` scripts are hardware
programs, not host tests. CI runs host regressions and compilation on Python 3.10
and 3.12. Mocks do not validate physical audio, timing, FAT mounting, card support,
electrical behavior or Pico heap stability.

See the [release review](docs/RELEASE_READINESS.md) for current results, limitations,
cleanup inventory and release recommendation, and the [hardware acceptance
checklist](docs/HARDWARE_ACCEPTANCE.md) for Thonny testing and soak evidence.
Earlier audit reports are historical snapshots: [baseline](docs/BASELINE_AUDIT.md),
[storage](docs/STORAGE_RELIABILITY.md), [startup](docs/STARTUP_RELIABILITY.md),
[audio](docs/PWM_AUDIO_RELIABILITY.md), [controls](docs/CONTROLS_AND_STATES.md),
[display/LED/shuffle](docs/DISPLAY_LED_SHUFFLE.md).
