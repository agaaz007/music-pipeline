"""Measure MIDI playing time by walking each file's tempo map.

Duration is the last event tick in any track, integrated over every tempo
change, so files that speed up or slow down are measured correctly.
"""

import struct
from pathlib import Path


def _vlq(data, i):
    value = 0
    while True:
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i


def _tracks(data):
    header_len = struct.unpack(">I", data[4:8])[0]
    _fmt, _ntrk, division = struct.unpack(">HHh", data[8:14])
    i = 8 + header_len
    chunks = []
    while i < len(data):
        if data[i : i + 4] != b"MTrk":
            i += 1
            continue
        length = struct.unpack(">I", data[i + 4 : i + 8])[0]
        chunks.append(data[i + 8 : i + 8 + length])
        i += 8 + length
    return division, chunks


def duration_seconds(path):
    """Playing time of one MIDI file, in seconds."""
    data = Path(path).read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"{path} is not a MIDI file")
    division, chunks = _tracks(data)

    tempos, last_tick = [], 0
    for chunk in chunks:
        i, tick, running, note_tick = 0, 0, None, 0
        while i < len(chunk):
            delta, i = _vlq(chunk, i)
            tick += delta
            if i >= len(chunk):
                break
            status = chunk[i]
            if status == 0xFF:
                kind = chunk[i + 1]
                length, i = _vlq(chunk, i + 2)
                payload = chunk[i : i + length]
                i += length
                if kind == 0x51 and length == 3:
                    tempos.append((tick, int.from_bytes(payload, "big")))
                elif kind == 0x2F:  # End of Track: anything after it is padding
                    break
            elif status in (0xF0, 0xF7):
                length, i = _vlq(chunk, i + 1)
                i += length
            else:
                if status & 0x80:
                    running = status
                    i += 1
                elif running is None:
                    break
                else:
                    status = running
                if status & 0xF0 in (0x80, 0x90):
                    note_tick = tick
                i += 1 if status & 0xF0 in (0xC0, 0xD0) else 2
        last_tick = max(last_tick, note_tick)

    if division < 0:  # SMPTE: ticks are already absolute time
        fps = -(division >> 8)
        return last_tick / ((29.97 if fps == 29 else fps) * (division & 0xFF))

    tempos.sort()
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0, 500_000))
    seconds, prev, current = 0.0, 0, tempos[0][1]
    for tick, usec in tempos:
        if tick > last_tick:
            break
        seconds += (tick - prev) / division * (current / 1e6)
        prev, current = tick, usec
    return seconds + (last_tick - prev) / division * (current / 1e6)


# No arrangement in this corpus runs past an hour. A larger value means the
# byte stream desynced -- one file parses as 121 hours -- so it is reported as
# unreadable rather than silently inflating the total.
IMPLAUSIBLE_SECONDS = 3600


def summarize(directory):
    """(rows, total_seconds) for every .mid in a directory, longest first.

    Durations beyond IMPLAUSIBLE_SECONDS come back as None, the same as a file
    that failed to parse: callers surface them instead of counting them.
    """
    rows = []
    for path in sorted(Path(directory).glob("*.mid")):
        try:
            seconds = duration_seconds(path)
            rows.append((path.name, seconds if seconds <= IMPLAUSIBLE_SECONDS else None))
        except (ValueError, IndexError, struct.error):
            rows.append((path.name, None))
    rows.sort(key=lambda r: (r[1] is None, -(r[1] or 0)))
    return rows, sum(r[1] for r in rows if r[1])


def human(seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"
