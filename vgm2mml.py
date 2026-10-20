#!/usr/bin/env python3
"""Canonical YM2608 VGM -> musical MUCOM88 MUC; inherited MDX/MGS routes."""
import sys
import os
import argparse
import json
from pathlib import Path

# Allow importing py/ siblings from the repository root
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, 'py'))

from vgm_reader import parse_vgm
from scc_mml import process_scc_csv
from psg_mml import process_psg_csv
from opll_mml import process_opll_csv
from mml_sync import annotate_sync_points
from mml_macros import compress_macros
from gd3 import title_from_gd3
from mml_alloc import parse_alloc, override_alloc
from mml_envelopes import EnvelopeBank
from psg_scc_target import TUNING_HEADER
from conversion_config import inspect_source, select_mdx_route, normalization_enabled


def _has_chip_data(csv_path: str) -> bool:
    """Return True if *csv_path* contains at least one non-header data row."""
    try:
        with open(csv_path, 'r', newline='') as fh:
            for line in fh:
                line = line.rstrip('\r\n')
                if line and not line.startswith('#') and line.strip():
                    return True
    except OSError:
        pass
    return False


def _extract_from_alloc(mml_path: str) -> str:
    """Read *mml_path* and return the content starting from the first ``#alloc`` line.

    Lines before the first ``#alloc`` (i.e., the chip-level header:
    ``;[name=...]``, ``#opll_mode``, ``#tempo``, ``#title``) are stripped.
    The returned string starts with the first ``#alloc`` line.
    """
    try:
        with open(mml_path, 'r', newline='') as fh:
            lines = fh.readlines()
    except OSError:
        return ''

    for i, line in enumerate(lines):
        if line.startswith('#alloc'):
            return ''.join(lines[i:])
    # No #alloc found – return full content as fallback
    return ''.join(lines)


def _build_merged_mml(stem: str, song_dir: str,
                      has_psg: bool, has_scc: bool, has_opll: bool,
                      raw_ticks: bool = False, sync_min_gap: int = 1000,
                      target: bool = True, name: str | None = None,
                      title: str | None = None, tempo: int | None = None, opll_mode: int = 1) -> str:
    """Build the merged MML text from per-chip compress outputs.

    The merged file has a single global header followed by PSG, SCC, and OPLL
    parts (in that order) when the corresponding chip is present.  Each part
    begins with a separator comment and includes the chip MML content starting
    from the ``#alloc`` line (chip-level header is stripped).
    """
    lines = []
    lines.append(f';[name={stem if name is None else name} lpf=1]')
    lines.append(f'#opll_mode {opll_mode}')
    if target and (has_psg or has_scc):
        lines.append(TUNING_HEADER)
    lines.append(f'#tempo {tempo}' if tempo is not None else '#tempo 75' if raw_ticks else '#tempo 225')
    lines.append(f'#title {{ "{stem if title is None else title}"}}')
    lines.append('')

    parts = [
        ('psg',  'psg',
         "\n;-----------------------  psg part -------------------------------"),
        ('scc',  'scc',
         "\n;-----------------------  scc part -------------------------------"),
        ('opll', 'opll',
         "\n;-----------------------  OPLL part -------------------------------"),
    ]
    flags = {'psg': has_psg, 'scc': has_scc, 'opll': has_opll}

    header_text = '\n'.join(lines) + '\n'
    body_parts = [header_text]

    suffix = 'pass3.compress.MGS_pct.mml' if raw_ticks else 'pass3.compress.MGS.mml'

    for chip_key, chip_ext, separator in parts:
        if not flags[chip_key]:
            continue
        mml_path = os.path.join(song_dir,
                                f'{stem}.{chip_ext}.{suffix}')
        target_path = os.path.join(song_dir, f'{stem}.{chip_ext}.target.mml')
        if target and chip_key in ('psg', 'scc', 'opll') and os.path.exists(target_path):
            mml_path = target_path
        body_parts.append(separator + '\n')
        body_parts.append(_extract_from_alloc(mml_path))

    result = ''.join(body_parts)
    if not result.endswith('\n'):
        result += '\n'
    formatted = annotate_sync_points(result, min_gap=sync_min_gap, drop_silent=target)
    return compress_macros(formatted) if target else formatted


def build_parser():
    parser = argparse.ArgumentParser(
        description='Convert VGM to structured MDX MML; select the source route automatically')
    parser.add_argument('vgm', help='Input VGM file')
    parser.add_argument('--outdir', default=None, help='Output directory (default: input directory)')
    parser.add_argument('--target', choices=['mdx', 'mgs', 'opm', 'opm-additive'], default='mdx',
                        metavar='{mdx,mgs}', help='Output format: mdx (default) or MGSDRV compatibility mgs')
    parser.add_argument('--title', help='Override title (default: GD3 metadata, then input stem)')
    parser.add_argument('--gd3-language', choices=['ja', 'en'], default='ja',
                        help='Preferred GD3 language, with fallback (default: ja)')
    timing = parser.add_argument_group('MDX target timing')
    timing.add_argument('--normalize-lengths', action=argparse.BooleanOptionalAction, default=None,
                        help='Attempt safe target-clock correction (default: ON for structured MDX; MGS opt-in)')
    timing.add_argument('--normalization-ms', type=float, default=8,
                        help='MDX original whole-note omission threshold and maximum boundary movement in ms (default: 8; only with normalization enabled)')
    advanced = parser.add_argument_group('Advanced MDX notation and chip projection')
    advanced.add_argument('--notation', choices=['structured', 'legacy', 'registers'], default='structured',
                        help='Native MDX notation (default: structured)')
    advanced.add_argument('--track-layout', choices=['channels', 'conductor'], default='channels',
                        help='Native MDX tracks; conductor requires --notation registers')
    advanced.add_argument('--no-song-loop', action='store_true', help='Export one finite VGM pass instead of its declared song loop')
    advanced.add_argument('--no-loops', action='store_true', help='Disable MDX finite-repeat compression')
    advanced.add_argument('--psg-gain', type=float, default=None, help='PSG projection gain: fm=1, additive=0.125')
    advanced.add_argument('--psg-model', choices=['fm', 'additive'], default=None,
                         help='PSG-to-OPM tone model (default: fm); SCC uses additive')
    advanced.add_argument('--scc-gain', type=float, default=None, help='SCC-to-OPM gain (default: 0.125)')
    fidelity = parser.add_argument_group('Target fidelity policies')
    fidelity.add_argument('--opm-pitch-policy', choices=['clamp', 'error'], default=None,
                        help='OPM pitch range policy: default native/FM=clamp, additive=error')
    fidelity.add_argument('--pcm-policy', choices=['strict', 'best-effort'],
                        help='Native PCM target policy: strict (default) blocks known loss; best-effort reports defined loss')
    diagnostic = parser.add_argument_group('Diagnostic and development options')
    diagnostic.add_argument('--force', action='store_true',
                            help='Partial MDX output: omit unsupported OPNA HW effects and excess PCM samples; list all losses')
    diagnostic.add_argument('--fm-only', action='store_true',
                            help='OPM + SegaPCM diagnostic export: retain native SegaPCM evidence, omit it from MDX MML')
    diagnostic.add_argument('--dump-passes', action='store_true',
                            help='Keep source/state/Segment CSVs and target analysis passes')
    diagnostic.add_argument('--debug', action='store_true',
                            help='Keep diagnostics (MGSDRV also keeps all chip MML variants)')
    diagnostic.add_argument('--pcm-generator', help='External typed PCM MDX/PDX helper path')
    compatibility = parser.add_argument_group('MGSDRV compatibility options')
    compatibility.add_argument('--name', help='MGSDRV player metadata name (default: input stem)')
    compatibility.add_argument('--enhance-macros', action='store_true', default=None, help=argparse.SUPPRESS)
    compatibility.add_argument('--legacy-macros', dest='enhance_macros', action='store_false',
                        help='Use the previous greedy macro compressor')
    compatibility.add_argument('--legacy-loops', action='store_true',
                        help='Use previous loop/envelope projection for PSG, SCC and OPLL')
    compatibility.add_argument('--vgmticks', action='store_true',
                               help='Append absolute VGM samples to MGSDRV evidence; no retiming')
    compatibility.add_argument('--scc-input', choices=['trace', 'log'], default=None,
                        help='SCC intermediate format: trace (default, chronological)'
                             ' or log (per-channel grouped)')
    compatibility.add_argument('--psg-input', choices=['trace', 'log'], default=None,
                        help='PSG intermediate format: trace (default, chronological)'
                             ' or log (per-channel grouped)')
    compatibility.add_argument('--raw-ticks', action='store_true', help='MGSDRV raw tick %% note-length notation')
    compatibility.add_argument('--sync-min-gap', type=int, default=None,
                               help='MGSDRV sync-comment minimum gap (default: 1000; 0: all)')
    compatibility.add_argument('--alloc', type=parse_alloc,
                        help='Override selected track allocations, e.g. "9=1800, a=3780"')
    return parser


def _inherited_main():
    parser = build_parser()
    args = parser.parse_args()
    compatibility_target = args.target if args.target in ('opm', 'opm-additive') else None
    if compatibility_target:
        print(f'Warning: --target {compatibility_target} is deprecated; use --target mdx'
              + (' --psg-model additive' if compatibility_target == 'opm-additive' else ''), file=sys.stderr)
        args.target = 'mdx'
    if args.enhance_macros is True:
        print('Warning: --enhance-macros is deprecated; enhanced MGSDRV macros are already the default', file=sys.stderr)
    if args.sync_min_gap is not None and args.sync_min_gap < 0:
        parser.error('--sync-min-gap must be nonnegative')
    try:
        from conversion_config import normalization_samples
        normalization_samples(args.normalization_ms)
        normalize = normalization_enabled(args.normalize_lengths, notation=args.notation, target=args.target)
    except ValueError as error:
        parser.error(str(error))
    if normalize and args.raw_ticks:
        parser.error('--normalize-lengths cannot be combined with --raw-ticks')

    for field, value in (('name', args.name), ('title', args.title)):
        if value is not None and (any(c in value for c in '\r\n') or
                                  (']' in value if field == 'name' else '"' in value)):
            parser.error(f'--{field} contains a character that would break the MML header')

    if args.target == 'mgs' and (args.notation != 'structured' or args.track_layout != 'channels' or args.no_loops or args.no_song_loop):
        parser.error('MDX notation/track/loop options require --target mdx')
    if args.pcm_generator and args.target != 'mdx':
        parser.error('--pcm-generator requires --target mdx')
    if args.pcm_policy is not None and args.target != 'mdx':
        parser.error('--pcm-policy requires --target mdx')
    if args.target == 'mdx' and args.track_layout == 'conductor' and args.notation != 'registers':
        parser.error('Conductor tracks require --notation registers')
    mgs_options = (args.alloc is not None or args.raw_ticks or args.enhance_macros is not None
                   or args.legacy_loops or args.name is not None or args.sync_min_gap is not None
                   or args.vgmticks or args.psg_input is not None or args.scc_input is not None)
    if args.target == 'mdx' and mgs_options:
        parser.error('MGSDRV compatibility options require --target mgs')
    projection_options = (args.psg_model is not None or args.opm_pitch_policy is not None
                          or args.psg_gain is not None or args.scc_gain is not None)
    if args.target == 'mgs' and projection_options:
        parser.error('OPM projection options require --target mdx')

    vgm_path = args.vgm
    if not os.path.isfile(vgm_path):
        print(f"Error: {vgm_path!r} not found", file=sys.stderr)
        sys.exit(1)

    # Determine base name: "02_StartingPoint"
    base_name = os.path.splitext(os.path.basename(vgm_path))[0]
    if args.title is None and args.target != 'mdx':
        args.title = title_from_gd3(vgm_path, base_name, args.gd3_language)

    if args.outdir:
        song_dir = args.outdir
    else:
        song_dir = os.path.join(
            os.path.dirname(os.path.abspath(vgm_path)),
        )

    os.makedirs(song_dir, exist_ok=True)
    report_path = os.path.join(song_dir, base_name + '.conversion.json')
    if Path(report_path).resolve() == Path(vgm_path).resolve():
        parser.error('Conversion report must not replace its source input')
    if not Path(report_path).resolve().is_relative_to(Path(song_dir).resolve()):
        parser.error('Conversion report leaves its output directory')
    report = dict(target_format=args.target, compatibility_target=compatibility_target,
                  notation=args.notation, normalization_requested=args.normalize_lengths,
                  normalization_enabled=normalize, normalization_ms=args.normalization_ms, status='preflight')
    def record_report():
        with open(report_path, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    try:
        source_path = Path(vgm_path).resolve()
        from pcm_assessment import generated_binary_artifacts
        owned_binary = generated_binary_artifacts(song_dir, base_name) if args.target == 'mdx' else ()
        report['retained_binary_artifacts'] = [path.name for name in ('mdx', 'pdx')
            if (path := Path(song_dir) / (base_name + '.' + name)).is_file() and path not in owned_binary]
        suffixes = ('.mml', '.normalization.json') if args.target == 'mgs' else (
            '.mdx.mml', '.opna-opm.json', '.mdx.normalization.json',
            '.opna_ssg.omissions.csv', '.opna_ssg.omissions.json',
            '.opna_fm.omissions.csv', '.opna_fm.omissions.json', '.opna.unmodeled.csv',
            '.opna_force.skipped_registers.csv', '.opna_force.skipped_registers.json',
            '.psg_force.skipped_registers.csv', '.psg_force.skipped_registers.json',
            '.pcm.force_capacity.csv', '.pcm.force_capacity.json',
            '.mdx.normalization.csv', '.mdx.before.normalize.mml',
            '.pcm.timing.json', '.pcm.assessment.json', '.pcm.assessment.csv',
            '.pcm_target_commands.csv', '.pcm_projection.csv', '.pcm_clock.csv', '.pcm_bindings.csv')
        owned = list(owned_binary) + [Path(song_dir) / (base_name + suffix) for suffix in suffixes]
        if args.target == 'mdx':
            owned += [Path(song_dir) / (base_name + '.pcm') / name for name in
                      ('target.tsv', 'opm.mml', 'manifest.tsv', 'pdx_build.log', 'mdx_build.log')]
        for artifact in owned:
            if artifact.resolve() == source_path:
                raise ValueError('Conversion output must not replace its source input')
            if not artifact.resolve().is_relative_to(Path(song_dir).resolve()):
                raise ValueError('Conversion output leaves its output directory')
        for artifact in owned:
            artifact.unlink(missing_ok=True)
        usage = inspect_source(vgm_path)
        report['source'] = usage
        if args.target == 'mdx':
            route = select_mdx_route(usage, compatibility_target=compatibility_target)
        else:
            if usage['unsupported_commands'] or not set(usage['used_chips']) <= {'psg', 'scc', 'opll'}:
                detail = usage['unsupported_commands'][0] if usage['unsupported_commands'] else None
                raise ValueError('Used source chip/stream is not supported by MGSDRV compatibility: '
                                 + (f"{detail['command']} at {detail['address']:#x}: {detail['reason']}" if detail
                                    else ', '.join(usage['used_chips'])))
            route = 'mgs-compatibility'
        report['chip_projection'] = route
        report['force_requested'] = args.force
        if args.force and (args.target != 'mdx' or route not in (
                'opna-fm-to-opm', 'native-opm-pcm', 'opm-segapcm-source', 'psg-scc-to-opm')):
            raise ValueError('--force requires a supported MDX source route and --target mdx')
        if args.fm_only and (args.target != 'mdx' or route != 'opm-segapcm-source'
                             or 'opm' not in usage['used_chips']):
            raise ValueError('--fm-only requires an OPM + SegaPCM input and --target mdx')
        if args.fm_only:
            report['chip_projection'] = 'opm-segapcm-fm-only'
            report['excluded_sources'] = [dict(chip='segapcm', reason='Explicit --fm-only diagnostic export')]
        elif route == 'opm-segapcm-source':
            report['chip_projection'] = 'opm-segapcm-pdx'
        pcm_applicable = (route == 'opna-fm-to-opm'
                          or route == 'opm-segapcm-source' and not args.fm_only
                          or route == 'native-opm-pcm' and 'pcm' in usage['used_chips'])
        report['pcm_options'] = dict(applicable=pcm_applicable, policy=args.pcm_policy or 'strict',
                                    generator_configured=bool(args.pcm_generator))
        if compatibility_target == 'opm-additive' and args.psg_model not in (None, 'additive'):
            raise ValueError('--target opm-additive requires the additive PSG model')
        if route == 'psg-scc-to-opm' and (args.notation == 'legacy' or args.track_layout != 'channels'):
            raise ValueError('PSG/SCC OPM projection supports structured/registers notation and channel tracks')
        if route in ('native-opm-pcm', 'opna-fm-to-opm', 'opm-segapcm-source') and any(v is not None for v in (args.psg_model, args.psg_gain, args.scc_gain)):
            raise ValueError('PSG/SCC projection options do not apply to native MDX')
        report['status'] = 'resolved'
        record_report()
        for chip, initialization in usage['compatibility_initialization'].items():
            print(f'Source {chip.upper()}: {initialization["reason"]} ({initialization["command_count"]} commands)')
        if not pcm_applicable and (args.pcm_policy is not None or args.pcm_generator):
            print('PCM options: not applicable to this input; configuration recorded in conversion report')
    except (OSError, ValueError) as error:
        report.update(status='blocked', reason=str(error))
        record_report()
        parser.error(str(error))

    if route == 'opna-fm-to-opm':
        from opna_opm import convert
        try:
            mml, settings = convert(vgm_path, song_dir, dump_passes=args.dump_passes or args.debug,
                track_layout=args.track_layout, notation=args.notation,
                loops=not args.no_loops, song_loop=not args.no_song_loop,
                title=args.title if args.title is not None else title_from_gd3(vgm_path, base_name, args.gd3_language),
                normalize_lengths=args.normalize_lengths, normalization_ms=args.normalization_ms,
                pcm_generator=args.pcm_generator, pcm_policy=args.pcm_policy or 'strict',
                pitch_policy=args.opm_pitch_policy or 'clamp', force=args.force)
        except (OSError, ValueError) as error:
            report.update(status='blocked', reason=str(error))
            record_report()
            parser.error(str(error))
        report.update(status='generated', projection_settings=settings)
        record_report()
        print(f'MDX MML: {mml}')
        forced = settings['force_projection']
        if forced['omitted_intervals']:
            print(f"WARNING: --force muted {forced['omitted_intervals']} unsupported SSG intervals; "
                  f"see {Path(song_dir) / forced['intervals_csv']}")
        if args.force:
            from opna_force import format_register_list, capacity_lines
            print('\n'.join(format_register_list(forced['skipped_registers'])))
            print('Register details: ' + str(Path(song_dir) / forced['registers_csv']))
            for line in capacity_lines(song_dir, base_name):
                print(line)
        allocation = settings['channel_allocation']
        for part in allocation['parts']:
            if part['status'] == 'assigned':
                print(f'OPNA {part["source_domain"].upper()} ch{part["source_ch"] + 1} -> '
                      f'OPM {part["target_track"]}')
            elif part['status'] == 'dropped':
                print(f'OPNA SSG ch{part["source_ch"] + 1} omitted: {part["reason"]}')
        for domain, count in settings['excluded_or_approximated'].items():
            print(f'OPNA projection: {domain} ({count} occurrences)')
        pcm_ir = settings['pcm_ir']
        print(f'OPNA PCM IR: {pcm_ir["native_segments"]} source segments, '
              f'{pcm_ir["target_samples"]} MSM6258 samples, '
              f'{pcm_ir["pdx_distinct_samples"]} distinct PDX samples, '
              f'{pcm_ir["ineligible_playbacks"]} ineligible playbacks')
        from opna_force import pcm_artifact_lines
        for line in pcm_artifact_lines(song_dir, base_name):
            print(line)
        assessment_path = Path(song_dir) / (base_name + '.pcm.assessment.json')
        if assessment_path.is_file():
            assessment = json.loads(assessment_path.read_text(encoding='utf-8'))
            print(f'PCM projection: {assessment["assessment_status"]}; validation: '
                  f'{assessment["validation_status"]} ({assessment["validation_run"]})')
            print(f'PCM known losses: {len(assessment["known_losses"])}; assessment: {assessment_path}')
        return

    if route == 'psg-scc-to-opm':
        from psg_scc_conversion import convert
        try:
            mml, plan = convert(vgm_path, song_dir, psg_gain=args.psg_gain,
                                scc_gain=.125 if args.scc_gain is None else args.scc_gain,
                                title=args.title if args.title is not None else title_from_gd3(vgm_path, base_name, args.gd3_language),
                                psg_model=args.psg_model or ('additive' if compatibility_target == 'opm-additive' else 'fm'),
                                pitch_policy=args.opm_pitch_policy,
                                dump_passes=args.dump_passes or args.debug,
                                notation=args.notation, loops=not args.no_loops, song_loop=not args.no_song_loop,
                                normalize_lengths=args.normalize_lengths, normalization_ms=args.normalization_ms,
                                force=args.force)
        except (OSError, ValueError) as error:
            report.update(status='blocked', reason=str(error))
            record_report()
            parser.error(str(error))
        report.update(status='generated', projection_settings=plan.settings)
        record_report()
        if args.force:
            from opna_force import force_console_lines
            for line in force_console_lines(song_dir, base_name):
                print(line)
        print(f'MDX MML: {mml}')
        if normalize:
            with open(os.path.join(song_dir, base_name + '.mdx.normalization.json'), encoding='utf-8') as stream:
                normalization = json.load(stream)
            print(f'Note normalization: {normalization["status"]} ({normalization["reason"]})')
        return

    if args.target == 'mdx':
        from opm_conversion import convert
        try:
            mml, _, _ = convert(vgm_path, song_dir, dump_passes=args.dump_passes or args.debug,
                                track_layout=args.track_layout, notation=args.notation,
                                loops=not args.no_loops, song_loop=not args.no_song_loop, title=args.title,
                                gd3_language=args.gd3_language, normalize_lengths=args.normalize_lengths,
                                pcm_generator=args.pcm_generator, pcm_policy=args.pcm_policy or 'strict',
                                pitch_policy=args.opm_pitch_policy or 'clamp',
                                normalization_ms=args.normalization_ms,
                                segapcm_source=route == 'opm-segapcm-source',
                                segapcm_pcm=route == 'opm-segapcm-source' and not args.fm_only,
                                force=args.force)
        except (OSError, ValueError) as error:
            report.update(status='blocked', reason=str(error))
            record_report()
            parser.error(str(error))
        report.update(status='generated', pcm_policy=args.pcm_policy or 'strict',
                      pitch_policy=args.opm_pitch_policy or 'clamp')
        record_report()
        if args.fm_only:
            print('SegaPCM omitted from target (--fm-only); native evidence retained. No PDX generated.')
        if args.force:
            from opna_force import capacity_lines
            for line in capacity_lines(song_dir, base_name):
                print(line)
        print(f'MDX MML: {mml}')
        from opna_force import pcm_artifact_lines
        for line in pcm_artifact_lines(song_dir, base_name):
            print(line)
        assessment_path = os.path.join(song_dir, base_name + '.pcm.assessment.json')
        if os.path.isfile(assessment_path):
            with open(assessment_path, encoding='utf-8') as stream:
                assessment = json.load(stream)
            print(f'PCM projection: {assessment["assessment_status"]}; validation: {assessment["validation_status"]} ({assessment["validation_run"]})')
            for loss in assessment['known_losses'][:5]:
                print(f'PCM known loss {loss["code"]}: samples {loss["start_vgmticks"]}..{loss["end_vgmticks"]}, '
                      f'{loss["source_value"]} -> {loss["projected_value"]} ({loss["fallback"]})')
            if len(assessment['known_losses']) > 5:
                print(f'PCM known losses: {len(assessment["known_losses"])} total')
            print(f'PCM assessment: {assessment_path}')
        if normalize:
            with open(os.path.join(song_dir, base_name + '.mdx.normalization.json'), encoding='utf-8') as stream:
                report = json.load(stream)
            print('Note normalization: ' + report['status'] + ' (' + report['reason'] + ')')
        return

    args.normalize_lengths = normalize
    args.enhance_macros = True if args.enhance_macros is None else args.enhance_macros
    args.sync_min_gap = 1000 if args.sync_min_gap is None else args.sync_min_gap
    args.psg_input = args.psg_input or 'trace'
    args.scc_input = args.scc_input or 'trace'
    # ── Step 1: Parse VGM → SCC + PSG + OPLL log/trace CSVs ──────
    opm_metadata = {}
    (psg_log_csv, scc_log_csv, psg_trace_csv, scc_trace_csv,
     opll_log_csv, opll_trace_csv, opll_voice_csv, opll_regs_csv) = parse_vgm(
         vgm_path, song_dir, dump_loop=args.debug or args.dump_passes,
         include_vgmticks=args.vgmticks or args.normalize_lengths,
         opm_metadata=opm_metadata, dump_opm_segments=args.debug or args.dump_passes)

    if args.debug:
        print(f"PSG log:       {psg_log_csv}")
        print(f"PSG trace:     {psg_trace_csv}")
        print(f"SCC log:       {scc_log_csv}")
        print(f"SCC trace:     {scc_trace_csv}")
        print(f"OPLL log:      {opll_log_csv}")
        print(f"OPLL trace:    {opll_trace_csv}")
        print(f"OPLL voice:    {opll_voice_csv}")
        print(f"OPLL regs:     {opll_regs_csv}")
        if opm_metadata['csv_path']:
            print(f"OPM regs:      {opm_metadata['csv_path']}")
            print(f"OPM state:     {opm_metadata['state_csv_path']}")
            print(f"OPM Segments:  {opm_metadata['segments_csv_path']}")

    # Detect chip presence from trace CSVs
    has_psg  = _has_chip_data(psg_trace_csv)
    has_scc  = _has_chip_data(scc_trace_csv)
    has_opll = _has_chip_data(opll_trace_csv)

    # ── Step 2: SCC MML pipeline ─────────────────────────────────
    if args.legacy_loops:
        envelope_bank = EnvelopeBank(deferred=True)
    else:
        from pre_envelope_loops import LoopFirstEnvelopeBank
        envelope_bank = LoopFirstEnvelopeBank(deferred=True)
    scc_csv = scc_trace_csv if args.scc_input == 'trace' else scc_log_csv

    scc_mml_path = process_scc_csv(scc_csv, song_dir, stem=base_name,
                                   dump_passes=args.dump_passes,
                                   debug=args.debug,
                                   raw_ticks=args.raw_ticks,
                                   envelope_bank=envelope_bank)
    if args.debug:
        print(f"SCC MML: {scc_mml_path}")

    # ── Step 3: PSG MML pipeline ─────────────────────────────────
    psg_csv = psg_trace_csv if args.psg_input == 'trace' else psg_log_csv

    psg_mml_path = process_psg_csv(psg_csv, song_dir, stem=base_name,
                                   dump_passes=args.dump_passes,
                                   debug=args.debug,
                                   raw_ticks=args.raw_ticks,
                                   envelope_bank=envelope_bank)
    if args.debug:
        print(f"PSG MML: {psg_mml_path}")

    envelope_bank.flush()

    from opll_mode import mode_from_trace
    opll_mode = mode_from_trace(opll_trace_csv)

    # ── Step 4: OPLL MML pipeline ────────────────────────────────
    opll_mml_path = process_opll_csv(opll_trace_csv, song_dir, stem=base_name,
                                     dump_passes=args.dump_passes,
                                     debug=args.debug,
                                     voice_csv_path=opll_voice_csv,
                                     raw_ticks=args.raw_ticks, source_loops=not args.legacy_loops,
                                     opll_mode=opll_mode)
    if args.debug:
        print(f"OPLL MML: {opll_mml_path}")

    # Optional target-only normalization; legacy passes and Segment dumps stay native.
    normalized_tempo = None
    if args.normalize_lengths:
        from note_normalization import normalize_outputs
        from psg import build_segments as build_psg
        from scc import build_segments as build_scc
        controls = {}
        if has_psg:
            controls['psg'] = build_psg(psg_csv, song_dir, base_name, dump_passes=False)
        if has_scc:
            controls['scc'] = build_scc(scc_csv, song_dir, dump_passes=False, stem=base_name).segments
        normalized_tempo = normalize_outputs(opll_trace_csv, opll_voice_csv, song_dir,
                                             base_name, args.dump_passes or args.debug, controls,
                                             opll_mode=opll_mode, source_loops=not args.legacy_loops)
        if normalized_tempo is not None:
            print(f'Note normalization: applied (tempo {normalized_tempo})')
        else:
            with open(os.path.join(song_dir, f'{base_name}.normalization.json'), encoding='utf-8') as stream:
                reason = json.load(stream)['reason']
            print(f'Note normalization: unchanged ({reason})')

    # ── Step 5: Build merged MML ──────────────────────────────────
    merged_text = _build_merged_mml(base_name, song_dir,
                                    has_psg, has_scc, has_opll,
                                    raw_ticks=args.raw_ticks,
                                    sync_min_gap=args.sync_min_gap,
                                    name=args.name, title=args.title, tempo=normalized_tempo, opll_mode=opll_mode)
    merged_text = override_alloc(merged_text, args.alloc)
    if args.enhance_macros:
        from structured_macros import enhance_macros
        merged_text = enhance_macros(merged_text,
            dump_prefix=os.path.join(song_dir, base_name) if args.dump_passes else None)
    merged_path = os.path.join(song_dir, f'{base_name}.mml')
    with open(merged_path, 'w', encoding='cp932', errors='replace', newline='\n') as fh:
        fh.write(merged_text)
    print(f"Merged MML: {merged_path}")
    report.update(status='generated')
    record_report()
    if not args.debug and not args.dump_passes:
        for chip in ('psg', 'scc', 'opll'):
            os.remove(os.path.join(song_dir, f'{base_name}.{chip}.target.mml'))

    # ── Step 6: Clean up intermediate files in non-debug mode ─────
    if not args.debug:
        # Remove log/trace CSVs written by parse_vgm (intermediate inputs)
        if not args.dump_passes:
            for csv_path in (psg_log_csv, psg_trace_csv,
                             scc_log_csv, scc_trace_csv,
                             opll_log_csv, opll_trace_csv,
                             opll_voice_csv, opll_regs_csv,
                             opm_metadata['csv_path']):
                if csv_path is None:
                    continue
                try:
                    os.remove(csv_path)
                except OSError:
                    pass
        # Every pipeline writes these variants, including absent chips.
        # Debug mode retains them; normal output must not leak empty-chip files.
        chips_to_clean = ('psg', 'scc', 'opll')

        for chip in chips_to_clean:
            for suffix in ('pass3.compress.MGS.mml', 'pass3.compress.MGS_pct.mml'):
                chip_path = os.path.join(song_dir, f'{base_name}.{chip}.{suffix}')
                try:
                    os.remove(chip_path)
                except OSError:
                    pass


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    selected='mucom'
    for index,argument in enumerate(arguments):
        if argument=='--target' and index+1<len(arguments):selected=arguments[index+1]
        elif argument.startswith('--target='):selected=argument.split('=',1)[1]
    if selected in ('mdx','mgs','opm','opm-additive'):
        previous=sys.argv
        try:
            sys.argv=[previous[0],*arguments]
            return _inherited_main()
        finally:
            sys.argv=previous
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vgm',help='Input supported VGM/VGZ')
    parser.add_argument('--target',choices=('mucom','mucomsx','mdx','mgs'),default='mucom',
                        help='mucom (default), mucomsx MSX musical MUC, or explicit inherited compatibility target')
    parser.add_argument('--outdir',default='outputs')
    parser.add_argument('--dump-passes',action='store_true',help='source/music/structure dumps are always retained')
    parser.add_argument('--diagnostics-outdir')
    parser.add_argument('--timing-bound-ms',type=float,default=8.0,
                        help='Baseline MUCOM boundary movement bound in milliseconds (default: 8)')
    normalization=parser.add_mutually_exclusive_group()
    normalization.add_argument('--normalize-lengths',dest='normalize_lengths',action='store_true',
                               help='Apply bounded musical output normalization (default)')
    normalization.add_argument('--no-normalize-lengths',dest='normalize_lengths',action='store_false',
                               help='Keep baseline target timing without output normalization')
    parser.set_defaults(normalize_lengths=True)
    parser.add_argument('--normalization-ms',type=float,default=8.0,
                        help='Output normalization bound in milliseconds (default: 8)')
    parser.add_argument('--force',action='store_true',help='Omit explicitly unsupported target effects and excess PCM; record every omission')
    parser.add_argument('--opm-channel-policy',choices=('auto','error','omit','dynamic'),default='auto',
                        help='OPM8 to OPNA6: auto uses dynamic six-voice allocation with force; error/omit retain fixed policy')
    parser.add_argument('--pcm-only',action='store_true',help='Explicitly extract MSM6258/SegaPCM; retain source evidence, exclude other engines')
    parser.add_argument('--pcm-profile',choices=('standard','low','ultra_low'),default='standard',
                        help='Derived MSM6258/SegaPCM PCM rate: standard, half-rate low or quarter-rate ultra_low')
    parser.add_argument('--pcm-policy',choices=('strict','best-effort'),default='strict',
                        help='External PCM transcoding requires explicit best-effort')
    parser.add_argument('--no-loops',action='store_true',help='expand finite phrase repetitions')
    parser.add_argument('--no-song-loop',action='store_true',help='emit the first source traversal without a song loop')
    parser.add_argument('--opll-rhythm',choices=('native','layered'),default='native',
                        help='MUCOMSX: native OPLL percussion, optionally also layer OPNA rhythm')
    parser.add_argument('--psg-enhance-cents',type=float,
                        help='MUCOMSX: add an OPNA SSG copy of native PSG at the specified detune (e.g. -11)')
    args=parser.parse_args(arguments)
    if args.target=='mucomsx':
        unsupported={'--pcm-only','--pcm-profile','--pcm-policy','--opm-channel-policy'}
        requested=sorted({flag for option in arguments for flag in unsupported
                          if flag.startswith(option.split('=',1)[0])})
        if requested:
            parser.error('--target mucomsx does not support '+', '.join(requested))
        from mucomsx_conversion import ConversionError,convert_vgm
    else:
        if args.opll_rhythm!='native' or args.psg_enhance_cents is not None:
            parser.error('Native MSX enhancement options require --target mucomsx')
        from opna_muc import ConversionError,convert_vgm
    options=dict(dump_passes=args.dump_passes,diagnostics_outdir=args.diagnostics_outdir,
                 timing_bound_ms=args.timing_bound_ms,loops=not args.no_loops,
                 song_loop=not args.no_song_loop,normalize_lengths=args.normalize_lengths,
                 normalization_ms=args.normalization_ms,force=args.force)
    if args.target=='mucomsx':
        options.update(opll_rhythm=args.opll_rhythm,psg_enhance_cents=args.psg_enhance_cents)
    if args.target=='mucom':
        options.update(pcm_only=args.pcm_only,pcm_policy=args.pcm_policy,
                       pcm_profile=args.pcm_profile,opm_channel_policy=args.opm_channel_policy)
    try:
        result=convert_vgm(args.vgm,args.outdir,**options)
    except (ConversionError,OSError) as error:
        if args.target=='mucom' and (args.force or args.opm_channel_policy=='omit'):
            from mucom_force import console_lines
            for line in console_lines(args.diagnostics_outdir or args.outdir,Path(args.vgm).stem):
                print(line)
        parser.exit(1,f'MUC conversion failed: {error}\n')
    if args.target=='mucom' and (args.force or args.opm_channel_policy=='omit'):
        from mucom_force import console_lines
        for line in console_lines(args.diagnostics_outdir or args.outdir,Path(args.vgm).stem):
            print(line)
    print('MUC: '+result['muc_path'])
    label='Musical source units' if args.target=='mucomsx' else 'Musical FM gates'
    print(f"{label}: {result['musical_note_units']}; Timer-B: {result['timing']['timer_b']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
