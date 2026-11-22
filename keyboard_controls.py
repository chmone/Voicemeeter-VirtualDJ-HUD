"""Pair Win32 key transitions before deciding which application owns a press."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path

from dj_setup import DJ_ALT_DECK_KEYS, NUMPAD_ACTIONS, keypad_alias_sequence

VK_KEYS = {0x60 + n: str(n) for n in range(10)}
VK_KEYS.update({0x6E: '.', 0x6A: '*', 0x6B: '+', 0x6D: '-', 0x6F: '/'})
KEYPAD_SCANS = dict(zip((0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49),
                        '0123456789'))
KEYPAD_SCANS.update({0x53: '.', 0x37: '*', 0x4E: '+', 0x4A: '-'})
NAVIGATION = {0x0C, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
VK_NUMLOCK = 0x90
MUSIC_OUTPUT_KEYS = {'F5': 'speakers', 'F6': 'headphones'}


def identify_key(vk, scan, flags):
    extended = bool(flags & 1)
    if vk == 0x0D:
        return ('keypad', 'enter') if extended else ('main', 'enter')
    if vk == 0x24 and extended:
        return 'main', 'home'
    if vk in VK_KEYS:
        return 'keypad', VK_KEYS[vk]
    if not extended and vk in NAVIGATION and scan in KEYPAD_SCANS:
        return 'keypad', KEYPAD_SCANS[scan]
    if 0x30 <= vk <= 0x39:
        return 'row', chr(vk)
    if 0x41 <= vk <= 0x5A and f'ALT+{chr(vk)}' in DJ_ALT_DECK_KEYS:
        return 'letter', chr(vk)
    if vk in (0x74, 0x75) or 0x7A <= vk <= 0x82:
        return 'function', f'F{vk - 0x6F}'
    return None


def native_dj_action(key, alt, options):
    kind, name = key
    if kind == 'keypad':
        if name == 'enter':
            return options.enter if not alt else None
        return NUMPAD_ACTIONS.get(name)
    if alt and kind in ('row', 'letter'):
        return DJ_ALT_DECK_KEYS.get(f'ALT+{name}')
    if not alt and kind == 'function':
        if name in ('F13', 'F14', 'F15', 'F16', 'F17', 'F18', 'F19'):
            return 'reset_all' if name == 'F19' else options.g_keys[int(name[1:]) - 13]
    return None


class ShortcutPresses:
    def __init__(self, num_lock=True, lock_held=False):
        self.held = {}
        self.num_lock = num_lock
        self.lock_held = lock_held

    def num_lock_event(self, down):
        if down and not self.lock_held:
            self.num_lock = not self.num_lock
        self.lock_held = down

    def event(self, key, *, down, ctrl, alt, other_modifier, mode, exclusive,
              number_row, music_actions, music_g_keys, dj_options,
              home_action=None, music_ctrl_actions=None):
        if key is None:
            return False, None
        if not down:
            return self.held.pop(key, False), None
        kind, name = key
        keypad = kind == 'keypad'
        if key in self.held:
            # Keep a held key from repeating into the app that just gained focus.
            background_repeat = keypad and not self.num_lock and mode in ('dj_background', 'dj_waiting')
            return self.held[key] or background_repeat, None

        capture = keypad and not self.num_lock and mode in ('dj', 'dj_waiting', 'dj_background')
        command = None
        if not other_modifier:
            dj_action = native_dj_action(key, alt, dj_options) if not ctrl else None
            dj_binding = bool(dj_action and dj_action != 'none')
            if mode == 'dj':
                if dj_binding:
                    if keypad:
                        if capture:
                            command = ('dj_keypad', name, dj_action, not alt)
                    else:
                        command = ('dj_key', dj_action, False)
            elif mode == 'dj_waiting':
                capture = capture or (not keypad and dj_binding)
            elif mode in ('music', 'music_vdj'):
                capture = mode == 'music_vdj' and not keypad and alt and dj_binding
                if exclusive and not alt:
                    if ctrl and (kind == 'keypad' or (kind == 'row' and number_row)):
                        capture = True
                        aliases = music_actions if music_ctrl_actions is None else music_ctrl_actions
                        if name in aliases:
                            command = 'key_action:' + aliases[name]
                    elif ctrl and key == ('main', 'home') and home_action:
                        capture, command = True, 'key_action:' + home_action
                    elif not ctrl and kind == 'function' and name in music_g_keys:
                        capture, command = True, 'key_action:' + music_g_keys[name]
                    elif not ctrl and not self.num_lock and kind == 'function' and name in MUSIC_OUTPUT_KEYS:
                        capture, command = True, 'key_action:' + MUSIC_OUTPUT_KEYS[name]
                    elif not ctrl and keypad and not self.num_lock:
                        action = music_actions.get(name)
                        if action and action != 'none':
                            capture, command = True, 'key_action:' + action
        self.held[key] = capture
        return capture, command


@lru_cache(maxsize=1)
def _keyboard_input_api():
    class KeyboardInput(ctypes.Structure):
        _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]

    class MouseInput(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                    ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]

    class InputData(ctypes.Union):
        _fields_ = [('keyboard', KeyboardInput), ('mouse', MouseInput)]

    class Input(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('data', InputData)]

    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    send_input.restype = wintypes.UINT
    return send_input, Input, InputData, KeyboardInput


@lru_cache(maxsize=16)
def _dj_keypad_inputs(key):
    send_input, Input, InputData, KeyboardInput = _keyboard_input_api()
    sequence = keypad_alias_sequence(key)
    events = (Input * (2 * len(sequence)))(
        *(Input(1, InputData(keyboard=KeyboardInput(0x6F + int(alias[1:]), 0, flags, 0, 0)))
          for alias in sequence for flags in (0, 0x0002)))  # INPUT_KEYBOARD, KEYEVENTF_KEYUP
    return send_input, events, ctypes.sizeof(Input)


def send_dj_keypad(key):
    if not foreground_is_virtualdj():
        return False
    send_input, events, size = _dj_keypad_inputs(key)
    count = send_input(len(events), events, size)
    if count % 2:
        send_input(1, ctypes.pointer(events[count]), size)
    return count == len(events)


@lru_cache(maxsize=32)
def process_executable(pid):
    if os.name != 'nt' or not pid:
        return None
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        value = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(value))
        if kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(length)):
            return Path(value.value)
    finally:
        kernel.CloseHandle(handle)
    return None


def foreground_is_virtualdj():
    if os.name != 'nt':
        return False
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
    path = process_executable(pid.value)
    return path is not None and path.name.casefold() == 'virtualdj.exe'
