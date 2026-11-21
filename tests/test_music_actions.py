import unittest
from types import SimpleNamespace
from unittest.mock import patch

from overlay import VoiceReader


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
