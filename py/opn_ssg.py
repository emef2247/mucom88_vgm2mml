"""Native OPN (YM2203) SSG intervals with unchanged register evidence.

Mirrors the OPNA SSG replay for the YM2203 evidence shape (command
0x55, one port). The SSG section shares the chip prescaler with the
FM section; at the reference prescale 6 the effective SSG clock is
the master clock / 2, which makes identical register values sound at
the same pitch as on OPNA (master / 4 at prescale 6).
"""
import csv
from dataclasses import asdict, replace
import json
from pathlib import Path

from opna_ssg import SSGSegment
from psg import get_psg_mode


class OpnSSGAnalysis:
    def __init__(self, segments, source_end, clock_hz, clock_raw):
        self.segments = segments
        self.source_end = source_end
        self.clock_hz = clock_hz
        self.clock_raw = clock_raw

    @property
    def source_end_vgmticks(self):
        return self.source_end

    def dump(self, outdir, stem):
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        columns = list(SSGSegment.__dataclass_fields__) + ['effective_clock_hz']
        with (out / (stem + '.opn_ssg.segments.csv')).open(
                'w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
            writer.writeheader()
            for channel in self.segments.values():
                for segment in channel:
                    row = asdict(segment)
                    for key in ('registers', 'pass3_row'):
                        row[key] = json.dumps(row[key], separators=(',', ':'))
                    row['effective_clock_hz'] = segment.effective_clock_hz
                    writer.writerow(row)
        (out / (stem + '.opn_ssg.analysis.json')).write_text(json.dumps(dict(
            source_end_vgmticks=self.source_end, clock_hz=self.clock_hz,
            clock_raw=self.clock_raw, registers_reset_assumed=True,
            prescale_reset_assumed=6, initial_amplitude=0,
            hardware_envelope_output_modeled=False,
            segment_counts={ch: len(items) for ch, items in self.segments.items()}),
            indent=2) + '\n', encoding='utf-8')


def build_segments(trace_csv, *, end_vgmticks):
    """Replay SSG writes without target quantization or terminal mute writes."""
    if type(end_vgmticks) is not int or end_vgmticks < 0:
        raise ValueError('OPN SSG source end must be a nonnegative integer sample')
    with open(trace_csv, encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError('OPN SSG requires register evidence with a clock')
    clock = int(rows[0]['clock_hz'])
    clock_raw = int(rows[0]['clock_raw'])
    registers = [0] * 16
    prescale = 6
    result = {ch: [] for ch in range(3)}

    def snapshot(ch, tick, *, event_id=None, original_id=None, address=None,
                 register=None, data=None, command=None, original_tick=0, iteration=0):
        amplitude = registers[8 + ch] & 0x1f
        mode = get_psg_mode(ch, registers[7])
        audible = mode != 0 and bool(amplitude)
        return SSGSegment(ev_type='initial' if event_id is None else 'ssg_write',
            time=tick / 44100, ch=ch, ticks=tick * 60 // 44100, l=0,
            tone_period=registers[2 * ch] | ((registers[2 * ch + 1] & 15) << 8),
            volume=amplitude & 15, octave=0, scale='' if audible else 'r',
            volume_delta=0, pass3_row=(), vgmticks=tick, vgmticks_end=tick,
            mode=mode, noise_period=registers[6] & 31,
            envelope_enabled=(amplitude >> 4) & 1,
            envelope_period=registers[11] | (registers[12] << 8),
            envelope_shape=registers[13] & 15, mixer=registers[7],
            amplitude_register=amplitude, event_id=event_id,
            original_event_id=original_id, address=address, register=register,
            data=data, port=0, command=command, clock_hz=clock, clock_raw=clock_raw,
            prescale=prescale, original_vgmticks=original_tick,
            loop_iteration=iteration, registers=tuple(registers),
            envelope_trigger=register == 13,
            source_clock_hz=clock * 6 / prescale / 2)

    current = {ch: snapshot(ch, 0) for ch in range(3)}

    def close(ch, end):
        prior = current[ch]
        result[ch].append(replace(prior, vgmticks_end=end,
                                 l=end * 60 // 44100 - prior.ticks))

    previous_id = previous_address = -1
    previous_tick = previous_iteration = 0
    for row in rows:
        instance, tick, event_id, address, register, data, command, row_clock, raw, port = (
            int(row[key]) for key in ('chip_instance', 'vgmticks', 'event_id', 'address',
                                      'register', 'data', 'command', 'clock_hz', 'clock_raw', 'port'))
        if (instance != 0 or port != 0 or command != 0x55
                or row['chip_type'] != 'YM2203' or not 0 <= register <= 255
                or not 0 <= data <= 255 or row_clock <= 0
                or not 0 <= raw <= 0xffffffff or raw & 0x80000000
                or row_clock != raw & 0x3fffffff or (row_clock, raw) != (clock, clock_raw)):
            raise ValueError('Invalid or unsupported OPN SSG register evidence')
        iteration = int(row.get('loop_iteration') or 0)
        original_id = int(row.get('original_event_id') or event_id)
        original_tick = int(row.get('original_vgmticks') or tick)
        if (event_id < 0 or address < 0 or original_id < 0 or iteration < 0
                or not 0 <= original_tick <= tick or event_id <= previous_id
                or iteration < previous_iteration
                or iteration == previous_iteration and address <= previous_address
                or not previous_tick <= tick <= end_vgmticks):
            raise ValueError('OPN SSG evidence must be ordered and within source end')
        previous_id, previous_address, previous_tick, previous_iteration = (
            event_id, address, tick, iteration)
        if register <= 15:
            registers[register] = data
            channels = (register // 2,) if register < 6 else (
                (register - 8,) if 8 <= register <= 10 else tuple(range(3)))
        elif register in (0x2d, 0x2e, 0x2f):
            selected = 6 if register == 0x2d else 2 if register == 0x2f else (
                3 if prescale == 6 else prescale)
            if selected == prescale:
                continue
            prescale = selected
            channels = tuple(range(3))
        else:
            continue
        for ch in channels:
            prior_volume = current[ch].volume
            close(ch, tick)
            current[ch] = replace(snapshot(ch, tick, event_id=event_id,
                original_id=original_id, address=address, register=register,
                data=data, command=command, original_tick=original_tick,
                iteration=iteration), volume_delta=(registers[8 + ch] & 15) - prior_volume)
    for ch in range(3):
        close(ch, end_vgmticks)
    return OpnSSGAnalysis({ch: tuple(items) for ch, items in result.items()},
                          end_vgmticks, clock, clock_raw)
