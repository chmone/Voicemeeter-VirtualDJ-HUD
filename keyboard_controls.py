"""Pair Win32 key transitions before deciding which application owns a press."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path

from dj_setup import NUMPAD_ACTIONS

VK_KEYS = {0x60 + n: str(n) for n in range(10)}
VK_KEYS.update({0x6E: '.', 0x6A: '*', 0x6B: '+', 0x6D: '-', 0x6F: '/'})
KEYPAD_SCANS = dict(zip((0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49),
                        '0123456789'))
KEYPAD_SCANS.update({0x53: '.', 0x37: '*', 0x4E: '+', 0x4A: '-'})
NAVIGATION = {0x0C, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


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
    if 0x7B <= vk <= 0x82:
        return 'function', f'F{vk - 0x6F}'
    return None


def native_dj_action(key, ctrl, options):
    kind, name = key
    if kind == 'keypad':
        if name == 'enter':
            return options.enter if ctrl else None
        return NUMPAD_ACTIONS.get(name)
    if kind == 'function':
        if name == 'F12':
            return 'reset_all' if ctrl else None
        if not ctrl:
            return 'reset_all' if name == 'F19' else options.g_keys[int(name[1:]) - 13]
    return None


class ShortcutPresses:
    def __init__(self):
        self.held = {}

    def event(self, key, *, down, ctrl, other_modifier, mode, exclusive,
              number_row, music_actions, music_g_keys, dj_options,
              home_action=None, dj_keypad_available=True):
        if key is None:
            return False, None
        if not down:
            return self.held.pop(key, False), None
        kind, name = key
        keypad = kind == 'keypad'
        if key in self.held:
            # Keep a held key from repeating into the app that just gained focus.
            background_repeat = keypad and mode in ('dj_background', 'dj_waiting')
            return self.held[key] or background_repeat, None

        capture = keypad and mode in ('dj', 'dj_waiting', 'dj_background')
        command = None
        if not other_modifier:
            dj_action = native_dj_action(key, ctrl, dj_options)
            dj_binding = bool(dj_action and dj_action != 'none')
            # VirtualDJ calls both Enter keys CTRL+RETURN. Filter the main key
            # before its native mapper can treat it like keypad Enter.
            main_enter_alias = ctrl and key == ('main', 'enter')
            if mode == 'dj':
                if dj_binding and (not keypad or dj_keypad_available):
                    capture = False
                    command = ('dj_key', dj_action, keypad and not ctrl)
                else:
                    capture = capture or main_enter_alias
            elif mode == 'dj_waiting':
                capture = capture or dj_binding or main_enter_alias
            elif mode in ('music', 'music_vdj'):
                capture = mode == 'music_vdj' and ctrl and (dj_binding or main_enter_alias)
                if exclusive:
                    if ctrl and (kind == 'keypad' or (kind == 'row' and number_row)):
                        capture = True
                        if name in music_actions:
                            command = 'key_action:' + music_actions[name]
                    elif ctrl and key == ('main', 'home') and home_action:
                        capture, command = True, 'key_action:' + home_action
                    elif not ctrl and kind == 'function' and name in music_g_keys:
                        capture, command = True, 'key_action:' + music_g_keys[name]
        self.held[key] = capture
        return capture, command


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
