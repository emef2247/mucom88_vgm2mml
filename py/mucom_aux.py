"""Native MUCOM88 projection for OPNA SSG, rhythm and ADPCM-B.

Items retain source sample boundaries. Their prefixes contain musical controls
and a note, but no duration; musical gate construction belongs to the caller.
The native bank contains original ADPCM-B bytes, without an MSM6258 projection.
"""
import struct
from dataclasses import replace
from bisect import bisect_left, bisect_right
from fractions import Fraction

TARGET_CLOCK_HZ = 7987200
NOTES = ('c', 'c+', 'd', 'd+', 'e', 'f', 'f+', 'g', 'g+', 'a', 'a+', 'b')
SSG_PERIOD = (0x0ee8, 0x0e12, 0x0d48, 0x0c89, 0x0bd5, 0x0b2b,
              0x0a8a, 0x09f3, 0x0964, 0x08dd, 0x085e, 0x07e6)
PCM_N = tuple(n + 200 for n in (0x49ba, 0x4e1c, 0x52c1, 0x57ad, 0x5ce4, 0x626a,
                                0x6844, 0x6e77, 0x7509, 0x7bfe, 0x835e, 0x8b2d))


class ProjectionError(ValueError):
    """An observed auxiliary-engine behavior lacks a supported native mapping."""


def _clock(value):
    if value <= 0:
        raise ProjectionError('OPNA source clock must be positive')
    return value


def _period(value, source_clock_hz, maximum, label):
    projected = round(value * TARGET_CLOCK_HZ / _clock(source_clock_hz))
    if not 0 <= projected <= maximum:
        raise ProjectionError(f'{label} exceeds native register range after clock projection')
    return projected


def project_ssg(analysis, events, raw, source_clock_hz=TARGET_CLOCK_HZ, *, global_controls=None,
                source_command=0x56, effective_source_clock_hz=None, force=False, omissions=None,
                musical_notes=None):
    """Return DEF control intervals; retain hardware-envelope trigger ordering."""
    _clock(source_clock_hz)
    if effective_source_clock_hz is not None:
        # Only a projection ratio; preserve the original AY source clock.
        source_clock_hz=4*_clock(effective_source_clock_hz)
    shapes = {}
    for event in events:
        if event.command == source_command and raw[event.address + 1] == 13:
            shapes.setdefault(event.vgmticks, []).append(raw[event.address + 2] & 15)
    hardware = sorted((s.vgmticks, s.vgmticks_end, ch)
                      for ch, channel in analysis.segments.items() for s in channel
                      if s.scale != 'r' and s.envelope_enabled and s.vgmticks_end > s.vgmticks)
    conflicts=[];active=[]
    for start,stop,ch in hardware:
        active=[row for row in active if row[1]>start]
        for other_start,other_stop,other in active:
            if other!=ch:conflicts.append((start,min(stop,other_stop)))
        active.append((start,stop,ch))
    merged=[]
    for start,stop in sorted(conflicts):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(stop,merged[-1][1]))
        else:merged.append((start,stop))
    conflicts=merged
    conflict_starts=[a for a,b in conflicts];conflict_ends=[b for a,b in conflicts]
    if conflicts and not force:
        raise ProjectionError('Overlapping SSG hardware-envelope channels share a global trigger and are unsupported')
    # The installed legacy S command addresses only shape8..15 and retriggers
    # twice at a note attack. Global corrections execute on G after DEF; generic
    # y commands on SSG parts compile but are not consumed by the legacy driver.
    corrections = global_controls if global_controls is not None else []
    for event in events:
        if event.command == source_command and raw[event.address + 1] == 13:
            corrections.append((event.vgmticks, 13, raw[event.address + 2] & 15,
                                event.source_event_id))
    # Native FRQ_DF clears beforeCode to0. A tied o1c also has note code0,
    # so SSSUBB incorrectly skips its pitch update after D. Use another legal
    # note+detune spelling, preserving the exact projected register period.
    candidates = [(o, n, p >> (o - 1)) for o in range(1, 9)
                  for n, p in enumerate(SSG_PERIOD) if (o,n)!=(1,0)]
    tracks = {}
    for ch, segments in analysis.segments.items():
        gate, previous_end, previous_hardware = 0, None, False
        previous_software_note = None
        note_membership = {sid: index for index, note in enumerate((musical_notes or {}).get(ch, ()))
                           if not note.rest for sid in note.segment_indices}
        projected_segments=[]
        for source_index, source_segment in enumerate(segments):
            boundaries={source_segment.vgmticks,source_segment.vgmticks_end}
            if force and source_segment.envelope_enabled:
                left=bisect_right(conflict_ends,source_segment.vgmticks)
                right=bisect_left(conflict_starts,source_segment.vgmticks_end)
                boundaries.update(t for span in conflicts[left:right] for t in span
                    if source_segment.vgmticks<t<source_segment.vgmticks_end)
            points=sorted(boundaries)
            projected_segments.extend((source_index, replace(source_segment,vgmticks=a,vgmticks_end=b))
                for a,b in zip(points,points[1:]))
        for source_index, segment in projected_segments:
            s = segment
            if s.scale == 'r' or s.vgmticks_end <= s.vgmticks:
                continue
            reason=None
            if getattr(analysis,'source_kind','opna')=='opna' and s.prescale != 6:
                reason='SSG prescale changes are unsupported'
            conflict=bisect_right(conflict_ends,s.vgmticks)
            if s.envelope_enabled and conflict<len(conflicts) and conflict_starts[conflict]<s.vgmticks_end:
                reason='Overlapping SSG hardware-envelope channels are unsupported'
            try:
                if reason:raise ProjectionError(reason)
                period = _period(s.tone_period, source_clock_hz, 4095, 'SSG tone period') if s.mode&1 else 1
                noise = _period(s.noise_period, source_clock_hz, 31, 'SSG noise period') if s.mode&2 else 0
                envelope = _period(s.envelope_period, source_clock_hz, 65535, 'SSG envelope period') if s.envelope_enabled else 0
            except ProjectionError as error:
                if not force:raise
                if omissions is not None:omissions.append(dict(part='DEF'[ch],
                    source_chip='PSG' if source_command==0xa0 else 'OPNA',
                    action='mute_ssg_interval',reason=str(error),
                    source_start_vgmticks=s.vgmticks,source_end_vgmticks=s.vgmticks_end,
                    source_event_ids=[] if s.source_event_id is None else [s.source_event_id],
                    register=s.register if hasattr(s,'register') else None))
                previous_end=None;previous_hardware=False;previous_software_note=None
                continue
            octave, note, base = min(candidates, key=lambda item: abs(item[2] - period))
            controls = f'P{s.mode} '
            if s.mode&2:controls+=f'w{noise} '
            if s.envelope_enabled:
                if global_controls is None:
                    raise ProjectionError('Native SSG hardware envelope requires a global correction lane')
                triggers = shapes.get(s.vgmticks, [])
                attack = previous_end != s.vgmticks or not previous_hardware
                if attack or triggers:
                    gate += 1
                if attack:
                    controls += f'S{max(8, s.envelope_shape) - 8} '
                    if not triggers:
                        corrections.append((s.vgmticks, 13, s.envelope_shape,
                                            s.source_event_id))
                elif triggers:
                    # Explicit source retriggers belong to the global action lane;
                    # a tied SSG pitch/level slice must not produce a new attack.
                    pass
                controls += f'm{envelope} '
            else:
                software_note = note_membership.get(source_index)
                if musical_notes is not None and software_note is None:
                    raise ProjectionError('Audible PSG interval lacks shared musical-note membership')
                new_note = musical_notes is not None and software_note != previous_software_note
                if previous_end!=s.vgmticks or previous_hardware or new_note:
                    if previous_hardware or new_note:gate+=1
                    # E reinitialization during a tied software-envelope note
                    # changes its live envelope phase and can mute held controls.
                    controls += 'E255,255,255,255,0,255 '
                controls += f'v{s.volume} '
            # Native SSG D subtracts from the first-octave period before its
            # octave shift; it is not a direct current-octave period offset.
            detune=(base-period)<<(octave-1)
            if not -32768<=detune<=32767:
                raise ProjectionError('SSG detune exceeds native signed range')
            prefix = controls + f'o{octave} D{detune} {NOTES[note]}'
            tracks.setdefault('DEF'[ch], []).append((s.vgmticks, s.vgmticks_end,
                                                    prefix, ('ssg', ch, gate)))
            previous_end = s.vgmticks_end
            previous_hardware = bool(s.envelope_enabled)
            previous_software_note = None if s.envelope_enabled else note_membership.get(source_index)
    return tracks


def project_rhythm(events, raw, end, *, extra_controls=()):
    """Project percussion actions on G without automatic nonzero-mask dumps.

    The native rhythm driver dumps its selected instrument at every note/rest
    boundary. @0 makes those housekeeping writes inert. Source hit masks,
    selective dumps and controls remain ordered beside the musical timing.
    """
    writes = []
    for event in events:
        if event.command != 0x56:
            continue
        register, data = raw[event.address + 1:event.address + 3]
        if register == 0x10 or register == 0x11 or 0x18 <= register <= 0x1d:
            writes.append((event.vgmticks, register, data, event.source_event_id))
    if not extra_controls and not any(register == 0x10 and data & 63 and not data & 128
               for _, register, data, _ in writes):
        return []
    writes.extend(extra_controls)
    writes.sort(key=lambda row: (row[0], -1 if row[3] is None else row[3]))
    groups = []
    for tick, register, data, event_id in writes:
        if groups and groups[-1][0] == tick:
            groups[-1][1].append((register, data))
        else:
            groups.append((tick, [(register, data)]))
    result = []
    for index, (start, controls) in enumerate(groups):
        stop = groups[index + 1][0] if index + 1 < len(groups) else end
        prefix = '@0 ' + ' '.join(f'y{register},{data}' for register, data in controls)
        result.append((start, stop, prefix + ' r', None))
    return result


def pack_pcm(analysis):
    """Pack source ADPCM-B bytes into the native inclusive-end MUCOM bank."""
    used = sorted({s.sample_id for s in analysis.segments if s.sample_id is not None})
    if len(used) > 32:
        raise ProjectionError('PCM bank exceeds 32 MUCOM slots')
    mapping = {sample_id: slot + 1 for slot, sample_id in enumerate(used)}
    samples = {sample.sample_id: sample for sample in analysis.samples}
    header, payload, bindings = bytearray(1024), bytearray(), []
    for sample_id in used:
        if sample_id not in samples:
            raise ProjectionError('PCM playback lacks an encoded source sample')
        sample = samples[sample_id]
        encoded = sample.encoded_bytes
        payload.extend(b'\0' * (-len(payload) % 4))
        start = len(payload) // 4
        if len(encoded) < 4 or len(encoded) % 4:
            raise ProjectionError('Native PCM playback requires complete 4-byte address units')
        if len(encoded) - 4 > 65535 or start + len(encoded) // 4 - 1 > 65535:
            raise ProjectionError('PCM bank address or sample length exceeds MUCOM header')
        slot = mapping[sample_id]
        offset = (slot - 1) * 32
        name = f'OPNA_{sample_id}'.encode('ascii')[:16]
        header[offset:offset + len(name)] = name
        header[offset + 26] = 255
        struct.pack_into('<HH', header, offset + 28, start, len(encoded) - 4)
        payload.extend(encoded)
        bindings.append(dict(sample_id=sample_id, slot=slot, start_units=start,
                             encoded_length=len(encoded), source_sha256=sample.sha256))
    payload.extend(b'\0' * (-len(payload) % 4))
    return bytes(header + payload), mapping, bindings


def project_pcm(analysis, source_clock_hz=TARGET_CLOCK_HZ, *, raw=None, diagnostics=None, projection=None):
    """Return K intervals, native encoded bank and inspectable sample bindings."""
    _clock(source_clock_hz)
    # Inactive sample supplies and initialization diagnostics are source evidence,
    # not an active decoder epoch requiring a target PCM note.
    if not analysis.segments:
        started=any(control.register==0 and control.data&128 for control in analysis.controls)
        if not started:
            if diagnostics is not None and analysis.issues:
                diagnostics.append('PCM: no observed playback; inactive data/initialization issues retained in source IR')
            return [],b'',[]
    volume_only_header=False
    if raw is not None:
        from vgm_io import read_vgm_header
        header=read_vgm_header(raw)
        relative=header['extra_header_offset']
        if relative:
            extra=0xbc+relative
            if extra+8<=header['data_start']:
                size=int.from_bytes(raw[extra:extra+4],'little')
                volume_only_header=(size>=8 and extra+size<=header['data_start']
                    and int.from_bytes(raw[extra+4:extra+8],'little')==0
                    and analysis.clock_raw==analysis.clock_hz and analysis.clock_hz>0)
    resolved={'unsupported_clock_or_chip_configuration'} if volume_only_header else set()
    if volume_only_header and diagnostics is not None:
        diagnostics.append('PCM: extra header has no additional chip clocks; ordinary clock retained. External mixer volume metadata is not a decoder configuration')
    groups={}
    for segment in analysis.segments:groups.setdefault(segment.playback_id,[]).append(segment)
    if any(sum(s.end_vgmticks>s.start_vgmticks for s in group)>1 for group in groups.values()):
        return _project_pcm_controls(analysis,resolved,diagnostics,projection)
    projected=[];boundary_bundles=set()
    for identity,group in groups.items():
        positive=[s for s in group if s.end_vgmticks>s.start_vgmticks]
        if len(positive)==1:
            held=positive[0]
            boundary_only=all(s is held or (s.start_vgmticks==s.end_vgmticks and
                s.start_vgmticks in (held.start_vgmticks,held.end_vgmticks)) for s in group)
            same_sample=all(s.sample_id==held.sample_id for s in group)
            if boundary_only and same_sample:
                projected.append(held);boundary_bundles.add(identity)
                continue
        projected.extend(group)
    if boundary_bundles and any(issue.code=='controls_during_decoder_epoch' for issue in analysis.issues):
        from opna_adpcm_playback import build_epochs
        try:build_epochs(analysis,resolved_codes=resolved)
        except ValueError as error:raise ProjectionError(str(error)) from error
    if groups and len(boundary_bundles)==len(groups):
        resolved.add('controls_during_decoder_epoch')
        if diagnostics is not None and any(s.continuation_index for s in analysis.segments):
            diagnostics.append(f'PCM: {len(groups)} decoder epochs have only zero-time boundary edits; positive playback controls projected, original continuation evidence retained')
    blocking = [issue.code for issue in analysis.issues
                if issue.code not in resolved and not issue.code.endswith('_seeded_from_vgm_rom_block_assumption')]
    if blocking:
        raise ProjectionError('Unresolved PCM source controls: ' + ', '.join(sorted(set(blocking))))
    bank, slots, bindings = pack_pcm(analysis)
    candidates = [(o, n, base >> (o - 1)) for o in range(1, 9)
                  for n, base in enumerate(PCM_N)]
    result = []
    for segment in projected:
        s = segment
        remaining=tuple(code for code in s.issues if code not in resolved)
        if (not s.independently_playable and remaining) or (s.continuation_index and s.playback_id not in boundary_bundles) or s.repeat or remaining:
            raise ProjectionError('PCM continuation/repeat or unresolved source playback is unsupported')
        if s.sample_id not in slots:
            raise ProjectionError('PCM playback lacks an encoded sample binding')
        if s.end_vgmticks <= s.start_vgmticks:
            continue
        # Native rate is clock*delta-N/(144*65536). Use source IR's rational
        # nominal rate so source prescaler evidence is not lost in projection.
        delta = round(s.rate_num * 144 * 65536 / (s.rate_den * TARGET_CLOCK_HZ))
        if not 1 <= delta <= 65535:
            raise ProjectionError('PCM rate exceeds native delta-N range')
        octave, note, base = min(candidates, key=lambda item: abs(item[2] - delta))
        detune = (delta << (octave - 1)) - PCM_N[note]
        if not -32768 <= detune <= 32767:
            raise ProjectionError('PCM rate detune exceeds native signed range')
        if s.level >= 250:
            raise ProjectionError('Native driver mutes PCM levels 250..255')
        prefix = f'@{slots[s.sample_id]} vm0 V-4 v{s.level} p{s.pan} o{octave} D{detune} {NOTES[note]}'
        result.append((s.start_vgmticks, s.end_vgmticks, prefix, None))
    return result, bank, bindings


def _pcm_spelling(segment):
    delta=round(Fraction(segment.rate_num*144*65536,segment.rate_den*TARGET_CLOCK_HZ))
    if not 1<=delta<=65535:
        raise ProjectionError('PCM initial rate exceeds native nonzero delta-N range')
    candidates=[(o,n,base>>(o-1)) for o in range(1,9) for n,base in enumerate(PCM_N)]
    octave,note,_=min(candidates,key=lambda item:abs(item[2]-delta))
    detune=(delta<<(octave-1))-PCM_N[note]
    if not -32768<=detune<=32767:
        raise ProjectionError('PCM initial rate detune exceeds native signed range')
    return octave,note,detune


def _project_pcm_controls(analysis,resolved,diagnostics,projection):
    from opna_adpcm_playback import build_epochs,describe
    try:epochs=build_epochs(analysis,resolved_codes=resolved)
    except ValueError as error:raise ProjectionError(str(error)) from error
    if projection is not None:
        projection.update(compiler_requirements=['multi_port_y'],source_epochs=describe(epochs),
                          source_ir_unchanged=True)
    bank,slots,bindings=pack_pcm(analysis)
    result=[];controls=[]
    for epoch in epochs:
        positive=[(index,s) for index,s in enumerate(epoch.segments) if s.end_vgmticks>s.start_vgmticks]
        if not positive:continue
        first_index,first=positive[0]
        octave,note,detune=_pcm_spelling(first)
        if first.level>=250:
            raise ProjectionError('Native driver mutes initial PCM levels 250..255')
        # Retain one attack for this decoder epoch. Every later note uses this
        # same code, with no D/@ command that could invalidate the driver's tie.
        identity=('pcm',epoch.playback_id)
        prefix=f'@{slots[epoch.sample_id]} vm0 V-4 v{first.level} p{first.pan} o{octave} D{detune}'
        result.append((first.start_vgmticks,first.end_vgmticks,prefix+' '+NOTES[note],identity))
        previous_index=first_index
        for index,segment in positive[1:]:
            words=[]
            for a,b in zip(epoch.segments[previous_index:index],epoch.segments[previous_index+1:index+1]):
                for register,(old,new) in enumerate(zip(a.registers,b.registers)):
                    if old==new:continue
                    if register==1:
                        writes=[(1,new&192)] # native bank uses four-byte address units
                    elif register==11:writes=[(11,new)]
                    elif register in (9,10):
                        delta=round(Fraction(b.rate_num*144*65536,b.rate_den*TARGET_CLOCK_HZ))
                        if not 0<=delta<=65535:raise ProjectionError('PCM live rate exceeds native delta-N range')
                        if analysis.clock_hz==TARGET_CLOCK_HZ:writes=[(register,new)]
                        else:writes=[(9,delta&255),(10,delta>>8)]
                    else:raise AssertionError('Uncertified PCM continuation reached MUC projection')
                    for reg,value in writes:
                        command=f'y0,1,{reg},{value}'
                        words.append(command)
                        controls.append(dict(playback_id=epoch.playback_id,source_segment_id=b.segment_id,
                            source_sample=b.start_vgmticks,source_register=register,register=reg,
                            value=value,command=command,source_event_ids=list(b.source_event_ids)))
            result.append((segment.start_vgmticks,segment.end_vgmticks,
                           (' '.join(words)+' ' if words else '')+NOTES[note],identity))
            previous_index=index
    if projection is not None:
        projection.update(compiler_requirements=['multi_port_y'],source_epochs=describe(epochs),
            target_controls=controls,source_ir_unchanged=True,
            continuity='one native PCM attack per epoch; identical-code held notes and explicit port1 controls',
            natural_end_policy='source nominal end retained as estimate; live epoch remains scheduled through observed end')
    if diagnostics is not None:
        diagnostics.append('PCM: live level/pan/Delta-N controls use mucomDotNET multi-port y; continuation slices share one decoder epoch')
    return result,bank,bindings


def pcm_timing_audit(projection,clock,timing,*,omitted=()):
    """Compare nominal source positions with accepted target-time consumption."""
    period=Fraction((256-timing['timer_b'])*2304*44100,TARGET_CLOCK_HZ)
    target=[];omitted_ids={row['playback_id'] for row in omitted}
    for epoch in projection.get('source_epochs',()):
        if epoch['playback_id'] in omitted_ids:
            target.append(dict(playback_id=epoch['playback_id'],status='omitted_by_normalization'))
            continue
        position=Fraction(0);natural=None;size=epoch['sample_nibbles']
        for span in epoch['spans']:
            a,b=span['source_start'],span['source_end']
            rate=Fraction(span['rate']['numerator'],span['rate']['denominator'])
            delta=round(rate*144*65536/TARGET_CLOCK_HZ)
            target_rate=Fraction(TARGET_CLOCK_HZ*delta,144*65536)
            duration=(clock[b]-clock[a])*period
            if natural is None and target_rate>0 and position+duration*target_rate/44100>=size:
                natural=(clock[a]-clock[epoch['source_start']])*period+(size-position)*44100/target_rate
            position=min(Fraction(size),position+duration*target_rate/44100)
        source_final=epoch['spans'][-1]['position_end']
        source_position=Fraction(source_final['numerator'],source_final['denominator'])
        source_natural=epoch['nominal_end']
        difference=None
        if natural is not None and source_natural is not None:
            source_elapsed=Fraction(source_natural['numerator'],source_natural['denominator'])-epoch['source_start']
            difference=float(natural-source_elapsed)
        target.append(dict(playback_id=epoch['playback_id'],target_end_position_nibbles=float(position),
            source_end_position_nibbles=float(source_position),position_difference_nibbles=float(position-source_position),
            nominal_end_duration_difference_samples=difference,
            gate_ends_before_target_nominal_exhaustion=position<size,
            scope='nominal positions only; neither decoder phase nor waveform equivalence is certified'))
    projection['target_position_audit']=target
