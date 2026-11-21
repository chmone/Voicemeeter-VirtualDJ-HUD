"""Add/remove the HUD as an opt-in per-user Windows startup item."""
import os
import sys
from pathlib import Path

if os.name != 'nt':
    raise SystemExit('Windows only')

import winreg

NAME = 'VoicemeeterCornerHUD'
REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
script_path = Path(__file__).resolve().parent / 'overlay.py'
pythonw = Path(sys.executable).with_name('pythonw.exe')
cmd = f'"{pythonw}" "{script_path}"'

with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as reg:
    if '--remove' in sys.argv:
        try:
            winreg.DeleteValue(reg, NAME)
        except FileNotFoundError:
            pass
        print('Startup entry removed.')
    else:
        if not pythonw.exists():
            raise SystemExit('Could not find pythonw.exe alongside current Python executable.')
        winreg.SetValueEx(reg, NAME, 0, winreg.REG_SZ, cmd)
        print('Enabled Windows login startup:', cmd)
