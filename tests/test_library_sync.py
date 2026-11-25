import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import library_sync
from library_sync import LibrarySync, find_music_history


class Result:
    def __init__(self, code=0, out='Uploaded VirtualDJ analysis for 12 tracks.\n', err=''):
        self.returncode, self.stdout, self.stderr = code, out, err


class LibrarySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.project = root / 'music-history'
        (self.project / 'music_history').mkdir(parents=True)
        (self.project / 'music_history' / '__main__.py').write_text('')
        python = library_sync.music_history_python(self.project)
        python.parent.mkdir(parents=True)
        python.write_text('')
        self.database = root / 'VirtualDJ' / 'database.xml'
        self.database.parent.mkdir()
        self.database.write_text('<VirtualDJ_Database/>')
        os.utime(self.database, (1000, 1000))
        self.calls = []
        self.result = Result()
        self.now = 2000.0

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **cfg):
        def runner(command, **options):
            self.calls.append((command, options))
            return self.result

        return LibrarySync({'music_history_dir': str(self.project), **cfg}, threading.Event(),
                           Path(self.tmp.name) / 'state.json', find_database=lambda: self.database,
                           runner=runner, clock=lambda: self.now)

    def test_uploads_once_per_database_change_without_a_window(self):
        sync = self.sync()
        self.assertTrue(sync.check())
        command, options = self.calls[0]
        self.assertEqual(command[1:5], ['-m', 'music_history', 'virtualdj-sync', '--database'])
        self.assertEqual(options['cwd'], str(self.project))
        if os.name == 'nt':
            self.assertEqual(options['creationflags'], subprocess.CREATE_NO_WINDOW)
        self.assertIn('12 tracks', sync.status)
        self.assertFalse(sync.check())
        self.assertFalse(self.sync().check())  # the last upload survives a restart
        os.utime(self.database, (1500, 1500))
        self.assertTrue(sync.check())
        self.assertEqual(len(self.calls), 2)

    def test_waits_while_virtualdj_is_still_writing(self):
        self.now = 1010.0
        sync = self.sync()
        self.assertFalse(sync.check())
        self.assertIn('Waiting', sync.status)
        sync.request_now()
        self.assertTrue(sync.check())

    def test_a_failed_upload_is_retried_on_the_next_check(self):
        self.result = Result(1, '', 'Traceback...\nRuntimeError: no token')
        sync = self.sync()
        with self.assertLogs('VoicemeeterCornerHUD.library_sync', 'WARNING'):
            self.assertFalse(sync.check())
        self.assertIn('failed', sync.status)
        self.result = Result()
        self.assertTrue(sync.check())

    def test_missing_packages_are_installed_once_then_retried(self):
        results = iter([Result(1, '', "ModuleNotFoundError: No module named 'numpy'"), Result(0, ''), Result()])
        sync = self.sync()
        sync.runner = lambda command, **options: (self.calls.append(command), next(results))[1]
        self.assertTrue(sync.check())
        self.assertEqual(self.calls[1][1:4], ['-m', 'pip', 'install'])
        self.assertEqual(len(self.calls), 3)

    def test_turned_off_or_missing_pieces_do_nothing(self):
        self.assertFalse(self.sync(vdj_library_sync=False).check())
        self.database.unlink()
        sync = self.sync()
        self.assertFalse(sync.check())
        self.assertIn('database.xml not found', sync.status)
        self.assertEqual(self.calls, [])

    def test_finds_the_project_next_to_the_hud(self):
        hud = Path(self.tmp.name) / 'voicemeeter-virtualdj-hud'
        hud.mkdir()
        self.assertEqual(find_music_history(None, here=hud), self.project)
        self.assertEqual(find_music_history(str(self.project), here=hud), self.project)
        self.assertIsNone(find_music_history(None, here=Path(self.tmp.name) / 'elsewhere' / 'hud'))


if __name__ == '__main__':
    unittest.main()
