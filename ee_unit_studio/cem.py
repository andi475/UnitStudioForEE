# SPDX-License-Identifier: MIT
# Part of EE Unit Studio, an unofficial fan-made modding tool for Empire Earth.
"""
Empire Earth .cem models (SSMF version 2) and .sst textures: reading/writing
(pure Python) and Blender import/export.

CEM layout (all little-endian):
  header   'SSMF', u32 version=2, u32 triangles (LOD 0), u32 vertices,
           u32 tag points, u32 materials, u32 frames, u32 child models,
           u32 LODs, string name, vec3 center
  per LOD  u32 n, n * (u32 a, u32 b, u32 c); grouped by material, and each
           material's indices count from that material's first vertex
  per mat  string name, u32 flags, LODs * (u32 first tri, u32 tri count),
           u32 first vertex, u32 vertex count, string texture
  per tag  string name
  per frame
           f32 radius (max distance of a vertex from the header center)
           vertices * (vec3 position, vec3 normal, f32 u, f32 v)  [v = 0 is the
           top of the texture, DirectX style; Blender uses 1 - v]
           tags * vec3 position
           f32[16] matrix, vec3 bbox min, vec3 bbox max
  children complete CEM blocks, one after another
  The matrix is row-major (translation in elements 3, 7, 11) and places the
  model in its parent: identity for the main model; a sub-model's vertices are
  stored around its own pivot. Sub-models named orbital_cw_<speed>_<n> or
  orbital_ccw_<speed>_<n> spin around their local Z axis (propellers, rotors,
  radar dishes), turret_<n> turn to aim.
  string = u32 length incl. terminating 0, then the bytes
Z is up, the ground is z = 0. Animation is stored as one full vertex snapshot
per frame.
"""

import math
import os
import struct
from array import array

# ---------------------------------------------------------------- pure python

def _rstr(d, off):
    n, = struct.unpack_from('<I', d, off)
    return d[off + 4:off + 4 + n].split(b'\0')[0].decode('latin1'), off + 4 + n

def _wstr(s):
    b = s.encode('latin1', 'replace') + b'\0'
    return struct.pack('<I', len(b)) + b

class UnsupportedModel(ValueError):
    """A model in a format this add-on cannot read (11 of the game's models use
    version 0x30001 instead of 2)."""


def read_cem(d, off=0):
    """Returns (model dict, end offset)."""
    if d[off:off + 4] != b'SSMF':
        raise ValueError('not a CEM model')
    ver, ntri, nvert, ntag, nmat, nframe, nchild, nlod = struct.unpack_from('<8I', d, off + 4)
    if ver != 2:
        raise UnsupportedModel('this model uses a newer format (version %#x) that this add-on cannot read yet' % ver)
    name, p = _rstr(d, off + 36)
    center = struct.unpack_from('<3f', d, p)
    p += 12
    lods = []
    for _ in range(nlod):
        n, = struct.unpack_from('<I', d, p)
        flat = struct.unpack_from('<%dI' % (3 * n), d, p + 4)
        lods.append([flat[i:i + 3] for i in range(0, 3 * n, 3)])
        p += 4 + 12 * n
    mats = []
    for _ in range(nmat):
        mname, p = _rstr(d, p)
        flags, = struct.unpack_from('<I', d, p)
        p += 4
        flat = struct.unpack_from('<%dI' % (2 * nlod), d, p)
        p += 8 * nlod
        vs, vc = struct.unpack_from('<2I', d, p)
        p += 8
        tex, p = _rstr(d, p)
        mats.append(dict(name=mname, flags=flags, ranges=[flat[i:i + 2] for i in range(0, 2 * nlod, 2)],
                         first_vertex=vs, vertex_count=vc, texture=tex))
    tags = []
    for _ in range(ntag):
        t, p = _rstr(d, p)
        tags.append(t)
    frames = []
    for _ in range(nframe):
        radius, = struct.unpack_from('<f', d, p)
        p += 4
        v = struct.unpack_from('<%df' % (8 * nvert), d, p)
        p += 32 * nvert
        t = struct.unpack_from('<%df' % (3 * ntag), d, p)
        p += 12 * ntag
        m = struct.unpack_from('<16f', d, p)
        p += 64
        bb = struct.unpack_from('<6f', d, p)
        p += 24
        frames.append(dict(radius=radius, verts=v, tags=t, matrix=m, bbox=bb))
    children = []
    for _ in range(nchild):
        c, p = read_cem(d, p)
        children.append(c)
    return dict(name=name, center=center, lods=lods, materials=mats, tags=tags,
                frames=frames, children=children, nvert=nvert), p

def write_cem(model):
    """Exact inverse of read_cem (children included)."""
    nlod = len(model['lods'])
    out = bytearray(b'SSMF')
    out += struct.pack('<8I', 2, len(model['lods'][0]) if nlod else 0, model['nvert'], len(model['tags']),
                       len(model['materials']), len(model['frames']), len(model['children']), nlod)
    out += _wstr(model['name']) + struct.pack('<3f', *model['center'])
    for tris in model['lods']:
        out += struct.pack('<I', len(tris)) + struct.pack('<%dI' % (3 * len(tris)), *[i for t in tris for i in t])
    for m in model['materials']:
        out += _wstr(m['name']) + struct.pack('<I', m['flags'])
        out += b''.join(struct.pack('<2I', *r) for r in m['ranges'])
        out += struct.pack('<2I', m['first_vertex'], m['vertex_count']) + _wstr(m['texture'])
    for t in model['tags']:
        out += _wstr(t)
    for fr in model['frames']:
        out += struct.pack('<f', fr['radius']) + struct.pack('<%df' % len(fr['verts']), *fr['verts'])
        out += struct.pack('<%df' % len(fr['tags']), *fr['tags'])
        out += struct.pack('<16f', *fr['matrix']) + struct.pack('<6f', *fr['bbox'])
    for c in model['children']:
        out += write_cem(c)
    return bytes(out)

def with_frame_count(model, n):
    """Copy of a model whose animation has exactly n frames (last frame repeated
    or extra frames dropped), children included."""
    fr = model['frames']
    return dict(model, frames=[fr[min(k, len(fr) - 1)] for k in range(n)],
                children=[with_frame_count(c, n) for c in model['children']])

def attach_children(cem, children):
    """Append child models (bytes) to a CEM that was written without any."""
    out = bytearray(cem)
    struct.pack_into('<I', out, 28, len(children))
    return bytes(out) + b''.join(children)

def global_triangles(model, lod=0):
    """Triangles of one LOD with indices into the whole vertex list (in the file,
    each material's triangles count from that material's first vertex)."""
    tris = [tuple(t) for t in model['lods'][lod]] if model['lods'] else []
    for mat in model['materials']:
        if lod < len(mat['ranges']):
            first, count = mat['ranges'][lod]
            for t in range(first, min(first + count, len(tris))):
                tris[t] = tuple(i + mat['first_vertex'] for i in tris[t])
    return tris

IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)

def build_cem(tris_by_mat, mat_names, mat_textures, positions, normals, uvs, tag_names, tag_positions,
              vertex_ranges, lods=10, mat_flags=None, name='Scene Root', matrices=None):
    """
    tris_by_mat: list (per material) of triangle lists (vertex index triples)
    positions/normals: per frame, flat float lists (3 per vertex)
    uvs: flat list (2 per vertex), shared by every frame
    tag_positions: per frame, flat float list (3 per tag)
    vertex_ranges: per material (first vertex, count)
    matrices: per frame, 16 floats placing the model in its parent (sub-models)
    """
    nvert = len(uvs) // 2
    nframe = len(positions)
    tris = [t for ts in tris_by_mat for t in ts]
    xs, ys, zs = positions[0][0::3], positions[0][1::3], positions[0][2::3]
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    out = bytearray(b'SSMF')
    out += struct.pack('<8I', 2, len(tris), nvert, len(tag_names), len(tris_by_mat), nframe, 0, lods)
    out += _wstr(name) + struct.pack('<3f', *center)
    flat = [i for t in tris for i in t]
    for _ in range(lods):  # every LOD gets the full mesh
        out += struct.pack('<I', len(tris)) + struct.pack('<%dI' % len(flat), *flat)
    first = 0
    for m, ts in enumerate(tris_by_mat):
        out += _wstr(mat_names[m]) + struct.pack('<I', mat_flags[m] if mat_flags else 0)
        out += struct.pack('<2I', first, len(ts)) * lods
        out += struct.pack('<2I', *vertex_ranges[m]) + _wstr(mat_textures[m])
        first += len(ts)
    for t in tag_names:
        out += _wstr(t)
    for f in range(nframe):
        P, N = positions[f], normals[f]
        xs, ys, zs = P[0::3], P[1::3], P[2::3]
        radius = max(math.sqrt((P[i] - center[0]) ** 2 + (P[i + 1] - center[1]) ** 2 + (P[i + 2] - center[2]) ** 2)
                     for i in range(0, len(P), 3))
        out += struct.pack('<f', radius)
        v = array('f')
        for i in range(nvert):
            v.extend((P[3 * i], P[3 * i + 1], P[3 * i + 2], N[3 * i], N[3 * i + 1], N[3 * i + 2],
                      uvs[2 * i], uvs[2 * i + 1]))
        out += v.tobytes()
        out += struct.pack('<%df' % len(tag_positions[f]), *tag_positions[f])
        out += struct.pack('<16f', *(matrices[f] if matrices else IDENTITY))
        out += struct.pack('<6f', min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    return bytes(out)

def sst_images(d):
    """SST = 14-byte header (u8 0, u8 mip levels, u8 image count, 3 zero bytes,
    u32 width, u32 height - little-endian; a wrong size here freezes the whole
    computer when the texture is loaded), then for every image and mip level: u8 0 and a
    plain uncompressed TGA (largest mip first). A material's 'flags' value is
    the index of the image it uses. Returns the full-size TGA of every image."""
    if d[:2] == b'PK':
        raise ValueError('texture is still compressed')
    mips, count = max(1, d[1]), max(1, d[2])
    off, images = 14, []
    for _ in range(count):
        for m in range(mips):
            if off + 19 > len(d):
                return images
            off += 1
            idlen, w, h, bpp = d[off], *struct.unpack_from('<HH', d, off + 12), d[off + 16]
            size = 18 + idlen + w * h * (bpp // 8)
            if m == 0:
                images.append(d[off:off + size])
            off += size
    return images

def sst_to_tga(d):
    return sst_images(d)[0]

def images_to_sst(width, height, rgba_images):
    """rgba bytes per image (all width x height, rows bottom-up like Blender) -> SST."""
    out = bytearray(sst_header(width, height, len(rgba_images)))
    for rgba in rgba_images:
        px = bytearray(rgba)
        px[0::4], px[2::4] = rgba[2::4], rgba[0::4]  # RGBA -> BGRA
        out += b'\0' + struct.pack('<BBB5sHHHHBB', 0, 0, 2, b'\0' * 5, 0, 0, width, height, 32, 8) + px
    return bytes(out)

def sst_header(width, height, images=1, mips=1):
    return bytes([0, mips, images, 0, 0, 0]) + struct.pack('<II', width, height)

def tga_to_sst(tga):
    """Plain uncompressed TGA -> single-image SST."""
    w, h = struct.unpack_from('<HH', tga, 12)
    return sst_header(w, h) + b'\0' + tga

def rgba_to_sst(width, height, rgba_bottom_up):
    return images_to_sst(width, height, [rgba_bottom_up])

ICON_SIZE = 64  # every button icon of the game: 64x64, 24 bit, one image

def icon_sst(rgb_bottom_up):
    """ICON_SIZE x ICON_SIZE RGB bytes (rows bottom-up) -> .sst laid out exactly
    like the game's own button icons."""
    px = bytearray(rgb_bottom_up)
    px[0::3], px[2::3] = rgb_bottom_up[2::3], rgb_bottom_up[0::3]  # RGB -> BGR
    return (sst_header(ICON_SIZE, ICON_SIZE) + b'\0' +
            struct.pack('<BBB5sHHHHBB', 0, 0, 2, b'\0' * 5, 0, 0, ICON_SIZE, ICON_SIZE, 24, 0) + px)

# ---------------------------------------------------------------- blender
try:
    import bpy
    from mathutils import Matrix, Vector
except ImportError:  # used outside Blender (tests, command line)
    bpy = None

if bpy:
    PLAYER_COLOR = 'player color'
    IMPORT_TAG = 'ee_unit_studio_import'  # custom property on everything an import creates

    def _find_texture(cem_path):
        folder, base = os.path.split(os.path.splitext(cem_path)[0])
        for d in (folder, os.path.join(folder, '..', 'textures')):
            for n in (base + 't.sst', base + 'T.sst', base + '.sst', base + 't.tga', base + '.tga'):
                p = os.path.join(d, n)
                if os.path.exists(p):
                    return p
        return None

    def _load_images(data, name):
        """Texture file contents (.sst or .tga) -> list of packed Blender images."""
        folder = bpy.app.tempdir or os.path.join(os.path.expanduser('~'), 'ee_unit_studio')
        os.makedirs(folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(name))[0]
        tgas = [data] if name.lower().endswith('.tga') else sst_images(data)
        images = []
        for k, tga in enumerate(tgas):
            tmp = os.path.join(folder, '%s_%d.tga' % (stem, k))
            open(tmp, 'wb').write(tga)
            img = bpy.data.images.load(tmp)
            img.pack()
            img.name = stem if len(tgas) == 1 else '%s_%d' % (stem, k)
            img[IMPORT_TAG] = True
            images.append(img)
        return images

    def _material(name, image):
        mat = bpy.data.materials.new(name or 'ee_material')
        mat[IMPORT_TAG] = True
        mat.use_nodes = True
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf:
            bsdf.inputs['Roughness'].default_value = 1.0  # the game has no shine
        if image and bsdf:
            tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
            tex.image = image
            tex.location = (-400, 200)
            mat.node_tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
            # the game cuts parts out of the texture by its alpha channel (leaves,
            # hair, roof edges, whole building parts on a transparent sheet)
            mat.node_tree.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
            for attr, value in (('surface_render_method', 'DITHERED'), ('blend_method', 'CLIP')):
                try:
                    setattr(mat, attr, value)
                except (AttributeError, TypeError, ValueError):
                    pass
        if name == PLAYER_COLOR and bsdf and not image:
            bsdf.inputs['Base Color'].default_value = (0.1, 0.2, 1.0, 1.0)
        return mat

    def _build_object(context, model, name, images):
        f0 = model['frames'][0]['verts']
        nvert = len(f0) // 8
        tris = global_triangles(model)
        mesh = bpy.data.meshes.new(name)
        mesh.vertices.add(nvert)
        mesh.vertices.foreach_set('co', [c for i in range(nvert) for c in f0[8 * i:8 * i + 3]])
        mesh.loops.add(3 * len(tris))
        mesh.polygons.add(len(tris))
        mesh.loops.foreach_set('vertex_index', [i for t in tris for i in t])
        mesh.polygons.foreach_set('loop_start', list(range(0, 3 * len(tris), 3)))
        mesh.polygons.foreach_set('loop_total', [3] * len(tris))
        uv = mesh.uv_layers.new(name='UVMap')
        # the game's v runs from the top of the image down, Blender's from the bottom up
        uv.data.foreach_set('uv', [c for t in tris for i in t for c in (f0[8 * i + 6], 1.0 - f0[8 * i + 7])])
        mat_index = [0] * len(tris)
        for m, mat in enumerate(model['materials']):
            image = images[min(mat['flags'], len(images) - 1)] if images else None
            mname = PLAYER_COLOR if mat['name'] == PLAYER_COLOR else (mat['name'] or mat['texture'] or name)
            mesh.materials.append(_material(mname, image))
            if mat['ranges']:
                first, count = mat['ranges'][0]
                for t in range(first, min(first + count, len(tris))):
                    mat_index[t] = m
        mesh.polygons.foreach_set('material_index', mat_index)
        mesh.polygons.foreach_set('use_smooth', [True] * len(tris))
        mesh.update()
        mesh.validate()
        try:
            mesh.normals_split_custom_set_from_vertices(
                [tuple(f0[8 * i + 3:8 * i + 6]) for i in range(nvert)])
        except Exception:
            pass
        obj = bpy.data.objects.new(name, mesh)
        mesh[IMPORT_TAG] = obj[IMPORT_TAG] = True
        context.collection.objects.link(obj)

        frames = model['frames']
        if len(frames) > 1:
            obj.shape_key_add(name='Basis', from_mix=False)
            for k, fr in enumerate(frames):
                kb = obj.shape_key_add(name='frame_%03d' % k, from_mix=False)
                v = fr['verts']
                kb.data.foreach_set('co', [c for i in range(nvert) for c in v[8 * i:8 * i + 3]])
                for f, val in ((k - 1, 0.0), (k, 1.0), (k + 1, 0.0)):
                    if 0 <= f < len(frames):
                        kb.value = val
                        kb.keyframe_insert('value', frame=f)
                kb.value = 0.0
        for t, tname in enumerate(model['tags']):
            e = bpy.data.objects.new(tname, None)
            e[IMPORT_TAG] = True
            e.empty_display_type = 'SPHERE'
            e.empty_display_size = 0.04
            e.parent = obj
            context.collection.objects.link(e)
            for k, fr in enumerate(frames):
                e.location = fr['tags'][3 * t:3 * t + 3]
                if len(frames) > 1:
                    e.keyframe_insert('location', frame=k)
            if e.animation_data and e.animation_data.action:
                e.animation_data.action[IMPORT_TAG] = True
        keys = mesh.shape_keys
        if keys and keys.animation_data and keys.animation_data.action:
            keys.animation_data.action[IMPORT_TAG] = True
        return obj

    def image_to_icon_sst(image, background=(0.0, 0.0, 0.0)):
        """A Blender image -> button icon .sst (scaled to 64x64, transparent
        parts filled with the background color)."""
        img = image.copy()
        try:
            if tuple(img.size) != (ICON_SIZE, ICON_SIZE):
                img.scale(ICON_SIZE, ICON_SIZE)
            px = array('f', [0.0]) * (ICON_SIZE * ICON_SIZE * 4)
            img.pixels.foreach_get(px)
        finally:
            bpy.data.images.remove(img)
        rgb = bytearray(ICON_SIZE * ICON_SIZE * 3)
        for i in range(ICON_SIZE * ICON_SIZE):
            a = px[4 * i + 3]
            for c in range(3):
                v = px[4 * i + c] * a + background[c] * (1.0 - a)
                rgb[3 * i + c] = max(0, min(255, int(round(v * 255))))
        return icon_sst(bytes(rgb))

    RENDERED_ICON = 'EE icon (rendered)'

    def render_icon(context, obj, background=(0.15, 0.13, 0.1), zoom=1.0, orbit=math.radians(29), elevation=math.radians(24)):
        """Render obj (and the meshes parented to it) as a square picture for a
        button icon: orbit = angle around it (0 = straight from the front, the
        side units face, +Y; positive = from the right), zoom > 1 = closer.
        Uses a temporary scene, so the user's scene is not touched. Returns the image."""
        import tempfile
        objs = [obj] + [c for c in obj.children_recursive if c.type == 'MESH']
        deps = context.evaluated_depsgraph_get()
        pts = []
        for o in objs:
            ev = o.evaluated_get(deps)
            mesh = ev.to_mesh()
            pts += [ev.matrix_world @ v.co for v in mesh.vertices]
            ev.to_mesh_clear()
        if not pts:
            raise RuntimeError('"%s" has no vertices' % obj.name)
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        center, size = (lo + hi) / 2, max(hi - lo) or 1.0
        path = os.path.join(tempfile.gettempdir(), 'ee_unit_studio_icon.png')
        scene = bpy.data.scenes.new('EE icon render')
        cam_data = bpy.data.cameras.new('EE icon camera')
        cam = bpy.data.objects.new('EE icon camera', cam_data)
        world = bpy.data.worlds.new('EE icon world')
        try:
            for o in objs:
                scene.collection.objects.link(o)
            scene.collection.objects.link(cam)
            cam_data.type, cam_data.ortho_scale = 'ORTHO', size * 1.15 / max(zoom, 0.01)
            cam_data.clip_end = size * 20
            direction = Vector((math.sin(orbit) * math.cos(elevation), math.cos(orbit) * math.cos(elevation),
                                math.sin(elevation)))
            cam.location = center + direction * size * 4
            cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
            scene.camera = cam
            world.color = background
            scene.world = world
            scene.frame_current = context.scene.frame_current
            r = scene.render
            r.engine = 'BLENDER_WORKBENCH'
            r.resolution_x = r.resolution_y = ICON_SIZE * 4  # rendered larger, scaled down when written
            r.resolution_percentage = 100
            r.film_transparent = False
            r.image_settings.file_format, r.image_settings.color_mode = 'PNG', 'RGB'
            r.filepath = path
            shading = scene.display.shading
            shading.light, shading.color_type = 'STUDIO', 'TEXTURE'
            bpy.ops.render.render(write_still=True, scene=scene.name)
        finally:
            bpy.data.scenes.remove(scene)
            bpy.data.objects.remove(cam)
            bpy.data.cameras.remove(cam_data)
            bpy.data.worlds.remove(world)
        new = bpy.data.images.load(path, check_existing=False)
        old = bpy.data.images.get(RENDERED_ICON)
        if old and tuple(old.size) == tuple(new.size):  # update in place: the panel preview follows at once
            px = array('f', [0.0]) * len(new.pixels)
            new.pixels.foreach_get(px)
            old.pixels.foreach_set(px)
            old.pack()
            bpy.data.images.remove(new)
            if old.preview:
                old.preview.reload()
            return old
        if old:
            bpy.data.images.remove(old)
        new.pack()
        new.name = RENDERED_ICON
        return new

    def show_icon(sst, name):
        """An icon .sst as a Blender image (replacing an older one of that name), for the preview."""
        old = bpy.data.images.get(name)
        if old:
            bpy.data.images.remove(old)
        if not sst:
            return None
        img = _load_images(sst, name + '.sst')[0]
        img.name = name
        del img[IMPORT_TAG]  # not part of an imported model: Clear Previous Model keeps it
        return img

    def clear_imported():
        """Delete everything an earlier import created (objects, meshes,
        materials, textures, animations) - and nothing else."""
        removed = 0
        for obj in [o for o in bpy.data.objects if o.get(IMPORT_TAG)]:
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
        for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.actions):
            for block in [b for b in coll if b.get(IMPORT_TAG) and b.users == 0]:
                coll.remove(block)
        return removed

    def import_model(context, cem, name, texture=None, texture_name=''):
        """cem / texture: file contents (texture .sst or .tga). Returns the root object."""
        model, _ = read_cem(cem)
        images = _load_images(texture, texture_name or (name + 't.sst')) if texture else []
        root = _build_object(context, model, name, images)
        for i, child in enumerate(model['children']):
            # keep the game's name: orbital_cw_200_00 spins, turret_1 aims...
            cname = child['name'] if child['name'] not in ('', 'Scene Root') else '%s_part%d' % (name, i + 1)
            obj = _build_object(context, child, cname, images)
            obj.parent = root
            obj['ee_sub_model'] = i  # export keeps the original order
            _place(obj, [fr['matrix'] for fr in child['frames']])
        context.scene.frame_start, context.scene.frame_end = 0, len(model['frames']) - 1
        context.scene.frame_set(0)
        return root, model

    def _place(obj, matrices):
        """Put a sub-model where its matrix says (animated if it changes)."""
        mats = [Matrix([m[0:4], m[4:8], m[8:12], m[12:16]]) for m in matrices]
        obj.matrix_basis = mats[0]
        if any(m != mats[0] for m in mats[1:]):
            for k, m in enumerate(mats):
                obj.matrix_basis = m
                for path in ('location', 'rotation_euler', 'scale'):
                    obj.keyframe_insert(path, frame=k)
            obj.animation_data.action[IMPORT_TAG] = True
            obj.matrix_basis = mats[0]

    def import_file(context, path, texture_path=''):
        tex = texture_path or _find_texture(path)
        name = os.path.splitext(os.path.basename(path))[0]
        return import_model(context, open(path, 'rb').read(), name,
                            open(tex, 'rb').read() if tex else None, os.path.basename(tex) if tex else '')

    def _corner_normals(mesh):
        if hasattr(mesh, 'corner_normals'):  # Blender 4.1+
            cn = array('f', [0.0]) * (3 * len(mesh.loops))
            mesh.corner_normals.foreach_get('vector', cn)
            return cn
        mesh.calc_normals_split()
        cn = array('f', [0.0]) * (3 * len(mesh.loops))
        mesh.loops.foreach_get('normal', cn)
        return cn

    INFANTRY_TAGS = ['attack', 'impact_00', 'impact_01', 'water_ripple_00']

    def _image_of(mat):
        if mat and mat.use_nodes:
            return next((n.image for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image), None)
        return None

    def _tag_empties(obj):
        """Empties below obj that belong to it (not to one of its child meshes)."""
        out = []
        for e in obj.children_recursive:
            if e.type != 'EMPTY':
                continue
            p = e.parent
            while p is not obj and p.type != 'MESH':
                p = p.parent
            if p is obj:
                out.append(e)
        return sorted(out, key=lambda e: e.name)

    class _Part:
        """One exported model (the main mesh or a child mesh), sampled per frame."""

        def __init__(self, obj, images, sub_model=False):
            self.obj, self.tags = obj, _tag_empties(obj)
            # a sub-model keeps its vertices around its own origin (the pivot it
            # spins or turns around) and stores its placement as a matrix
            self.sub_model, self.matrices = sub_model, []
            self.slot_group = []
            for s in obj.material_slots:
                img = _image_of(s.material)
                self.slot_group.append((images.index(img) if img else 0,
                                        bool(s.material and PLAYER_COLOR in s.material.name.lower())))
            self.group_keys = sorted(set(self.slot_group) or {(0, False)})
            self.group_of_slot = [self.group_keys.index(k) for k in self.slot_group] or [0]
            self.layout = None
            self.positions, self.normals, self.tag_positions = [], [], []

        def sample(self, depsgraph, frame, scale):
            ev = self.obj.evaluated_get(depsgraph)
            mesh = ev.to_mesh()
            try:
                mw = ev.matrix_world
                if self.sub_model:
                    m = [c for row in mw for c in row]
                    m[3], m[7], m[11] = m[3] * scale, m[7] * scale, m[11] * scale
                    self.matrices.append(m)
                    to_local = mw.inverted_safe()
                    mw = Matrix.Identity(4)
                nm = mw.to_3x3().inverted_safe().transposed()
                mesh.calc_loop_triangles()
                cn = _corner_normals(mesh)
                if self.layout is None:
                    self._build_layout(mesh, cn)
                elif len(mesh.loops) != self.loop_count:
                    raise RuntimeError('"%s" has a different topology at frame %d; apply modifiers that add or '
                                       'remove geometry (decimate, remesh...) first' % (self.obj.name, frame))
                P, N = array('f'), array('f')
                for v, l in self.layout:
                    P.extend((mw @ mesh.vertices[v].co) * scale)
                    N.extend((nm @ Vector(cn[3 * l:3 * l + 3])).normalized())
                self.positions.append(P)
                self.normals.append(N)
                T = array('f')
                for t in self.tags:
                    T.extend((to_local @ t.matrix_world.translation if self.sub_model
                              else t.matrix_world.translation) * scale)
                self.tag_positions.append(T)
            finally:
                ev.to_mesh_clear()

        def _build_layout(self, mesh, cn):
            if not mesh.uv_layers.active:
                raise RuntimeError('"%s" has no UV map' % self.obj.name)
            uvdata = mesh.uv_layers.active.data
            ng = len(self.group_keys)
            groups, verts, uvs, keys = [[] for _ in range(ng)], [[] for _ in range(ng)], [[] for _ in range(ng)], {}
            for lt in mesh.loop_triangles:
                g = self.group_of_slot[min(lt.material_index, len(self.group_of_slot) - 1)]
                tri = []
                for l, v in zip(lt.loops, lt.vertices):
                    uv = uvdata[l].uv
                    key = (g, v, round(uv[0], 5), round(uv[1], 5),
                           round(cn[3 * l], 3), round(cn[3 * l + 1], 3), round(cn[3 * l + 2], 3))
                    if key not in keys:
                        keys[key] = len(verts[g])
                        verts[g].append((v, l))
                        uvs[g].extend((uv[0], 1.0 - uv[1]))  # game v runs top-down
                    tri.append(keys[key])
                groups[g].append(tuple(tri))
            if not any(groups):
                raise RuntimeError('"%s" has no faces' % self.obj.name)
            # vertices grouped by material; triangle indices stay relative to
            # their material's first vertex. Empty groups are dropped.
            used = [g for g in range(ng) if groups[g]]
            self.layout = [v for g in used for v in verts[g]]
            self.uv_flat = [c for g in used for c in uvs[g]]
            self.tris_by_mat = [groups[g] for g in used]
            self.ranges, first = [], 0
            for g in used:
                self.ranges.append((first, len(verts[g])))
                first += len(verts[g])
            self.mat_keys = [self.group_keys[g] for g in used]
            self.loop_count = len(mesh.loops)

        def tag_list(self, reference):
            """(names, per-frame positions). Tag points of the reference model keep
            their order and are taken from the reference when there is no empty
            of that name; extra empties are appended."""
            own = [(t.name.split('.')[0], i) for i, t in enumerate(self.tags)]
            frames = len(self.positions)
            if reference is None:
                if own:
                    return [n for n, _ in own], self.tag_positions
                return None, None
            names, sources, left = [], [], list(own)
            for k, name in enumerate(reference['tags']):
                hit = next((x for x in left if x[0] == name), None)
                if hit:
                    left.remove(hit)
                names.append(name)
                sources.append(('own', hit[1]) if hit else ('ref', k))
            for name, i in left:
                names.append(name)
                sources.append(('own', i))
            ref_frames = reference['frames']
            out = []
            for f in range(frames):
                T = array('f')
                for kind, i in sources:
                    if kind == 'own':
                        T.extend(self.tag_positions[f][3 * i:3 * i + 3])
                    else:
                        T.extend(ref_frames[min(f, len(ref_frames) - 1)]['tags'][3 * i:3 * i + 3])
                out.append(T)
            return names, out

        def build(self, images, reference):
            names, tag_pos = self.tag_list(reference)
            auto = names is None
            if auto:  # same tag points as the game's infantry, placed from the bounding box
                names, tag_pos = list(INFANTRY_TAGS), []
                for P in self.positions:
                    xs, ys, zs = P[0::3], P[1::3], P[2::3]
                    cx, cy, top = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(zs)
                    tag_pos.append(array('f', (cx, cy, top * 0.75, cx, cy, top * 0.9, cx, cy, top * 0.6, cx, cy, 0.0)))
            tex_names = [os.path.splitext(i.name)[0] for i in images] or ['texture']
            data = build_cem(self.tris_by_mat, [PLAYER_COLOR if pc else '' for _, pc in self.mat_keys],
                             [tex_names[min(i, len(tex_names) - 1)] for i, _ in self.mat_keys],
                             self.positions, self.normals, self.uv_flat, names, tag_pos, self.ranges,
                             mat_flags=[i for i, _ in self.mat_keys],
                             # same number of detail levels as the model it replaces (some have 1)
                             lods=min(10, max(1, len(reference['lods']))) if reference else 10,
                             name=self.obj.name.split('.')[0] if self.sub_model else 'Scene Root',
                             matrices=self.matrices if self.sub_model else None)
            from_ref = [n for n in (reference or {}).get('tags', []) if n not in {t.name.split('.')[0] for t in self.tags}]
            return data, names, auto, from_ref

    def export_model(context, obj, scale=1.0, with_texture=True, reference=None):
        """Export obj (and the meshes parented directly to it, as sub-models).
        reference: bytes of the game model this replaces; its tag points and
        sub-models are kept where the Blender scene has none.
        Returns (cem bytes, sst bytes or None, message)."""
        scene = context.scene
        frames = list(range(scene.frame_start, scene.frame_end + 1))
        try:
            ref, unreadable_ref = (read_cem(reference)[0] if reference else None), False
        except UnsupportedModel:  # the original's tag points and sub-models cannot be copied
            ref, unreadable_ref = None, True
        child_objs = sorted((c for c in obj.children if c.type == 'MESH'),
                            key=lambda c: (c.get('ee_sub_model', 1 << 30), c.name))

        images = []
        for o in [obj] + child_objs:
            for s in o.material_slots:
                img = _image_of(s.material)
                if img and img not in images:
                    images.append(img)
        parts = [_Part(obj, images)] + [_Part(o, images, sub_model=True) for o in child_objs]

        depsgraph = context.evaluated_depsgraph_get()
        current = scene.frame_current
        try:
            for f in frames:
                scene.frame_set(f)
                for p in parts:
                    p.sample(depsgraph, f, scale)
        finally:
            scene.frame_set(current)

        data, tag_names, auto, from_ref = parts[0].build(images, ref)
        msg = '%d vertices, %d triangles, %d frames, tags: %s' % (
            len(parts[0].layout), sum(map(len, parts[0].tris_by_mat)), len(frames), ', '.join(tag_names))
        if auto:
            msg += ' (placed automatically; add empties as children of the mesh to control them)'
        if from_ref:
            msg += ' (taken from the original model: %s)' % ', '.join(from_ref)
        if unreadable_ref:
            msg += ' (the original model uses a format that cannot be read, so its tag points could not be copied)'
        ref_children = ref['children'] if ref else []
        if len(parts) > 1:
            kids = [p.build(images, ref_children[i] if i < len(ref_children) else None)[0]
                    for i, p in enumerate(parts[1:])]
            msg += '; %d sub-models' % len(kids)
        elif ref_children:  # keep the original sub-models (turrets, propellers...)
            kids = [write_cem(with_frame_count(c, len(frames))) for c in ref_children]
            msg += '; kept the original %d sub-models' % len(kids)
        else:
            kids = []
        if kids:
            data = attach_children(data, kids)

        sst = None
        if with_texture:
            if not images:
                msg += '; no image texture found on the materials, texture not exported'
            else:
                # one size for all images (that of the first, rounded to a power of two)
                p2 = lambda x: 1 << max(0, int(round(math.log2(max(1, x)))))
                w, h = p2(images[0].size[0]), p2(images[0].size[1])
                rgbas = []
                for image in images:
                    img = image
                    if tuple(image.size) != (w, h):
                        img = image.copy()
                        img.scale(w, h)
                    px = array('f', [0.0]) * (4 * w * h)
                    img.pixels.foreach_get(px)
                    if img is not image:
                        bpy.data.images.remove(img)
                    rgbas.append(bytes(max(0, min(255, int(c * 255 + 0.5))) for c in px))
                sst = images_to_sst(w, h, rgbas)
                msg += '; texture %dx%d' % (w, h) + (' (%d images)' % len(images) if len(images) > 1 else '')
        return data, sst, msg
