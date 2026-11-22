import json
import queue
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from overlay import ACTIONS, DEFAULTS, HUD, VoiceReader, load_config


class MusicActionsTests(unittest.TestCase):
    def test_saved_g_actions_fade_and_change_only_the_music_strip(self):
        reader = VoiceReader({'music_strip': 1}, None, None, None)
        music = SimpleNamespace(gain=-16, bass=0, mid=3, treble=6, mute=False)
        vm = SimpleNamespace(strip=[object(), music])
        for action, target, duration in (('music_fade_up', -14, .15),
                                         ('music_fade_down', -16, .15),
                                         ('music_clutch_fade', -46, .3),
                                         ('music_clutch_fade', -16, .4)):
            with patch('overlay.time.monotonic', return_value=5):
                reader.apply_action(vm, action)
            self.assertEqual((target, duration), reader.fade[2:])
            with patch('overlay.time.monotonic', return_value=6):
                reader.update_fade(vm)
            self.assertEqual(target, music.gain)
        reader.apply_action(vm, 'music_mute')
        self.assertTrue(music.mute)
        reader.apply_action(vm, 'music_mute')
        self.assertFalse(music.mute)
        reader.apply_action(vm, 'music_bass_drop')
        self.assertEqual(-12, music.bass)
        reader.apply_action(vm, 'music_bass_drop')
        self.assertEqual(8, music.bass)
        reader.apply_action(vm, 'music_normalize')
        self.assertEqual((0, 0, 0, 0), (music.gain, music.bass, music.mid, music.treble))

    def test_music_eq_moves_by_8_percent_of_range_and_lands_on_limits(self):
        reader = VoiceReader({'music_strip': 0}, None, None, None)
        music = SimpleNamespace(bass=0, mid=11, treble=-11)
        vm = SimpleNamespace(strip=[music])
        reader.apply_action(vm, 'low_up')
        reader.apply_action(vm, 'mid_up')
        reader.apply_action(vm, 'high_down')
        self.assertEqual((1.92, 12, -12), (music.bass, music.mid, music.treble))

    def test_output_commands_switch_only_the_first_two_bus_mutes(self):
        reader = VoiceReader({'music_strip': 0}, None, None, None)
        a1 = SimpleNamespace(mute=False, gain=-3)
        a2 = SimpleNamespace(mute=True, gain=-8)
        a3 = SimpleNamespace(mute=False, gain=-2)
        vm = SimpleNamespace(strip=[object()], bus=[a1, a2, a3])
        reader.apply_action(vm, 'headphones')
        self.assertEqual((True, False, False), (a1.mute, a2.mute, a3.mute))
        reader.apply_action(vm, 'speakers')
        self.assertEqual((False, True, False), (a1.mute, a2.mute, a3.mute))
        self.assertEqual((-3, -8, -2), (a1.gain, a2.gain, a3.gain))

    def test_ctrl_keypad_enter_is_assignable_and_dispatches_to_filter(self):
        self.assertEqual('filter_toggle', DEFAULTS['key_actions']['enter'])
        self.assertIn('filter_toggle', ACTIONS)
        hud = HUD.__new__(HUD)
        hud.cfg = {'dj_mode': False}
        hud.commands, hud.messages, hud.vm_actions = (queue.Queue() for _ in range(3))
        hud.current = {'online': False}
        hud.update_dj_focus = Mock()
        hud.root = Mock()
        hud.root.state.return_value = 'withdrawn'
        hud.toggle_filter_enabled = Mock()
        hud.commands.put('key_action:filter_toggle')
        hud.tick()
        hud.toggle_filter_enabled.assert_called_once_with()
        self.assertTrue(hud.vm_actions.empty())


    def test_legacy_enter_defaults_are_updated_without_rebinding_other_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'settings.json'
            config.write_text(json.dumps({
                'block_numpad_enter': True,
                'key_actions': {'1': 'music_fade_down', 'enter': 'none', 'main_enter': 'filter_toggle'},
                'dj_enter': 'sync',
            }), encoding='utf-8')
            with patch('overlay.CONFIG_FILE', config):
                data = load_config()
                self.assertEqual({'1': 'music_fade_down', 'enter': 'filter_toggle'}, data['key_actions'])
                self.assertNotIn('block_numpad_enter', data)
                self.assertEqual('filter_toggle', data['dj_enter'])
                data['key_actions'].pop('enter')
                data['dj_enter'] = 'sync'
                config.write_text(json.dumps(data), encoding='utf-8')
                self.assertNotIn('enter', load_config()['key_actions'])
                self.assertEqual('sync', load_config()['dj_enter'])
