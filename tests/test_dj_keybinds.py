from types import SimpleNamespace
import unittest

from dj_keybinds import guide_position, keypad_rows
from dj_setup import DjOptions


class KeybindGuideTests(unittest.TestCase):
    def test_guide_uses_configured_enter_action(self):
        self.assertIn(('Enter', 'Toggle deck filter'), keypad_rows(DjOptions()))
        self.assertIn(('Enter', 'Sync deck'), keypad_rows(DjOptions(enter='sync')))
        self.assertNotIn('Enter', dict(keypad_rows(DjOptions(enter='none'))))

    def test_guide_uses_space_above_or_below_hud_on_its_monitor(self):
        left = SimpleNamespace(x=-1920, y=0, width=1920, height=1080)
        main = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitors = (left, main)
        self.assertEqual((-420, 386),
                         guide_position((-300, 800, 250, 214), (412, 406), monitors))
        self.assertEqual((30, 242),
                         guide_position((30, 20, 370, 214), (412, 406), monitors))
