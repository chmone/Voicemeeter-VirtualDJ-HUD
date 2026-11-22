import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import dj_setup


SETTINGS = b'''<?xml version="1.0" encoding="UTF-8"?>
<settings>
  <!-- Keep these bytes, and only edit the keyboard selection -->
  <controllers>
    <controllersCustomization>
      <controller name="KEYBOARD" mapper="KEYBOARD - Personal" />
      <controller name="Other" mapper="Preserve" />
    </controllersCustomization>
  </controllers>
  <unrelated>Example</unrelated>
</settings>
'''


class MapperTests(unittest.TestCase):
    def test_verified_bindings_and_independent_frequency_eq(self):
        options = dj_setup.DjOptions(eq_step=4, filter_step=3, volume_step=2)
        mapper = ET.fromstring(dj_setup.build_mapper(options))
        actions = {n.get("value"): n.get("action") for n in mapper.findall("map")}
        self.assertIn("deck 1 eq_high_freq -4%", actions["ALT+NUM8"])
        self.assertIn("deck 2 eq_high_freq -4%", actions["ALT+NUM8"])
        self.assertIn("deck 1 filter_activate", actions["F20"])
        self.assertIn("deck 2 filter_activate", actions["F20"])
        self.assertIn("deck 2 level 100% : deck 2 level 0%", actions["ALT+NUM+"])
        self.assertIn("deck 1 level 100% : deck 1 level 0%", actions["NUM+"])
        self.assertIn("deck 2 sync : deck 1 sync", actions["ALT+Q"])
        self.assertIn("deck 2 eq_high_freq -15%", actions["NUM8"])
        self.assertIn("deck 2 filter +3%", actions["NUM."])
        self.assertIn("deck 1 eq_reset", actions["ALT+8"])
        self.assertNotIn("RETURN", actions)
        self.assertFalse(any(key.startswith("CTRL+") for key in actions))
        self.assertNotIn("NUMENTER", actions)
        self.assertIn("deck 1 filter 50%", actions["ALT+NUM."])
        self.assertIn("deck 1 filter +3%", actions["ALT+NUM."])
        self.assertIn("deck 2 level -2%", actions["ALT+NUM1"])
        self.assertIn("isrepeat", actions["ALT+NUM/"])

    def test_selected_deck_transport_keys_keep_user_overrides(self):
        base = (b'<mapper device="KEYBOARD"><map value="ALT+Q" action="cue" />'
                b'<map value="ALT+U" action="playlist" /></mapper>')
        root = ET.fromstring(dj_setup.build_mapper(base=base))
        actions = {item.get('value'): item.get('action') for item in root.findall('map')}
        self.assertEqual('cue', actions['ALT+Q'])
        self.assertEqual('playlist', actions['ALT+U'])
        for shortcut, verb in (('ALT+1', 'play_button'), ('ALT+2', 'cue_button'),
                               ('ALT+3', 'stop_button'), ('ALT+4', 'loop'),
                               ('ALT+5', 'loop_half'), ('ALT+6', 'loop_double')):
            self.assertIn(f'{dj_setup.VARIABLE} 2 ? deck 2 {verb} : deck 1 {verb}', actions[shortcut])
        for shortcut in ('ALT+4', 'ALT+5', 'ALT+6'):
            self.assertTrue(actions[shortcut].startswith(dj_setup.ONCE))
        self.assertTrue({'ALT+F11', 'ALT+F12', 'ALT+9', 'ALT+0'}.isdisjoint(actions))

    def test_alt_w_sets_only_the_selected_decks_frequency_eq_to_minimum(self):
        base = b'<mapper device="KEYBOARD"><map value="ALT+W" action="nudge" /></mapper>'
        root = ET.fromstring(dj_setup.build_mapper(base=base))
        actions = root.findall("./map[@value='ALT+W']")
        self.assertEqual(1, len(actions))
        self.assertEqual(dj_setup.ONCE + "var_equal '$cornerhud_deck' 2 ? "
                         "deck 2 eq_high_freq 0% & deck 2 eq_mid_freq 0% & deck 2 eq_low_freq 0% : "
                         "deck 1 eq_high_freq 0% & deck 1 eq_mid_freq 0% & deck 1 eq_low_freq 0%",
                         actions[0].get('action'))

    def test_native_filter_targets_starter_control_and_regular_mixer(self):
        actions = {n.get('value'): n.get('action') for n in ET.fromstring(dj_setup.build_mapper())}
        self.assertIn("load_skin 'Default:Starter' ?", actions['NUM.'])
        self.assertIn("deck 2 effect_colorfx 1 'Filter' +5%", actions['NUM.'])
        self.assertIn("deck 1 filter +5%", actions['NUM.'])
        self.assertIn("deck 2 effect_colorfx 1 'Filter' 50%", actions['NUM-'])
        self.assertIn("deck 1 effect_colorfx 1 'Filter' 50%", actions['F20'])
        self.assertIn("deck 1 filter_activate", actions['F20'])
        self.assertIn("deck 2 level -15%", actions['NUM1'])
        self.assertIn("deck 1 level +15%", actions['ALT+NUM4'])

    def test_volume_toggle_queries_each_decks_current_level(self):
        actions = {n.get('value'): n.get('action') for n in ET.fromstring(dj_setup.build_mapper())}
        self.assertEqual(actions['NUM+'], actions['ALT+NUM+'])
        self.assertIn('isrepeat ? nothing', actions['NUM+'])
        for deck in (1, 2):
            self.assertIn(f'param_equal `deck {deck} level` 0% ? '
                          f'deck {deck} level 100% : deck {deck} level 0%', actions['NUM+'])

    def test_default_eq_steps_and_bass_swap_mapping(self):
        opts = dj_setup.DjOptions.from_config({})
        self.assertEqual(dj_setup.DjOptions(), opts)
        self.assertEqual((8, 15), (opts.eq_step, opts.eq_coarse_step))
        custom = dj_setup.DjOptions.from_config({'dj_eq_step': 4, 'dj_eq_coarse_step': 12})
        self.assertEqual((4, 12), (custom.eq_step, custom.eq_coarse_step))
        actions = {n.get('value'): n.get('action') for n in ET.fromstring(dj_setup.build_mapper(opts))}
        self.assertIn('deck 1 eq_low_freq +8%', actions['ALT+NUM3'])
        self.assertIn('deck 2 eq_low_freq +15%', actions['NUM3'])
        boost_1 = "deck 2 eq_low_freq 0% & deck 1 eq_low_freq 60%"
        boost_2 = "deck 1 eq_low_freq 0% & deck 2 eq_low_freq 60%"
        self.assertEqual(dj_setup.ONCE
                         + f"param_greater `deck 1 eq_low_freq` `deck 2 eq_low_freq` ? {boost_2} : "
                         f"param_greater `deck 2 eq_low_freq` `deck 1 eq_low_freq` ? {boost_1} : "
                         f"var_equal '$cornerhud_deck' 2 ? {boost_2} : {boost_1}", actions['ALT+7'])

    def test_bipolar_step_snaps_to_center_in_both_directions(self):
        self.assertEqual(50, dj_setup.filter_position(48, 3))
        self.assertEqual(50, dj_setup.filter_position(52, -3))
        self.assertEqual(53, dj_setup.filter_position(50, 3))
        self.assertEqual(47, dj_setup.filter_position(50, -3))
        self.assertEqual(100, dj_setup.filter_position(99, 3))

    @patch("dj_setup.virtualdj_running", return_value=False)
    def test_install_reinstall_and_rollback_preserve_unrelated_settings(self, _running):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            mappers = home / "Mappers"
            mappers.mkdir()
            settings = home / "settings.xml"
            settings.write_bytes(SETTINGS)
            personal = mappers / "KEYBOARD - Personal.xml"
            personal.write_bytes(b'<mapper device="KEYBOARD"><map value="ALT+A" action="play" />'
                                 b'<map value="NUMENTER" action="cue" /></mapper>')
            first = dj_setup.install_mapping(home)
            installed = Path(first["mapper"])
            self.assertIn(b'ALT+A', installed.read_bytes())
            self.assertIn(b'NUMENTER', installed.read_bytes())
            self.assertTrue(dj_setup.mapper_is_active(dj_setup.DjOptions(), home))
            disabled = dj_setup.DjOptions(g_keys=('none',) * 6)
            self.assertFalse(dj_setup.mapper_is_active(disabled, home))
            expected = SETTINGS.replace(b"KEYBOARD - Personal", b"KEYBOARD - Corner HUD DJ")
            self.assertEqual(expected, settings.read_bytes())
            dj_setup.install_mapping(home, options=dj_setup.DjOptions(filter_step=4))
            unrelated = expected.replace(b"Example", b"Changed after install")
            settings.write_bytes(unrelated)
            dj_setup.rollback_mapping(home)
            self.assertFalse(installed.exists())
            self.assertEqual(SETTINGS.replace(b"Example", b"Changed after install"), settings.read_bytes())
            self.assertIn(b'ALT+A', personal.read_bytes())

    @patch("dj_setup.virtualdj_running", return_value=False)
    def test_reinstall_uses_latest_previous_mapper_and_preserves_other_keyboard_attrs(self, _running):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            settings = home / 'settings.xml'
            settings.write_bytes(SETTINGS)
            dj_setup.install_mapping(home)
            switched = SETTINGS.replace(b'KEYBOARD - Personal', b'KEYBOARD - Other')
            settings.write_bytes(switched)
            dj_setup.install_mapping(home)
            test_settings = settings.read_bytes().replace(
                b'name="KEYBOARD"', b'name="KEYBOARD" option="changed"')
            settings.write_bytes(test_settings)
            dj_setup.rollback_mapping(home)
            selection = dj_setup.keyboard_selection(settings.read_bytes())
            self.assertEqual('KEYBOARD - Other', selection.get('mapper'))
            self.assertEqual('changed', selection.get('option'))

    @patch("dj_setup.virtualdj_running", return_value=False)
    def test_old_mapper_revision_stays_selected_but_requires_update(self, _running):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'settings.xml').write_bytes(SETTINGS)
            options = dj_setup.DjOptions()
            dj_setup.install_mapping(home, options=options)
            self.assertTrue(dj_setup.mapper_is_selected(home))
            self.assertTrue(dj_setup.mapper_is_active(options, home))
            manifest = home / dj_setup.STATE_DIR / 'installation.json'
            data = json.loads(manifest.read_text('utf-8'))
            data.pop('revision')
            manifest.write_text(json.dumps(data), encoding='utf-8')
            self.assertTrue(dj_setup.mapper_is_selected(home))
            self.assertFalse(dj_setup.mapper_is_active(options, home))

    @patch("dj_setup.virtualdj_running", return_value=True)
    def test_disallows_writes_while_running(self, _running):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with self.assertRaises(RuntimeError):
                dj_setup.install_mapping(home)
            self.assertEqual([], list(home.iterdir()))


if __name__ == "__main__":
    unittest.main()
