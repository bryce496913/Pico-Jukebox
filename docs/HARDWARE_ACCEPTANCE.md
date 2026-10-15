# Pico hardware acceptance record

**All boxes are unverified until someone tests the actual Pico.** Host mocks do
not count as a pass. Use the existing wiring and a backed-up, known working music
card. Do not run SD write diagnostics on that card. Save the old firmware for
comparison; record failures and restore it if playback regresses.

## Record the tested system

- [ ] Date/operator, firmware commit, Pico WH board and power supply recorded.
- [ ] Thonny captures `sys.implementation` and `os.uname()`; exact MicroPython
  version/build and required APIs in README verified.
- [ ] Card make/capacity/type, FAT format, library size, LCD address and wiring
  compared against the README; no unexplained GPIO/bus changes.
- [ ] Fixture filenames, PCM sample counts/source durations and previous firmware
  revision recorded. Same card, songs, amplifier and volume used for comparisons.

## Startup and power

- [ ] Cold boot ON: backlight/legible LCD, existing two boot screens, one
  conservative sweep only, restored 40 kHz carrier, silence then STOPPED/red LED.
- [ ] Cold boot OFF: no unnecessary LCD/SD/PWM initialization or boot sound until
  ON. Soft power does not electrically disconnect the board.
- [ ] OFF during LCD screens and sweep: sound stops safely; ON completes deferred
  startup without repeating already attempted screens/sweep. Completed startup
  and subsequent OFF/ON never replay self-tests or remount unnecessarily.
- [ ] OFF while stopped/paused/playing and near EOF: no unintended random advance
  or audio; LED off, POWER OFF stable without repeated writes; ON is STOPPED.

## Storage and faults

Power down before changing wiring; simulate faults only with a safe hardware
procedure. Use a spare card for malformed/corrupt-filesystem tests. Hot removal
is not required to demonstrate a read failure and can damage filesystems.

- [ ] SPI1 initialization/mount/read succeeds with the installed firmware and
  actual card; driver identification and post-init clock settings are compatible.
- [ ] Missing card, invalid filesystem, empty library and all-invalid library:
  bounded error response, no continuous speaker signal or initialization loop.
- [ ] Valid WAVs with canonical/extra chunks play only their data; wrong format,
  truncated and unreadable files are excluded with useful Thonny messages.
- [ ] Missing/changed file after discovery or safely induced read failure: closed
  file, silence and STOPPED; explicit navigation can choose a valid song.
- [ ] Missing LCD, speaker/PWM failure where safely reproducible, and other
  disconnected peripherals: meaningful error in LCD/Thonny, safe outputs,
  no repeated startup attempts. After repair/reset, normal boot succeeds.
- [ ] Ctrl-C during playback/boot sweep: speaker quiet, outputs released, SD
  unmounted where mounted. Reset restores normal operation.

## Controls, display and LED

- [ ] Play/pause/resume retains position without replay/skip; Stop then Play starts
  the selected song from its beginning. Verify at chunk boundaries and near EOF.
- [ ] Single presses, held keys, bounce, simultaneous keys and rapid qualified
  presses give one intended action; Stop remains available while another key
  is held. Buttons held through boot/OFF require release before acting.
- [ ] Random boot, Next and automatic EOF avoid immediate repeats with multiple
  songs; one-track and two-track libraries work without stalls. Previous wraps
  backward through sorted names, not selection history.
- [ ] Next/Previous preserve playing/paused/stopped state, clear old bookmarks and
  restart the new title at its beginning. Paused selection starts at zero on Play.
- [ ] Short and exactly 16-character titles remain static and padded; long titles
  scroll with three spaces between repeats. Pause/resume keeps scroll position;
  track changes reset it. Check long and non-ASCII filenames, flicker and contrast.
- [ ] Green music reaction, blue pause, red stop and off during OFF are correct.
  Check quiet/loud passages and brightness stability against previous firmware.

## Audio comparison and endurance (release gates)

- [ ] Same songs before/after: record wall-clock duration and pitch, distortion,
  background whine, start/stop clicks, volume endpoints/steps and button latency.
  Include long-title scrolling and track changes; no quality/speed regression.
- [ ] Where available, inspect 40 kHz PWM and sample timing using suitable test
  equipment. Optional `tools/audio_timing.py` measures whole-call average only,
  including stalls; it cannot establish per-sample jitter or physical fidelity.
- [ ] Run at least two hours of continuous shuffled playback on the target setup,
  covering many tracks, then repeat pause/navigation/OFF/ON. Record elapsed time,
  track advances, failures and whether another run is needed for intended use.
- [ ] Record Pico `gc.mem_free()` / `gc.mem_alloc()` before and after repeated
  playback batches and `gc.collect()` at **stopped test checkpoints**, outside the
  sample loop. Capture comparable checkpoints over the soak, not every sample.
  Look for a falling post-collection baseline, MemoryError, stalls or leaked open
  files. Do not add periodic GC or diagnostic prints to normal playback.

## Sign-off

Record each result as pass/fail/not-tested with observations and measurements.
Approve a stable release only when the relevant checks above pass on the intended
firmware/card/hardware and exposed historical credentials have been rotated by
their owner. Until then this is a host-tested release candidate, not a fully
hardware-validated release.
