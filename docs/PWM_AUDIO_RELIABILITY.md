# Pass 4: stabilize the existing PWM playback path

This is the Pass 4 audit snapshot. [Pass 5](CONTROLS_AND_STATES.md) subsequently
corrects input/state handling and pause position; its required bookkeeping changes
the control portion of the sample loop. Signal math and the 10 us sleep remain.

The priority is the working Monk Makes Amplified Speaker v2 / GP18 PWM path.
The default remains 40000 Hz carrier, unsigned 8-bit mono 8000 Hz source WAV,
GP26 ADC volume, and the hardware-tuned `time.sleep_us(10)` loop. No I2S,
replacement speaker, new format, scheduler, new DSP or alternate output mode is
introduced. Keep the actual hardware-tested firmware/settings as the reference;
no host test demonstrates correct playback speed or physical signal quality.

## Changes and unchanged calculations

- Extract the validated payload loop into private `_play_pcm(f, remaining)` in
  `main.py`. `play_wav` still owns format validation, file lifetime, failure
  handling and final mute. This adds one function call/frame per song, not per
  sample. The sample-loop AST was compared against pre-pass `92b01e0`; its
  statements, operation order, literal arithmetic and 10 us sleep are unchanged.
  This structural check is not a MicroPython timing measurement.
- Bound random-next rejection sampling to 32 draws. Normal selection, modulo
  mapping and rejection of the current index are retained. A stuck generator
  previously spun indefinitely inside the next-button handler with the last PWM
  duty still active. Exhaustion now raises into the existing muted/stopped error
  path. EOF/outside-playback selection is contained by the existing main-loop
  fault handler (generic HARDWARE ERROR); no automatic retry is introduced.
  This is a retry-count bound, not a measured millisecond response guarantee.
- Add opt-in `tools/audio_timing.py`. It is never imported by production `main.py`.
  It times a player call once at entry/exit with wrapping `ticks_ms`/`ticks_diff`,
  then reports result and elapsed milliseconds. For a completed call with a known
  positive payload count `N`, source duration is `N * 1000 / 8000` ms and the
  whole-call average is `N * 1000 / elapsed_ms` samples/s. Partial/error calls,
  unknown counts and zero/negative elapsed intervals do not get an invented rate.
  Counts must describe the unchanged comparison file. Exceptions propagate so
  the player's existing cleanup/interrupt handling remains in charge.

No audio signal calculation is changed. The known working output is the sole
normal path, so there is no unvalidated alternate math to fall back from.

## Timing audit

| Location | Work and implications |
| --- | --- |
| Every sample | ADC read; right shift by 8; integer division by 25; byte-times-gain multiplication; PWM write; 10 us sleep; stop/play/next/previous GPIO checks in that order. Button handling short-circuits on a press. No new function call, timing probe, float, lookup table or filter is inserted per sample. |
| Pressed control | Existing state/index changes and immediate mute. Random-next now has bounded retries. Held buttons retain the earlier repeat behavior; edge/debounce changes are outside this pass. |
| Before each chunk | SD/file read of at most 512 data bytes, exact-read/truncation checks, LED peak sampling and RGB writes. Synchronous storage and garbage collection can stall output and control polling. |
| After each chunk | Title cleanup/scroll checks and possible LCD writes. The LCD driver explicitly sleeps 1.2 ms per byte; a 16-character row plus cursor command has at least 20.4 ms of explicit delays, plus I2C/Python work. These are source-derived delays, not measured device stalls. |
| File boundaries | RIFF validation/seeks, file open/close, one new payload-function call, caller UI updates and existing inter-track/control delays. Optional timing reports include the player call but exclude caller delays and the diagnostic's final dictionary/printing. |

8000 source samples/s corresponds to an ideal 125 us sample period. A 10 us sleep
only describes one part of the software loop; ADC, GPIO, interpreter/PWM calls,
interrupts, GC and chunk-level pauses determine actual timing. Neither 8000 nor
100000 output samples/s can be inferred from the sleep alone. GPIO/ADC polling
and dictionary/bound-method lookups are intentionally retained: caching methods,
reducing polling/ADC rate, moving volume to chunk boundaries or imposing 125 us
deadlines would change the hardware-tuned loop or response and need A/B evidence.

A timer/IRQ engine, deadline scheduler and per-sample diagnostic are not added.
Without real-device timing/pitch data, those changes could slow or speed playback
or cause catch-up bursts. The opt-in aggregate diagnostic provides a first real
measurement without adding probes to the hot loop, but cannot reveal sample
jitter, instantaneous sample periods, pitch, or analog/electrical quality. A
40 kHz carrier measurement alone also does not establish an 8000 Hz update rate.

## Signal and filtering audit

For ADC reading `a` in 0..65535 and WAV byte `b` in 0..255:

```text
volume8 = a >> 8                  # 0..255
gain    = volume8 // 25           # 0..10, 11 discrete levels including mute
duty    = b * gain                # 0..2550, passed directly to duty_u16
```

| Sample meaning | Byte | Duty at gain 1 | Duty at gain 10 |
| --- | --- | --- | --- |
| Minimum PCM amplitude | 0 | 0 | 0 |
| Just below midpoint | 127 | 127 | 1270 |
| PCM silence midpoint | 128 | 128 | 1280 |
| Just above midpoint | 129 | 129 | 1290 |
| Maximum PCM amplitude | 255 | 255 | 2550 |

- Unsigned PCM silence is byte 128, not byte 0. At a fixed gain it produces a
  constant requested PWM average. GPIO mute remains duty zero. Relative to that
  midpoint the sample deviation is `(b - 128) * gain`, spanning -128..127 times
  gain, but that signed quantity is **not** passed directly to unsigned PWM.
- The midpoint/DC level varies with volume; at maximum gain it requests duty
  1280/65535 (about 1.95%). The entire duty span uses only 2550/65535 (about 3.89%)
  of the requested 16-bit range. Actual PWM resolution depends on clock/carrier
  configuration; these integers are not proof of 16-bit analog resolution.
- ADC values 0..6399 give zero gain; gain changes in steps, and the top gain bin
  covers ADC 64000..65535. This loses fine volume resolution and can make noise
  near thresholds toggle gain. Reading ADC once per chunk or smoothing it might
  alter responsiveness and speed. Both are retained unchanged pending comparison.
- No numeric clipping/saturation occurs for valid bytes/ADC values: duties stay
  in 0..2550, far below 65535. No extra clamp is added per sample. Expanding to
  full-scale PWM or recentering at 32768 would materially increase signal/DC
  levels at the amplified speaker; its input coupling/allowable levels have not
  been established by this audit. Do not apply generic PWM-DAC math blindly.
- Starting from zero duty, stopping back to zero and switching gain can introduce
  steps/clicks. No fade, DC-removal or noise gate is added without amplifier and
  timing measurements. The current mapping and zero-duty safe mute are retained.
- No audio noise gate, bass filter or smoothing exists in the tracked PWM path.
  There are no obsolete audio-filter calculations to remove. `update_music_led`
  subtracts 128 to estimate a peak every eighth byte, scales green brightness
  and clamps the asymmetric -128 extreme to 255. It only controls the LED; it
  neither filters nor alters audio. The separate I2S conversion experiment is not
  migrated into this engine.

## Buffers and responsiveness

Reads remain bounded at 512 bytes. At nominal source rate that is 64 ms of
source audio, not a measured 64 ms response bound. SD read time and LCD work are
uninterruptible at Python sample-poll level. Stop/play/next/previous are checked
after each emitted sample; soft power is still checked at chunk boundaries.
Files remain validated/bounded to the data chunk, and close/mute on EOF, controls,
read failures and interrupts. The private PCM routine must only be called through
its owning `play_wav` wrapper.

A fresh bytes chunk is allocated per read; completing a short read can concatenate
bounded pieces. The old chunk can remain referenced during the next read. There
is allocation churn, not evidence of a retained-data leak. Audio arithmetic stays
in small integer ranges and does not deliberately construct per-sample buffers,
strings, lists or floats; actual VM/driver allocations still need profiling.

Buffer size, iteration type and read method are deliberately not changed.
Reusing a bytearray/memoryview or binding/caching methods might reduce allocation
or interpreter work, but changes timing and requires real FAT-file API validation
and A/B measurements. Forced GC or disabling GC is not added. No reduction in
heap usage, sample jitter, CPU cost or playback duration has been measured or
claimed in this pass. The default memory tradeoff is one extra call frame per
file; the optional diagnostic adds two clock reads and per-file report allocations
outside the hot loop. Printing reports adds inter-track overhead, so also compare
uninstrumented playback.

## Host validation

Run `python -m unittest discover -s tests -v`, `python -m compileall -q .`, and
`git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check`.
New tests cover all 256 unsigned values at each gain, golden midpoint/extreme
vectors, volume thresholds and per-sample ADC changes, range/no-clipping behavior,
I/O order, the 10 us sleep request, EOF across chunk boundaries without metadata,
mid-chunk stop/pause/track change, ADC/GPIO/PWM failures and bounded shuffle failure.
Diagnostic tests cover arithmetic, counter wrap, unknown/partial counts, zero
elapsed time and interrupt propagation. Virtual timing validates the diagnostic
calculation only. Prior SD/WAV/startup regressions continue to run.

Validation executed on CPython 3.12.14: all 94 tests passed, syntax compilation
passed, and the CRLF-aware whitespace check passed. No physical audio/timing
measurements were performed.

## Actual-hardware A/B checklist (not yet executed)

Use the [Thonny startup instructions](STARTUP_RELIABILITY.md) to back up the
working Pico files first. Record the actual working commit/settings; pre-Pass-4
software reference is `92b01e0`. Compare on the same Pico WH, MicroPython build,
CPU clock, SD card, speaker, supply, volume positions and identical known-good
WAV. Keep 40 kHz and sleep_us(10) throughout. If comparing the historical 44-byte
reader, use its already-working canonical WAV, so metadata handling differences
do not masquerade as timing changes. Do not write/format the music card.

For optional measurements, copy `tools/audio_timing.py` to the Pico root as
`audio_timing.py`. On the development computer obtain the known frame count of
the unchanged comparison file with Python's `wave` module (`getnframes()` and
`getframerate()`; confirm mono, one-byte samples and 8000 Hz). In a temporary copy
of each main version, just before INITIAL UI, insert this wrapper after the real
`play_wav` definition. Fill in the exact file/count before running:

```python
from audio_timing import measure_playback
known_samples = {"/sd/comparison.wav": 80000}  # EXAMPLE: replace with actual count
actual_play_wav = play_wav

def play_wav(path):
    count = known_samples.get(path)
    # Pass 5: an ended resume emits only the remaining tail, not the full song.
    if count is not None and resume_path == path:
        count -= resume_offset
    report = measure_playback(actual_play_wav, path, count if count else None)
    print("Audio timing:", path, report)
    return report["result"]
```

Do not import an additional `main` while it is running. Run only one temporary
copy at a time in Thonny; stop/reset between versions as documented in Pass 3.
The wrapper preserves normal state/results and is not installed in production.
No pre-play file read is added by the diagnostic. Use only uninterrupted full-file
`ended` runs for rate comparison; keep calls shorter than half the platform's
`ticks_ms` wrap period. The result includes validation, SD/GC/UI pauses and final
mute/close, so it is an aggregate, not a sample-clock or pitch measurement.

| Check | Compare original hardware reference versus updated firmware |
| --- | --- |
| Song duration | Repeat the same full song at least three times. Record elapsed_ms, N, nominal N/8000 seconds, actual average, variation and any audible stalls. Also compare uninstrumented recordings/wall time. Do not accept a speed change merely because it approaches 8000 numerically. |
| Playback pitch | Listen to the same passage or compare recordings with a known reference/tone from the existing test material. Record any pitch shift independently of total duration. |
| Audible distortion | Compare quiet/loud passages at matched physical knob positions. Check for crackle, asymmetric distortion or changed loudness; note clicks at start, stop, pause and track switching. |
| Background whine | Compare PCM midpoint silence/quiet material, muted STOPPED state and playback. Check carrier/noise with instruments if available; do not infer electrical behavior from mock duties. |
| Potentiometer response | Sweep slowly through the existing dead zone and each gain threshold, and turn it during playback. Check stepping, noise/jitter, maximum level and mute against the reference. |
| Button response | Compare short/held stop, play/pause, next, previous, simultaneous controls and soft OFF both mid-chunk and during title scrolling/SD pauses. Confirm mute and no stuck signal; held-repeat semantics are unchanged. |
| Long playback | Watch for worsening pauses, memory/GC issues or missed controls over repeated tracks, with diagnostics off as well as on. |

Record results rather than filling in assumed rates or improvements. Until the
hardware comparison is done, sample scheduling, signal recentering/full-scale
math, filtering, volume smoothing and buffer reuse remain deliberately deferred.
The current implementation is the stable working fallback.
