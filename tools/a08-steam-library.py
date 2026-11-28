#!/usr/bin/env python3
"""A08.1: local Steam library inventory, standard library only, no writes."""
import argparse
import json
import re
import tempfile
from pathlib import Path

TOKEN = re.compile(r'//[^\n]*|"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+')

def parse(text):
    tokens = [m.group() for m in TOKEN.finditer(text) if not m.group().startswith('//')]
    def atom(s):
        return re.sub(r'\\(["\\])', r'\1', s[1:-1]) if s.startswith('"') else s
    def obj(i, nested=False):
        out = {}
        while i < len(tokens):
            k = tokens[i]; i += 1
            if k == '}':
                if not nested: raise ValueError('unexpected closing brace')
                return out, i
            if k == '{' or i >= len(tokens): raise ValueError('invalid key/value')
            v = tokens[i]; i += 1
            if v == '{': v, i = obj(i, True)
            elif v == '}': raise ValueError('missing value')
            else: v = atom(v)
            out[atom(k)] = v
        if nested: raise ValueError('unclosed object')
        return out, i
    return obj(0)[0]

def read_vdf(p):
    if p.stat().st_size > 32*1024*1024: raise ValueError('file too large')
    return parse(p.read_text(encoding='utf-8-sig', errors='strict'))

def scan(roots):
    libraries = set()
    errors = []
    for root in roots:
        root = root.expanduser().resolve()
        if not (root/'steamapps').is_dir(): continue
        libraries.add(root)
        p = root/'steamapps/libraryfolders.vdf'
        if not p.is_file(): continue
        try:
            data = read_vdf(p)
            folders = data.get('libraryfolders', data.get('LibraryFolders', {}))
            for k,v in folders.items():
                if not k.isdecimal(): continue
                path = v.get('path') if isinstance(v,dict) else v
                if isinstance(path,str) and Path(path).is_absolute():
                    libraries.add(Path(path).resolve())
        except (OSError, ValueError, AttributeError) as e:
            errors.append(f'{p}: {e}')
    games = {}
    for root in sorted(libraries):
        apps = root/'steamapps'
        if not apps.is_dir():
            errors.append(f'Library unavailable: {root}')
            continue
        for p in sorted(apps.glob('appmanifest_*.acf')):
            try:
                a = read_vdf(p)['AppState']
                appid = str(a['appid'])
                if not appid.isdecimal() or p.name != f'appmanifest_{appid}.acf':
                    raise ValueError('appid mismatch')
                name = a['name']
                flags = int(a.get('StateFlags','0'))
                folder = a.get('installdir','')
                location = apps/'common'/folder
                if not folder or Path(folder).name != folder: raise ValueError('invalid installdir')
                if not flags & 4 or not location.is_dir(): continue
                entry = games.setdefault(appid, {'appid':appid,'name':name,'locations':[]})
                entry['locations'].append(str(location))
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                errors.append(f'{p}: {e}')
    return {'libraries':[str(x) for x in sorted(libraries)],'games':sorted(games.values(),key=lambda x:(x['name'].casefold(),x['appid'])),'warnings':errors}

def selftest():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)/'Steam'; other=Path(d)/'Other SSD'
        for p in (root,other):
            (p/'steamapps/common/Test Game').mkdir(parents=True)
        (root/'steamapps/libraryfolders.vdf').write_text('"libraryfolders" { "1" { "path" "'+str(other)+'" } }')
        for p in (root,other):
            (p/'steamapps/appmanifest_42.acf').write_text('"AppState" { "appid" "42" "name" "Test Game" "StateFlags" "4" "installdir" "Test Game" }')
        result=scan([root])
        assert len(result['games'])==1 and len(result['games'][0]['locations'])==2 and not result['warnings'],result
        assert parse('"a" "b" // comment\n "c" { "d" "e" }')=={'a':'b','c':{'d':'e'}}
    print('Self-test OK')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--root',action='append',type=Path)
    args=ap.parse_args()
    if args.self_test: selftest(); return
    h=Path.home()
    roots=args.root or [h/'.steam/steam',h/'.steam/root',h/'.local/share/Steam',h/'.var/app/com.valvesoftware.Steam/.local/share/Steam']
    print(json.dumps(scan(roots),ensure_ascii=False,indent=2))

if __name__=='__main__': main()
