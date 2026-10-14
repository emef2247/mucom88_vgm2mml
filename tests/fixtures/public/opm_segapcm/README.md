# Authored SegaPCM listening patterns

These tiny OPM/SegaPCM VGMs contain a generated triangle waveform and an authored
FM voice. No game or external ROM data is included. Regenerate them with:

```bash
python tests/scripts/generate_segapcm_listening.py
```

- `SEGAONE`: one-shot PCM with natural stop at1.048576 seconds; finite FM note.
- `SEGAMIX`: two overlapping hardware loops, stereo volume and live delta changes.
- `SEGALOOP`: held PCM and FM across a declared VGM song loop, with a short final
  control slice; output repeats the second traversal.

Generate listening files using the built helper:

```bash
python scripts/export_mdx.py tests/fixtures/public/opm_segapcm \
  --outdir outputs/listen/segapcm --no-vgm --pcm-policy best-effort
```

The normal PCM extension player requirement applies. Artifact generation and
byte verification do not confirm native playback or acoustic fidelity. The
JSON expectations describe source facts, not verified listening outcomes.
