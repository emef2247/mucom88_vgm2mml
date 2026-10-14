"""Explicit SegaPCM runtime reconstruction, separate from observed source State.

The reset/profile assumptions follow the SegaPCM model used by VGMPlay:
RAM starts at FF, samples are unsigned bytes centred at80, and each frame
advances a16.8 address by the programmed delta. No target quantization,
resampling, clipping, or ADPCM encoding occurs here.
"""
from array import array
import csv
from dataclasses import asdict, dataclass
from fractions import Fraction
import json
from pathlib import Path
import sys


@dataclass(frozen=True)
class PlaybackGate:
    gate_id: int
    chip_instance: int
    channel: int
    start_vgmticks: Fraction
    end_vgmticks: Fraction
    start_event_id: int
    start_source_event_id: int
    loop_iteration: int
    end_event_id: int | None
    termination: str


@dataclass(frozen=True)
class RuntimeEvent:
    vgmticks: Fraction
    chip_instance: int
    channel: int
    kind: str
    address_fixed: int
    control: int
    source_event_id: int | None
    event_id: int | None


@dataclass(frozen=True)
class PlaybackAnalysis:
    sample_rate: Fraction
    samples: array
    gates: tuple[PlaybackGate, ...]
    runtime_events: tuple[RuntimeEvent, ...]
    source_end_vgmticks: int
    loop_start_vgmticks: int | None
    issues: tuple[str, ...]
    missing_rom_reads: int = 0
    reset_profile: str = 'VGMPlay-compatible RAM FF; runtime fractions zero'

    @property
    def frame_count(self):
        return len(self.samples) // 2

    def dump(self, output_dir, stem):
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        waveform = root / f'{stem}.segapcm.native.s32le'
        data = array('i', self.samples)
        if data.itemsize != 4:
            raise ValueError('Native waveform requires a32-bit array integer')
        if sys.byteorder != 'little':
            data.byteswap()
        waveform.write_bytes(data.tobytes())
        gates = root / f'{stem}.segapcm.playback_gates.csv'
        events = root / f'{stem}.segapcm.runtime.csv'
        _dump_rows(gates, self.gates, PlaybackGate)
        _dump_rows(events, self.runtime_events, RuntimeEvent)
        manifest = root / f'{stem}.segapcm.playback.json'
        summary = dict(
            model='explicit SegaPCM runtime reconstruction',
            reset_profile=self.reset_profile,
            assumptions=['RAM reset to FF; runtime address fractions reset to zero',
                         'Sparse ROM holes and resized ROM backing read as80',
                         'Frame zero is at source time zero; rate is exact clock/128',
                         'Runtime natural stop and sample loops are inferred by this model',
                         'Native mixed waveform is not clipped or target-quantized'],
            sample_rate_numerator=self.sample_rate.numerator,
            sample_rate_denominator=self.sample_rate.denominator,
            channels=2, sample_format='s32le',
            sample_units='sum of (ROM byte -128) * (channel volume &127)',
            frame_count=self.frame_count,
            source_end_vgmticks=self.source_end_vgmticks,
            loop_start_vgmticks=self.loop_start_vgmticks,
            gate_count=len(self.gates), runtime_event_count=len(self.runtime_events),
            missing_rom_reads=self.missing_rom_reads,
            issues=list(self.issues), waveform_path=str(waveform),
            gates_csv_path=str(gates), runtime_csv_path=str(events),
            analysis_json_path=str(manifest),
            source_state_modified=False,
            current_files=[str(waveform), str(gates), str(events), str(manifest)])
        manifest.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        return summary


def _dump_rows(path, objects, record_type):
    fields = list(record_type.__dataclass_fields__)
    fraction_fields = ('start_vgmticks', 'end_vgmticks') if record_type is PlaybackGate else ('vgmticks',)
    fields += [name + suffix for name in fraction_fields for suffix in ('_numerator', '_denominator')]
    with open(path, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for obj in objects:
            row = asdict(obj)
            for name in fraction_fields:
                value = row[name]
                row[name] = str(value)
                row[name + '_numerator'] = value.numerator
                row[name + '_denominator'] = value.denominator
            writer.writerow(row)


class _Rom:
    """Sparse ordered ROM overlays; uncovered bytes retain neutral sample80."""
    def __init__(self):
        self.size = 0
        self.mask = 0
        self.blocks = []
        self.cached = None
        self.missing_reads = 0

    def update(self, block):
        size, start = block.declared_rom_size, block.start_address
        if block.malformed_header or size is None or start is None:
            raise ValueError('Malformed SegaPCM ROM block cannot be reconstructed')
        if size <= 0 or start < 0 or start + len(block.payload) > size:
            raise ValueError('SegaPCM ROM block is outside its declared ROM size')
        if self.size != size:
            # ROM resizing in the reference resets the backing buffer to80.
            self.blocks.clear()
        self.size = size
        self.mask = (1 << (size - 1).bit_length()) - 1
        self.blocks.append((start, start + len(block.payload), block.payload))
        self.cached = None

    def read(self, address):
        if not self.size:
            self.missing_reads += 1
            return 0x80
        address &= self.mask
        cached = self.cached
        if cached is not None and cached[0] <= address < cached[1]:
            return cached[2][address - cached[3]]
        for index in range(len(self.blocks) - 1, -1, -1):
            block = self.blocks[index]
            if block[0] <= address < block[1]:
                # Cache only until the next newer overlay intersects the range.
                lo, hi, data = block
                for newer in self.blocks[index + 1:]:
                    if address < newer[0] < hi:
                        hi = newer[0]
                    if lo < newer[1] <= address:
                        lo = newer[1]
                self.cached = (lo, hi, data, block[0])
                return data[address - block[0]]
        self.missing_reads += 1
        return 0x80


def render(trace, end_vgmticks, loop_start=None, *, max_frames=32_000_000,
           max_runtime_events=1_000_000):
    """Reconstruct one supplied source traversal, preserving its event order.

    VGM-loop expansion is the reader's responsibility. ``loop_start`` is
    provenance only and never resets emulator state. Events at a source time
    apply before the first native frame at or after that time.
    """
    if end_vgmticks < 0 or not trace.clock_hz:
        raise ValueError('SegaPCM playback requires a declared clock and nonnegative duration')
    if loop_start is not None and not 0 <= loop_start <= end_vgmticks:
        raise ValueError('SegaPCM loop provenance is outside the source traversal')
    if trace.clock_raw & 0x80000000:
        raise ValueError('Unsupported SegaPCM clock flag')
    shift = trace.interface_raw & 0xff
    if shift >= 32:
        raise ValueError('Unsupported SegaPCM bank shift')
    # Bits8..15 and24..31 have no defined meaning in the supported interface.
    if trace.interface_raw & 0xff00ff00:
        raise ValueError('Unsupported SegaPCM interface flags')
    ordered = sorted([*trace.writes, *trace.rom_blocks], key=lambda e: e.source.event_id)
    instances = {e.source.chip_instance for e in ordered}
    if 1 in instances and (not trace.dual_chip or trace.extra_header_offset):
        raise ValueError('Second SegaPCM clock/interface is not modeled')
    if any(e.source.chip_instance not in (0, 1) for e in ordered):
        raise ValueError('Unsupported SegaPCM chip instance')
    if any(a.source.vgmticks > b.source.vgmticks for a, b in zip(ordered, ordered[1:])):
        raise ValueError('SegaPCM source events are not monotonic')
    if any(e.source.vgmticks < 0 or e.source.vgmticks > end_vgmticks for e in ordered):
        raise ValueError('SegaPCM event is outside the source traversal')
    rate = Fraction(trace.clock_hz, 128)
    time_den = trace.clock_hz
    time_step = 44100 * 128
    frame_count = (end_vgmticks * time_den + time_step - 1) // time_step
    if frame_count > max_frames:
        raise ValueError(f'SegaPCM native render exceeds the {max_frames} frame resource limit')
    chips = sorted(instances)
    ram = {chip: bytearray([0xff]) * 0x800 for chip in chips}
    fractions = {chip: [0] * 16 for chip in chips}
    rom = {chip: _Rom() for chip in chips}
    active = set()
    active_order = ()
    opened = {}
    gates, events = [], []
    samples = array('i')
    issues = []
    if not trace.rom_blocks and trace.writes:
        issues.append('No source ROM blocks; uncovered ROM reads produce neutral80')

    def runtime(chip, channel, kind, time, source=None):
        if len(events) >= max_runtime_events:
            raise ValueError('SegaPCM runtime event resource limit exceeded')
        memory = ram[chip]
        base = channel * 8
        address = ((memory[base + 0x85] << 8) | memory[base + 0x84]) << 8
        events.append(RuntimeEvent(time, chip, channel, kind,
                                   address | fractions[chip][channel], memory[base + 0x86],
                                   None if source is None else source.source_event_id,
                                   None if source is None else source.event_id))

    def close(key, time, reason, source=None):
        start, origin = opened.pop(key)
        gates.append(PlaybackGate(len(gates), *key, start, time,
                                  origin.event_id, origin.source_event_id,
                                  origin.loop_iteration,
                                  None if source is None else source.event_id, reason))

    def apply(event):
        nonlocal active_order
        source = event.source
        chip = source.chip_instance
        time = Fraction(source.vgmticks)
        if hasattr(event, 'payload'):
            rom[chip].update(event)
            return
        memory = ram[chip]
        offset = event.ram_offset
        channel = event.channel if event.channel is not None else (offset & 0x7f) // 8
        key = chip, channel
        old = memory[offset]
        memory[offset] = event.value
        if event.role == 'unmodeled_ram' and 'Unused RAM writes retained without audible effect' not in issues:
            issues.append('Unused RAM writes retained without audible effect')
        if event.role == 'control':
            if event.value & 1:
                if key in active:
                    close(key, time, 'disable', source)
                    active.remove(key)
                    active_order = tuple(sorted(active))
            elif old & 1:
                opened[key] = time, source
                active.add(key)
                active_order = tuple(sorted(active))
        runtime(chip, channel, event.role + '_write', time, source)

    position = 0
    for frame in range(frame_count):
        time_num = frame * time_step
        while position < len(ordered) and ordered[position].source.vgmticks * time_den <= time_num:
            apply(ordered[position])
            position += 1
        left = right = 0
        for chip, channel in active_order:
            memory = ram[chip]
            base = channel * 8
            address = ((memory[base + 0x85] << 8) | memory[base + 0x84]) << 8
            address |= fractions[chip][channel]
            control = memory[base + 0x86]
            end = (memory[base + 6] + 1) & 0xff
            if (address >> 16) == end:
                time = Fraction(time_num, time_den)
                if control & 2:
                    memory[base + 0x86] |= 1
                    fractions[chip][channel] = 0
                    runtime(chip, channel, 'natural_stop', time)
                    close((chip, channel), time, 'natural_stop')
                    active.remove((chip, channel))
                    active_order = tuple(sorted(active))
                    continue
                address = ((memory[base + 5] << 8) | memory[base + 4]) << 8
                memory[base + 0x84] = (address >> 8) & 0xff
                memory[base + 0x85] = (address >> 16) & 0xff
                fractions[chip][channel] = 0
                runtime(chip, channel, 'sample_loop', time)
            mask = ((trace.interface_raw >> 16) & 0xff) or 0x70
            bank = (control & mask & (rom[chip].mask >> shift)) << shift
            sample = rom[chip].read((address >> 8) + bank) - 128
            left += sample * (memory[base + 2] & 0x7f)
            right += sample * (memory[base + 3] & 0x7f)
            address = (address + memory[base + 7]) & 0xffffff
            memory[base + 0x84] = (address >> 8) & 0xff
            memory[base + 0x85] = (address >> 16) & 0xff
            fractions[chip][channel] = address & 0xff
        samples.extend((left, right))
    # Retain writes exactly at EOF although no following audible frame exists.
    while position < len(ordered):
        apply(ordered[position])
        position += 1
    for key in sorted(opened):
        close(key, Fraction(end_vgmticks), 'source_end')
    gates.sort(key=lambda g: (g.start_vgmticks, g.chip_instance, g.channel, g.gate_id))
    missing = sum(memory.missing_reads for memory in rom.values())
    if missing:
        issues.append(f'{missing} active ROM reads used neutral80 because source ROM bytes were unavailable')
    return PlaybackAnalysis(rate, samples, tuple(gates), tuple(events),
                            end_vgmticks, loop_start, tuple(issues), missing)
