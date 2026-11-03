"""Parser for the consolidated established OPLL preset header.

The single source of truth for the established set is the C header
opll_presets_established.h (base YM2413 register bytes plus the
derived OPL / OPN / OPNA / OPM preset tables, all carrying the
established-set notice). Converters parse the header instead of
keeping a second data copy, so the set exists exactly once.

Default location: env OPLL_PRESETS_H, else
~/workspace/opll_calib/opll_presets_established.h (the set is not
part of this repository).
"""
from pathlib import Path
import os
import re

OPL_OP_FIELDS = ('am', 'vib', 'egt', 'ksr', 'multi', 'ksl', 'tl',
                 'ar', 'dr', 'sl', 'rr', 'ws')
OP4_OP_FIELDS = ('mul', 'tl', 'ks', 'ar', 'd1r', 'd2r', 'd1l', 'rr',
                 'dt', 'am')


def default_path():
    env = os.environ.get('OPLL_PRESETS_H')
    if env:
        return Path(env)
    return Path.home() / 'workspace' / 'opll_calib' / 'opll_presets_established.h'


def _array(text, name):
    m = re.search(re.escape(name) + r'\[[^\]]*\](?:\[[^\]]*\])?\s*=\s*\{(.*?)\};',
                  text, re.S)
    if not m:
        raise ValueError(f'array {name} not found in preset header')
    body = re.sub(r'//[^\n]*', '', m.group(1))
    return [int(tok, 0) for tok in re.findall(r'0x[0-9a-fA-F]+|\d+', body)]


def _rows(flat, width):
    if len(flat) % width:
        raise ValueError(f'preset table size {len(flat)} not a multiple of {width}')
    return [flat[i:i + width] for i in range(0, len(flat), width)]


def _op4_table(text, name):
    out = []
    for row in _rows(_array(text, name), 44):
        ops = [dict(zip(OP4_OP_FIELDS, row[4 + 10 * i:14 + 10 * i]))
               for i in range(4)]
        out.append({'fb': row[0], 'alg': row[1], 'pms': row[2],
                    'ams': row[3], 'ops': ops})
    return out


def decode_base_row(b):
    """8 YM2413 register bytes -> canonical voice fields."""
    def op(o, tl_reg):
        return {'am': o >> 7 & 1, 'vib': o >> 6 & 1, 'egt': o >> 5 & 1,
                'ksr': o >> 4 & 1, 'multi': o & 15}
    mod, car = op(b[0], b[2]), op(b[1], None)
    mod.update({'ksl': b[2] >> 6, 'tl': b[2] & 63})
    car.update({'ksl': b[3] >> 6, 'ws': b[3] >> 4 & 1})  # R#03 DC = bit 4
    mod.update({'ws': b[3] >> 3 & 1})                    # R#03 DM = bit 3
    for o, a, s in ((mod, b[4], b[6]), (car, b[5], b[7])):
        o.update({'ar': a >> 4, 'dr': a & 15, 'sl': s >> 4, 'rr': s & 15})
    return {'mod': mod, 'car': car, 'fb': b[3] & 7}


def load_presets(path=None):
    """Return {'base', 'opl', 'opn', 'opna', 'opm'} or None if absent."""
    path = Path(path) if path else default_path()
    if not path.exists():
        return None
    text = path.read_text(encoding='utf-8')
    base = _rows(_array(text, 'OPLL_ESTABLISHED_BASE'), 8)
    opl = []
    for row in _rows(_array(text, 'OPLL_PRESET_OPL'), 25):
        ops = [dict(zip(OPL_OP_FIELDS, row[1 + 12 * i:13 + 12 * i]))
               for i in range(2)]
        opl.append({'fb': row[0], 'ops': ops})
    return {'base': base, 'opl': opl,
            'opn': _op4_table(text, 'OPLL_PRESET_OPN'),
            'opna': _op4_table(text, 'OPLL_PRESET_OPNA'),
            'opm': _op4_table(text, 'OPLL_PRESET_OPM'),
            'path': str(path)}


def drum_voices(presets):
    """Canonical drum voices from the base rhythm entries: BD is
    entry 16; HH and SD share entry 17; TOM and TC share entry 18."""
    base = presets['base']
    return {'bd': decode_base_row(base[15]),
            'hh': decode_base_row(base[16]),
            'sd': decode_base_row(base[16]),
            'tom': decode_base_row(base[17]),
            'tc': decode_base_row(base[17])}
