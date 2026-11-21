import unittest
from unittest.mock import patch

from dj_setup import DjOptions
from dj_state import DeckEstimates
from keyboard_controls import ShortcutPresses, identify_key
import macrobuttons
from kilohearts import cutoff_value, filter_controls


class KeyboardTests(unittest.TestCase):
    def event(self, presses, key, **overrides):
        args = dict(down=True, ctrl=True, other_modifier=False, mode='music',
                    exclusive=True, block_enter=True, number_row=False,
                    music_actions={'8': 'high_down'}, music_g_keys={},
                    dj_options=DjOptions())
        args.update(overrides)
        return presses.event(key, **args)

    def test_physical_keypad_survives_num_lock(self):
        self.assertEqual(('keypad', '8'), identify_key(0x68, 0x48, 0))
        self.assertEqual(('keypad', '8'), identify_key(0x26, 0x48, 0))
        self.assertIsNone(identify_key(0x26, 0x48, 1))
        self.assertEqual(('row', '8'), identify_key(0x38, 0x09, 0))
        for vk, key in [(0x6e, '.'), (0x6f, '/'), (0x6a, '*'), (0x6b, '+'), (0x6d, '-')]:
            self.assertEqual(('keypad', key), identify_key(vk, 0, 0x10))
        self.assertEqual(('keypad', 'enter'), identify_key(0x0d, 0x1c, 1))
        self.assertEqual(('main', 'enter'), identify_key(0x0d, 0x1c, 0))

    def test_down_up_pair_survives_ctrl_release_and_mode_change(self):
        presses = ShortcutPresses()
        key = ('keypad', '8')
        self.assertEqual((True, 'key_action:high_down'), self.event(presses, key))
        self.assertEqual((True, None), self.event(presses, key, mode='dj'))
        self.assertEqual((True, None), self.event(presses, key, down=False, ctrl=False, mode='dj'))
        self.assertEqual((False, ('dj_key', 'high_down')), self.event(presses, key, mode='dj'))
        self.assertEqual((False, None), self.event(presses, key, down=False))
        self.assertEqual((False, None), self.event(presses, key, exclusive=False))

    def test_unrelated_ctrl_chord_and_modifier_free_g_key(self):
        presses = ShortcutPresses()
        self.assertEqual((False, None), self.event(presses, ('row', '8')))
        self.assertEqual((False, None),
                         self.event(presses, ('keypad', '8'), other_modifier=True))
        self.assertEqual((True, 'key_action:high_down'), self.event(
            presses, ('function', 'F13'), ctrl=False, music_g_keys={'F13': 'high_down'}))
        self.assertEqual((True, None), self.event(
            presses, ('function', 'F13'), ctrl=False, mode='dj', down=False))
        self.assertEqual((False, ('dj_key', 'deck1')), self.event(
            presses, ('function', 'F13'), ctrl=False, mode='dj'))

    def test_configured_ctrl_home_is_distinct_from_keypad_seven(self):
        home = identify_key(0x24, 0x47, 1)
        keypad_seven = identify_key(0x24, 0x47, 0)
        self.assertEqual(('main', 'home'), home)
        self.assertEqual(('keypad', '7'), keypad_seven)
        self.assertEqual((True, 'key_action:music_fade_up'), self.event(
            ShortcutPresses(), home, home_action='music_fade_up'))
        self.assertEqual((False, None), self.event(ShortcutPresses(), home))
        self.assertEqual((False, None), self.event(
            ShortcutPresses(), home, mode='dj', home_action='music_fade_up'))

    def test_music_does_not_leak_active_native_mapper_bindings(self):
        for key in (('main', 'enter'), ('keypad', '8')):
            presses = ShortcutPresses()
            self.assertEqual((True, None), self.event(
                presses, key, mode='music_vdj', exclusive=False))
            self.assertEqual((True, None), self.event(presses, key, down=False, mode='dj'))
        self.assertEqual((False, None), self.event(
            ShortcutPresses(), ('row', '8'), mode='music_vdj', exclusive=False))

    def test_macrobuttons_transition_blocks_dj_commands_until_ready(self):
        presses = ShortcutPresses()
        key = ('keypad', '*')
        self.assertEqual((True, None), self.event(presses, key, mode='dj_waiting'))
        self.assertEqual((True, None), self.event(presses, key, down=False, mode='dj'))
        self.assertEqual((False, ('dj_key', 'deck2')), self.event(presses, key, mode='dj'))


class EstimateTests(unittest.TestCase):
    def test_unknown_values_need_explicit_baseline_and_decks_stay_independent(self):
        values = DeckEstimates()
        opts = DjOptions(filter_step=3)
        values.apply('filter_up', opts)
        self.assertEqual({}, values.values)
        values.apply('reset_all', opts)
        values.apply('filter_down', opts)
        values.apply('deck2', opts)
        values.apply('filter_up', opts)
        values.apply('sync', opts)
        self.assertEqual(47, values.values[1, 'filter'])
        self.assertEqual(53, values.values[2, 'filter'])
        self.assertEqual(2, values.sync_deck)


class MacroButtonsTests(unittest.TestCase):
    @patch('macrobuttons.request_close', return_value=True)
    @patch('macrobuttons.wait_for_exit', return_value=True)
    @patch('macrobuttons.macrobuttons_pids', return_value=[])
    def test_pause_and_no_session(self, _pids, wait, close):
        macrobuttons.pause(None)
        close.assert_not_called()
        wait.side_effect = [False, True]
        macrobuttons.pause((123, 'MacroButtons path'))
        close.assert_called_once_with(123)
        wait.assert_called_with(123)

    @patch('macrobuttons.request_close', return_value=True)
    @patch('macrobuttons.wait_for_exit', return_value=False)
    def test_does_not_force_a_stuck_app_to_exit(self, _wait, _close):
        with self.assertRaisesRegex(macrobuttons.PausePending, 'stayed open'):
            macrobuttons.pause((123, 'MacroButtons path'))

    @patch('macrobuttons.macrobuttons_pids', return_value=[123])
    @patch('macrobuttons.subprocess.Popen')
    def test_does_not_launch_duplicate_on_restore(self, launch, _pids):
        macrobuttons.restore('VoicemeeterMacroButtons.exe')
        launch.assert_not_called()


class FilterDiscoveryTests(unittest.TestCase):
    def test_adaptive_unit_with_manual_config_compatibility(self):
        self.assertEqual('35 Hz', cutoff_value(35, 'Hz', adaptive=True))
        self.assertEqual('1000 Hz', cutoff_value(1000, 'Hz', adaptive=True))
        self.assertEqual('1.7 kHz', cutoff_value(1700, 'Hz', adaptive=True))
        self.assertEqual('1700 Hz', cutoff_value(1700, 'Hz'))

    def test_mode_ordinals_and_frequency_unit_come_from_plugin(self):
        catalog = ('Parameter\t1\tType\tLow pass\t\t1\t1=Low pass | 3=High pass\n'
                   'Parameter\t2\tCutoff\t620\u202fHz\t\t\t\n')
        controls = filter_controls(catalog)
        self.assertEqual({'Low Pass': 1, 'High Pass': 3}, controls['fx_type_values'])
        self.assertEqual(1, controls['fx_type_parameter'])
        self.assertEqual(2, controls['fx_cutoff_parameter'])
        self.assertEqual('Hz', controls['fx_parameter_unit'])


if __name__ == '__main__':
    unittest.main()
