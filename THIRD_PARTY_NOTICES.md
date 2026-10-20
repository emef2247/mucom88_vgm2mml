# Third-party attribution and notices

This file retains attribution inherited from x68k_vgm2mml. It covers referenced
mapping material and external tools in the inherited code, not a claim that
MDX/PDX tooling is part of the planned MUCOM88 production route. The project source is MIT-licensed; each third-party component keeps
its own terms. Tool installation by `scripts/setup_tools.sh` is a local fetch
and build, not a redistribution of tool executables in this repository.

## vgm-conv

Project: [digital-sound-antiques/vgm-conv](https://github.com/digital-sound-antiques/vgm-conv)

Reference revision: `780a20ed505e23eb21aa9c6ce3f349029718b5a0`

Reference source:
[src/converter/ay8910-to-opm-coverter.ts](https://github.com/digital-sound-antiques/vgm-conv/blob/780a20ed505e23eb21aa9c6ce3f349029718b5a0/src/converter/ay8910-to-opm-coverter.ts)

The PSG FM projection in `py/psg_opm_fm.py` uses the published AY8910-to-OPM
voice parameters and volume-to-TL mapping as a reference. This includes
AL4/FB7, the MUL2/TL27 modulator, MUL1 carrier, fixed-level TL table and eight
attenuation steps of headroom. Segment processing and target generation are
implemented in this project; the external converter is not a runtime dependency.
The upstream notice is retained for these referenced mapping materials.

The OPNA FM projection in `py/opna_opm.py` also references
[opn-to-opm-converter.ts](https://github.com/digital-sound-antiques/vgm-conv/blob/780a20ed505e23eb21aa9c6ce3f349029718b5a0/src/converter/opn-to-opm-converter.ts)
at the same revision for operator-bank addresses, channels0..2/4..6, key masks,
RL swapping, FB/algorithm, PMS/AMS and approximate LFO frequency mapping.
This project consumes native committed11-bit FNUM/BLOCK and uses its existing
4MHz OPM pitch helper, rather than the upstream raw-register9-bit cache path.
Source parsing, exact-sample serialization and MDX generation use this project's
separate stages; vgm-conv is not a runtime dependency. The ISC notice below
also applies to the referenced OPN mapping materials.

Upstream [LICENSE.md](https://github.com/digital-sound-antiques/vgm-conv/blob/780a20ed505e23eb21aa9c6ce3f349029718b5a0/LICENSE.md):

```text
ISC License

Copyright (c) 2023 Mitsutaka Okazaki and Contributors

Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted, provided that the above copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## mmlx 0.2.0

Project: [h1romas4/chipstream](https://github.com/h1romas4/chipstream)

Use: MML-to-MDX compilation and the FM part of the typed PCM backend. This crate is linked into the Rust
`mdx-fixture-generator` helper; it is not a separate command-line subprocess.
The exact Cargo version is pinned in `scripts/mdx_fixture_generator/Cargo.toml`.
The published package's `.cargo_vcs_info.json` records revision `7e0c97733d3636e40ee23233c898e74691d48417`.

License: MIT. The following text is copied from the installed published crate's
[LICENSE](https://github.com/h1romas4/chipstream/blob/7e0c97733d3636e40ee23233c898e74691d48417/crates/mmlx/LICENSE).
Retain it when redistributing the crate or a helper binary containing it.

```text
MIT License

Copyright (c) 2026 h1romas4

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## soundlog 0.15.0

Project: [h1romas4/chipstream](https://github.com/h1romas4/chipstream)

Use: MDX-to-VGM replay, PDX construction and PCM reference checks. This crate is linked into the Rust
`mdx-fixture-generator` helper; it is not a separate command-line subprocess.
The exact Cargo version is pinned in `scripts/mdx_fixture_generator/Cargo.toml`.
The published package's `.cargo_vcs_info.json` records revision `7e0c97733d3636e40ee23233c898e74691d48417`.

License: MIT. The following text is copied from the installed published crate's
[LICENSE](https://github.com/h1romas4/chipstream/blob/7e0c97733d3636e40ee23233c898e74691d48417/crates/soundlog/LICENSE).
Retain it when redistributing the crate or a helper binary containing it.

```text
MIT License

Copyright (c) 2025 h1romas4

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## MXC v1.01

Authors: MFS soft, milk. The compiler banner reads:

```text
MML converter for mxdrv2  version 1.01 (c)1989 MFS soft, milk.
```

Use: external Human68k MML-to-MDX compiler executed through run68x.
The setup script extracts `mxc.x` and its accompanying `mxc.doc` from
[MDX_TOOL.lzh](https://nfggames.com/x68000/Mirrors/x68pub/x68tools/SOUND/MXDRV/MDX_TOOL.lzh).
The checked archive SHA-256 is
`4d4a6f00a100c728f5f4b217998e66ca8e6fa530cdfe1a5ee7c03f70715d98c4`;
`mxc.x` SHA-256 is
`38def25dae39ae35a16668f086843ae9f0d00295e50fd034b7b4108f7c762086`.

The original CP932 `mxc.doc` states:

```text
☆ このプログラムはフリーウェアです。配布はご自由にどうぞ。
```

This permits distribution of MXC as freeware. It is not an MIT, ISC or GPL
license, and the checked documentation does not establish permission to modify
MXC or access its source. Keep `mxc.doc` with the compiler if redistributing it.
This permission concerns MXC itself: it is not a blanket license for the other
programs bundled in the historical archive. The archive's `readme.doc` describes
an unofficial repack; setup only extracts MXC and its document.

## run68x

Project: [kg68k/run68x](https://github.com/kg68k/run68x)

Installed revision: `fc28826dd795b1f0a3241f0b03fe2353e12e7233`

Use: a separately executed Human68k runner for MXC, locally fetched and built.
Its code is not linked into the Python converter or the Rust helper.

License: GNU GPL version 2 or later, as stated by the pinned
[README](https://github.com/kg68k/run68x/blob/fc28826dd795b1f0a3241f0b03fe2353e12e7233/README.md)
and source headers. Author: TcbnErik, with upstream run68 contributors.
For example, `src/run68.c` carries `Copyright (C) 2025 TcbnErik`.
Other files retain their respective upstream notices, including the separately
licensed Windows console component; this example is not an exhaustive copyright list.

The full license is preserved in the fetched checkout's
[LICENCE](https://github.com/kg68k/run68x/blob/fc28826dd795b1f0a3241f0b03fe2353e12e7233/LICENCE).
When redistributing run68x, retain its copyright/license notices and comply with
GPL source-distribution requirements (in particular section 3 for binaries).
A README credit alone does not replace those requirements.

## Optional mdxtools inspectors

Project: [vampirefrog/mdxtools](https://github.com/vampirefrog/mdxtools)

Installed revision: `9c8539fec2757fcf7c85d1986171b50ebe2ef1e5`

Use: independent `mdxinfo`, `pdxinfo`, `mdxdump` and `mdx2mml` commands installed
only by `scripts/setup_tools.sh --with-mdxtools`.
The upstream README refers to its
[LICENSE](https://github.com/vampirefrog/mdxtools/blob/9c8539fec2757fcf7c85d1986171b50ebe2ef1e5/LICENSE),
which contains GNU GPL version 3. Preserve the upstream checkout, component
notices and corresponding source if redistributing these tools; this entry is
not a full audit of mdxtools' bundled sources and submodules.

## Distribution scope

The repository and listening exports do not bundle the above external compiler,
runner or inspector executables. The setup script retains MXC's document and
full Git checkouts for run68x/mdxtools. Cargo fetches the published Rust crates,
whose license files were checked for the notices above.

A distribution of the Rust helper binary must also cover its transitive
Rust dependencies from `Cargo.lock`; the two direct MIT notices above are not
an exhaustive binary-distribution license bundle. The inspected lockfile
contains permissive MIT/Apache-2.0 alternatives and, for `unicode-ident`, an
additional Unicode-3.0 requirement. Preserve applicable upstream license and
copyright texts before distributing such a binary.

## Native MUCOM88 compiler and offline replay

[MUCOM88 Windows](https://onitama.tv/mucom88/) includes the separately executed
[mucomDotNET](https://github.com/kuma4649/mucomDotNET) compiler and MUB-to-VGM
converter. Tested distribution: MUCOM88 Windows 260407. Its bundled mucomDotNET
README states **CC BY-NC-SA 4.0**; copyright remains with the authors/component
holders. Refer to the upstream README and bundled component notices for terms.
Executables, DLLs and source code are not incorporated or redistributed here.
The repository MIT license does not relicense them.

The target uses independently written interoperability logic based on documented
formats and native replay probes. Channel, note, detune, timer and volume facts
were checked against [MUCOM88 Reference](https://github.com/onitama/mucom88/wiki/MMLReference)
and mucomDotNET documentation/driver sources. Public fixtures contain only
project-authored music/voices and synthesized PCM; no songs or rhythm ROM assets.


## MUCOMVGM 0.0.5

Project: [wingghost/MUCOMVGM](https://github.com/wingghost/MUCOMVGM)

Author: WING. Release: [Ver.0.0.5](https://github.com/wingghost/MUCOMVGM/releases/tag/Ver.0.0.5),
revision `827a97c6c63e0df1ce74c8cee7dcc93cd6a00a22`.

Use: separately executed `mucomvgm.exe` compiles MUCOM88-format MML to YM2608
VGM for local test-fixture preparation, native OPNA reader/State/Segment
validation and the diagnostic FM
control-state roundtrip. This is not the X68000 converter's normal output.
The Studio GUI is a separate wrapper and is not required by this project.
This compiler is distinct from the official MUCOM88 Windows CLI.

The local portable archive `mucomvgm.zip` has SHA256
`6e5fc2e5fa845b77487f9755fe855bb7a3544df070fbd7518e988028dc312021`.
The release's `LICENSE.txt` and the pinned source
[LICENSE.txt](https://github.com/wingghost/MUCOMVGM/blob/827a97c6c63e0df1ce74c8cee7dcc93cd6a00a22/LICENSE.txt)
were checked. License: MIT. No compiler executable or upstream implementation
is bundled in this repository. Retain its copyright and permission notice when
redistributing the software or substantial portions of it.

```text
MIT License

Copyright (c) 2026 WING

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```


## mucomDotNET multi-port PCM tool profile

Author/project: [kuma4649/mucomDotNET](https://github.com/kuma4649/mucomDotNET).
Reference revision: `f0403762a37028c77afe23193bd674d7bf6efaf7`.
`scripts/setup_mucom_pcm_tools.py` downloads the upstream `bin.zip` as a separate
local tool dependency. Archive SHA256:
`666bc13830a6a9d4748a8f6b9c7c0317f59f2732b7bc12b6c25209600138f2dd`.
Its verified NET6 compiler/replay supports explicit chip/port register writes
used for continuous ADPCM-B controls. No vendor binary or source is included
in this repository or copied into the project implementation.

The pinned upstream [LICENSE.txt](https://github.com/kuma4649/mucomDotNET/blob/f0403762a37028c77afe23193bd674d7bf6efaf7/LICENSE.txt)
contains GNU GPL version3; setup retains that file beside the local archive.
Other packaged dependencies retain their own terms. This local subprocess use
does not change the MIT license of the project-authored converter and fixtures.
Thanks to kuma4649 for the maintained compiler, driver and published MML/source
references. This profile is distinct from the older Windows260407 bundle.

## MUCOMSX external tool profile

The optional setup downloads the unmodified official MUCOMSX runtime release
from [ToughkidDev/MUCOMSX](https://github.com/ToughkidDev/MUCOMSX/releases/tag/v1.0-stable)
into ignored local tooling. No COM executable, factory DAT, ROM or DOS system
file is vendored here. The runtime's NOTICE.txt and full LICENSES directory are
preserved in installations and staged DOS jobs. MUCOM88-derived components
retain the upstream CC BY-NC-SA 4.0 terms; other components carry their own BSD
notices or stated terms. The project's license does not relicense those tools.
See the bundled notices for ToughkidCST's MSX adaptations, Yuzo Koshiro / onitama,
MUCOM88em, VGMPlay MSX/neonlib and ymfm attribution. This profile does not import
MUCOMSX compiler/driver implementation into this project's Python converter.

## ymfm reconstructed YM2413 presets

`py/mucomsx_opll.py` uses the reconstructed YM2413 ROM preset parameter table
from ymfm (`src/ymfm_opl.cpp`, revision
`81aec25ccbb98f4873a255f7551ac4dadac59b4a`). The full BSD-3-Clause copyright,
conditions and disclaimer are retained in that module. The upstream credits
Aaron Giles and the preset reconstruction by David Viens and Hubert Lamontagne.
Source: https://github.com/aaronsgiles/ymfm
This notice does not apply to the deferred Nuked-OPLL-based mapping proposal.
