"""FM target primitives, including native driver parameter control order."""
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'py'), str(ROOT / 'scripts')]
from mucom_fm import project_state, fm_voice, am_restore_commands, TARGET_CLOCK_HZ
from opna import OpnaRegisterState
from mucom_compiler import compile_muc
from vgm_timing import command_times


def state():
    registers = OpnaRegisterState()
    for bank in range(4):
        for base, value in ((0x30, 1), (0x40, 17 + bank * 11), (0x50, 31),
                            (0x60, 136), (0x70, 3), (0x80, 15), (0x90, 0)):
            registers.write(base + bank * 4, value)
    for reg, val in ((0xb0, 7), (0xb4, 226), (0x22, 11), (0xa4, 26), (0xa0, 106), (0x28, 240)):
        registers.write(reg, val)
    return registers.snapshot(0)


class MucomFmTests(unittest.TestCase):
    def test_initial_exact_tl_and_am_bank(self):
        source, voices = state(), {}
        projected = project_state(source, TARGET_CLOCK_HZ, voices, initial=True)
        self.assertEqual(projected.source_tl, projected.target_tl)
        self.assertIn('yTL,3,28', projected.commands)
        self.assertIn('H3,2,2', projected.commands)
        self.assertEqual(fm_voice(source)[13:17], bytes([136] * 4))
        self.assertEqual(projected.frequency_error_hz, 0)
        self.assertEqual(source, state())

    def test_alternate_source_clock_preserves_frequency(self):
        projected = project_state(state(), 8_000_000, {}, initial=True)
        self.assertNotEqual(projected.target_fnum, state().fnum)
        self.assertLess(abs(projected.frequency_error_hz), 1)

    def test_held_operator_change_does_not_reload_voice(self):
        before = state()
        ops = list(before.operators)
        ops[0] = replace(ops[0], tl=3, mul=7)
        after = replace(before, operators=tuple(ops), algorithm=2, feedback=3)
        result = project_state(after, TARGET_CLOCK_HZ, {}, initial=False, previous_state=before)
        self.assertIn('yTL,1,3', result.commands)
        self.assertIn('yDM,1,7', result.commands)
        self.assertIn('y$B0,26', result.commands)
        self.assertFalse(any(cmd.startswith('@') for cmd in result.commands))

    def test_partial_keys_use_ordered_native_delay_and_held_mask_controls(self):
        before = replace(state(), key_mask=12)
        initial = project_state(before, TARGET_CLOCK_HZ, {}, initial=True)
        self.assertIn('KD128,128,0,0', initial.commands)
        after = replace(before, key_mask=8)
        held = project_state(after, TARGET_CLOCK_HZ, {}, initial=False,
                             previous_state=before, target_channel=3)
        self.assertIn('KD128,128,128,0', held.commands)
        self.assertIn('y$28,132', held.commands)
        self.assertFalse(any(command.startswith('@') for command in held.commands))
        full = project_state(state(), TARGET_CLOCK_HZ, {}, initial=True)
        self.assertIn('KD0,0,0,0', full.commands)
        for bit in range(4):
            mask = replace(state(), key_mask=1 << bit)
            command = next(command for command in
                           project_state(mask, TARGET_CLOCK_HZ, {}, initial=True).commands
                           if command.startswith('KD'))
            self.assertEqual(command[2:].split(',')[bit], '0')

    def test_more_than_255_held_profiles_do_not_allocate_unloaded_voices(self):
        previous, voices = state(), {}
        project_state(previous, TARGET_CLOCK_HZ, voices, initial=True)
        for index in range(300):
            ops = list(previous.operators)
            ops[0] = replace(ops[0], tl=index % 128, mul=index // 128 + 1)
            current = replace(previous, operators=tuple(ops))
            projected = project_state(current, TARGET_CLOCK_HZ, voices, initial=False,
                                      previous_state=previous)
            self.assertIsNone(projected.voice_id)
            self.assertFalse(any(command.startswith('@') for command in projected.commands))
            previous = current
        self.assertEqual(len(voices), 1)

    def test_native_partial_long_gate_and_ordered_operator_edges(self):
        tool = ROOT / '.tools/mucom88-260407/mucom88win260407/mucomdotnet/toVgm/mucomDotNET2vgm.exe'
        if sys.platform != 'win32' or not tool.is_file():
            self.skipTest('Windows native MUCOM88 compiler/replay unavailable')
        from opna_music import build_fm_gates, audit_fm_key_edges
        from vgm_reader import parse_vgm
        source, voices = replace(state(), key_mask=12), {}
        transient = replace(source, key_mask=8)
        initial = project_state(transient, TARGET_CLOCK_HZ, voices, initial=True)
        first = project_state(source, TARGET_CLOCK_HZ, voices, initial=False,
                              previous_state=transient)
        only_four = replace(source, key_mask=8)
        changed = project_state(only_four, TARGET_CLOCK_HZ, voices, initial=False,
                                previous_state=source)
        full = replace(source, key_mask=15)
        enabled = project_state(full, TARGET_CLOCK_HZ, voices, initial=False,
                                previous_state=only_four)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / 'voice.dat').write_bytes(bytes(32) + b''.join(voices))
            score = ('#title Partial operator gate\r\n#driver mucomDotNET\r\n'
                     '#voice voice.dat\r\nA C128 t202 q0 '
                     + ' '.join(initial.commands) + ' y$28,128 & '
                     + ' '.join(first.commands) + f' {first.note}%111 & '
                     + 'KD128,128,0,0 ' + f'{initial.note}%111 & '
                     + ' '.join(changed.commands) + f' {changed.note}%16 & '
                     + ' '.join(enabled.commands) + f' {enabled.note}%16 r%8\r\n')
            muc = out / 'partial.muc'
            muc.write_bytes(score.encode('cp932'))
            mub = out / 'partial.mub'
            compile_muc(muc, mub)
            replay = subprocess.run([str(tool), str(mub)], cwd=out,
                                    capture_output=True, timeout=60)
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
            raw = mub.with_suffix('.vgm').read_bytes()
            edges, previous = [], 0
            for event in command_times(raw):
                if event.command != 0x56 or raw[event.address + 1] != 0x28:
                    continue
                value = raw[event.address + 2]
                if value & 7 or value == previous:
                    continue
                edges.append(value)
                previous = value
            self.assertEqual(edges, [0x80, 0xc0, 0x80, 0xf0, 0])
            metadata = {}
            parse_vgm(str(mub.with_suffix('.vgm')), str(out / 'passes'),
                      opna_metadata=metadata, dump_opna_segments=True, loop_count=1)
            gates = build_fm_gates(metadata['analysis'])
            audit_fm_key_edges(metadata['analysis'], gates)
            self.assertEqual(len(gates['A']), 1)
            gate = gates['A'][0]
            self.assertEqual([control.state.key_mask for control in gate.controls], [8, 12, 8, 15])
            self.assertGreater(gate.end - gate.start, 128 * 680)

    def test_native_mute_compiler_limit_is_explicit(self):
        source=replace(state(),left_enabled=0,right_enabled=0)
        with self.assertRaisesRegex(ValueError,'installed native compiler'):
            project_state(source,TARGET_CLOCK_HZ,{},initial=True)

    def test_native_initial_and_held_control(self):
        tool = ROOT / '.tools/mucom88-260407/mucom88win260407/mucomdotnet/toVgm/mucomDotNET2vgm.exe'
        if sys.platform != 'win32' or not tool.is_file():
            self.skipTest('Windows native MUCOM88 compiler/replay unavailable')
        source, voices = state(), {}
        source = replace(source, operators=tuple(replace(op, am_enabled=index % 2)
                                                for index, op in enumerate(source.operators)))
        first = project_state(source, TARGET_CLOCK_HZ, voices, initial=True, target_channel=3)
        ops = list(source.operators)
        ops[0] = replace(ops[0], tl=3, mul=7, ssg_eg_raw=9, ssg_eg_enabled=1)
        changed = replace(source, operators=tuple(ops), algorithm=2, feedback=3,
                          lfo_enabled=0, lfo_raw=0)
        second = project_state(changed, TARGET_CLOCK_HZ, voices, initial=False,
                               previous_state=source, target_channel=3)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / 'voice.dat').write_bytes(bytes(32) + b''.join(voices))
            score = ('#title FM control probe\r\n#voice voice.dat\r\nH C128 t202 q0 '
                     + ' '.join(first.commands) + f' {first.note}%16 & '
                     + ' '.join(second.commands) + f' {second.note}%16 r%8 '
                     + 'H4,1,1 p2 ' + ' '.join(am_restore_commands(changed)) + ' r%8\r\n')
            muc = out / 'probe.muc'
            muc.write_bytes(score.encode('cp932'))
            mub = out / 'probe.mub'
            compile_muc(muc, mub)
            run = subprocess.run([str(tool), str(mub)], cwd=out, capture_output=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            raw = mub.with_suffix('.vgm').read_bytes()
            controls, keys = {}, []
            for event in command_times(raw):
                if event.command not in (0x56, 0x57):
                    continue
                reg, val = raw[event.address + 1:event.address + 3]
                if reg == 0x28 and val & 7 == 4:
                    keys.append((event.vgmticks, val))
                controls.setdefault(event.vgmticks, {})[(event.command, reg)] = val
            attack = next(t for t, value in keys if value == 244)
            initial = controls[attack]
            self.assertEqual(tuple(initial[(0x57, 0x40 + bank * 4)] for bank in range(4)), first.source_tl)
            self.assertEqual(initial[(0x56, 0x22)], 11)
            self.assertEqual(initial[(0x57, 0xb4)], 226)
            self.assertEqual(tuple(initial[(0x57, 0x60 + bank * 4)] for bank in range(4)),
                             tuple(op.am_enabled << 7 | op.d1r for op in source.operators))
            boundary = min(t for t, writes in controls.items() if t > attack and (0x57, 0x30) in writes)
            self.assertFalse(any(t == boundary for t, _ in keys), (attack, boundary, keys))
            self.assertEqual(controls[boundary][(0x57, 0x30)], 7)
            self.assertEqual(controls[boundary][(0x57, 0x40)], 3)
            self.assertEqual(controls[boundary][(0x57, 0xb0)], 26)
            self.assertEqual(controls[boundary][(0x57, 0x90)], 9)
            self.assertEqual(controls[boundary][(0x56, 0x22)], 0)
            self.assertEqual(tuple(controls[boundary][(0x57, 0x60 + bank * 4)] for bank in range(4)),
                             tuple(op.am_enabled << 7 | op.d1r for op in changed.operators))
            released = max(t for t, writes in controls.items()
                           if writes.get((0x56, 0x22)) == 12)
            self.assertGreater(released, boundary)
            self.assertEqual(tuple(controls[released][(0x57, 0x60 + bank * 4)] for bank in range(4)),
                             tuple(op.am_enabled << 7 | op.d1r for op in changed.operators))
            self.assertEqual(controls[released][(0x57, 0xb4)], 145)
            self.assertFalse(any(t == released and val & 0xf0 for t, val in keys))
            # The front end must preserve these controls during the release rest.
            from opna_muc import convert_vgm
            converted = out / 'converted'
            report = convert_vgm(mub.with_suffix('.vgm'), converted)
            muc_text = (converted / 'probe.muc').read_bytes().decode('cp932')
            self.assertIn('H4,1,1 p2 ' + ' '.join(am_restore_commands(changed)), muc_text)


if __name__ == '__main__':
    unittest.main()




