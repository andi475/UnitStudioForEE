# SPDX-License-Identifier: MIT
# Part of EE Unit Studio, an unofficial fan-made modding tool for Empire Earth.
"""
Read and modify Empire Earth: The Art of Conquest game data (pure Python).

  Data\\data.ssa   archive ('rass' header, sorted lower-case file table); every
                  file inside is PKWARE-DCL compressed behind a 'PK01' header
  Language.dll    string table resource with the names shown in game

The base game's archive (..\\Empire Earth\\Data\\data.ssa) is read too: AoC takes
most of the original models and textures from there.
"""
import ctypes
import os
import re
import shutil
import struct

# ---------------------------------------------------------------- PKWARE DCL
# explode() and its tables are an ALTERED VERSION (a Python port) of blast.c
# from zlib's contrib/blast, which carries this notice:
#
#   Copyright (C) 2003, 2012, 2013 Mark Adler
#
#   This software is provided 'as-is', without any express or implied
#   warranty.  In no event will the author be held liable for any damages
#   arising from the use of this software.
#
#   Permission is granted to anyone to use this software for any purpose,
#   including commercial applications, and to alter it and redistribute it
#   freely, subject to the following restrictions:
#
#   1. The origin of this software must not be misrepresented; you must not
#      claim that you wrote the original software. If you use this software
#      in a product, an acknowledgment in the product documentation would be
#      appreciated but is not required.
#   2. Altered source versions must be plainly marked as such, and must not be
#      misrepresented as being the original software.
#   3. This notice may not be removed or altered from any source distribution.
#
# implode() (a literal-only compressor) was written for this project.
def _construct(rep):
    length = []
    for b in rep:
        length += [b & 15] * ((b >> 4) + 1)
    count = [0] * 14
    for l in length:
        count[l] += 1
    offs = [0] * 14
    for i in range(1, 13):
        offs[i + 1] = offs[i] + count[i]
    sym = [0] * len(length)
    for s, l in enumerate(length):
        if l:
            sym[offs[l]] = s
            offs[l] += 1
    return count, sym

_LIT = _construct(bytes([11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8, 9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5, 7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12, 8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27, 44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45, 44, 173]))
_LEN = _construct(bytes([2, 35, 36, 53, 38, 23]))
_DIST = _construct(bytes([2, 20, 53, 230, 247, 151, 248]))
_BASE = [3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264]
_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]

def explode(data):
    st = [0, 0, 0]  # pos, bitbuf, bitcnt
    def bits(need):
        val = st[1]
        while st[2] < need:
            val |= data[st[0]] << st[2]
            st[0] += 1
            st[2] += 8
        st[1] = val >> need
        st[2] -= need
        return val & ((1 << need) - 1)
    def decode(h):
        count, sym = h
        code = first = index = 0
        for l in range(1, 14):
            code |= bits(1) ^ 1
            c = count[l]
            if code < first + c:
                return sym[index + (code - first)]
            index += c
            first = (first + c) << 1
            code <<= 1
        raise ValueError('bad DCL code')
    lit, dictb = bits(8), bits(8)
    out = bytearray()
    while True:
        if bits(1):
            sym = decode(_LEN)
            ln = _BASE[sym] + bits(_EXTRA[sym])
            if ln == 519:
                return bytes(out)
            sb = 2 if ln == 2 else dictb
            dist = (decode(_DIST) << sb) + bits(sb) + 1
            for _ in range(ln):
                out.append(out[-dist])
        else:
            out.append(decode(_LIT) if lit else bits(8))

def _end_code():
    count, sym = _LEN
    code = idx = 0
    for l in range(1, 14):
        for _ in range(count[l]):
            if sym[idx] == 15:
                return code, l
            code += 1
            idx += 1
        code <<= 1

def implode(data):
    """Valid DCL stream made of raw literals only (binary mode, 4K dictionary)."""
    out = bytearray([0, 6])
    full = len(data) - len(data) % 8
    for i in range(0, full, 8):  # 8 literals = 72 bits = 9 whole bytes
        c = data[i:i + 8]
        v = (c[0] << 1 | c[1] << 10 | c[2] << 19 | c[3] << 28 | c[4] << 37 | c[5] << 46 | c[6] << 55 | c[7] << 64)
        out += v.to_bytes(9, 'little')
    st = [0, 0]  # acc, nbits
    def put(v, nb):
        st[0] |= v << st[1]
        st[1] += nb
        while st[1] >= 8:
            out.append(st[0] & 255)
            st[0] >>= 8
            st[1] -= 8
    for b in data[full:]:
        put(0, 1)
        put(b, 8)
    put(1, 1)  # end of stream: length symbol 15 + 8 extra bits of 255
    code, l = _end_code()
    for i in range(l - 1, -1, -1):
        put(((code >> i) & 1) ^ 1, 1)
    put(255, 8)
    if st[1]:
        out.append(st[0] & 255)
    return bytes(out)

def unpack_file(blob):
    if blob[:4] != b'PK01':
        return blob
    data = explode(blob[12:])
    assert len(data) == struct.unpack_from('<I', blob, 4)[0]
    return data

def pack_file(data):
    return b'PK01' + struct.pack('<II', len(data), 0) + implode(data)

# ---------------------------------------------------------------- SSA archive
class Archive:
    """Reads the file table; file contents are read on demand."""
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            head = f.read(16)
            if head[:4] != b'rass':
                raise ValueError(f'{path} is not an SSA archive')
            self.version, self.zero, tsize = struct.unpack_from('<III', head, 4)
            table = f.read(tsize)
            self.entries = {}
            off = first = 0
            while off < len(table):
                n, = struct.unpack_from('<I', table, off)
                name = table[off + 4:off + 4 + n].rstrip(b'\0').decode('latin1')
                off += 4 + n
                s, e, _ = struct.unpack_from('<III', table, off)
                off += 12
                self.entries[name] = (s, e - s + 1)
                first = s if not first else min(first, s)
            self.meta = f.read(first - 16 - tsize)  # key/value block (timestamp)
    def read(self, name):
        s, n = self.entries[name]
        with open(self.path, 'rb') as f:
            f.seek(s)
            return f.read(n)
    def read_all(self):
        with open(self.path, 'rb') as f:
            data = f.read()
        return {name: data[s:s + n] for name, (s, n) in self.entries.items()}

def write_archive(path, version, zero, meta, blobs):
    names = sorted(blobs)  # the game expects a sorted, lower-case table
    table = bytearray()
    for name in names:
        nb = name.encode('latin1') + b'\0'
        table += struct.pack('<I', len(nb)) + nb + b'\0' * 12
    pos, t = 16 + len(table) + len(meta), 0
    for name in names:
        t += 4 + len(name.encode('latin1')) + 1
        struct.pack_into('<III', table, t, pos, pos + len(blobs[name]) - 1, len(blobs[name]))
        t += 12
        pos += len(blobs[name])
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(b'rass' + struct.pack('<III', version, zero, len(table)))
        f.write(table)
        f.write(meta)
        for name in names:
            f.write(blobs[name])
    check = Archive(tmp)
    assert sorted(check.entries) == names
    os.replace(tmp, path)

# ---------------------------------------------------------------- databases
OBJ_SIZE, BTN_SIZE = 1960, 224

def i32(r, o): return struct.unpack_from('<i', r, o)[0]
def f32(r, o): return struct.unpack_from('<f', r, o)[0]
def set_i32(r, o, v): struct.pack_into('<i', r, o, int(v))
def set_f32(r, o, v): struct.pack_into('<f', r, o, float(v))
def name_of(r): return bytes(r[:100]).split(b'\0')[0].decode('latin1')
def set_name(r, s):
    b = s.encode('latin1', 'replace')[:99]
    r[:100] = b + b'\0' * (100 - len(b))

class FixedTable:
    def __init__(self, data, size):
        n, = struct.unpack_from('<I', data, 0)
        self.recs = [bytearray(data[4 + i * size:4 + (i + 1) * size]) for i in range(n)]
    def build(self):
        return struct.pack('<I', len(self.recs)) + b''.join(self.recs)

class TechTree:
    """One section per epoch: u32 (records - 1), then variable records whose
    tail is a u32 count at 268 followed by that many building IDs."""
    def __init__(self, data):
        off, self.secs = 0, []
        while off < len(data):
            n, = struct.unpack_from('<I', data, off)
            off += 4
            recs = []
            for _ in range(n + 1):
                c, = struct.unpack_from('<I', data, off + 268)
                ln = 272 + 4 * c
                recs.append(bytearray(data[off:off + ln]))
                off += ln
            self.secs.append(recs)
    def build(self):
        out = bytearray()
        for recs in self.secs:
            out += struct.pack('<I', len(recs) - 1)
            for r in recs:
                out += r
        return bytes(out)
    @staticmethod
    def key(sec, idx):
        # how buildings refer to a tech: (epoch section + 1) * 1000 + index - 1
        return (sec + 1) * 1000 + idx - 1
    def find_key(self, k):
        s, i = k // 1000 - 1, k % 1000 + 1
        if 0 <= s < len(self.secs) and 0 < i < len(self.secs[s]) and i32(self.secs[s][i], TECH['key']) == k:
            return s, i, self.secs[s][i]
        return None

class GraphicsTable:
    """Per record 33 length-prefixed strings (name, model path without
    extension, texture, 30 extra texture slots, the last being the shadow)
    followed by 716 fixed bytes (name[100], id at 104, animation table)."""
    NSTR, FIXED = 33, 716
    def __init__(self, data):
        n, = struct.unpack_from('<I', data, 0)
        off, self.recs = 4, []
        for _ in range(n):
            strs = []
            for _ in range(self.NSTR):
                L, = struct.unpack_from('<I', data, off)
                strs.append(data[off + 4:off + 4 + L].decode('latin1'))
                off += 4 + L
            self.recs.append([strs, bytearray(data[off:off + self.FIXED])])
            off += self.FIXED
        assert off == len(data)
    def build(self):
        out = bytearray(struct.pack('<I', len(self.recs)))
        for strs, fixed in self.recs:
            for x in strs:
                b = x.encode('latin1')
                out += struct.pack('<I', len(b)) + b
            out += fixed
        return bytes(out)

class EffectTable:
    """dbeffects: 88-byte records, no header. Used records have their own index
    at offset 4; the effect type is at 32 and its target tech key at 40
    (type 8 = enable that tech, 9 = disable it, 19 = upgrade units)."""
    SIZE = 88
    def __init__(self, data):
        self.recs = [bytearray(data[i:i + self.SIZE]) for i in range(0, len(data), self.SIZE)]
    def build(self):
        return b''.join(self.recs)

class EventTable:
    """dbevents: name[100], u32 id, u32 count, count effect indices; no header.
    A tech's event (index at tech offset 116) fires when it is researched."""
    def __init__(self, data):
        off, self.recs = 0, []
        while off + 108 <= len(data):
            cnt, = struct.unpack_from('<I', data, off + 104)
            self.recs.append([data[off:off + 104], list(struct.unpack_from('<%di' % cnt, data, off + 108))])
            off += 108 + 4 * cnt
        assert off == len(data)
    def build(self):
        out = bytearray()
        for head, effects in self.recs:
            out += head + struct.pack('<I%di' % len(effects), len(effects), *effects)
        return bytes(out)

EFFECT = dict(id=0, index=4, type=32, key=40)
ENABLE_TECH = 8

# Field offsets, identified by comparing known units.
OBJ = dict(id=116, hp=120, range=132, attack=192, button=296, graphics=300, name_string=472,
           tech_count=884,  # how many entries of tech_list the game reads
           tech_key=888, tech_list=1156)
# Costs: wood 124 (buildings, archers, ships), stone 128 (buildings), gold 136
# (cavalry, priests), iron 144 (foot soldiers), food 148. 132 and 140 are
# practically unused (2 and 0 of the game's techs).
TECH = dict(id=100, key=104, first_epoch=108, last_epoch=112, wood=124, stone=128, gold=136,
            iron=144, food=148, build_ms=152, requires=156, object=172, button=176)
BTN = dict(id=204, slot=216)
ANIM = dict(idle=(164, 168), walk=(124, 128), attack=(204, 208), attack2=(244, 248), death=(284, 288))
CORPSE = (308, 312)  # frame shown after death (= last death frame)
GFX_ID = 104
COSTS = ('food', 'wood', 'stone', 'gold', 'iron')
EPOCHS = ['Prehistoric', 'Stone', 'Copper', 'Bronze', 'Dark', 'Middle', 'Renaissance', 'Imperial',
          'Industrial', 'Atomic WW1', 'Atomic WW2', 'Atomic Modern', 'Digital', 'Nano', 'Space']
FIRST_STRING_ID = 40000  # Language.dll's own strings stop at 33005
DB = {'objs': 'db\\dbobjects.dat', 'techs': 'db\\dbtechtree.dat', 'btns': 'db\\dbbuttons.dat',
      'gfx': 'db\\dbgraphics.dat', 'effects': 'db\\dbeffects.dat', 'events': 'db\\dbevents.dat'}

# ---------------------------------------------------------------- Language.dll
def lang_strings(path):
    """Returns ({string id: text}, language id)."""
    d = open(path, 'rb').read()
    pe, = struct.unpack_from('<I', d, 0x3c)
    nsec, = struct.unpack_from('<H', d, pe + 6)
    optsz, = struct.unpack_from('<H', d, pe + 20)
    oh = pe + 24
    rva_rsrc, = struct.unpack_from('<I', d, oh + 96 + 16)
    secs = [struct.unpack_from('<8sIIII', d, oh + optsz + 40 * i) for i in range(nsec)]
    def r2o(rva):
        for _, vs, va, rs, ro in secs:
            if va <= rva < va + max(vs, rs):
                return rva - va + ro
    base = r2o(rva_rsrc)
    def entries(off):
        a, b = struct.unpack_from('<HH', d, off + 12)
        return [struct.unpack_from('<II', d, off + 16 + 8 * i) for i in range(a + b)]
    strings, langid = {}, None
    for tid, sub in entries(base):
        if tid != 6:
            continue
        for bid, sub2 in entries(base + (sub & 0x7fffffff)):
            for lid, leaf in entries(base + (sub2 & 0x7fffffff)):
                langid = lid
                rva, _ = struct.unpack_from('<II', d, base + leaf)
                p = r2o(rva)
                for k in range(16):
                    ln, = struct.unpack_from('<H', d, p)
                    p += 2
                    if ln:
                        strings[(bid - 1) * 16 + k] = d[p:p + 2 * ln].decode('utf-16le')
                    p += 2 * ln
    return strings, langid

def write_lang_strings(path, new):  # new: {id: text}
    existing, langid = lang_strings(path)
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.BeginUpdateResourceW.restype = ctypes.c_void_p
    k32.BeginUpdateResourceW.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
    k32.UpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_ushort, ctypes.c_void_p, ctypes.c_uint]
    k32.EndUpdateResourceW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    h = k32.BeginUpdateResourceW(path, False)
    if not h:
        raise OSError(ctypes.get_last_error(), 'cannot open Language.dll for writing (is the game running?)')
    for block in sorted({i // 16 for i in new}):
        buf = bytearray()
        for k in range(16):
            sid = block * 16 + k
            s = new.get(sid, existing.get(sid, ''))
            buf += struct.pack('<H', len(s)) + s.encode('utf-16le')
        data = ctypes.create_string_buffer(bytes(buf), len(buf))
        if not k32.UpdateResourceW(h, 6, block + 1, langid, data, len(buf)):
            raise OSError(ctypes.get_last_error(), 'UpdateResource failed')
    if not k32.EndUpdateResourceW(h, False):
        raise OSError(ctypes.get_last_error(), 'writing Language.dll failed')

# ---------------------------------------------------------------- session
# The game's own graphics records use model paths of at most 32 characters and
# texture paths of at most 39; longer ones can freeze it. Custom files are
# therefore named after their graphics record: 'models\mod879' (+ '.cem'),
# 'textures\mod879t.sst' - at most 20 characters.
CUSTOM_FILE = re.compile(r'^(models|textures)\\mod\d')
MAX_GFX_NAME = 31  # longest graphics name in the game: 33

def custom_model_path(gid):
    return 'models\\mod%d' % gid

def custom_texture_path(gid):
    return 'textures\\mod%dt.sst' % gid

def slug(name):
    return ''.join(c if c.isalnum() else '_' for c in name.lower()).strip('_') or 'unit'

class GameError(Exception):
    pass

class GameSession:
    """Game data loaded into memory. Edit with the methods below, then save()."""

    def __init__(self, game_dir, base_dir=''):
        self.game_dir = game_dir
        self.ssa = os.path.join(game_dir, 'Data', 'data.ssa')
        self.lang = os.path.join(game_dir, 'Language.dll')
        base_dir = base_dir or os.path.join(os.path.dirname(os.path.normpath(game_dir)), 'Empire Earth')
        self.base_ssa = os.path.join(base_dir, 'Data', 'data.ssa')
        for p in (self.ssa, self.lang):
            if not os.path.exists(p):
                raise GameError(f'{p} not found - pick the "Empire Earth - The Art of Conquest" folder')
        self.load()

    def load(self):
        self.aoc = Archive(self.ssa)
        self.base = Archive(self.base_ssa) if os.path.exists(self.base_ssa) else None
        self.objs = FixedTable(unpack_file(self.aoc.read(DB['objs'])), OBJ_SIZE)
        self.techs = TechTree(unpack_file(self.aoc.read(DB['techs'])))
        self.btns = FixedTable(unpack_file(self.aoc.read(DB['btns'])), BTN_SIZE)
        self.gfx = GraphicsTable(unpack_file(self.aoc.read(DB['gfx'])))
        self.effects = EffectTable(unpack_file(self.aoc.read(DB['effects'])))
        self.events = EventTable(unpack_file(self.aoc.read(DB['events'])))
        self.strings, _ = lang_strings(self.lang)
        self.new_strings = {}
        self.new_files = {}

    # ------------------------------------------------------------ reading
    def unit_ids(self):
        return [i for i, r in enumerate(self.objs.recs) if i > 0 and i32(r, OBJ['id']) == i and name_of(r)]

    def tech_of(self, oid):
        t = self.techs.find_key(i32(self.objs.recs[oid], OBJ['tech_key']))
        if t and i32(t[2], TECH['object']) == oid:
            return t
        return None

    def display_name(self, oid):
        return self.new_strings.get(i32(self.objs.recs[oid], OBJ['name_string'])) or \
            self.strings.get(i32(self.objs.recs[oid], OBJ['name_string']), '')

    def unit(self, oid):
        o = self.objs.recs[oid]
        u = dict(id=oid, internal=name_of(o), name=self.display_name(oid), hp=i32(o, OBJ['hp']),
                 attack=i32(o, OBJ['attack']), range=f32(o, OBJ['range']))
        t = self.tech_of(oid)
        u['trainable'] = t is not None
        if t:
            for c in COSTS:
                u[c] = i32(t[2], TECH[c])
            u['build_time'] = i32(t[2], TECH['build_ms']) / 1000.0
            u['epoch'] = t[0] + 1
        g = self.graphics_of(oid)
        if g:
            strs, fixed = g
            u['model'], u['texture'] = strs[1], strs[2]
            u['anims'] = {a: (i32(fixed, x), i32(fixed, y)) for a, (x, y) in ANIM.items()}
        return u

    def graphics_of(self, oid):
        gid = i32(self.objs.recs[oid], OBJ['graphics'])
        if 0 < gid < len(self.gfx.recs) and self.gfx.recs[gid][0][1]:
            return self.gfx.recs[gid]
        return None

    def game_file(self, name):
        name = name.lower()
        if name in self.new_files:
            return unpack_file(self.new_files[name])
        for arc in (self.aoc, self.base):
            if arc and name in arc.entries:
                return unpack_file(arc.read(name))
        return None

    def model_files(self, oid):
        """(cem bytes, texture bytes or None, texture file name) of a unit."""
        g = self.graphics_of(oid)
        if not g:
            raise GameError('this object has no 3D model')
        cem = self.game_file(g[0][1] + '.cem')
        if cem is None:
            raise GameError(f'{g[0][1]}.cem not found in the game archives')
        tex = g[0][2]
        # some texture paths have no extension; the game then finds the .sst
        for name in ([tex] if os.path.splitext(tex)[1] else []) + [os.path.splitext(tex)[0] + e for e in ('.sst', '.tga')]:
            data = self.game_file(name) if tex else None
            if data is not None:
                return cem, data, os.path.basename(name)
        return cem, None, os.path.basename(tex)

    def original_model(self, oid, base_oid=None):
        """The unit's model as shipped with the game (from the *.orig backup),
        so a replaced model can keep the original tag points and sub-models even
        after earlier edits. New units use their base unit's. None if unknown."""
        orig = self.ssa + '.orig'
        try:
            arc = Archive(orig) if os.path.exists(orig) else self.aoc
            objs = FixedTable(unpack_file(arc.read(DB['objs'])), OBJ_SIZE)
            gfx = GraphicsTable(unpack_file(arc.read(DB['gfx'])))
        except (OSError, ValueError, KeyError):
            return None
        if base_oid is None and not (0 < oid < len(objs.recs) and i32(objs.recs[oid], OBJ['id']) == oid):
            base_oid = self._guess_base(oid, objs)
        for i in (oid, base_oid):
            if i is not None and 0 < i < len(objs.recs) and i32(objs.recs[i], OBJ['id']) == i:
                gid = i32(objs.recs[i], OBJ['graphics'])
                if 0 < gid < len(gfx.recs) and gfx.recs[gid][0][1]:
                    name = (gfx.recs[gid][0][1] + '.cem').lower()
                    for a in (arc, self.base):
                        if a and name in a.entries:
                            return unpack_file(a.read(name))
        return None

    def _guess_base(self, oid, orig_objs):
        """Unit a created unit was copied from: the original record that shares
        the most bytes with it (new_unit only changes a handful of fields)."""
        rec = self.objs.recs[oid]
        best, score = None, -1
        for i, r in enumerate(orig_objs.recs):
            if i and i32(r, OBJ['id']) == i:
                same = sum(a == b for a, b in zip(rec[100:], r[100:]))
                if same > score:
                    best, score = i, same
        return best

    def _orig_table(self, key):
        """A table from the *.orig backup of the game files (cached)."""
        cache = self.__dict__.setdefault('_orig_cache', {})
        if key not in cache:
            orig = self.ssa + '.orig'
            if not os.path.exists(orig):
                raise GameError('the backup of the original game files (data.ssa.orig) was not found')
            data = unpack_file(Archive(orig).read(DB[key]))
            cache[key] = {'objs': lambda: FixedTable(data, OBJ_SIZE), 'btns': lambda: FixedTable(data, BTN_SIZE),
                          'techs': lambda: TechTree(data), 'gfx': lambda: GraphicsTable(data)}[key]()
        return cache[key]

    def reset_unit(self, oid):
        """Put one of the game's own units back as shipped: unit record (name,
        stats), tech record (cost, build time, availability), button, graphics
        record (model, texture, animations). A building keeps its current list
        of trainable techs, so units created since stay trainable there."""
        if self.created_base(oid):
            raise GameError('this unit was created with the add-on, so there is no original to go back to')
        oobjs, otechs, obtns, ogfx = (self._orig_table(k) for k in ('objs', 'techs', 'btns', 'gfx'))
        cur = self.objs.recs[oid]
        rec = bytearray(oobjs.recs[oid])
        for off, size in ((OBJ['tech_count'], 4), (OBJ['tech_list'], 800)):
            rec[off:off + size] = cur[off:off + size]
        cur_gid = i32(cur, OBJ['graphics'])
        self.objs.recs[oid] = rec

        key = i32(rec, OBJ['tech_key'])
        orig_tech, cur_tech = otechs.find_key(key), self.techs.find_key(key)
        if orig_tech and cur_tech:
            self.techs.secs[cur_tech[0]][cur_tech[1]] = bytearray(orig_tech[2])
        bid = i32(rec, OBJ['button'])
        if 0 <= bid < len(obtns.recs):
            self.btns.recs[bid] = bytearray(obtns.recs[bid])
        restore_gfx = lambda gid: self.gfx.recs.__setitem__(
            gid, [list(ogfx.recs[gid][0]), bytearray(ogfx.recs[gid][1])])
        gid = i32(rec, OBJ['graphics'])
        if 0 < gid < len(ogfx.recs):
            restore_gfx(gid)
        # a graphics copy made in a free slot (the record was shared): free it again
        if cur_gid != gid and 0 < cur_gid < len(ogfx.recs) and not any(
                i32(self.objs.recs[i], OBJ['graphics']) == cur_gid for i in self.unit_ids()):
            restore_gfx(cur_gid)

    # ------------------------------------------------------------ editing
    def _new_string(self, text):
        used = set(self.strings) | set(self.new_strings)
        sid = max([FIRST_STRING_ID - 1] + [s for s in used if s >= FIRST_STRING_ID]) + 1
        self.new_strings[sid] = text
        return sid

    def set_stats(self, oid, name=None, hp=None, attack=None, range=None, build_time=None, **costs):
        o = self.objs.recs[oid]
        if name is not None and name != (self.display_name(oid) or name_of(o)):
            sid = i32(o, OBJ['name_string'])
            if sid >= FIRST_STRING_ID:  # our own string: just change it
                self.new_strings[sid] = name
            else:  # shared game string: give this unit its own
                set_i32(o, OBJ['name_string'], self._new_string(name))
        if hp is not None:
            set_i32(o, OBJ['hp'], hp)
        if attack is not None:
            set_i32(o, OBJ['attack'], attack)
        if range is not None:
            set_f32(o, OBJ['range'], range)
        t = self.tech_of(oid)
        if t:
            for c in COSTS:
                if costs.get(c) is not None:
                    set_i32(t[2], TECH[c], costs[c])
            if build_time is not None:
                set_i32(t[2], TECH['build_ms'], round(build_time * 1000))

    def new_unit(self, base_oid, name, epoch=None, button_slot=None, available_now=True):
        """Copy of base_oid with its own unit/tech/button records, trained in the
        same buildings. available_now: trainable as soon as its epoch is reached
        (like the Clubman); otherwise only when the base unit is (e.g. after the
        upgrade a battleship needs). Returns (new unit id, description)."""
        bobj = self.objs.recs[base_oid]
        bt = self.tech_of(base_oid)
        if not bt:
            raise GameError(f'"{name_of(bobj)}" is not trained anywhere, pick a trainable unit as the base')
        bs, _, btech = bt
        bbtn = self.btns.recs[i32(btech, TECH['button'])]
        try:
            oid = next(i for i, r in enumerate(self.objs.recs) if i > 0 and i32(r, OBJ['id']) == -1)
            sec = (epoch or bs + 1) - 1
            tidx = next(i for i, r in enumerate(self.techs.secs[sec]) if i > 0 and i32(r, TECH['id']) == -1)
            bid = next(i for i, r in enumerate(self.btns.recs) if i > 1 and i32(r, BTN['id']) == -1)
        except StopIteration:
            raise GameError('no free slot left in the game tables')
        key = TechTree.key(sec, tidx)
        tid = max(i32(r, TECH['id']) for s in self.techs.secs for r in s) + 1

        o = bytearray(bobj)
        set_name(o, name[:MAX_GFX_NAME])  # internal names stay short; the shown name is in Language.dll
        set_i32(o, OBJ['id'], oid)
        set_i32(o, OBJ['button'], bid)
        set_i32(o, OBJ['name_string'], self._new_string(name))
        set_i32(o, OBJ['tech_key'], key)
        self.objs.recs[oid] = o

        t = bytearray(btech)
        set_name(t, name[:MAX_GFX_NAME])
        set_i32(t, TECH['id'], tid)
        set_i32(t, TECH['key'], key)
        set_i32(t, TECH['first_epoch'], sec)
        set_i32(t, TECH['object'], oid)
        set_i32(t, TECH['button'], bid)
        if available_now:  # no prerequisite tech, and (below) no enabling event needed
            set_i32(t, TECH['requires'], 0)
        self.techs.secs[sec][tidx] = t

        base_key = i32(bobj, OBJ['tech_key'])
        trainers = [r for r in self.objs.recs if base_key in struct.unpack_from('<200i', r, OBJ['tech_list'])]
        slot = button_slot or self._free_slot(trainers)
        b = bytearray(bbtn)
        set_name(b, name[:MAX_GFX_NAME])
        set_i32(b, BTN['id'], bid)
        set_i32(b, BTN['slot'], slot)
        self.btns.recs[bid] = b
        for r in trainers:
            lst = struct.unpack_from('<200i', r, OBJ['tech_list'])
            set_i32(r, OBJ['tech_list'] + 4 * lst.index(-1), key)
        where = ', '.join(name_of(r).strip() for r in trainers) or 'nowhere'
        if available_now:
            when = '; trainable as soon as the %s epoch is reached' % EPOCHS[sec]
        else:
            enabled = self.copy_enablers(base_key, key)
            when = ('; becomes trainable together with the base unit (%d event%s)' % (enabled, 's' * (enabled > 1))
                    if enabled else '; trainable whenever the base unit is')
        return oid, f'button slot {slot}, trained at: {where}{when}'

    def copy_enablers(self, base_key, new_key):
        """Many units (all of an upgrade line) are not trainable until an event
        - reaching an epoch, researching an upgrade - enables their tech. Give
        the new tech a copy of every effect that enables the base unit's tech,
        in the same events, so both become trainable at the same moment. (The
        effects that later disable the base unit are not copied: the new unit
        stays trainable.) Returns the number of effects added."""
        recs = self.effects.recs
        used = lambda i: i32(recs[i], EFFECT['index']) == i and i > 0
        def enablers(k):
            return [i for i in range(len(recs)) if used(i) and i32(recs[i], EFFECT['type']) == ENABLE_TECH
                    and i32(recs[i], EFFECT['key']) == k]
        if enablers(new_key):
            return 0
        added = 0
        for src in enablers(base_key):
            try:
                slot = next(i for i in range(1, len(recs)) if not used(i))
            except StopIteration:
                raise GameError('no free effect slot left in the game tables')
            new = bytearray(recs[src])
            set_i32(new, EFFECT['id'], max(i32(r, EFFECT['id']) for r in recs) + 1)
            set_i32(new, EFFECT['index'], slot)
            set_i32(new, EFFECT['key'], new_key)
            recs[slot] = new
            for _, effects in self.events.recs:
                if src in effects:
                    effects.append(slot)
            added += 1
        return added

    def created_base(self, oid):
        """(base unit, base tech key) of a unit made with new_unit, else None."""
        orig = self.ssa + '.orig'
        if not os.path.exists(orig):
            return None
        if getattr(self, '_orig_objs', None) is None:
            self._orig_objs = FixedTable(unpack_file(Archive(orig).read(DB['objs'])), OBJ_SIZE)
        objs = self._orig_objs
        if 0 < oid < len(objs.recs) and i32(objs.recs[oid], OBJ['id']) == oid:
            return None  # an original game unit
        base = self._guess_base(oid, objs)
        return base, i32(objs.recs[base], OBJ['tech_key'])

    def _enablers(self, key):
        recs = self.effects.recs
        return [i for i in range(1, len(recs)) if i32(recs[i], EFFECT['index']) == i
                and i32(recs[i], EFFECT['type']) == ENABLE_TECH and i32(recs[i], EFFECT['key']) == key]

    def is_available_now(self, oid):
        """Trainable as soon as its epoch is reached (no prerequisite tech and no
        event that has to enable it first)?"""
        t = self.tech_of(oid)
        return bool(t) and i32(t[2], TECH['requires']) == 0 and not self._enablers(i32(t[2], TECH['key']))

    def set_available_now(self, oid, flag):
        """For a created unit: trainable as soon as its epoch is reached (flag),
        or only together with its base unit (e.g. after the base's upgrade)."""
        created, t = self.created_base(oid), self.tech_of(oid)
        if not (created and t):
            raise GameError('only units created with this add-on can change when they become trainable')
        key = i32(t[2], TECH['key'])
        if flag:
            set_i32(t[2], TECH['requires'], 0)
            for i in self._enablers(key):  # free the effect slot and unhook it
                self.effects.recs[i] = bytearray(self.effects.recs[0])
                for _, effects in self.events.recs:
                    while i in effects:
                        effects.remove(i)
        else:
            bt = self.tech_of(created[0])
            set_i32(t[2], TECH['requires'], i32(bt[2], TECH['requires']) if bt else 0)
            self.copy_enablers(created[1], key)

    def _free_slot(self, buildings):
        used = set()  # unit buttons sit in slots 1-15 of a building's panel
        for r in buildings:
            for k in struct.unpack_from('<200i', r, OBJ['tech_list']):
                t = self.techs.find_key(k) if k > 0 else None
                if t:
                    used.add(i32(self.btns.recs[i32(t[2], TECH['button'])], BTN['slot']))
        free = [s for s in range(1, 16) if s not in used]
        if not free:
            raise GameError('no free button slot in that building')
        return free[0]

    def _own_graphics(self, oid):
        """Graphics record used only by this unit. A record no other unit uses is
        edited in place (keeps the unit as close to the original as possible);
        a shared one is cloned into a free slot first."""
        o = self.objs.recs[oid]
        gid = i32(o, OBJ['graphics'])
        users = sum(1 for i in self.unit_ids() if i32(self.objs.recs[i], OBJ['graphics']) == gid)
        if users == 1:
            return gid
        try:
            new = next(i for i, (s, _) in enumerate(self.gfx.recs) if i > 0 and not s[0])
        except StopIteration:
            raise GameError('no free graphics slot left')
        s, fixed = list(self.gfx.recs[gid][0]), bytearray(self.gfx.recs[gid][1])
        s[0] = name_of(o)[:MAX_GFX_NAME]
        set_name(fixed, name_of(o)[:MAX_GFX_NAME])
        set_i32(fixed, GFX_ID, new)
        self.gfx.recs[new] = [s, fixed]
        set_i32(o, OBJ['graphics'], new)
        return new

    def set_animations(self, oid, anims):
        gid = self._own_graphics(oid)
        fixed = self.gfx.recs[gid][1]
        for a, (first, last) in anims.items():
            set_i32(fixed, ANIM[a][0], first)
            set_i32(fixed, ANIM[a][1], last)
            if a == 'death':
                set_i32(fixed, CORPSE[0], last)
                set_i32(fixed, CORPSE[1], last)

    def set_model(self, oid, cem=None, sst=None):
        gid = self._own_graphics(oid)
        strs = self.gfx.recs[gid][0]
        if cem is not None:
            if cem[:4] != b'SSMF':
                raise GameError('not a .cem model')
            strs[1] = custom_model_path(gid)
            self.new_files[strs[1] + '.cem'] = pack_file(cem)
        if sst is not None:
            strs[2] = custom_texture_path(gid)
            self.new_files[strs[2]] = pack_file(sst)

    def _drop_unused_custom_files(self, blobs):
        """Remove custom models/textures no graphics record uses any more (e.g.
        after Reset Unit to Original)."""
        used = {strs[1].lower() + '.cem' for strs, _ in self.gfx.recs} | {strs[2].lower() for strs, _ in self.gfx.recs}
        for name in [n for n in blobs if CUSTOM_FILE.match(n) and n not in used]:
            del blobs[name]

    # ------------------------------------------------------------ files
    def backup(self):
        for p in (self.ssa, self.lang):
            if not os.path.exists(p + '.orig'):
                shutil.copy2(p, p + '.orig')

    def _sync_tech_counts(self):
        """The game reads only the first tech_count entries of a building's tech
        list, so keep that count equal to the number of entries (new units are
        appended to the list)."""
        for i, r in enumerate(self.objs.recs):
            if i and i32(r, OBJ['id']) == i:
                n = sum(1 for k in struct.unpack_from('<200i', r, OBJ['tech_list']) if k != -1)
                if i32(r, OBJ['tech_count']) != n:
                    set_i32(r, OBJ['tech_count'], n)

    def save(self):
        self.backup()
        self._sync_tech_counts()
        blobs = self.aoc.read_all()
        blobs.update(self.new_files)
        self._drop_unused_custom_files(blobs)
        for key, table in (('objs', self.objs), ('techs', self.techs), ('btns', self.btns), ('gfx', self.gfx),
                           ('effects', self.effects), ('events', self.events)):
            data = table.build()
            if data != unpack_file(blobs[DB[key]]):
                blobs[DB[key]] = pack_file(data)
        try:
            write_archive(self.ssa, self.aoc.version, self.aoc.zero, self.aoc.meta, blobs)
            if self.new_strings:
                write_lang_strings(self.lang, self.new_strings)
        except PermissionError:
            raise GameError('cannot write the game files - close Empire Earth first')
        self.load()

    def restore(self):
        restored = []
        for p in (self.ssa, self.lang):
            if os.path.exists(p + '.orig'):
                shutil.copy2(p + '.orig', p)
                restored.append(os.path.basename(p))
        self.load()
        return restored
