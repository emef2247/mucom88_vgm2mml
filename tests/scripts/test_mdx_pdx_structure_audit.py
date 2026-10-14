"""Authored cases for full-bank diagnostics; no private fixture dependency."""
import struct
import unittest

from mdx_pdx_structure_audit import pdx_banks
from mdx_reference_expectations import ReferenceError


class PdxBankAuditTests(unittest.TestCase):
    def test_second_bank_reference_and_same_payload_alias(self):
        raw = bytearray(1536) + b'\x12\x34'
        for index in (1, 96 + 4):
            struct.pack_into('>II', raw, index * 8, 1536, 2)
        slots = pdx_banks(raw, 2)
        self.assertEqual(len(slots), 192)
        self.assertEqual((slots[100]['bank'], slots[100]['slot']), (1, 4))
        self.assertEqual(slots[1]['sha256'], slots[100]['sha256'])
        self.assertTrue(all(s['bounds_valid'] for s in slots))

    def test_second_bank_out_of_bounds_and_table_overlap(self):
        raw = bytearray(1536) + b'\x12'
        struct.pack_into('>II', raw, 96 * 8, 1536, 2)
        struct.pack_into('>II', raw, 97 * 8, 768, 1)
        slots = pdx_banks(raw, 2)
        self.assertFalse(slots[96]['bounds_valid'])
        self.assertFalse(slots[97]['bounds_valid'])
        self.assertIsNone(slots[96]['sha256'])

    def test_explicit_bank_count_and_truncated_table(self):
        for banks, raw in ((0, bytes(768)), (257, bytes(768)), (2, bytes(768))):
            with self.subTest(banks=banks), self.assertRaises(ReferenceError):
                pdx_banks(raw, banks)


if __name__ == '__main__':
    unittest.main()
