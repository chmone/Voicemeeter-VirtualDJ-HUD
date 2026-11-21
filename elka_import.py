"""Read-only best-effort extraction of IDs from ELKA saved JSON/XML layouts.

Does NOT edit an ELKA file, load a VST, or wire its graph.
Only exact ID/name pairs from named VST nodes are returned for user approval.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import xml.etree.ElementTree as ET

NAME_KEYS=('pluginName','vstName','plugin_name','vst_name','displayName','name','label')
ID_KEYS=('stableVstId','stableVSTId','StableVstId','vstId','VstId','stableId','StableId')

def extract_layout_nodes(path: Path) -> list[dict]:
    path=Path(path)
    if path.stat().st_size > 16*1024*1024:
        raise ValueError('Layout larger than 16 MB; unsupported')
    raw=path.read_text(encoding='utf-8-sig')
    if raw.lstrip().startswith('<'):
        root=ET.fromstring(raw)
        def elem_to_dict(n):
            row=dict(n.attrib)
            for e in n:
                row.setdefault(e.tag,[]).append(elem_to_dict(e))
            return row
        layout=elem_to_dict(root)
    else:
        layout=json.loads(raw)
    if isinstance(layout, dict) and isinstance(layout.get('PluginNodes'), list):
        # ELKA 0.8.3.0 uses InstanceId for VBAN. Slot is assigned by the engine.
        active = {}
        for node in layout['PluginNodes']:
            if not isinstance(node, dict) or node.get('MissingPlugin'):
                continue
            stable_id = node.get('InstanceId')
            name = node.get('Name')
            if type(stable_id) is int and 0 <= stable_id < 200 and isinstance(name, str):
                label = re.sub(r'\s*\[VST[23]\]$', '', name.strip(), flags=re.I)
                if label:
                    active[stable_id] = {'name': label[:48], 'id': stable_id}
        return sorted(active.values(), key=lambda row: row['id'])
    matches={}
    def walk(item,location=''):
        if isinstance(item,dict):
            name=next((str(item[k]).strip() for k in NAME_KEYS if isinstance(item.get(k),str) and item[k].strip()),None)
            stable=next((item[k] for k in ID_KEYS if k in item),None)
            context=(location+' '+str(item.get('type',''))+' '+str(item.get('kind',''))).lower()
            if name and stable is not None and ('vst' in context or 'plugin' in context):
                try:
                    stable=int(stable)
                    if stable>=0 and not name.isnumeric():
                        matches[stable]={'name':name[:48],'id':stable}
                except (TypeError,ValueError):
                    pass
            for k,value in item.items():
                walk(value,location+' '+k)
        elif isinstance(item,list):
            for v in item:
                walk(v,location)
    walk(layout)
    return sorted(matches.values(),key=lambda r:r['id'])

def auto_discover_elka_nodes(root: Path|None=None) -> list[dict]:
    """Find IDs in supported saved-layout files; never touch the active ELKA graph."""
    if root is None:
        appdata = Path(os.environ.get('APPDATA', Path.home()))
        local = Path(os.environ.get('LOCALAPPDATA', Path.home()))
        for path in (appdata / 'ElkaSoft' / 'VoiceMeeterFxHost' / 'wpf-main-window.json',
                     local / 'ElkaVoiceMeeterFxHost' / 'wpf-main-window.json'):
            if path.is_file():
                try:
                    return extract_layout_nodes(path)
                except (OSError, ValueError, ET.ParseError):
                    return []
        root = local / 'ElkaSoft' / 'VoiceMeeterFxHost'
    if not root.is_dir():
        return []
    found={}
    for pattern in ('*.json','*.xml','*/*.json','*/*.xml','*/*/*.json','*/*/*.xml'):
        for candidate in root.glob(pattern):
            try:
                if candidate.stat().st_size>16*1024*1024:
                    continue
                for row in extract_layout_nodes(candidate):
                    found[row['id']]=row
            except (OSError,UnicodeError,ValueError,ET.ParseError,json.JSONDecodeError):
                continue
    return sorted(found.values(),key=lambda row:row['id'])
