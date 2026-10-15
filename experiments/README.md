# Unsupported experiments

`main_newspeaker.py` is the preserved MAX98357A/I2S prototype, moved from the
repository root in Pass 7. It duplicates older playback/state logic and is not
maintained or validated as part of the GP18 PWM jukebox. Its code remains for
reference; it is not a deployment alternative. Do not copy it to the Pico or
rename it to `main.py` for the supported hardware.

The root `test_max98357a.py` is a separate manual I2S hardware diagnostic for
alternate wiring (GP2/3/4 and GP6). It is retained for reference and must not run
on the supported PWM setup. Other root `test_*.py` scripts are manual hardware
diagnostics, not host tests; see the main README.
