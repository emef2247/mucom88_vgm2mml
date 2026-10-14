# Original OPM / MSM6258 fixtures

These VGM files contain arithmetic byte patterns written for this project,
not game audio or ROM samples. They are distributed under the repository's
MIT license. Regenerate with `python tests/scripts/generate_pcm_fixtures.py`.
This folder groups native MSM6258 inputs, including PCM-only and OPM+PCM.
It does not stand for other chips' PCM encodings or playback interfaces.

- `reset_pan_hold`: PCM-only, observed STOP/PLAY resets, identical sample reuse,
  left/right/mute/center changes inside a long held sample, and a rest.
- `opm_pcm_rates`: simultaneous OPM and PCM, with one encoded sample reused at
  exact standard F0 and F4 rates.
- `long_hold_stop`: a 512-tick PCM note on the 256-us clock, requiring F7
  before its first 256-tick chunk, followed by STOP and a rest.
- `stream95_finite`: four authored bytes from a type-04 bank, supplied by a
  finite fast stream start while explicit B7 STOP/PLAY controls the decoder.
- `stream93_count` and `stream93_to_end`: the same four bytes through the
  command-count and bank-to-end modes of command 93.
- `stream_natural_end`: supply exhausts without inventing a B7 chip STOP.
- `stream_supply_only`: a stream supplies bytes while the decoder stays stopped.
- `stream94_supply_stop`: command 94 stops supply after two bytes, followed
  separately by the actual B7 chip STOP.
- `stream_delayed_chip_stop`: the same finite four-byte supply with chip STOP
  delayed by 100 ticks; continuous target delivery is an explicit known loss.
- `stream_stopped_supply_restart`: bytes supplied after a chip STOP remain
  inspectable, then the next PLAY and stream restart occur at the same tick.

Headers use flags `6`: divider 512 and the player's bit-2-set 4-bit ADPCM
selection. The stream fixtures use integer frequency 7813 bytes/second;
their JSON files describe scheduled supply rather than decoder consumption.

`reset_pan_hold` intentionally requires best-effort for the current onset-pan
projection: its held pan changes are diagnosed as known loss. Each complete
playback fits one note, so the target does not invent pan-driven ties or attacks.
Generated PCM MDX now selects the 16-track/E8 extension route; only P is active.
This does not certify additional extension features or decoder consumption.

Byte writes follow `floor(i * 88200 * divider / clock)` VGM samples. The adjacent
JSON files specify independently constructed source times, sample hashes and
target bindings/rates/pans. They do not assert measured decoder consumption,
waveform equivalence or successful third-party PCM VGM replay.
