"""Synthetic control-flow regressions independent of the original game image."""

import unittest

import capstone

from arm2c import Emitter, TranslationError


class FakeImage:
    def __init__(self, words):
        self.words = words
        self.func_by_addr = {}
        self.plt = {}

    def is_code(self, address):
        return address in self.words

    def read32(self, address):
        return self.words[address]


def emit(words, start=0x1000, end=0x100c):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    md.detail = True
    function = {"addr": start, "end": end, "size": end - start,
                "name": "synthetic", "aliases": []}
    emitter = Emitter(FakeImage(words), function, md, {})
    return emitter.translate(), emitter.unsupported


class CodeDataControlFlowTests(unittest.TestCase):
    def test_sequential_gap_traps_at_actual_next_pc(self):
        source, unsupported = emit({0x1000: 0xe3a00001, 0x1008: 0xe12fff1e})
        self.assertEqual(unsupported, [{"addr": "0x1004",
                                      "why": "sequential PC enters non-code region"}])
        trap = source.index('aot_unsupported(c, 0x00101004u,')
        self.assertLess(source.index('/* 00001000:'), trap)
        self.assertLess(trap, source.index('/* 00001008:'))

    def test_unconditional_branch_can_cross_data_gap(self):
        source, unsupported = emit({0x1000: 0xea000000, 0x1008: 0xe12fff1e})
        self.assertEqual(unsupported, [])
        self.assertIn('goto L_00001008;', source)
        self.assertIn('L_00001008:', source)
        self.assertNotIn('sequential PC enters non-code region', source)

    def test_conditional_branch_still_traps_false_path(self):
        source, unsupported = emit({0x1000: 0x0a000000, 0x1008: 0xe12fff1e})
        self.assertEqual([item['addr'] for item in unsupported], ['0x1004'])
        self.assertLess(source.index('aot_unsupported(c, 0x00101004u,'),
                        source.index('L_00001008:'))

    def test_unreachable_instruction_before_gap_does_not_add_gap_failure(self):
        source, unsupported = emit({0x1000: 0xea000002, 0x1004: 0xe3a00001,
                                    0x1010: 0xe12fff1e}, end=0x1014)
        self.assertEqual(unsupported, [])
        self.assertIn('goto L_00001010;', source)
        self.assertNotIn('sequential PC enters non-code region', source)

    def test_non_code_entry_is_not_silently_advanced(self):
        source, unsupported = emit({0x1008: 0xe12fff1e})
        self.assertEqual([item['addr'] for item in unsupported], ['0x1000'])
        self.assertLess(source.index('aot_unsupported(c, 0x00101000u,'),
                        source.index('/* 00001008:'))

    def test_empty_code_body_traps_entry_once(self):
        source, unsupported = emit({})
        self.assertEqual([item['addr'] for item in unsupported], ['0x1000'])
        self.assertEqual(source.count('aot_unsupported('), 1)

    def test_trailing_gap_traps_next_pc_and_return_gap_is_unreachable(self):
        source, unsupported = emit({0x1000: 0xe3a00001})
        self.assertEqual([item['addr'] for item in unsupported], ['0x1004'])
        self.assertIn('aot_unsupported(c, 0x00101004u,', source)
        source, unsupported = emit({0x1000: 0xe12fff1e, 0x1008: 0xe12fff1e})
        self.assertEqual(unsupported, [])
        self.assertNotIn('sequential PC enters non-code region', source)

    def test_lr_pc_call_pattern_requires_physical_adjacency(self):
        source, unsupported = emit({0x1000: 0xe1a0e00f, 0x1008: 0xe12fff10})
        self.assertEqual([item['addr'] for item in unsupported], ['0x1004'])
        self.assertIn('AOT_JUMP(t_, 0x00101008u)', source)
        self.assertNotIn('AOT_CALL_INDIRECT', source)

    def test_lr_pc_call_pattern_cannot_cross_branch_target_label(self):
        source, unsupported = emit({0x1000: 0xe1a0e00f, 0x1004: 0xe12fff10,
                                    0x1008: 0xe12fff1e, 0x100c: 0xeafffffc},
                                   end=0x1010)
        self.assertEqual(unsupported, [])
        self.assertIn('L_00001004:', source)
        self.assertIn('AOT_JUMP(t_, 0x00101004u)', source)
        self.assertNotIn('AOT_CALL_INDIRECT', source)

    def test_adjacent_lr_pc_call_pattern_remains_supported(self):
        source, unsupported = emit({0x1000: 0xe1a0e00f, 0x1004: 0xe12fff10,
                                    0x1008: 0xe12fff1e})
        self.assertEqual(unsupported, [])
        self.assertIn('AOT_CALL_INDIRECT(t_, 0x00101004u)', source)

    def test_exclusive_lr_pc_predicates_do_not_create_call(self):
        # MOVEQ LR,PC and BXNE R0 cannot both execute on the same path.
        source, unsupported = emit({0x1000: 0x01a0e00f, 0x1004: 0x112fff10,
                                    0x1008: 0xe12fff1e})
        self.assertEqual(unsupported, [])
        self.assertIn('AOT_JUMP(t_, 0x00101004u)', source)
        self.assertNotIn('AOT_CALL_INDIRECT', source)


class InstructionEligibilityTests(unittest.TestCase):
    def test_malformed_misc_fixed_fields_are_rejected(self):
        # Flip each fixed bit in the three recognized miscellaneous forms.
        for word, mask in ((0xe12fff10, 0x0ffffff0),
                           (0xe12fff30, 0x0ffffff0),
                           (0xe16f0f11, 0x0fff0ff0)):
            for bit in range(28):
                if not mask & (1 << bit):
                    continue
                mutated = word ^ (1 << bit)
                # Flipping a bit can turn BX into BLX or CLZ; those are
                # separate recognized encodings rather than malformed words.
                if any(mutated & fixed == encoding for fixed, encoding in (
                        (0x0ffffff0, 0x012fff10), (0x0ffffff0, 0x012fff30),
                        (0x0fff0ff0, 0x016f0f10))):
                    continue
                with self.subTest(word=hex(mutated)):
                    # Mutations can encode other valid instruction families;
                    # this test isolates the miscellaneous decoder contract.
                    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
                    function = {"addr": 0x1000, "end": 0x1004, "size": 4,
                                "name": "synthetic", "aliases": []}
                    emitter = Emitter(FakeImage({}), function, md, {})
                    with self.assertRaises(TranslationError):
                        emitter.misc(0x1000, mutated, 14, None)

    def test_known_invalid_bx_words_do_not_lower_to_return(self):
        for word in (0xe123451e, 0xe120001e):
            with self.subTest(word=hex(word)):
                source, unsupported = emit({0x1000: word}, end=0x1004)
                self.assertEqual([item['addr'] for item in unsupported], ['0x1000'])
                self.assertIn('misc instruction', unsupported[0]['why'])
                self.assertNotIn('AOT_RETURN_TO', source)

    def test_valid_misc_registers_and_conditions_remain_supported(self):
        for condition in (0, 1, 14):
            for register in range(15):
                for encoding, expected in ((0x012fff10, 'AOT_JUMP'),
                                           (0x012fff30, 'AOT_CALL_INDIRECT'),
                                           (0x016f0f10, 'aot_clz')):
                    word = (condition << 28) | encoding | register
                    with self.subTest(word=hex(word)):
                        source, unsupported = emit({0x1000: word}, end=0x1004)
                        self.assertEqual(unsupported, [])
                        # BX LR uses its dedicated return lowering.
                        self.assertIn('AOT_RETURN_TO' if encoding == 0x012fff10
                                      and register == 14 else expected, source)

    def test_movw_movt_pc_destination_is_rejected(self):
        for condition in (0, 1, 14):
            for encoding, name in ((0x0300f001, 'MOVW'), (0x0340f001, 'MOVT')):
                word = (condition << 28) | encoding
                with self.subTest(word=hex(word)):
                    source, unsupported = emit({0x1000: word}, end=0x1004)
                    self.assertEqual(unsupported, [{"addr": "0x1000",
                                                   "why": name + " with PC destination"}])
                    self.assertNotIn('pc =', source)

    def test_movw_movt_general_registers_remain_supported(self):
        for register in range(15):
            for encoding in (0xe3000001, 0xe3400001):
                word = encoding | (register << 12)
                with self.subTest(word=hex(word)):
                    _source, unsupported = emit({0x1000: word}, end=0x1004)
                    self.assertEqual(unsupported, [])


if __name__ == '__main__':
    unittest.main()
