# Pass 3: predictable boot and safe peripheral initialization

This is the Pass 3 startup snapshot. [Pass 5](CONTROLS_AND_STATES.md) subsequently
changes normal OFF/ON to a fresh STOPPED state, adds `buttons.py`, and corrects
button/pause transitions. Boot test and peripheral safeguards remain in effect.

This pass applies to `main.py`, the GP18 PWM application for the Monk Makes
Amplified Speaker v2. The experimental I2S application is not a supported startup
entry point. There are no new UI flows, extra sounds or hardware pin changes.
Normal playback format, carrier, gain, per-sample sleep and controls remain as in
Pass 2. Startup functions remain in `main.py`; no extra runtime module is needed.

## Configuration from project evidence

Both the working PWM application and `test_lcd.py` use I2C0, SDA GP0, SCL GP1,
100 kHz and LCD address 0x27. Retain that setup. The separate I2S experiment's
400 kHz bus is not evidence for changing the working PWM setup. Prefer 0x27 even
if other devices respond. Preserve fallback only when the scan has exactly one
plausible PCF8574/PCF8574A address (0x20–0x27 or 0x38–0x3F); reject unrelated or
ambiguous addresses rather than sending LCD commands to an arbitrary device.
An address cannot prove device identity; any fallback still needs hardware
verification. Empty scans and I2C/driver failures produce an explicit console
error and a muted fault state, not an index error or a pre-UI sleep trap.

GP18 remains PWM with a 40000 Hz normal carrier. The original sweep sequence
`range(200, 2000, 50)` and its 50 ms tone steps are retained (200 through 1950 Hz,
exclusive upper bound 2000). Its duty is reduced from 30000 to 2000 out of 65535,
about 3%, as a conservative starting level. This is not a measurement of speaker
power or audibility. Verify it on the actual amplified speaker before changing
that named constant. No additional startup sounds are introduced.

The full pin map is unchanged from [Pass 1](BASELINE_AUDIT.md#hardware-contract),
including the SPI1 keyword constructor and the SD identification/data speeds
established in [Pass 2](STORAGE_RELIABILITY.md).

## Startup order and OFF behavior

1. Drive GP18 and RGB GPIOs GP5/8/9 low and configure GP22 as a pull-up input.
   These are the only hardware operations before the first ON state. A cold OFF
   boot does not allocate PWM, initialize ADC, scan I2C, mount SD, or play tones.
   It logs POWER OFF once. An uninitialized LCD is left untouched; its physical
   power/backlight can still be on because the switch is not an electrical cut.
2. On ON (GP22 low), initialize RGB at zero duty, then the LCD at 100 kHz with its
   backlight enabled. Configure GP16/17/19/21 pull-up buttons, verify ADC GP26,
   then create GP18 PWM muted before setting 40 kHz.
3. Show the existing HELLO BRYCE / LCD WORKING and PICO JUKEBOX / ADDR screens.
   Run the existing speaker sweep at reduced duty. Mark each test attempted
   before starting it; it cannot repeat after OFF/ON or after a partial attempt.
   An OFF that happens before a test starts defers its first attempt until ON.
4. Initialize/mount the existing SD driver and discover validated songs. Reuse a
   completed SD initialization/mount if OFF interrupts later startup stages.
   OFF is checked between storage stages and files; no new stage starts after
   OFF is observed. An interrupted directory scan is restarted on the next ON,
   replacing its partial list; completed SD initialization/mount are not repeated.
5. Seed random once after discovery and enter the normal STOPPED display. Normal
   OFF/ON after readiness preserves established state and does not rerun boot.

Screen/tone/settling waits check OFF in steps of at most 20 ms. Synchronous bus
calls and one file's header validation must return before software can check
again. Normal playback still checks OFF at chunk boundaries, as before. OFF
mutes, turns RGB off and writes POWER OFF once if the LCD was initialized. It
does not repeatedly write the same screen or reconfigure peripherals while idle.
The LCD remains powered; neither GPIO22 nor this software disconnects electrical
power from the Pico, speaker amplifier or LCD.

## Safe failures and display handling

- Initialization errors are latched as GPIO ERROR, RGB ERROR, LCD ERROR,
  ADC ERROR or SPEAKER ERROR, with details in the Thonny/USB console and on an
  available LCD. Further playback and hardware reinitialization are blocked for
  that boot. Restore hardware and reset to retry; automatic hot-plug recovery
  is not added. SD ERROR and NO WAV FILES retain the Pass 2 stopped/muted behavior.
- Every attempted sweep exits through a mute and restores 40 kHz when PWM is
  usable, including OFF, a raised exception and Ctrl-C. If PWM configuration or
  mute itself fails, cleanup attempts deinit and forces GP18 to GPIO output low.
  A missing physical speaker is not detectable through the PWM API.
- RGB initialization mutes each allocated channel immediately. If a later channel
  fails, all earlier channels are muted/released and their GPIOs driven low.
  Cleanup proceeds across individual PWM failures. This also protects the shared
  RGB helper's other callers without changing color mapping or channel frequency.
- Runtime LCD/RGB failures latch a fault and mute instead of leaking exceptions
  out of the UI. A failed LCD is disabled to avoid repeated failing bus writes;
  a music-LED failure is checked before any more samples are emitted. The main
  loop contains other peripheral exceptions and releases outputs; user interrupts
  release PWM, drive GP18 low and attempt to unmount SD after file contexts close.
  Cleanup is idempotent and never reruns the self-tests. A failed electrical/API
  fallback cannot be guaranteed safe by software alone; cleanup failures are
  reported where available. No real hardware fault guarantees are inferred.
- The existing LCD driver already initializes the backlight bit and clears once.
  It is retained unchanged. The application enables the backlight once, writes
  complete padded 16-character lines, and caches successful lines. Subsequent
  screen/state/title changes overwrite rows without clearing the whole display;
  unchanged text is not rewritten. Blank second rows erase old titles/messages.

## Host validation

Use the README's explicit host discovery path; root `test_*.py` files are manual
hardware diagnostics and some run forever or write to SD. Run:

```sh
python -m unittest discover -s tests -v
python -m compileall -q .
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Tests execute the existing startup/functions and bounded main-loop iterations
under fakes. Coverage includes normal and cold-OFF boot, OFF during LCD screens,
sweep and SD initialization, no repeated tests/mounts, LCD missing/scan/backlight/
constructor faults, speaker constructor/frequency/sweep/mute faults, partial RGB
setup, GPIO/ADC faults, late-failure unmount, runtime LCD/RGB faults, Ctrl-C
cleanup, stable screen writes and reset after repair. The real LCD driver is
also exercised against recorded I2C writes to check backlight and initial clear.
The existing storage/WAV regression tests still run.

Executed on CPython 3.12.14: all 75 tests passed, syntax compilation passed,
and the CRLF-aware whitespace check passed.

These are software checks only. No Pico, physical speaker, LCD, SD card or
instrumented signal has been tested in this pass. They do not establish physical
audio quality, electrical safety, actual timing or successful recovery of a
physically failed peripheral.

## Thonny real-device validation (not yet executed)

1. Keep the working MicroPython firmware. In Thonny select the Raspberry Pi Pico
   MicroPython interpreter and correct USB port. Stop the current program with
   Ctrl-C and save backups of the Pico files using View > Files. Record
   `sys.implementation` and `os.uname()` in the Shell for the validation report.
   Copy `main.py`, `buttons.py`, `rgb_led.py`, `i2c_lcd.py`, `sdcard.py` and `wav_reader.py` from
   this branch to the Pico root. Do not copy the I2S experiment as `main.py`.
   Use a known-good, existing 8-bit unsigned mono 8000 Hz PCM WAV on the music card.
2. Set the slide OFF, then soft-reset with Ctrl-D (or power-cycle the board) once.
   Observe one POWER OFF console message and no speaker sweep, I2C scan message
   or SD mount activity. The uninitialized LCD may remain blank or show retained
   text; its power/backlight is not disconnected. Leave it OFF for at least ten
   seconds and confirm it stays silent. Do not also press F5 after resetting:
   that starts another program execution and would be a separate boot attempt.
3. Switch ON. Confirm the two existing LCD screens, then one quiet sweep, then
   STOPPED and a song title. The ADDR screen should report the actual address
   (normally 0x27). Confirm the backlight, contrast and complete line clearing;
   watch for flicker. Leave idle and toggle OFF/ON several times: expect POWER
   OFF/normal display and no repeated HELLO screens or sweep. Play a known song,
   check volume and controls, and confirm normal output after the boot test.
4. Reset ON, then switch OFF once during each boot phase in separate boots:
   first LCD screen, second screen, sweep, and SD startup. The sound should stop
   promptly during the sweep; after an in-flight bus call finishes, no later
   stage should start while OFF. Switch ON again. A previously attempted screen
   test/sweep must not restart; a not-yet-attempted sweep should run once. Verify
   the final STOPPED UI. If measuring GP18, expect zero duty while muted and a
   40 kHz carrier when normal PWM resumes; a deinitialized fault output is GPIO
   low and need not have a running carrier.
5. Test missing hardware in separate boots. Remove power before disconnecting
   or restoring the LCD or SD card. With LCD absent, expect an empty scan and
   meaningful LCD ERROR in the Shell, no sweep, and no crash/retry stream. With
   SD absent but other peripherals present, expect one boot sweep followed by
   SD ERROR and silence. Restore the hardware, reset once and verify successful
   startup. There is no automatic live reconnection/retry flow.
6. For controlled speaker error testing, stop main, use a temporary **copy** of
   this branch's `main.py` in Thonny, and replace `speaker = PWM(speaker_pin)`
   with `raise OSError("Injected speaker init failure")`. Run that copy once
   with F5; expect SPEAKER ERROR, no sweep and no sustained sound. In a second
   temporary copy, replace `speaker.freq(frequency)` in the sweep loop with
   `raise OSError("Injected sweep failure")`; expect prompt silence and a
   SPEAKER ERROR after the test begins. These injected tests exercise cleanup,
   not a real PWM hardware defect. Keep the normal saved Pico `main.py` intact;
   discard the temporary copies, reset, and verify one normal boot after repair.
7. During an ordinary sweep and then during playback in separate boots, issue
   Ctrl-C from the Shell. Confirm no continuous tone remains. Restore the
   normal program by resetting, not by importing another copy while it is
   running. Record the observed screen/messages, whether the quiet sweep is
   audible, and any cleanup errors. Never run `test_sd.py` on the music card;
   no write test is required for this startup pass.

Remaining unrelated work includes button edge debounce/repeat semantics, exact
playback timing measurements, credentials remediation, and the experimental I2S
entry point. The standalone manual diagnostic scripts remain separate from the
production boot path. See the earlier audits for those findings.
