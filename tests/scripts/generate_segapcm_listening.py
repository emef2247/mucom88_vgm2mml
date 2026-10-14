"""Small authored OPM/SegaPCM inputs; no game/ROM-derived material."""
import argparse
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]


def write(channel, register, value):
    return b'\xc0' + struct.pack('<HB', channel * 8 + register, value)


def wait(samples):
    result = b''
    while samples:
        part = min(samples, 65535)
        result += b'\x61' + struct.pack('<H', part)
        samples -= part
    return result


def voice(channel, *, left=96, right=96, delta=32, end=15, looping=False):
    values = ((0x86, 1), (2, left), (3, right), (4, 0), (5, 0),
              (6, end), (7, delta), (0x84, 0), (0x85, 0),
              (0x86, 0 if looping else 2))
    return b''.join(write(channel, reg, value) for reg, value in values)


def fm():
    result = bytes((0x54, 0x20, 0xc7))
    for op in range(4):
        for reg, value in ((0x40, 1), (0x60, 32), (0x80, 31),
                           (0xa0, 0), (0xc0, 0), (0xe0, 15)):
            result += bytes((0x54, reg + op * 8, value))
    return result + bytes((0x54, 0x28, 0x40, 0x54, 0x30, 0, 0x54, 8, 0x78))


def patterns():
    # A 32-byte triangle repeats in a fully authored 4096-byte unsigned ROM.
    triangle = bytes(32 + (i if i < 16 else 31-i) * 12 for i in range(32))
    payload = triangle * 128
    body = struct.pack('<II', len(payload), 0) + payload
    rom = b'\x67\x66\x80' + struct.pack('<I', len(body)) + body
    stop_fm = bytes((0x54, 8, 0))
    setup = rom + fm()
    one = setup + voice(0) + wait(55125) + stop_fm + wait(11025)
    mix = (setup + voice(0, left=96, right=0, end=1, looping=True)
           + voice(1, left=0, right=96, delta=48, end=1, looping=True)
           + wait(22050) + write(0, 2, 48) + write(1, 7, 24)
           + wait(22050) + write(0, 0x86, 1) + write(1, 0x86, 1)
           + stop_fm + wait(11025))
    loop_setup = setup + voice(0, end=1, looping=True)
    loop = loop_setup + wait(22049) + write(0, 2, 48) + wait(1)
    return {
        'SEGAONE': (one, None, dict(source_samples=66150, channels=1,
            expected_natural_stop_seconds=4096 * 32768 / (4000000 * 32),
            description='One-shot unsigned PCM triangle and a finite FM note')),
        'SEGAMIX': (mix, None, dict(source_samples=55125, channels=2,
            description='Overlapping left/right hardware loops; live volume and delta changes')),
        'SEGALOOP': (loop, len(loop_setup), dict(source_samples=22050, channels=1,
            description='Held SegaPCM across declared VGM loop; short final control interval')),
    }


def make_vgm(commands, loop=None):
    header = bytearray(0x100)
    header[:4] = b'Vgm '
    struct.pack_into('<I', header, 8, 0x171)
    struct.pack_into('<I', header, 0x30, 4000000)
    struct.pack_into('<I', header, 0x34, len(header)-0x34)
    struct.pack_into('<II', header, 0x38, 4000000, 0x70000c)
    if loop is not None:
        struct.pack_into('<I', header, 0x1c, len(header)+loop-0x1c)
    raw = header + commands + b'\x66'
    struct.pack_into('<I', raw, 4, len(raw)-4)
    return bytes(raw)


def generate(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for name, (commands, loop, expected) in patterns().items():
        (outdir / (name+'.vgm')).write_bytes(make_vgm(commands, loop))
        (outdir / (name+'.expected.json')).write_text(
            json.dumps(dict(authored=True, clock_hz=4000000,
                            nominal_native_frame_rate=31250, **expected), indent=2)+'\n',
            encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir', type=Path,
                        default=ROOT/'tests/fixtures/public/opm_segapcm')
    generate(parser.parse_args().outdir)
