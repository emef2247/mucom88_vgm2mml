"""Native-chip control comparison for MUCOMSX exported public VGM.

Reports control/pitch evidence, not acoustic or oscillator-phase equivalence.
OPNA projections are reported separately from same-chip comparisons.
"""
from bisect import bisect_right
from collections import defaultdict
import hashlib
import math
from vgm_io import read_vgm_bytes,read_vgm_header
from vgm_timing import command_times


def trajectories(path):
    raw=read_vgm_bytes(path);header=read_vgm_header(raw)
    header['opll_clock_raw']=int.from_bytes(raw[0x10:0x14],'little')
    regs=[0]*16;opll=[0]*64;freq=[0]*10;vol=[0]*5;waves=[bytearray(32) for _ in range(5)];enable=0
    states={};end=0
    events=defaultdict(list)
    for event in command_times(raw):events[event.vgmticks].append(event);end=max(end,event.vgmticks+event.wait_samples)
    for time,rows in events.items():
        for event in rows:
            i=event.address;c=event.command
            if c==0xa0:regs[raw[i+1]&15]=raw[i+2]
            elif c==0x51:opll[raw[i+1]&63]=raw[i+2]
            elif c==0xd2:
                port,address,value=raw[i+1:i+4]
                if port==0 and address<128:
                    ch=address//32;waves[ch][address%32]=value
                    if ch==3:waves[4][address%32]=value
                elif port==4 and address<160:waves[address//32][address%32]=value
                elif port==1 and address<10:freq[address]=value
                elif port==2 and address<5:vol[address]=value&15
                elif port==3:enable=value
        for ch in range(3):
            period=regs[ch*2]+256*(regs[ch*2+1]&15);volume=regs[ch+8]&15;hardware=bool(regs[ch+8]&16)
            mode=(not bool(regs[7]&(1<<ch)))+2*(not bool(regs[7]&(8<<ch)))
            value=dict(period=period,volume=volume,hardware_envelope=hardware,mode=int(mode),noise=regs[6]&31,
                       envelope_period=regs[11]+256*regs[12],shape=regs[13]&15,
                       active=bool(mode and (volume or hardware)))
            states.setdefault(('psg',ch),[]).append((time,value))
        for ch in range(5):
            period=freq[ch*2]+256*(freq[ch*2+1]&15)
            value=dict(period=period,volume=vol[ch],wave=bytes(waves[ch]).hex(),
                       active=bool(enable&(1<<ch) and vol[ch] and period>8))
            states.setdefault(('scc',ch),[]).append((time,value))
        for ch in range(9):
            fnum=opll[0x10+ch]+256*(opll[0x20+ch]&1);block=(opll[0x20+ch]>>1)&7
            key=bool(opll[0x20+ch]&16);att=opll[0x30+ch]&15;inst=opll[0x30+ch]>>4
            value=dict(fnum=fnum,block=block,key=key,sustain=bool(opll[0x20+ch]&32),attenuation=att,instrument=inst,
                       patch=bytes(opll[:8]).hex() if not inst else '',
                       active=bool(key and att<15 and fnum and not(ch>=6 and opll[14]&32)))
            states.setdefault(('opll',ch),[]).append((time,value))
    compressed={}
    for key,rows in states.items():
        kept=[]
        for t,value in rows:
            if not kept or kept[-1][1]!=value:kept.append((t,value))
        compressed[key]=kept
    return dict(header=header,states=compressed,end=end,sha256=hashlib.sha256(raw).hexdigest())


def compare(source,target,projection):
    a,b=trajectories(source),trajectories(target)
    timing=projection['timing'];maprows=timing['source_boundaries'];times=[r['source_sample'] for r in maprows]
    # MUCOMSX's writer truncates each Timer-B wait to a whole source sample.
    tick_samples=math.floor(timing['samples_per_tick']);rows=[];totals=defaultdict(int)
    route=projection.get('routing',[])
    channels={(r['source_chip'],r['source_channel']) for r in route if r['target_chip'] in ('psg','scc','opll')}
    target_index={key:[t for t,_ in value] for key,value in b['states'].items()}
    def clock(t):
        i=bisect_right(times,t)-1
        if i<0:return 0
        if times[i]==t:return maprows[i]['target_tick']*tick_samples
        return round(t/timing['samples_per_tick'])*tick_samples
    def frequency(chip,state,header):
        if chip=='opll':return (header.get('opll_clock_raw',0)&0x3fffffff)*state['fnum']*2**state['block']/(72*2**19)
        rate=header.get('ay_clock_raw' if chip=='psg' else 'scc_clock_raw',0)&0x3fffffff
        if chip=='psg' and header.get('ay_type')==0x10 and header.get('ay_flags',0)&16:rate/=2
        return rate/(16*max(1,state['period']+(chip=='scc')))
    for key in sorted(channels):
        chip,ch=key;source_rows=a['states'].get(key,[]);target_rows=b['states'].get(key,[])
        for i,(start,state) in enumerate(source_rows):
            stop=source_rows[i+1][0] if i+1<len(source_rows) else a['end']
            left,right=clock(start),clock(stop)
            if stop<=start or right<=left:continue
            point=(left+right)//2;j=bisect_right(target_index.get(key,[]),point)-1
            actual=target_rows[j][1] if j>=0 else dict(active=False)
            differences=[]
            if state['active']!=actual['active']:differences.append('activity')
            cents=None
            if state['active'] and actual['active']:
                src=frequency(chip,state,a['header']);dst=frequency(chip,actual,b['header'])
                if src and dst:cents=1200*math.log2(dst/src)
                if cents is not None and abs(cents)>15:differences.append('pitch_over_15_cents')
                fields=(('mode','hardware_envelope')+ (('noise',) if state['mode']&2 else ())+
                    (('envelope_period','shape') if state['hardware_envelope'] else ('volume',))) if chip=='psg' else ('volume','wave') if chip=='scc' else ('attenuation','instrument','patch','sustain')
                for field in fields:
                    if state.get(field)!=actual.get(field):differences.append(field)
            for field in differences:totals[field]+=1
            rows.append(dict(chip=chip,channel=ch,source_start=start,source_end=stop,
                target_start=left,target_end=right,pitch_error_cents=cents,
                differences=differences,source_state=state,target_state=actual))
    return dict(source_sha256=a['sha256'],target_sha256=b['sha256'],
        source_end_samples=a['end'],export_end_samples=b['end'],checked_intervals=len(rows),
        mismatched_intervals=sum(bool(r['differences']) for r in rows),difference_counts=dict(totals),
        opna_projection_exclusions=[r for r in route if r['target_chip'].startswith('opna')],
        interpretation='Control-state evidence at mapped positive interval midpoints; not audio/phase equivalence',intervals=rows)
