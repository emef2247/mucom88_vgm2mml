"""Authored FAT12 and mocked emulator checks; no ROM/DOS image is required."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'py')]
import mucomsx_runtime as runtime


def disk(files):
    raw = bytearray(2880 * 512)
    struct.pack_into('<HBHBHHBHH', raw, 11, 512, 1, 1, 2, 224, 2880, 0xf0, 9, 18)
    fat = bytearray(9 * 512);fat[:3] = b'\xf0\xff\xff'
    root_start = 19 * 512;data_start = 33 * 512;cluster = 2
    def link(index, value):
        offset = index + index // 2
        word = int.from_bytes(fat[offset:offset + 2], 'little')
        word = (word & 15) | value << 4 if index & 1 else (word & 0xf000) | value
        fat[offset:offset + 2] = word.to_bytes(2, 'little')
    for index, (name, content) in enumerate(files.items()):
        base, _, ext = name.partition('.')
        start = root_start + index * 32
        raw[start:start + 11] = (base.ljust(8) + ext.ljust(3)).encode('ascii')
        raw[start + 11] = 0x20
        struct.pack_into('<H', raw, start + 26, cluster if content else 0)
        struct.pack_into('<I', raw, start + 28, len(content))
        for position in range(0, len(content), 512):
            piece = content[position:position + 512]
            offset = data_start + (cluster - 2) * 512
            raw[offset:offset + len(piece)] = piece
            link(cluster, cluster + 1 if position + 512 < len(content) else 0xfff)
            cluster += 1
    raw[512:10 * 512] = fat;raw[10 * 512:19 * 512] = fat
    return bytes(raw)


def vgm():
    raw = bytearray(0x100)
    raw[:4] = b'Vgm '
    struct.pack_into('<I', raw, 8, 0x171)
    struct.pack_into('<I', raw, 0x18, 1000)
    struct.pack_into('<I', raw, 0x34, 0x100 - 0x34)
    raw += bytes.fromhex('61 e8 03 66')
    struct.pack_into('<I', raw, 4, len(raw) - 4)
    return bytes(raw)


def outputs():
    return {'CHECK.MUB': b'MUB8' + bytes(16),
            'SESSION.MUB': b'msPb0101' + bytes(16),
            'CHECK.VGM': vgm(), 'DONE.OK': b'FINISHED\r\n'}


class RuntimeTests(unittest.TestCase):
    def test_fat12_reads_only_allowlisted_generated_regular_files(self):
        source = outputs();source['SECRET.TXT'] = b'PRIVATE SYSTEM DATA'
        found = runtime.fat12_files(disk(source))
        self.assertEqual(found, outputs())
        self.assertNotIn('SECRET.TXT', found)
        self.assertEqual(runtime.fat12_files(disk(source), ('SECRET.TXT',)),
                         {'SECRET.TXT': b'PRIVATE SYSTEM DATA'})

    def test_fat12_rejects_cycle_truncation_and_invalid_geometry(self):
        raw = bytearray(disk({'CHECK.MUB': b'MUB8' + bytes(600)}))
        # Cluster 2 points to itself while root size requires a second cluster.
        raw[515:517] = b'\x02\x00'
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            runtime.fat12_files(raw)
        with self.assertRaisesRegex(ValueError, 'Truncated image'):
            runtime.fat12_files(disk(outputs())[:-1])
        raw = bytearray(disk(outputs()));raw[13] = 3
        with self.assertRaisesRegex(ValueError, 'geometry'):
            runtime.fat12_files(raw)

    def test_valid_containers_require_final_vgm_header_duration_and_end(self):
        self.assertEqual(runtime.validate_artifacts(outputs())['vgm_samples'], 1000)
        for mutation in ('zero', 'eof', 'samples', 'end', 'mub'):
            files = outputs();raw = bytearray(files['CHECK.VGM'])
            if mutation == 'zero':raw[:0x100] = bytes(0x100)
            elif mutation == 'eof':raw[4:8] = bytes(4)
            elif mutation == 'samples':raw[0x18:0x1c] = bytes(4)
            elif mutation == 'end':raw[-1] = 0x62
            else:files['CHECK.MUB'] = b'BROKEN'
            files['CHECK.VGM'] = bytes(raw)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                runtime.validate_artifacts(files)

    def test_completion_marker_waits_for_valid_stable_headers(self):
        clock = [0.0]
        incomplete = outputs();incomplete['CHECK.VGM'] = bytes(0x100)
        snapshots = [incomplete, outputs(), outputs(), outputs()]
        def read(*args):return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]
        proc = Mock();proc.poll.return_value = None
        with patch.object(runtime, 'fat12_files', side_effect=read), \
             patch.object(runtime.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(runtime.time, 'sleep', side_effect=lambda n: clock.__setitem__(0, clock[0] + n)):
            result, proof = runtime._wait_for_artifacts(proc, 'unused', 10)
        self.assertEqual(proof['vgm_samples'], 1000)
        self.assertGreaterEqual(clock[0], 3)
        self.assertTrue(result['DONE.OK'])

    def test_compile_only_requires_valid_stable_mub_without_vgm(self):
        files = {'CHECK.MUB': b'msPb0101' + bytes(16), 'DONE.OK': b'FINISHED'}
        self.assertIn('mub_signatures', runtime.validate_artifacts(files, compile_only=True))
        for bad in (b'', b'BROKEN'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                runtime.validate_artifacts({'CHECK.MUB': bad}, compile_only=True)
        clock = [0.0]
        proc = Mock();proc.poll.return_value = None
        with patch.object(runtime, 'fat12_files', return_value=files), \
             patch.object(runtime.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(runtime.time, 'sleep', side_effect=lambda n: clock.__setitem__(0, clock[0] + n)):
            _, proof = runtime._wait_for_artifacts(proc, 'unused', 10, compile_only=True)
        self.assertNotIn('vgm_samples', proof)
        self.assertGreaterEqual(clock[0], 2)

    def test_failed_marker_and_early_exit_are_failures(self):
        proc = Mock();proc.poll.return_value = None
        with patch.object(runtime, 'fat12_files', return_value={'FAILED.ERR': b''}):
            with self.assertRaisesRegex(RuntimeError, 'FAILED.ERR'):
                runtime._wait_for_artifacts(proc, 'unused', 1)
        proc.poll.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'exited'):
            runtime._wait_for_artifacts(proc, 'unused', 1)

    def test_mocked_job_preserves_inputs_and_exports_only_generated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);source = base / 'source.muc';source.write_bytes(b'D c4\r\n')
            image = base / 'boot.dsk';original = disk({'SECRET.TXT': b'private'});image.write_bytes(original)
            work = base / 'result'
            def stage(src, out, tools):
                out.mkdir();(out / 'SOURCE.MUC').write_bytes(source.read_bytes())
                (out / 'RUN.BTM').write_bytes(b'ECHO RUN\r\n');(out / 'MUCPLAY.COM').write_bytes(b'program')
                (out / 'job.json').write_text('{}')
                return {'status': 'staged_not_executed'}
            def prepare(command, **kwargs):
                files = {p.name:p.read_bytes() for p in (work / 'flat').iterdir()}
                files['SECRET.TXT'] = b'private'
                (work / 'work.dsk').write_bytes(disk(files))
                return Mock(returncode=0, stdout=b'prepared', stderr=b'warning')
            def wait(proc, path, timeout):
                files = outputs();files['SECRET.TXT'] = b'private'
                Path(path).write_bytes(disk(files))
                return outputs(), runtime.validate_artifacts(outputs())
            proc = Mock();proc.poll.return_value = None;proc.wait.return_value = -15
            with patch.object(runtime, 'stage_score', side_effect=stage), \
                 patch.object(runtime.subprocess, 'run', side_effect=prepare), \
                 patch.object(runtime.subprocess, 'Popen', return_value=proc), \
                 patch.object(runtime, '_wait_for_artifacts', side_effect=wait):
                report = runtime.run_job(source, work, boot_disk=image, rom_path=base / 'roms', extension='LOCAL_ROM')
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(image.read_bytes(), original)
            self.assertEqual(source.read_bytes(), b'D c4\r\n')
            self.assertEqual(set(report['files']), set(outputs()))
            self.assertFalse((work / 'artifacts/SECRET.TXT').exists())
            self.assertIn('set renderer none', (work / 'boot.tcl').read_text())
            self.assertIn(b'\r\nRUN.BTM\r\n', (work / 'flat/AUTOEXEC.BAT').read_bytes())
            self.assertNotIn(b'CALL ', (work / 'flat/AUTOEXEC.BAT').read_bytes())
            self.assertNotIn(b'job.json', (work / 'flat/AUTOEXEC.BAT').read_bytes())
            self.assertEqual(json.loads((work / 'validation.json').read_text())['status'], 'passed')
            proc.terminate.assert_called_once()

    def test_mocked_preparation_failure_is_persisted_without_native_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);source = base / 'source.muc';source.write_bytes(b'D c4')
            image = base / 'boot.dsk';image.write_bytes(disk({}))
            def stage(src, out, tools):out.mkdir();return {}
            with patch.object(runtime, 'stage_score', side_effect=stage), \
                 patch.object(runtime.subprocess, 'run', return_value=Mock(returncode=1,stdout=b'',stderr=b'failure')):
                report = runtime.run_job(source, base / 'result', boot_disk=image)
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['failed_stage'], 'disk_preparation')
            self.assertNotIn('validation', report)

    def test_configuration_quoting_and_profile_reject_unknown_fields(self):
        quoted = runtime._tcl_word('a$[b]"c')
        self.assertIn('\\$', quoted);self.assertIn('\\[', quoted);self.assertIn('\\"', quoted)
        with self.assertRaisesRegex(ValueError, 'NUL'):
            runtime._tcl_word('bad\x00path')
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'local.json';profile.write_text('{"unknown": 1}')
            with self.assertRaises(SystemExit):
                runtime.main(['song.muc','--outdir',str(Path(directory)/'out'),'--profile',str(profile)])


if __name__ == '__main__':unittest.main()
