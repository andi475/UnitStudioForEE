# SPDX-License-Identifier: MIT
# Part of EE Unit Studio, an unofficial fan-made modding tool for Empire Earth.
"""
EE Unit Studio - edit Empire Earth: The Art of Conquest units from Blender.

3D Viewport > Sidebar (N) > "Empire Earth": pick the game folder, load the
unit list, import a unit's model, change its name/stats/cost/animation frames,
replace its model with your own mesh, or create a brand-new unit, and write
everything straight into the game. The first write backs up data.ssa and
Language.dll (*.orig); "Restore Original Game Files" puts them back.
"""

bl_info = {
    "name": "EE Unit Studio (Empire Earth)",
    "author": "UnitMod",
    "version": (1, 1, 3),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Empire Earth, File > Import-Export",
    "description": "Unofficial fan-made tool: edit Empire Earth units, models and textures in the game files",
    "category": "Import-Export",
}

import os
import time

import bpy
from bpy.props import (BoolProperty, CollectionProperty, FloatProperty, IntProperty, IntVectorProperty,
                       PointerProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import cem, gamedata, nodes

_session = None  # gamedata.GameSession of the loaded game folder
HAS_PLACEHOLDER = 'placeholder' in bpy.types.UILayout.bl_rna.functions['prop'].parameters.keys()


def prefs(context):
    return context.preferences.addons[__package__].preferences


def state(context):
    return context.scene.ee_unit_studio


def session(context):
    p = prefs(context)
    if _session is None or os.path.normpath(_session.game_dir) != os.path.normpath(bpy.path.abspath(p.game_dir)):
        return None
    return _session


# ---------------------------------------------------------------- properties
class EEUS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    game_dir: StringProperty(name='Game Folder', subtype='DIR_PATH',
                             description='The "Empire Earth - The Art of Conquest" folder (contains Language.dll)')
    base_dir: StringProperty(name='Base Game Folder', subtype='DIR_PATH',
                             description='The original "Empire Earth" folder; left empty, the folder next to the '
                                         'game folder is used (most original models are stored there)')

    def draw(self, context):
        self.layout.prop(self, 'game_dir')
        self.layout.prop(self, 'base_dir')


class EEUS_UnitItem(bpy.types.PropertyGroup):
    # 'name' (from PropertyGroup) holds "display name (internal name)" for the list search
    oid: IntProperty()
    look: IntProperty()  # graphics record of an epoch look; 0 = the unit's normal look
    look_name: StringProperty()
    label: StringProperty()
    internal: StringProperty()
    trainable: BoolProperty()
    hp: IntProperty()
    modified: StringProperty()  # the unit's state: '', 'changed' or 'created'
    look_changed: BoolProperty()  # this epoch look's model/texture/animations differ


ANIMS = list(gamedata.ANIM)


def _on_select(self, context):
    fill_edit_fields(context)


class EEUS_State(bpy.types.PropertyGroup):
    units: CollectionProperty(type=EEUS_UnitItem)
    index: IntProperty(default=-1, update=_on_select)
    search: StringProperty(name='Search', options={'TEXTEDIT_UPDATE'},
                           description='Show units whose name contains all of these words '
                                       '(game name, internal name or #number)')
    only_trainable: BoolProperty(name='Trainable only', default=True,
                                 description='Only list units that a building can train')
    only_modified: BoolProperty(name='Modified only', default=False,
                                description='Only list units you changed or created')
    # fields of the selected unit
    name: StringProperty(name='Name', description='Name shown in the game')
    hp: IntProperty(name='Hit Points', min=1)
    attack: IntProperty(name='Attack', min=0)
    range: FloatProperty(name='Range', min=0.0, description='0.5 for melee units', precision=2)
    line_of_sight: FloatProperty(name='Line of Sight', min=0.0, precision=1,
                                 description='How far the unit sees (Clubman: 4)')
    reload_time: FloatProperty(name='Reload Time', min=0.0, precision=2, unit='TIME_ABSOLUTE',
                               description='Seconds between two attacks (Clubman: 2, Catapult: 8)')
    speed: FloatProperty(name='Speed', min=0.0, precision=1,
                         description='As the game shows it (Clubman: 12, Horseman: 16)')
    armor_shock: IntProperty(name='Shock', min=0)
    armor_arrow: IntProperty(name='Arrow', min=0)
    armor_pierce: IntProperty(name='Pierce', min=0)
    armor_gun: IntProperty(name='Gun', min=0)
    armor_laser: IntProperty(name='Laser', min=0)
    armor_missile: IntProperty(name='Missile', min=0)
    show_armor: BoolProperty(name='Armor', default=False, description='Show the armor values')
    show_stats: BoolProperty(name='Stats', default=False, description='Show hit points, attack, range and so on')
    show_cost: BoolProperty(name='Cost', default=False, description='Show the cost and build time')
    food: IntProperty(name='Food', min=0)
    wood: IntProperty(name='Wood', min=0)
    stone: IntProperty(name='Stone', min=0)
    gold: IntProperty(name='Gold', min=0)
    iron: IntProperty(name='Iron', min=0)
    build_time: FloatProperty(name='Build Time', min=0.0, unit='TIME_ABSOLUTE', description='Seconds')
    idle: IntVectorProperty(name='Idle', size=2, min=0, description='First and last frame')
    walk: IntVectorProperty(name='Walk', size=2, min=0, description='First and last frame')
    attack_anim: IntVectorProperty(name='Attack', size=2, min=0, description='First and last frame')
    attack2: IntVectorProperty(name='Attack 2', size=2, min=0, description='First and last frame')
    death: IntVectorProperty(name='Death', size=2, min=0, description='First and last frame')
    show_anims: BoolProperty(name='Animation Frames', default=False,
                             description='Show the first and last frame of each animation')
    clear_previous: BoolProperty(name='Clear Previous Model', default=True,
                                 description='Before importing, delete the models imported earlier (only objects '
                                             'created by an import and copies of them; your own objects stay)')
    use_mesh: BoolProperty(name='Use Active Mesh as Model', default=True,
                           description='Export the active mesh (scene frame range = animation frames) and its '
                                       'texture as the unit\'s model')
    scale: FloatProperty(name='Model Scale', default=1.0, min=0.0001,
                         description='Multiplies the exported coordinates (infantry is about 1.0 tall)')
    new_name: StringProperty(name='New Unit Name', default='New Unit')
    is_created: BoolProperty()
    available_now: BoolProperty(name='Available in Its Epoch', default=True,
                                description='Trainable as soon as its epoch is reached. Off: only together with '
                                            'the unit it was copied from (e.g. after the upgrade a battleship needs)')
    new_available_now: BoolProperty(name='Available in Its Epoch', default=True,
                                    description='Trainable as soon as its epoch is reached. Off: only together with '
                                                'the selected unit (e.g. after the upgrade a battleship needs)')
    new_epoch: IntProperty(name='Epoch', default=0, min=0, max=15,
                           description='1 = Prehistoric ... 15 = Space; 0 = same as the selected unit')
    info: StringProperty()


ANIM_PROPS = dict(idle='idle', walk='walk', attack='attack_anim', attack2='attack2', death='death')


def selected_oid(context):
    st = state(context)
    if 0 <= st.index < len(st.units):
        return st.units[st.index].oid
    return None


def selected_look(context):
    """Graphics record of the selected epoch look, or None for a unit's normal look."""
    st = state(context)
    if 0 <= st.index < len(st.units):
        return st.units[st.index].look or None
    return None


def selected_look_name(context):
    st = state(context)
    return st.units[st.index].look_name if 0 <= st.index < len(st.units) else ''


def fill_list(context, select_oid=None, select_look_name=''):
    """One row per unit, followed by one row per epoch look of that unit."""
    st, s = state(context), session(context)
    st.units.clear()
    if not s:
        return
    modified, changed_gfx = s.modified()
    for oid in s.unit_ids():
        u = s.unit(oid)
        if 'model' not in u and not u['trainable']:
            continue  # technical objects without a model
        rows = [(0, '')] + s.looks(oid)
        for look, look_name in rows:
            item = st.units.add()
            item.oid, item.internal, item.trainable, item.hp = oid, u['internal'], u['trainable'], u['hp']
            item.look, item.look_name = look, look_name
            item.modified, item.look_changed = modified.get(oid, ''), look in changed_gfx
            name = u['name'] or u['internal']
            item.label = '%s – %s' % (name, look_name) if look else name
            item.name = '%s (%s)' % (item.label, u['internal'])
    st.index = next((i for i, it in enumerate(st.units)
                     if it.oid == select_oid and it.look_name == select_look_name), -1)


def fill_edit_fields(context):
    st, s, oid = state(context), session(context), selected_oid(context)
    if not s or oid is None:
        return
    gid = selected_look(context)
    u = s.unit(oid, gid)
    st.name, st.hp, st.attack, st.range = u['name'] or u['internal'], u['hp'], u['attack'], u['range']
    st.line_of_sight, st.reload_time, st.speed = u['line_of_sight'], u['reload_time'], u['speed']
    for a in gamedata.ARMORS:
        setattr(st, 'armor_' + a, u['armor_' + a])
    for c in gamedata.COSTS:
        setattr(st, c, u.get(c, 0))
    st.build_time = u.get('build_time', 0.0)
    for a, prop in ANIM_PROPS.items():
        setattr(st, prop, u['anims'][a] if 'anims' in u else (0, 0))
    st.is_created = s.created_base(oid) is not None
    st.available_now = s.is_available_now(oid)
    parts = ['#%d %s' % (oid, u['internal'])]
    if u['trainable']:
        parts.append('epoch %d (%s)' % (u['epoch'], gamedata.EPOCHS[u['epoch'] - 1]))
    if gid:
        parts.append('look: %s' % selected_look_name(context))
    st.info = ', '.join(parts)


def check_anim_frames(anims, frame_count):
    """The game reads past the end of the model for animation frames it does not
    have (that can freeze the computer), so refuse such a combination."""
    bad = ['%s %d-%d' % (a, f, l) for a, (f, l) in anims.items() if max(f, l) >= frame_count]
    if bad:
        raise gamedata.GameError(
            'the model has %d frame(s) (0-%d) but these animations use frames beyond that: %s. Extend the scene '
            'frame range / animation, or lower the animation frame numbers.'
            % (frame_count, frame_count - 1, ', '.join(bad)))


def apply_fields(context, s, oid, base_anims=None, base_oid=None, gid=None, reference=None):
    """Write the panel fields into the session for unit oid. Stats belong to
    the unit; model and animation frames to its look gid (default: normal)."""
    st = state(context)
    s.set_stats(oid, name=st.name.strip() or None, hp=st.hp, attack=st.attack, range=st.range,
                line_of_sight=st.line_of_sight, reload_time=st.reload_time, speed=st.speed,
                armor={a: getattr(st, 'armor_' + a) for a in gamedata.ARMORS},
                build_time=st.build_time, **{c: getattr(st, c) for c in gamedata.COSTS})
    if st.is_created and st.available_now != s.is_available_now(oid):
        s.set_available_now(oid, st.available_now)
    u = s.unit(oid, gid)
    anims = {a: tuple(getattr(st, prop)) for a, prop in ANIM_PROPS.items()}
    current = base_anims or u.get('anims')
    msg = ''
    if st.use_mesh:
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            raise gamedata.GameError('select the mesh to use as the model (make it the active object)')
        check_anim_frames(anims, context.scene.frame_end - context.scene.frame_start + 1)
        if reference is None:
            reference = s.original_model(oid, base_oid, gid)
        data, sst, msg = cem.export_model(context, obj, st.scale, True, reference)
        gid = s.set_model(oid, data, sst, gid=gid)
        s.set_animations(oid, anims, gid=gid)
    elif current and anims != current:
        try:
            model, _ = cem.read_cem(s.model_files(oid, gid)[0])
        except cem.UnsupportedModel:
            raise gamedata.GameError(
                'the animation frames cannot be changed alone here: this model uses a format the add-on cannot '
                'read, so its frame count is unknown. Replace the model (Use Active Mesh as Model) to change them.')
        check_anim_frames(anims, len(model['frames']))
        s.set_animations(oid, anims, gid=gid)
    return msg


# ---------------------------------------------------------------- operators
class EEUS_OT_load(bpy.types.Operator):
    """Read the unit list from the game files"""
    bl_idname = 'ee_unit_studio.load'
    bl_label = 'Load Units'

    def execute(self, context):
        global _session
        p = prefs(context)
        try:
            _session = gamedata.GameSession(bpy.path.abspath(p.game_dir), bpy.path.abspath(p.base_dir))
        except (gamedata.GameError, OSError, ValueError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        fill_list(context)
        if _session.base is None:
            self.report({'WARNING'}, 'Base game archive not found (%s); original models may be missing. '
                                     'Set "Base Game Folder" in the add-on preferences.' % _session.base_ssa)
        else:
            self.report({'INFO'}, '%d units loaded' % len(state(context).units))
        return {'FINISHED'}


class _NeedsUnit:
    @classmethod
    def poll(cls, context):
        return session(context) is not None and selected_oid(context) is not None


class EEUS_OT_import(_NeedsUnit, bpy.types.Operator):
    """Import the selected unit's 3D model, texture and animation into the scene"""
    bl_idname = 'ee_unit_studio.import_model'
    bl_label = 'Read Model from Game'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s, oid, gid = session(context), selected_oid(context), selected_look(context)
        try:
            data, tex, tex_name = s.model_files(oid, gid)
            cem.read_cem(data)  # check the format before anything in the scene is removed
        except gamedata.GameError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except cem.UnsupportedModel as e:
            self.report({'ERROR'}, '%s. You can still replace it with your own model: select your mesh, tick '
                                   '"Use Active Mesh as Model" and click Write Changes to Game.'
                        % str(e).capitalize())
            return {'CANCELLED'}
        u = s.unit(oid, gid)
        if state(context).clear_previous:
            cem.clear_imported()
        name = (u['name'] or u['internal']) + (' ' + selected_look_name(context) if gid else '')
        root, model = cem.import_model(context, data, gamedata.slug(name), tex, tex_name)
        markers = context.scene.timeline_markers
        for m in [m for m in markers if m.name.startswith('ee_')]:
            markers.remove(m)
        for a, (first, last) in u.get('anims', {}).items():
            markers.new('ee_%s %d-%d' % (a, first, last), frame=first)
        for o in context.selected_objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        self.report({'INFO'}, '%s: %d frames, tags: %s%s' % (
            u['name'] or u['internal'], len(model['frames']), ', '.join(model['tags']) or 'none',
            '' if tex else ' (texture not found)'))
        return {'FINISHED'}


class EEUS_OT_write(_NeedsUnit, bpy.types.Operator):
    """Write the name, stats, cost and animation frames (and the active mesh, if enabled) into the game"""
    bl_idname = 'ee_unit_studio.write'
    bl_label = 'Write Changes to Game'

    def execute(self, context):
        s, oid, gid = session(context), selected_oid(context), selected_look(context)
        look_name = selected_look_name(context)
        t = time.time()
        try:
            msg = apply_fields(context, s, oid, gid=gid)
            s.save()
        except (gamedata.GameError, OSError, RuntimeError) as e:
            s.load()  # drop the half-applied edit
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        fill_list(context, oid, look_name)
        self.report({'INFO'}, 'Saved to game in %.1fs%s' % (time.time() - t, ('. Model: ' + msg) if msg else ''))
        return {'FINISHED'}


class EEUS_OT_create(_NeedsUnit, bpy.types.Operator):
    """Create a new unit as a copy of the selected one, with the values from the panel, and write it into the game"""
    bl_idname = 'ee_unit_studio.create'
    bl_label = 'Create New Unit'

    def execute(self, context):
        s, base, st = session(context), selected_oid(context), state(context)
        name = st.new_name.strip()
        if not name:
            self.report({'ERROR'}, 'Enter a name for the new unit')
            return {'CANCELLED'}
        look = selected_look(context)
        base_anims = s.unit(base, look).get('anims')
        try:
            oid, desc = s.new_unit(base, name, st.new_epoch or None, available_now=st.new_available_now)
            reference = None
            if look:  # created from an epoch-look row: the new unit looks like that
                gamedata.set_i32(s.objs.recs[oid], gamedata.OBJ['graphics'], look)
                reference = s.original_model(base, gid=look)
            st.is_created = False  # the fields below still describe the base unit's settings
            st.name = name
            msg = apply_fields(context, s, oid, base_anims, base, reference=reference)
            s.save()
        except (gamedata.GameError, OSError, RuntimeError) as e:
            s.load()
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        fill_list(context, oid)
        self.report({'INFO'}, '"%s" created (%s)%s' % (name, desc, ('. Model: ' + msg) if msg else ''))
        return {'FINISHED'}


class EEUS_OT_reset(_NeedsUnit, bpy.types.Operator):
    """Put the selected unit back as it was in the original game: name, stats, cost, build time, button, model,
    texture and animations. Other units are not changed; units you created stay trainable"""
    bl_idname = 'ee_unit_studio.reset_unit'
    bl_label = 'Reset Unit to Original'

    @classmethod
    def poll(cls, context):
        return super().poll(context) and not state(context).is_created

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s, oid, look_name = session(context), selected_oid(context), selected_look_name(context)
        try:
            s.reset_unit(oid)
            s.save()
        except (gamedata.GameError, OSError, ValueError) as e:
            s.load()
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        fill_list(context, oid, look_name)
        fill_edit_fields(context)
        self.report({'INFO'}, '"%s" is back to the original' % (s.unit(oid)['name'] or s.unit(oid)['internal']))
        return {'FINISHED'}


class EEUS_OT_restore(bpy.types.Operator):
    """Put back the original data.ssa and Language.dll (removes every change made with this add-on)"""
    bl_idname = 'ee_unit_studio.restore'
    bl_label = 'Restore Original Game Files'

    @classmethod
    def poll(cls, context):
        return session(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = session(context)
        oid = selected_oid(context)
        restored = s.restore()
        fill_list(context, oid)
        self.report({'INFO'}, ('Restored ' + ', '.join(restored)) if restored else 'No backups found, nothing changed')
        return {'FINISHED'}


class EEUS_OT_import_file(bpy.types.Operator, ImportHelper):
    """Import an Empire Earth model file"""
    bl_idname = 'import_scene.ee_cem'
    bl_label = 'Import Empire Earth Model'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.cem'
    filter_glob: StringProperty(default='*.cem', options={'HIDDEN'})
    texture_path: StringProperty(name='Texture', subtype='FILE_PATH',
                                 description='.sst or .tga; left empty, a matching file next to the model is used')
    clear_previous: BoolProperty(name='Clear Previous Model', default=False,
                                 description='Delete the models imported earlier first')

    def execute(self, context):
        try:
            if self.clear_previous:
                cem.clear_imported()
            root, model = cem.import_file(context, self.filepath, self.texture_path)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, '%d frames, tags: %s' % (len(model['frames']), ', '.join(model['tags']) or 'none'))
        return {'FINISHED'}


class EEUS_OT_export_file(bpy.types.Operator, ExportHelper):
    """Export the active mesh (and its animation) as an Empire Earth model file"""
    bl_idname = 'export_scene.ee_cem'
    bl_label = 'Export Empire Earth Model'
    filename_ext = '.cem'
    filter_glob: StringProperty(default='*.cem', options={'HIDDEN'})
    scale: FloatProperty(name='Scale', default=1.0, min=0.0001)
    export_texture: BoolProperty(name='Export Texture (.sst)', default=True)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, 'Select the unit mesh (make it the active object)')
            return {'CANCELLED'}
        try:
            data, sst, msg = cem.export_model(context, obj, self.scale, self.export_texture)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        open(self.filepath, 'wb').write(data)
        if sst:
            open(os.path.splitext(self.filepath)[0] + 't.sst', 'wb').write(sst)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------- UI
class EEUS_UL_units(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        if item.look:  # an epoch look of the unit in the row(s) above
            row.label(text='', icon='BLANK1')
            row.label(text=item.label, icon='TEXTURE')
            if item.look_changed:
                row.label(text='', icon='MODIFIER')
            return
        row.label(text=item.label, icon='COMMUNITY' if item.trainable else 'OBJECT_DATA')
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text='%d HP' % item.hp)
        if item.modified:  # changed: wrench, created with the add-on: plus
            sub.label(text='', icon='ADD' if item.modified == 'created' else 'MODIFIER')

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = bpy.types.UI_UL_list.filter_items_by_name(self.filter_name, self.bitflag_filter_item, items, 'name')
        if not flags:
            flags = [self.bitflag_filter_item] * len(items)
        flags = [f if unit_visible(data, it) else 0 for f, it in zip(flags, items)]
        order = bpy.types.UI_UL_list.sort_items_by_name(items, 'label') if self.use_filter_sort_alpha else []
        return flags, order


def unit_visible(st, item):
    """Panel filters: 'Trainable only', 'Modified only' and the search words
    (all must match)."""
    if st.only_trainable and not item.trainable:
        return False
    if st.only_modified and not item.modified:
        return False
    text = item.name.lower()
    return all(w == '#%d' % item.oid if w.startswith('#') else w in text for w in st.search.lower().split())


def fold(layout, st, prop, text):
    """A clickable heading that shows or hides the fields below it; returns
    whether they are shown."""
    shown = getattr(st, prop)
    layout.prop(st, prop, text=text, emboss=False, icon='DISCLOSURE_TRI_DOWN' if shown else 'DISCLOSURE_TRI_RIGHT')
    return shown


class EEUS_PT_panel(bpy.types.Panel):
    bl_label = 'EE Unit Studio'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Empire Earth'

    def draw(self, context):
        layout, st, p, s = self.layout, state(context), prefs(context), session(context)
        layout.prop(p, 'game_dir', text='')
        row = layout.row(align=True)
        row.operator(EEUS_OT_load.bl_idname, icon='FILE_REFRESH', text='Reload Units' if s else 'Load Units')
        row.operator(nodes.EEUS_OT_switch_editor.bl_idname, text='Building Panels', icon='NODETREE').target = 'NODES'
        if not s:
            return
        extra = {'placeholder': 'Search units'} if HAS_PLACEHOLDER else {}
        layout.prop(st, 'search', text='', icon='VIEWZOOM', **extra)
        row = layout.row()
        row.prop(st, 'only_trainable')
        row.prop(st, 'only_modified')
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text='%d of %d' % (sum(unit_visible(st, it) for it in st.units), len(st.units)))
        layout.template_list('EEUS_UL_units', '', st, 'units', st, 'index', rows=8)
        oid = selected_oid(context)
        if oid is None:
            layout.label(text='Select a unit')
            return
        u = s.unit(oid)
        layout.label(text=st.info, icon='INFO')
        if selected_look(context):
            layout.label(text='Model, texture and animation frames: this look only. Stats: whole unit.',
                         icon='TEXTURE')
        row = layout.row(align=True)
        row.operator(EEUS_OT_import.bl_idname, icon='IMPORT')
        row.prop(st, 'clear_previous', text='', icon='TRASH')

        box = layout.box()
        box.prop(st, 'name')
        col = box.column(align=True)
        if fold(col, st, 'show_stats', 'Stats'):
            for prop in ('hp', 'attack', 'range', 'reload_time', 'line_of_sight', 'speed'):
                col.prop(st, prop)
        col = box.column(align=True)
        if fold(col, st, 'show_armor', 'Armor'):
            for a in gamedata.ARMORS:
                col.prop(st, 'armor_' + a)
        if u['trainable']:
            col = box.column(align=True)
            if fold(col, st, 'show_cost', 'Cost and Build Time'):
                for c in gamedata.COSTS:
                    col.prop(st, c)
                col.separator()
                col.prop(st, 'build_time')
            if st.is_created:
                box.prop(st, 'available_now')
        if 'anims' in u:
            col = box.column(align=True)
            if fold(col, st, 'show_anims', 'Animation Frames (first, last)'):
                for prop in ANIM_PROPS.values():
                    col.prop(st, prop)
        col = box.column()
        col.prop(st, 'use_mesh')
        if st.use_mesh:
            obj = context.active_object
            col.label(text='Mesh: %s, frames %d-%d' % (obj.name if obj and obj.type == 'MESH' else '(none selected)',
                                                     context.scene.frame_start, context.scene.frame_end),
                      icon='MESH_DATA')
            col.prop(st, 'scale')
        row = box.row()
        row.scale_y = 2.0
        row.operator(EEUS_OT_write.bl_idname, icon='EXPORT')
        if not st.is_created:
            box.operator(EEUS_OT_reset.bl_idname, icon='FILE_REFRESH')

        if u['trainable']:
            box = layout.box()
            box.label(text='New unit based on this one (uses the values above)')
            box.prop(st, 'new_name')
            box.prop(st, 'new_epoch')
            box.prop(st, 'new_available_now')
            box.operator(EEUS_OT_create.bl_idname, icon='ADD')
        layout.operator(EEUS_OT_restore.bl_idname, icon='LOOP_BACK')


def _menu_import(self, context):
    self.layout.operator(EEUS_OT_import_file.bl_idname, text='Empire Earth Model (.cem)')


def _menu_export(self, context):
    self.layout.operator(EEUS_OT_export_file.bl_idname, text='Empire Earth Model (.cem)')


classes = (EEUS_Preferences, EEUS_UnitItem, EEUS_State, EEUS_OT_load, EEUS_OT_import, EEUS_OT_write,
           EEUS_OT_create, EEUS_OT_reset, EEUS_OT_restore, EEUS_OT_import_file, EEUS_OT_export_file, EEUS_UL_units,
           EEUS_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.ee_unit_studio = PointerProperty(type=EEUS_State)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)
    nodes.register()


def unregister():
    nodes.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    del bpy.types.Scene.ee_unit_studio
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
