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
        self.assertIn("deck 1 eq_high_freq -4%", actions["CTRL+NUM8"])
        self.assertIn("deck 2 eq_high_freq -4%", actions["CTRL+NUM8"])
        self.assertIn("deck 1 filter_activate", actions["CTRL+RETURN"])
        self.assertIn("deck 2 filter_activate", actions["CTRL+RETURN"])
        self.assertIn("deck 2 sync", actions["CTRL+NUM+"])
        self.assertIn("deck 1 sync", actions["NUM+"])
        self.assertIn("deck 2 eq_high_freq -8%", actions["NUM8"])
        self.assertIn("deck 2 filter +3%", actions["NUM."])
        self.assertIn("deck 1 eq_reset", actions["CTRL+F12"])
        self.assertNotIn("RETURN", actions)
        self.assertNotIn("NUMENTER", actions)
        self.assertIn("deck 1 filter 50%", actions["CTRL+NUM."])
        self.assertIn("deck 1 filter +3%", actions["CTRL+NUM."])
        self.assertIn("deck 2 level -2%", actions["CTRL+NUM1"])
        self.assertIn("isrepeat", actions["CTRL+NUM/"])

    def test_standard_transport_keys_keep_user_overrides(self):
        base = b'<mapper device="KEYBOARD"><map value="ALT+Q" action="cue" /></mapper>'
        root = ET.fromstring(dj_setup.build_mapper(base=base))
        actions = {item.get('value'): item.get('action') for item in root.findall('map')}
        self.assertEqual('cue', actions['ALT+Q'])
        self.assertIn('deck 2 sync', actions['ALT+U'])
        self.assertEqual('deck 1 play_button', actions['ALT+1'])
        self.assertEqual('deck 2 cue_button', actions['ALT+8'])
        self.assertEqual('deck 1 stop_button', actions['ALT+3'])
        self.assertEqual('deck 2 loop_button', actions['ALT+0'])

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
