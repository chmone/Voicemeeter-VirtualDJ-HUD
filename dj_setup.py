"""Build, install and roll back the foreground VirtualDJ keyboard mapper."""
from __future__ import annotations

import ctypes
import csv
import io
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET


MAPPER_NAME = 'KEYBOARD - Corner HUD DJ'
MAPPER_REVISION = 13
DEFAULT_EQ_STEP = 8
DEFAULT_EQ_COARSE_STEP = 15
DEFAULT_VOLUME_STEP = 15
BASS_SWAP_BOOST = 60
DJ_ACTIONS = {
    'none': 'No action',
    'deck1': 'Select Deck 1', 'deck2': 'Select Deck 2',
    'high_down': 'High EQ down', 'high_up': 'High EQ up',
    'mid_down': 'Mid EQ down', 'mid_up': 'Mid EQ up',
    'low_down': 'Low EQ down', 'low_up': 'Low EQ up',
    'filter_down': 'Filter left', 'filter_up': 'Filter right',
    'filter_center': 'Center filter', 'filter_toggle': 'Toggle deck filter',
    'deck_center': 'Center deck EQ and filter',
    'volume_down': 'Volume down', 'volume_up': 'Volume up',
    'volume_toggle': 'Toggle deck volume zero / full',
    'eq_minimum': 'Set deck EQ to minimum', 'eq_reset': 'Reset deck EQ',
    'sync': 'Sync deck', 'reset_all': 'Reset both decks',
    'bass_swap': 'Swap bass between decks',
}
DEFAULT_ENTER_ACTION = 'filter_toggle'
DJ_KEYPAD_START = 'F20'
DJ_KEYPAD_DIGITS = ('F21', 'F22', 'F23', 'F24')
DJ_KEYPAD_PREFIX = "'$cornerhud_keypad_prefix'"
BASS_SWAP_BINDING = 'ALT+7'
RESET_BOTH_BINDING = 'ALT+8'
EQ_MINIMUM_BINDING = 'ALT+W'
DJ_ALT_DECK_KEYS = {
    BASS_SWAP_BINDING: 'bass_swap',
    RESET_BOTH_BINDING: 'reset_all',
    EQ_MINIMUM_BINDING: 'eq_minimum',
}
NUMPAD_ACTIONS = {
    '/': 'deck1', '*': 'deck2', '8': 'high_down', '9': 'high_up',
    '5': 'mid_down', '6': 'mid_up', '2': 'low_down', '3': 'low_up',
    '0': 'filter_down', '.': 'filter_up', '1': 'volume_down', '4': 'volume_up',
    '7': 'eq_reset', '-': 'deck_center', '+': 'volume_toggle',
}
DJ_KEYPAD_KEYS = (*NUMPAD_ACTIONS, 'enter')
TRANSPORT_KEYS = (
    ('Play / pause', 'play_button', '1'),
    ('Cue', 'cue_button', '2'),
    ('Stop', 'stop_button', '3'),
    ('Toggle loop', 'loop', '4'),
    ('Halve loop', 'loop_half', '5'),
    ('Double loop', 'loop_double', '6'),
    ('Sync', 'sync', 'Q'),
)
DEFAULT_G_KEYS = ('deck1', 'deck2', 'sync', 'eq_reset', 'filter_center', 'none')
VARIABLE = "'$cornerhud_deck'"
ONCE = 'up ? nothing : isrepeat ? nothing : '
STATE_DIR = 'Corner HUD Mapper Backups'
STARTER_LAYOUT = "load_skin 'Default:Starter'"


@dataclass(frozen=True)
class DjOptions:
    eq_step: int = DEFAULT_EQ_STEP
    filter_step: int = 5
    volume_step: int = DEFAULT_VOLUME_STEP
    enter: str = DEFAULT_ENTER_ACTION
    g_keys: tuple[str, ...] = DEFAULT_G_KEYS
    eq_coarse_step: int = DEFAULT_EQ_COARSE_STEP

    def __post_init__(self):
        if any(type(step) is not int or not 1 <= step <= 25
               for step in (self.eq_step, self.eq_coarse_step, self.filter_step, self.volume_step)):
            raise ValueError('DJ increments must be whole percentages from 1 to 25.')
        if len(self.g_keys) != 6 or any(action not in DJ_ACTIONS for action in (*self.g_keys, self.enter)):
            raise ValueError('Choose a supported action for each DJ key.')

    @classmethod
    def from_config(cls, cfg):
        return cls(
            eq_step=cfg.get('dj_eq_step', DEFAULT_EQ_STEP),
            eq_coarse_step=cfg.get('dj_eq_coarse_step', DEFAULT_EQ_COARSE_STEP),
            filter_step=cfg.get('dj_filter_step', 5),
            volume_step=cfg.get('dj_volume_step', DEFAULT_VOLUME_STEP),
            enter=cfg.get('dj_enter', DEFAULT_ENTER_ACTION),
            g_keys=tuple(cfg.get('dj_g_keys', DEFAULT_G_KEYS)),
        )

    def bindings(self):
        keys = {f'F{number}': action for number, action in enumerate(self.g_keys, 13)
                if action != 'none'}
        keys.update(DJ_ALT_DECK_KEYS)
        keys['F19'] = 'reset_all'
        return keys


def keypad_alias_sequence(key):
    first, second = divmod(DJ_KEYPAD_KEYS.index(key), len(DJ_KEYPAD_DIGITS))
    return DJ_KEYPAD_START, DJ_KEYPAD_DIGITS[first], DJ_KEYPAD_DIGITS[second]


def filter_position(current: float, delta: int) -> float:
    target = min(100, max(0, current + delta))
    if current < 50 <= target or target <= 50 < current:
        return 50
    return target


def bipolar_step(control: str, step: int, *, variable=False) -> str:
    toward = 'smaller' if step > 0 else 'greater'
    crossing = 'greater' if step > 0 else 'smaller'
    threshold = 50 - step - (0.0001 if step > 0 else -0.0001)
    limit = ''
    if variable:
        query = f'`get_var {control}`'
        adjust = f'set {control} {step / 100:+g}'
        center = f'set {control} 0.5'
        end = 1 if step > 0 else 0
        limit = f'var_{crossing} {control} {end - step / 100:g} ? set {control} {end} : '
    else:
        query = f'`{control}`'
        adjust = f'{control} {step:+d}%'
        center = f'{control} 50%'
    return (f'{limit}param_{toward} {query} 50% ? '
            f'param_{crossing} {query} {threshold:g}% ? {center} : {adjust} : {adjust}')


def filter_script(deck: int, action: str, *, step=0) -> str:
    mixer = f'deck {deck} filter'
    # Starter's visible FX dial is a separate Color FX slot from the mixer filter.
    starter = f"deck {deck} effect_colorfx 1 'Filter'"
    bypassed = f"'$cornerhud_filter_bypass_{deck}'"
    saved = f"'$cornerhud_filter_position_{deck}'"
    select = f"deck {deck} filter_selectcolorfx 'filter'"
    active = f'deck {deck} filter_activate'
    if action == 'reset':
        return (f'set {bypassed} 1 & set {saved} 0.5 & {STARTER_LAYOUT} ? '
                f'{starter} 50% : {mixer} 50% & {active} off')
    if action == 'center':
        return (f'set {saved} 0.5 & var {bypassed} ? nothing : '
                f'{STARTER_LAYOUT} ? {starter} 50% : {select} & {mixer} 50%')
    if action == 'toggle':
        restore = f"get_var {saved} & param_cast 'percentage'"
        enable = (f'{STARTER_LAYOUT} ? {restore} & {starter} : '
                  f'{select} & ({restore} & {mixer}) & {active} on')
        disable = (f'{STARTER_LAYOUT} ? set {saved} `{starter}` & {starter} 50% : '
                   f'set {saved} `{mixer}` & {active} off')
        return (f'var {bypassed} ? set {bypassed} 0 & ({enable}) : '
                f'set {bypassed} 1 & ({disable})')
    mixer_step = f'{active} on & {bipolar_step(mixer, step)}'
    return (f'var {bypassed} ? {bipolar_step(saved, step, variable=True)} : '
            f'{STARTER_LAYOUT} ? {bipolar_step(starter, step)} : '
            f'{select} ? {mixer_step} : {select} & {mixer_step}')


def deck_script(deck: int, action: str, options: DjOptions, *, coarse=False) -> str:
    eq_step = options.eq_coarse_step if coarse else options.eq_step
    steps = {'high': ('eq_high_freq', eq_step),
             'mid': ('eq_mid_freq', eq_step),
             'low': ('eq_low_freq', eq_step),
             'volume': ('level', options.volume_step)}
    if action == 'eq_reset':
        return f'deck {deck} eq_reset'
    if action == 'deck_center':
        return f'deck {deck} eq_reset & ({filter_script(deck, "center")})'
    if action == 'eq_minimum':
        return ' & '.join(f'deck {deck} eq_{band}_freq 0%' for band in ('high', 'mid', 'low'))
    if action == 'volume_toggle':
        return f'param_equal `deck {deck} level` 0% ? deck {deck} level 100% : deck {deck} level 0%'
    if action == 'filter_center':
        return filter_script(deck, 'center')
    if action == 'filter_toggle':
        return filter_script(deck, 'toggle')
    if action in ('sync', 'play_button', 'cue_button', 'stop_button', 'loop', 'loop_half', 'loop_double'):
        return f'deck {deck} {action}'
    control, direction = action.split('_')
    sign = -1 if direction == 'down' else 1
    if control == 'filter':
        return filter_script(deck, 'step', step=sign * options.filter_step)
    verb, step = steps[control]
    return f'deck {deck} {verb} {sign * step:+d}%'


def action_script(action: str, options: DjOptions, *, coarse=False) -> str:
    if action in ('deck1', 'deck2'):
        return f'{ONCE}set {VARIABLE} {action[-1]}'
    if action == 'none':
        return 'nothing'
    if action == 'bass_swap':
        low_1, low_2 = 'deck 1 eq_low_freq', 'deck 2 eq_low_freq'
        boost_1 = f'{low_2} 0% & {low_1} {BASS_SWAP_BOOST}%'
        boost_2 = f'{low_1} 0% & {low_2} {BASS_SWAP_BOOST}%'
        return (f'{ONCE}param_greater `{low_1}` `{low_2}` ? {boost_2} : '
                f'param_greater `{low_2}` `{low_1}` ? {boost_1} : '
                f'var_equal {VARIABLE} 2 ? {boost_2} : {boost_1}')
    if action == 'reset_all':
        reset = ' & '.join(f'deck {deck} eq_reset & ({filter_script(deck, "reset")})'
                           f' & deck {deck} level 100%' for deck in (1, 2))
        return f'{ONCE}set {VARIABLE} 1 & {reset}'
    right = deck_script(2, action, options, coarse=coarse)
    left = deck_script(1, action, options, coarse=coarse)
    prefix = '' if action in ('play_button', 'cue_button', 'stop_button') else ONCE
    return f'{prefix}var_equal {VARIABLE} 2 ? {right} : {left}'


def keypad_alias_scripts(options):
    # A start key resets the decoder. The next two function keys identify one
    # keypad key without assigning any physical digit or navigation key in VirtualDJ.
    clear = f'set {DJ_KEYPAD_PREFIX} 0'
    count = len(DJ_KEYPAD_DIGITS)
    for modifier in ('', 'ALT+'):
        yield modifier + DJ_KEYPAD_START, ONCE + clear
        for second, alias in enumerate(DJ_KEYPAD_DIGITS):
            script = f'set {DJ_KEYPAD_PREFIX} {second + 1}'
            for first in reversed(range(count)):
                key = DJ_KEYPAD_KEYS[first * count + second]
                action = NUMPAD_ACTIONS.get(key, options.enter if not modifier else 'none')
                command = action_script(action, options, coarse=not modifier).removeprefix(ONCE)
                script = f'var_equal {DJ_KEYPAD_PREFIX} {first + 1} ? {clear} & ({command}) : {script}'
            yield modifier + alias, ONCE + script


def build_mapper(options=None, base=None) -> bytes:
    options = options or DjOptions()
    root = ET.Element('mapper', {'device': 'KEYBOARD', 'author': 'Corner HUD', 'version': '850'})
    if base is not None:
        root = ET.fromstring(base, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
        if root.tag != 'mapper' or root.get('device', '').upper() != 'KEYBOARD':
            raise ValueError('The selected base mapping is not a VirtualDJ keyboard mapper.')
    bindings = options.bindings()
    aliases = dict(keypad_alias_scripts(options))
    reserved = bindings.keys() | aliases.keys() | {
        f'{modifier}NUM{key}' for modifier in ('', 'ALT+', 'CTRL+') for key in NUMPAD_ACTIONS
    }
    for node in root.findall('map'):
        if node.get('value', '').upper() in reserved:
            root.remove(node)
    startup = next((node for node in root.findall('map') if node.get('value') == 'ONINIT'), None)
    if startup is None:
        startup = ET.SubElement(root, 'map', {'value': 'ONINIT'})
    filters = ' & '.join(f'({filter_script(deck, "reset")})' for deck in (1, 2))
    initial = f'set {VARIABLE} 1 & {filters}'
    existing = startup.get('action', '')
    startup.set('action', f'({existing}) & {initial}' if existing else initial)
    for key, action in bindings.items():
        ET.SubElement(root, 'map', {'value': key, 'action': action_script(action, options)})
    for key, script in aliases.items():
        ET.SubElement(root, 'map', {'value': key, 'action': script})
    assigned = {node.get('value', '').upper() for node in root.findall('map')}
    for _label, action, key in TRANSPORT_KEYS:
        chord = f'ALT+{key}'
        if chord not in assigned:
            ET.SubElement(root, 'map', {'value': chord, 'action': action_script(action, options)})
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='UTF-8', xml_declaration=True) + b'\n'


def registry_value(key, name):
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    return winreg.QueryValueEx(handle, name)[0]
            except FileNotFoundError:
                continue
    return None


def vdj_home() -> Path:
    configured = registry_value(r'Software\VirtualDJ', 'HomeFolder')
    if configured:
        return Path(os.path.expandvars(configured))
    documents = registry_value(
        r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders', 'Personal')
    candidates = [Path(os.environ[name]) / 'VirtualDJ' for name in ('LOCALAPPDATA', 'APPDATA')
                  if os.environ.get(name)]
    candidates.append((Path(os.path.expandvars(documents)) if documents
                       else Path.home() / 'Documents') / 'VirtualDJ')
    return next((path for path in candidates if (path / 'settings.xml').is_file()), candidates[0])


def virtualdj_executable() -> Path | None:
    registered = registry_value(
        r'Software\Microsoft\Windows\CurrentVersion\App Paths\virtualdj.exe', '')
    paths = [Path(registered)] if registered else []
    paths.extend(Path(os.environ[name]) / 'VirtualDJ' / 'virtualdj.exe'
                 for name in ('ProgramFiles', 'ProgramFiles(x86)') if os.environ.get(name))
    return next((path for path in paths if path.is_file()), None)


def executable_version(path: Path | None) -> str | None:
    if os.name != 'nt' or path is None:
        return None
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not version.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 16:
        return None
    fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    return '.'.join(str(part) for part in (fixed[2] >> 16, fixed[2] & 65535,
                                           fixed[3] >> 16, fixed[3] & 65535))


def virtualdj_pid() -> int | None:
    if os.name != 'nt':
        return None
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq virtualdj.exe', '/NH', '/FO', 'CSV'],
                            capture_output=True, check=True, timeout=4,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    rows = csv.reader(io.StringIO(result.stdout.decode('mbcs', errors='replace')))
    return next((int(row[1]) for row in rows if len(row) > 1
                 and row[0].casefold() == 'virtualdj.exe'), None)


def virtualdj_running() -> bool:
    return virtualdj_pid() is not None


def mapper_is_selected(home=None):
    home = home or vdj_home()
    try:
        selection = keyboard_selection((home / 'settings.xml').read_bytes())
        return selection is not None and selection.get('mapper') == MAPPER_NAME
    except (OSError, ValueError, ET.ParseError):
        return False


def mapper_is_active(options, home=None):
    home = home or vdj_home()
    if not mapper_is_selected(home):
        return False
    try:
        mapper = (home / 'Mappers' / f'{MAPPER_NAME}.xml').read_bytes()
        manifest = home / STATE_DIR / 'installation.json'
        state = json.loads(manifest.read_text('utf-8'))
        return (state.get('revision') == MAPPER_REVISION
                and state.get('options') == options_data(options)
                and state.get('mapper_hash') == hashlib.sha256(mapper).hexdigest())
    except (OSError, ValueError, ET.ParseError):
        return False


def options_data(options):
    return {**asdict(options), 'g_keys': list(options.g_keys)}


def require_closed():
    if virtualdj_running():
        raise RuntimeError('Close VirtualDJ before changing its keyboard mapping.')


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        with temporary.open('wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def keyboard_selection(settings: bytes):
    root = ET.fromstring(settings)
    if root.tag != 'settings':
        raise ValueError('Unexpected VirtualDJ settings root.')
    nodes = [node for node in root.findall('./controllers/controllersCustomization/controller')
             if node.get('name', '').upper() == 'KEYBOARD']
    if len(nodes) > 1:
        raise ValueError('VirtualDJ has multiple keyboard mapper selections.')
    return nodes[0] if nodes else None


CUSTOMIZATIONS = re.compile(
    rb'<controllersCustomization(?P<empty>\s*/>|\s*>(?P<body>.*?)</controllersCustomization\s*>)',
    re.DOTALL)
CONTROLLER = re.compile(rb'<controller\s+[^<>]*?\s*/>')
MAPPER_ATTRIBUTE = re.compile(rb'(?P<prefix>\bmapper\s*=\s*)(["\'])(.*?)\2')


def select_keyboard(settings: bytes, selection) -> bytes:
    current = keyboard_selection(settings)
    if current is None and selection is None:
        return settings
    if current is not None and selection is not None and current.attrib == selection.attrib:
        return settings
    section = CUSTOMIZATIONS.search(settings)
    if section is None:
        raise ValueError('VirtualDJ settings lack the expected controllersCustomization section.')
    replacement = b'' if selection is None else ET.tostring(selection, encoding='utf-8').strip()
    if current is None:
        if section.group('body') is None:
            start, end = section.span()
            replacement = b'<controllersCustomization>' + replacement + b'</controllersCustomization>'
        else:
            start = end = section.end('body')
    else:
        body = section.group('body') or b''
        matches = [tag for tag in CONTROLLER.finditer(body)
                   if ET.fromstring(tag.group()).get('name', '').upper() == 'KEYBOARD']
        if len(matches) != 1:
            raise ValueError('VirtualDJ keyboard selection uses an unfamiliar XML representation.')
        tag = matches[0]
        start, end = (section.start('body') + offset for offset in tag.span())
        if selection is not None and {k: v for k, v in current.attrib.items() if k != 'mapper'} == {
                k: v for k, v in selection.attrib.items() if k != 'mapper'}:
            pattern = MAPPER_ATTRIBUTE.search(tag.group())
            desired = ET.tostring(selection, encoding='utf-8').strip()
            value = MAPPER_ATTRIBUTE.search(desired)
            if pattern is not None and value is not None and pattern.group(2) == value.group(2):
                replacement = tag.group()[:pattern.start(3)] + value.group(3) + tag.group()[pattern.end(3):]
        if selection is None:
            line_start = settings.rfind(b'\n', 0, start) + 1
            line_end = settings.find(b'\n', end)
            if line_end >= 0 and not settings[line_start:start].strip() and not settings[end:line_end].strip():
                start, end = line_start, line_end + 1
            if not (settings[section.start('body'):start] + settings[end:section.end('body')]).strip():
                start, end = section.span()
                replacement = b'<controllersCustomization />'
    result = settings[:start] + replacement + settings[end:]
    verified = keyboard_selection(result)
    if (verified.attrib if verified is not None else None) != (
            selection.attrib if selection is not None else None):
        raise ValueError('Could not verify the updated VirtualDJ keyboard selection.')
    return result


def current_mapper(home: Path, selected):
    name = selected.get('mapper', '') if selected is not None else ''
    if not name or name == MAPPER_NAME or '/' in name or '\\' in name:
        return None
    path = home / 'Mappers' / f'{name}.xml'
    return path.read_bytes() if path.is_file() else None


def backup_version(store: Path, settings: bytes, mapper: bytes | None) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='before-', dir=store))
    atomic_write(folder / 'settings.xml', settings)
    if mapper is not None:
        atomic_write(folder / 'mapper.xml', mapper)
    return folder


def apply_changes(settings_path, old_settings, new_settings, mapper_path, old_mapper, new_mapper):
    require_closed()
    if settings_path.read_bytes() != old_settings:
        raise RuntimeError('VirtualDJ changed its settings. Retry after it finishes closing.')
    actual = mapper_path.read_bytes() if mapper_path.exists() else None
    if actual != old_mapper:
        raise RuntimeError('The VirtualDJ mapper changed during setup.')
    if new_mapper is None:
        mapper_path.unlink(missing_ok=True)
    elif new_mapper != old_mapper:
        atomic_write(mapper_path, new_mapper)
    try:
        require_closed()
        if new_settings != old_settings:
            if settings_path.read_bytes() != old_settings:
                raise RuntimeError('VirtualDJ settings changed before the mapping could be activated.')
            atomic_write(settings_path, new_settings)
    except Exception:
        if old_mapper is None:
            mapper_path.unlink(missing_ok=True)
        elif new_mapper != old_mapper:
            atomic_write(mapper_path, old_mapper)
        raise


def install_mapping(home: Path | None = None, apply_settings: bool = True,
                    options: DjOptions | None = None) -> dict:
    require_closed()
    home = home or vdj_home()
    settings_path = home / 'settings.xml'
    mapper_path = home / 'Mappers' / f'{MAPPER_NAME}.xml'
    store = home / STATE_DIR
    manifest = store / 'installation.json'
    try:
        settings = settings_path.read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError(f'Open VirtualDJ once to initialize its settings in {home}.') from exc
    old_mapper = mapper_path.read_bytes() if mapper_path.exists() else None
    selected = keyboard_selection(settings)
    attrs = dict(selected.attrib) if selected is not None else {'name': 'KEYBOARD'}
    selection = ET.Element('controller', {**attrs, 'mapper': MAPPER_NAME})
    updated = select_keyboard(settings, selection) if apply_settings else settings
    state = json.loads(manifest.read_text('utf-8')) if manifest.exists() else {}
    selection_origin = read_origin(store, state, 'selection_origin') if state else None
    already_selected = selected is not None and selected.get('mapper') == MAPPER_NAME
    base_selection = selected
    if already_selected and selection_origin:
        base_selection = keyboard_selection((selection_origin / 'settings.xml').read_bytes())
    options = options or DjOptions()
    new_mapper = build_mapper(options, current_mapper(home, base_selection))
    backup = backup_version(store, settings, old_mapper)
    state = {'origin': state.get('origin', backup.name),
             'selection_origin': selection_origin.name if already_selected and selection_origin else backup.name,
             'mapper_hash': hashlib.sha256(new_mapper).hexdigest(),
             'options': options_data(options), 'revision': MAPPER_REVISION}
    previous_manifest = manifest.read_bytes() if manifest.exists() else None
    atomic_write(manifest, json.dumps(state).encode('utf-8'))
    try:
        apply_changes(settings_path, settings, updated, mapper_path, old_mapper, new_mapper)
    except Exception:
        if previous_manifest is None:
            manifest.unlink(missing_ok=True)
        else:
            atomic_write(manifest, previous_manifest)
        raise
    return {'mapper': str(mapper_path), 'settings_updated': apply_settings,
            'backups': [str(backup)], 'version': executable_version(virtualdj_executable())}


def read_origin(store, state, key='origin'):
    name = state.get(key, state.get('origin', ''))
    if not re.fullmatch(r'before-[A-Za-z0-9_-]+', name):
        raise ValueError('Invalid VirtualDJ mapper backup reference.')
    return store / name


def rollback_mapping(home: Path | None = None) -> dict:
    require_closed()
    home = home or vdj_home()
    store = home / STATE_DIR
    manifest = store / 'installation.json'
    if not manifest.is_file():
        raise RuntimeError('No Corner HUD mapper installation backup was found.')
    state = json.loads(manifest.read_text('utf-8'))
    origin = read_origin(store, state)
    settings_path = home / 'settings.xml'
    mapper_path = home / 'Mappers' / f'{MAPPER_NAME}.xml'
    settings = settings_path.read_bytes()
    old_mapper = mapper_path.read_bytes() if mapper_path.exists() else None
    if old_mapper is not None and hashlib.sha256(old_mapper).hexdigest() != state['mapper_hash']:
        raise RuntimeError('The installed mapper has changed since setup. The original backup is still available.')
    selection = keyboard_selection(settings)
    activation_origin = read_origin(store, state, 'selection_origin')
    previous = keyboard_selection((activation_origin / 'settings.xml').read_bytes())
    updated = settings
    if selection is not None and selection.get('mapper') == MAPPER_NAME:
        attrs = dict(selection.attrib)
        attrs.pop('mapper', None)
        if previous is not None and 'mapper' in previous.attrib:
            attrs['mapper'] = previous.get('mapper')
        restored = ET.Element('controller', attrs) if previous is not None or set(attrs) != {'name'} else None
        updated = select_keyboard(settings, restored)
    original_mapper = origin / 'mapper.xml'
    restore_mapper = original_mapper.read_bytes() if original_mapper.is_file() else None
    backup = backup_version(store, settings, old_mapper)
    apply_changes(settings_path, settings, updated, mapper_path, old_mapper, restore_mapper)
    manifest.unlink()
    return {'backups': [str(backup)]}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    print(json.dumps(rollback_mapping() if args.rollback else install_mapping(), indent=2))
