"""Force-only PSG omissions; source Segment fields remain unchanged."""
from opna_force import dump


def dump_decisions(plan, source, outdir, stem):
    records = []
    for loss in plan.settings['forced_omissions']:
        segment = source[loss['source_ch']][loss['source_row']]
        registers = []
        if 'psg_noise' in loss['reason']:
            registers += [(6,segment.noise_period,0x1f),(7,segment.mixer,0x3f)]
        if 'psg_hardware_envelope' in loss['reason']:
            registers += [(8+segment.ch,segment.amplitude_register,0x10),
                          (11,segment.envelope_period&255,0xff),
                          (12,segment.envelope_period>>8,0xff),
                          (13,segment.envelope_shape,0x0f)]
        for register,data,bits in registers:
            records.append(dict(source_event_id=None,original_event_id=None,loop_iteration=None,
                original_vgmticks=None,address=None,vgmticks=segment.vgmticks,
                vgmticks_end=segment.vgmticks_end,chip_instance=0,port=0,register=register,
                data=data,source_ch=segment.ch,source_part='PSG'+str(segment.ch+1),
                source_segment_id=loss['source_row'],reason=loss['reason'],
                action='mute_psg_interval',record_kind='interval',affected_bits=bits,
                trigger_port=None,trigger_register=None,trigger_data=None,data_origin='segment_state'))
    report = dump(records,outdir,stem,force=True,prefix='psg_force')
    return dict(report, source_domain='PSG', omitted_intervals=len(plan.settings['forced_omissions']),
                intervals_csv=report['registers_csv'],omissions=plan.settings['forced_omissions'])
