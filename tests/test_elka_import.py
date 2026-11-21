import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elka_import import auto_discover_elka_nodes


class ElkaSavedLayoutTests(unittest.TestCase):
    def test_reads_active_dotted_layout_and_stable_id_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            roaming = root / 'Roaming'
            host = roaming / 'ElkaSoft' / 'VoiceMeeterFxHost'
            host.mkdir(parents=True)
            (host / 'wpf-main-window.json').write_text(json.dumps({
                'Kind': 2,
                'PluginNodes': [
                    {'Name': 'kHs Filter [VST3]', 'InstanceId': 0, 'Slot': 7},
                    {'Name': 'Missing plugin', 'MissingPlugin': True, 'InstanceId': 4},
                ],
                'EditionWorkspaces': {'Potato': {'PluginNodes': [
                    {'Name': 'Separate workspace', 'InstanceId': 0, 'Slot': 0},
                ]}},
            }), encoding='utf-8')
            with patch.dict('os.environ', {'APPDATA': str(roaming), 'LOCALAPPDATA': str(root)}):
                self.assertEqual([{'name': 'kHs Filter', 'id': 0}], auto_discover_elka_nodes())
