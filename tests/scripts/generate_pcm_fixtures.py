"""Original, reproducible MSM6258 fixtures; no music or ROM-derived assets."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / 'tests/fixtures/public/opm_msm6258'


def write(register, value):
    return bytes((0xb7, register, value))


def wait(ticks):
    result = bytearray()
    while ticks:
        count = min(ticks, 65535)
        result.extend(b'\x61' + struct.pack('<H', count))
        ticks -= count
    return bytes(result)


def playback(data, *, clock=8000000, divider=512, pan=0, pan_changes=()):
    """An independently scheduled byte supply at floor(i * 88200 / rate)."""
    result = bytearray(write(0, 1))
    for register, value in zip(range(8, 12), clock.to_bytes(4, 'little')):
        result.extend(write(register, value))
    result.extend(write(12, {1024: 0, 768: 1, 512: 2}[divider]))
    result.extend(write(2, pan) + write(0, 2))
    period = Fraction(88200 * divider, clock)
    changes = dict(pan_changes)
    cursor = 0
    for i, value in enumerate(data):
        time = int(i * period)
        result.extend(wait(time - cursor))
        cursor = time
        if i in changes:
            result.extend(write(2, changes[i]))
        result.extend(write(1, value))
    end = int(len(data) * period)
    result.extend(wait(end - cursor) + write(0, 1))
    return bytes(result), end


def vgm(commands, *, opm=False, clock=8000000, flags=6):
    header = bytearray(0x100)
    header[:4] = b'Vgm '
    struct.pack_into('<I', header, 8, 0x171)
    struct.pack_into('<I', header, 0x34, 0xcc)
    struct.pack_into('<I', header, 0x30, 4000000 if opm else 0)
    struct.pack_into('<I', header, 0x90, clock)
    header[0x94] = flags
    result = header + commands + b'\x66'
    struct.pack_into('<I', result, 4, len(result) - 4)
    return bytes(result)


def cases():
    # Fixed arithmetic byte patterns exercise nibble order without recorded audio.
    data = bytes((i * 17 + 0x12) & 255 for i in range(512))
    commands, duration = playback(data, pan=1, pan_changes=((128, 2), (256, 3), (384, 0)))
    commands += wait(100)
    repeated, _ = playback(data, pan=2)
    commands += repeated
    yield 'reset_pan_hold', vgm(commands), dict(
        source_end_vgmticks=duration * 2 + 100,
        sample_bytes=[len(data)], sample_sha256=[hashlib.sha256(data).hexdigest()],
        playback_samples=[0, 0], starts=[0, duration + 100],
        ends=[duration, duration * 2 + 100], mdx_frequencies=[4, 4],
        source_pan=[1, 2], mdx_pan=[1, 2])
    other = bytes((i * 31 + 0x34) & 255 for i in range(128))
    low, low_duration = playback(other, clock=4000000, divider=1024)
    high, high_duration = playback(other)
    # Same encoded asset, different exact playback rate: one PDX slot.
    opm_start = bytes((0x54, 0x20, 0xc7, 0x54, 0x28, 0x4e, 0x54, 8, 0x78))
    opm_stop = bytes((0x54, 8, 0))
    yield 'opm_pcm_rates', vgm(opm_start + low + high + opm_stop, opm=True), dict(
        source_end_vgmticks=low_duration + high_duration,
        sample_bytes=[len(other)], sample_sha256=[hashlib.sha256(other).hexdigest()],
        playback_samples=[0, 0], starts=[0, low_duration],
        ends=[low_duration, low_duration + high_duration],
        mdx_frequencies=[0, 4], source_pan=[0, 0], mdx_pan=[3, 3])
    held = bytes(range(256)) * 4
    long_note, long_duration = playback(held)
    yield 'long_hold_stop', vgm(long_note + wait(100)), dict(
        source_end_vgmticks=long_duration + 100,
        sample_bytes=[len(held)], sample_sha256=[hashlib.sha256(held).hexdigest()],
        playback_samples=[0], starts=[0], ends=[long_duration],
        mdx_frequencies=[4], source_pan=[0], mdx_pan=[3])


def data_block(data, bank=4):
    return b'\x67\x66' + bytes((bank,)) + struct.pack('<I', len(data)) + data


def stream_setup(*, stream=0, bank=4, frequency=7813, step=1, base=0,
                 chip=0x17, port=0, register=1):
    return (bytes((0x90, stream, chip, port, register)) +
            bytes((0x91, stream, bank, step, base)) +
            bytes((0x92, stream)) + struct.pack('<I', frequency))


def stream_fast_start(block=0, flags=0, *, stream=0):
    return bytes((0x95, stream)) + struct.pack('<H', block) + bytes((flags,))


def stream_start(offset=0, mode=1, length=4, *, stream=0):
    return (bytes((0x93, stream)) + struct.pack('<I', offset) + bytes((mode,)) +
            struct.pack('<I', length))


def stream_cases():
    data = bytes((0x12, 0x34, 0x56, 0x78))
    prefix = data_block(data) + stream_setup()
    expected = dict(source_end_vgmticks=22, transfer_ticks=[0, 6, 12, 17],
                    transfer_bytes=list(data), sample_sha256=hashlib.sha256(data).hexdigest())
    chip_start = write(0, 1) + write(0, 2)
    for name, start in (
            ('stream95_finite', stream_fast_start()),
            ('stream93_count', stream_start()),
            ('stream93_to_end', stream_start(mode=3, length=0))):
        yield name, vgm(prefix + chip_start + start + wait(22) + write(0, 1)), expected
    # Stream exhaustion stops byte supply, without writing the chip control port.
    yield 'stream_natural_end', vgm(prefix + chip_start + stream_fast_start() + wait(22)), expected
    # A stream controller supplies bytes; it does not start the ADPCM decoder.
    yield 'stream_supply_only', vgm(prefix + stream_fast_start() + wait(22)), {
        **expected, 'sample_sha256': None}
    yield 'stream94_supply_stop', vgm(prefix + chip_start + stream_fast_start() +
        wait(7) + b'\x94\x00' + wait(4) + write(0, 1)), dict(
            source_end_vgmticks=11, transfer_ticks=[0, 6], transfer_bytes=list(data[:2]),
            sample_sha256=hashlib.sha256(data[:2]).hexdigest())
    yield 'stream_delayed_chip_stop', vgm(prefix + chip_start + stream_fast_start() +
        wait(122) + write(0, 1)), {**expected, 'source_end_vgmticks': 122}
    yield 'stream_stopped_supply_restart', vgm(prefix + chip_start + stream_fast_start() +
        wait(17) + write(0, 1) + wait(7) + write(0, 2) + stream_fast_start() +
        wait(22) + write(0, 1)), dict(source_end_vgmticks=46,
            transfer_ticks=[0, 6, 12, 17, 24, 30, 36, 41], transfer_bytes=[*data, *data],
            sample_sha256=hashlib.sha256(data[:3]).hexdigest())


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name, raw, expected in (*cases(), *stream_cases()):
        (DESTINATION / (name + '.vgm')).write_bytes(raw)
        (DESTINATION / (name + '.expected.json')).write_text(
            json.dumps(expected, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
