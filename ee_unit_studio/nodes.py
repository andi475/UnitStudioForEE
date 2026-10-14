# SPDX-License-Identifier: MIT
# Part of EE Unit Studio, an unofficial fan-made modding tool for Empire Earth.
"""
Node editor for building panels: a Building node shows one epoch of a
building and has one input per button slot, a Unit node (also research and
buildings a Citizen builds) has one output. A link means "this building has
this button, in this slot, in this epoch"; units of earlier epochs that are
still available are linked too. Write to Game makes the game's building
lists, button slots and the epochs in which a tech is available (first to
last epoch) match the links.
"""
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import gamedata

TREE = 'EEUS_PanelTree'
SLOT_SOCKET, BUTTON_SOCKET = 'EEUS_SlotSocket', 'EEUS_ButtonSocket'
ROW_LABEL = {0: 'Bottom', 1: 'Row 2', 2: 'Row 3'}


def _addon():
    return sys.modules[__package__]


def _session(context):
    s = _addon().session(context)
    if not s:
        raise gamedata.GameError('load the units first (3D Viewport sidebar > Empire Earth > Load Units)')
    return s


def slot_label(slot):
    return '%s · %d' % (ROW_LABEL.get(slot // 16, 'Row %d' % (slot // 16 + 1)), slot % 16 + 1)


def epoch_name(sec):
    return gamedata.EPOCHS[sec] if 0 <= sec < len(gamedata.EPOCHS) else str(sec + 1)


# ---------------------------------------------------------------- tree, sockets, nodes
class EEUS_PanelTree(bpy.types.NodeTree):
    """Which buildings have which buttons (units, research), in which slot"""
    bl_idname = TREE
    bl_label = 'EE Building Panels'
    bl_icon = 'HOME'


class _InTree:
    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE


class EEUS_SlotSocket(bpy.types.NodeSocket):
    """A button slot of the building's panel"""
    bl_idname = SLOT_SOCKET
    bl_label = 'Button Slot'
    slot: IntProperty()
    # 'own': used in this epoch, 'free': empty in every epoch, 'other': used
    # only in other epochs (note says which), e.g. by the unit this one replaces
    kind: StringProperty(default='own')
    note: StringProperty()

    def draw(self, context, layout, node, text):
        if self.kind == 'free' and not self.is_linked:
            text += '  (free)'
        elif self.kind == 'other':
            text += '  (%s)' % self.note
        layout.label(text=text)

    def draw_color(self, context, node):
        return {'own': (0.95, 0.6, 0.2, 1.0), 'free': (0.55, 0.55, 0.55, 1.0)}.get(self.kind, (0.6, 0.45, 0.3, 1.0))


class EEUS_ButtonSocket(bpy.types.NodeSocket):
    """The unit's button: link it to a building slot"""
    bl_idname = BUTTON_SOCKET
    bl_label = 'Button'

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return (0.3, 0.6, 1.0, 1.0)


def _update_shown(self, context):
    for sock in self.inputs:
        sock.enabled = (sock.is_linked or sock.kind == 'own' or (sock.kind == 'free' and self.show_free)
                        or (sock.kind == 'other' and self.show_other))


class EEUS_BuildingNode(_InTree, bpy.types.Node):
    """One epoch of a building (or the Citizen) and the slots of its button panel"""
    bl_idname = 'EEUS_BuildingNode'
    bl_label = 'Building'
    bl_icon = 'HOME'
    oid: IntProperty()
    epoch: IntProperty()  # epoch section, 0 = Prehistoric
    show_free: BoolProperty(name='Free Slots', default=True, update=_update_shown,
                            description='Also show the slots that are free in every epoch (slots of fixed buttons '
                                        'like Stop or Populate are never offered)')
    show_other: BoolProperty(name='Other Epochs', default=False, update=_update_shown,
                             description='Also show the slots used only in other epochs, e.g. to put a unit in the '
                                         'slot of the one it replaces')

    def init(self, context):
        self.width = 260

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, 'show_free', toggle=True)
        row.prop(self, 'show_other', toggle=True)

    def slot_socket(self, slot, kind, note=''):
        """The input for a slot (created in slot order if missing)."""
        for sock in self.inputs:
            if sock.slot == slot:
                return sock
        sock = self.inputs.new(SLOT_SOCKET, slot_label(slot), identifier='slot%d' % slot)
        sock.slot, sock.kind, sock.note = slot, kind, note
        sock.link_limit = 4095
        order = sorted(range(len(self.inputs)), key=lambda i: self.inputs[i].slot)
        self.inputs.move(len(self.inputs) - 1, order.index(len(self.inputs) - 1))
        return sock


class EEUS_UnitNode(_InTree, bpy.types.Node):
    """A unit, building or research and its button; link it to a building slot of its epoch"""
    bl_idname = 'EEUS_UnitNode'
    bl_label = 'Unit'
    bl_icon = 'COMMUNITY'
    key: IntProperty()  # tech key
    section: IntProperty()  # the first epoch it is available in

    def init(self, context):
        self.outputs.new(BUTTON_SOCKET, '')
        self.width = 240


# ---------------------------------------------------------------- filling the tree
def _tree(context, create=True):
    space = context.space_data
    tree = getattr(space, 'edit_tree', None)
    if tree and tree.bl_idname == TREE:
        return tree
    if not create:
        return None
    tree = bpy.data.node_groups.new('Building Panels', TREE)
    if space and space.type == 'NODE_EDITOR':
        space.node_tree = tree
    return tree


def _unit_node(tree, s, key, location):
    for n in tree.nodes:
        if n.bl_idname == EEUS_UnitNode.bl_idname and n.key == key:
            return n
    info = s.tech_info(key)
    n = tree.nodes.new(EEUS_UnitNode.bl_idname)
    n.key, n.section, n.location = key, info['first'], location
    n.label = '%s · %s' % (info['label'], info['epoch'])
    if info['unit'] < 0:  # research: brown
        n.use_custom_color, n.color = True, (0.32, 0.27, 0.22)
    return n


def load_building(tree, s, node):
    """Rebuild a building node's slots and links from the game files.
    Returns the height the node and its unit nodes take up."""
    for sock in list(node.inputs):
        for link in list(sock.links):
            tree.links.remove(link)
    node.inputs.clear()
    panel = s.panel_of(node.oid)
    own = [i for i in panel if available(i, node.epoch)]  # this epoch's and earlier ones still available
    others = {}
    for i in panel:
        if not available(i, node.epoch):
            others.setdefault(i['slot'], set()).add(i['first'])
    for slot in sorted({i['slot'] for i in own}):
        node.slot_socket(slot, 'own')
    for slot in s.free_slots(node.oid):
        node.slot_socket(slot, 'free')
    for slot in sorted(set(others) - {i['slot'] for i in own}):
        node.slot_socket(slot, 'other', ', '.join(epoch_name(e) for e in sorted(others[slot])))
    by_slot = {}
    for info in own:
        by_slot.setdefault(info['slot'], []).append(info)
    y = node.location.y
    for sock in node.inputs:
        infos = by_slot.get(sock.slot, [])
        infos.sort(key=lambda i: -i['first'])  # this epoch's first, older ones further left
        for j, info in enumerate(infos):  # several buttons in one slot: side by side
            un = _unit_node(tree, s, info['key'], (node.location.x - 300 - 270 * j, y))
            tree.links.new(un.outputs[0], sock)
        if infos:
            y -= 50
    _update_shown(node, None)
    shown = sum(1 for sock in node.inputs if sock.enabled)
    return max(node.location.y - y, 60 + 20 * shown)


def available(info, epoch):
    return info['first'] <= epoch <= info['last']


def collect_panels(tree, s):
    """What Write to Game changes, from the links of every building node:
    ({building: {tech key: slot}}, {building: tech keys to remove},
    {tech key: last epoch}). Only epochs that have a node are looked at:
    a unit unlinked in the node of its first epoch is taken off the building;
    unlinked in a later node, it stops being available from that epoch on;
    linked into a later node than its last epoch, it stays available longer."""
    panels, removals, lasts = {}, {}, {}
    nodes_of = {}
    for n in tree.nodes:
        if n.bl_idname == EEUS_BuildingNode.bl_idname:
            nodes_of.setdefault(n.oid, {})[n.epoch] = n
    for b, nodes in nodes_of.items():
        bname = next(iter(nodes.values())).label.split(' · ')[0]
        linked = {}  # tech key -> {epoch: slot}
        for epoch, n in nodes.items():
            for sock in n.inputs:
                for link in sock.links:
                    src = link.from_node
                    if not link.is_valid or src.bl_idname != EEUS_UnitNode.bl_idname:
                        continue
                    if src.section > epoch:
                        raise gamedata.GameError('"%s" starts in the %s epoch, so it cannot be in the %s node'
                                                 % (src.label.split(' · ')[0], epoch_name(src.section), n.label))
                    linked.setdefault(src.key, {})[epoch] = sock.slot
        game = {i['key']: i for i in s.panel_of(b)}
        techs, gone = panels.setdefault(b, {}), removals.setdefault(b, set())
        for k in set(game) | set(linked):
            info = game.get(k) or s.tech_info(k)
            name = info['label']
            where = linked.get(k, {})
            if len(set(where.values())) > 1:
                raise gamedata.GameError('"%s" is in different slots in the %s nodes; a button has one slot'
                                         % (name, bname))
            unlinked = {e for e in nodes if k in game and available(info, e) and e not in where}
            last = None
            if not where:
                if not unlinked:
                    continue  # not shown in any node: stays as it is
                if info['first'] in unlinked:
                    gone.add(k)  # removed from its own epoch: off this building
                    continue
                last = min(unlinked) - 1
            else:
                techs[k] = next(iter(where.values()))
                if info['first'] in unlinked:
                    raise gamedata.GameError('"%s" starts in the %s epoch; link it there as well, or remove it from '
                                             'the later %s nodes too' % (name, epoch_name(info['first']), bname))
                later = {e for e in unlinked if e > info['first']}
                if later:
                    if max(where) > min(later):
                        raise gamedata.GameError('"%s" is linked in the %s node after being removed in the %s node; '
                                                 'a unit cannot come back in a later epoch'
                                                 % (name, epoch_name(max(where)), epoch_name(min(later))))
                    last = min(later) - 1
                elif max(where) > info['last']:
                    last = max(where)
            if last is not None:
                if lasts.setdefault(k, last) != last:
                    raise gamedata.GameError('"%s" would end in different epochs in two buildings' % name)
    return panels, removals, lasts


def _building_nodes(tree, oid):
    return [n for n in tree.nodes if n.bl_idname == EEUS_BuildingNode.bl_idname and n.oid == oid]


def add_epoch(tree, s, oid, epoch, location):
    """The node of one epoch of a building (created if missing) and its height."""
    for n in _building_nodes(tree, oid):
        if n.epoch == epoch:
            return n, 0
    node = tree.nodes.new(EEUS_BuildingNode.bl_idname)
    name = s.display_name(oid) or gamedata.name_of(s.objs.recs[oid]).strip()
    node.oid, node.epoch, node.location = oid, epoch, location
    node.label = '%s · %s' % (name, epoch_name(epoch))
    return node, load_building(tree, s, node)


def add_building(context, oid, epoch):
    """The node of one epoch of a building: below the building's other epoch
    nodes, or right of everything else. Returns it (selected and active)."""
    s, tree = _session(context), _tree(context)
    same = _building_nodes(tree, oid)
    if same:
        # below the building's lowest node or linked unit (sizes are estimated:
        # a node that was never drawn has no dimensions yet)
        bottom = min(min(n.location.y - max(n.dimensions.y, 60 + 20 * sum(x.enabled for x in n.inputs)),
                         *[l.from_node.location.y - 60 for x in n.inputs for l in x.links])
                     for n in same)
        location = (same[0].location.x, bottom - 120)
    else:
        right = max([n.location.x + n.width for n in tree.nodes] or [0])
        location = (right + (900 if tree.nodes else 0), 0)
    node, _ = add_epoch(tree, s, oid, epoch, location)
    for n in tree.nodes:
        n.select = n == node
    tree.nodes.active = node
    return node


# ---------------------------------------------------------------- operators
_items_cache = {}  # Blender needs the enum strings to stay alive


def _items(context, kind):
    s = _addon().session(context)
    if not s:
        return [('-1', 'Load the units first', '')]
    if kind not in _items_cache or _items_cache[kind][0] is not s:
        if kind == 'buildings':  # one entry per building and epoch it has buttons in
            rows = []
            for oid in s.buildings():
                name = s.display_name(oid) or gamedata.name_of(s.objs.recs[oid]).strip()
                for e in sorted({i['section'] for i in s.panel_of(oid)}):
                    rows.append(('%d:%d' % (oid, e), '%s · %s' % (name, epoch_name(e)), (name.lower(), e)))
            rows.sort(key=lambda r: r[2])
            _items_cache[kind] = (s, [(v, label, '') for v, label, _ in rows])
            return _items_cache[kind][1]
        else:
            ids = [oid for oid in s.unit_ids() if s.tech_of(oid)]
        rows = sorted(((s.display_name(oid) or gamedata.name_of(s.objs.recs[oid]).strip(),
                        gamedata.name_of(s.objs.recs[oid]).strip(), oid) for oid in ids), key=lambda r: r[0].lower())
        _items_cache[kind] = (s, [(str(oid), '%s (%s)' % (name, internal) if name != internal else name, '')
                                  for name, internal, oid in rows])
    return _items_cache[kind][1]


def _in_node_editor(context):
    return context.space_data and context.space_data.type == 'NODE_EDITOR'


class EEUS_OT_node_add_building(bpy.types.Operator):
    """Add one epoch of a building (or the Citizen) with the buttons that epoch adds, linked to their slots"""
    bl_idname = 'ee_unit_studio.node_add_building'
    bl_label = 'Add Building'
    bl_property = 'building'
    bl_options = {'REGISTER', 'UNDO'}
    building: EnumProperty(name='Building and Epoch', items=lambda self, context: _items(context, 'buildings'))

    @classmethod
    def poll(cls, context):
        return _in_node_editor(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            oid, epoch = map(int, self.building.split(':'))
            add_building(context, oid, epoch)
        except gamedata.GameError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class EEUS_OT_node_add_unit(bpy.types.Operator):
    """Add a unit's button; link it to a building slot of its epoch to train it there"""
    bl_idname = 'ee_unit_studio.node_add_unit'
    bl_label = 'Add Unit'
    bl_property = 'unit'
    bl_options = {'REGISTER', 'UNDO'}
    unit: EnumProperty(name='Unit', items=lambda self, context: _items(context, 'units'))

    @classmethod
    def poll(cls, context):
        return _in_node_editor(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            s, tree = _session(context), _tree(context)
        except gamedata.GameError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        oid = int(self.unit)
        key = gamedata.i32(s.objs.recs[oid], gamedata.OBJ['tech_key'])
        left = min([n.location.x for n in tree.nodes] or [0])
        top = max([n.location.y for n in tree.nodes] or [0])
        node = _unit_node(tree, s, key, (left - 300, top))
        for n in tree.nodes:
            n.select = n == node
        tree.nodes.active = node
        return {'FINISHED'}


class EEUS_OT_node_add_epoch(bpy.types.Operator):
    """Add another epoch of the selected building, e.g. to give it a unit of an epoch it has no buttons in yet"""
    bl_idname = 'ee_unit_studio.node_add_epoch'
    bl_label = 'Add Epoch'
    bl_property = 'epoch'
    bl_options = {'REGISTER', 'UNDO'}
    epoch: EnumProperty(name='Epoch', items=[(str(i), e, '') for i, e in enumerate(gamedata.EPOCHS)])

    @classmethod
    def poll(cls, context):
        tree = _tree(context, create=False) if _in_node_editor(context) else None
        return bool(tree and tree.nodes.active and tree.nodes.active.bl_idname == EEUS_BuildingNode.bl_idname)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            add_building(context, _tree(context, create=False).nodes.active.oid, int(self.epoch))
        except gamedata.GameError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class EEUS_OT_switch_editor(bpy.types.Operator):
    """Switch this editor between the 3D Viewport and the building panel nodes"""
    bl_idname = 'ee_unit_studio.switch_editor'
    bl_label = 'Switch Editor'
    target: EnumProperty(items=[('NODES', 'Building Panels', ''), ('VIEW_3D', '3D Viewport', '')])

    @classmethod
    def poll(cls, context):
        return context.area is not None

    def execute(self, context):
        area = context.area
        if self.target == 'VIEW_3D':
            area.type = 'VIEW_3D'
        else:
            area.type = 'NODE_EDITOR'
            area.ui_type = TREE
            space = area.spaces.active
            if not space.node_tree:  # the last building panel tree, or a new one
                trees = [t for t in bpy.data.node_groups if t.bl_idname == TREE]
                space.node_tree = trees[-1] if trees else bpy.data.node_groups.new('Building Panels', TREE)
        area.spaces.active.show_region_ui = True  # keep the Empire Earth sidebar open
        return {'FINISHED'}


class EEUS_OT_node_reload(bpy.types.Operator):
    """Show the buildings as they are in the game files again (discards links not written yet)"""
    bl_idname = 'ee_unit_studio.node_reload'
    bl_label = 'Reload from Game'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _in_node_editor(context) and _tree(context, create=False) is not None

    def execute(self, context):
        try:
            s = _session(context)
        except gamedata.GameError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        tree = _tree(context, create=False)
        for n in tree.nodes:
            if n.bl_idname == EEUS_BuildingNode.bl_idname:
                load_building(tree, s, n)
        return {'FINISHED'}


class EEUS_OT_node_write(bpy.types.Operator):
    """Make the buildings' panels in the game match the links: linked buttons are added or moved to
    their slot, unlinked ones removed from that building (only in the epochs that have a node)"""
    bl_idname = 'ee_unit_studio.node_write'
    bl_label = 'Write to Game'

    @classmethod
    def poll(cls, context):
        return _in_node_editor(context) and _tree(context, create=False) is not None

    def execute(self, context):
        tree = _tree(context, create=False)
        try:
            s = _session(context)
            changes = s.set_panels(*collect_panels(tree, s))
            if not changes:
                self.report({'INFO'}, 'nothing to change')
                return {'FINISHED'}
            s.save()
        except gamedata.GameError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for n in tree.nodes:
            if n.bl_idname == EEUS_BuildingNode.bl_idname:
                load_building(tree, s, n)
        _addon().fill_list(context)
        self.report({'INFO'}, '; '.join(changes[:6]) + (' (+%d more)' % (len(changes) - 6) if len(changes) > 6 else ''))
        return {'FINISHED'}


class EEUS_PT_nodes(bpy.types.Panel):
    bl_label = 'EE Building Panels'
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Empire Earth'

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, 'tree_type', '') == TREE

    def draw(self, context):
        layout = self.layout
        layout.operator(EEUS_OT_switch_editor.bl_idname, text='Back to 3D Viewport', icon='VIEW3D').target = 'VIEW_3D'
        col = layout.column(align=True)
        col.operator(EEUS_OT_node_add_building.bl_idname, icon='HOME')
        col.operator(EEUS_OT_node_add_unit.bl_idname, icon='COMMUNITY')
        col.operator(EEUS_OT_node_add_epoch.bl_idname, icon='TIME')
        col = layout.column(align=True)
        col.operator(EEUS_OT_node_reload.bl_idname, icon='FILE_REFRESH')
        row = col.row()
        row.scale_y = 2.0
        row.operator(EEUS_OT_node_write.bl_idname, icon='EXPORT')
        box = layout.box().column(align=True)
        for line in ('A building node shows one epoch,', 'with the units of earlier epochs', 'still available in it.',
                     'Link a unit to a slot: trained', 'there. Remove the link in its own', 'epoch: off the building; in a',
                     'later epoch: gone from that', 'epoch on. A button has one slot', 'in every building. Brown nodes',
                     'are research.'):
            box.label(text=line)


def _menu_add(self, context):
    if getattr(context.space_data, 'tree_type', '') == TREE:
        self.layout.separator()
        self.layout.operator(EEUS_OT_node_add_building.bl_idname, icon='HOME')
        self.layout.operator(EEUS_OT_node_add_unit.bl_idname, icon='COMMUNITY')
        self.layout.operator(EEUS_OT_node_add_epoch.bl_idname, icon='TIME')


classes = (EEUS_PanelTree, EEUS_SlotSocket, EEUS_ButtonSocket, EEUS_BuildingNode, EEUS_UnitNode,
           EEUS_OT_node_add_building, EEUS_OT_node_add_unit, EEUS_OT_node_add_epoch, EEUS_OT_switch_editor,
           EEUS_OT_node_reload,
           EEUS_OT_node_write, EEUS_PT_nodes)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.NODE_MT_add.append(_menu_add)


def unregister():
    bpy.types.NODE_MT_add.remove(_menu_add)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
