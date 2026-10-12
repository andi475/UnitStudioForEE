# SPDX-License-Identifier: MIT
# Part of EE Unit Studio, an unofficial fan-made modding tool for Empire Earth.
"""
Empire Earth: The Art of Conquest - add new units from the command line
(the Blender add-on in ee_unit_studio does the same interactively).

  py ee_unitmod.py install      restore the original game files, then apply units.json
  py ee_unitmod.py uninstall    restore the original game files
  py ee_unitmod.py list [text]  list unit names/IDs (optionally filtered)
  py ee_unitmod.py extract <unit>
                                copy a unit's 3D model + texture into
                                UnitMod\\extracted and show its animation frames

The first change backs up Data\\data.ssa and Language.dll as *.orig.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'ee_unit_studio'))
import gamedata as g  # noqa: E402
import cem  # noqa: E402

GAME = os.path.dirname(HERE)


def find(s, ref):
    if str(ref).isdigit():
        return int(ref)
    for oid in s.unit_ids():
        u = s.unit(oid)
        if ref in (u['internal'], u['name']):
            return oid
    raise SystemExit(f'unit "{ref}" not found (try: py ee_unitmod.py list {str(ref).split()[0]})')


def install():
    cfg = json.load(open(os.path.join(HERE, 'units.json'), encoding='utf-8'))
    s = g.GameSession(GAME)
    s.restore()
    print('Adding units:')
    for u in cfg['units']:
        base = find(s, u['base'])
        base_anims = s.unit(base).get('anims')
        oid, desc = s.new_unit(base, u['name'], u.get('epoch'), u.get('button_slot'), u.get('available_now', True))
        s.set_stats(oid, name=u.get('display_name'), hp=u.get('hp'), attack=u.get('attack'),
                    range=u.get('range'), line_of_sight=u.get('line_of_sight'),
                    reload_time=u.get('reload_time'), speed=u.get('speed'),
                    armor=u.get('armor'), build_time=u.get('build_time'), **{c: u.get(c) for c in g.COSTS})
        o = s.objs.recs[oid]
        if 'icon' in u:  # any existing button texture
            p = u['icon'].encode('latin1')[:99]
            s.btns.recs[g.i32(o, g.OBJ['button'])][100:200] = p + bytes(100 - len(p))
        for off, v in u.get('raw_object_fields', {}).items():
            (g.set_f32 if isinstance(v, float) else g.set_i32)(o, int(off), v)
        read = lambda key: open(os.path.join(HERE, u[key]), 'rb').read() if key in u else None
        tex = read('texture')
        if tex and u['texture'].lower().endswith('.tga'):  # wrap a plain uncompressed TGA as .sst
            tex = cem.tga_to_sst(tex)
        if 'model' in u or tex:
            s.set_model(oid, read('model'), tex)
        if 'animations' in u or 'model' in u:
            anims = dict(base_anims)
            anims.update({k: tuple(v) for k, v in u.get('animations', {}).items()})
            s.set_animations(oid, anims)
        print(f'  + {u["name"]}: unit #{oid}, {desc}')
    s.save()
    print("Done. Start the game and look in the building's command panel.")


def uninstall():
    restored = g.GameSession(GAME).restore()
    print('restored', ', '.join(restored) if restored else 'nothing (no backups found)')


def list_units(flt=''):
    s = g.GameSession(GAME)
    for oid in s.unit_ids():
        u = s.unit(oid)
        if flt.lower() in (u['internal'] + ' ' + u['name']).lower():
            print(f'{oid:4}  {u["internal"]:40} {u["name"]:24} hp={u["hp"]:<5} attack={u["attack"]:<4} '
                  f'range={u["range"]:g}')


def extract(ref):
    s = g.GameSession(GAME)
    oid = find(s, ref)
    cem, tex, tex_name = s.model_files(oid)
    out = os.path.join(HERE, 'extracted')
    os.makedirs(out, exist_ok=True)
    stem = s.unit(oid)['model'].split('\\')[-1].lower()
    open(os.path.join(out, stem + '.cem'), 'wb').write(cem)
    print('  wrote extracted\\' + stem + '.cem')
    if tex:
        name = stem + 't' + os.path.splitext(tex_name)[1].lower()
        open(os.path.join(out, name), 'wb').write(tex)
        print('  wrote extracted\\' + name)
    print(f'Animation frames of "{s.unit(oid)["internal"]}" (first-last):')
    for a, (first, last) in s.unit(oid)['anims'].items():
        print(f'  {a:8} {first}-{last}')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        if cmd == 'install':
            install()
        elif cmd == 'uninstall':
            uninstall()
        elif cmd == 'list':
            list_units(' '.join(sys.argv[2:]))
        elif cmd == 'extract' and len(sys.argv) > 2:
            extract(' '.join(sys.argv[2:]))
        else:
            print(__doc__)
    except g.GameError as e:
        raise SystemExit(str(e))
