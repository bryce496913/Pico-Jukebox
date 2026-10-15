# Pico Jukebox

MicroPython jukebox for Raspberry Pi Pico WH. `main.py` is the existing PWM
application; `main_newspeaker.py` is a separate experimental MAX98357A/I2S
variant, not the baseline firmware.

See [the baseline audit](docs/BASELINE_AUDIT.md) for supported behavior, the pin
map, known issues, cleanup priorities, and required hardware checks.

Run the host regression suite from this directory with Python 3.10+:

```sh
python -m unittest discover -s tests -v
```

No third-party packages or GPIO devices are required. Use this explicit discovery
path: root `test_*.py` files are manual Pico diagnostics, some of which run forever
or write to the SD card. Do not run them as host tests. Do not copy `tests/` onto
the Pico as firmware.

Mocks establish software behavior only. They do not validate physical audio
quality, timing accuracy, SD compatibility, wiring, or electrical behavior.
