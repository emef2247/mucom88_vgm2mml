# Public OPNA patterns

`from_fm/` (16 VGM cases) and `from_psg/` (13 cases) were migrated from the
MSX converter's public OPNA fixtures. The user prepared them by converting
the project's public OPLL patterns with vgm-conv. Retained MGSDRV MML in
`reference/` is pre-conversion musical context, not an expected MUCOM score.
See the repository public fixture policy in `tests/fixtures/README.md` and
vgm-conv attribution in `THIRD_PARTY_NOTICES.md`.

The FM cases have observed OPNA FM gates and rhythm initialization. The PSG
cases contain SSG writes but no FM attacks. FM roundtrip reports these as
`no_active_fm`, rather than counting silent cases as successful FM coverage.
All 29 inputs declare a 7987200 Hz YM2608 clock. Tests compare actual OPNA
commands/state rather than assuming the original OPLL score.

Locally expanded `mucom88/` samples are separate third-party source material
from the Ancient/Onion distributions, with accompanying copyright/licensing
notices. They are not required by the automated pattern checks or included in
this fixture migration commit. Preserve those originals and notices. Generated
validation artifacts belong under ignored `outputs/`, never beside sources.
