from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from dj_setup import DjOptions
from dj_state import DeckEstimates
from keyboard_controls import ShortcutPresses, identify_key
import macrobuttons
from kilohearts import cutoff_value, filter_controls
from overlay import HUD, MUTED


class KeyboardTests(unittest.TestCase):
    def event(self, presses, key, **overrides):
        args = dict(down=True, ctrl=True, alt=False, other_modifier=False, mode='music',
                    exclusive=True, number_row=False,
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
        self.assertEqual((False, ('dj_key', 'high_down', False)),
                         self.event(presses, key, mode='dj', ctrl=False, alt=True))
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
        self.assertEqual((False, ('dj_key', 'deck1', False)), self.event(
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
        key = ('keypad', '8')
        presses = ShortcutPresses()
        self.assertEqual((True, None), self.event(
            presses, key, mode='music_vdj', ctrl=False, alt=True, exclusive=False))
        self.assertEqual((True, None), self.event(presses, key, down=False, mode='dj'))
        self.assertEqual((False, None), self.event(
            ShortcutPresses(), ('row', '8'), mode='music_vdj', exclusive=False))
        self.assertEqual((False, None), self.event(
            ShortcutPresses(), ('keypad', '8'), mode='music_vdj', ctrl=False))
        self.assertEqual((False, None), self.event(
            ShortcutPresses(), key, mode='music', ctrl=False, alt=True))

    def test_ctrl_keypad_enter_toggles_filter_only_in_music(self):
        key = ('keypad', 'enter')
        presses = ShortcutPresses()
        music_actions = {'enter': 'filter_toggle'}
        self.assertEqual((True, 'key_action:filter_toggle'),
                         self.event(presses, key, music_actions=music_actions))
        self.assertEqual((True, None), self.event(presses, key, music_actions=music_actions))
        self.assertEqual((True, None), self.event(presses, key, down=False, ctrl=False))
        self.assertEqual((True, None),
                         self.event(presses, key, mode='dj', music_actions=music_actions))
        self.assertEqual((False, None), self.event(
            ShortcutPresses(), key, ctrl=False, music_actions=music_actions))
        self.assertEqual((False, None), self.event(
            ShortcutPresses(), ('main', 'enter'), music_actions=music_actions))

    def test_main_enter_remains_available_in_both_modes(self):
        key = ('main', 'enter')
        old_assignment = {'main_enter': 'filter_toggle'}
        self.assertEqual((False, None),
                         self.event(ShortcutPresses(), key, music_actions=old_assignment))
        for mode in ('dj', 'dj_waiting', 'music_vdj'):
            presses = ShortcutPresses()
            self.assertEqual((False, None), self.event(presses, key, mode=mode))
            self.assertEqual((False, None),
                             self.event(presses, key, down=False, ctrl=False, mode='idle'))
        self.assertEqual((False, None),
                         self.event(ShortcutPresses(), key, ctrl=False, mode='dj'))

    def test_bare_numpad_enter_forwards_only_once_in_focused_dj_mode(self):
        key = identify_key(0x0d, 0x1c, 1)
        presses = ShortcutPresses()
        self.assertEqual((True, ('dj_enter', 'filter_toggle')),
                         self.event(presses, key, ctrl=False, mode='dj'))
        self.assertEqual((True, None), self.event(presses, key, ctrl=False, mode='dj'))
        self.assertEqual((True, None),
                         self.event(presses, key, ctrl=False, mode='music', down=False))
        self.assertEqual((True, None), self.event(presses, key, ctrl=False, mode='dj_background'))
        self.assertEqual((True, None), self.event(
            ShortcutPresses(), key, ctrl=False, alt=True, mode='dj'))

    def test_dj_owns_bare_keypad_and_reserves_it_when_unfocused(self):
        key = ('keypad', '8')
        self.assertEqual((False, ('dj_key', 'high_down', True)),
                         self.event(ShortcutPresses(), key, mode='dj', ctrl=False))
        self.assertEqual((False, ('dj_key', 'high_down', False)), self.event(
            ShortcutPresses(), key, mode='dj', ctrl=False, alt=True))
        self.assertEqual((True, None), self.event(ShortcutPresses(), key, mode='dj'))
        for ctrl in (False, True):
            self.assertEqual((True, None), self.event(
                ShortcutPresses(), key, mode='dj_background', ctrl=ctrl))
        self.assertEqual((True, None), self.event(
            ShortcutPresses(), key, mode='dj', ctrl=False, dj_keypad_available=False))
        self.assertEqual((True, ('dj_enter', 'filter_toggle')), self.event(
            ShortcutPresses(), ('keypad', 'enter'), mode='dj', ctrl=False))
        self.assertEqual((False, None), self.event(ShortcutPresses(), key, ctrl=False))

    def test_holding_keypad_key_does_not_repeat_into_another_app(self):
        key = ('keypad', '8')
        presses = ShortcutPresses()
        self.assertEqual((False, ('dj_key', 'high_down', True)),
                         self.event(presses, key, mode='dj', ctrl=False))
        self.assertEqual((True, None),
                         self.event(presses, key, mode='dj_background', ctrl=False))
        self.assertEqual((False, None),
                         self.event(presses, key, mode='dj_background', ctrl=False, down=False))

    def test_alt_top_row_eight_resets_both_decks_in_dj_mode(self):
        key = identify_key(0x38, 0x09, 0x20)
        self.assertEqual(('row', '8'), key)
        self.assertEqual((False, ('dj_key', 'reset_all', False)),
                         self.event(ShortcutPresses(), key, mode='dj', ctrl=False, alt=True))
        self.assertEqual((False, None),
                         self.event(ShortcutPresses(), key, mode='dj', ctrl=False))
        self.assertEqual((False, None), self.event(ShortcutPresses(), key))

    def test_bass_swap_only_uses_alt_top_row_seven_in_focused_dj_mode(self):
        key = identify_key(0x37, 0x08, 0x20)
        self.assertEqual(('row', '7'), key)
        presses = ShortcutPresses()
        self.assertEqual((False, ('dj_key', 'bass_swap', False)),
                         self.event(presses, key, mode='dj', ctrl=False, alt=True))
        self.assertEqual((False, None),
                         self.event(presses, key, mode='dj', ctrl=False, alt=True))
        self.assertEqual((False, None), self.event(presses, key, mode='dj', down=False, ctrl=False))
        self.assertEqual((False, None), self.event(presses, key, mode='dj', ctrl=False))
        self.assertEqual((False, None), self.event(ShortcutPresses(), key))
        self.assertEqual((True, None),
                         self.event(ShortcutPresses(), key, mode='music_vdj', ctrl=False, alt=True))
        self.assertEqual((False, ('dj_key', 'eq_reset', False)), self.event(
            ShortcutPresses(), ('keypad', '7'), mode='dj', ctrl=False, alt=True))

    def test_alt_w_is_forwarded_and_tracked_once_in_focused_dj_mode(self):
        key = identify_key(0x57, 0x11, 0x20)
        presses = ShortcutPresses()
        self.assertEqual((False, ('dj_key', 'eq_minimum', False)),
                         self.event(presses, key, mode='dj', ctrl=False, alt=True))
        self.assertEqual((False, None), self.event(presses, key, mode='dj', ctrl=False, alt=True))
        self.assertEqual((False, None),
                         self.event(presses, key, mode='dj', down=False, ctrl=False))
        self.assertEqual((False, None), self.event(presses, key, mode='dj', ctrl=False))
        self.assertEqual((False, None),
                         self.event(ShortcutPresses(), key, mode='music', ctrl=False, alt=True))

    def test_macrobuttons_transition_blocks_dj_commands_until_ready(self):
        presses = ShortcutPresses()
        key = ('keypad', '*')
        self.assertEqual((True, None), self.event(presses, key, mode='dj_waiting'))
        self.assertEqual((True, None), self.event(presses, key, down=False, mode='dj'))
        self.assertEqual((False, ('dj_key', 'deck2', False)),
                         self.event(presses, key, mode='dj', ctrl=False, alt=True))


class EstimateTests(unittest.TestCase):
    def test_default_filter_baseline_and_decks_stay_independent(self):
        values = DeckEstimates()
        opts = DjOptions(filter_step=3)
        values.apply('filter_up', opts)
        self.assertEqual((53, 50), tuple(values.values[d, 'filter'] for d in (1, 2)))
        values.apply('reset_all', opts)
        values.apply('filter_down', opts)
        values.apply('deck2', opts)
        values.apply('filter_up', opts)
        values.apply('sync', opts)
        self.assertEqual(47, values.values[1, 'filter'])
        self.assertEqual(53, values.values[2, 'filter'])
        self.assertEqual(2, values.sync_deck)


    def test_bare_eq_is_faster_without_changing_other_decks(self):
        values = DeckEstimates()
        opts = DjOptions()
        values.apply('high_up', opts, coarse=True)
        values.apply('mid_down', opts)
        self.assertEqual(65, values.values[1, 'eq_high'])
        self.assertEqual(42, values.values[1, 'eq_mid'])
        self.assertEqual(50, values.values[2, 'eq_high'])

    def test_eq_minimum_updates_all_three_bands_and_preserves_volume(self):
        values = DeckEstimates()
        opts = DjOptions()
        values.apply('deck2', opts)
        values.apply('volume_down', opts)
        values.apply('eq_minimum', opts)
        self.assertEqual((0, 0, 0, 85), tuple(
            values.values[2, control] for control in ('eq_high', 'eq_mid', 'eq_low', 'level')))
        self.assertEqual(50, values.values[1, 'eq_high'])
        values.apply('eq_reset', opts)
        self.assertEqual(50, values.values[2, 'eq_high'])
        self.assertEqual(85, values.values[2, 'level'])

    def test_bass_swap_alternates_the_lower_deck_without_reselecting(self):
        values = DeckEstimates()
        opts = DjOptions()
        values.apply('deck2', opts)
        values.apply('bass_swap', opts)
        self.assertEqual((0, 60), tuple(values.values[d, 'eq_low'] for d in (1, 2)))
        values.apply('bass_swap', opts)
        self.assertEqual((60, 0), tuple(values.values[d, 'eq_low'] for d in (1, 2)))
        values.apply('bass_swap', opts)
        self.assertEqual((0, 60), tuple(values.values[d, 'eq_low'] for d in (1, 2)))
        self.assertEqual(2, values.deck)

    def test_volume_steps_and_toggle_use_selected_decks_fader(self):
        values = DeckEstimates()
        opts = DjOptions()
        values.apply('volume_down', opts)
        self.assertEqual(85, values.values[1, 'level'])
        values.apply('volume_toggle', opts)
        self.assertEqual(0, values.values[1, 'level'])
        values.apply('volume_toggle', opts)
        self.assertEqual(100, values.values[1, 'level'])
        values.apply('deck2', opts)
        values.apply('volume_toggle', opts)
        self.assertEqual((100, 0), tuple(values.values[d, 'level'] for d in (1, 2)))

    def test_bypassed_filter_position_can_move_without_enabling_it(self):
        values = DeckEstimates()
        opts = DjOptions()
        self.assertEqual({1: False, 2: False}, values.filter_enabled)
        values.apply('deck2', opts)
        values.apply('filter_toggle', opts)
        self.assertEqual({1: False, 2: True}, values.filter_enabled)
        values.apply('reset_all', opts)
        values.apply('filter_up', opts)
        self.assertEqual(55, values.values[1, 'filter'])
        self.assertEqual({1: False, 2: False}, values.filter_enabled)
        values.apply('filter_toggle', opts)
        values.apply('filter_up', opts)
        self.assertEqual(60, values.values[1, 'filter'])
        values.apply('filter_toggle', opts)
        values.apply('filter_up', opts)
        self.assertFalse(values.filter_enabled[1])
        values.apply('filter_toggle', opts)
        self.assertTrue(values.filter_enabled[1])
        self.assertEqual(65, values.values[1, 'filter'])
        self.assertEqual(50, values.values[2, 'filter'])

    def test_both_hud_sizes_render_eq_around_100_and_dim_bypassed_filter(self):
        hud = HUD.__new__(HUD)
        hud.cfg = {'dj_mode': True, 'dj_http_readback': False}
        hud.dj_options = DjOptions()
        hud.dj_estimates = DeckEstimates()
        hud.dj_estimates.apply('filter_up', hud.dj_options)
        hud.dj_estimates.apply('high_up', hud.dj_options, coarse=True)
        hud.dj_estimates.apply('mid_down', hud.dj_options)
        hud.dj_estimates.apply('volume_down', hud.dj_options)
        hud.dj_estimates.values[2, 'level'] = 85
        hud.dj_connected = False
        hud.dj_focused = True
        hud.canvas = Mock()
        hud.round_rect = Mock()
        hud.text = Mock()
        hud.draw_mode_button = Mock()
        for mode, connected in (('small', False), ('large', True)):
            with self.subTest(mode=mode, connected=connected):
                hud.cfg['view_mode'] = mode
                hud.dj_connected = connected
                hud.dj_values = {key: f'{value}%' for key, value in hud.dj_estimates.values.items()}
                hud.dj_values[1, 'filter'] = '50%'
                hud.text.reset_mock()
                hud.draw_dj()
                labels = {call.args[2]: call.kwargs.get('color') for call in hud.text.call_args_list}
                self.assertEqual(MUTED, labels['HP 55%'])
                self.assertTrue({'CENTER', '130%', '84%', '100%', '85%'}.issubset(labels))


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

    @patch('macrobuttons.macrobuttons_pids', return_value=[])
    @patch('macrobuttons.subprocess.Popen')
    def test_resume_recovers_a_blank_map_and_preserves_a_populated_one(self, launch, _pids):
        blank = (b'<VBAudioVoicemeeterMacroButtonMap><MacroButtonConfiguration>'
                 b'<MacroButton key="0"><MB_Name /></MacroButton>'
                 b'</MacroButtonConfiguration></VBAudioVoicemeeterMacroButtonMap>')
        saved = blank.replace(b'<MB_Name />', b'<MB_Name>Fade Up</MB_Name>')
        changed = saved.replace(b'Fade Up', b'Fade Down')
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            active = folder / macrobuttons.MAP_FILENAME
            backup = folder / 'before-dj.xml'
            exe = folder / macrobuttons.IMAGE
            exe.touch()
            active.write_bytes(saved)
            with patch('macrobuttons.configuration_path', return_value=active):
                self.assertTrue(macrobuttons.backup_configuration(backup))
                active.write_bytes(blank)
                macrobuttons.restore(str(exe), backup=backup)
                self.assertEqual(saved, active.read_bytes())
                active.write_bytes(changed)
                macrobuttons.restore(str(exe), backup=backup)
                self.assertEqual(changed, active.read_bytes())
            self.assertEqual(2, launch.call_count)


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
