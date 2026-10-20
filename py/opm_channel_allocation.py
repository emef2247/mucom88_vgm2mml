"""Target-only, ordered six-slot OPM allocation; native evidence is untouched."""


class DynamicChannels:
    def __init__(self, *, force, omissions, end, loop_start=None):
        self.force,self.omissions,self.end=force,omissions,end
        self.loop_start=loop_start
        self.channel_map={}
        self.owners=[None]*6
        self.keys={ch:0 for ch in range(8)}
        self.dropped={}
        self.rows=[]
        self.gate_ids={}
        self.next_gate=0
        self.entry=None
        self.peak=0

    def snapshot(self):
        return dict(owners=list(self.owners),key_masks=dict(self.keys),
                    dropped_channels=sorted(self.dropped))

    def record(self,s,action,target=None,previous=None):
        row=dict(action=action,source_event_id=s.source_event_id,
                 original_event_id=s.original_event_id,loop_iteration=s.loop_iteration,
                 source_vgmticks=s.vgmticks,source_channel=s.ch,
                 source_gate_id=self.gate_ids.get(s.ch),target_channel=target,
                 previous_source_channel=previous,source_key_mask=s.state.key_mask)
        self.rows.append(row)
        return row

    def before(self,group,*,same_sample_release=False):
        s=group[0]
        if self.loop_start is not None and self.entry is None and s.vgmticks>=self.loop_start:
            self.entry=self.snapshot()
        if s.chip_instance or s.chip_type!='YM2151':return False
        for member in group:
            if member.ch in self.dropped:
                self.dropped[member.ch]['source_event_ids'].append(member.source_event_id)
        if s.register!=8:return False
        ch=s.ch;old=self.keys[ch];mask=s.state.key_mask
        restore=False
        if not old and mask:
            gate=self.next_gate;self.next_gate+=1;self.gate_ids[ch]=gate
            if ch in self.channel_map:
                target=self.channel_map[ch]
            else:
                free=[i for i,owner in enumerate(self.owners)
                      if owner is None or not self.keys[owner]]
                target=next((i for i in free if self.owners[i] is None),next(iter(free),None))
            if target is None:
                if not self.force:
                    raise ValueError('OPM exceeds6 simultaneous keyed FM voices; use --force to omit excess gates')
                reason=('opm_same_sample_order_capacity' if same_sample_release
                        else 'opm_simultaneous_fm_capacity')
                row=dict(source_chip='OPM',part='ABCDEFGH'[ch],source_channel=ch,
                    action='omit_fm_gate',reason=reason,source_gate_ids=[gate],
                    source_event_ids=[s.source_event_id],source_start_vgmticks=s.vgmticks,
                    source_end_vgmticks=self.end,actual=sum(bool(v) for v in self.keys.values())+1,
                    limit=6,unit='keyed FM voices',same_sample_release_pending=same_sample_release)
                self.omissions.append(row);self.dropped[ch]=row
                self.record(s,'omit_gate_start')
            else:
                previous=self.owners[target]
                if previous is not None and previous!=ch:
                    if not self.force:
                        raise ValueError('Dynamic FM slot reuse may truncate release; use --force for explicit approximation')
                    self.omissions.append(dict(source_chip='OPM',part='ABCDEFGH'[previous],
                        source_channel=previous,target_channel=target,new_source_channel=ch,
                        action='truncate_possible_release',reason='opm_dynamic_slot_reuse',
                        source_event_ids=[s.source_event_id],source_start_vgmticks=s.vgmticks,
                        source_end_vgmticks=s.vgmticks,
                        detail='Key-Off is not proven silence; previous release may be truncated by new owner'))
                    del self.channel_map[previous]
                self.owners[target]=ch;self.channel_map[ch]=target
                self.record(s,'acquire' if previous is None else 'reuse',target,previous)
                restore=True
        self.keys[ch]=mask
        self.peak=max(self.peak,sum(bool(v) for v in self.keys.values()))
        if old and not mask:
            if ch in self.dropped:
                self.dropped[ch]['source_end_vgmticks']=s.vgmticks
                self.record(s,'omit_gate_end')
                del self.dropped[ch]
            else:self.record(s,'key_off_release',self.channel_map.get(ch))
        return restore

    def finish(self):
        for ch,row in self.dropped.items():row['source_end_vgmticks']=self.end
        tail=self.snapshot()
        entry=self.entry if self.entry is not None else tail
        stable=self.loop_start is None or entry==tail
        if not stable and not self.force:
            raise ValueError('Dynamic FM ownership is not stable across song loop; use --force or --no-song-loop')
        if not stable:
            self.omissions.append(dict(source_chip='OPM',part='FM',action='omit_song_loop',
                reason='opm_dynamic_loop_ownership_not_stable',source_start_vgmticks=self.loop_start,
                source_end_vgmticks=self.end,detail='Finite shared FM/PCM traversal retained; no ownership reset or invented held attack'))
        return dict(rows=self.rows,peak_ordered_source_keyed_voices=self.peak,
            slot_count=6,policy='ordered whole gates; retain six admitted voices, omit new gate until full Key-Off',
            same_sample_policy='preserve source order; transient conflicts explicitly diagnosed',
            release_policy='retain released owner controls until reuse; reuse may truncate release',
            loop=dict(start=self.loop_start,stable=stable,entry=entry,tail=tail))
