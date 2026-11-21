"""Read Kilohearts automation metadata in an isolated ELKA worker process."""
from __future__ import annotations

import ctypes
import json
import os
import re
from pathlib import Path
import subprocess
import sys


def elka_home():
    return Path(os.environ.get('APPDATA', Path.home())) / 'ElkaSoft' / 'VoiceMeeterFxHost'


def active_filter(stable_id, layout=None):
    path = layout or elka_home() / 'wpf-main-window.json'
    graph = json.loads(path.read_text(encoding='utf-8-sig'))
    for node in graph.get('PluginNodes', []):
        plugin = Path(node.get('PluginIdentifier', ''))
        if (node.get('InstanceId') == stable_id and node.get('PluginFormat') == 'VST3'
                and not node.get('MissingPlugin') and plugin.name.casefold() == 'khs filter.vst3'
                and plugin.exists()):
            return plugin
    raise ValueError(f'ELKA has no installed kHs Filter at stable ID {stable_id}.')


def filter_controls(catalog):
    rows = [line.split('\t') for line in catalog.splitlines()]
    controls = {row[2].casefold(): row for row in rows if len(row) == 7 and row[0] == 'Parameter'}
    mode = controls['type']
    cutoff = controls['cutoff']
    choices = {}
    for choice in mode[6].split(' | '):
        ordinal, separator, label = choice.partition('=')
        if separator:
            choices[label.title()] = int(ordinal)
    displayed = re.search(r'\s(k?Hz)$', cutoff[3])
    unit = cutoff[4] or (displayed.group(1) if displayed else '')
    if not {'Low Pass', 'High Pass'} <= choices.keys() or unit not in ('Hz', 'kHz'):
        raise ValueError('kHs Filter did not report bipolar modes and a frequency unit.')
    return {'fx_type_parameter': int(mode[1]), 'fx_type_values': choices,
            'fx_cutoff_parameter': int(cutoff[1]), 'fx_parameter_unit': unit}


def cutoff_value(frequency, configured_unit, *, adaptive=False):
    if adaptive:
        configured_unit = 'kHz' if frequency > 1000 else 'Hz'
    if configured_unit == 'Hz':
        return f'{frequency} Hz'
    if configured_unit == 'kHz':
        return f'{frequency / 1000:g} kHz'
    raise ValueError('fx_parameter_unit must be Hz or kHz')


def discover_filter_controls(stable_id):
    plugin = active_filter(stable_id)
    native = (Path(os.environ['ProgramFiles']) / 'ElkaSoft' / 'VoiceMeeter FX Host'
              / 'ElkaVoiceMeeterFxHost.Native.dll')
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(native), str(plugin)],
        capture_output=True, check=True, timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
    # Vendor DLLs may write diagnostics to stdout before the metadata.
    marker = b'CORNERHUD_CONTROLS='
    line = next(line for line in result.stdout.splitlines() if line.startswith(marker))
    return json.loads(line[len(marker):])


def read_plugin_catalog(native, plugin):
    library = ctypes.CDLL(str(native))
    create = library.ElkaFx_WorkerCreatePlugin
    create.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, *([ctypes.c_int] * 6),
                       ctypes.c_wchar_p, ctypes.c_int)
    status = ctypes.create_unicode_buffer(8192)
    handle = create('VST3', str(plugin), 48000, 512, 2, 2, 1, 1, status, len(status))
    if handle <= 0:
        raise RuntimeError(status.value)
    try:
        output = ctypes.create_string_buffer(262144)
        read = library.ElkaFx_WorkerGetPluginParameterInfo
        read.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_int)
        size = read(handle, output, len(output), status, len(status))
        if size <= 0 or size >= len(output):
            raise RuntimeError(status.value)
        return output.value.decode('utf-8')
    finally:
        library.ElkaFx_WorkerDestroyPlugin(handle)


if __name__ == '__main__':
    print('CORNERHUD_CONTROLS=' + json.dumps(filter_controls(read_plugin_catalog(*sys.argv[1:]))))
