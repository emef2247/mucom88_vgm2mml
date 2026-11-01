"""Native SCC/SCC+ MUC projection for the MDPlayer4Mac chip extension.

Keep source wave bytes and interpreted software-volume runs intact. Ordinary
C128 rows retain target clock lengths; signed ?DB applies a direct period delta
without the ordinary D command's OPNA-to-SCC pitch conversion.
"""
from dataclasses import dataclass
import math

from mml_envelopes import settings

TARGET_CLOCK_HZ = 1_789_773
NOTES = ('c','c+','d','d+','e','f','f+','g','g+','a','a+','b')
# NativeNotePeriods.Scc in MDPlayer4Mac/MucoNative. Frozen native notation
# periods, not an OPNA FNUM/SSG table. ?DB adjusts these physical SCC periods.
SCC_PERIODS = (
    3413,3221,3040,2870,2709,2556,2412,2277,2149,2029,1915,1808,
    1706,1610,1520,1434,1353,1277,1206,1138,1074,1013,957,903,
    852,804,759,716,676,638,602,568,537,506,478,451,
    426,402,379,358,337,318,300,283,267,252,238,224,
    212,200,189,178,168,158,149,141,133,125,118,112,
    105,99,94,88,83,79,74,70,66,62,58,55,
    52,49,46,44,41,38,37,34,32,30,29,27,
    25,24,22,21,20,19,18,16,15,14,13,12,
)
FLAT_ENVELOPE = 'E255,0,0,0,0,255'


def wave_definition(wave_id, waveform_hex):
    """Render exactly one observed signed 32-byte wave in ?WD notation."""
    if not isinstance(wave_id,int) or not 0 <= wave_id <= 63:
        raise ValueError('Native SCC ?WD waveform ID must be 0..63')
    raw=bytes.fromhex(waveform_hex)
    if len(raw)!=32:
        raise ValueError('Native SCC waveform requires exactly 32 bytes')
    signed=(value if value<128 else value-256 for value in raw)
    return '?WD'+str(wave_id)+'={'+','.join(map(str,signed))+'}'


@dataclass(frozen=True)
class PitchProjection:
    note: str
    octave: int
    base_period: int
    target_period: int
    requested_period: int
    detune: int
    source_frequency_hz: float
    target_frequency_hz: float
    clamped: bool

    @property
    def commands(self):
        return ('D0',f'?DB{self.detune},1',f'o{self.octave}')


def project_pitch(period,source_clock_hz,*,target_clock_hz=TARGET_CLOCK_HZ):
    """Map VGM clock/16/(period+1) to native SCC note plus signed period bias."""
    if not isinstance(period,int) or not 9<=period<=4095:
        raise ValueError('Sounding native SCC source period must be 9..4095')
    if (not math.isfinite(source_clock_hz) or source_clock_hz<=0 or
            not math.isfinite(target_clock_hz) or target_clock_hz<=0):
        raise ValueError('Native SCC projection requires finite positive clocks')
    requested=round((period+1)*target_clock_hz/source_clock_hz)-1
    target=max(9,min(4095,requested))
    index=min(range(len(SCC_PERIODS)),key=lambda i:(abs(SCC_PERIODS[i]-target),i))
    base=SCC_PERIODS[index]
    return PitchProjection(NOTES[index%12],index//12+1,base,target,requested,
        target-base,source_clock_hz/(16*(period+1)),target_clock_hz/(16*(target+1)),
        target!=requested)


def _timed(note,clocks,continuation=False):
    words=[]
    while clocks>0:
        chunk=min(clocks,111)
        if continuation or words:words.append('&')
        words.append(f'{note}%{chunk}')
        clocks-=chunk
    return ' '.join(words)


def render_note(note,ticks,wave_id,source_clock_hz):
    """Lower one complete interpreted note, retaining its held volume changes."""
    wave_definition(wave_id,note.segment.waveform_hex)
    pitch=project_pitch(note.segment.tone_period,source_clock_hz)
    continuation=bool(note.continuation)
    words=['&'] if continuation else []
    words.append(f'@{wave_id}')
    if not continuation:words.append(FLAT_ENVELOPE)
    words.extend(pitch.commands)
    cursor=note.start;held=False
    for volume,length in note.runs:
        if not isinstance(volume,int) or not 0<=volume<=15:
            raise ValueError('Native SCC volume must be 0..15')
        if length<0:raise ValueError('Native SCC volume-run duration cannot be negative')
        duration=ticks[cursor+length]-ticks[cursor]
        if duration<0:raise ValueError('Native SCC target timing must be monotonic')
        if held and duration>0:words.append('&')
        words.append('v'+str(volume))
        text=_timed(pitch.note,duration)
        if text:words.append(text);held=True
        cursor+=length
    if cursor!=note.start+note.length:
        raise ValueError('Native SCC volume runs must cover the complete note')
    command=' '.join(words)
    key=(settings(note.segment,'scc'),note.length,tuple(note.runs),bool(note.continuation))
    info=dict(projected_opna_period=pitch.target_period,detune=pitch.detune,octave=pitch.octave)
    return command,key,info
