"""Generic multi-solid "Component" container.

Generalizes the container/placement machinery originally built for
SMDComponent (see features/smd_component.py) so any assembly of tagged
conductor/dielectric solids -- parametrically generated (SMD) or captured
from an existing selection -- can be organized, moved, copied, and deleted
as a single unit.

Every Component owns two parallel solid sets, exactly like the original SMD
design: a hidden "master" set (Group children -- the single movable/
re-attachable assembly) and a visible "shadow" set (ShadowSolids -- plain
copies physically added to the shared DielectricGroup/ConductorGroup
objects, so meshing/config generation need no Component-specific code at
all: shadow solids are ordinary group members exactly like any other
dielectric/conductor solid a user adds by hand).

Placement propagation
----------------------
Broadcasting the container's Placement onto every child unmodified (the
original SMD behavior) is only correct when every child was generated in a
shared local frame sharing the exact same absolute Placement -- true for SMD
(all ten solids are placed identically at creation time, with relative
offsets baked into raw vertex coordinates, not into per-child Placement) but
not for solids captured from an existing design, which each carry their own
independent absolute placement.

``ChildBasePlacements`` records, for each logical solid, its placement
*relative to the container's own Placement at the moment it was added*:
``base_i = container.Placement.inverse() * child_absolute_placement``.
``onChanged()`` then recomputes each child's absolute placement as
``obj.Placement.multiply(base_i)`` whenever the container moves. Because
``multiply()`` composes "base_i first, then obj.Placement", this is the
standard parent/child rigid-transform composition: moving the container
applies the same delta transform to every child, preserving their relative
offsets.

For SMD, every master/shadow is placed at the exact same absolute Placement
as the container itself at bake time (the container's Placement is set to
its final attachment target *before* any child is added -- see
``create_smd_component()``), so every ``base_i`` reduces to Identity and the
formula collapses to today's flat ``child.Placement = obj.Placement``
broadcast. For a Selection-derived component (container left at its default
Identity placement, each child kept at its original absolute position), each
``base_i`` equals that solid's original absolute placement, so moving or
duplicating the container correctly preserves relative offsets between the
tagged solids instead of collapsing them onto one spot.

**Calling convention**: ``add_solid_pair()`` computes ``base_i`` from
``container.Placement``'s value *at the time it is called* -- callers must
set ``container.Placement`` to its intended final value (a real attachment
target for SMD, or simply leave it at the default Identity for a
Selection-derived component) *before* adding any children, not after.

**Index alignment invariant**: ``add_solid_pair()`` always appends to
``Group`` (masters), ``ShadowSolids``, and ``ChildBasePlacements`` together,
in the same call, so the three stay the same length with matching order --
``Group[i]``/``ShadowSolids[i]``/``ChildBasePlacements[i]`` always describe
the same logical solid. Any code that removes a child later (e.g. shrinking
a Selection-derived component in its edit panel) must remove the same index
from all three in lock-step, or ``onChanged``'s zip-by-index placement
propagation will misalign for every entry after the removed one.
"""

import os

from features import find_dielectric_groups, find_conductor_groups

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Component.svg")
_SMD_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "SMDComponent.svg")


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


def init_component_properties(obj):
    """Add the properties every Component needs, regardless of ComponentKind.

    Callable both by the generic ``Component`` proxy below and by any
    kind-specific proxy (e.g. ``SMDComponent`` in features/smd_component.py)
    that adds its own extra properties on top -- this codebase avoids Python
    class hierarchies for feature proxies (see DielectricGroup/ConductorGroup
    in features/material_group.py, independent sibling classes rather than
    subclasses of a common base), so sharing is done via plain functions
    instead of inheritance.
    """
    _add(obj, "App::PropertyString", "ComponentKind", "Component",
         "How this component's geometry was produced, e.g. 'SMD' or 'Selection'", "")
    _add(obj, "App::PropertyLinkList", "ShadowSolids", "Component",
         "Visible copies of this component's solids, physically added to "
         "the shared dielectric/conductor groups for meshing")
    _add(obj, "App::PropertyPlacementList", "ChildBasePlacements", "Component",
         "Each logical solid's placement relative to this container's own "
         "Placement at the moment it was added -- see module docstring")


def propagate_placement(obj):
    """Recompute every child's absolute Placement from ChildBasePlacements.

    Call from any Component-kind proxy's ``onChanged(obj, prop)`` when
    ``prop == "Placement"``.
    """
    masters = list(getattr(obj, "Group", []))
    shadows = list(getattr(obj, "ShadowSolids", []))
    bases = list(getattr(obj, "ChildBasePlacements", []))
    touched = []
    for i, base in enumerate(bases):
        new_placement = obj.Placement.multiply(base)
        if i < len(masters):
            masters[i].Placement = new_placement
            touched.append(masters[i])
        if i < len(shadows):
            shadows[i].Placement = new_placement
            touched.append(shadows[i])
    if not touched:
        return

    if obj.Document.Recomputing:
        # This onChanged is firing *during* an already-active doc.recompute()
        # -- e.g. obj.Placement was just set by its own Part::AttachExtension
        # resolving an attachment to another object's face/edge that just
        # moved. Document.recompute() refuses reentrant calls here ("Recursive
        # calling of recompute") -- and confirmed empirically, calling it
        # anyway doesn't just no-op, it corrupts the outer pass's remaining
        # work (dependents silently never got recomputed in testing). So in
        # this branch we deliberately do nothing further and leave every
        # child touched: the already-running outer recompute will still reach
        # any dependents on its own (a port's PartDesign::SubShapeBinder --
        # features/port_group.py -- a LumpedPort's cached edge-pair Shape, or
        # an ImpedanceBoundary's cached Shape), at the cost of a "still
        # touched after recompute" report-view line for each child in this
        # specific (attachment-driven) case.
        return

    # Not nested inside another recompute -- e.g. a direct Property Editor
    # edit or a script setting obj.Placement. Safe to recompute the moved
    # children now (so any dependents reachable in this pass get their turn)
    # and then purge their touched flag: a plain Part::Feature has no
    # execute() to naturally clear its own touched flag, so without this the
    # *enclosing* doc.recompute() (the one that ends up processing whatever
    # triggered this onChanged) would log "still touched after recompute" for
    # each one. Purging immediately after -- rather than never purging, an
    # earlier version of this function -- silences that, without starving
    # dependents of the recompute they need first.
    obj.Document.recompute(touched)
    for child in touched:
        child.purgeTouched()


def restore_component_extensions(obj):
    """Re-add the Group/Attach extensions if a legacy document is missing them.

    Call from any Component-kind proxy's ``onDocumentRestored``.
    """
    if not hasattr(obj, "Group"):
        try:
            obj.addExtension("App::GroupExtensionPython")
        except Exception:
            pass
    if not hasattr(obj, "MapMode"):
        try:
            obj.addExtension("Part::AttachExtensionPython")
        except Exception:
            pass


class Component:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        init_component_properties(obj)

    def execute(self, obj):
        pass

    def onChanged(self, obj, prop):
        if prop == "Placement":
            propagate_placement(obj)

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        restore_component_extensions(obj)

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderComponent:
    def __init__(self, vobj):
        vobj.Proxy = self
        vobj.addExtension("PartGui::ViewProviderAttachExtensionPython")

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object

    def claimChildren(self):
        return getattr(self.Object, "Group", [])

    def getIcon(self):
        if getattr(self.Object, "ComponentKind", "") == "SMD":
            return _SMD_ICON
        return _ICON

    def setEdit(self, vobj, mode=0):
        obj = vobj.Object
        from panels import show_palace_panel
        if getattr(obj, "ComponentKind", "") == "SMD":
            from panels.smd_component_panel import SMDComponentPanel
            show_palace_panel(SMDComponentPanel.for_existing(obj))
        else:
            from panels.component_panel import ComponentBuilderPanel
            show_palace_panel(ComponentBuilderPanel.for_existing(obj))
        return True

    def unsetEdit(self, vobj, mode=0):
        import FreeCADGui
        FreeCADGui.Control.closeDialog()
        return False

    def doubleClicked(self, vobj):
        self.setEdit(vobj)
        return True

    def onDelete(self, vobj, subelements):
        # Called by FreeCAD's tree-view Delete command before it removes this
        # object -- cascade-delete the masters/shadows/ImpedanceBoundary/
        # now-empty material groups here, then return True so FreeCAD
        # proceeds to remove the container itself right afterward. See
        # delete_component_children()'s docstring for why this lives here
        # rather than on the DocumentObject's own Proxy.
        obj = vobj.Object
        delete_component_children(obj.Document, obj)
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def create_component_container(doc, kind, base_name="Component", proxy_cls=Component):
    """Create an empty Component container of the given ComponentKind.

    *proxy_cls* defaults to the generic ``Component`` proxy; pass a
    kind-specific proxy (e.g. ``SMDComponent``) for a kind that needs extra
    properties -- it must call ``init_component_properties``/
    ``propagate_placement``/``restore_component_extensions`` itself (see
    their docstrings) so the container still behaves like any other
    Component as far as this module's helpers are concerned.

    Its Placement is left at the default Identity. Callers that need a
    non-Identity final placement (e.g. SMD's attachment target) must set
    ``obj.Placement`` themselves *before* calling ``add_solid_pair`` for any
    child -- see the module docstring's "Calling convention".
    """
    obj = doc.addObject("Part::FeaturePython", base_name)
    obj.addExtension("App::GroupExtensionPython")
    obj.addExtension("Part::AttachExtensionPython")
    proxy_cls(obj)
    obj.ComponentKind = kind
    if obj.ViewObject is not None:
        ViewProviderComponent(obj.ViewObject)
    return obj


def _add_solid(doc, base_name, solid, placement, visible=True):
    obj = doc.addObject("Part::Feature", base_name)
    # doc.addObject() only guarantees a unique internal Name -- on a collision
    # (e.g. base_name reused from a source object that's still live in the
    # document) it silently disambiguates the Name (e.g. "Box" -> "Box001")
    # and leaves Label defaulted to that disambiguated Name rather than the
    # originally-requested text. Setting Label explicitly guarantees the
    # caller's intended name shows in the tree regardless of any such
    # collision.
    obj.Label = base_name
    obj.Shape = solid
    obj.Placement = placement
    if obj.ViewObject is not None:
        obj.ViewObject.Visibility = visible
    return obj


def add_solid_pair(doc, container, base_name, solid, absolute_placement):
    """Add one logical solid to *container* as a hidden master + visible shadow pair.

    Both copies are placed at *absolute_placement* (this always wins,
    regardless of whatever placement *solid* itself carried -- Part::Feature
    Placement assignment is absolute, not compositional). Records
    *absolute_placement* relative to the container's *current* Placement in
    ChildBasePlacements (see module docstring), so a later change to
    container.Placement recomputes each child's absolute placement instead of
    silently overwriting it.

    Returns (master, shadow); the caller is responsible for adding *shadow*
    to whichever DielectricGroup/ConductorGroup applies -- this function only
    handles the container's own bookkeeping.
    """
    master = _add_solid(doc, base_name + "Master", solid, absolute_placement, visible=False)
    shadow = _add_solid(doc, base_name, solid.copy(), absolute_placement, visible=True)

    container.addObject(master)
    container.ShadowSolids = list(container.ShadowSolids) + [shadow]

    base = container.Placement.inverse().multiply(absolute_placement)
    container.ChildBasePlacements = list(container.ChildBasePlacements) + [base]

    return master, shadow


def delete_component_children(doc, container):
    """Remove everything *container* owns EXCEPT the container object itself:
    the master solids, the shadow solids, and the ImpedanceBoundary (if any --
    SMD-only). Additionally, any DielectricGroup/ConductorGroup left with
    zero members once this component's shadows are removed from it is
    deleted too, since a material group with no solids left in it is dead
    weight (a MeshAttribute number that maps to nothing, at best; a dangling
    reference in palace_config.json, at worst). A group still shared by
    another component or hand-added solid is left untouched -- this is
    checked *after* this component's own removals complete, so a group is
    never judged empty prematurely.

    Split out from delete_component() so ViewProviderComponent.onDelete() can
    call it too: native tree-view Delete goes through FreeCAD's GUI delete
    command, which consults the *ViewProvider's* onDelete hook, not the
    DocumentObject's -- confirmed empirically in this FreeCAD build, a plain
    Document.removeObject() does NOT invoke any App-level Proxy.onDelete.
    Without this hook, deleting a Component via the tree only removed the
    container itself, orphaning its masters/shadows/material groups.
    """
    live_names = {o.Name for o in doc.Objects}

    shadows = [s for s in list(getattr(container, "ShadowSolids", [])) if s.Name in live_names]
    candidate_groups = [
        grp for grp in find_dielectric_groups(doc) + find_conductor_groups(doc)
        if any(s in grp.Group for s in shadows)
    ]

    boundary = getattr(container, "ImpedanceBoundaryObj", None)

    for child in list(container.Group):
        if child.Name in live_names:
            doc.removeObject(child.Name)
    for shadow in shadows:
        doc.removeObject(shadow.Name)
    if boundary is not None and boundary.Name in live_names:
        doc.removeObject(boundary.Name)

    still_live = {o.Name for o in doc.Objects}
    for grp in candidate_groups:
        if grp.Name in still_live and len(grp.Group) == 0:
            doc.removeObject(grp.Name)


def delete_component(doc, container):
    """Remove *container* and everything it owns, leaving the simulation as
    clean as possible. See delete_component_children() for what "everything
    it owns" covers.
    """
    delete_component_children(doc, container)
    doc.removeObject(container.Name)


def create_component_from_solids(doc, role_assignments, base_name="Component"):
    """Build a "Selection"-kind Component from existing document solids.

    *role_assignments* is a list of ``(source_obj, role, material_name)``
    tuples, where *role* is ``"Conductor"`` or ``"Dielectric"``. Each
    source object's current Shape is baked into an independent copy
    (detached from its parametric history), so the resulting Component is
    fully self-contained -- safe to move, copy, or export without depending
    on the objects it was created from.

    The container's own Placement is left at its default Identity, so every
    solid keeps its original absolute position; ChildBasePlacements then
    records each one's original placement verbatim (base_i = P_i -- see
    module docstring), meaning moving/duplicating the returned container
    afterward preserves the solids' relative layout.

    Each (role, material_name) is resolved via
    get_or_create_dielectric_group/get_or_create_conductor_group
    (features/material_group.py) -- a material name shared with an existing
    group elsewhere in the document is reused rather than duplicated, same
    as SMD's shared ceramic/coating/contact groups.

    Returns the created Component container.
    """
    from features.material_group import (
        get_or_create_dielectric_group, get_or_create_conductor_group,
    )

    container = create_component_container(doc, kind="Selection", base_name=base_name)
    # container.Placement stays at its default Identity -- see docstring.

    for source_obj, role, material_name in role_assignments:
        _master, shadow = add_solid_pair(
            doc, container, source_obj.Label, source_obj.Shape.copy(), source_obj.Placement,
        )
        if role == "Dielectric":
            get_or_create_dielectric_group(doc, material_name, solids=[shadow])
        else:
            get_or_create_conductor_group(doc, material_name, solids=[shadow])

    doc.recompute()
    return container
