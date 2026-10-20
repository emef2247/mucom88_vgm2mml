"""One-channel four-carrier OPNA approximation for SCC source voices.

Source waves and software-volume runs remain caller-owned evidence. Harmonic
selection and TL quantization reuse the inherited SCC additive projection;
ordinary OPNA DAT/MUC syntax is applied only in this target adapter.
"""
from dataclasses import dataclass
import hashlib
import math

from psg_scc_opm import waveform_voice, carrier_tl, source_frequency
from mucom_fm import FNUM, NOTES, OPERATOR_NUMBERS
from mucomsx_opll import PitchProjection

TARGET_CLOCK_HZ = 8_000_000


@dataclass(frozen=True)
class SccOpnaVoice:
    dat: bytes
    harmonics: tuple[int, ...]
    amplitudes: tuple[float, ...]
    gain: float
    report: dict


def _levels(amplitudes, volume, gain, enabled=True):
    if not isinstance(volume, int) or not 0 <= volume <= 15:
        raise ValueError('SCC source volume must be 0..15')
    factor = gain * volume / 15 if enabled else 0
    return tuple(carrier_tl(amplitude * factor) for amplitude in amplitudes)


def project_waveform(waveform_hex, *, gain=.125):
    """Return a volume-15 DAT proxy and source-derived spectral diagnostics.

    All four operators are carriers (algorithm7, feedback0). Envelope attack is
    fastest with no decay/sustain decay; source software-volume runs are
    separately expressed through all four carrier TLs. No waveform phase, DC,
    Nyquist component or unselected harmonic is synthesized by this proxy.
    """
    if not math.isfinite(gain) or gain <= 0:
        raise ValueError('SCC additive gain must be finite and positive')
    voice = waveform_voice(waveform_hex)
    raw = bytes.fromhex(waveform_hex)
    levels = _levels(voice.amplitudes, 15, gain)
    dat = bytearray(32)
    for index, (harmonic, tl) in enumerate(zip(voice.harmonics, levels)):
        for group, value in enumerate((harmonic, tl, 31, 0, 0, 15)):
            dat[1 + group * 4 + index] = value
    dat[25] = 7
    report = dict(
        projection='scc_four_harmonic_opna_algorithm7', exact=False,
        source_waveform_hex=raw.hex(), waveform_sha256=hashlib.sha256(raw).hexdigest(),
        harmonics=list(voice.harmonics), amplitudes=list(voice.amplitudes),
        source_phases=list(voice.phases), source_dc=voice.dc,
        retained_ac_fraction=voice.retained_ac_fraction,
        gain=gain, volume15_tls=list(levels),
        limitations=[
            'Only four harmonics 1..15 are retained on one OPNA FM channel',
            'Source phases, DC and Nyquist harmonic are not reproduced',
            'Carrier TLs quantize amplitudes in 0.75 dB steps',
            'Fixed gain is a projection policy, not acoustic calibration',
            'OPNA envelope and key transitions differ from SCC oscillators',
        ])
    return SccOpnaVoice(bytes(dat), voice.harmonics, voice.amplitudes, gain, report)


def volume_controls(voice, volume, *, enabled=True):
    """Restore all carrier TLs after voice loading/v and at held gain changes.

    Native v overwrites additive carrier ratios; the renderer must issue these
    explicit controls afterward. Source gate/rest allocation remains external.
    TL127 is maximum attenuation, not an assertion of exact digital silence.
    """
    levels = _levels(voice.amplitudes, volume, voice.gain, enabled)
    return tuple(f'yTL,{operator},{tl}'
                 for operator, tl in zip(OPERATOR_NUMBERS, levels))


def project_pitch(period, source_clock_hz, *, target_clock_hz=TARGET_CLOCK_HZ):
    """Return ordinary MUC note/detune for VGM SCC clock /16/(period+1).

    The native MUCOMSX OPNA notation convention uses denominator 2**21.
    Periods0..8 are silent under the established SCC source model and cannot
    produce a sounding pitch. Source evidence is never clamped or modified.
    """
    if not isinstance(period, int) or not 0 <= period <= 4095:
        raise ValueError('SCC period must be 0..4095')
    if (not math.isfinite(source_clock_hz) or source_clock_hz <= 0
            or not math.isfinite(target_clock_hz) or target_clock_hz <= 0):
        raise ValueError('Source and target clocks must be finite and positive')
    frequency = source_frequency('scc', source_clock_hz, period)
    if frequency <= 0:
        raise ValueError('SCC period0..8 has no sounding frequency')
    target_units = frequency * (144 * 2 ** 21) / target_clock_hz
    choices = []
    for block in range(8):
        fnum = round(target_units / 2 ** block)
        if not 1 <= fnum <= 2047:
            continue
        actual = target_clock_hz * fnum * 2 ** block / (144 * 2 ** 21)
        for index in range(1 if block == 0 else 0, 12):
            detune = fnum - FNUM[index]
            choices.append((abs(math.log2(fnum / FNUM[index])), abs(actual - frequency),
                            block, index, fnum, detune, actual))
    if not choices:
        raise ValueError('SCC frequency exceeds target OPNA FM range')
    _, _, block, index, fnum, detune, actual = min(choices)
    return PitchProjection((f'o{block + 1}', f'D{detune}'), NOTES[index], fnum,
                           block, detune, frequency, actual, actual - frequency,
                           1200 * math.log2(actual / frequency), target_clock_hz)
