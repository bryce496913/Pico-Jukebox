"""Opt-in MicroPython whole-file timing; never imported by normal firmware.

Supply the known sample count for this call on an unchanged fixture: for a
resume, pass the remaining count, not the full song length.
The player must return 'ended' only when all samples have been emitted. This
measures the whole call, including headers, SD/GC/UI stalls and mute/close work;
it cannot measure individual sample periods, jitter, pitch or electrical audio.
"""
import time


def measure_playback(player, path, sample_count=None):
    if sample_count is not None and (not isinstance(sample_count, int) or sample_count <= 0):
        raise ValueError("Expected a positive known sample count, or None")
    started = time.ticks_ms()
    result = player(path)
    elapsed_ms = time.ticks_diff(time.ticks_ms(), started)
    # Interrupted/error calls have an unknown emitted-sample count. Do not infer
    # a rate from the whole file's size in those cases, or divide by zero.
    complete = result == "ended" and sample_count is not None
    return {
        "result": result,
        "elapsed_ms": elapsed_ms,
        "completed_samples": sample_count if complete else None,
        "source_duration_ms": sample_count * 1000 / 8000 if complete else None,
        "average_samples_per_second": sample_count * 1000 / elapsed_ms
        if complete and elapsed_ms > 0 else None,
    }
