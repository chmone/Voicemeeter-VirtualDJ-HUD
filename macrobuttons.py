"""Pause the Voicemeeter companion without stopping the mixer."""
from __future__ import annotations

import csv
import ctypes
from ctypes import wintypes
import io
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from dj_setup import atomic_write
from keyboard_controls import process_executable

IMAGE = 'VoicemeeterMacroButtons.exe'
MAP_FILENAME = 'MacroButtonConfig.xml'


class PausePending(RuntimeError):
    pass


def macrobuttons_pids():
    if os.name != 'nt':
        return []
    result = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {IMAGE}', '/FO', 'CSV', '/NH'],
                            capture_output=True, check=True, text=True, timeout=3,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return [int(row[1]) for row in csv.reader(io.StringIO(result.stdout))
            if len(row) > 1 and row[0].casefold() == IMAGE.casefold()]


def request_close(pid, is_main_window=None):
    user = ctypes.windll.user32
    if is_main_window is None:
        is_main_window = lambda title: 'macro.buttons' in title or 'macrobuttons' in title
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    sent = False

    @callback
    def visit(hwnd, _):
        nonlocal sent
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, len(title))
            if is_main_window(title.value.casefold()):
                sent = bool(user.PostMessageW(hwnd, 0x0010, 0, 0))
                return not sent
        return True

    user.EnumWindows.argtypes = (callback, wintypes.LPARAM)
    user.EnumWindows(visit, 0)
    return sent


def wait_for_exit(pid, timeout=4):
    """Wait on the exact PID, including a window that merely hid to the tray."""
    if os.name != 'nt':
        return True
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: PID has exited
    try:
        return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel.CloseHandle(handle)


def find_session():
    pids = macrobuttons_pids()
    if not pids:
        return None
    if len(pids) != 1:
        raise RuntimeError('Multiple MacroButtons instances are running.')
    path = process_executable(pids[0])
    if path is None or path.name.casefold() != IMAGE.casefold():
        raise RuntimeError('The running MacroButtons executable could not be identified.')
    return pids[0], str(path)


def pause(session):
    if session is None:
        return
    pid, _path = session
    if not wait_for_exit(pid, timeout=0):
        if not request_close(pid) and not wait_for_exit(pid, timeout=0):
            raise RuntimeError('Could not close MacroButtons. Use its tray menu, then retry DJ Mode.')
        if not wait_for_exit(pid):
            raise PausePending('MacroButtons stayed open. Finish shutting it down and retry DJ Mode.')
    if macrobuttons_pids():
        raise RuntimeError('MacroButtons restarted while DJ Mode was starting.')


def configuration_path():
    documents = Path.home() / 'Documents'
    if os.name == 'nt':
        buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:  # CSIDL_PERSONAL
            documents = Path(buffer.value)
    return documents / 'Voicemeeter' / MAP_FILENAME


def map_has_buttons(content):
    """Return None for an unfamiliar map and False for a known blank map."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    if root.tag != 'VBAudioVoicemeeterMacroButtonMap':
        return None
    buttons = root.findall('./MacroButtonConfiguration/MacroButton')
    if not buttons:
        return None
    return any(button.get('key') not in (None, '', '0')
               or any((child.text or '').strip() for child in button)
               for button in buttons)


def backup_configuration(backup):
    try:
        content = configuration_path().read_bytes()
    except FileNotFoundError:
        return False
    if map_has_buttons(content) is not True:
        return False
    atomic_write(backup, content)
    return True


def restore(path, *, backup=None):
    if not path or macrobuttons_pids():
        return
    executable = Path(path)
    if executable.name.casefold() != IMAGE.casefold() or not executable.is_file():
        raise RuntimeError('The previous MacroButtons executable could not be restored.')
    if backup and backup.is_file():
        saved = backup.read_bytes()
        config = configuration_path()
        try:
            current = config.read_bytes()
        except FileNotFoundError:
            current = None
        if (map_has_buttons(saved) is True
                and (current is None or map_has_buttons(current) is False)):
            if macrobuttons_pids():
                return
            atomic_write(config, saved)
    subprocess.Popen([str(executable)], cwd=executable.parent)
