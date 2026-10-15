# Pass 1 baseline audit

This is the historical Pass 1 snapshot. See [Pass 2](STORAGE_RELIABILITY.md) for
resolved SD/WAV findings, updated storage behavior and current validation.

Reviewed all 13 tracked Python files at local baseline
`797f39954e54efb63775376527d91d39de00fad6` on 2026-10-14. No configuration modules,
package metadata, CI, or automated regression suite existed. The user's real
hardware operation is the starting evidence; this audit used source inspection
and CPython mocks, not a connected Pico. Firmware version and actual WAV format
have not been supplied. Findings below distinguish software facts from hardware
questions. Production behavior is preserved in this pass.

## Existing behavior

`main.py` configures pull-up, active-low controls, RGB PWM at 1 kHz, speaker PWM
at 40 kHz and ADC volume. It scans I2C, prefers address 0x27, and otherwise uses
the first responding device. It always runs LCD screens and a tone sweep before
mounting `/sd`, even if soft power is off. No I2C devices or no WAV names causes
an indefinite one-second sleep loop. SD initialization/mount errors escape.

Root SD names ending in `.wav` (case insensitive) are sorted with normal string
ordering; entries are not checked to be regular files. A random track is selected
at boot, initially STOPPED. Next selects a random different index; previous wraps
backward in the sorted list, not playback history. With one track, selection is
always zero. Selection is based on 16 random bits modulo track count, with retry
to avoid the current index; this is not a uniform shuffle and retry has no bound.

Play starts playback; play during playback pauses; another play restarts the file
from the beginning because no file position is saved. Stop mutes and stops. Next
and previous during playback keep PLAYING; navigation outside playback stops.
EOF selects another random track and continues playing. In playback, simultaneous
button priority is stop, play, next, previous. Outside playback, separate `if`
statements can process several controls in the same iteration.

Playback skips exactly 44 bytes, reads 512-byte chunks and treats each following
byte as an unsigned sample, including any trailing WAV chunks. Each sample writes
`byte * ((ADC >> 8) // 25)` to PWM followed by a nominal 10 microsecond sleep.
It does not parse or honor WAV sample rate, encoding, channels, or data size.
The source assumes the shape of simple 8-bit mono PCM WAVs; it does not enforce
that format. Actual audible compatibility remains a hardware question.

LCD state occupies row 0; row 1 strips `.wav`, replaces underscores and pads or
scrolls 16 characters with three spaces between repeats at 350 ms intervals.
STOPPED is red, PAUSED blue, PLAYING green; playback green brightness samples
chunk amplitude every eighth byte. Soft off mutes, disables RGB and shows POWER
OFF, but retains state and leaves the Pico and LCD powered. It is not shutdown.

## Hardware contract

All pin numbers below are GPIO numbers, not physical header positions.

| Function | Peripheral / pins | Configuration |
| --- | --- | --- |
| SD | SPI1: SCK GP10, MOSI GP11, MISO GP12, CS GP13 | Keyword `SPI(1, ...)`, initially 1 MHz, mode 0, 8 bits, MSB; driver attempts 10 MHz after init |
| LCD | I2C0: SDA GP0, SCL GP1 | 100 kHz, 16x2, preferred 0x27 |
| Speaker | PWM GP18 | 40 kHz after boot sweep |
| RGB | R GP5, G GP8, B GP9 | 1 kHz PWM, 0..255 scaled by 257 |
| Play / pause | GP16 | Input pull-up, active low |
| Stop | GP17 | Input pull-up, active low |
| Next | GP19 | Input pull-up, active low |
| Previous | GP21 | Input pull-up, active low |
| Soft power | GP22 | Input pull-up, low = on |
| Volume | ADC GP26 | 16-bit reading, upper eight bits used |

Preserve the historically working SPI1 keyword constructor. This pass makes no
pin, bus, timing, driver implementation, or audio algorithm changes. The I2S
experiment additionally uses GP2/3/4 and amplifier enable GP6; those are not the
PWM baseline hardware contract.

## Findings by severity

### High

- **H1 — tracked network credentials** (`wifi_test.py`, module constants): a
  literal SSID and password are committed. Values are intentionally not repeated
  here. The standalone script connects at import; neither application imports it.
  Treat the password as exposed and rotate it through the network owner. Removing
  it from the current tree does not remove history. Credential rotation/history
  remediation is separate from this behavior-preserving pass; the script remains
  tracked and must not be included in a firmware deployment bundle.
- **H2 — block-device API mismatch** (`sdcard.py:ioctl`): current MicroPython's
  documented protocol uses operations 1=init, 2=deinit, 3=sync, 4=block count,
  5=block size, 6=block erase. This driver instead returns zero for 4/5/6 and
  calculates capacity for 7, size for 8. Standard VfsFat may see zero capacity or
  size. This is a source-level mismatch, not proof that the user's working
  firmware fails. Capture the installed firmware version and reproduce mount
  behavior before a focused driver fix; do not swap drivers or change SPI buses.
- **H3 — unbounded SD write busy wait** (`sdcard.py:writeblocks`): permanent zero
  responses hang forever. Unexpected SPI exceptions in command/read/write/CSD
  paths can leave CS asserted because cleanup lacks `finally`. The main player
  reads, but the manual SD diagnostic writes and exposes this path.

### Medium

- **M1 — playback timing and format** (`main.py:play_wav`): fixed header skip and
  byte interpretation accept malformed, stereo, compressed or 16-bit files and
  ignore sample rate. ADC read, arithmetic, PWM call, sleep and up to four GPIO
  reads occur per sample. SD reads and UI work interrupt the stream. Do not infer
  a 100 kHz effective sample rate from the sleep alone or optimize speculatively.
- **M2 — button levels are not debounced edges** (`main.py`, both loops): 250 ms
  sleeps throttle repeat but do not wait for release or validate stable input.
  Holding play can immediately pause a just-started file; stop/navigation repeat.
  During playback, raw samples see bounce without debounce. The baseline tests
  deliberately record the held-play outcome rather than fixing it silently.
- **M3 — stale soft-power transitions** (`main.py:play_wav`, final main loop):
  slide is checked only at chunk boundaries. Turning off can finish the current
  chunk, return `ended`, select another track and show PLAYING before the outer
  power-off branch runs. State is retained and power-on may start a different
  track. Paused playback restarts rather than resumes. Intended future semantics
  need agreement and hardware verification before a behavior change.
- **M4 — failure and startup handling** (`main.py` module setup): no-device and
  empty-card loops ignore soft power and buttons forever; startup tones run even
  when off. SD/mount/LCD failures have no recovery UI or top-level mute cleanup.
  Falling back to an arbitrary I2C address can send LCD commands to another device.
- **M5 — cleanup depends on caller** (`main.py:play_wav`): file context managers
  close files on return/error, but EOF and exception paths do not mute PWM locally.
  The current main-loop caller mutes on return. Ctrl-C or another BaseException
  bypasses the handler and can leave a nonzero duty. There is no top-level finally,
  PWM deinit, filesystem unmount, or actual power-down sequence.
- **M6 — experimental I2S state loss** (`main_newspeaker.py`): duplicates setup,
  UI and controls, uses a different speaker and 400 kHz LCD bus, shuffles once and
  advances sequentially. The outer loop always forces STOPPED after `play_wav`,
  discarding PAUSED and track-change PLAYING states. WAV parsing assumes fixed
  offsets, does not check RIFF/PCM/data, and creates per-chunk conversion buffers
  plus per-sample bytes/unpack objects for 16-bit input. I2S API availability is
  firmware dependent; this is not a drop-in replacement for the PWM application.
  Bare `except` in `stop_audio` hides deinit failures; amplifier enable remains on.
- **M7 — SD validation gaps** (`sdcard.py`): CMD8 response voltage/check pattern
  and CMD55 status are unchecked, CRC is discarded, partial-block buffer lengths
  are silently truncated, and unknown ioctl operations return success. Timeouts
  based on command retry count can take much longer than a wall-clock timeout.
  CSD handling treats any non-v2 structure as v1. No extended block offset API.

### Low / maintenance

- **L1 — global coupling**: state, index, tracks, scroll position and peripherals
  are module globals; importing either application runs hardware startup and an
  infinite loop. `play_wav` mutates index/state and reports a string result, making
  callers easy to desynchronize. Tests isolate existing code without restructuring.
- **L2 — allocation pressure, not a demonstrated leak**: 512-byte file reads,
  title cleanup/concatenation/slicing, LCD bytearrays per nibble and a new row-offset
  list per cursor move create GC pressure. A 16-character LCD line alone has at
  least 19.2 ms of explicit nibble sleeps (1.2 ms per byte), plus cursor/I2C work.
  These are potential jitter sources, not measured bottlenecks. No unbounded
  retained collection growth is apparent in production code; no hardware heap
  endurance measurements were performed. Host mocks retain histories for tests.
- **L3 — naming and duplication**: promote header length 44, chunk size 512,
  sample sleep 10 us, gain divisor 25, PWM rates, bus rates, LCD address/geometry,
  250/200/100 ms UI delays, SD block size/retry/timeouts and RGB scale 257 to named
  constants in later small passes. LCD_WIDTH and SCROLL_INTERVAL_MS already exist.
- **L4 — dead/unused candidates**: `get_volume8` is unused in `main.py` but useful
  in the I2S variant. LCD constants such as RW, cursor shift, CGRAM and eight-bit
  mode are unused internally but may form a driver interface. `home`, backlight
  off and `white` are unused by main, but `white` is used by `test_led.py`.
  Keep these helpers/interfaces until deployment usage is known. The only removed
  import is `from machine import I2C` in `i2c_lcd.py`: the driver never references
  that name and receives its bus through the constructor; no tracked consumer
  imports the alias. No other production code is removed.

## Complete file inventory

| Tracked Python file | Role and review result |
| --- | --- |
| `main.py` | Supported PWM entry point; findings M1–M5, L1–L4 |
| `sdcard.py` | Existing SPI SD driver retained; H2/H3/M7; inspect firmware protocol before fixes |
| `i2c_lcd.py` | Existing PCF8574 driver retained; pulse sleeps/allocations above; row upper bound clamped but negative rows/columns and Unicode are unchecked; `putstr` has no wrapping/newline handling |
| `rgb_led.py` | Small PWM wrapper used by both applications and LED diagnostic; no clamp for values outside 0..255 or deinit; main callers stay within range |
| `main_newspeaker.py` | Separate unfinished I2S experiment, not referenced by main; M6; retained pending owner decision |
| `test_buttons.py` | Manual active-low button print loop; 200 ms sleep is repeat throttling, not edge debounce; infinite |
| `test_lcd.py` | Manual repeating display; scan checks nonempty but always uses 0x27; infinite, no cleanup |
| `test_led.py` | Finite manual RGB cycle including white; ends off; no PWM deinit |
| `test_max98357a.py` | Manual alternate I2S tone; 256-sample repeated buffer has phase discontinuity at 440 Hz/22050 Hz; nominal three-second test; normal deinit/amp off but no finally |
| `test_sd.py` | Manual SPI1 mount/list/write/read smoke test; overwrites `/sd/hello.txt`, no unmount; same hardware constructor as main |
| `test_slide_switch.py` | Manual 500 ms input logger; infinite, not a host regression |
| `test_speaker.py` | Manual GP18 PWM tone sweep; ends muted but no finally/deinit |
| `wifi_test.py` | Standalone network connection experiment, no application references; H1; finite ~10 s retry window, leaves WLAN active |

Imports in production are MicroPython hardware modules plus `os`, `time`, `random`
and (I2S only) `ustruct`; there are no unnecessary CPython-only runtime dependencies.
`machine`, `micropython.const`, `network`, `time.sleep_ms/sleep_us/ticks_*`,
`os.mount/VfsFat` and `ustruct` need the appropriate MicroPython port and firmware.
CPython syntax compilation is not MicroPython API validation. Host test modules
use standard-library CPython APIs only and are not part of deployed firmware.

## Regression foundation and limits

Run `python -m unittest discover -s tests -v` (Python 3.10+, standard library).
The harness substitutes GPIO/PWM/ADC, LCD, SD mount/directory/files, deterministic
random and a wrapping virtual clock. It executes the real `main.py` startup and
functions, deferring only the final module-level loop via AST. For loop scenarios
it inserts a bounded iteration hook; function bodies and state branches remain
unchanged. Update this harness explicitly if the entry-point structure changes.
Host print output is suppressed during startup. Files are in-memory BytesIO.

Separate tests execute the actual LCD driver against recorded I2C writes and
actual SD driver methods against scripted protocol responses; mount success in
main's mocks does not establish VfsFat compatibility. SD protocol tests cover
command encoding, SDHC/SDSC initialization/addressing, multiblock reads, token
expiry, and read/write rejection cleanup. They do not yet cover capacity decoding,
successful writes, all initialization failures, or unexpected SPI exceptions.
I2S and standalone diagnostics are inspected but not executed in host tests.

Several assertions intentionally characterize current limitations (restart/held
play, caller-owned EOF mute, retained state while off). They are a baseline, not
approval of those semantics. Change them explicitly alongside any later fix.
No mock result establishes audio quality, electrical correctness, real timing,
SD reliability, or a lack of memory fragmentation on device.

## Prioritized follow-up plan

1. Rotate exposed Wi-Fi password with network owner; remove tracked private
   configuration in a dedicated security cleanup and decide whether history needs
   remediation. Do not copy credentials into documentation, test fixtures or PRs.
2. Record firmware version, card type and a known-good WAV fixture/format. Validate
   H2 on that exact device, add ioctl/capacity tests and correct only the confirmed
   protocol mismatch. Add bounded busy wait and exception-safe CS release with
   injected-failure tests; retain the working SPI1 initialization.
3. Agree on intended pause, held-button and soft-power behavior. Add transition
   tests before small fixes to edge/debounce handling and termination reasons.
4. Make startup/error/interrupt cleanup deterministic with injected-failure tests;
   retain existing boot tests unless an explicit behavior change is authorized.
5. Introduce named constants and remove proven dead helpers in small reviewed
   changes. Decide whether to archive the I2S/network experiments and clearly
   separate manual hardware diagnostics from host tests.
6. Measure on-device sample timing, LCD stalls and heap behavior using known media
   before considering any audio/allocations optimization. No speculative change
   to gain, buffers, PWM frequency, ADC sampling or sample delay in this pass.

## Hardware validation still required

- Record MicroPython build and verify cold boot, LCD address/fallback, boot screens
  and tone sweep with slide both on and off; verify missing LCD/card/empty card.
- Mount/read the known working SD card using the unchanged SPI1 keyword setup;
  verify driver ioctl calls and behavior at its post-init 10 MHz speed. Use a
  disposable card/file for write tests; the existing diagnostic overwrites a file.
- Compare known-good audio before/after, volume endpoints, LED reaction and long
  title scrolling. Mocks make no claims about physical audio quality.
- Test short presses, bounce, held/simultaneous controls, pause/replay, EOF, single
  track, rapid navigation and soft-off mid-chunk/on restoration.
- Exercise missing/malformed files, SD removal/read errors and interruption;
  observe output mute and power state. Run repeated tracks long enough to monitor
  free heap/GC stalls and resource stability. Do not run I2S experiments on the
  PWM wiring as if they were the supported application.

## Checks executed for this pass

On CPython 3.12.14, all 21 tests passed with
`python -m unittest discover -s tests -v`. `python -m compileall -q .` passed
syntax compilation of every Python file without executing hardware diagnostics.
`git diff --check` passed. No Pico, physical audio, electrical, real SD mount,
or hardware endurance validation was performed. Those checks remain listed above.
