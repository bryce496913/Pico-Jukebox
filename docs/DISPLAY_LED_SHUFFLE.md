# Pass 6: LCD, RGB LED and shuffle

## Findings and changes

- LCD titles were reformatted repeatedly, and forced refresh advanced scrolling.
  Status changes also reset the title. Titles are now formatted once per selected
  filename; track selection (including selecting the only song again) resets to
  the first window. Pause/resume/status refresh preserves the window. Short titles
  remain static; long titles retain the three-space separator and 350 ms interval.
  Delayed polling advances one step, without a burst of catch-up writes.
- Padding uses slicing, length and explicit spaces, without `str.ljust()`.
  Printable ASCII is retained, control characters become spaces, and other Unicode
  characters become `?` for predictable LCD ROM behavior. Original filenames are
  retained for filesystem access. Long titles use one cached ring and slices of
  at most 16 characters rather than doubling the full title for each window.
  Existing successful-row caching avoids repeated I2C writes and display clears.
- Music peak sampling still examines every eighth byte once per audio chunk.
  Absolute unsigned-PCM amplitude spans 0..128, so brightness is now
  `10 + peak * 245 // 128`, replacing division by 127 and subsequent saturation.
  Silence maps to 10, byte 0 to 255, and byte 255 to 253. This changes LED
  brightness only, not speaker PCM conversion or volume calculations.
- A four-level brightness deadband compares against the last applied value;
  small fluctuations accumulate until that threshold is reached. Silence and
  maximum amplitude always settle immediately. There is no animation or timer.
  RGB channel caches omit unchanged PWM writes and commit only successful writes;
  teardown still forces zero and releases channels. Status transitions reset the
  music brightness cache. Music updates cannot overwrite stopped/paused colors.
- Retrying random choices could fail after 32 identical draws, and 16-bit draws
  could not address all entries of a very large library. Selection now draws once
  over eligible slots and skips the current index. Two-track navigation selects
  the other song without a draw; one-track navigation stays at zero. Boot still
  selects randomly from the full library. Ordinary libraries use `getrandbits(16)`;
  ranges above 65536 use `getrandbits(32)`. Modulo bias is retained; this is not a
  claim of mathematically uniform sampling. No rejection loop remains. RNG errors
  still enter existing safe error handling.

## Preserved behavior and timing boundaries

Boot, Next and automatic EOF advance select randomly, avoiding immediate repeats
when more than one song exists. Previous decrements the sorted library index with
wraparound; it does not replay selection history. Next/Previous preserve stopped,
playing or paused state and discard the old resume bookmark. Status colors remain
green/blue/red for playing/paused/stopped and off for soft OFF. No pins, formats,
display pages, controls, boot tests or drivers were replaced.

AST comparison against the pre-Pass-6 `main.py` confirmed `_play_pcm`, `play_wav`,
`poll_controls` and `seed_random` are unchanged. The 40 kHz carrier, legacy sample
math, per-sample ADC/GPIO work, tuned `sleep_us(10)`, chunk size and call sites of
LCD/music updates are retained. New scroll resets run at track transitions,
outside the sample loop. No new per-sample clocks, allocations or DSP were added.

The mock write-count regression processes 100 identical 512-byte music chunks
after setting PLAYING: red 0, green 1, blue 0 additional PWM writes. Title-format
instrumentation records one formatting call across 100 scroll updates for the
same filename. Static-title refreshes produce no additional LCD row writes.
These counts demonstrate omitted work, not measured Pico speed or audio quality.
LCD I2C operations remain synchronous and can delay playback at the existing
chunk-level update points; scrolling cadence depends on when those points run.
No audio timing accuracy or elimination of audible gaps is claimed.

## Host validation

`python -m unittest discover -s tests -q`: **133 tests passed**.
`python -m compileall -q .`: passed.
Whitespace validation with `cr-at-eol`: passed.

Coverage includes static/exact-width/long titles, padding, character fallback,
track reset, status refresh, wraparound windows and ticks, cached formatting and
PWM writes, brightness extremes/deadband, LED state transitions, failed PWM cache
writes, single/two/many-track selection, large index ranges, seeding, Previous,
RNG failures during playback/EOF, and existing controls/storage/audio regressions.
Old navigation fixtures now supply eligible-slot draws rather than direct track
indices; their expected state transitions and selected songs are retained.

## Pico/Thonny checks still required

1. Retain the previous firmware for comparison. Copy updated `main.py` and
   `rgb_led.py` with the existing modules. Keep all wiring and the same SD card.
2. Boot ON and OFF, then switch ON: check existing boot tests, random initial song,
   normal readiness, LCD address/backlight and safe silence while OFF.
3. Check short, exactly 16-character and long filenames. Watch padding, separator,
   smoothness and flicker. Pause/resume should preserve the scroll position;
   Next/Previous and automatic advance should begin at the title's first window.
   Verify non-ASCII filenames still open while their display uses fallback glyphs.
4. Confirm green music response, blue pause, red stop and fully off on soft OFF.
   Compare subtle fluctuations, quiet passages and loud passages with the previous
   firmware; confirm responsiveness is adequate without new animations.
5. Test one-, two- and several-song libraries: random boot, Next, EOF advance,
   no immediate repeats when possible, sorted-index Previous and held keys.
6. Compare the same song's wall-clock duration, pitch, distortion, background
   whine, potentiometer response and control latency against the previous firmware,
   especially during long-title scrolling. Use the existing optional on-device
   timing diagnostics if needed; no host test establishes physical timing.

If hardware comparison reveals a regression, retain the known working firmware
and record the observation before further tuning. Real-device validation was not
available for this pass.
