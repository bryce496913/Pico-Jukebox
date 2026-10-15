# Pass 5: playback controls and state management

This pass corrects existing controls; it adds no buttons, long-press actions,
formats or hardware changes. Inputs remain active-low with internal pull-ups:
GP16 Play/Pause, GP17 Stop, GP19 Next/random, GP21 Previous, GP22 soft power.
The speaker remains GP18 PWM at 40 kHz, with the same unsigned PCM/ADC scaling
and `time.sleep_us(10)`. Deploy **`buttons.py`** with `main.py` and the existing
`wav_reader.py`, `sdcard.py`, `i2c_lcd.py` and `rgb_led.py`.

## Defects corrected

- Both loops acted on raw low levels. A held Play key could start then immediately
  pause; navigation could fire repeatedly and cross into the next track. The
  250 ms sleeps throttled repeat without qualifying a release.
- Idle controls used separate `if` statements, allowing multiple actions in one
  iteration. Playback had a different priority/transition path.
- Pause closed the file and discarded position. Resume reopened from sample zero;
  using `tell()` at pause would instead skip the unplayed tail of the read-ahead
  chunk. Both behaviors are incorrect.
- Navigation outside the player forced STOPPED, including while paused. Soft OFF
  retained PLAYING/PAUSED, allowing unintended playback after ON.
- Raw OFF could occur during a file read; the returned chunk could still play.
  The player now checks OFF immediately after a read as well as at boundaries.

## State-transition table

`state` remains STOPPED, PLAYING or PAUSED. Soft OFF is a power overlay with audio
state STOPPED; no second competing playback state machine is introduced. Healthy
state LEDs are red/green/blue respectively. Each accepted control mutes before
changing state/index and updates the LCD/status LED through one shared function.

| Current state | Event | Result | Position / selection | Output and display |
| --- | --- | --- | --- | --- |
| STOPPED | Play | PLAYING | Selected song from beginning | PLAYING, green, playback begins |
| PLAYING | Play | PAUSED | Save first unplayed sample | Muted, PAUSED, blue |
| PAUSED | Play | PLAYING | Resume saved sample, or new selection from beginning | PLAYING, green |
| Any healthy ON state | Stop | STOPPED | Keep selection, discard saved position | Muted, STOPPED, red |
| STOPPED | Next / Previous | STOPPED | Random different / previous sorted index; position zero | Muted, new title, red |
| PLAYING | Next / Previous | PLAYING | Random different / previous sorted index; new song from beginning | Old file closes and mutes; new title, green |
| PAUSED | Next / Previous | PAUSED | Change selection, discard old position; new song at beginning | Muted, new title, blue; no automatic playback |
| PLAYING | Real data EOF | PLAYING | Random different next song; clear saved position | Mute between files, new title, green |
| Any state | OFF | Soft OFF / STOPPED | Keep selection, discard saved position | Muted, LED off, POWER OFF; fault detail on second row if present |
| Soft OFF | ON, healthy | STOPPED | Same selection at beginning; require fresh qualified press | Muted, STOPPED, red; no automatic playback or repeated self-tests |
| Any state | File/read/resume error | STOPPED | Clear saved position | Muted, STOPPED, red; existing error log |
| Any state | Hardware fault | STOPPED/fault | Clear saved position | Existing fault UI/console; release outputs and require repair/reset |

Previous remains previous in sorted track order, not playback history. Random
Pass 6 replaces the original 32-attempt rejection sampling with one draw over
eligible tracks; see [display, LED and shuffle](DISPLAY_LED_SHUFFLE.md). A paused
selection stays paused even with a single-track library. Pausing on the final
sample saves the data-end position; resume emits no duplicate sample and processes
normal EOF. Storage/fault states retain their earlier recovery rules and do not
turn ON into a successful ready state until the fault has actually been resolved.

## Debounce and button ownership

`buttons.py` contains a small shared `ButtonLatch`. First contact acts immediately;
then no repeat is accepted until all four keys have been observed released for
at least 30 ms. A low observation resets that release interval. This leading-edge
policy suppresses contact bounce/long holds without delaying the first action.
It does not promise to reject an isolated initial electrical glitch.

Priority is Stop, Play/Pause, Next, Previous in both idle and playback. A chord
has one action; lower-priority held keys cannot fire later as a side effect of
the first key being released. Stop may override a previously accepted different
held key, once, so a held Next/Play cannot prevent stopping. Otherwise release all
keys before another action. No chord or long-press feature is introduced.

The latch persists across file open/close, EOF, pause and track changes. Keys held
through boot or OFF must be released before a fresh action. Idle polling sleeps
5 ms between iterations; OFF/fault polling sleeps 20 ms. The artificial 250 ms
control sleeps and 100 ms EOF delay are removed. There is no blocking debounce
wait. The existing LCD line cache prevents repeated OFF/status writes.

During audio the existing raw GPIO checks remain in priority order. A small
integer offset advances per emitted sample; no enumerate tuple, string, list or
float is allocated per sample for position tracking. Release timing is checked
at most once per 32 samples, only when the latch is locked and all keys are high.
A held key is suppressed by a flag check without calling the latch helper for
every held sample (Stop's one-time override is checked separately).

These changes add necessary offset/flag work to the control portion of the sample
loop; they are **not** a claim that timing is identical on MicroPython. ADC/gain,
PWM writes and the tuned 10 us delay are unchanged. Perform the Pass 4 A/B timing
and pitch checks on the actual device. The 32-sample interval is not a measured
millisecond latency bound. SD reads, file validation and LCD bus operations remain
synchronous; a pulse entirely inside such a pause can still be missed. OFF during
a chunk is observed at its boundary, with no extra read emitted after observed
OFF. No interrupt/IRQ-driven audio or button engine is introduced.

## Exact pause position and resources

The bookmark contains the path, declared data size and number of samples already
emitted. On pause after chunk offset `k`, save `chunk_start + k + 1`, not the file
cursor (which can already be 512 bytes ahead). The sample counter only spans the
current chunk, avoiding per-sample tuples or growing integers. At resume:

1. Reopen the selected file and run the existing RIFF/format/bounds validation.
2. Check that the data size still matches and the bookmark is within its extent.
3. Seek relative to the **validated data start**, so extra RIFF chunks do not
   affect the position. Play only the remaining data bytes.

Files close while paused; no SD handle or audio chunk is retained across pause.
Stop, navigation, OFF, EOF, faults, interruption and failed resume clear the
bookmark. Repeated pauses accumulate the resumed offset correctly. A missing,
invalid or resized file stops safely with no replay or unbounded retry. This is
not transactional media tracking: same-length content replacement cannot be
identified without additional identity/checksum work, which is not added. Keep
music content unchanged during a playback session and reset to rediscover edits.

The optional Pass 4 timing helper must receive the **remaining** count for a
resumed call, not the full song length. Its documentation/example now subtracts
`resume_offset` when `resume_path` matches; unknown/zero counts do not infer a
rate. Full-song duration comparisons should use uninterrupted runs from STOPPED.

## Validation results

Executed on CPython 3.12.14:

- `python -m unittest discover -s tests -v`: **115 passed**.
- `python -m compileall -q .`: passed.
- `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check`:
  passed, preserving existing firmware line endings.

New tests use mocked GPIO and virtual time for the transition table/LCD/RGB,
press/release bounce, long holds, simultaneous controls/Stop override, clock wrap,
rapid navigation while playing and paused, held presses crossing EOF, release
qualification during audio, OFF from every state, OFF during a read or at EOF cleanup, no automatic
ON playback, and no repeated OFF LCD writes. Known PCM vectors verify exact
resume at samples 1, 7, 511, 512, 513, 1199 and 1200, including non-44-byte data
starts and repeated pauses. Tests also cover Stop/reset replay, paused navigation
to a distinct song, and missing/invalid/resized files on resume.

Earlier regression expectations were updated only where this pass intentionally
fixes behavior: held Play no longer pauses again, resume starts at the first
unplayed sample, OFF clears playback state, independent button scenarios include
a real release interval, and a failed random selection has an additional early
mute. Existing startup/storage/audio-math tests still pass. No physical audio,
electrical timing or Pico GPIO behavior is validated by these mocks.

## Pico/Thonny checks still required

Use [Pass 3 deployment instructions](STARTUP_RELIABILITY.md), including the new
`buttons.py`. No actual Pico tests or music-card writes were performed here.

- Verify each button with short presses, a long hold and release bounce. Check
  one action per press and that releasing a held Next does not skip again. Press
  Stop while holding another key and verify prompt mute. Try simultaneous keys
  and verify the documented priority without chained actions.
- Pause a recognizable passage, wait, resume and confirm continuity. Repeat near
  a chunk/file boundary and several times in the same song. Stop then Play should
  restart; Next/Previous while paused should change title but remain silent, then
  play the selected song from its beginning on Play.
- Navigate rapidly while playing, stopped and paused. Check file/title, state
  text, red/green/blue indication and absence of unintended STOPPED transitions.
- Switch OFF mid-song and while paused. Confirm zero output, LED off and POWER
  OFF without flicker/repeated writes. ON must show STOPPED and remain silent
  even if Play was held while OFF; release/repress should start the selected song.
  Verify no repeat sweep or boot-screen sequence on a normal OFF/ON cycle.
- Compare the same uninterrupted song duration, pitch, distortion, whine and knob
  response against the hardware-tested reference using the [Pass 4 checklist](PWM_AUDIO_RELIABILITY.md).
  The necessary counter/flag checks can alter interpreter timing; no playback
  rate or fidelity improvement is asserted. Record measured differences before
  considering scheduling or changing the tuned sleep.
- Confirm recovery after restoring a missing/changed song follows the existing
  explicit-user-action/reset rules. Test close/read failures only with controlled
  fixtures; do not run the write-oriented manual SD diagnostic on the music card.
