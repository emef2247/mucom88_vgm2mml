"""VGM -> OPM Segments -> MDX MML -> external MDX/VGM -> OPM Segments."""
import argparse
import csv
import json
from pathlib import Path
import struct
import subprocess
import sys
from mdx_compiler import compile_mxc, compiler_evidence_paths
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'py'))
from opm import build_segments, dump_analysis
from opm_roundtrip import controls, compare
from vgm_io import read_vgm_bytes, read_vgm_header
from vgm_reader import _OpmTrace, parse_vgm
from opm_conversion import convert
from opm_mdx import scheduled_projection
from conversion_config import inspect_source, select_mdx_route


def source_files(path):
    if not path.is_dir():
        return [path]
    # Expected replay evidence must not become a new source fixture.
    return sorted(p for p in path.rglob('*')
                  if p.is_file() and p.suffix.lower() in ('.vgm', '.vgz')
                  and not {'reference', 'mdx_roundtrip'}.intersection(p.relative_to(path).parts))


def source_facts(path, *, include_pcm=False):
    raw = read_vgm_bytes(path)
    if len(raw) < 0x40 or raw[:4] != b'Vgm ':
        raise ValueError('Not a complete VGM header')
    # Reuse the native reader's version-aware clock/variant interpretation.
    trace = _OpmTrace(raw, struct.unpack_from('<I', raw, 8)[0])
    header=read_vgm_header(raw)
    pcm_clock=header['msm6258_clock_raw'] & 0x3fffffff
    result=dict(clock_hz=trace.clock_hz, chip_type=trace.chip_type, dual_chip=trace.dual_chip)
    if include_pcm:
        result.update(source_pcm_present=bool(pcm_clock), comparison_scope='OPM only; PCM is not compared')
    return result


def generate(generator, mml, folder, stem, *, max_ticks=None,
             compiler='mmlx', mxc=None, run68=None):
    # Keep existing diagnostic callers compatible; the CLI selects MXC by default.
    if compiler not in ('mxc', 'mmlx'):
        raise ValueError('Compiler must be mxc or mmlx')
    mdx, vgm = folder / (stem + '.mdx'), folder / (stem + '.vgm')
    prepared = folder / (stem + '.mxc.mml')
    for path in (mdx, vgm, prepared, *compiler_evidence_paths(prepared)):
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError(f'Compiler output path leaves its directory: {path}')
        path.unlink(missing_ok=True)
    command = [str(generator), str(mml), str(mdx), str(vgm)]
    log = folder / (stem + '.compile.log')
    try:
        if compiler == 'mxc':
            compile_mxc(mml, mdx, timeout=180, mxc=mxc, run68=run68,
                        generator=generator, prepared_output=prepared)
            command = [str(generator), '--from-mdx', str(mdx), str(vgm)]
        if max_ticks is not None:
            command.extend(['--max-ticks', str(max_ticks)])
        run = subprocess.run(command, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=180)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
        log.write_text(decoded(error.stdout) + decoded(error.stderr)
                       + '\nExternal compiler/player exceeded 180 seconds\n', encoding='utf-8')
        raise
    except (ValueError, RuntimeError, OSError) as error:
        log.write_text(str(error) + '\n', encoding='utf-8')
        raise
    log.write_text(run.stdout + run.stderr, encoding='utf-8')
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)
    metadata = {}
    analysis_dir=folder/(stem+'_segments')
    parse_vgm(str(vgm), str(analysis_dir), opm_metadata=metadata)
    if metadata['clock_hz'] != 4000000:
        raise ValueError('Returned VGM does not declare the required 4 MHz OPM clock')
    analysis=build_segments(metadata['csv_path'], end_vgmticks=metadata['source_end_vgmticks'])
    dump_analysis(analysis,state_csv=analysis_dir/(stem+'_trace.opm.csv'),
                  segments_csv=analysis_dir/(stem+'.opm.segments.csv'))
    return analysis


def save_results(folder, rows):
    (folder / 'results.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (folder / 'results.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='OPM VGM or directory to scan recursively')
    parser.add_argument('--outdir', type=Path, default=ROOT / 'outputs/opm/mdx_roundtrip')
    suffix = '.exe' if sys.platform == 'win32' else ''
    parser.add_argument('--notation', choices=('structured', 'legacy', 'registers'), default='structured')
    parser.add_argument('--no-loops', action='store_true')
    parser.add_argument('--normalize-lengths', action=argparse.BooleanOptionalAction, default=None,
                        help='Safe target-clock correction (default: ON for structured MDX)')
    parser.add_argument('--title')
    parser.add_argument('--reference-mml-only', action='store_true', help='Select VGMs with a same-stem reference MML')
    parser.add_argument('--mdxdump',type=Path,help='External mdxtools executable for reference/generated MDX metadata audit')
    parser.add_argument('--track-layout', choices=('channels', 'conductor'), default='channels')
    parser.add_argument('--generator', type=Path, default=ROOT / 'scripts/mdx_fixture_generator/target/release' / ('mdx-fixture-generator' + suffix))
    parser.add_argument('--compiler', choices=('mxc', 'mmlx'), default='mxc',
                        help='FM MML compiler (default mxc); comparator is unchanged')
    parser.add_argument('--mxc', type=Path, help='Native MXC.X compiler')
    parser.add_argument('--run68', type=Path, help='run68 executable for native MXC')
    args = parser.parse_args()
    if args.normalize_lengths and args.notation != 'structured':
        parser.error('--normalize-lengths requires --notation structured')
    generator = args.generator.resolve()
    if not generator.is_file():
        parser.error('Build the external MDX fixture generator or pass --generator')
    args.outdir.mkdir(parents=True, exist_ok=True)
    baseline_dir = args.outdir / '_compiler_initialization'
    baseline_dir.mkdir(exist_ok=True)
    baseline_mml = baseline_dir / 'initialization.mml'
    baseline_mml.write_text('#title "Compiler initialization"\nA @t255 r%1\n', encoding='utf-8')
    compiler_options = dict(compiler=args.compiler, mxc=args.mxc, run68=args.run68)
    try:
        initialization = controls(generate(generator, baseline_mml, baseline_dir,
                                           'initialization', **compiler_options))
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        parser.error(f'Compiler/player initialization failed: {error}')
    if any(sample != 0 or reg == 8 for sample, reg, data in initialization):
        raise ValueError('Compiler baseline contains timed controls or Key writes')
    sources = source_files(args.input)
    def reference(source,suffix):
        folder=source.parent/'reference'
        return next((p for p in folder.iterdir() if p.is_file() and p.stem.lower()==source.stem.lower()
                     and p.suffix.lower()==suffix),None) if folder.is_dir() else None
    if args.reference_mml_only:
        sources=[p for p in sources if reference(p,'.mml') is not None]
    if not sources:
        parser.error('No input VGM found')
    rows = []
    for source in sources:
        relative = source.relative_to(args.input) if args.input.is_dir() else Path(source.name)
        folder = args.outdir / relative.with_suffix('')
        folder.mkdir(parents=True, exist_ok=True)
        row = {'input': str(relative), 'status': 'conversion_failed', 'compiler': args.compiler}
        phase='conversion'
        try:
            # A failed rerun must not attribute prior evidence to this compiler.
            for old in (folder / (source.stem + '.mdx.mml'),
                        *(folder / ('returned' + suffix)
                          for suffix in ('.mdx', '.vgm', '.mxc.mml', '.mxc.mdx', '.mxc.metadata.json', '.compile.log')),
                        folder / 'conversion.log'):
                if not old.resolve().is_relative_to(args.outdir.resolve()):
                    raise ValueError(f'Compiler output path leaves the output directory: {old}')
                old.unlink(missing_ok=True)
            row.update(source_facts(source, include_pcm=True))
            row['source_usage'] = inspect_source(source)
            if select_mdx_route(row['source_usage']) != 'native-opm-pcm':
                row['status'] = 'unsupported_target'
                raise ValueError('This roundtrip verifier requires native OPM/PCM source commands')
            if row['clock_hz'] <= 0 or row['chip_type'] != 'YM2151' or row['dual_chip']:
                row['status'] = 'unsupported_target'
                raise ValueError('MDX control replay requires one YM2151 with a positive source clock')
            mml, source_analysis, projection = convert(source, folder, dump_passes=True, track_layout=args.track_layout,
                                                        notation=args.notation, loops=not args.no_loops,title=args.title,
                                                        normalize_lengths=args.normalize_lengths,
                                                        pcm_generator=args.generator)
            tolerance = 6
            correction = json.loads((folder / (source.stem + '.mdx.normalization.json')).read_text(encoding='utf-8'))
            row['length_normalization'] = correction
            if correction['status'] == 'applied':
                tolerance = correction['correction_bound_samples']
            phase='compile_replay'
            returned = generate(generator, mml, folder, 'returned',
                                max_ticks=max(2, projection.end_mdx_tick + 1), **compiler_options)
            phase='comparison'
            scheduled = scheduled_projection(projection, track_layout=args.track_layout)
            if args.notation in ('structured', 'legacy'):
                from opm_mdx_structure import compare_hybrid
                from opm_target_pruning import omission_comparison_analysis
                omitted = correction.get('short_note_policy', {}).get('omitted_gates', []) if correction.get('short_note_omission_adopted') else []
                expected_analysis = omission_comparison_analysis(source_analysis, omitted)
                result = compare_hybrid(projection, expected_analysis, returned, initialization=initialization,
                                        source_timing_tolerance_samples=tolerance)
                result.update(intentional_omitted_gates=len(omitted),
                              exact_source_equivalence=not omitted and correction['status'] != 'applied'
                                  and projection.source_clock_hz == projection.clock_hz)
            else:
                result = compare(scheduled, source_analysis.segments, returned, initialization=initialization)
            result['track_layout'] = args.track_layout
            result['source_write_order_preserved'] = [w.source_event_id for w in scheduled.writes] == [w.source_event_id for w in projection.writes]
            row.update(result)
            row.update(projection.timing_report())
            row['notation'] = args.notation
            if args.notation in ('structured', 'legacy'):
                row.update(json.loads((folder / (source.stem + '.mdx.timing.json')).read_text(encoding='utf-8')))
            row['status'] = 'success' if result['passed'] else 'comparison_failed'
            if args.mdxdump:
                original=reference(source,'.mdx')
                if original is None:
                    row['metadata_audit']='reference_missing'
                else:
                    from audit_mdx_metadata import audit
                    try:
                        extra=audit(args.mdxdump,original,folder/'returned.mdx',mml,folder/'metadata')
                        row.update(metadata_audit='completed',
                            reference_title_matches=extra['title_matches_reference'],
                            reference_tempo_bytes_match=extra['tempo_bytes_match_reference'],
                            generated_metadata_visible_in_mml=extra['compiled_title_matches_mml'] and extra['generated_tempo_visible_in_mml'])
                    except (ValueError,RuntimeError,OSError) as error:
                        row.update(metadata_audit='failed',metadata_error=str(error))
        except subprocess.TimeoutExpired:
            row['status'] = 'compile_timeout'
            row['error'] = 'External compiler exceeded 180 seconds'
        except (ValueError, RuntimeError, OSError) as error:
            row['error'] = str(error)
            row['failure_phase']=phase
            if phase=='compile_replay':
                row['status']='compile_or_replay_failed'
        for label, path in [('mml', folder / (source.stem + '.mdx.mml')),
                            ('mdx', folder / 'returned.mdx'), ('vgm', folder / 'returned.vgm'),
                            ('compile_log', folder / 'returned.compile.log')]:
            if path.is_file():
                row[label] = str(path.resolve())
        compiler_input = ((folder / 'returned.mxc.mml' if args.compiler == 'mxc' else mml)
                          if phase != 'conversion' else None)
        if compiler_input is not None and compiler_input.is_file():
            row['compiler_input'] = str(compiler_input.resolve())
        if args.compiler == 'mxc' and phase != 'conversion':
            for label, evidence in zip(('compiler_native_mdx', 'compiler_metadata'),
                                       compiler_evidence_paths(folder / 'returned.mxc.mml')):
                if evidence.is_file():
                    row[label] = str(evidence.resolve())
        (folder / 'comparison.json').write_text(json.dumps(row, indent=2) + '\n', encoding='utf-8')
        if row.get('error'):
            (folder / 'conversion.log').write_text(row['error'] + '\n', encoding='utf-8')
        rows.append(row)
        print(str(relative) + ': ' + row['status'], flush=True)
        save_results(args.outdir, rows)
    return int(any(row['status'] != 'success' or row.get('metadata_audit')=='failed' for row in rows))


if __name__ == '__main__':
    raise SystemExit(main())
