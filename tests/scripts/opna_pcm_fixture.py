"""Project-authored ADPCM-B bytes and controlled playback VGM; no external assets."""
import argparse
from pathlib import Path
import struct


def authored_pcm(changes=(), *, end=22050, size=8192, memory_mode=0xc1, loop_tick=None):
    encoded=bytes((0x12,0x98,0x21,0x89))*(size//4)
    header=bytearray(256);header[:4]=b'Vgm '
    struct.pack_into('<I',header,8,0x171)
    struct.pack_into('<I',header,0x34,256-0x34)
    struct.pack_into('<I',header,0x48,7987200)
    payload=struct.pack('<II',size,0)+encoded
    body=bytearray(b'\x67\x66\x81'+struct.pack('<I',len(payload))+payload)
    shift=5 if memory_mode&3 else 2
    limit=(size>>shift)-1
    setup=((1,memory_mode),(2,0),(3,0),(4,limit&255),(5,limit>>8),
           (9,0x82),(10,0x4a),(11,100),(0,0xa0))
    for reg,value in setup:body.extend((0x57,reg,value))
    tick=0;loop_address=None
    for at,reg,value in (*changes,(end,0,1)):
        if at<tick:raise ValueError('Unordered authored writes')
        while at>tick:
            step=min(at-tick,65535)
            if loop_tick==tick:loop_address=256+len(body)
            body.extend(b'\x61'+struct.pack('<H',step));tick+=step
        if loop_tick==tick:loop_address=256+len(body)
        body.extend((0x57,reg,value))
    body.append(0x66);raw=header+body
    struct.pack_into('<I',raw,4,len(raw)-4);struct.pack_into('<I',raw,0x18,end)
    if loop_address is not None:
        struct.pack_into('<I',raw,0x1c,loop_address-0x1c)
        struct.pack_into('<I',raw,0x20,end-loop_tick)
    return bytes(raw)



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir',type=Path,required=True)
    args=parser.parse_args();args.outdir.mkdir(parents=True,exist_ok=True)
    changes=((4410,11,80),(4410,11,60),(8820,1,0x41),
             (13230,9,0),(13230,10,0),(17640,9,0x34),(17640,10,0x12),
             (22050,11,255),(88200,0,1),(92610,11,100),(92610,1,0xc1),
             (92610,9,0x82),(92610,10,0x4a),(92610,0,0xa0))
    path=args.outdir/'heldctrl.vgm'
    path.write_bytes(authored_pcm(changes,end=110250))
    print(path)


if __name__=='__main__':main()
