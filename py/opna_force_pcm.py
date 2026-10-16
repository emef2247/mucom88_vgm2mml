"""Explicit whole-PCM omission at the OPNA target-projection boundary.

The returned empty PCM view is projection-only, never a revised native analysis.
Callers must dump the original analysis before preparing this view. Source blocks,
controls, clock, timing and identity survive in both views; omission records name
all affected observed controls, not merely the write that exposed a limitation.
"""
from dataclasses import replace

from opna_adpcm import AdpcmAnalysis


_HARDWARE_CODES = frozenset({
    'unsupported_adpcm_prescaler',
    'unsupported_cpu_record_or_ram_mode',
    'unsupported_control_bits_or_memory_mode',
    'unmodeled_data_or_prescale_control',
    'repeat_requires_continuous_decoder_timing',
    'unsupported_address_wrap_or_limit',
    'unsupported_record_mode',
    'unsupported_record_memory_mutation',
    'unsupported_record_memory_history',
    'memory_change_during_playback',
    'prescaler_change',
    'unsupported_stream_or_ram_transfer',
})
_HARMLESS_CODES = frozenset({
    'controls_during_decoder_epoch',
    'ram1_seeded_from_vgm_rom_block_assumption',
    'ram8_seeded_from_vgm_rom_block_assumption',
})


def prepare(native: AdpcmAnalysis, *, force=False):
    """Return (projection-only input, source-linked whole-part omissions).

    Only explicit hardware limitations authorize omission. Missing bytes,
    malformed source evidence, invalid clocks, resource limits and unknown
    diagnostics remain on the ordinary projection/failure path. Supported live
    controls and ordinary-clock Delta-N zero never trigger omission themselves.
    """
    if not force:
        return native, []
    codes = {issue.code for issue in native.issues}
    codes.update(code for segment in native.segments for code in segment.issues)
    hardware = codes & _HARDWARE_CODES
    harmless = _HARMLESS_CODES
    if (native.clock_hz > 0 and 0 < native.clock_raw < 0x40000000
            and native.clock_hz == native.clock_raw):
        harmless = harmless | {'zero_delta_n_or_clock'}
    if not hardware or codes - _HARDWARE_CODES - harmless:
        return native, []
    # Check the clock even when hand-authored analysis lacks its native issue.
    if not (native.clock_hz > 0 and 0 < native.clock_raw < 0x40000000
            and native.clock_hz == native.clock_raw):
        return native, []
    reason = ';'.join(sorted(hardware))
    records = []
    for control in native.controls:
        records.append({
            'source_event_id': control.source_event_id,
            'original_event_id': control.original_source_event_id,
            'loop_iteration': control.loop_iteration,
            'original_vgmticks': control.original_vgmticks,
            'address': control.address,
            'vgmticks': control.vgmticks,
            'vgmticks_end': control.vgmticks,
            'chip_instance': control.chip_instance,
            'port': control.port,
            'register': control.register,
            'data': control.data,
            'source_ch': None,
            'reason': reason,
            'action': 'omit_pcm_part',
            'record_kind': 'write',
            'affected_bits': None,
            'trigger_port': control.port,
            'trigger_register': control.register,
            'trigger_data': control.data,
        })
    if not records:
        # No register write is invented for an issue raised by a stream command.
        records.append(dict.fromkeys((
            'source_event_id', 'original_event_id', 'loop_iteration',
            'original_vgmticks', 'address', 'vgmticks', 'vgmticks_end',
            'chip_instance', 'port', 'register', 'data', 'source_ch',
            'affected_bits', 'trigger_port', 'trigger_register', 'trigger_data'), None))
        records[-1].update(reason=reason, action='omit_pcm_part', record_kind='part')
    return replace(native, segments=(), samples=(), issues=()), records
