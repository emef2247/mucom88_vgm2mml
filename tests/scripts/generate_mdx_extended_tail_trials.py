"""Apply the reference extended PCM mode to six preserved tail trials."""
import argparse
import csv
import hashlib
import json
import io
from pathlib import Path
import shutil

from generate_local_mdx_reference_phrases import private_destination
from generate_mdx_mode_order_trials import run, verify_mode
from mdx_pdx_structure_audit import audit, export
from mdx_reference_expectations import read_mdx

ROOT = Path(__file__).resolve().parents[2]
CASES = ('RATES', 'RATESF', 'RATESP', 'FS432', 'FS432F', 'FS432P')


def generate(out, probe):
    out = private_destination(out)
    archived = out/'original_standard9'
    archived.mkdir(exist_ok=True)
    for name in [*(stem+ext for stem in CASES for ext in ('.MDX','.MML')),
                 'PUBPCM.PDX','FF4SIREN.PDX','validation.json','listening_results.json',
                 'listening_observations.csv','README.md']:
        source, target = out/name, archived/name
        # Preserve the failed nine-track baseline exactly once; reruns always
        # consume it instead of treating a previously extended file as input.
        if not target.exists():
            shutil.copy2(source,target)
    staging = out/'_extended_generation'
    staging.mkdir(exist_ok=True)
    cases = []
    for stem in CASES:
        source, target = archived/(stem+'.MDX'), staging/(stem+'.MDX')
        before = read_mdx(source.read_bytes())
        log = run([probe,'mdx16',source,target])
        after = read_mdx(target.read_bytes(),allow_pcm8=True)
        verify_mode(before,after,True)
        pdx_name = 'PUBPCM.PDX' if stem.startswith('RATES') else 'FF4SIREN.PDX'
        if (archived/pdx_name).read_bytes() != (out/pdx_name).read_bytes():
            raise ValueError('Fixed PDX changed')
        result = audit(target,out/pdx_name,1)
        if result['invalid_pcm_bindings'] or result['invalid_pdx_slots']:
            raise ValueError('Invalid sample binding')
        export(result,staging/'expected'/stem)
        (staging/(stem+'.log')).write_text(log)
        cases.append(dict(stem=stem,source_mdx_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            mdx_sha256=result['mdx_sha256'],pdx_sha256=result['pdx_sha256'],
            tracks=16,pcm8_enable_commands=len(result['pcm_mode_commands']),
            end_tick=max(t['duration_ticks'] for t in after['tracks']),
            commands_notes_controls_tones_metadata='preserved_except_initial_E8_and_inactive_Q_to_W',
            static='pass',native='unverified'))
    # Publish only after all six original-command and sample checks pass.
    for stem in CASES:
        shutil.copy2(staging/(stem+'.MDX'),out/(stem+'.MDX'))
        shutil.copytree(staging/'expected'/stem,out/'expected_extended'/stem,dirs_exist_ok=True)
    info = run([ROOT/'outputs/mdx-reference-tools/mdxtools/mdxinfo','-u','-H',
                *[out/(stem+'.MDX') for stem in CASES]])
    inspected = list(csv.DictReader(io.StringIO(info),delimiter='\t'))
    if len(inspected) != len(CASES) or any(r['Error']!='Success' or r['Tracks']!='16' or r['PCM8']!='1' or not r['PDX file'] for r in inspected):
        raise ValueError('mdxinfo rejected an extended case or failed PDX resolution')
    (out/'mdxinfo_extended.tsv').write_text(info)
    validation = dict(schema='extended-tail-mode-trials-v1',cases=cases,
        pdx='unchanged',mml='unchanged; native MXC source precedes explicit typed mode projection',
        toolchain='native MXC v1.01 -> soundlog0.15.0 typed MdxDocument extended-mode projection; existing exact PDX',
        production_changed=False,native_cessation='unverified')
    (out/'validation_extended.json').write_text(json.dumps(validation,indent=2)+'\n')
    (out/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    (out/'listening_results.json').write_text(json.dumps(dict(schema='extended-tail-listening-v1',
        cases={c['stem']:dict(mdx_sha256=c['mdx_sha256'],natural_end='unverified',display='unverified',
            sustained_silence='unverified') for c in cases},
        previous_failed_generation='original_standard9/listening_results.json'),indent=2)+'\n')
    with (out/'listening_observations.csv').open('w',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['case','display','audible','natural_end','sustained_silence','notes'])
        writer.writerows((stem,'unverified','unverified','unverified','unverified','') for stem in CASES)
    (out/'README.md').write_text(
        '# Extended-mode finite-ending trials\n\n'
        'The six root MDX files are newly generated16-track+E8 diagnostic versions. '
        'Copy all six MDX plus PUBPCM.PDX and FF4SIREN.PDX together for listening. '
        'The filenames and titles are retained, so replace old copies rather than mixing '
        'generations. Original failed9-track files and listening evidence are retained '
        'under original_standard9/.\n\n'
        'Only the target mode is changed: add Q..W inactive tracks and an initial E8 '
        'PCM4/8-enable command. Existing note/control order, duration, release requests, '
        'voice data, title, PDX reference and every PDX byte are verified unchanged. '
        'This deliberately changes the PCM playback route; it is not only an offset-table '
        'formatting change. No physical cessation is claimed until user listening.\n\n'
        'The source MML is unchanged; compiler_inputs/ contains the original native-MXC '
        'standard9 stage, not a direct16-track MML compilation. The reproducible chain '
        'is native MXC -> soundlog0.15 typed mode projection. Validation and source/final '
        'hashes are in validation_extended.json; command/sample CSVs in expected_extended/; '
        'mdxinfo_extended.tsv describes the new generation.\n\n'
        'Check display, audible music, natural ending and silence after ending; keep repeat '
        'disabled. Do not copy original_standard9/ MDX into the same playback folder. '
        'Ordering investigation and production conversion changes are paused. Private '
        'reference-derived music and samples must not be redistributed.\n')
    print(json.dumps(dict(cases=cases,pdx='unchanged',native='unverified'),indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outdir',type=Path,default=ROOT/'outputs/listen/finite_end_tail')
    parser.add_argument('--probe',type=Path,default=ROOT/'tests/scripts/mdx_structure_probe/target/release/typed-structure-probe')
    args=parser.parse_args()
    generate(args.outdir,args.probe)


if __name__=='__main__':
    main()
