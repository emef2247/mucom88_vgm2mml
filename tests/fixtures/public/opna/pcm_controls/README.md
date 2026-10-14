# OPNA PCM live-control fixture

`heldctrl.vgm` contains project-authored, synthetic ADPCM-B bytes. No game data,
recorded instrument samples or Yamaha rhythm ROM data is included. The fixture
is covered by this repository's MIT license.

It exercises ordered same-time volume writes, live pan changes, Delta-N pause
and resume, level255 during a held sample, long ties, and a later independent
start. Source nominal depletion and observed reset remain separate evidence.
There is no song loop.

Regenerate from the repository root:

```sh
python tests/scripts/opna_pcm_fixture.py --outdir tests/fixtures/public/opna/pcm_controls
```

Convert/compile with the default8ms normalization:

```sh
python scripts/setup_mucom_pcm_tools.py
python scripts/export_mucom.py tests/fixtures/public/opna/pcm_controls --outdir outputs/pcm_controls --no-song-loop --timeout 600
```

The live-control MUC requires the pinned mucomDotNET multi-port-y profile and
Windows .NET6 runtime. Source codec bytes, playback CSV and MUC target-position
JSON are distinct artifacts; MUB compilation is not waveform verification.
