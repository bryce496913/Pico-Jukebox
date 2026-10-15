# Pass 2: SD storage and WAV reliability

This pass builds on the Pass 1 audit. The PWM jukebox still uses the same pins,
SPI1 keyword constructor, LCD setup, sorted track order, random-next selection,
previous-index wrapping, ADC gain, PWM carrier and per-sample sleep. No new
storage system, audio format, driver replacement or audio optimization is added.
Deploy the new `wav_reader.py` with the application and existing drivers.
The alternate I2S application is still experimental and has not received the PWM
application's WAV validation or storage UI changes; both applications share the
hardened SD driver.

## Confirmed defects and changes

| Defect found | Correction and regression evidence |
| --- | --- |
| SD `ioctl` used operations 7/8 for capacity/size, returned zero for standard 4/5, and success for unknown operations | Standard simple MicroPython block protocol: 1 init, 2 deinit, 3 sync, 4 actual CSD capacity, 5 size 512. Unsupported operations return `None`. Tested with distinct CSD v1/v2 fixtures and failure responses. |
| Invalid CSD structures were treated as v1 | Reject unsupported structures, invalid v1 block lengths and conflicts with OCR addressing. Capacity always comes from card CSD fields, never a fixed guessed card size. |
| Nonmultiples of 512 were silently truncated | Reject partial buffers before any transfer, plus negative/overflowing addresses. Multiple-block reads retain memoryviews of the caller's buffer; this was already correct, not a slicing-copy defect. Distinct-sector tests verify every byte reaches the original buffer; simulated writes verify all bytes and addresses. |
| Read/write/CSD/command SPI exceptions could leave CS low | Transaction `finally` blocks deassert CS before trailing clocks, including failed command responses and interrupts. A trailing clock failure can propagate, but CS has already been deasserted. Driver retains caller ownership of SPI; it does not deinit the bus. |
| Write busy wait was infinite; command waits used only retry counts | Busy wait expires after 1000 ms; command response wait has a 100 ms elapsed limit plus the original finite read cap. Token wait remains 300 ms. CMD0/ACMD41 also retain finite retries with 1000/4000 ms elapsed bounds; any individual command timeout aborts initialization early. All elapsed checks use wrapping `ticks_diff`. |
| Identification ran at the application's initial 1 MHz and accepted incomplete identity/status checks | Driver selects 100 kHz for identification (SD identification requires <=400 kHz), validates CMD8 echo/voltage code, CMD55/ACMD41, OCR readiness/voltage and CCS, then retains its existing attempt to switch to 10 MHz. SPI1 constructor, mode and wiring are preserved. Simulated streams test SD v1 SDSC, v2 SDSC, SDHC/SDXC addressing and bad/never-ready responses. |
| SD initialization/mount/list errors escaped startup; empty card blocked before the UI | One initialization/mount/discovery attempt per boot. Failure shows `SD ERROR`; no usable songs shows `NO WAV FILES`. Both remain muted in the main loop and respond to soft power. A listing failure after mount attempts unmount. No automatic remount, reboot or song retry loop is introduced. |
| `.wav` names included directories, invalid formats and unreadable files | Discovery keeps case-insensitive suffix filtering and original sorted ordering, then checks regular-file status and validates RIFF metadata. Invalid/unreadable entries are logged and excluded; valid files are retained in order for the same shuffle/previous logic. All opened files use context managers. |
| Fixed 44-byte skip treated arbitrary bytes as samples | Walk RIFF chunks, validate bounds and required metadata, locate data independently of header length, and stop at its declared length. Unknown chunks, optional PCM fmt extensions and odd chunk padding are skipped; headers, padding and trailing metadata never reach PWM. |
| Truncated data or a read failure could be mistaken for EOF or leave PWM cleanup to callers | Check file and RIFF/chunk extents before output, complete short file reads, and treat premature EOF as an error. Every playback exit mutes locally in `finally`, with immediate mute before error logging. Actual data EOF alone triggers the existing random-next flow. A soft-power interruption is a distinct result and does not choose a different track. |

## Accepted WAV files

Only RIFF/WAVE PCM encoding 1, one channel, 8000 Hz, 8 bits per sample, block
alignment 1 and byte rate 8000 are accepted. PCM bytes remain unsigned and retain
the established gain calculation. RIFF length and each chunk, including required
odd-length pad bytes, must fit the file. A single fmt chunk and a single nonempty
data chunk are required. Duplicate chunks, missing metadata/data, malformed
bounds, compressed/float/extensible encodings, other bit depths, rates and channel
counts are rejected. A zero-length data chunk is excluded to avoid a library of
empty files endlessly auto-advancing.

The parser reads only small headers and the first 16 fmt bytes, seeking over
metadata and audio while validating structure. It never allocates an unknown
chunk's declared size. Every chunk advances by at least eight bytes and is bounded
by the file's RIFF extent. It scans once during discovery and revalidates each
opened song immediately before playback, covering files changed after discovery.
Data reads remain at most 512 bytes and never extend into padding or other chunks.
Files may contain bytes beyond the declared RIFF extent; they are ignored.

## Failure and recovery behavior

A missing, invalid or unreadable song discovered at boot is excluded. If a listed
song disappears, changes format, truncates or suffers an I/O error later, playback
mutes, closes the file and enters STOPPED. It does not automatically skip/retry
that failed song. The user can navigate to another song and press play. The list
is not rewritten on transient failures, preserving established index/shuffle
behavior; an explicit retry is possible. An already-held play button retains the
baseline repeat behavior (not changed to edge debounce in this storage pass).

Missing card, invalid filesystem and empty/all-invalid libraries do not cause
an exception-driven restart loop. Restore the card/files and reset the Pico to
repeat discovery; hot-plug remount is not added. A valid empty/all-invalid mount
stays mounted for the session. Post-mount directory enumeration failure attempts
unmount and records a storage error; unmount failure is contained while the output
remains muted. No filesystem format/repair or writes occur during discovery or
playback. No new actual shutdown/power circuitry behavior is introduced.

## Host validation

Run from the repository root with Python 3.10+:

```sh
python -m unittest discover -s tests -v
python -m compileall -q .
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

The whitespace check treats CRLF endings in the existing firmware files as line
endings, preserving their original format. Syntax compilation does not execute
hardware diagnostics. Host tests use standard-library fakes, scripted SPI byte
streams and memory-backed files. There are no real GPIO accesses, SD mounts or
writes to any actual music card. Tests cover normal and failed initialization,
real CSD decoding, ioctl mapping, addressing, partial transfers, timeout/release
paths, empty card, discovery filtering, valid/invalid/truncated/unsupported WAV,
short reads, read failure after samples have played, interrupt cleanup and EOF.
Baseline expectations were updated only for authorized storage changes: valid
WAV fixtures replace zero-filled fake headers, playback now mutes locally on EOF,
and an empty music card reaches the main UI instead of blocking in startup.

Validation executed on CPython 3.12.14: all 52 tests passed; syntax compilation
and the CRLF-aware whitespace check passed. No physical tests were executed.

## Limits and required Pico verification

- Record the exact MicroPython build. Verify VfsFat calls the standard simple
  protocol, reports correct capacity for a known card, and mounts/list/plays using
  the unchanged SPI1 pins. The extended offset/erase block protocol is not added;
  this FAT driver does not support filesystem formatting/repair or other storage
  technologies. SDUC/MMC are not added.
- Verify identification at 100 kHz and the existing post-init 10 MHz speed on the
  real wiring, card and power supply. The host timeout tests exercise software
  deadlines; a blocking low-level SPI/Pin implementation cannot be preempted by
  these Python checks. Read CRC remains discarded and write CRC remains dummy as
  in the original driver; undetected electrical corruption is not ruled out.
- Use known-good 8-bit mono 8000 Hz songs, including a file with extra RIFF chunks.
  Compare audible playback, volume, EOF shuffle, pause/replay and navigation. The
  existing nominal 10 us sample sleep is deliberately unchanged; metadata
  validation at 8000 Hz is not a timing correction or a claim of exact playback
  rate/audio fidelity.
- Check missing card, invalid filesystem, empty/all-invalid library, disappeared
  file and card removal during playback. Confirm muted output and no restart
  loop; confirm reset after restoring storage. Verify soft-off/on during a song
  does not falsely advance the track, while retaining chunk-boundary polling.
- Files are seekable FAT files; discovery validates metadata/length, not every
  payload sector. A later payload read can still fail, which playback handles.
  Scanning each WAV header costs startup I/O; no hardware latency or heap
  endurance measurements were made.
- No physical tests have been executed in this pass. Mocks establish software
  behavior, not audio quality, electrical correctness or real-card reliability.
  Any optional physical write validation must use a disposable card, never the
  user's music card. Existing manual `test_sd.py` writes a file and is not run.

The unrelated baseline findings (tracked Wi-Fi credentials, input debounce,
missing-LCD blocking, I2S experiment state handling and remaining global/UI
cleanup) remain outstanding; see the historical audit. No credential values are
copied into new code, fixtures or documentation.
