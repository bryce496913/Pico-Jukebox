# Early playback termination: diagnosis and physical retest

## Evidence and current conclusion

The owner reports successful physical upload of all six production files, boot
OFF then ON, both LCD messages, the speaker sweep, SD mount and a displayed random
song. Play then produces about 1–2 seconds of audio followed by STOPPED/red.
Those observations establish working startup; they do not identify the exit path.
No Pico or affected WAV/card was available for this change.

**The cause of that physical stop is not yet established.** There is no elapsed
playback timeout in `_play_pcm`. Host playback spans more than 24000 samples and
many 512-byte chunks without a spontaneous stop. Real EOF returns `ended`, selects
another song and stays PLAYING. A red LED therefore is not evidence of ordinary
EOF alone: an accepted Stop or subsequent error/fault can produce it.

Confirmed software paths, distinguished from hypotheses:

- GP17 low is accepted immediately. The latch debounces/rearms **release**, not
  initial contact. One low sample can stop playback even when Play remains held,
  because Stop intentionally overrides another latched key. A host test reproduces
  this limitation and existing priority; it does **not** prove electrical noise
  caused the user's event. Held/bouncing Stop cannot rearm without qualified
  release, restart playback or create repeated attempts. No button/latch behavior
  is changed here; a new press filter would change established response without
  evidence identifying this physical fault.
- An SD/file read exception, premature EOF or `None` read safely returns `error`,
  closes the file, mutes and goes STOPPED. Injected failures reproduce red without
  a Stop event. Actual sustained card reads at 10 MHz remain a hardware question.
- LCD/RGB or other latched hardware faults can also stop playback; their fault
  field and existing exception prints distinguish them. No hardware fault was
  inferred from the LED color alone.
- RIFF parsing returns the actual data offset/length; remaining count decreases
  by bytes read, and only validated data bytes play. Extra chunks/padding do not
  become audio, and truncation is not silently accepted as successful EOF.
  No incorrect remaining-count, premature-duration or auto-advance defect was
  found in the reviewed paths; neither the WAV parser nor SD protocol is rewritten.

This PR delivers the requested isolation tools rather than claiming an unproven
repair of the 1–2-second hardware failure.

## Instrumentation and timing boundaries

`PLAYBACK_DIAGNOSTICS = True` near the top of `main.py` enables **one summary per
returned playback attempt**, printed by the main loop after `play_wav()` has
closed its file and muted the speaker. The filename is captured before playback
because Next/Previous can change the selected index during the call. Existing
`WAV error:` and hardware fault messages are retained and may precede the summary.
There is no extra START print or per-sample/chunk logging.

Each summary includes exit reason, original path, classified cause, final state,
hardware/storage faults, elapsed milliseconds, emitted/remaining/total samples,
resume start offset, accepted control, phase and exception text. Runtime file/SD
failures do not set the startup `storage_error` latch, so `STORAGE: None` does not
rule out a read failure: use `CAUSE`, `PHASE` and `EXCEPTION` together.

- Phases `open` / `header` identify file-open or WAV/header-read problems; a header
  error can be malformed media **or** an SD read failure. Read its exception text.
- Phase `read` identifies data-fetch failures before that chunk is output. Counts
  then represent completed output, not bytes already fetched into a partial read.
- Phase `output` can include ADC, GPIO, PWM or music-LED activity. An exception
  there reports unknown (`None`) sample counts rather than inventing how many
  samples emitted before it. A latched fault takes classification precedence.
- Normal/control/OFF returns record their existing exact output offset. Counts
  emitted are **for the current attempt**, while total and remaining refer to the
  whole data chunk. A resumed attempt records its original start offset.

Metadata is a fixed set of scalars, updated at existing chunk boundaries and
accepted-control/return paths. The normal per-sample loop has no new logging,
clock calls, counter increments, allocations or debounce calculations. Its AST
matches the merged baseline apart from accepted-control return values that record
an offset after muting. ADC/gain/PWM arithmetic, 40 kHz carrier and `sleep_us(10)`
remain untouched. Initialization/self-test functions, Stop priority, pin map,
button latch, WAV parser and SD commands/timeouts/CS cleanup are unchanged.

Only two diagnostic timing calls occur around the entire attempt. Elapsed time
includes file/header/SD/UI work and mute/close, not just sample output; unavailable
clock readings produce `None`. Wrapping tick differences are used, not wall time.
Block-boundary metadata still costs instructions, and USB printing after silence
can lengthen the gap between attempts. No physical timing/speed claim follows
from the host comparison; disable diagnostics after collecting evidence and
compare full-song timing/pitch on the Pico. Ctrl-C still propagates after existing
cleanup rather than masquerading as a returned EOF.

## Example outcomes

These excerpts are generated by host fakes, **not measurements from the Pico**.
The actual summary also includes elapsed time, state, fault fields and counts.

```text
PLAYBACK EXIT: ended FILE: '/sd/A.wav' CAUSE: eof STATE: PLAYING ... SAMPLES_EMITTED: 1701 SAMPLES_REMAINING: 0 ... CONTROL: None
PLAYBACK EXIT: stopped FILE: '/sd/A.wav' CAUSE: button STATE: STOPPED ... SAMPLES_EMITTED: 1 SAMPLES_REMAINING: 1700 ... CONTROL: stop
WAV error: SD read timeout
PLAYBACK EXIT: error FILE: '/sd/A.wav' CAUSE: read_error STATE: STOPPED ... SAMPLES_EMITTED: 512 SAMPLES_REMAINING: 1189 ... PHASE: read EXCEPTION: 'SD read timeout'
```

## Thonny: isolate the actual failure

1. Back up the working Pico files. Stop the running program with Thonny Stop/
   Ctrl-C, upload this `main.py` and `sdcard.py`, retain the other four production
   modules and existing firmware/wiring, then reset. Do not run a second copy of
   `main.py` while another is active. Keep the Shell visible.
2. Leave `PLAYBACK_DIAGNOSTICS = True` and the SD clock at its default **10 MHz**
   first. Boot OFF then ON and verify the existing screens/sweep/mount still work.
3. Press and release Play. Capture the entire `WAV error:`/hardware message and
   `PLAYBACK EXIT:` line at the 1–2-second stop. Note the filename, source song
   length, whether any keys were held, accepted control, phase, exception and
   sample counts. Do not infer a cause from STOPPED/red alone.
4. If `CAUSE: button CONTROL: stop`, inspect GP17 wiring, pull-up/contact/noise
   conditions and whether Stop is held. Repeat with deliberate Stop and with no
   Stop contact. Do not disable Stop or alter the safety priority as a workaround.
   The current test proves a low input is accepted, not why the pin went low.
5. If `CAUSE: read_error` or a header-read exception implicates storage, perform
   the single-variable clock comparison below. If `CAUSE: hardware_fault`, follow
   the named peripheral and preceding exception. `PHASE: output` with an exception
   can implicate ADC/GPIO/PWM; it is not automatically an SD failure.
6. If `ended` appears, check total samples against the expected source duration
   (8000 samples per source second) and capture the **next** attempt's report.
   An error opening/reading the next random track can explain a subsequent stop.
7. On the Pico, confirm a complete known song plays through real EOF and random
   automatic advance stays PLAYING/green. Exercise Stop/red, pause/blue and exact
   resume, Next/Previous, volume, long-title scrolling, reactive green and soft OFF.
   Confirm closing/muting on failure and no repeated attempts while stopped.
8. After collecting the decisive output, set `PLAYBACK_DIAGNOSTICS = False` in
   `main.py`, upload and reset. Ordinary exception messages remain. Compare song
   duration/pitch, distortion, whine and button/volume response to the known working
   firmware. Full-song playback and shuffle are **ready for retest, not certified**.

### Temporary 1 MHz SD data-clock comparison

There is one data-clock setting, in **`sdcard.py`**, not the SPI1 constructor:

```python
SD_DATA_BAUDRATE_HZ = 10_000_000  # default, retained
```

Temporarily change that line to:

```python
SD_DATA_BAUDRATE_HZ = 1_000_000
```

Upload `sdcard.py` and reset so the cached module/card initialization is recreated.
Use the same card, file, wiring, diagnostics setting and control actions. Record
both outcomes and repeat before drawing conclusions. Improvement at 1 MHz supports
an SD signal/timing hypothesis but is not by itself proof of the electrical cause.
Restore 10 MHz after the comparison unless measured evidence supports keeping a
change in a follow-up. There is no automatic fallback/retry-speed reduction.
Identification remains 100 kHz, and SPI1 GP10/11/12 with CS GP13 is unchanged. The
existing driver TypeError compatibility path when setting the data speed is also
retained; the configured value is a request, not measured bus speed.

Do not run `test_sd.py` on the music card or perform destructive write tests.

## Host validation

`python -m unittest discover -s tests -v`: **153 passed**, including all 139
existing tests unchanged and 14 new tests. Compilation and CRLF-aware whitespace
checks passed. Coverage includes a >24000-sample song with extra chunks and
metadata, real data EOF/random advance, Stop noise/bounce/hold characterization,
pause/resume, original filename during navigation, midstream failure/EOF/None,
invalid/truncated/missing WAV, red/green/blue/OFF and mute behavior, latched faults,
unknown output counts, once-only reporting after close/mute, timing wrap/failure,
diagnostics disabled and 10 MHz/1 MHz protocol reads using a scripted SPI.

Enabled versus disabled diagnostics produce the same duty sequence and requested
sleep sequence in deterministic tests at multiple sizes. Exactly two additional
clock calls per entire attempt are observed, independent of chunk/sample count.
No host result establishes physical timing, electrical reliability, actual card
throughput or that this hardware's early-stop defect is resolved. The next piece
of root-cause evidence is the real Pico's exit line and preceding error, if any.
