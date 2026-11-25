"""Keep the Music History track library up to date with VirtualDJ's analysis, in the background.

When VirtualDJ saves its database.xml (after analysing or playing tracks), this runs the
Music History project's `virtualdj-sync` command without a window. That uploads VirtualDJ's
BPM, key, cue points and automix mix-in/out points to Drive, where the daily job merges
them into TrackLibrary.json. Nothing runs while the database is unchanged.
"""
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

CHECK_SECONDS = 5 * 60
SETTLE_SECONDS = 60  # wait until VirtualDJ has finished writing the database
RUN_TIMEOUT = 10 * 60
INSTALL_TIMEOUT = 20 * 60
log = logging.getLogger('VoicemeeterCornerHUD.library_sync')


def music_history_python(folder: Path) -> Path:
    return folder / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def is_music_history(folder: Path) -> bool:
    return (folder / 'music_history' / '__main__.py').is_file() and music_history_python(folder).is_file()


def find_music_history(configured: str | None = None, here: Path | None = None) -> Path | None:
    """The configured Music History checkout, or one next to the HUD or in the usual places."""
    home = Path.home()
    here = here or Path(__file__).resolve().parent
    candidates = [Path(os.path.expandvars(configured))] if configured else []
    candidates += [here.parent / 'music-history', home / 'Documents' / 'GitHub' / 'music-history',
                   home / 'source' / 'repos' / 'music-history', home / 'Documents' / 'music-history',
                   home / 'Desktop' / 'music-history', home / 'music-history']
    return next((folder for folder in candidates if is_music_history(folder)), None)


class LibrarySync(threading.Thread):
    """Checks every few minutes; a changed database triggers one background upload."""

    def __init__(self, cfg: dict, stop: threading.Event, state_file: Path, *, find_database,
                 runner=subprocess.run, clock=time.time, interval=CHECK_SECONDS,
                 settle=SETTLE_SECONDS):
        super().__init__(daemon=True, name='VirtualDJ-library-sync')
        self.cfg = cfg
        self.stop = stop
        self.state_file = state_file
        self.find_database = find_database
        self.runner = runner
        self.clock = clock
        self.interval = interval
        self.settle = settle
        self.wake = threading.Event()
        self.force = False
        self.installed = False
        self.status = 'Waiting for the first check'
        self.synced_mtime = self._load_state().get('database_mtime')

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text('utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, **values) -> None:
        try:
            self.state_file.write_text(json.dumps({**self._load_state(), **values}, indent=2), 'utf-8')
        except OSError:
            log.exception('Could not save the library sync state')

    def request_now(self) -> None:
        self.force = True
        self.wake.set()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.check()
            except Exception:  # never take the HUD down
                log.exception('VirtualDJ library sync failed')
                self.status = 'Sync failed; see hud.log'
            self.wake.wait(self.interval)
            self.wake.clear()

    def _run(self, command: list[str], folder: Path, timeout: int):
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        return self.runner(command, cwd=str(folder), capture_output=True, text=True,
                           timeout=timeout, creationflags=flags)

    def check(self) -> bool:
        """Upload if the database changed since the last upload. Returns True after an upload."""
        force, self.force = self.force, False
        if not self.cfg.get('vdj_library_sync', True) and not force:
            self.status = 'Off'
            return False
        database = self.find_database()
        if database is None or not database.is_file():
            self.status = "VirtualDJ's database.xml not found"
            return False
        folder = find_music_history(self.cfg.get('music_history_dir'))
        if folder is None:
            self.status = 'Music History project not found (set music_history_dir)'
            return False
        mtime = database.stat().st_mtime
        if not force and mtime == self.synced_mtime:
            return False
        if not force and self.clock() - mtime < self.settle:
            self.status = 'Waiting for VirtualDJ to finish saving'
            return False
        python = str(music_history_python(folder))
        try:
            result = self._run([python, '-m', 'music_history', 'virtualdj-sync',
                                '--database', str(database)], folder, RUN_TIMEOUT)
            if result.returncode != 0 and 'ModuleNotFoundError' in (result.stderr or '') \
                    and not self.installed:
                # The project was updated but its packages weren't: install them once, quietly.
                self.installed = True
                self.status = 'Installing Music History requirements'
                self._run([python, '-m', 'pip', 'install', '-q', '-r', 'requirements.txt'],
                          folder, INSTALL_TIMEOUT)
                result = self._run([python, '-m', 'music_history', 'virtualdj-sync',
                                    '--database', str(database)], folder, RUN_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as error:
            self.status = f'Sync failed ({type(error).__name__})'
            log.warning('VirtualDJ library sync could not run: %s', error)
            return False
        output = (result.stdout or '').strip().splitlines()
        if result.returncode != 0:
            detail = ((result.stderr or '').strip().splitlines() or ['no details'])[-1]
            self.status = 'Sync failed; see hud.log'
            log.warning('VirtualDJ library sync failed (%s): %s', result.returncode, detail)
            return False
        self.synced_mtime = mtime
        stamp = time.strftime('%H:%M', time.localtime(self.clock()))
        self.status = f'Synced at {stamp}: {output[-1] if output else "done"}'
        self._save_state(database_mtime=mtime, last_status=self.status)
        log.info('VirtualDJ library sync: %s', self.status)
        return True
