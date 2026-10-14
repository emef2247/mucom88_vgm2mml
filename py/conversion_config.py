"""Frontend route selection; source decoders and target IR stay independent."""
from collections import Counter

from vgm_io import read_vgm_bytes, read_vgm_header
from vgm_timing import command_times


def normalization_enabled(requested, *, notation='structured', target='mdx'):
    if requested is None:
        return target == 'mdx' and notation == 'structured'
    if requested and target == 'mdx' and notation != 'structured':
        raise ValueError('MDX length normalization requires --notation structured')
    return requested


def inspect_source(source):
    """Inventory actual commands, including inaudible writes and stream setup."""
    raw = read_vgm_bytes(source)
    header = read_vgm_header(raw)
    chips, counts, first, unsupported, streams = set(), Counter(), {}, [], {}
    opll_key_on = scc_nonzero_volume = False
    psg_volumes = [0] * 3
    psg_silent = True
    # AY/SCC reset volumes and SCC enable are zero. SCC waveform RAM is
    # treated as unknown until written; a constant nonzero wave is not mute.
    scc_volumes = [0] * 5
    scc_waves = [[None] * 32 for _ in range(5)]
    scc_enabled = 0
    scc_silent = True
    previous_time = 0
    banks = []
    for event in command_times(raw):
        cmd, pos = event.command, event.address
        if event.vgmticks > previous_time and 'psg' in chips:
            psg_silent &= psg_volumes == [0, 0, 0]
        if event.vgmticks > previous_time and 'scc' in chips:
            scc_silent &= not any(scc_enabled & (1 << ch) and scc_volumes[ch]
                and any(value != 0 for value in scc_waves[ch]) for ch in range(5))
        previous_time = event.vgmticks
        counts[f'{cmd:#04x}'] += 1
        item = dict(command=f'{cmd:#04x}', address=pos, source_samples=event.vgmticks)
        chip = {0x54: 'opm', 0xa4: 'opm', 0x56: 'opna', 0x57: 'opna',
                0xa6: 'opna', 0xa7: 'opna', 0xa0: 'psg', 0xd2: 'scc',
                0x51: 'opll', 0xb7: 'pcm', 0xc0: 'segapcm'}.get(cmd)
        if chip:
            chips.add(chip)
            first.setdefault(chip, item)
            if cmd == 0xa4:
                unsupported.append(dict(item, reason='Second OPM instance is not supported'))
            elif cmd in (0xa6, 0xa7):
                unsupported.append(dict(item, reason='Second OPNA instance is not supported'))
            elif cmd == 0x51:
                reg, value = raw[pos + 1:pos + 3]
                opll_key_on |= bool((0x20 <= reg <= 0x28 and value & 0x10)
                                   or (reg == 0x0e and value & 0x20 and value & 0x1f))
            elif cmd == 0xa0:
                reg, value = raw[pos + 1:pos + 3]
                # Only explicitly muted, ordinary AY initialization is exempt.
                # Any nonzero/envelope volume or variant register stays used.
                if 8 <= reg <= 10:
                    psg_volumes[reg - 8] = value
                    psg_silent &= value == 0
                elif reg > 13:
                    psg_silent = False
            elif cmd == 0xd2:
                scc_nonzero_volume |= raw[pos + 1] == 2 and bool(raw[pos + 3] & 15)
                if raw[pos + 1] not in (0, 1, 2, 3):
                    unsupported.append(dict(item, reason='SCC test/variant/instance writes are not supported'))
                port, reg, value = raw[pos + 1:pos + 4]
                if port == 0 and reg < 128:
                    channel = reg // 32
                    scc_waves[channel][reg % 32] = value
                    if channel == 3:
                        scc_waves[4][reg % 32] = value
                elif port == 2 and reg < 5:
                    scc_volumes[reg] = value & 15
                elif port == 3 and reg == 0:
                    scc_enabled = value & 31
                elif not (port == 1 and reg < 10):
                    scc_silent = False
        elif cmd == 0x67:
            kind = raw[pos + 2]
            banks.append(dict(item, bank_type=f'{kind:#04x}'))
            if kind in (4, 0x44):
                chips.add('pcm')
                first.setdefault('pcm', item)
            elif kind == 0x81:
                chips.add('opna')
                first.setdefault('opna', item)
            elif kind == 0x80:
                chips.add('segapcm')
                first.setdefault('segapcm', item)
        elif 0x90 <= cmd <= 0x95:
            stream_id = raw[pos + 1]
            if stream_id == 255:
                # Reserved stream ID; 94/FF stops all configured supplies.
                continue
            if cmd == 0x90:
                streams[stream_id] = raw[pos + 2]
            destination = streams.get(stream_id)
            if destination == 0x17:
                # The PCM analyzer retains commands and validates their supplies.
                chips.add('pcm')
                first.setdefault('pcm', item)
            else:
                unsupported.append(dict(item, code='unsupported_stream_control',
                    reason='DAC Stream Control is not supported', stream_id=stream_id,
                    destination_chip=destination))
        elif cmd in (0x61, 0x62, 0x63, 0x64, 0x66) or 0x70 <= cmd <= 0x7f:
            continue
        else:
            unsupported.append(dict(item, reason='Unsupported source chip/stream command'))
    opm_offset = 0x30 if header['version'] >= 0x110 else 0x10
    opm_clock = int.from_bytes(raw[opm_offset:opm_offset + 4], 'little')
    opll_clock = int.from_bytes(raw[0x10:0x14], 'little') if header['version'] >= 0x110 else 0
    declarations = dict(opm=opm_clock, opna=header['opna_clock_raw'],
                        opll=opll_clock, psg=header['ay_clock_raw'],
                        scc=header['scc_clock_raw'], pcm=header['msm6258_clock_raw'],
                        segapcm=header['segapcm_clock_raw'])
    has_song_loop = bool(int.from_bytes(raw[0x1c:0x20], 'little'))
    initialization = {}
    if chips & {'opm', 'opna'} and 'psg' in chips and psg_silent and psg_volumes == [0, 0, 0]:
        initialization['psg'] = dict(command_count=counts['0xa0'],
            reason='Muted AY initialization: reset volumes remain zero; no nonzero/envelope volume writes; source commands retained')
    if 'opll' in chips and not opll_key_on:
        initialization['opll'] = dict(command_count=counts['0x51'],
            reason='Existing compatibility initialization: no melodic/rhythm Key-On; source commands retained')
    if 'scc' in chips and not declarations['scc'] and not scc_nonzero_volume:
        initialization['scc'] = dict(command_count=counts['0xd2'],
            reason='Existing compatibility initialization: SCC is undeclared and no nonzero volume is written')
    elif chips & {'opm', 'opna'} and 'scc' in chips and scc_silent and not has_song_loop:
        initialization['scc'] = dict(command_count=counts['0xd2'],
            reason='Finite silent SCC setup: reset volume/enable zero; every positive-duration enabled nonzero-volume interval has explicitly all-zero waveform RAM; source commands retained')
    # SegaPCM currently stops at native evidence; its declaration issues are
    # retained by that analyzer rather than preventing raw capture.
    for chip in chips - {'pcm', 'segapcm'} - initialization.keys():
        if not declarations[chip]:
            unsupported.append(dict(first[chip], reason=f'Used {chip.upper()} commands have no clock declaration'))
    return dict(used_chips=sorted(chips), declared_clocks=declarations,
                compatibility_initialization=initialization,
                unused_declarations=sorted(chip for chip, clock in declarations.items()
                                           if clock and chip not in chips),
                command_counts=dict(counts), first_use=first, data_blocks=banks,
                unsupported_commands=unsupported)


def select_mdx_route(usage, *, compatibility_target=None):
    if usage['unsupported_commands']:
        issue = usage['unsupported_commands'][0]
        raise ValueError(f"{issue.get('code', 'unsupported_source')}: {issue['reason']} "
                         f"({issue['command']} at {issue['address']:#x})")
    chips = set(usage['used_chips']) - usage['compatibility_initialization'].keys()
    if compatibility_target in ('opm', 'opm-additive'):
        if not chips or not chips <= {'psg', 'scc'}:
            raise ValueError('Compatibility --target opm/opm-additive requires PSG/SCC source commands')
        return 'psg-scc-to-opm'
    if 'segapcm' in chips and chips <= {'opm', 'segapcm'}:
        return 'opm-segapcm-source'
    if chips and chips <= {'opm', 'pcm'}:
        return 'native-opm-pcm'
    if chips == {'opna'}:
        return 'opna-fm-to-opm'
    if chips and chips <= {'psg', 'scc'}:
        return 'psg-scc-to-opm'
    if not chips and 'opll' in usage['used_chips']:
        raise ValueError('OPLL to MDX projection is not supported; use --target mgs for MGSDRV compatibility')
    if not chips:
        raise ValueError('Input contains no supported chip commands; clock declarations alone do not select a route')
    if chips == {'opll'}:
        raise ValueError('OPLL to MDX projection is not supported; use --target mgs for MGSDRV compatibility')
    raise ValueError('Unsupported MDX source combination: ' + ', '.join(sorted(chips)))


def normalization_samples(milliseconds):
    """Shared output policy threshold; floor original 44100 Hz source samples."""
    from decimal import Decimal, InvalidOperation
    import math
    try:
        value = Decimal(str(milliseconds))
        if not value.is_finite() or value <= 0 or not math.isfinite(float(value)):
            raise ValueError
        samples = int(value * Decimal('44.1'))
        if samples < 1:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise ValueError('Normalization milliseconds must be finite and at least one source sample (1/44.1 ms)') from None
    return samples
