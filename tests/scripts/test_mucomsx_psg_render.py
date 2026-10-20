"""Exact generated-state checks on authored, public-domain MUC units."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'py'))
from mucomsx_psg_render import compact_psg_commands

E='E255,0,0,0,0,255'

class PsgRenderTests(unittest.TestCase):
    def compact(self,units,**kw):
        return compact_psg_commands(units,hardware=kw.pop('hardware',[False]*len(units)),**kw)

    def test_decay_stays_one_attack_with_same_timed_volume_trajectory(self):
        original=[E+' P1 D0 o4 v15 c%8 & v10 c%16',
                  E+' P1 D0 o4 v15 d%8 & v10 d%16']
        result,report=self.compact(original)
        self.assertEqual(result[1],E+' v15 d%8 & v10 d8')
        self.assertEqual(sum(x.count(E) for x in result),2)
        self.assertTrue(report['effective_event_identity'])
        self.assertEqual(report['removed_setters'],{'p':1,'d':1,'o':1})

    def test_relative_octaves_and_duration_aliases_keep_integer_ticks(self):
        result,report=self.compact(['P1 D0 o4 v15 c%32','P1 D0 o5 v15 c%64',
                                    'P1 D0 o4 v15 c%1'])
        self.assertEqual(result,['P1 D0 o4 v15 c4','> c2','< c%1'])
        self.assertTrue(report['effective_event_identity'])

    def test_loop_entry_has_required_state_even_for_held_tie(self):
        result,report=self.compact(['P1 D0 o4 v15 c%32','& P1 D0 o4 v15 c%32'],loop_index=1)
        self.assertEqual(result[1],'& P1 D0 o4 v15 c4')
        self.assertTrue(report['units'][1]['loop_entry'])

    def test_shared_noise_and_hardware_trigger_actions_are_never_dropped(self):
        body=E+' P3 w7 m100 S9 D0 o4 c%8'
        result,report=self.compact([body,body],hardware=[True,True])
        for text in result:
            for action in (E,'w7','m100','S9'):
                self.assertIn(action,text)
        self.assertTrue(report['effective_event_identity'])

    def test_v_after_hardware_envelope_restores_software_volume_mode(self):
        result,report=self.compact(['P1 D0 o4 v15 c%8','m100 S9 D0 o4 c%8',
                                    E+' P1 D0 o4 v15 c%8'])
        self.assertIn('v15',result[2])
        self.assertTrue(report['effective_event_identity'])

    def test_rest_keeps_base_volume_and_same_pitch_retrigger(self):
        result,report=self.compact([E+' P1 D0 o4 v12 c%16','r%16',
                                    E+' P1 D0 o4 v12 c%16'])
        self.assertEqual(result[2],E+' c8')
        self.assertNotIn('&',result[2])
        self.assertTrue(report['effective_event_identity'])

    def test_exact_structure_retains_relative_octaves_inside_repeat(self):
        from source_loop_plan import SourceLoopPlan,expanded_tokens
        commands=['P1 D0 o4 v15 c%16']
        commands += ['P1 D0 o5 v15 c%16','P1 D0 o4 v15 c%16']*4
        result,report=self.compact(commands)
        plan=SourceLoopPlan.build([0]+[1,2]*4,strategy='structural')
        folded,_=plan.render(result)
        self.assertIn('[',folded)
        self.assertIn('>',folded)
        self.assertEqual(expanded_tokens(folded),expanded_tokens(' '.join(result)))
        self.assertTrue(report['effective_event_identity'])

    def test_pass_rejects_already_folded_music(self):
        with self.assertRaisesRegex(ValueError,'precede finite'):
            self.compact(['[o4 c%32]2'])

if __name__=='__main__':unittest.main()
