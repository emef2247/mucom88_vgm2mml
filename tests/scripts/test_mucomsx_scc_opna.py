"""Authored checks for waveform-derived additive OPNA voice, gain and pitch."""
from pathlib import Path
import math
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'py'))
from mucomsx_scc_opna import project_waveform, volume_controls, project_pitch
from mucom_fm import FNUM, NOTES


def sine_wave(harmonic=1):
    return bytes(round(100 * math.sin(2 * math.pi * harmonic * i / 32)) & 255
                 for i in range(32)).hex()


class SccOpnaTests(unittest.TestCase):
    def test_source_dft_drives_operator_multipliers_and_levels(self):
        voice = project_waveform(sine_wave(3))
        self.assertEqual(len(voice.dat), 32)
        self.assertEqual(voice.dat[25], 7)
        self.assertIn(3, voice.harmonics)
        strongest = max(range(4), key=lambda index: voice.amplitudes[index])
        self.assertEqual(voice.harmonics[strongest], 3)
        self.assertEqual(tuple(voice.dat[1:5]), voice.harmonics)
        self.assertEqual(voice.dat[9:13], bytes((31, 31, 31, 31)))
        self.assertEqual(voice.dat[13:21], bytes(8))
        self.assertEqual(voice.dat[21:25], bytes((15, 15, 15, 15)))
        self.assertGreater(voice.report['retained_ac_fraction'], .999)
        self.assertFalse(voice.report['exact'])
        self.assertEqual(voice.report['source_waveform_hex'], sine_wave(3))
        self.assertEqual(len(voice.report['source_phases']), 4)

    def test_volume_controls_preserve_spectral_ratios_and_source_record(self):
        voice = project_waveform(sine_wave())
        original = voice.dat
        full = [int(command.rsplit(',', 1)[1]) for command in volume_controls(voice, 15)]
        half = [int(command.rsplit(',', 1)[1]) for command in volume_controls(voice, 7)]
        self.assertTrue(all(after >= before for before, after in zip(full, half)))
        self.assertEqual(volume_controls(voice, 0),
                         ('yTL,1,127', 'yTL,3,127', 'yTL,2,127', 'yTL,4,127'))
        self.assertEqual(volume_controls(voice, 15, enabled=False), volume_controls(voice, 0))
        self.assertEqual(voice.dat, original)

    def test_dc_and_phase_loss_remain_reported(self):
        voice = project_waveform((bytes((64,)) * 32).hex())
        self.assertEqual(voice.report['source_dc'], .5)
        self.assertEqual(voice.report['retained_ac_fraction'], 0)
        self.assertEqual(voice.dat[5:9], bytes((127,)) * 4)
        self.assertTrue(voice.report['limitations'])

    def test_scc_pitch_uses_vgm_clock_and_native_opna_convention(self):
        projection = project_pitch(253, 1_789_773)
        source = 1_789_773 / (16 * 254)
        self.assertAlmostEqual(projection.source_frequency_hz, source)
        encoded = FNUM[NOTES.index(projection.note)] + projection.detune
        target = 8_000_000 * encoded * 2 ** projection.target_block / (144 * 2 ** 21)
        self.assertAlmostEqual(projection.frequency_error_hz, target - source)
        self.assertLess(abs(projection.frequency_error_cents), 2)

    def test_invalid_evidence_and_unrepresentable_pitch_rejected(self):
        for wave in ('', 'ff' * 31):
            with self.assertRaises(ValueError):
                project_waveform(wave)
        for gain in (0, -1, math.inf):
            with self.assertRaises(ValueError):
                project_waveform(sine_wave(), gain=gain)
        for period in (0, 8, 9, -1, 4096):
            with self.assertRaises(ValueError):
                project_pitch(period, 1_789_773)
        with self.assertRaises(ValueError):
            volume_controls(project_waveform(sine_wave()), 16)


if __name__ == '__main__':
    unittest.main()
