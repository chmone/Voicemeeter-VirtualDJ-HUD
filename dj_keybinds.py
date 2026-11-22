"""A focus-preserving shortcut reference companion for the DJ HUD."""
import ctypes
from ctypes import wintypes
import sys
import tkinter as tk

from dj_setup import BASS_SWAP_BINDING, DJ_ACTIONS, EQ_MINIMUM_BINDING, RESET_BOTH_BINDING, TRANSPORT_KEYS


def keypad_rows(options):
    rows = [
        ('/   *', 'Select Deck 1 / 2'),
        ('8   9', 'High EQ down / up'),
        ('5   6', 'Mid EQ down / up'),
        ('2   3', 'Low EQ down / up'),
        ('0   .', 'Filter left / right'),
        ('1   4', f'Volume −/+ {options.volume_step}%'),
        ('7', 'Reset deck EQ'),
        ('−', 'Center EQ and filter'),
        ('+', 'Volume zero / full'),
    ]
    if options.enter != 'none':
        rows.append(('Enter', DJ_ACTIONS[options.enter]))
    return rows


def guide_position(hud, guide, monitors):
    x, y, hud_width, hud_height = hud
    width, height = guide
    gap = 8
    top = y - height - gap
    bottom = y + hud_height + gap
    for monitor in monitors:
        right = monitor.x + monitor.width
        edge = monitor.y + monitor.height
        if (monitor.x <= x + hud_width / 2 < right
                and monitor.y <= y + hud_height / 2 < edge):
            x = max(monitor.x + gap, min(x, right - width - gap))
            y = top if top >= monitor.y + gap else bottom
            return x, max(monitor.y + gap, min(y, edge - height - gap))
    return x, top if top >= 0 else bottom


class DjKeybindGuide:
    def __init__(self, root, dismiss, *, bg, panel, fg, muted, accent, border):
        self.root = root
        self.dismiss = dismiss
        self.bg, self.panel, self.fg = bg, panel, fg
        self.muted, self.accent, self.border = muted, accent, border
        self.window = None
        self.options = None
        self.geometry = None

    def build(self, options):
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.title('VirtualDJ keybinds')
        window.overrideredirect(True)
        window.configure(bg=self.bg, highlightthickness=1, highlightbackground=self.border)

        def label(parent, text, *, bg=self.bg, fg=self.fg, size=9, bold=False):
            return tk.Label(parent, text=text, bg=bg, fg=fg, anchor='w',
                            font=('Segoe UI', size, 'bold' if bold else 'normal'))

        header = tk.Frame(window, bg=self.bg)
        header.pack(fill='x', padx=12, pady=(7, 0))
        label(header, 'VirtualDJ keybinds', size=11, bold=True).pack(side='left')
        close = label(header, '×', size=15, fg=self.muted)
        close.configure(cursor='hand2', padx=5)
        close.pack(side='right')
        close.bind('<ButtonRelease-1>', lambda _event: self.dismiss())
        label(window, f'EQ knob travel  ·  Bare {options.eq_coarse_step}%   /   Alt {options.eq_step}%',
              fg=self.accent).pack(anchor='w', padx=12, pady=(0, 7))

        tables = tk.Frame(window, bg=self.bg)
        tables.pack(fill='both', padx=12, pady=(0, 10))
        keypad = tk.Frame(tables, bg=self.panel, padx=7, pady=5)
        keypad.pack(side='left', fill='both', padx=(0, 6))
        regular = tk.Frame(tables, bg=self.panel, padx=7, pady=5)
        regular.pack(side='left', fill='both', expand=True)
        label(keypad, 'Numpad · Num Lock off', bg=self.panel, fg=self.accent, bold=True).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))
        for row, (key, action) in enumerate(keypad_rows(options), 1):
            label(keypad, key, bg=self.panel, bold=True).grid(row=row, column=0, sticky='w', padx=(0, 7))
            label(keypad, action, bg=self.panel).grid(row=row, column=1, sticky='w')

        label(regular, 'DJ shortcuts', bg=self.panel, fg=self.accent, bold=True).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))
        for row, (action, _verb, key) in enumerate(TRANSPORT_KEYS, 1):
            for col, text in enumerate((action, f'Alt+{key}')):
                label(regular, text, bg=self.panel, bold=bool(col)).grid(
                    row=row, column=col, sticky='w', padx=(0, 4))
        for row, (key, action) in enumerate(((EQ_MINIMUM_BINDING, 'Minimize selected deck EQ'),
                                              (BASS_SWAP_BINDING, 'Swap bass between decks'),
                                              (RESET_BOTH_BINDING, 'Reset both decks')),
                                             len(TRANSPORT_KEYS) + 1):
            label(regular, f'{key.title()}  {action}', bg=self.panel).grid(
                row=row, column=0, columnspan=2, sticky='w', pady=(3, 0))
        window.update_idletasks()
        return window

    def show(self, options, monitors):
        if options != self.options:
            if self.window is not None:
                self.window.destroy()
            self.window = self.build(options)
            self.options = options
            self.geometry = None
        window = self.window
        hud = (self.root.winfo_rootx(), self.root.winfo_rooty(),
               self.root.winfo_width(), self.root.winfo_height())
        size = window.winfo_reqwidth(), window.winfo_reqheight()
        position = guide_position(hud, size, monitors)
        if position != self.geometry:
            window.geometry(f'{size[0]}x{size[1]}+{position[0]}+{position[1]}')
            self.geometry = position
        if window.state() == 'withdrawn':
            if sys.platform == 'win32':
                show_without_activation(window)
            else:
                window.attributes('-topmost', True)
                window.deiconify()

    def hide(self):
        if self.window is not None and self.window.state() != 'withdrawn':
            self.window.withdraw()


def show_without_activation(window):
    # Tk's Win32 wrapper owns the extended style. Disable it while Tk maps it,
    # then keep clicks and topmost placement from activating the wrapper.
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
    style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
    ctypes.set_last_error(0)
    if not user32.SetWindowLongW(hwnd, -20, style | 0x08000000) and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())

    window.attributes('-disabled', True)
    try:
        window.deiconify()
        window.update_idletasks()
    finally:
        window.attributes('-disabled', False)
    # HWND_TOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
    if not user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013):
        raise ctypes.WinError(ctypes.get_last_error())
