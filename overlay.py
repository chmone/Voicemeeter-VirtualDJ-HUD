"""Windows Voicemeeter Corner HUD with optional Spotify-only VST filter control.

EQ/meters are normally read-only. Optional exclusive numpad shortcuts
can adjust Voicemeeter directly. Filter commands go to ELKA FX Host;
no audio samples are processed by this overlay.
"""
from __future__ import annotations

import json
import logging
import math
import re
import os
import queue
import urllib.request
import urllib.parse
import socket
import struct
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageDraw
from screeninfo import get_monitors
import pystray

from dj_keybinds import DjKeybindGuide
from dj_setup import BASS_SWAP_BINDING, EQ_MINIMUM_BINDING, RESET_BOTH_BINDING, DjOptions
from dj_state import DeckEstimates
from library_sync import LibrarySync
from keyboard_controls import (
    NAVIGATION, VK_NUMLOCK, ShortcutPresses, foreground_is_virtualdj, identify_key, process_executable,
    send_dj_keypad,
)
import macrobuttons

APP = 'VoicemeeterCornerHUD'
CONFIG_HOME = Path(os.environ.get('APPDATA', str(Path.home()))) / APP
CONFIG_HOME.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_HOME / 'settings.json'
LOG_FILE = CONFIG_HOME / 'hud.log'
MACROBUTTONS_BACKUP = CONFIG_HOME / 'MacroButtonConfig.before-dj.xml'
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
shortcut_log = logging.getLogger(APP + '.shortcuts')
if os.environ.get('CORNERHUD_LOG_SHORTCUTS') == '1':
    shortcut_log.setLevel(logging.DEBUG)

DEFAULTS = {
    'music_strip': 4,  # Voicemeeter AUX Input (Spotify in the prior configuration)
    'game_strip': 3,   # Voicemeeter Input (Windows / game)
    'monitor_index': 0,
    'corner': 'bottom-right',
    'opacity': 0.94,
    'view_mode': 'large',  # large mixer or compact EQ + filter overlay
    'filter_step': 12,  # open: 20kHz; stored independently of the three EQ bands
    'filter_enabled': True,  # False = true dry bypass in ELKA; cutoff is retained
    'bipolar_position': 0,  # DJ dial: negative low pass, center dry, positive high pass
    'fx_type_parameter': None,  # ELKA Info > kHs Filter > Type, optional
    'fx_type_name': 'Low Pass',
    'fx_type_values': {},  # fill exact Type parameter choices from ELKA Info; never guess
    'fx_slots': [],  # other *already loaded and routed* ELKA VST nodes: {name,id}
    'vst_scan_dirs': [],  # optional user-selected plugin folders beyond default VST3 location
    'exclusive_shortcuts': True,  # enable Num Lock-off macros and saved shortcut aliases
    'capture_number_row': False,  # optional Ctrl+1..0 top-row mapping (may shadow app shortcuts)
    'suppress_all_numpad': True,
    'shortcut_defaults_v6': False,
    'dj_mode': False,
    'dj_selected_deck': 1,
    'dj_auto_focus': False,
    'dj_pause_macrobuttons': True,
    'dj_http_readback': False,
    'vdj_library_sync': True,  # upload VirtualDJ's analysis to Music History when it changes
    'music_history_dir': '',  # blank = find the music-history checkout automatically
    'macrobuttons_resume': None,
    'macrobuttons_pending_pid': None,
    'macrobuttons_saved_map': False,
    'music_g_actions': {},
    'music_home_action': None,
    'music_ctrl_num_actions': None,
    'dj_http_base': 'http://127.0.0.1:80',
    'key_actions': {
        '0': 'filter_down', '.': 'filter_up',
        '2': 'low_down', '3': 'low_up',
        '5': 'mid_down', '6': 'mid_up',
        '8': 'high_down', '9': 'high_up',
        '1': 'music_down', '4': 'music_up', '7': 'music_mute',
        '+': 'clutch', '-': 'bass_reset', '*': 'none', '/': 'none', 'enter': 'filter_toggle',
    },
    'active_fx_id': None,  # None = primary kHs Filter, or one of fx_slots IDs
    'fx_vst_id': None,  # stable plugin ID from ELKA FX Host > VST > Info
    'fx_cutoff_parameter': None,  # cutoff automation index from the same Info window
    'fx_adaptive_cutoff': None,
    'fx_parameter_unit': 'Hz',  # adjust only if ELKA VST Info says kHz
    'fx_vban_port': 6981,
    'fx_vban_stream': 'Command1',
    'x': None,
    'y': None,
}

BG = '#0D131D'
PANEL = '#17212F'
BORDER = '#354457'
FG = '#E7EDF5'
MUTED = '#8E9BAD'
TRACK = '#273443'
GREEN = '#54D7B6'
BLUE = '#6CA7EA'
ORANGE = '#EEA35B'
RED = '#FF7474'
LARGE_SIZE = (366, 306)
SMALL_SIZE = (240, 132)
MUSIC_EQ_LIMIT_DB = 12.0
MUSIC_EQ_STEP_DB = 1.92  # 8% of the full -12 to +12 dB range
# Each keypress advances exactly one musical (roughly logarithmic) frequency step.
FILTER_FREQS = (250, 350, 500, 700, 1000, 1500, 2200, 3300, 5000, 7500, 11000, 16000, 20000)
# In high-pass mode the left-to-right knob moves from a subtle bass rolloff
# to a dramatic upper-mid/high-pass sweep. Center is exact bypass.
HP_FREQS = (35, 50, 75, 110, 160, 240, 350, 500, 750, 1100, 1700, 2600)


def dial_filter(position: int) -> tuple[str, int | None]:
    position = max(-12, min(12, int(position)))
    if position < 0:
        return 'Low Pass', FILTER_FREQS[12 + position]
    if position > 0:
        return 'High Pass', HP_FREQS[position-1]
    return 'Center', None


def filter_frequency(index: int) -> int:
    return FILTER_FREQS[max(0, min(len(FILTER_FREQS) - 1, int(index)))]


def filter_text(frequency: int) -> str:
    return (f'{frequency / 1000:g}k' if frequency >= 1000 else str(frequency)) + 'Hz'


def clean_slots(slots):
    cleaned, seen = [], set()
    for row in slots:
        if not isinstance(row, dict):
            continue
        try:
            identifier = int(row['id'])
        except (KeyError, ValueError, TypeError):
            continue
        name = str(row.get('name', '')).strip()[:38]
        if identifier >= 0 and identifier not in seen and name:
            cleaned.append({'id': identifier, 'name': name})
            seen.add(identifier)
    return cleaned[:48]


# Standard VST3 locations.  This is a local filesystem inventory, not an ELKA
# graph query: installed effects are not necessarily instantiated or routed.
def discover_kilohearts_plugins(extra_paths=(), platform=None):
    if (platform if platform is not None else os.name) != 'nt':
        return []
    roots = [Path(os.environ.get('CommonProgramFiles', r'C:\Program Files\Common Files')) / 'VST3',
             Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'Common' / 'VST3']
    roots += [Path(path) for path in extra_paths if path]
    found = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            # Kilohearts puts separate VST3 bundles in (or near) this folder;
            # depth cap avoids traversing arbitrary user trees.
            candidates = list(root.glob('*.vst3')) + list(root.glob('*/*.vst3')) + list(root.glob('*/*/*.vst3'))
            for item in candidates:
                name = item.stem.strip()
                if (name.casefold().startswith(('khs ', 'kilohearts '))
                        or 'kilohearts' in str(item.relative_to(root)).casefold()):
                    if not name.lower().startswith(('khs ', 'kilohearts ')):
                        name = 'kHs ' + name
                    found[name.casefold()] = {'name': name[:56], 'path': str(item)}
        except (OSError, PermissionError):
            logging.exception('Could not inspect VST3 folder: %s', root)
    return sorted(found.values(), key=lambda item: item['name'].casefold())


def normalize_fx_name(value):
    name = re.sub(r'\s*\[VST[23]\]$', '', str(value), flags=re.I)
    return re.sub(r'[^a-z0-9]+', '', name.casefold().replace('kilohearts', 'khs'))


ACTIONS = {
    'none': 'No assignment',
    'low_down': f'Bass −{MUSIC_EQ_STEP_DB:g} dB', 'low_up': f'Bass +{MUSIC_EQ_STEP_DB:g} dB',
    'mid_down': f'Mids −{MUSIC_EQ_STEP_DB:g} dB', 'mid_up': f'Mids +{MUSIC_EQ_STEP_DB:g} dB',
    'high_down': f'Highs −{MUSIC_EQ_STEP_DB:g} dB', 'high_up': f'Highs +{MUSIC_EQ_STEP_DB:g} dB',
    'filter_down': 'Filter darker', 'filter_up': 'Filter brighter',
    'filter_toggle': 'Toggle music filter',
    'music_down': 'Music −2 dB', 'music_up': 'Music +2 dB',
    'music_mute': 'Music mute toggle', 'clutch': 'Clutch toggle',
    'music_fade_up': 'Music fade +2 dB', 'music_fade_down': 'Music fade −2 dB',
    'music_clutch_fade': 'Music clutch fade',
    'music_bass_drop': 'Bass drop / boost', 'music_normalize': 'Normalize EQ and gain',
    'bass_cut_boost': 'Bass cut / boost', 'bass_reset': 'Bass reset',
    'speakers': 'Switch to A1 speakers', 'headphones': 'Switch to A2 headphones',
}


class FXFilterSender:
    """Send supported VFX text commands to ELKA, no invented Voicemeeter filter API."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.frame = 0

    @property
    def configured(self) -> bool:
        return (isinstance(self.cfg.get('fx_vst_id'), int)
                and self.cfg['fx_vst_id'] >= 0
                and isinstance(self.cfg.get('fx_cutoff_parameter'), int)
                and self.cfg['fx_cutoff_parameter'] >= 0)

    def command(self, frequency: int) -> str:
        if not self.configured:
            raise ValueError('ELKA FX Host VST ID and cutoff parameter have not been configured')
        from kilohearts import cutoff_value
        stable_id = self.cfg['fx_vst_id']
        parameter = self.cfg['fx_cutoff_parameter']
        adaptive = self.cfg.get('fx_adaptive_cutoff') == [stable_id, parameter]
        value = cutoff_value(frequency, self.cfg.get('fx_parameter_unit', 'Hz'), adaptive=adaptive)
        return f'VFX.VST({stable_id}).Parameter({parameter})={value};'

    def packet(self, command: str) -> bytes:
        stream = self.cfg.get('fx_vban_stream', 'Command1').encode('ascii')
        if len(stream) > 16:
            raise ValueError('VBAN stream name may not exceed 16 bytes')
        header = struct.pack('<4s4B16sI', b'VBAN', 0x52, 0, 0, 0x10,
                             stream.ljust(16, b'\0'), self.frame & 0xFFFFFFFF)
        self.frame += 1
        payload = command.encode('utf-8')
        if len(payload) > 1436:
            raise ValueError('VBAN TEXT packet too large')
        return header + payload

    def send_text(self, command: str) -> None:
        port = int(self.cfg.get('fx_vban_port', 6981))
        if not 1 <= port <= 65535:
            raise ValueError('Invalid VBAN port')
        # UDP is write-only: ELKA does not acknowledge state changes.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(self.packet(command), ('127.0.0.1', port))

    def send(self, frequency: int) -> None:
        if self.configured:
            self.send_text(self.command(frequency))

    def bypass_command(self, vst_id: int, bypass: bool) -> str:
        return f'VFX.VST({int(vst_id)}).Bypass={1 if bypass else 0};'

    def type_command(self, mode: str) -> str:
        if not self.configured or not isinstance(self.cfg.get('fx_type_parameter'), int):
            raise ValueError('Configure the Kilohearts Type parameter in ELKA Info first')
        value = self.cfg['fx_type_values'][mode]
        return f"VFX.VST({self.cfg['fx_vst_id']}).Parameter({self.cfg['fx_type_parameter']})={int(value)};"


class WindowsFilterHotkeys(threading.Thread):
    """Run the low-level hook on a dedicated Win32 message loop."""

    def __init__(self, commands, stop, cfg, input_mode):
        super().__init__(name='Filter-global-shortcuts', daemon=True)
        self.commands = commands
        self.stop = stop
        self.cfg = cfg
        self.input_mode = input_mode
        self.thread_id = None
        self.ready = threading.Event()
        self.presses = ShortcutPresses()

    def run(self):
        if os.name != 'nt':
            self.ready.set()
            return
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        kernel = ctypes.windll.kernel32
        message = wintypes.MSG()
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.thread_id = kernel.GetCurrentThreadId()

        class KeyboardEvent(ctypes.Structure):
            _fields_ = [('vkCode', wintypes.DWORD), ('scanCode', wintypes.DWORD),
                        ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                        ('dwExtraInfo', ctypes.c_size_t)]

        callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                     ctypes.c_size_t, ctypes.c_ssize_t)
        user.SetWindowsHookExW.argtypes = (ctypes.c_int, callback, wintypes.HINSTANCE, wintypes.DWORD)
        user.SetWindowsHookExW.restype = wintypes.HHOOK
        user.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
        user.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        user.GetAsyncKeyState.restype = ctypes.c_short
        user.GetKeyState.argtypes = (ctypes.c_int,)
        user.GetKeyState.restype = ctypes.c_short

        def pressed(vk):
            return bool(user.GetAsyncKeyState(vk) & 0x8000)

        self.presses = ShortcutPresses(bool(user.GetKeyState(VK_NUMLOCK) & 1), pressed(VK_NUMLOCK))
        ctrl_keys = set()
        track_ctrl = False

        @callback
        def keyboard_proc(code, wparam, lparam):
            nonlocal track_ctrl
            if code == 0:
                try:
                    info = ctypes.cast(lparam, ctypes.POINTER(KeyboardEvent)).contents
                    down = not (info.flags & 0x80)
                    if info.vkCode == VK_NUMLOCK:
                        self.presses.num_lock_event(down)
                    if info.vkCode in (0x11, 0xA2, 0xA3):
                        track_ctrl = True
                        is_right = info.vkCode == 0xA3 or bool(info.flags & 1)
                        if info.flags & 0x80:
                            ctrl_keys.discard(is_right)
                        else:
                            ctrl_keys.add(is_right)
                    key = identify_key(info.vkCode, info.scanCode, info.flags)
                    if key is not None:
                        ctrl = bool(ctrl_keys) if track_ctrl else pressed(0x11)
                        alt = bool(info.flags & 0x20) or pressed(0x12)
                        other = any(pressed(vk) for vk in (0x10, 0x5B, 0x5C))
                        # A real, unmodified digit reflects the keyboard layout's
                        # lock state even if a toggle occurred on a secure desktop.
                        if (down and not (ctrl or alt or other or info.flags & 0x10)
                                and key[0] == 'keypad' and key[1] in '0123456789.'):
                            self.presses.num_lock = info.vkCode not in NAVIGATION
                        mode, options = self.input_mode()
                        suppress, command = self.presses.event(
                            key, down=down, ctrl=ctrl, alt=alt, other_modifier=other,
                            mode=mode, exclusive=self.cfg['exclusive_shortcuts'],
                            number_row=self.cfg['capture_number_row'],
                            music_actions=self.cfg['key_actions'],
                            music_g_keys=self.cfg['music_g_actions'], dj_options=options,
                            home_action=self.cfg['music_home_action'],
                            music_ctrl_actions=self.cfg['music_ctrl_num_actions'])
                        if isinstance(command, tuple) and command[0] == 'dj_keypad':
                            _, keypad_key, action, coarse = command
                            command = ('dj_key', action, coarse) if send_dj_keypad(keypad_key) else None
                        if command is not None:
                            self.commands.put(command)
                        if suppress:
                            return 1
                except Exception:
                    self.commands.put('hotkey_failed')
            return user.CallNextHookEx(hook, code, wparam, lparam)

        hook = user.SetWindowsHookExW(13, keyboard_proc, None, 0)
        if not hook:
            logging.warning('Keyboard hook unavailable')
            self.commands.put('hotkey_failed')
        self.ready.set()
        try:
            while not self.stop.is_set() and user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            if hook:
                user.UnhookWindowsHookEx(hook)

    def wake_and_exit(self):
        if os.name == 'nt' and self.thread_id is not None:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)


def dimensions(mode: str) -> tuple[int, int]:
    return SMALL_SIZE if mode == 'small' else LARGE_SIZE


def load_config() -> dict:
    data = DEFAULTS.copy()
    try:
        saved = json.loads(CONFIG_FILE.read_text('utf-8-sig'))
        data.update(saved)
    except (OSError, ValueError, TypeError):
        pass
    actions = data.get('key_actions')
    if not isinstance(actions, dict):
        actions = DEFAULTS['key_actions']
    actions = actions.copy()
    actions.pop('main_enter', None)
    data.pop('block_numpad_enter', None)
    if not data.get('keypad_profile_configured'):
        if actions.get('enter') in (None, 'none'):
            actions['enter'] = 'filter_toggle'
        if data.get('dj_enter') in (None, 'sync'):
            data['dj_enter'] = 'filter_toggle'
        data['keypad_profile_configured'] = True
    data['key_actions'] = actions
    return data


def save_config(data: dict, *, required=False) -> None:
    try:
        from dj_setup import atomic_write
        atomic_write(CONFIG_FILE, (json.dumps(data, indent=2) + '\n').encode('utf-8'))
    except OSError:
        if required:
            raise
        logging.exception('Failed to save settings')


def hold_single_instance():
    """Prevents duplicate windows when manually launched and started at login."""
    if os.name != 'nt':
        return None
    import ctypes
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\VoicemeeterCornerHUD_OneInstance')
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    return mutex


def state_from_vm(vm, music_strip: int, game_strip: int) -> dict:
    # Calling IsParametersDirty refreshes the Remote API parameter cache.
    _ = vm.pdirty
    music = vm.strip[music_strip]
    game = vm.strip[game_strip]

    def stereo_peak(strip):
        levels = tuple(strip.levels.postmute)  # dBFS, -200.0 if silent
        return max(levels[:2]) if levels else -200.0

    return {
        'online': True,
        'music_gain': float(music.gain),
        'game_gain': float(game.gain),
        'music_mute': bool(music.mute),
        'game_mute': bool(game.mute),
        'low': float(music.bass),
        'mid': float(music.mid),
        'high': float(music.treble),
        'music_peak': stereo_peak(music),
        'game_peak': stereo_peak(game),
        'a1': bool(vm.bus[0].mute) is False,
        'a2': bool(vm.bus[1].mute) is False,
    }


class VoiceReader(threading.Thread):
    def __init__(self, config: dict, results: queue.Queue, stop: threading.Event, actions: queue.Queue):
        super().__init__(name='Voicemeeter-reader', daemon=True)
        self.cfg = config
        self.results = results
        self.stop = stop
        self.actions = actions
        self.clutch_saved_gain = None
        self.bass_phase = False
        self.bass_drop_active = False
        self.clutch_fade_active = False
        self.fade = None

    def apply_action(self, vm, action):
        music = vm.strip[int(self.cfg["music_strip"])]
        eq_attr = {"low": "bass", "mid": "mid", "high": "treble"}
        if action == 'speakers':
            vm.bus[1].mute = True
            vm.bus[0].mute = False
        elif action == 'headphones':
            vm.bus[0].mute = True
            vm.bus[1].mute = False
        elif action.endswith(("_up", "_down")) and action.rsplit("_", 1)[0] in eq_attr:
            band, direction = action.rsplit("_", 1)
            attr = eq_attr[band]
            current = float(getattr(music, attr))
            step = MUSIC_EQ_STEP_DB if direction == "up" else -MUSIC_EQ_STEP_DB
            setattr(music, attr, max(-MUSIC_EQ_LIMIT_DB, min(MUSIC_EQ_LIMIT_DB, current + step)))
        elif action in ("music_up", "music_down"):
            music.gain = max(-60., min(12., float(music.gain) + (2. if action == "music_up" else -2.)))
        elif action in ("music_fade_up", "music_fade_down", "music_clutch_fade"):
            if action == "music_clutch_fade":
                change, duration = (30, .4) if self.clutch_fade_active else (-30, .3)
                self.clutch_fade_active = not self.clutch_fade_active
            else:
                change, duration = (2 if action == "music_fade_up" else -2), .15
            current = float(music.gain)
            start = self.fade[2] if self.fade else current
            target = max(-60., min(12., start + change))
            self.fade = (time.monotonic(), current, target, duration)
        elif action == "music_bass_drop":
            music.bass = 8.0 if self.bass_drop_active else -12.0
            self.bass_drop_active = not self.bass_drop_active
        elif action == "music_normalize":
            self.fade = None
            self.clutch_fade_active = False
            self.clutch_saved_gain = None
            music.bass = music.mid = music.treble = music.gain = 0.0
        elif action == "music_mute":
            music.mute = not bool(music.mute)
        elif action == "clutch":
            current = float(music.gain)
            if self.clutch_saved_gain is None:
                self.clutch_saved_gain = current
                target, duration = -30., 0.3
            else:
                target, duration = self.clutch_saved_gain, 0.4
                self.clutch_saved_gain = None
            self.fade = (time.monotonic(), current, target, duration)
        elif action == "bass_cut_boost":
            music.bass = 6. if self.bass_phase else -12.
            self.bass_phase = not self.bass_phase
        elif action == "bass_reset":
            music.bass = 0.
            self.bass_phase = False

    def update_fade(self, vm):
        if self.fade is None:
            return
        started, initial, target, duration = self.fade
        fraction = min(1., (time.monotonic()-started) / duration)
        vm.strip[int(self.cfg['music_strip'])].gain = initial+(target-initial)*fraction
        if fraction >= 1.:
            self.fade = None

    def publish(self, value: dict) -> None:
        try:
            self.results.put_nowait(value)
        except queue.Full:
            try:
                self.results.get_nowait()
            except queue.Empty:
                pass
            self.results.put_nowait(value)

    def run(self) -> None:
        try:
            import voicemeeterlib
        except Exception as exc:
            logging.exception('Cannot import voicemeeterlib')
            self.publish({'online': False, 'reason': f'Install voicemeeter-api: {type(exc).__name__}'})
            return
        while not self.stop.is_set():
            try:
                # Writes occur only after an explicitly enabled exclusive shortcut.
                with voicemeeterlib.api('banana', timeout=1.5) as vm:
                    logging.info('Connected to Voicemeeter Banana')
                    while not self.stop.is_set():
                        try:
                            while True:
                                action = self.actions.get_nowait()
                                try:
                                    self.apply_action(vm, action)
                                except Exception:
                                    logging.exception("Failed direct Voicemeeter action: %s", action)
                        except queue.Empty:
                            pass
                        self.update_fade(vm)
                        reading = state_from_vm(vm, int(self.cfg['music_strip']), int(self.cfg['game_strip']))
                        self.publish(reading)
                        self.stop.wait(0.1)
            except Exception as exc:
                logging.warning('Voicemeeter connection/read failure: %s', exc)
                self.publish({'online': False, 'reason': 'Waiting for Voicemeeter'})
                self.stop.wait(2.5)


def meter_fraction(db: float) -> float:
    if db <= -60:
        return 0.0
    return max(0.0, min(1.0, (db + 60) / 60))


def tray_image() -> Image.Image:
    im = Image.new('RGB', (64, 64), '#17212F')
    d = ImageDraw.Draw(im)
    for i, h in enumerate((21, 39, 30, 45)):
        x = 10 + i * 13
        d.rounded_rectangle((x, 56-h, x+7, 56), radius=3,
                            fill=['#54D7B6', '#6CA7EA', '#EEA35B', '#54D7B6'][i])
    return im


class HUD:
    def __init__(self):
        self.cfg = load_config()
        if self.cfg.get('view_mode') not in ('small', 'large'):
            self.cfg['view_mode'] = 'large'
        self.cfg['filter_step'] = max(0, min(len(FILTER_FREQS)-1, int(self.cfg.get('filter_step', 12))))
        if not self.cfg.get('shortcut_defaults_v6'):
            # Migrate saved v5 defaults once, retaining the user's custom map.
            self.cfg['shortcut_defaults_v6'] = True
            self.cfg['bipolar_position'] = min(0, int(self.cfg['filter_step'])-12)
            save_config(self.cfg)
        self.cfg['bipolar_position'] = max(-12, min(12,int(self.cfg.get('bipolar_position',0))))
        resume_dj = bool(self.cfg.get('dj_mode'))
        self.cfg['dj_mode'] = False
        try:
            self.dj_options = DjOptions.from_config(self.cfg)
        except (ValueError, TypeError):
            self.dj_options = DjOptions()
        from dj_setup import mapper_is_selected
        self.dj_mapper_selected = mapper_is_selected()
        self.dj_waiting = False
        self.dj_pid = None
        self.dj_estimate_pid = None
        self.dj_estimates = DeckEstimates()
        self.dj_auto_blocked = False
        self.dj_auto_blocked_pid = None
        self.dj_last_mode_check = 0.0
        self.dj_focused = False
        self.filter_probe_running = False
        self.filter_pending_step = None
        if not isinstance(self.cfg.get('music_g_actions'), dict):
            self.cfg['music_g_actions'] = {}
        self.cfg['filter_enabled'] = bool(self.cfg.get('filter_enabled', True))
        if not isinstance(self.cfg.get('fx_slots'), list):
            self.cfg['fx_slots'] = []
        self.cfg['fx_slots'] = clean_slots(self.cfg['fx_slots'])
        if not isinstance(self.cfg.get('fx_type_values'), dict):
            self.cfg['fx_type_values'] = DEFAULTS['fx_type_values'].copy()
        self.filter_sender = FXFilterSender(self.cfg)
        self.hotkey_error = False
        self.messages = queue.Queue(maxsize=3)
        self.commands = queue.Queue()
        self.vm_actions = queue.Queue()
        self.stop_event = threading.Event()
        self.hotkey_stop_event = threading.Event()
        self.root = tk.Tk()
        self.dj_keybinds_requested = False
        self.dj_keybind_guide = DjKeybindGuide(
            self.root, self.hide_dj_keybinds, bg=BG, panel=PANEL, fg=FG, muted=MUTED,
            accent=BLUE, border=BORDER)
        self.context_menu = None
        self.root.title('Voicemeeter Corner HUD')
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', float(self.cfg['opacity']))
        self.root.resizable(False, False)
        width, height = self.size
        self.canvas = tk.Canvas(self.root, width=width, height=height, bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.root.update_idletasks()
        self.position_window()
        self.root.bind('<Button-1>', self.drag_start)
        self.root.bind('<Motion>', self.cursor_hint)
        self.root.bind('<B1-Motion>', self.drag_motion)
        self.root.bind('<ButtonRelease-1>', self.drag_end)
        self.root.bind('<Button-3>', self.open_context)
        self.root.bind('<Escape>', lambda _e: self.toggle_visibility())
        self.root.bind('<Control-Shift-q>', lambda _e: self.quit())
        self.drag = None
        self.current = {'online': False, 'reason': 'Connecting...'}
        self.dj_values = {}
        self.dj_connected = False
        self.dj_last_read = 0.0
        self.filter_warning = ''
        self.peak_display = [0.0, 0.0]
        self.tray = self.make_tray()
        self.reader = VoiceReader(self.cfg, self.messages, self.stop_event, self.vm_actions)
        self.reader.start()
        self.hotkeys = WindowsFilterHotkeys(self.commands, self.hotkey_stop_event, self.cfg, self.keyboard_mode)
        self.hotkeys.start()
        from dj_setup import vdj_home
        self.library_sync = LibrarySync(self.cfg, self.stop_event, CONFIG_HOME / 'library_sync.json',
                                        find_database=lambda: vdj_home() / 'database.xml')
        self.library_sync.start()
        threading.Thread(target=self.run_tray, daemon=True, name='Tray').start()
        self.root.after(100, self.tick)
        # Re-send remembered settings after ELKA has had time to start.
        self.root.after(4000, self.reapply_filter)
        self.root.after(16000, self.reapply_filter)
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
        self.root.after(250, lambda: self.restore_mode(resume_dj))
        self.root.after(500, self.discover_filter_controls)

    @property
    def size(self) -> tuple[int, int]:
        if self.cfg.get('dj_mode'):
            return (370, 214) if self.cfg['view_mode']=='small' else (480, 300)
        return dimensions(self.cfg['view_mode'])

    def mode_button_bounds(self) -> tuple[int, int, int, int]:
        width, _ = self.size
        # Tight 18x18px button, centered 9pt glyph: ↗ expand / ↙ shrink.
        if self.cfg['view_mode'] == 'small':
            return (width - 26, 6, width - 8, 24)
        return (width - 29, 7, width - 11, 25)

    def cursor_hint(self, ev):
        left, top, right, bottom = self.mode_button_bounds()
        self.canvas.config(cursor='hand2' if left <= ev.x <= right and top <= ev.y <= bottom else 'fleur')

    def monitors(self):
        try:
            result = list(get_monitors())
            return result or []
        except Exception:
            logging.exception('Could not enumerate monitors')
            return []

    def position_window(self, force_anchor=False):
        width, height = self.size
        x, y = self.cfg.get('x'), self.cfg.get('y')
        monitors = self.monitors()
        idx = max(0, min(int(self.cfg.get('monitor_index', 0)), len(monitors)-1)) if monitors else 0
        if monitors and (force_anchor or x is None or y is None):
            m = monitors[idx]
            margin_x, margin_y = 24, 70  # bottom taskbar clearance
            if self.cfg.get('corner') == 'bottom-left':
                x = m.x + margin_x
            else:
                x = m.x + m.width - width - margin_x
            y = m.y + m.height - height - margin_y
        elif x is None or y is None:
            x, y = 40, 60
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def set_view_mode(self, mode: str):
        if mode not in ('large', 'small') or mode == self.cfg['view_mode']:
            return
        old_width, old_height = self.size
        old_x, old_y = self.root.winfo_x(), self.root.winfo_y()
        self.cfg['view_mode'] = mode
        new_width, new_height = self.size
        # Preserve the window's lower-right/lower-left edge when changing size.
        new_x = old_x if self.cfg.get('corner') == 'bottom-left' else old_x + old_width - new_width
        new_y = old_y + old_height - new_height
        # Prevent expanding a small HUD off the selected monitor.
        monitors = self.monitors()
        if monitors:
            idx = max(0, min(int(self.cfg.get('monitor_index', 0)), len(monitors) - 1))
            active = next((m for m in monitors if m.x <= old_x + old_width // 2 < m.x + m.width
                           and m.y <= old_y + old_height // 2 < m.y + m.height), monitors[idx])
            new_x = min(max(new_x, active.x + 5), active.x + max(5, active.width - new_width - 5))
            new_y = min(max(new_y, active.y + 5), active.y + max(5, active.height - new_height - 5))
        self.cfg['x'], self.cfg['y'] = new_x, new_y
        self.canvas.configure(width=new_width, height=new_height)
        self.root.geometry(f'{new_width}x{new_height}+{new_x}+{new_y}')
        save_config(self.cfg)
        self.draw()

    def toggle_view_mode(self):
        self.set_view_mode('large' if self.cfg['view_mode'] == 'small' else 'small')

    def drag_start(self, ev):
        left, top, right, bottom = self.mode_button_bounds()
        if left <= ev.x <= right and top <= ev.y <= bottom:
            self.drag = None
            self.toggle_view_mode()
            return 'break'
        self.drag = (ev.x_root, ev.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag_motion(self, ev):
        if self.drag:
            sx, sy, wx, wy = self.drag
            width, height = self.size
            self.root.geometry(f'{width}x{height}+{wx + ev.x_root-sx}+{wy + ev.y_root-sy}')

    def drag_end(self, _ev):
        if self.drag:
            self.cfg['x'], self.cfg['y'] = self.root.winfo_x(), self.root.winfo_y()
            save_config(self.cfg)
        self.drag = None

    def select_monitor(self, idx: int):
        self.cfg['monitor_index'] = idx
        self.cfg['x'] = self.cfg['y'] = None
        save_config(self.cfg)
        self.position_window(force_anchor=True)

    def select_corner(self, corner: str):
        self.cfg['corner'] = corner
        self.cfg['x'] = self.cfg['y'] = None
        save_config(self.cfg)
        self.position_window(force_anchor=True)

    def setup_filter_dialog(self):
        """Configure ELKA plugin IDs without hand-editing the JSON settings file."""
        popup = tk.Toplevel(self.root)
        popup.title('Connect Spotify filter')
        popup.configure(bg=BG)
        popup.resizable(False, False)
        popup.attributes('-topmost', True)
        popup.geometry(f'330x255+{max(0, self.root.winfo_x())}+{max(0, self.root.winfo_y()-260)}')

        tk.Label(popup, text='ELKA FX Host • VST low-pass filter', bg=BG, fg=FG,
                 font=('Segoe UI', 11, 'bold')).pack(pady=(11, 7))
        fields = tk.Frame(popup, bg=BG)
        fields.pack()

        def entry(label, value, row):
            tk.Label(fields, text=label, bg=BG, fg=MUTED,
                     font=('Segoe UI', 9)).grid(row=row, column=0, sticky='w', pady=3, padx=5)
            ent = tk.Entry(fields, width=12, bg=PANEL, fg=FG, insertbackground=FG,
                           highlightthickness=1, highlightbackground=BORDER)
            ent.insert(0, '' if value is None else str(value))
            ent.grid(row=row, column=1, pady=3, padx=5)
            return ent

        vst_id = entry('VST stable ID', self.cfg.get('fx_vst_id'), 0)
        param_id = entry('Cutoff parameter index', self.cfg.get('fx_cutoff_parameter'), 1)
        port_entry = entry('VBAN UDP port', self.cfg.get('fx_vban_port', 6981), 2)
        unit_var = tk.StringVar(value=self.cfg.get('fx_parameter_unit', 'Hz'))
        tk.Label(fields, text='Parameter unit', bg=BG, fg=MUTED).grid(row=3, column=0, sticky='w', padx=5)
        tk.OptionMenu(fields, unit_var, 'Hz', 'kHz').grid(row=3, column=1, sticky='w', padx=3)
        error = tk.Label(popup, text='', bg=BG, fg=RED, font=('Segoe UI', 8), wraplength=305)
        error.pack(pady=(6, 1))

        def apply_setup():
            try:
                vid, pid, port = int(vst_id.get()), int(param_id.get()), int(port_entry.get())
                if vid < 0 or pid < 0 or not 1 <= port <= 65535:
                    raise ValueError('Values must be valid nonnegative IDs and a UDP port.')
                self.cfg.update(fx_vst_id=vid, fx_cutoff_parameter=pid,
                                fx_vban_port=port, fx_parameter_unit=unit_var.get())
                self.cfg['fx_slots'] = [r for r in self.cfg['fx_slots'] if r['id'] != vid]
                if self.cfg.get('active_fx_id') == vid:
                    self.cfg['active_fx_id'] = None
                save_config(self.cfg)
                self.reapply_filter()
                self.draw()
                popup.destroy()
            except ValueError:
                error.configure(text='Enter nonnegative IDs and a port between 1 and 65535.')

        tk.Button(popup, text='Save and send current cutoff', command=apply_setup,
                  bg=GREEN, fg='#0B1920', relief='flat', padx=13, pady=6).pack(pady=4)
        tk.Label(popup, text='Find IDs in ELKA: right-click your VST → Info.',
                 bg=BG, fg=MUTED, font=('Segoe UI', 8)).pack()

    def configure_filter_types(self):
        """ELKA Info supplies the exact Type parameter and numbered mode choices."""
        popup = tk.Toplevel(self.root)
        popup.title('Kilohearts filter types')
        popup.configure(bg=BG)
        popup.attributes('-topmost', True)
        popup.geometry(f'390x425+{self.root.winfo_x()}+{max(0,self.root.winfo_y()-350)}')
        tk.Label(popup, text='Filter modes for the loaded kHs Filter', fg=FG, bg=BG,
                 font=('Segoe UI', 10, 'bold')).pack(pady=(12, 6))
        tk.Label(popup, text='In ELKA, right-click the filter → Info. Find the Type\n'
                 'parameter index and numbered choices. Do not guess.',
                 fg=MUTED, bg=BG, justify='left', font=('Segoe UI', 9)).pack(pady=(0, 10))
        f = tk.Frame(popup, bg=BG)
        f.pack()
        tk.Label(f, text='Type parameter index', fg=FG, bg=BG).grid(row=0,column=0,sticky='w',padx=5)
        idx = tk.Entry(f,width=7,bg=PANEL,fg=FG,insertbackground=FG)
        idx.insert(0, '' if self.cfg.get('fx_type_parameter') is None else str(self.cfg['fx_type_parameter']))
        idx.grid(row=0,column=1,padx=5,pady=5)
        tk.Label(popup,text='Mode=Value (one per line, match ELKA Info)',fg=MUTED,bg=BG).pack(pady=(8,3))
        names = tk.Text(popup,width=39,height=9,bg=PANEL,fg=FG,insertbackground=FG,
                        relief='flat',font=('Consolas',9))
        names.insert('1.0', '\n'.join(f'{name}={n}' for name,n in self.cfg['fx_type_values'].items())
                     if self.cfg['fx_type_values'] else
                     'Low Pass=\nBand Pass=\nHigh Pass=\nNotch=\nLow Shelf=\nPeak=\nHigh Shelf=')
        names.pack(padx=12,fill='x')
        error = tk.Label(popup,text='',fg=RED,bg=BG,wraplength=360)
        error.pack(pady=5)
        def save():
            try:
                type_index = int(idx.get())
                if type_index < 0:
                    raise ValueError('Index must not be negative')
                values = {}
                for line in names.get('1.0','end').splitlines():
                    if not line.strip(): continue
                    name, sep, num = line.partition('=')
                    if not sep or not name.strip() or not 0 <= int(num.strip()) <= 1024:
                        raise ValueError('Each mode needs Name=Number')
                    values[name.strip()[:38]] = int(num.strip())
                if not values:
                    raise ValueError('Enter at least one mode')
                self.cfg['fx_type_parameter'] = type_index
                self.cfg['fx_type_values'] = values
                self.cfg['fx_type_name'] = next(iter(values))
                save_config(self.cfg)
                popup.destroy()
                self.select_filter_mode(self.cfg['fx_type_name'])
            except ValueError as exc:
                error.config(text=f'Check settings: {exc}')
        tk.Button(popup,text='Save filter type settings',command=save,bg=GREEN,fg=BG,
                  relief='flat',padx=10,pady=5).pack(pady=6)

    def discover_installed(self):
        return discover_kilohearts_plugins(self.cfg.get('vst_scan_dirs', []))

    def auto_register_elka(self):
        """Discover saved plugin IDs from ELKA's own configuration directory."""
        from tkinter import messagebox
        from elka_import import auto_discover_elka_nodes
        matches=auto_discover_elka_nodes()
        if not matches:
            messagebox.showinfo('No saved VST IDs discovered',
                'No recognizable saved VST IDs in the ELKA configuration folder.\n'
                'Save your current layout in ELKA, then use Import IDs from saved ELKA layout.',
                parent=self.root)
            return
        items='\n'.join(f"{x['id']}: {x['name']}" for x in matches[:24])
        if messagebox.askyesno('Auto-register ELKA instances',
                'Found these saved VST IDs:\n'+items+'\n\nRegister them with the HUD? '
                'Register only effects routed to your music path.',parent=self.root):
            self.cfg['fx_slots']=clean_slots(self.cfg.get('fx_slots',[])+
                [x for x in matches if x['id'] != self.cfg.get('fx_vst_id')])
            save_config(self.cfg)

    def import_elka_layout(self):
        """Register already-loaded IDs; never modify ELKA graph files."""
        from tkinter import messagebox
        from elka_import import extract_layout_nodes
        path=filedialog.askopenfilename(parent=self.root,title='Choose saved ELKA FX layout',
            filetypes=[('ELKA JSON/XML saves','*.json *.xml *.elka'),('All files','*.*')])
        if not path:
            return
        try:
            matches=extract_layout_nodes(Path(path))
            if not matches:
                messagebox.showwarning('No plugin IDs found',
                    'This ELKA save format did not expose recognizable VST names and stable IDs. '
                    'Use Info in ELKA to read its stable ID.',
                    parent=self.root)
                return
            display='\n'.join(f"{x['id']} — {x['name']}" for x in matches[:24])
            if not messagebox.askyesno('Register saved ELKA effects',
                'Found these already-saved VST nodes:\n\n'+display+
                '\n\nRegister their IDs in the HUD? Only register effects on your music path.',
                parent=self.root):
                return
            primary=self.cfg.get('fx_vst_id')
            extra=[x for x in matches if x['id']!=primary]
            self.cfg['fx_slots']=clean_slots(self.cfg.get('fx_slots',[])+extra)
            save_config(self.cfg)
            messagebox.showinfo('Registered',
                f'Registered {len(extra)} candidates. Confirm they are wired in series in ELKA.',
                parent=self.root)
        except Exception as exc:
            messagebox.showerror('Could not read ELKA save',str(exc),parent=self.root)

    def add_vst_folder(self):
        folder = filedialog.askdirectory(title='Choose Kilohearts VST3 folder', parent=self.root)
        if not folder:
            return
        paths = self.cfg.get('vst_scan_dirs', [])
        if not isinstance(paths, list):
            paths = []
        if folder not in paths:
            paths.append(folder)
        self.cfg['vst_scan_dirs'] = paths[:12]
        save_config(self.cfg)


    def installed_match(self, plugin_name):
        wanted = normalize_fx_name(plugin_name)
        if wanted == normalize_fx_name('kHs Filter') and self.filter_sender.configured:
            return None, True
        for slot in self.cfg.get('fx_slots', []):
            if normalize_fx_name(slot['name']) == wanted:
                return slot['id'], True
        return None, False

    def choose_installed(self, name):
        effect_id, loaded = self.installed_match(name)
        if loaded:
            self.select_effect(effect_id)
        else:
            self.pair_installed(name)

    def pair_installed(self, name):
        popup = tk.Toplevel(self.root)
        popup.title('Connect ' + name)
        popup.configure(bg=BG)
        popup.attributes('-topmost', True)
        popup.geometry(f'420x245+{self.root.winfo_x()}+{max(0,self.root.winfo_y()-220)}')
        tk.Label(popup, text=name, bg=BG, fg=FG, font=('Segoe UI', 11, 'bold')).pack(pady=(10,5))
        tk.Label(popup, text='Installed, but not yet connected to this HUD.\n'
                 'In ELKA: add this VST to the Spotify AUX stereo chain,\n'
                 'wire both channels, then right-click the VST → Info.\n'
                 'Enter its stable VST ID once; after that this menu switches it.',
                 bg=BG, fg=MUTED, justify='left', font=('Segoe UI', 9)).pack(pady=5)
        area = tk.Frame(popup, bg=BG); area.pack(pady=8)
        tk.Label(area, text='Stable VST ID', bg=BG, fg=FG).pack(side='left', padx=5)
        field = tk.Entry(area, width=10, bg=PANEL, fg=FG, insertbackground=FG)
        field.pack(side='left')
        error = tk.Label(popup, text='', bg=BG, fg=RED)
        error.pack()
        def save():
            try:
                stable_id = int(field.get())
                if stable_id < 0 or stable_id == self.cfg.get('fx_vst_id'):
                    raise ValueError('Choose a different nonnegative VST ID')
                self.cfg['fx_slots'] = [s for s in self.cfg['fx_slots'] if s['id'] != stable_id
                                         and normalize_fx_name(s['name']) != normalize_fx_name(name)]
                self.cfg['fx_slots'].append({'name':name,'id':stable_id})
                self.cfg['fx_slots'] = clean_slots(self.cfg['fx_slots'])
                save_config(self.cfg)
                popup.destroy()
                self.select_effect(stable_id)
            except ValueError as exc:
                error.config(text=str(exc) or 'Enter a valid stable VST ID')
        tk.Button(popup, text='Connect and select', command=save, bg=GREEN,
                  fg=BG, relief='flat', padx=12, pady=5).pack(pady=4)

    def configure_exclusive_shortcuts(self):
        from tkinter import messagebox, ttk
        popup = tk.Toplevel(self.root)
        popup.title('Music shortcuts')
        popup.attributes('-topmost', True)
        body = ttk.Frame(popup, padding=12)
        body.pack(fill='both', expand=True)

        capture = ttk.LabelFrame(body, text='Keyboard capture in Music Mode', padding=8)
        capture.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        options = (
            ('exclusive_shortcuts', 'Enable Num Lock-off keypad macros and saved shortcuts'),
            ('capture_number_row', 'Also capture Ctrl + top-row numbers'),
        )
        flags = {}
        for setting, label in options:
            flags[setting] = tk.BooleanVar(popup, self.cfg[setting])
            ttk.Checkbutton(capture, text=label, variable=flags[setting]).pack(anchor='w')

        numpad = ttk.LabelFrame(body, text='Numpad (Num Lock off)', padding=8)
        numpad.grid(row=1, column=0, sticky='new', padx=(0, 8))
        other = ttk.LabelFrame(body, text='Saved shortcuts', padding=8)
        other.grid(row=1, column=1, sticky='new')
        bindings = (
            *((str(n), str(n)) for n in range(10)),
            ('.', 'Decimal'), ('/', 'Divide'), ('*', 'Multiply'), ('-', 'Minus'),
            ('+', 'Plus'), ('enter', 'Enter'),
        )
        keypad_values = self.shortcut_rows(
            numpad, ((key, label, self.cfg['key_actions'].get(key)) for key, label in bindings), ACTIONS)
        other_values = self.shortcut_rows(other, [
            ('home', 'Ctrl + Home', self.cfg['music_home_action']),
        ], ACTIONS)
        ttk.Label(other, text='Num Lock off\nF5  A1 speakers\nF6  A2 headphones', justify='left').grid(
            row=len(other_values), columnspan=2, sticky='w', pady=(8, 0))
        advanced = ttk.Frame(other)
        advanced.grid(row=len(other_values) + 1, columnspan=2, sticky='w', pady=(8, 0))
        g_values = self.optional_virtual_keys(advanced, self.cfg['music_g_actions'], ACTIONS)
        action_ids = {label: action for action, label in ACTIONS.items()}

        def save():
            music = {key: action_ids[var.get()] for key, var in keypad_values.items()}
            home = action_ids[other_values['home'].get()]
            g_actions = {key: action_ids[var.get()] for key, var in g_values.items()}
            updated = {
                **{key: var.get() for key, var in flags.items()},
                'key_actions': {key: action for key, action in music.items() if action != 'none'},
                'music_home_action': None if home == 'none' else home,
                'music_g_actions': {key: action for key, action in g_actions.items() if action != 'none'},
            }
            try:
                save_config({**self.cfg, **updated}, required=True)
            except OSError as exc:
                messagebox.showerror('Could not save Music shortcuts', str(exc), parent=popup)
                return
            self.cfg.update(updated)
            popup.destroy()

        footer = ttk.Frame(body)
        footer.grid(row=2, columnspan=2, sticky='e', pady=(10, 0))
        ttk.Button(footer, text='Cancel', command=popup.destroy).pack(side='left', padx=(0, 6))
        ttk.Button(footer, text='Save shortcuts', command=save).pack(side='left')

    @staticmethod
    def shortcut_rows(parent, bindings, actions):
        from tkinter import ttk
        variables = {}
        for row, (key, key_label, action) in enumerate(bindings):
            ttk.Label(parent, text=key_label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
            value = tk.StringVar(parent, actions.get(action, actions['none']))
            ttk.Combobox(parent, textvariable=value, values=list(actions.values()), state='readonly',
                         width=23).grid(row=row, column=1, sticky='ew', pady=2)
            variables[key] = value
        return variables

    def optional_virtual_keys(self, parent, current, actions):
        from tkinter import ttk

        content = ttk.Frame(parent)
        keys = [f'F{n}' for n in range(13, 19)]
        fields = self.shortcut_rows(content, ((key, key, current.get(key)) for key in keys), actions)
        ttk.Label(content, text='These apply only if your iCUE profile sends F13–F18.',
                  wraplength=210).grid(row=len(fields), columnspan=2, sticky='w', pady=(4, 0))
        visible = tk.BooleanVar(parent, False)

        def show_or_hide():
            if visible.get():
                content.pack(anchor='w', pady=(4, 0))
            else:
                content.pack_forget()

        ttk.Checkbutton(parent, text='Show optional virtual keys', variable=visible,
                        command=show_or_hide).pack(anchor='w')
        return fields

    def manage_effect_slots(self):
        """Configure already-loaded, wired ELKA VST IDs. Not a plugin loader."""
        popup=tk.Toplevel(self.root)
        popup.title('Loaded ELKA effects')
        popup.configure(bg=BG)
        popup.attributes('-topmost',True)
        popup.geometry(f'420x350+{self.root.winfo_x()}+{max(0,self.root.winfo_y()-310)}')
        tk.Label(popup,text='Optional additional Spotify effects',fg=FG,bg=BG,
                 font=('Segoe UI',10,'bold')).pack(pady=(11,5))
        tk.Label(popup,text='First load each plugin in ELKA and wire it in the AUX\n'
                 'chain in series. Keep each plugin On. Copy its stable ID\n'
                 'from the right-click node menu. This does NOT load VSTs.',
                 fg=MUTED,bg=BG,justify='left',font=('Segoe UI',9)).pack(pady=(0,6))
        field=tk.Text(popup,width=42,height=8,bg=PANEL,fg=FG,insertbackground=FG,
                      font=('Consolas',10),relief='flat')
        field.insert('1.0','\n'.join(f"{r['name']}={r['id']}" for r in self.cfg['fx_slots']))
        field.pack(padx=10,fill='x')
        tk.Label(popup,text='One per line: Reverb=4   /   Flanger=6',fg=MUTED,bg=BG).pack(pady=5)
        err=tk.Label(popup,text='',fg=RED,bg=BG,wraplength=380)
        err.pack()
        def save():
            try:
                items=[]
                for line in field.get('1.0','end').splitlines():
                    if not line.strip(): continue
                    name, sep, value=line.partition('=')
                    if not sep or not name.strip():
                        raise ValueError('Each line must be Name=ID')
                    vst_id=int(value.strip())
                    if vst_id < 0: raise ValueError('VST IDs must be nonnegative')
                    items.append({'name':name.strip(),'id':vst_id})
                if len(items)>48: raise ValueError('At most 48 effects')
                items=clean_slots(items)
                primary=self.cfg.get('fx_vst_id')
                if isinstance(primary,int) and primary in [i['id'] for i in items]:
                    raise ValueError('Do not add the main kHs filter ID twice')
                self.cfg['fx_slots']=items
                if self.cfg.get('active_fx_id') not in [i['id'] for i in items]:
                    self.cfg['active_fx_id']=None
                save_config(self.cfg)
                self.apply_effect_bypass()
                self.draw()
                popup.destroy()
            except ValueError as exc: err.config(text=str(exc))
        tk.Button(popup,text='Save loaded effects',command=save,bg=GREEN,fg=BG,
                  relief='flat',padx=12,pady=6).pack(pady=7)

    def step_filter(self, direction: int):
        old = int(self.cfg.get('bipolar_position',0))
        new = max(-12,min(12,old + direction))
        if new == old:
            return
        if new > 0 and not (isinstance(self.cfg.get('fx_type_parameter'),int)
                            and 'High Pass' in self.cfg.get('fx_type_values',{})):
            self.discover_filter_controls(step=direction)
            if not self.filter_probe_running:
                self.filter_warning = 'High Pass needs the kHs Filter Type parameter.'
                self.draw()
            return
        self.filter_warning = ''
        self.filter_pending_step = None
        self.cfg['bipolar_position'] = new
        mode, _frequency = dial_filter(new)
        if mode != 'Center':
            self.cfg['fx_type_name'] = mode
        save_config(self.cfg)
        self.reapply_filter()
        self.draw()

    def available_effect_ids(self):
        ids = []
        primary = self.cfg.get('fx_vst_id')
        if isinstance(primary, int) and primary >= 0:
            ids.append(primary)
        ids += [slot['id'] for slot in self.cfg['fx_slots']]
        return list(dict.fromkeys(ids))

    def selected_effect(self):
        candidate = self.cfg.get('active_fx_id')
        ids = self.available_effect_ids()
        return candidate if candidate in ids else self.cfg.get('fx_vst_id')

    def selected_effect_name(self):
        if self.cfg.get('active_fx_id') is None:
            return 'kHs Filter'
        for slot in self.cfg['fx_slots']:
            if slot['id'] == self.cfg['active_fx_id']:
                return slot['name']
        return 'kHs Filter'

    def apply_effect_bypass(self):
        ids = self.available_effect_ids()
        selected = self.selected_effect()
        if not ids:
            return
        # A DJ filter has a true dry center position, not a fake 20 kHz cut.
        neutral = selected == self.cfg.get('fx_vst_id') and self.cfg.get('bipolar_position',0) == 0
        commands = [self.filter_sender.bypass_command(vst_id,
                    not self.cfg['filter_enabled'] or neutral or vst_id != selected)
                    for vst_id in ids]
        try:
            # VBAN-TEXT packets must be bounded: multiple loaded effects can
            # otherwise exceed the 1436-byte payload ceiling.
            for start in range(0, len(commands), 12):
                self.filter_sender.send_text(' '.join(commands[start:start+12]))
        except Exception:
            logging.exception('Failed sending VST bypass controls')

    def set_filter_enabled(self, enabled: bool):
        self.cfg['filter_enabled'] = bool(enabled)
        save_config(self.cfg)
        # Avoid powering a VST off: Enable=0 can block the signal.
        self.apply_effect_bypass()
        self.draw()

    def toggle_filter_enabled(self):
        self.set_filter_enabled(not self.cfg['filter_enabled'])

    def select_effect(self, vst_id=None):
        self.cfg['active_fx_id'] = vst_id
        self.cfg['filter_enabled'] = True
        save_config(self.cfg)
        self.apply_effect_bypass()
        self.draw()

    def select_filter_mode(self, name: str):
        if not self.filter_sender.configured or not isinstance(self.cfg.get('fx_type_parameter'), int):
            return
        self.cfg['fx_type_name'] = name
        self.cfg['active_fx_id'] = None
        self.cfg['filter_enabled'] = True
        save_config(self.cfg)
        try:
            self.filter_sender.send_text(self.filter_sender.type_command(name))
        except Exception:
            logging.exception('Unable to send filter mode %s', name)
        self.apply_effect_bypass()
        self.draw()

    def reapply_filter(self):
        if not self.filter_sender.configured:
            return
        try:
            mode, freq = dial_filter(self.cfg.get('bipolar_position',0))
            if freq is not None:
                if isinstance(self.cfg.get('fx_type_parameter'),int) and mode in self.cfg.get('fx_type_values',{}):
                    self.filter_sender.send_text(self.filter_sender.type_command(mode))
                self.filter_sender.send(freq)
            self.apply_effect_bypass()
        except Exception:
            logging.exception('Could not resend DJ-style filter / bypass state')

    def keyboard_mode(self):
        if self.dj_waiting or self.cfg['dj_mode']:
            if foreground_is_virtualdj():
                return ('dj_waiting' if self.dj_waiting else 'dj'), self.dj_options
            return 'dj_background', self.dj_options
        if self.dj_mapper_selected and foreground_is_virtualdj():
            if self.cfg['dj_auto_focus'] and not self.dj_auto_blocked:
                return 'dj_waiting', self.dj_options
            return 'music_vdj', self.dj_options
        return 'music', self.dj_options

    def restore_mode(self, resume_dj):
        from dj_setup import virtualdj_running
        try:
            if resume_dj and virtualdj_running():
                self.set_dj_mode(True, manual=False)
            if not self.cfg['dj_mode']:
                self.restore_macrobuttons()
                save_config(self.cfg)
        except Exception:
            logging.exception('Could not restore Music or DJ mode at startup')

    def restore_macrobuttons(self):
        pending = self.cfg.get('macrobuttons_pending_pid')
        if pending and not macrobuttons.wait_for_exit(pending, timeout=0):
            return
        if self.cfg.get('macrobuttons_resume'):
            backup = MACROBUTTONS_BACKUP if self.cfg.get('macrobuttons_saved_map') else None
            macrobuttons.restore(self.cfg['macrobuttons_resume'], backup=backup)
            self.cfg['macrobuttons_resume'] = None
            self.cfg['macrobuttons_pending_pid'] = None
            self.cfg['macrobuttons_saved_map'] = False
            save_config(self.cfg)

    def set_dj_mode(self, enabled: bool, manual=True):
        from tkinter import messagebox
        from dj_setup import mapper_is_active, virtualdj_pid

        if enabled == self.cfg['dj_mode']:
            return
        try:
            if enabled:
                pid = virtualdj_pid()
                self.dj_auto_blocked_pid = pid
                if not mapper_is_active(self.dj_options):
                    if not manual or not self.configure_dj_mapping(self.dj_options, pid=pid):
                        return
                    pid = virtualdj_pid()
                if pid is None:
                    if manual:
                        messagebox.showinfo('VirtualDJ mapping ready',
                                            'Launch VirtualDJ, then select DJ Mode.',
                                            parent=self.root)
                    return
                self.dj_waiting = True
                self.filter_pending_step = None
                if self.cfg['dj_pause_macrobuttons']:
                    session = macrobuttons.find_session()
                    if session:
                        self.cfg['macrobuttons_saved_map'] = macrobuttons.backup_configuration(MACROBUTTONS_BACKUP)
                        self.cfg['macrobuttons_resume'] = session[1]
                        self.cfg['macrobuttons_pending_pid'] = session[0]
                        save_config(self.cfg, required=True)
                    macrobuttons.pause(session)
                    self.cfg['macrobuttons_pending_pid'] = None
                if self.dj_estimate_pid != pid:
                    self.dj_estimates = DeckEstimates()
                    self.dj_estimate_pid = pid
                self.dj_values = {}
                self.dj_connected = False
                self.dj_pid = pid
                self.dj_auto_blocked = False
            else:
                self.restore_macrobuttons()
                self.dj_auto_blocked = manual
                self.dj_auto_blocked_pid = self.dj_pid
                self.dj_pid = None
            self.cfg['dj_mode'] = enabled
            self.dj_keybinds_requested = enabled
            if not enabled:
                self.dj_keybind_guide.hide()
            save_config(self.cfg)
            w, h = self.size
            self.canvas.configure(width=w, height=h)
            self.position_window()
            self.draw()
        except Exception as exc:
            if (enabled and self.cfg.get('macrobuttons_resume')
                    and not isinstance(exc, macrobuttons.PausePending)):
                self.cfg['macrobuttons_pending_pid'] = None
                try:
                    self.restore_macrobuttons()
                except Exception:
                    logging.exception('Could not restore MacroButtons after mode transition failed')
            if manual:
                messagebox.showerror('DJ Mode', str(exc), parent=self.root)
            else:
                logging.warning('DJ mode transition failed: %s', exc)
            self.dj_auto_blocked = True
        finally:
            self.dj_waiting = False

    def configure_dj_mapping(self, options, pid=None, rollback=False):
        from tkinter import messagebox
        import subprocess
        from dj_setup import install_mapping, rollback_mapping, virtualdj_pid

        pid = virtualdj_pid() if pid is None else pid
        executable = process_executable(pid) if pid else None
        if pid and not messagebox.askyesno(
                'Restart VirtualDJ', 'Restart VirtualDJ to apply the keyboard mapping?',
                parent=self.root):
            return False
        if pid and not executable:
            raise RuntimeError('VirtualDJ is running but its executable could not be found.')
        if pid:
            if not macrobuttons.request_close(pid, lambda title: title.startswith('virtualdj')):
                raise RuntimeError('Could not close the VirtualDJ main window.')
            if not macrobuttons.wait_for_exit(pid, timeout=8):
                raise RuntimeError('VirtualDJ stayed open. Finish closing it and retry.')
        try:
            if rollback:
                rollback_mapping()
            else:
                install_mapping(options=options)
        finally:
            if executable is not None:
                launched = subprocess.Popen([str(executable)], cwd=executable.parent)
                if self.cfg['dj_mode']:
                    self.dj_pid = self.dj_estimate_pid = launched.pid
                    self.dj_estimates = DeckEstimates()
                    self.dj_connected = False
        self.dj_mapper_selected = not rollback
        return True

    def install_dj_mapping(self, rollback=False):
        from tkinter import messagebox
        import subprocess
        try:
            if rollback and self.cfg['dj_mode']:
                self.set_dj_mode(False)
                if self.cfg['dj_mode']:
                    return
            if self.configure_dj_mapping(self.dj_options, rollback=rollback):
                messagebox.showinfo(
                    'VirtualDJ keyboard mapping',
                    'Restored the previous mapping.' if rollback else 'Installed and selected Corner HUD DJ.',
                    parent=self.root)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror('VirtualDJ setup', str(exc), parent=self.root)

    def configure_dj_controls(self):
        from tkinter import messagebox, ttk
        import subprocess
        from dj_setup import DJ_ACTIONS, TRANSPORT_KEYS, mapper_is_active
        popup = tk.Toplevel(self.root)
        popup.title('VirtualDJ shortcuts')
        popup.attributes('-topmost', True)
        body = ttk.Frame(popup, padding=12)
        body.pack(fill='both', expand=True)

        increments = ttk.LabelFrame(body, text='Step size (% of knob or fader travel)', padding=8)
        increments.pack(fill='x', pady=(0, 8))
        step_values = {}
        for index, (label, key, current) in enumerate((
            ('EQ fine (Alt)', 'dj_eq_step', self.dj_options.eq_step),
            ('EQ fast (bare)', 'dj_eq_coarse_step', self.dj_options.eq_coarse_step),
            ('Filter', 'dj_filter_step', self.dj_options.filter_step),
            ('Volume', 'dj_volume_step', self.dj_options.volume_step),
        )):
            row, column = divmod(index, 2)
            ttk.Label(increments, text=label).grid(row=row, column=2 * column, sticky='w', padx=(0, 4), pady=2)
            step_values[key] = tk.StringVar(popup, str(current))
            ttk.Entry(increments, textvariable=step_values[key], width=5).grid(
                row=row, column=2 * column + 1, padx=(0, 9), pady=2)

        bindings = ttk.LabelFrame(body, text='Editable shortcuts', padding=8)
        bindings.pack(fill='x', pady=(0, 8))
        f_keys = [f'F{n}' for n in range(13, 19)]
        custom_values = self.shortcut_rows(
            bindings, [('dj_enter', 'Numpad Enter', self.dj_options.enter)], DJ_ACTIONS)
        advanced = ttk.Frame(body)
        advanced.pack(fill='x', pady=(0, 8))
        g_values = self.optional_virtual_keys(
            advanced, dict(zip(f_keys, self.dj_options.g_keys)), DJ_ACTIONS)
        action_ids = {label: action for action, label in DJ_ACTIONS.items()}
        ttk.Label(body, text=('Numpad Plus toggles deck volume zero / full.\n'
                             f'{EQ_MINIMUM_BINDING.title()} sets the selected deck EQ to minimum.\n'
                             f'{BASS_SWAP_BINDING.title()} swaps bass between decks.\n'
                             f'{RESET_BOTH_BINDING.title()} resets both decks.'),
                  justify='left').pack(anchor='w', pady=(0, 8))
        transport = ttk.LabelFrame(body, text='Selected-deck transport shortcuts', padding=8)
        transport.pack(fill='x')
        for row, (label, _action, key) in enumerate(TRANSPORT_KEYS):
            for col, value in enumerate((label, f'Alt + {key}')):
                ttk.Label(transport, text=value).grid(row=row, column=col, sticky='w', padx=(0, 12))

        def apply():
            try:
                updated = {key: int(var.get()) for key, var in step_values.items()}
                updated['dj_enter'] = action_ids[custom_values['dj_enter'].get()]
                updated['dj_g_keys'] = [action_ids[g_values[key].get()] for key in f_keys]
                options = DjOptions.from_config(updated)
                if options == self.dj_options and mapper_is_active(options):
                    popup.destroy()
                    return
                if self.configure_dj_mapping(options):
                    self.dj_options = options
                    self.cfg.update(updated)
                    save_config(self.cfg, required=True)
                    popup.destroy()
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                messagebox.showerror('VirtualDJ shortcuts', str(exc), parent=popup)

        footer = ttk.Frame(body)
        footer.pack(anchor='e', pady=(10, 0))
        ttk.Button(footer, text='Cancel', command=popup.destroy).pack(side='left', padx=(0, 6))
        ttk.Button(footer, text='Apply shortcuts', command=apply).pack(side='left')

    def update_dj_focus(self):
        now = time.monotonic()
        if now - self.dj_last_mode_check < 0.5:
            return
        self.dj_last_mode_check = now
        focus = foreground_is_virtualdj()
        self.dj_focused = focus
        if not self.cfg['dj_mode'] and self.cfg.get('macrobuttons_pending_pid'):
            self.restore_macrobuttons()
        if self.cfg['dj_mode'] and self.dj_pid and macrobuttons.wait_for_exit(self.dj_pid, timeout=0):
            self.set_dj_mode(False, manual=False)
        elif not self.cfg['dj_mode'] and self.cfg['dj_auto_focus'] and focus and not self.dj_auto_blocked:
            self.set_dj_mode(True, manual=False)
            self.dj_auto_blocked = not self.cfg['dj_mode']
        elif (self.dj_auto_blocked and self.dj_auto_blocked_pid
              and macrobuttons.wait_for_exit(self.dj_auto_blocked_pid, timeout=0)):
            self.dj_auto_blocked = False
            self.dj_auto_blocked_pid = None
        self.update_dj_keybinds()

    def update_dj_keybinds(self):
        if (self.dj_keybinds_requested and self.cfg['dj_mode'] and self.dj_focused
                and self.root.state() != 'withdrawn'):
            self.dj_keybind_guide.show(self.dj_options, self.monitors())
        else:
            self.dj_keybind_guide.hide()

    def show_dj_keybinds(self):
        self.dj_keybinds_requested = True

    def hide_dj_keybinds(self):
        self.dj_keybinds_requested = False
        self.dj_keybind_guide.hide()

    def discover_filter_controls(self, step=None):
        stable_id = self.cfg.get('fx_vst_id')
        if (os.name != 'nt' or not isinstance(stable_id, int)
                or (isinstance(self.cfg.get('fx_type_parameter'), int)
                    and 'High Pass' in self.cfg['fx_type_values']
                    and self.cfg.get('fx_adaptive_cutoff') ==
                    [stable_id, self.cfg.get('fx_cutoff_parameter')])):
            return
        if step is not None and not self.cfg['dj_mode']:
            marker = (stable_id, self.cfg['bipolar_position'])
            pending = self.filter_pending_step
            count = pending[2] if pending and pending[:2] == marker else 0
            self.filter_pending_step = (*marker, max(-12, min(12, count + step)))
        if self.filter_probe_running:
            return
        self.filter_probe_running = True

        def read():
            from kilohearts import discover_filter_controls
            try:
                values = discover_filter_controls(stable_id)
            except Exception:
                logging.exception('Could not discover the loaded kHs Filter parameters')
                values = None
            self.commands.put(('filter_controls', stable_id, values))

        threading.Thread(target=read, daemon=True, name='Kilohearts-metadata').start()

    def dj_poll(self):
        """Optional read-only VDJ Network Control plugin telemetry; never invent values."""
        if (not self.cfg['dj_mode'] or not self.cfg['dj_http_readback']
                or time.monotonic()-self.dj_last_read < 4.0):
            return
        self.dj_last_read=time.monotonic()
        base=str(self.cfg.get('dj_http_base','http://127.0.0.1:80')).rstrip('/')
        if not base.startswith('http://127.0.0.1:') and base != 'http://127.0.0.1':
            return
        def poll():
            out={}
            try:
                for d in (1,2):
                    for attr in ('eq_high','eq_mid','eq_low','filter','level'):
                        verb = attr + '_freq' if attr.startswith('eq_') else attr
                        script = f'deck {d} {verb}'
                        url=base+'/query?'+urllib.parse.urlencode({'script':script})
                        with urllib.request.urlopen(url,timeout=.25) as r:
                            raw=r.read(96).decode('utf-8','replace').strip()
                        if raw.startswith('error:') or not raw:
                            raise ValueError(raw)
                        out[(d,attr)]=raw
                self.commands.put(('dj_levels',out))
            except Exception:
                self.commands.put(('dj_levels',None))
        threading.Thread(target=poll,daemon=True,name='VirtualDJ-telemetry').start()

    def select_opacity(self, opacity: float):
        opacity = max(0.20, min(1.0, float(opacity)))
        self.cfg['opacity'] = opacity
        self.root.attributes('-alpha', opacity)
        save_config(self.cfg)

    def opacity_dialog(self):
        popup = tk.Toplevel(self.root)
        popup.title('HUD Opacity')
        popup.configure(bg=BG)
        popup.resizable(False, False)
        popup.attributes('-topmost', True)
        popup.geometry(f'265x125+{max(0, self.root.winfo_x())}+{max(0, self.root.winfo_y()-145)}')
        tk.Label(popup, text='Overlay opacity (20–100%)', fg=FG, bg=BG,
                 font=('Segoe UI', 10, 'bold')).pack(pady=(11, 0))
        level = tk.IntVar(value=round(float(self.cfg['opacity']) * 100))
        scale = tk.Scale(popup, from_=20, to=100, orient='horizontal', resolution=1,
                         variable=level, command=lambda value: self.select_opacity(float(value) / 100),
                         length=235, bg=BG, fg=FG, highlightthickness=0,
                         troughcolor=TRACK, activebackground=GREEN)
        scale.pack(pady=(0, 3))
        tk.Button(popup, text='Close', command=popup.destroy,
                  bg=PANEL, fg=FG, relief='flat', padx=15).pack()

    def open_context(self, ev):
        self.show_context_menu(ev.x_root, ev.y_root)

    def show_context_menu(self, x, y):
        if self.context_menu is not None:
            self.context_menu.destroy()
        self.context_menu = self.build_context_menu()
        try:
            self.context_menu.tk_popup(x, y)
        finally:
            self.context_menu.grab_release()

    def build_context_menu(self):
        menu = tk.Menu(self.root, tearoff=False)
        mode = tk.Menu(menu, tearoff=False)
        dj_active = self.cfg['dj_mode']
        for enabled, label in ((False, 'Music / Spotify'), (True, 'DJ / VirtualDJ (2 decks)')):
            mode.add_command(label=('✓ ' if dj_active == enabled else '   ') + label,
                             command=lambda value=enabled: self.set_dj_mode(value))
        menu.add_cascade(label='Mode · VirtualDJ' if dj_active else 'Mode · Music', menu=mode)

        music = tk.Menu(menu, tearoff=False)
        self.populate_music_menu(music)
        menu.add_cascade(label='Music', menu=music)

        dj = tk.Menu(menu, tearoff=False)
        dj.add_command(label='Show keybinds…', command=self.show_dj_keybinds,
                       state='normal' if dj_active else 'disabled')
        dj.add_command(label='Shortcuts and step sizes…', command=self.configure_dj_controls)
        dj.add_command(label='Install or update keyboard mapper…', command=self.install_dj_mapping)
        dj.add_command(label='Restore previous keyboard mapper…', command=lambda: self.install_dj_mapping(True))
        dj.add_separator()
        for label, key in (('Enter DJ Mode when VirtualDJ has focus', 'dj_auto_focus'),
                           ('Pause MacroButtons in DJ Mode', 'dj_pause_macrobuttons'),
                           ('Sync track analysis to Music History', 'vdj_library_sync')):
            var = tk.BooleanVar(self.root, self.cfg[key])

            def save_option(setting=key, value=var):
                self.cfg[setting] = value.get()
                self.dj_auto_blocked = False
                save_config(self.cfg)

            dj.add_checkbutton(label=label, variable=var, command=save_option)
        dj.add_command(label='Sync track analysis now', command=self.library_sync.request_now)
        dj.add_command(label='Library sync: ' + self.library_sync.status, state='disabled')
        menu.add_cascade(label='VirtualDJ', menu=dj)

        appearance = tk.Menu(menu, tearoff=False)
        for value, label in (('small', 'Compact view'), ('large', 'Large view')):
            appearance.add_command(label=('✓ ' if self.cfg['view_mode'] == value else '   ') + label,
                                   command=lambda view=value: self.set_view_mode(view))
        appearance.add_separator()
        screens = tk.Menu(appearance, tearoff=False)
        for i, monitor in enumerate(self.monitors()):
            suffix = ' (primary)' if getattr(monitor, 'is_primary', False) else ''
            mark = '✓ ' if self.cfg['monitor_index'] == i else '   '
            screens.add_command(label=f'{mark}Monitor {i+1} · {monitor.width}×{monitor.height}{suffix}',
                                command=lambda index=i: self.select_monitor(index))
        appearance.add_cascade(label='Monitor', menu=screens)
        corners = tk.Menu(appearance, tearoff=False)
        for value, label in (('bottom-left', 'Bottom left'), ('bottom-right', 'Bottom right')):
            corners.add_command(label=label, command=lambda corner=value: self.select_corner(corner))
        appearance.add_cascade(label='Anchor to corner', menu=corners)
        opacity = tk.Menu(appearance, tearoff=False)
        for pct in (100, 95, 90, 85, 80, 75, 70, 60, 50, 40, 30, 20):
            opacity.add_command(label=f'{pct}%', command=lambda percent=pct: self.select_opacity(percent / 100))
        opacity.add_separator()
        opacity.add_command(label='Custom slider…', command=self.opacity_dialog)
        appearance.add_cascade(label=f'Opacity · {round(float(self.cfg["opacity"]) * 100)}%', menu=opacity)
        menu.add_cascade(label='Appearance', menu=appearance)
        menu.add_separator()
        menu.add_command(label='Hide to tray', command=self.toggle_visibility)
        menu.add_command(label='Quit HUD', command=self.quit)
        return menu

    def populate_music_menu(self, menu):
        menu.add_command(label='Shortcuts…', command=self.configure_exclusive_shortcuts)
        menu.add_separator()
        state = 'On' if self.cfg['filter_enabled'] else 'Bypassed'
        if (state == 'On' and self.cfg['bipolar_position'] == 0
                and self.selected_effect() == self.cfg.get('fx_vst_id')):
            state = 'On at center'
        hint = ''
        if self.cfg['exclusive_shortcuts']:
            if self.cfg['key_actions'].get('enter') == 'filter_toggle':
                hint = '  (Numpad Enter, Num Lock off)'
        menu.add_command(label=f'Toggle filter · {state}{hint}', command=self.toggle_filter_enabled)
        menu.add_command(label='Filter darker', command=lambda: self.step_filter(-1))
        menu.add_command(label='Filter brighter', command=lambda: self.step_filter(+1))

        types = tk.Menu(menu, tearoff=False)
        can_modes = (self.filter_sender.configured
                     and isinstance(self.cfg.get('fx_type_parameter'), int)
                     and bool(self.cfg.get('fx_type_values')))
        if can_modes:
            for name in self.cfg['fx_type_values']:
                mark = '✓ ' if self.cfg['fx_type_name'] == name else '   '
                types.add_command(label=mark + name, command=lambda value=name: self.select_filter_mode(value))
            types.add_separator()
        types.add_command(label='Configure filter modes…', command=self.configure_filter_types)
        menu.add_cascade(label='Kilohearts filter type', menu=types)

        effects = tk.Menu(menu, tearoff=False)
        chosen = self.selected_effect()
        primary = self.cfg.get('fx_vst_id')
        effects.add_command(label=('✓ ' if chosen == primary else '   ') + 'kHs Filter',
                            command=lambda: self.select_effect(None),
                            state='normal' if self.filter_sender.configured else 'disabled')
        for slot in self.cfg['fx_slots']:
            effects.add_command(label=('✓ ' if chosen == slot['id'] else '   ') + slot['name'],
                                command=lambda identifier=slot['id']: self.select_effect(identifier))
        effects.add_separator()
        effects.add_command(label='Manage loaded effects…', command=self.manage_effect_slots)
        menu.add_cascade(label='Loaded effects', menu=effects)

        installed = tk.Menu(menu, tearoff=False)
        installed_items = self.discover_installed()
        selected_name = normalize_fx_name(self.selected_effect_name())
        for plugin in installed_items:
            name = plugin['name']
            _effect_id, ready = self.installed_match(name)
            mark = '✓ ' if ready and normalize_fx_name(name) == selected_name else '   '
            installed.add_command(label=mark + name + ('' if ready else '  (connect once)'),
                                  command=lambda value=name: self.choose_installed(value))
        if installed_items:
            installed.add_separator()
        else:
            installed.add_command(label='No Kilohearts VST3 bundles found', state='disabled')
        installed.add_command(label='Add VST3 scan folder…', command=self.add_vst_folder)
        menu.add_cascade(label=f'Installed Kilohearts ({len(installed_items)})', menu=installed)

        host = tk.Menu(menu, tearoff=False)
        host.add_command(label='Connect filter VST…', command=self.setup_filter_dialog)
        host.add_command(label='Auto-register from ELKA saves…', command=self.auto_register_elka)
        host.add_command(label='Import IDs from saved ELKA layout…', command=self.import_elka_layout)
        host.add_separator()
        host.add_command(label='Resend settings to FX Host', command=self.reapply_filter)
        menu.add_cascade(label='ELKA connection', menu=host)

    def make_tray(self):
        def dispatch(command):
            return lambda _icon, _item: self.commands.put(command)

        menu = pystray.Menu(
            pystray.MenuItem('Show / Hide HUD', dispatch('toggle'), default=True),
            pystray.MenuItem('Settings…', dispatch('settings')),
            pystray.MenuItem('Switch Music / DJ Mode', dispatch('audio_mode')),
            pystray.MenuItem('Sync VirtualDJ analysis to Music History', dispatch('vdj_library_sync')),
            pystray.MenuItem('Music filter', pystray.Menu(
                pystray.MenuItem('Toggle bypass', dispatch('filter_toggle')),
                pystray.MenuItem('Darker', dispatch('filter_down')),
                pystray.MenuItem('Brighter', dispatch('filter_up')),
                pystray.MenuItem('Resend settings to FX Host', dispatch('filter_resend')),
            )),
            pystray.MenuItem('Appearance', pystray.Menu(
                pystray.MenuItem('Switch compact / large view', dispatch('mode')),
                pystray.MenuItem('Move to next monitor', dispatch('next_monitor')),
                pystray.MenuItem('Anchor bottom-right', dispatch('anchor')),
            )),
            pystray.MenuItem('Exit HUD', dispatch('quit')),
        )
        return pystray.Icon(APP, tray_image(), 'Voicemeeter Corner HUD', menu)

    def run_tray(self):
        try:
            self.tray.run()
        except Exception:
            logging.exception('Tray icon failed')

    def toggle_visibility(self):
        if self.root.state() == 'withdrawn':
            self.root.deiconify()
            self.root.attributes('-topmost', True)
        else:
            self.dj_keybind_guide.hide()
            self.root.withdraw()

    def round_rect(self, x1, y1, x2, y2, r, **kw):
        points = [x1+r,y1, x2-r,y1, x2,y1,x2,y1+r,
                  x2,y2-r, x2,y2,x2-r,y2, x1+r,y2,
                  x1,y2,x1,y2-r, x1,y1+r,x1,y1]
        self.canvas.create_polygon(points, smooth=True, splinesteps=10, **kw)

    def text(self, x, y, s, size=10, color=FG, weight='normal', anchor='w'):
        self.canvas.create_text(x, y, text=s, fill=color, anchor=anchor,
                                font=('Segoe UI', size, weight))

    def divider(self, y):
        width, _ = self.size
        self.canvas.create_line(18, y, width-18, y, fill=BORDER)

    def vu(self, x1, y, x2, proportion, color):
        self.round_rect(x1, y, x2, y+9, 4, fill=TRACK, outline='')
        length = max(0, (x2-x1)*proportion)
        if length > 3:
            self.round_rect(x1, y, x1+length, y+9, 4, fill=color, outline='')

    def eq(self, y, label, value, color, compact=False):
        if compact:
            left, right, value_x, label_x, font_size = 61, 179, 228, 13, 9
        else:
            left, right, value_x, label_x, font_size = 114, 278, 345, 20, 10
        mid = (left + right) / 2
        self.text(label_x, y, label, size=font_size, color=MUTED, weight='bold')
        self.round_rect(left, y-4, right, y+4, 4, fill=TRACK, outline='')
        self.canvas.create_line(mid, y-7, mid, y+7, fill='#6B788C', width=1)
        if value is not None:
            v = max(-12, min(12, value))
            endpoint = mid + v / 12 * (right-left) / 2
            if abs(endpoint-mid) > 2:
                self.round_rect(min(mid, endpoint), y-4, max(mid, endpoint), y+4,
                                3, fill=color, outline='')
        label_value = f'{value:+.1f}' if value is not None else '—'
        self.text(value_x, y, label_value, size=font_size, color=FG if value is not None else MUTED,
                  anchor='e')

    def draw_mode_button(self):
        x1, y1, x2, y2 = self.mode_button_bounds()
        self.round_rect(x1, y1, x2, y2, 5, fill=PANEL, outline=BORDER, width=1)
        # Small, centered arrow: ↗ expands; ↙ shrinks toward bottom-left.
        arrow = '↗' if self.cfg['view_mode'] == 'small' else '↙'
        self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                text=arrow, fill=FG, anchor='center',
                                font=('Segoe UI Symbol', 9, 'normal'))

    def filter_row(self, y: int, compact: bool):
        pos=int(self.cfg.get('bipolar_position',0))
        mode,freq = dial_filter(pos)
        left, right = (61,179) if compact else (114,278)
        label_x,value_x=(13,228) if compact else (20,345)
        is_primary=self.selected_effect()==self.cfg.get('fx_vst_id')
        effective=(self.filter_sender.configured and self.cfg['filter_enabled']
                   and pos!=0 and is_primary)
        color=GREEN if effective else '#536171'
        self.text(label_x,y,'FILT',size=9 if compact else 10,color=MUTED,weight='bold')
        self.round_rect(left,y-4,right,y+4,4,fill=TRACK,outline='')
        middle=(left+right)/2
        self.canvas.create_line(middle,y-7,middle,y+7,fill=FG if pos==0 else '#788494')
        extent=middle + (right-left)/2*(pos/12)
        if pos:
            self.round_rect(min(middle,extent),y-4,max(middle,extent),y+4,3,fill=color,outline='')
        if not self.filter_sender.configured: value='SETUP'
        elif not is_primary: value=self.selected_effect_name()[:8]
        elif self.filter_warning: value='TYPE SETUP'
        elif pos==0: value='CENTER'
        else: value=('LP ' if pos<0 else 'HP ')+filter_text(freq)
        self.text(value_x,y,value,size=8 if compact else 10,
                  color=FG if effective else MUTED,anchor='e')

    def draw_dj(self):
        width, height = self.size
        small = self.cfg['view_mode'] == 'small'
        self.round_rect(0, 0, width-1, height-1, 12, fill=BG, outline=BORDER, width=1)
        self.text(13, 17, 'VIRTUALDJ', size=10, weight='bold')
        state = 'LIVE VALUES' if self.dj_connected else 'KEYBOARD ESTIMATES'
        if self.cfg['dj_http_readback'] and not self.dj_connected:
            state = 'ESTIMATED · LIVE UNAVAILABLE'
        if not self.dj_focused:
            state = 'LIVE · UNFOCUSED' if self.dj_connected else 'ESTIMATES · UNFOCUSED'
        self.text(width-35, 17, state, size=8, color=GREEN if self.dj_connected else MUTED, anchor='e')
        self.draw_mode_button()
        self.canvas.create_line(12, 34, width-12, 34, fill=BORDER)
        selected = self.dj_estimates.deck
        for deck in (1, 2):
            start = 10 if deck == 1 else width//2 + 4
            end = width//2 - 4 if deck == 1 else width-10
            color = BLUE if deck == 1 else ORANGE
            if selected == deck:
                self.round_rect(start, 42, end, 66, 5, fill=PANEL, outline=color)
            self.text(start+7, 54, f'DECK {deck}', size=10, color=color, weight='bold')
            if self.dj_estimates.sync_deck == deck:
                self.text(end-7, 54, 'SYNC SENT', size=8, color=MUTED, anchor='e')
            controls = (('HIGH', 'eq_high'), ('MID', 'eq_mid'), ('LOW', 'eq_low'),
                        ('FILT', 'filter'), ('VOL', 'level'))
            for row, (label, attribute) in enumerate(controls):
                y = 80 + row * (21 if small else 29)
                key = deck, attribute
                bypassed = attribute == 'filter' and self.dj_estimates.filter_enabled.get(deck) is False
                raw = (self.dj_estimates.values.get(key) if bypassed or not self.dj_connected
                       else self.dj_values.get(key))
                value = raw
                if isinstance(raw, str):
                    try:
                        value = float(raw.strip().rstrip('%'))
                        if '%' not in raw and 0 <= value <= 1:
                            value *= 100
                    except ValueError:
                        value = None
                if value is not None and not 0 <= value <= 100:
                    value = None
                self.text(start+6, y, label, size=9, color=MUTED)
                left, right = start+44, end-48
                self.canvas.create_line(left, y, right, y, fill=TRACK, width=4)
                if attribute != 'level':
                    center = (left+right)/2
                    self.canvas.create_line(center, y-5, center, y+5, fill=MUTED)
                else:
                    center = left
                if value is None:
                    display = '—'
                else:
                    end_pos = left + (right-left) * value / 100
                    self.canvas.create_line(center, y, end_pos, y, fill=MUTED if bypassed else color, width=4)
                    display = f'{value:g}%'
                    if attribute == 'filter':
                        display = 'CENTER' if value == 50 else f'{"LP" if value < 50 else "HP"} {value:g}%'
                    elif attribute.startswith('eq_'):
                        display = f'{2 * value:g}%'
                if bypassed and value is None:
                    display = 'BYPASS'
                self.text(end-5, y, display, size=8, color=MUTED if bypassed else FG, anchor='e')
        enter_hint = '   ·   NUM ENTER  FILTER' if self.dj_options.enter == 'filter_toggle' else ''
        footer = ('NUM LOCK OFF FOR KEYPAD' if self.hotkeys.presses.num_lock
                  else 'NUM / OR *  SELECT DECK' + enter_hint)
        self.text(13, 190 if small else 229, footer, size=8, color=MUTED)
        if not small:
            self.text(13, 251, 'EQ  8/9 HIGH   5/6 MID   2/3 LOW   ·   ALT FOR FINE', size=9, color=MUTED)
            self.text(13, 271, '0/. FILTER   - CENTER EQ/FILT   + VOL 0/100   1/4 VOL   7 EQ RESET',
                      size=9, color=MUTED)

    def draw(self):
        if self.cfg.get('dj_mode'):
            self.canvas.delete('all')
            self.draw_dj()
            return
        c = self.canvas
        c.delete('all')
        width, height = self.size
        self.round_rect(0, 0, width-1, height-1, 12 if self.cfg['view_mode'] == 'small' else 15,
                        fill=BG, outline=BORDER, width=1)
        status = self.current.get('online', False)
        keypad_active = self.cfg['exclusive_shortcuts'] and not self.hotkeys.presses.num_lock
        if self.cfg['view_mode'] == 'small':
            # Minimal EQ + filter view: no volume meters, outputs, or footer labels.
            self.round_rect(12, 11, 18, 17, 3, fill=GREEN if status else RED, outline='')
            self.text(26, 14, 'EQ  /  FILTER', size=9, weight='bold')
            if keypad_active:
                self.text(width - 32, 14, 'NUMPAD MACROS', size=8, color=GREEN, anchor='e')
            self.draw_mode_button()
            self.canvas.create_line(11, 29, width-11, 29, fill=BORDER)
            eq = self.current if status else {}
            self.eq(44, 'HIGH', eq.get('high'), BLUE, compact=True)
            self.eq(65, 'MID', eq.get('mid'), GREEN, compact=True)
            self.eq(86, 'LOW', eq.get('low'), ORANGE, compact=True)
            self.canvas.create_line(11, 100, width-11, 100, fill=BORDER)
            self.filter_row(116, compact=True)
            return

        self.round_rect(17, 15, 23, 21, 3, fill=GREEN if status else RED, outline='')
        self.text(32, 18, 'VOICEMEETER  /  CORNER HUD', size=10, weight='bold')
        if keypad_active:
            self.text(width - 35, 18, 'NUMPAD MACROS', size=8, color=GREEN, anchor='e')
        self.draw_mode_button()
        self.divider(36)
        if not status:
            self.text(22, 71, self.current.get('reason', 'Waiting for Voicemeeter...'), color=ORANGE, size=11)
            self.text(22, 93, 'Start Voicemeeter Banana, then this reconnects.', color=MUTED, size=9)
            self.text(22, 130, 'Settings: right-click this HUD. Drag to move.', color=MUTED, size=9)
            return

        s = self.current
        music_state = ('MUTED' if s['music_mute'] else 'CLUTCH' if s['music_gain'] <= -18 else 'PLAYING')
        self.text(20, 55, f'MUSIC   /   STRIP {self.cfg["music_strip"]}', size=10, weight='bold')
        self.text(345, 55, f'{s["music_gain"]:+.1f} dB', size=11, color=ORANGE, anchor='e', weight='bold')
        self.text(20, 79, music_state, color=RED if s['music_mute'] else GREEN, size=9)
        self.vu(116, 75, 344, self.peak_display[0], ORANGE)

        self.text(20, 110, f'GAME    /   STRIP {self.cfg["game_strip"]}', size=10, weight='bold')
        self.text(345, 110, f'{s["game_gain"]:+.1f} dB', size=11, color=BLUE, anchor='e', weight='bold')
        self.text(20, 134, 'MUTED' if s['game_mute'] else 'ACTIVE', color=RED if s['game_mute'] else GREEN, size=9)
        self.vu(116, 130, 344, self.peak_display[1], BLUE)
        self.divider(152)
        self.text(20, 170, 'MUSIC  /  3-BAND EQ', size=9, color=MUTED, weight='bold')
        self.eq(195, 'HIGH', s['high'], BLUE)
        self.eq(218, 'MID', s['mid'], GREEN)
        self.eq(241, 'LOW', s['low'], ORANGE)
        self.divider(255)
        self.filter_row(272, compact=False)
        self.divider(285)
        outputs = ('A1 ON' if s['a1'] else 'A1 OFF') + '    •    ' + ('A2 ON' if s['a2'] else 'A2 OFF')
        self.text(19, 298, outputs, size=8, color=MUTED)
        self.text(345, 298, 'DRAG  •  RIGHT-CLICK SETTINGS', size=8, color=MUTED, anchor='e')

    def tick(self):
        try:
            while True:
                command = self.commands.get_nowait()
                if isinstance(command, tuple):
                    if command[0] == 'dj_levels' and self.cfg['dj_mode']:
                        self.dj_connected = command[1] is not None
                        self.dj_values = command[1] or {}
                    elif command[0] == 'dj_key' and self.cfg['dj_mode']:
                        self.dj_estimates.apply(command[1], self.dj_options, coarse=command[2])
                    elif command[0] == 'filter_controls':
                        self.filter_probe_running = False
                        _, stable_id, values = command
                        pending = self.filter_pending_step
                        self.filter_pending_step = None
                        marker = (stable_id, self.cfg['bipolar_position'])
                        step = pending[2] if pending and pending[:2] == marker and not self.cfg['dj_mode'] else 0
                        if values and stable_id == self.cfg.get('fx_vst_id'):
                            if not isinstance(self.cfg.get('fx_type_parameter'), int):
                                self.cfg['fx_type_parameter'] = values['fx_type_parameter']
                                self.cfg['fx_type_values'] = values['fx_type_values']
                            elif self.cfg['fx_type_parameter'] == values['fx_type_parameter']:
                                self.cfg['fx_type_values'] = {
                                    **values['fx_type_values'], **self.cfg['fx_type_values']}
                            if self.cfg.get('fx_cutoff_parameter') is None:
                                self.cfg['fx_cutoff_parameter'] = values['fx_cutoff_parameter']
                                self.cfg['fx_parameter_unit'] = values['fx_parameter_unit']
                            if self.cfg['fx_cutoff_parameter'] == values['fx_cutoff_parameter']:
                                self.cfg['fx_adaptive_cutoff'] = [stable_id, values['fx_cutoff_parameter']]
                            self.filter_warning = ''
                            save_config(self.cfg)
                            if not step and not self.cfg['dj_mode']:
                                self.reapply_filter()
                            if step and 'High Pass' in self.cfg['fx_type_values']:
                                self.step_filter(step)
                        elif step:
                            self.filter_warning = 'High Pass needs the kHs Filter Type parameter.'
                    continue
                if command.startswith('key_action:'):
                    act = command.split(':',1)[1]
                    shortcut_log.debug('Music shortcut: %s', act)
                    if act in ('filter_down', 'filter_up'):
                        self.step_filter(-1 if act == 'filter_down' else 1)
                    elif act == 'filter_toggle':
                        self.toggle_filter_enabled()
                    elif act in ACTIONS and act != 'none':
                        self.vm_actions.put(act)
                elif command == 'toggle':
                    self.toggle_visibility()
                elif command == 'settings':
                    self.show_context_menu(*self.root.winfo_pointerxy())
                elif command == 'mode':
                    self.toggle_view_mode()
                elif command == 'audio_mode':
                    self.set_dj_mode(not self.cfg['dj_mode'])
                elif command == 'filter_down':
                    self.step_filter(-1)
                elif command == 'filter_up':
                    self.step_filter(1)
                elif command == 'filter_toggle':
                    self.toggle_filter_enabled()
                elif command == 'filter_resend':
                    self.reapply_filter()
                elif command == 'hotkey_failed':
                    self.hotkey_error = True
                elif command == 'next_monitor':
                    count = len(self.monitors())
                    if count > 0:
                        self.select_monitor((int(self.cfg['monitor_index']) + 1) % count)
                elif command == 'vdj_library_sync':
                    self.library_sync.request_now()
                elif command == 'anchor':
                    self.select_corner('bottom-right')
                elif command == 'quit':
                    self.quit()
                    return
        except queue.Empty:
            pass
        try:
            while True:
                self.current = self.messages.get_nowait()
        except queue.Empty:
            pass
        if self.current.get('online', False):
            for i, key in enumerate(('music_peak', 'game_peak')):
                desired = meter_fraction(self.current[key])
                self.peak_display[i] = max(desired, self.peak_display[i] * 0.81)
        else:
            self.peak_display = [0, 0]
        try:
            self.update_dj_focus()
        except Exception:
            logging.exception('Could not check VirtualDJ focus')
        if self.cfg.get('dj_mode'):
            self.dj_poll()
        if self.root.state() != 'withdrawn':
            self.draw()
        self.root.after(100, self.tick)

    def quit(self):
        try:
            self.restore_macrobuttons()
        except Exception:
            logging.exception('Could not restore MacroButtons at HUD shutdown')
        self.stop_event.set()
        self.library_sync.wake.set()
        self.hotkey_stop_event.set()
        self.hotkeys.wake_and_exit()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.quit()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    _mutex = hold_single_instance()
    try:
        HUD().run()
    except Exception:
        logging.exception('Uncaught HUD error')
        raise
