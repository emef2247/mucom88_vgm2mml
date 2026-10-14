"""Serialize explicit projected OPM evidence for the canonical OPM pipeline.

The generated stream is not a native OPM recording of the PSG/SCC input.
Source samples and target command addresses remain separate in the mapping.
"""
import csv
from dataclasses import asdict
from pathlib import Path
import struct

from opm_mdx import projected_samples, mdx_tick
from vgm_timing import command_times


def write_target_vgm(plan, path, *, mapping_csv=None, song_loop_start=None):
    from opm_target_state import build_target_trajectory
    writes = plan.scheduled_writes()
    build_target_trajectory(writes, end_tick=plan.end_tick,
                            source_end_vgmticks=plan.source_end)
    end = projected_samples(plan.end_tick)
    clock = plan.settings['target_clock']
    if not 0 < clock < 0x40000000 or not 0 <= end <= 0xffffffff:
        raise ValueError('Projected OPM clock/duration exceeds the VGM field range')
    commands, rows, cursor = bytearray(), [], 0

    def wait(gap):
        while gap:
            count = min(gap, 65535)
            commands.extend(b'\x61' + struct.pack('<H', count))
            gap -= count

    loop_sample = (projected_samples(mdx_tick(song_loop_start))
                   if song_loop_start is not None else None)
    loop_address = None
    if loop_sample is not None and not 0 <= loop_sample < end:
        raise ValueError('Projected song loop must have a positive duration')

    def advance(sample):
        nonlocal cursor, loop_address
        if loop_sample is not None and loop_address is None and cursor <= loop_sample <= sample:
            wait(loop_sample - cursor)
            cursor = loop_sample
            loop_address = 0x40 + len(commands)
        wait(sample - cursor)
        cursor = sample

    for write in writes:
        sample = projected_samples(write.mdx_tick)
        advance(sample)
        row = asdict(write)
        row.update(state_origin='projected_opm', target_command_address=0x40 + len(commands),
                   target_vgmticks=sample)
        rows.append(row)
        commands.extend((0x54, write.register, write.data))
        cursor = sample
    advance(end)
    commands.append(0x66)
    raw = bytearray(0x40) + commands
    if len(raw) - 4 > 0xffffffff:
        raise ValueError('Projected OPM VGM exceeds the file size field range')
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x18, end)
    struct.pack_into('<I', raw, 0x30, clock)
    if loop_address is not None:
        struct.pack_into('<I', raw, 0x1c, loop_address - 0x1c)
        struct.pack_into('<I', raw, 0x20, end - loop_sample)
    ids = {e.address: event_id for event_id, e in enumerate(command_times(raw)) if e.command == 0x54}
    for row in rows:
        row['target_source_event_id'] = ids[row['target_command_address']]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    if mapping_csv is not None:
        with Path(mapping_csv).open('w', encoding='utf-8', newline='') as stream:
            fields = list(rows[0]) if rows else ('write_id', 'state_origin', 'target_source_event_id',
                                               'target_command_address', 'target_vgmticks')
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
    return path
