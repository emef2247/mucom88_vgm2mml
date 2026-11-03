"""NES melodic lanes projected onto OPNA FM for MUCOMSX.

Pitch follows the target convention used by mucomsx_scc_opna
(the OPNA notation denominator 2**21 against the 8 MHz target
clock); the input here is the source frequency in Hz, computed
from the NES period at the bundle stage, because the NES period
formulas differ per lane kind and do not fit the SCC period
shape. Waveform-to-voice projection and volume controls are the
shared mucomsx_scc_opna additive-FM machinery.
"""
from collections import namedtuple
import math

from mucom_fm import FNUM, NOTES

TARGET_CLOCK_HZ = 8_000_000

PitchProjection = namedtuple('PitchProjection', (
    'commands', 'note', 'target_fnum', 'target_block', 'detune',
    'source_frequency_hz', 'target_frequency_hz', 'error_hz',
    'error_cents', 'target_clock_hz'))


def project_pitch_hz(frequency, *, target_clock_hz=TARGET_CLOCK_HZ):
    """Return the ordinary MUC note/detune nearest to a frequency."""
    if not math.isfinite(frequency) or frequency <= 0:
        raise ValueError('NES lane frequency must be positive')
    target_units = frequency * (144 * 2 ** 21) / target_clock_hz
    choices = []
    for block in range(8):
        fnum = round(target_units / 2 ** block)
        if not 1 <= fnum <= 2047:
            continue
        actual = target_clock_hz * fnum * 2 ** block / (144 * 2 ** 21)
        for index in range(1 if block == 0 else 0, 12):
            detune = fnum - FNUM[index]
            choices.append((abs(math.log2(fnum / FNUM[index])),
                            abs(actual - frequency),
                            block, index, fnum, detune, actual))
    if not choices:
        raise ValueError('NES lane frequency exceeds target OPNA FM range')
    _, _, block, index, fnum, detune, actual = min(choices)
    return PitchProjection((f'o{block + 1}', f'D{detune}'), NOTES[index],
                           fnum, block, detune, frequency, actual,
                           actual - frequency,
                           1200 * math.log2(actual / frequency),
                           target_clock_hz)
