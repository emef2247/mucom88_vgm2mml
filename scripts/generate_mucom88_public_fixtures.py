"""Author public musical MUCOM88 inputs and compile them with MUCOMVGM.

No upstream voices, music or sampled instrument recordings are used.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/public/opna/mucom88'
TOOL = ROOT / '.tools/mucomvgm-0.0.5/mucomvgm/mucomvgm.exe'
sys.path.insert(0, str(ROOT / 'py'))
from opna_adpcm import analyze as analyze_pcm
from opna_ssg import build_segments
from vgm_reader import parse_vgm
from vgm_timing import command_times

VOICE = """@1
0,4
31,8,0,8,4,34,0,1,0
31,6,0,8,3,0,0,1,0
31,8,0,8,4,38,0,2,0
31,6,0,8,3,0,0,1,0
@2
2,4
31,7,0,8,3,38,0,1,0
31,7,0,8,3,0,0,2,0
31,7,0,8,3,46,0,3,0
31,7,0,8,3,0,0,4,0
"""
SSG = 'E255,255,255,255,0,255 P1'
# Companions in combined cases are finite and end before the single loop.
PATTERNS = {
    'fm_notes': ('FM attacks, rests and tie', ['fm'],
        'A T120 @1 v12 o4 l4 q4 r4 L c d e8& e8 r4 g2 r2'),
    'fm_voice_pan': ('FM voice, pan and level changes', ['fm'],
        'A T120 @1 v12 o4 l4 q4 r4 L @1 v12 p1 c p2 e @2 p3 v9 g v12 c r2'),
    'fm_six_channels': ('Six FM channels; finite companions precede loop', ['fm'],
        'A T120 @1 v12 o4 q4 r1 L c4 e4 g4 r4\n' +
        '\n'.join(f'{ch} @1 v8 o{3+i%2} q4 c4 r2.' for i,ch in enumerate('BCHIJ'))),
    'ssg_tone': ('SSG tone pitch, levels, rest and tie', ['ssg'],
        f'D T120 {SSG} v10 o5 l4 q4 r4 L v10 c d v7 e8& e8 v10 r4 g2 r2'),
    'ssg_noise': ('SSG noise and tone/noise mixer changes', ['ssg','noise'],
        'D T120 E255,255,255,255,0,255 v9 o5 q4 r4 L P2 w4 c4 r4 w16 c4 r4 P3 c4 P1 e4 r2'),
    'ssg_hwenv': ('SSG hardware envelope shape and period', ['ssg','hardware_envelope'],
        'D T120 P1 v10 o5 q4 r4 L s9 m1024 c4 r4 s10 m2048 e4 r4 s13 m512 g4 r4'),
    'rhythm_six': ('Six built-in rhythm triggers and mask combination', ['rhythm'],
        'G T120 v40,20,18,16,14,12,10 r4 L @1 c8 r8 @2 c8 r8 @4 c8 r8 @8 c8 r8 @16 c8 r8 @32 c8 r8 @3 c8 r8'),
    'pcm_samples': ('Four synthetic ADPCM-B samples', ['pcm'],
        'K T120 o1 v200 p3 q4 r4 L @1 c8 r8 @2 c8 r8 @3 c8 r8 @4 c8 r8 r2'),
    'pcm_rate_level': ('ADPCM-B pitch, level and pan between notes', ['pcm'],
        'K T120 o1 @1 v200 p3 q4 r4 L v200 p3 c8 r8 d8 r8 v120 p1 e8 r8 v220 p2 g8 r8 p3 r2'),
    'mixed_intro': ('FM, all SSG channels, rhythm and PCM prelude; FM loop', ['fm','ssg','rhythm','pcm'],
        'A T120 @1 v10 o4 q4 c4 r2. L @2 c4 e4 g4 r4\n'
        f'D {SSG} v8 o5 q4 c4 r4 v0 r2\n'
        f'E {SSG} v6 o4 q4 e4 r4 v0 r2\n'
        f'F {SSG} v6 o4 q4 g4 r4 v0 r2\n'
        'G v36,18,16,14,12,10,8 @3 c8 r8 y16,$BF r2.\n'
        'K @2 o1 v180 p3 q4 c8 r8 v0 r2.'),
}


# At T120 a quarter note is exactly 22050 VGM samples.
# Independent authored score lengths: prelude quarter beats, loop quarter beats.
BEATS = dict(fm_notes=(1,8), fm_voice_pan=(1,6), fm_six_channels=(4,4),
    ssg_tone=(1,8), ssg_noise=(1,8), ssg_hwenv=(1,6), rhythm_six=(1,7),
    pcm_samples=(1,6), pcm_rate_level=(1,6), mixed_intro=(4,4))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def advance(predictor, step, nibble):
    delta = ((2 * (nibble & 7) + 1) * step) // 8
    predictor = max(-32768, min(32767, predictor + (-delta if nibble & 8 else delta)))
    step = max(127, min(24576, step * (57,57,57,57,77,102,128,153)[nibble & 7] // 64))
    return predictor, step


def encode(samples):
    # Nearest predicted value; high nibble first, fresh decoder per sample.
    predictor, step, nibbles = 0, 127, []
    for sample in samples:
        nibble = min(range(16), key=lambda n: (abs(advance(predictor, step, n)[0] - sample), n))
        predictor, step = advance(predictor, step, nibble)
        nibbles.append(nibble)
    if len(nibbles) % 2:
        nibbles.append(0)
    return bytes((a << 4) | b for a,b in zip(nibbles[::2], nibbles[1::2]))


def author_pcm(folder):
    folder.mkdir(parents=True, exist_ok=True)
    table, payload, items = bytearray(1024), bytearray(), []
    noise = 0x13579BDF
    for slot, (name,count) in enumerate([('tone',1600),('kick',3200),('noise',1600),('click',800)],1):
        samples=[]
        for i in range(count):
            t=i/16000
            envelope=(1-i/count)**2
            if name == 'tone':
                value=math.sin(2*math.pi*440*t)
            elif name == 'kick':
                value=math.sin(2*math.pi*(90*t-120*t*t))
            elif name == 'noise':
                noise=(1664525*noise+1013904223)&0xffffffff
                value=((noise>>16)/32767.5-1)
            else:
                value=1 if (i//8)%2==0 else -1
            samples.append(round(12000*envelope*value))
        wav=folder/(name+'.wav')
        with wave.open(str(wav),'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(struct.pack('<'+'h'*len(samples),*samples))
        encoded=encode(samples)
        padded=encoded+bytes(-len(encoded)%4)
        start=len(payload)//4
        entry=(slot-1)*32
        label=('SYNTH_'+name).encode('ascii')
        table[entry:entry+len(label)]=label
        table[entry+26]=255
        struct.pack_into('<HH',table,entry+28,start,len(padded))
        payload.extend(padded)
        items.append(dict(slot=slot,name=name,wav='pcm/'+wav.name,rate_hz=16000,
            sample_count=count,encoded_bytes=len(padded),bank_start_byte=start*4,
            encoded_sha256=digest(padded),wav_sha256=digest(wav.read_bytes())))
    bank=bytes(table+payload)
    (folder.parent/'synthetic_pcm.bin').write_bytes(bank)
    return bank,items


def author_voices(folder):
    # Self-authored musical voice definitions -> conventional 32-byte DAT slots.
    lines=VOICE.splitlines()
    data=bytearray(3*32)
    for offset in (0,6):
        slot=int(lines[offset][1:])
        feedback,algorithm=map(int,lines[offset+1].split(','))
        ops=[list(map(int,line.split(','))) for line in lines[offset+2:offset+6]]
        registers=[]
        for field in range(6):
            for index in (0,2,1,3):
                ar,dr,sr,rr,sl,tl,ks,mul,dt=ops[index]
                registers.append((dt<<4|mul,tl,ks<<6|ar,dr,sr,sl<<4|rr)[field])
        registers.append(feedback<<3|algorithm)
        data[slot*32+1:slot*32+26]=bytes(registers)
        data[slot*32+26:slot*32+32]=('TEST'+str(slot)).encode('ascii').ljust(6,b' ')
    (folder/'synthetic_voice.dat').write_bytes(data)
    return bytes(data)


def loop_markers(text):
    return sum(line.split(';',1)[0].count('L') for line in text.splitlines()
               if re.match(r'^[A-K]\s+',line))


def inspect(source, passes, features, bank, sample_items):
    raw=source.read_bytes()
    facts={}
    parse_vgm(str(source),str(passes),opna_metadata=facts,
              dump_opna_segments=True,loop_count=1,include_vgmticks=True)
    ssg=build_segments(facts['csv_path'],end_vgmticks=facts['source_end_vgmticks'])
    ssg.dump(passes,source.stem)
    pcm=analyze_pcm(raw,loop_count=1)
    pcm.dump(passes,source.stem)
    with open(facts['csv_path'],encoding='utf-8',newline='') as stream:
        rows=list(csv.DictReader(stream))
    writes=[(int(r['vgmticks']),int(r['port']),int(r['register']),int(r['data'])) for r in rows]
    fm_keys=[w for w in writes if w[1]==0 and w[2]==0x28 and w[3]&0xf0]
    rhythm=[w for w in writes if w[1]==0 and w[2]==0x10 and not w[3]&0x80 and w[3]&0x3f]
    ssg_on=[s for channel in ssg.segments.values() for s in channel
            if (s.volume>0 or s.envelope_enabled) and s.mode]
    loop_offset=0x1c+struct.unpack_from('<I',raw,0x1c)[0]
    events=list(command_times(raw,loop_count=1))
    loop_event=next(e for e in events if e.address==loop_offset)
    loop_samples=struct.unpack_from('<I',raw,0x20)[0]
    assert struct.unpack_from('<I',raw,0x1c)[0] and loop_event.vgmticks>0 and loop_samples>0
    prelude_beats, loop_beats=BEATS[source.stem]
    assert (loop_event.vgmticks,loop_samples)==(prelude_beats*22050,loop_beats*22050)
    assert loop_samples==facts['source_end_vgmticks']-loop_event.vgmticks
    actual=dict(fm=bool(fm_keys),ssg=bool(ssg_on),rhythm=bool(rhythm),pcm=bool(pcm.segments),
        noise=any(s.mode in (2,3) for s in ssg_on),
        hardware_envelope=any(s.envelope_enabled and s.envelope_trigger for ch in ssg.segments.values() for s in ch))
    for feature in features:
        assert actual[feature],(source.name,feature,'missing source evidence')
    for feature in ('fm','ssg','rhythm','pcm'):
        assert actual[feature]==(feature in features),(source.name,feature,'unexpected active engine')
    expected={x['encoded_sha256'] for x in sample_items}
    if pcm.segments:
        assert pcm.blocks and all(s.sample_id is not None for s in pcm.segments)
        assert all(s.sha256 in expected for s in pcm.samples)
        assert all(s.encoded_bytes in bank[1024:] for s in pcm.samples)
    # A second source traversal must preserve the loop boundary and evidence IDs.
    repeated=analyze_pcm(raw,loop_count=2)
    assert repeated.source_end_vgmticks==pcm.source_end_vgmticks+loop_samples
    expanded=list(command_times(raw,loop_count=2))
    first=[(e.command,raw[e.address+1:e.address+3],e.vgmticks-loop_event.vgmticks,e.source_event_id)
           for e in events if e.address>=loop_offset and e.command in (0x56,0x57)]
    second=[(e.command,raw[e.address+1:e.address+3],e.vgmticks-pcm.source_end_vgmticks,e.source_event_id)
            for e in expanded if e.loop_iteration==1 and e.command in (0x56,0x57)]
    assert first==second
    loop_pcm=sum(s.continuation_index==0 and s.start_vgmticks>=loop_event.vgmticks for s in pcm.segments)
    assert len({s.playback_id for s in repeated.segments})==len({s.playback_id for s in pcm.segments})+loop_pcm
    key_masks={ch:0 for ch in (0,1,2,4,5,6)}
    ssg_amplitude=[0,0,0]
    pcm_control=0
    rhythm_dump=False
    for tick,port,register,data in writes:
        if tick>=loop_event.vgmticks:
            break
        if port==0 and register==0x28:
            key_masks[data&7]=data&0xf0
        elif port==0 and 8<=register<=10:
            ssg_amplitude[register-8]=data&31
        elif port==1 and register==0:
            pcm_control=data
        elif port==0 and register==0x10 and data&0x80:
            rhythm_dump=True
    assert not any(key_masks.values()) and not any(ssg_amplitude)
    assert not pcm_control&0x80
    if source.stem=='mixed_intro':
        assert rhythm_dump
        assert all(s.end_vgmticks<loop_event.vgmticks for s in pcm.segments)
        assert not any(e.command==0x57 and raw[e.address+1]==0 and raw[e.address+2]&0x80
                       for e in expanded if e.loop_iteration==1)
    if source.stem=='fm_six_channels':
        assert {w[3]&7 for w in fm_keys}=={0,1,2,4,5,6}
    if source.stem=='rhythm_six':
        assert {w[3]&63 for w in rhythm}>={1,2,4,8,16,32}
    twice={}
    parse_vgm(str(source),str(passes/'two_loops'),opna_metadata=twice,
              dump_opna_segments=True,loop_count=2,include_vgmticks=True)
    first_attacks=[(e.ch,e.state) for e in twice['analysis'].events
                   if e.rising_mask and e.loop_iteration==0 and e.vgmticks>=loop_event.vgmticks]
    second_attacks=[(e.ch,e.state) for e in twice['analysis'].events
                    if e.rising_mask and e.loop_iteration==1]
    assert first_attacks==second_attacks
    first_pcm=[(s.original_vgmticks,s.sample_id,s.rate_num,s.rate_den,s.level,s.pan)
               for s in repeated.segments if s.loop_iteration==0 and s.start_vgmticks>=loop_event.vgmticks]
    second_pcm=[(s.original_vgmticks,s.sample_id,s.rate_num,s.rate_den,s.level,s.pan)
                for s in repeated.segments if s.loop_iteration==1]
    assert first_pcm==second_pcm
    result=dict(loop_start_samples=loop_event.vgmticks,loop_samples=loop_samples,
        total_samples=facts['source_end_vgmticks'],clock_hz=facts['clock_hz'],
        active_features=actual,fm_keyon_requests=len(fm_keys),rhythm_triggers=len(rhythm),
        ssg_active_control_intervals=len(ssg_on),pcm_memory_blocks=len(pcm.blocks),
        pcm_playbacks=len({s.playback_id for s in pcm.segments}),
        pcm_sample_hashes=sorted({s.sha256 for s in pcm.samples}),
        repeated_total_samples=repeated.source_end_vgmticks,
        loop_register_replay_equal=True,fm_loop_attack_states_equal=True,pcm_loop_states_equal=True,
        preloop_fm_key_masks=key_masks,preloop_ssg_amplitudes=ssg_amplitude,
        preloop_pcm_start_bit=bool(pcm_control&0x80))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir',type=Path,default=FIXTURES)
    parser.add_argument('--mucomvgm',type=Path,default=TOOL)
    parser.add_argument('--passes',type=Path,default=ROOT/'outputs/public_mucom88_fixtures')
    parser.add_argument('--inputs-only',action='store_true')
    args=parser.parse_args(argv)
    args.outdir.mkdir(parents=True,exist_ok=True)
    bank,items=author_pcm(args.outdir/'pcm')
    voices=author_voices(args.outdir)
    records=[]
    for name,(description,features,body) in PATTERNS.items():
        text='#title '+name+'\n#composer emef2247\n#author emef2247\n'
        text+='; Authored public test score. One loop command in the whole file.\n'
        if 'pcm' in features:
            text+='#pcm synthetic_pcm.bin\n'
        if 'fm' in features:
            text+='#voice synthetic_voice.dat\n'
        text+=body+'\n'
        assert loop_markers(text)==1
        muc=args.outdir/(name+'.muc');muc.write_text(text,encoding='utf-8',newline='\n')
        if args.inputs_only:
            continue
        vgm=muc.with_suffix('.vgm')
        def native_path(path):
            if sys.platform!='win32' and args.mucomvgm.suffix.lower()=='.exe':
                return subprocess.check_output(['wslpath','-w',str(path.resolve())],text=True).strip()
            return str(path.resolve())
        options=dict(cwd=args.outdir,capture_output=True,timeout=60)
        if sys.platform=='win32':
            options['creationflags']=subprocess.CREATE_NO_WINDOW
        run=subprocess.run([str(args.mucomvgm.resolve()),native_path(muc),native_path(vgm)],**options)
        folder=args.passes/name;folder.mkdir(parents=True,exist_ok=True)
        (folder/'compile.log').write_bytes(run.stdout+run.stderr)
        if run.returncode or not vgm.is_file():
            raise RuntimeError(f'{name}: compilation failed; see {folder}/compile.log')
        facts=inspect(vgm,folder,features,bank,items)
        records.append(dict(name=name,description=description,expected_features=features,
            loop_commands=1,muc_sha256=digest(muc.read_bytes()),vgm_sha256=digest(vgm.read_bytes()),**facts))
        print(name+': compiled and source evidence checked',flush=True)
    if not args.inputs_only:
        manifest=dict(schema_version=1,compiler='MUCOMVGM 0.0.5',
            compiler_sha256=digest(args.mucomvgm.read_bytes()),
            compiler_revision='827a97c6c63e0df1ce74c8cee7dcc93cd6a00a22',
            pcm_origin='Mathematically synthesized; no RX21 or upstream sample assets',
            pcm_codec='nominal Yamaha ADPCM-B, high nibble first',
            voice_dat_sha256=digest(voices),pcm_bank_address_unit_bytes=4,pcm_bank_sha256=digest(bank),samples=items,fixtures=records,
            validation_scope='Compilation and native source IR/loop/PCM byte checks; no musical MUC renderer or MUB/playback certification')
        (args.outdir/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
