# Pass 7: final integration and release readiness

## Recommendation

**Ready for hardware acceptance as a host-tested release candidate. Do not label
it a stable, fully hardware-validated release yet.** The exact installed firmware
build, physical playback comparison, real card/FAT compatibility and Pico heap
endurance still need recorded results. The owner must also confirm rotation of
the credential exposed in historical commits. No Pico was connected for this
review; the [acceptance record](HARDWARE_ACCEPTANCE.md) is deliberately unchecked.

## Scope and resolved cleanup

Reviewed the complete tracked repository: supported firmware/drivers, prototype,
manual diagnostics, host tests, timing tool, prior pass reports and ignores.
The existing six-module split is sufficient: `main.py` owns startup, playback,
state and UI; `buttons.py` and `wav_reader.py` isolate tested input/format logic;
three hardware drivers remain intact. No architecture rewrite is justified.

- Removed unused `main.py:get_volume8`; no tracked caller references it and
  playback uses its original inline ADC calculation. The similarly named helper
  in the I2S prototype remains because that prototype calls it.
- Removed unused `Pin` import from `tests/test_startup.py`. No package dependency
  files or pip runtime dependencies exist; none need replacing.
- Removed unreferenced `wifi_test.py`, an obsolete network-connect experiment
  containing private literals. No credential values are reproduced here. Network
  access was never a supported jukebox feature. Added ignores for local `.env`,
  `secrets.py` and `wifi_config.py`; these files are not deployment requirements.
- Moved `main_newspeaker.py` to `experiments/main_newspeaker.py`, retaining its code
  with an unsupported warning and a README. It is an older duplicate I2S player
  with known state/parser limitations; it is not deployed or imported at startup.
  This preserves reference work without advertising another supported entry point.
- Preserved all seven root hardware diagnostics. Speaker/LED diagnostics now
  release PWM in `finally` on completion, exceptions or Ctrl-C. The standalone
  speaker sweep uses the existing conservative boot duty (2000 instead of 30000),
  with its frequencies/duration retained. The manual SD script now unmounts in
  `finally` after mounting; its existing destructive write is clearly labelled
  and remains limited to spare-card use. The button logger's delay is correctly
  described as repeat throttling rather than production debounce.
- Grouped existing LCD/UI/carrier settings at the top of `main.py`, with a pointer
  to unchanged inline audio tuning and README wiring. Kept driver protocol
  constants local rather than creating another config module or shared imports
  from the executable entry point. Updated an ambiguous SD validation comment.
- Replaced the brief README with deployment requirements, wiring, exact file list,
  Thonny installation, current controls and troubleshooting. Marked the original
  audit as historical so resolved findings are not mistaken for current defects.
- Added CI for standard-library host tests and compilation on Python 3.10/3.12,
  plus CRLF-aware whitespace validation. CI is regression prevention, not a
  physical hardware or MicroPython runtime certification.

No hardware pins, speaker PCM calculations, timing sleeps, SD commands, WAV format,
user controls, display pages, startup sounds, shuffle modes or animations changed.

## Integrated behavior reviewed

| Area | Software evidence / retained behavior |
| --- | --- |
| Boot / self-tests | ON-gated stages, scan validation, one LCD test and conservative sweep; OFF defers work; 40 kHz restored and mute cleanup tested |
| SD / WAV | SPI1 keyword setup; actual capacity/addressing and block protocol tests; bounded failures and CS release; FAT mount remains hardware-only; RIFF metadata/format checked |
| Audio / volume | Legacy per-sample ADC/gain/duty/sleep retained; golden sample sequences, EOF/data bounds and fault mute tests |
| Controls | Single action per press/chord, qualified release, Stop priority, exact pause bookmark, state-preserving navigation, safe OFF/ON |
| Shuffle | Random boot/Next/EOF; no immediate repeats when possible, bounded one-draw selection; sorted-index Previous |
| LCD / LED | Portable padding, cached titles/rows, scroll resets, fallback glyphs, retained status colors and bounded brightness reaction |
| Errors / resources | Injected startup/runtime/IO faults, file close, mute, GPIO fallback, unmount and output teardown; latched faults avoid restart loops |
| Repetition | 250 automatic advances, no immediate repeat, one live file maximum, all files closed, same peripherals/mount reused |

AST comparison against the merged Pass 6 baseline confirms `_play_pcm`,
`play_wav`, both initialization functions, `poll_controls`, `apply_control`,
`enter_off`, `seed_random` and `random_track_index` are unchanged. Only constant
placement/comments and the unused helper were changed in supported runtime code.
The actual LCD driver paired with mocked I2C preserves the expected PCM sequence
while scrolling; virtual sleeps cannot demonstrate physical playback speed.

## Memory, exceptions and compatibility review

The application retains one sorted library, two LCD row caches, one current title
cache/ring, scalar resume state and fixed hardware objects. There is no growing
play history, task queue, periodic diagnostic collection or per-sample buffer
allocation. Playback uses fresh reads of at most 512 bytes; rare short-read
concatenation is also bounded by that request. RIFF payload sizes are skipped by
seek rather than allocated. LCD nibble bytearrays, cursor-offset lists and title
windows still allocate; garbage collection and synchronous LCD/SD work can stall
playback. No speculative buffer reuse, forced/disabled GC or scheduling changes
were introduced. Host mocks intentionally retain output histories; their heap
usage is not a model of Pico PWM or a device leak measurement.

Existing exception boundaries intentionally differ by responsibility: drivers
raise protocol failures after CS cleanup, the WAV wrapper closes/mutes and reports
an error, startup latches peripheral faults, and the outer loop releases outputs.
Ctrl-C/BaseException propagates after cleanup. Cleanup is best effort if an API or
the hardware itself fails. No known uncaught error remains in the exercised host
workflows; that statement does not establish coverage of every hardware failure.
The retained alternate I2S diagnostic/prototype are outside this acceptance scope
and retain older limitations; run neither on the supported PWM wiring.

Supported runtime imports use MicroPython hardware plus `os`, `time`, `random`;
there are no CPython-only imports or network imports in the deployment closure.
Host-only AST/IO/pathlib/mock dependencies stay in tests. No `str.ljust()` or new
firmware API was introduced. Exact MicroPython version/API availability remains
unverified until the installed build is recorded; do not upgrade a working board
solely for this cleanup.

## Security review (values withheld)

The baseline's known private literals were confirmed in the obsolete Wi-Fi file
and removed from the current tree. Literal-configuration checks and common private
key/GitHub-token/AWS-key pattern checks found no remaining candidates in current
source/configuration. These are limited scans, not a guarantee that every kind of
secret is detectable. The regression guard reports paths/lines only. Historical
commits still contain the removed Wi-Fi configuration: removal and ignore rules
are not credential revocation. The network owner must rotate the exposed password;
rotation and any coordinated history remediation have not been performed here.

## Validation results

On CPython **3.12.14**:

- `python -m unittest discover -s tests -v`: **139 passed**.
- `python -m compileall -q .`: passed, including retained diagnostics/prototype
  syntax; those hardware programs were not executed on physical devices.
- CRLF-aware `git diff --check`: passed.
- Runtime AST comparisons and limited sensitive-content review: passed as
  described above. Secret values were not printed.

Six new integration tests check the deployment import closure and absence of
literal private config, 250 auto-advances/resource reuse, actual LCD driver plus
mocked audio output, manual speaker/LED cleanup and memory-backed SD diagnostic
cleanup. SD diagnostic tests use only in-memory fake files, never a real card.
Existing 133 regressions remain passing. The new workflow is configured for Python
3.10/3.12; only the local 3.12 run is claimed here, not a completed remote CI run.

## Remaining release gates

1. Record firmware version and execute [hardware acceptance](HARDWARE_ACCEPTANCE.md)
   on the intended Pico/card/wiring, including boot/OFF, faults, controls and UI.
2. Compare duration, pitch, distortion/whine, volume and button response against
   the previous working firmware. This pass preserves source behavior; physical
   quality/speed and amplifier compatibility cannot be certified by mocks.
3. Run the hardware soak and record post-GC heap trends at stopped checkpoints.
   A 250-track host regression is not a two-hour hardware endurance run.
4. Confirm owner rotation of historical credentials and decide separately whether
   coordinated history remediation is required. No history rewrite was attempted.
5. Review remote CI results after pushing, resolve failures, and record hardware
   sign-off before tagging a stable release. No release tag/deployment is made
   by this cleanup PR.
