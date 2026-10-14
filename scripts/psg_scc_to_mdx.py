"""Batch diagnostic: PSG/SCC OPM projection and verification. Normal conversion: vgm2mml.py input.vgm --outdir OUTPUT."""
import argparse
import csv
import shutil
import subprocess
import tempfile
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT)]
from vgm_io import read_vgm_bytes, read_vgm_header
from vgm_timing import command_times
from vgm_reader import parse_vgm
from psg import build_segments as build_psg
from scc import build_segments as build_scc
from psg_scc_opm import project, verify_writes


from psg_scc_conversion import convert, source_facts


def compile_and_verify(generator, mml, plan, out, stem):
    # Imports also work when invoked by the repository's top-level entry point.
    from scripts.verify_opm_mdx_roundtrip import generate
    from opm_roundtrip import controls
    base = out / '_compiler_initialization'
    base.mkdir(exist_ok=True)
    initial = base / 'initialization.mml'
    initial.write_text('#title "Compiler initialization"\nA @t255 r%1\n', encoding='utf-8')
    init = controls(generate(generator.resolve(), initial, base, 'initialization'))
    if any(sample != 0 or reg == 8 for sample, reg, data in init):
        raise ValueError('Unexpected compiler initialization; comparison refused')
    context = plan.structured_context
    limit = context.projection.end_mdx_tick if context else plan.end_tick
    actual = generate(generator.resolve(), mml, out, stem, max_ticks=max(2, limit + 1))
    if context:
        from opm_performance_verify import compare_performance
        report = compare_performance(context, actual, initialization=init)
    else:
        report = verify_writes(plan, controls(actual), init, actual.source_end_vgmticks)
    (out / (stem + '.verification.json')).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if not report['passed']:
        raise RuntimeError('Generated target controls did not survive the MDX roundtrip')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='PSG/SCC VGM or directory to scan recursively')
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--psg-gain', type=float, default=None, help='PSG gain: default fm=1, additive=0.125')
    parser.add_argument('--psg-model', choices=['fm', 'additive'], default='fm',
                        help='PSG tone model (default: fm); SCC remains additive')
    parser.add_argument('--opm-pitch-policy', choices=['clamp', 'error'], default=None,
                        help='Range policy: default fm=clamp, additive=error')
    parser.add_argument('--scc-gain', type=float, default=.125)
    parser.add_argument('--notation', choices=['structured', 'registers'], default='structured')
    parser.add_argument('--no-loops', action='store_true')
    parser.add_argument('--normalize-lengths', action=argparse.BooleanOptionalAction, default=None,
                        help='Safe target-clock correction (default: ON for structured MDX)')
    parser.add_argument('--generator', type=Path, help='External MDX compiler/player; retain MDX/VGM and verify')
    parser.add_argument('--comparison-dir', type=Path, help='Optional existing OPM VGM tree, matched by relative path')
    args = parser.parse_args()
    if not args.input.exists():
        parser.error('Input does not exist')
    if args.generator and not args.generator.is_file():
        parser.error('Generator does not exist')
    files = sorted(p for p in args.input.rglob('*') if p.suffix.lower() in ('.vgm', '.vgz')) if args.input.is_dir() else [args.input]
    if not files:
        parser.error('No VGM inputs found')
    args.outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for source in files:
        relative = source.relative_to(args.input) if args.input.is_dir() else Path(source.name)
        out = args.outdir / relative.with_suffix('') if args.input.is_dir() else args.outdir
        row = dict(input=str(source), status='', detail='', mml='', mdx='', vgm='', comparison_vgm='')
        try:
            mml, plan = convert(source, out, psg_gain=args.psg_gain, scc_gain=args.scc_gain,
                                psg_model=args.psg_model, pitch_policy=args.opm_pitch_policy,
                                notation=args.notation, loops=not args.no_loops,
                                normalize_lengths=args.normalize_lengths)
            row.update(status='mml_only', mml=str(mml))
            if args.generator:
                report = compile_and_verify(args.generator, mml, plan, out, source.stem)
                row.update(status='verified', mdx=str(out / (source.stem + '.mdx')),
                           vgm=str(out / (source.stem + '.vgm')),
                           detail='Target OPM state/Key comparison passed; not acoustic equivalence')
            if args.comparison_dir:
                reference = args.comparison_dir / relative
                if reference.is_file():
                    target = out / (source.stem + '.vgm-conv.vgm')
                    shutil.copyfile(reference, target)
                    row['comparison_vgm'] = str(target)
        except ValueError as error:
            row.update(status='unsupported', detail=str(error))
        except subprocess.TimeoutExpired as error:
            row.update(status='timeout', detail=str(error))
        except (RuntimeError, OSError) as error:
            row.update(status='failed', detail=str(error))
        results.append(row)
        print(f"{relative}: {row['status']} {row['detail']}")
    (args.outdir / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    with (args.outdir / 'results.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    if any(r['status'] not in ('verified', 'mml_only') for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
