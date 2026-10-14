# Authored public MUCOM88 / OPNA fixtures

These short scores, FM voices and PCM waves were authored for this project and
are distributed under the repository MIT License. No RX21 captures, stock
MUCOM88 voices/PCM, third-party songs or chip rhythm ROM bytes are included.
Built-in rhythm is exercised through YM2608 register commands only.

Each `.muc` contains exactly **one `L` command in the entire file**, at a
positive source time. `L` is not a finite `[ ... ]` repetition. The generated
VGM contains one declared loop point; the file stores a prelude and one loop
body, not an indefinitely expanded song. Analysis checks two traversals.

| Score | Source behavior |
|---|---|
| `fm_notes` | FM attacks, rests and a held-note tie |
| `fm_voice_pan` | Two authored FM voices, level and stereo changes |
| `fm_six_channels` | All six FM channels, then one FM channel loops |
| `ssg_tone` | SSG tone, pitch, level, rests and tie |
| `ssg_noise` | Noise period and tone/noise mixer changes |
| `ssg_hwenv` | Hardware envelope shapes 9/10/13; transient period writes |
| `rhythm_six` | All six built-in rhythm masks, plus simultaneous kick/snare |
| `pcm_samples` | Four distinct synthetic ADPCM-B sample references |
| `pcm_rate_level` | PCM pitch, level and stereo changes between notes |
| `mixed_intro` | FM, three SSG channels, rhythm and PCM; FM-only loop |

The companion parts in `fm_six_channels` and `mixed_intro` are finite and end
before the loop starts. Source checks verify FM Key-Off, SSG amplitude zero and
ADPCM-B reset at the loop boundary. The mixed case explicitly dumps rhythm
before the loop. This avoids confusing MUCOMVGM's global loop with independent
native MUCOM88 part loops. Analog/release-tail silence is not measured.

## Dependencies and PCM provenance

FM scores use `#voice synthetic_voice.dat`, generated from two original voice
parameter sets. PCM scores use `#pcm synthetic_pcm.bin`, generated from
`pcm/tone.wav`, `pcm/kick.wav`, `pcm/noise.wav` and `pcm/click.wav`. The waves
are 16 kHz mono signed 16-bit: sine/decaying chirp/deterministic noise/square
pulse calculations, not recorded instrument samples.

The bank uses 32 entries of 32 bytes followed by ADPCM-B payload, little-endian
start addresses in four-byte RAM1 units, and encoded byte lengths. Samples
reset nominal predictor/step independently and store the high nibble first.
This format is checked against the pinned MUCOMVGM PCM loader. The waveform
names describe synthetic probes, not emulations of a particular drum machine.

`manifest.json` retains source/dependency hashes, slot mapping, compiler identity,
loop timing and inspected source facts. Compiled VGM contains an actual PCM
memory block and referenced playback controls; native sample bytes are checked
against the bank. Codec consistency and encoded-byte identity do not certify
analog sound or native MUB playback fidelity.

## Regeneration and checks

From the repository root with MUCOMVGM 0.0.5 installed locally:

```sh
python scripts/generate_mucom88_public_fixtures.py --mucomvgm PATH_TO_MUCOMVGM_EXE
python -m unittest discover -s tests/scripts -p 'test_mucom88_public_fixtures.py' -v
```

`--inputs-only` regenerates MUC, voice DAT, WAV and PCM bank without a compiler.
The generator supports Windows Python and WSL calling the Windows executable.
Native Raw/State/FM Segment/SSG Segment/PCM IR and two-loop FM passes are emitted
to `outputs/public_mucom88_fixtures/`, outside source fixtures.

These fixtures prepare the source side of musical MUC/MUB roundtrip validation.
MUCOMVGM MUC→VGM compilation and source-IR checks are separate from the new
VGM→musical MUC renderer, native MUB compilation and playback checks.

Sources: [MUCOMVGM](https://github.com/wingghost/MUCOMVGM), pinned
[PCM bank loader](https://github.com/wingghost/MUCOMVGM/blob/827a97c6c63e0df1ce74c8cee7dcc93cd6a00a22/adpcm.py),
and [MUCOM88 ADPCM workflow](https://github.com/onitama/mucom88/wiki/About-ADPCM-data).
Tool licenses are retained in the root THIRD_PARTY_NOTICES.md.

## Native MUC/MUB verification

Authored voices have equal carrier levels using the legacy native volume table.
All ten VGM patterns exported musical MUC/MUB and passed native replay control
comparison. Waveform/listening and MSX playback remain separate checks.
MUCOMVGM source banks store full encoded lengths; generated native banks store
encoded_length - 4 for the native inclusive end. Both retain the whole payload.
Do not interchange those headers.

MUCOMVGM writes hardware periods 1024/2048/512 and resets period to zero at
note-on here. Positive-duration source segments have period zero. This tests
shape changes and transient source writes, not sustained nonzero periods.
