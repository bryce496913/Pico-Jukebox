"""Bounded RIFF/WAVE parsing for the jukebox's existing PCM format only."""


class WavError(ValueError):
    pass


def read_exact(stream, size):
    """Accept short reads, but never turn premature EOF into successful audio."""
    data = stream.read(size)
    if data is None:
        raise OSError("WAV read failed")
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            raise WavError("Truncated WAV")
        data += part
    return data


def _uint(data):
    return int.from_bytes(data, "little")


def audio_data(stream):
    """Validate the RIFF structure; seek to PCM data and return its byte count.

    Chunk payloads other than the first 16 fmt bytes are skipped with seeks,
    never allocated according to an untrusted size. Validate the whole declared
    RIFF extent against file length before playing any samples.
    """
    stream.seek(0, 2)
    file_end = stream.tell()
    stream.seek(0)
    header = read_exact(stream, 12)
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise WavError("Not RIFF/WAVE")
    riff_end = 8 + _uint(header[4:8])
    if riff_end < 12 or riff_end > file_end:
        raise WavError("Invalid RIFF size")

    fmt_seen = False
    data_offset = None
    data_size = 0
    position = 12
    while position < riff_end:
        if riff_end - position < 8:
            raise WavError("Truncated WAV chunk header")
        stream.seek(position)
        chunk = read_exact(stream, 8)
        size = _uint(chunk[4:8])
        payload = position + 8
        next_position = payload + size + (size & 1)
        if next_position > riff_end:
            raise WavError("WAV chunk exceeds RIFF size")
        if chunk[:4] == b"fmt ":
            if fmt_seen or size < 16:
                raise WavError("Invalid WAV format chunk")
            fmt = read_exact(stream, 16)
            if (_uint(fmt[0:2]) != 1 or _uint(fmt[2:4]) != 1 or
                    _uint(fmt[4:8]) != 8000 or _uint(fmt[8:12]) != 8000 or
                    _uint(fmt[12:14]) != 1 or _uint(fmt[14:16]) != 8):
                raise WavError("Requires 8-bit mono 8000 Hz PCM")
            fmt_seen = True
        elif chunk[:4] == b"data":
            if data_offset is not None or size == 0:
                raise WavError("Invalid WAV data chunk")
            data_offset, data_size = payload, size
        position = next_position

    if not fmt_seen or data_offset is None:
        raise WavError("Missing WAV fmt or data chunk")
    stream.seek(data_offset)
    return data_size
