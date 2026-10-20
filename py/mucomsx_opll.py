"""Explicit approximate YM2413 voice and frequency projection for MUCOMSX.

This adapter generates native 32-byte OPNA DAT voices. MUCOMSX's subsequent
OPNA-to-ROM-OPLL voice selection is a separate lossy projection. Neither stage
claims waveform, envelope-time, vibrato or timbral equivalence.
"""
# BSD 3-Clause License
#
# Copyright (c) 2021, Aaron Giles
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# 
from dataclasses import dataclass
import math

from mucom_fm import FNUM, NOTES

TARGET_CLOCK_HZ = 8_000_000
# YMFM ym2413::s_default_instruments, Aaron Giles (BSD-3-Clause).
# Copyright-free OPLL ROM reconstruction by David Viens / Hubert Lamontagne.
# Source: ymfm/src/ymfm_opl.cpp (pinned local checkout; see third-party notice).
PRESET_PATCHES = (
    bytes.fromhex('71 61 1e 17 ef 7f 00 17'),
    bytes.fromhex('13 41 1a 0d f8 f7 23 13'),
    bytes.fromhex('13 01 99 00 f2 c4 11 23'),
    bytes.fromhex('31 61 0e 07 98 64 70 27'),
    bytes.fromhex('22 21 1e 06 bf 76 00 28'),
    bytes.fromhex('31 22 16 05 e0 71 0f 18'),
    bytes.fromhex('21 61 1d 07 82 8f 10 07'),
    bytes.fromhex('23 21 2d 14 ff 7f 00 07'),
    bytes.fromhex('41 61 1b 06 64 65 10 17'),
    bytes.fromhex('61 61 0b 18 85 ff 81 07'),
    bytes.fromhex('13 01 83 11 fa e4 10 04'),
    bytes.fromhex('17 81 23 07 f8 f8 22 12'),
    bytes.fromhex('61 50 0c 05 f2 f5 29 42'),
    bytes.fromhex('01 01 54 03 c3 92 03 02'),
    bytes.fromhex('41 41 89 03 f1 e5 11 13'),
)


def preset_patch(inst):
    """Return reconstructed YM2413 melodic preset 1..15; patch 0 needs trace bytes."""
    if not isinstance(inst, int) or not 1 <= inst <= 15:
        raise ValueError('YM2413 preset must be 1..15; supply trace patch for 0')
    return PRESET_PATCHES[inst - 1]


def _patch_bytes(patch):
    patch = bytes(patch)
    if len(patch) != 8:
        raise ValueError('YM2413 patch must contain exactly eight bytes')
    return patch


def _rate(value):
    # Preserve zero-rate meaning and endpoints; intermediate envelope rates
    # are an explicit parameter approximation, not chip-time equivalence.
    return 2 * value + bool(value)


def patch_voice(patch):
    """Return an approximate OPNA DAT record from an observed YM2413 patch.

    Algorithm 4 contains two parallel modulator/carrier pairs. Duplicate the
    source pair so either pair is available to the later MUCOMSX reducer.
    DAT storage order is OP1, OP3, OP2, OP4. This is a voice-selection proxy,
    not a four-operator rendering with equivalent amplitude.
    """
    patch = _patch_bytes(patch)
    record = bytearray(32)
    for index in range(4):
        carrier = index >= 2
        operator = int(carrier)
        flags = patch[operator]
        attack_decay = patch[4 + operator]
        sustain_release = patch[6 + operator]
        sustain_rate = 0 if flags & 0x20 else _rate(sustain_release & 15)
        fields = (
            flags & 15,
            0 if carrier else patch[2] & 63,
            (0xC0 if flags & 0x10 else 0) | _rate(attack_decay >> 4),
            (flags & 0x80) | _rate(attack_decay & 15),
            sustain_rate,
            sustain_release,
        )
        for group, value in enumerate(fields):
            record[1 + 4 * group + index] = value
    record[25] = (patch[3] & 7) << 3 | 4
    return bytes(record)


def patch_projection_report(patch):
    """Source flags and explicit approximation boundary for pass diagnostics."""
    patch = _patch_bytes(patch)
    return dict(
        projection='opll_two_operator_to_duplicated_opna_algorithm4_proxy',
        exact=False, source_patch_hex=patch.hex(),
        source_am=[bool(patch[i] & 0x80) for i in (0, 1)],
        source_vibrato=[bool(patch[i] & 0x40) for i in (0, 1)],
        source_sustained_eg=[bool(patch[i] & 0x20) for i in (0, 1)],
        source_ksr=[bool(patch[i] & 0x10) for i in (0, 1)],
        source_ksl=[patch[2] >> 6, patch[3] >> 6],
        source_waveform=[(patch[3] >> 3) & 1, (patch[3] >> 4) & 1],
        limitations=[
            'MUCOMSX subsequently selects an OPLL ROM preset from the OPNA proxy',
            'No waveform or timbre equivalence guarantee',
            'OPLL vibrato and key-scale attenuation have no exact DAT mapping',
            'Envelope rates and KSR are parameter approximations',
            'AM flag stored; modulation depth and speed are not preserved',
        ])


@dataclass(frozen=True)
class PitchProjection:
    commands: tuple[str, ...]
    note: str
    target_fnum: int
    target_block: int
    detune: int
    source_frequency_hz: float
    target_frequency_hz: float
    frequency_error_hz: float
    frequency_error_cents: float
    target_clock_hz: float


def project_pitch(fnum, block, source_clock_hz, *, target_clock_hz=TARGET_CLOCK_HZ):
    """Spell source pitch using ordinary OPNA FM note and signed D detune.

    YM2413: clock/72 * FNUM * 2**block / 2**19.
    MUCOMSX OPNA notation reference: clock/144 * FNUM * 2**block / 2**21.
    The explicit 8 MHz reference is MUCOMSX's OPNA notation domain; subsequent
    native OPLL playback quantization is outside this frequency-error proof.
    The 2**21 convention is confirmed by authored native OPLL export; using
    2**20 here produced an octave-low native export.
    """
    if not isinstance(fnum, int) or not 1 <= fnum <= 511:
        raise ValueError('Sounding YM2413 FNUM must be 1..511')
    if not isinstance(block, int) or not 0 <= block <= 7:
        raise ValueError('YM2413 block must be 0..7')
    if (not math.isfinite(source_clock_hz) or source_clock_hz <= 0
            or not math.isfinite(target_clock_hz) or target_clock_hz <= 0):
        raise ValueError('Source and target clocks must be finite and positive')
    source_frequency = source_clock_hz * fnum * 2 ** block / (72 * 2 ** 19)
    target_units = source_frequency * (144 * 2 ** 21) / target_clock_hz
    choices = []
    for target_block in range(8):
        target_fnum = round(target_units / 2 ** target_block)
        if not 1 <= target_fnum <= 2047:
            continue
        # Native D invalidates its previous-note cache (zero). Avoid o1c,
        # whose note code is also zero and can skip pitch initialization.
        for note_index in range(1 if target_block == 0 else 0, 12):
            detune = target_fnum - FNUM[note_index]
            frequency = (target_clock_hz * target_fnum * 2 ** target_block
                         / (144 * 2 ** 21))
            # Prefer the ordinary closest musical note/octave, then report
            # the quantization of that spelling rather than forcing a distant
            # note with a large compensating detune for one extra FNUM bit.
            spelling_distance = abs(math.log2(target_fnum / FNUM[note_index]))
            choices.append((spelling_distance, abs(frequency - source_frequency),
                            target_block, note_index, target_fnum, detune, frequency))
    if not choices:
        raise ValueError('Source OPLL frequency exceeds target FM range')
    _, _, target_block, note_index, target_fnum, detune, frequency = min(choices)
    return PitchProjection(
        (f'o{target_block + 1}', f'D{detune}'), NOTES[note_index],
        target_fnum, target_block, detune, source_frequency, frequency,
        frequency - source_frequency, 1200 * math.log2(frequency / source_frequency),
        target_clock_hz)


# OPNA rhythm @mask order retained by the native MSXG compatibility grammar.
# Source expanded Segment channels: BD9, SD10, TOM11, CYM12, HH13.
RHYTHM_MASKS = {9: 1, 10: 2, 11: 16, 12: 4, 13: 8}
RHYTHM_NAMES = {9: 'BD', 10: 'SD', 11: 'TOM', 12: 'CYM', 13: 'HH'}


@dataclass(frozen=True)
class RhythmUnit:
    start: int
    end: int
    commands: tuple[str, ...]
    note: str
    source_refs: tuple[tuple[int, int], ...]


def project_rhythm_segments(segments, clock_map, end_clock, *, source_end_sample=None):
    """Lower attack-only OPLL Segments to documented native MSXG commands.

    Units leave duration spelling to the ordinary renderer. Their clocks are
    accepted target clocks, not source ticks. This is explicitly approximate:
    source Segments record attacks but lack subsequent key-off/held controls.
    Native rhythm notes dump the previous selected mask at the next boundary.
    No invented source release time or target gate is written back to Segments.
    Distinct source instants collapsed by clock projection and duplicate hits
    of one instrument at a target instant are rejected rather than coalesced.
    """
    if not isinstance(end_clock, int) or end_clock < 0:
        raise ValueError('Rhythm end clock must be a nonnegative integer')
    if source_end_sample is not None and (not isinstance(source_end_sample,int) or source_end_sample<0):
        raise ValueError('Rhythm source end must be a nonnegative sample position')
    target_clock = clock_map if callable(clock_map) else clock_map.__getitem__
    grouped, audit, excluded = {}, [], []
    for channel, rows in segments.items():
        if channel not in RHYTHM_MASKS:
            continue
        for index, segment in enumerate(rows):
            if (getattr(segment, 'ev_type', '') != 'rhythm_expand'
                    or not getattr(segment, 'is_ryt', 0)
                    or not getattr(segment, 'keyon', 0)):
                continue
            source_start = getattr(segment, 'vgmticks', None)
            if source_start is None:
                raise ValueError('Rhythm attack requires source VGM sample position')
            if source_end_sample is not None and source_start>=source_end_sample:
                excluded.append(dict(source_channel=channel,source_segment_index=index,
                    source_start_vgmticks=source_start,reason='outside_emitted_source_timeline'))
                continue
            try:
                clock = target_clock(source_start)
            except KeyError as error:
                raise ValueError(f'No target clock for rhythm attack at source sample {source_start}') from error
            if not isinstance(clock, int) or not 0 <= clock < end_clock:
                raise ValueError('Rhythm attack has no positive target interval')
            attenuation = getattr(segment, 'vol', None)
            if not isinstance(attenuation, int) or not 0 <= attenuation <= 15:
                raise ValueError('OPLL rhythm attenuation must be 0..15')
            group = grouped.setdefault(clock, [])
            if any(row['source_start_vgmticks'] != source_start for row in group):
                raise ValueError('Distinct rhythm attack times collapsed to one target clock')
            if any(row['source_channel'] == channel for row in group):
                raise ValueError('Duplicate rhythm instrument attack at one target clock')
            row = dict(source_channel=channel, source_segment_index=index,
                       source_start_vgmticks=source_start, target_clock=clock,
                       instrument=RHYTHM_NAMES[channel], target_mask=RHYTHM_MASKS[channel],
                       source_attenuation=attenuation,
                       target_individual_level=31 - 2 * attenuation,
                       source_fnum=getattr(segment, 'fnum', None),
                       source_block=getattr(segment, 'block', None),
                       source_event_id=getattr(segment, 'source_event_id', None))
            group.append(row)
            audit.append(row)
    if not grouped:
        return (), dict(exact=False, attacks=[], excluded_attacks=excluded, limitations=[])
    units = []
    clocks = sorted(grouped)
    if clocks[0]:
        units.append(RhythmUnit(0, clocks[0], ('@0',), 'r', ()))
    for index, clock in enumerate(clocks):
        rows = sorted(grouped[clock], key=lambda row: row['source_channel'])
        stop = clocks[index + 1] if index + 1 < len(clocks) else end_clock
        levels, mask = [31] * 6, 0
        for row in rows:
            bit = row['target_mask']
            levels[bit.bit_length() - 1] = row['target_individual_level']
            mask |= bit
        commands = ('v63,' + ','.join(map(str, levels)), f'@{mask}')
        units.append(RhythmUnit(clock, stop, commands, 'c',
                                tuple((row['source_channel'], row['source_segment_index'])
                                      for row in rows)))
    return tuple(units), dict(
        exact=False, target_part='MSXG', excluded_attacks=excluded, attacks=sorted(audit, key=lambda row:
            (row['target_clock'], row['source_channel'])),
        release_policy='inferred_at_next_target_rhythm_boundary',
        level_policy='31_minus_twice_source_attenuation',
        limitations=[
            'Attack-only source Segments do not retain key-off or held controls',
            'Native rhythm note boundaries dump the previously selected mask',
            'Percussion pitch and held volume trajectories are not projected',
            'Individual levels use an approximate inverse attenuation mapping',
            'Waveform and envelope fidelity are not established',
        ])
