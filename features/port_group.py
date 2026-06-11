"""
Helper for keeping a port object's Group in sync with its sub-shape selections.

Each port (WavePort, LumpedPort) owns a Group (via App::GroupExtensionPython).
Calling sync_port_group() after the user confirms a face/edge selection will:
  1. Remove all existing children from the Group (unconditional — the Group is
     entirely managed by this function).
  2. For each selected face/edge reference, either:
     a. Add the source object directly (whole solid) if it is a "stray" object
        not already in any Palace Group — matches the material-group pattern.
     b. Create a PartDesign::SubShapeBinder referencing only the specific
        sub-element if the source object is locked inside another Palace Group
        (ConductorGroup, DielectricGroup, Airbox).
"""

_BINDER_TYPE = "PartDesign::SubShapeBinder"


def _in_any_palace_group(doc, obj):
    """Return True if obj or any Group-container ancestor is inside a Palace group.

    Handles nested Part/PartDesign structures (e.g. ConductorGroup → Part → Body → Pad)
    where the selected link_obj may not be a direct member of any Palace Group but is
    transitively contained within one.
    """
    from features import find_airbox, find_conductor_groups, find_dielectric_groups

    # IDs of objects that are direct members of any Palace container Group.
    palace_containers: set[int] = set()
    airbox = find_airbox(doc)
    if airbox and hasattr(airbox, "Group"):
        palace_containers.update(id(m) for m in airbox.Group)
    for g in find_conductor_groups(doc):
        if hasattr(g, "Group"):
            palace_containers.update(id(m) for m in g.Group)
    for g in find_dielectric_groups(doc):
        if hasattr(g, "Group"):
            palace_containers.update(id(m) for m in g.Group)

    # Build upward map: child_id → [parent_container, ...] via Group relationships.
    # Scanning .Group (not .InList) avoids false positives from binder/link references.
    parent_map: dict[int, list] = {}
    for candidate in doc.Objects:
        for child in getattr(candidate, "Group", []):
            parent_map.setdefault(id(child), []).append(candidate)

    # BFS upward from obj through the Group-parent graph.
    visited: set[int] = set()
    queue = [obj]
    while queue:
        current = queue.pop()
        cid = id(current)
        if cid in visited:
            continue
        visited.add(cid)
        if cid in palace_containers:
            return True
        for parent in parent_map.get(cid, []):
            queue.append(parent)
    return False


def sync_port_group(port_obj):
    """Rebuild the port's Group so it reflects the current face/edge selection.
    Safe to call every time the panel is accepted."""
    if not hasattr(port_obj, "Group"):
        return
    doc = port_obj.Document

    # Remove managed binders (owned by this function) and detach stray objects
    # without deleting them — stray objects belong to the user's document.
    for child in list(port_obj.Group):
        if child.TypeId == _BINDER_TYPE:
            doc.removeObject(child.Name)
        else:
            port_obj.removeObject(child)

    stray_added = set()   # deduplicate whole-object additions

    def _add_ref(link_obj, sub):
        """Add one face or edge reference to the port Group."""
        if _in_any_palace_group(doc, link_obj):
            b = doc.addObject(_BINDER_TYPE, "PortFace")
            b.Label = "Port Face"
            b.Support = [(link_obj, [sub])]
            port_obj.addObject(b)
        elif id(link_obj) not in stray_added:
            port_obj.addObject(link_obj)
            stray_added.add(id(link_obj))

    def _add_edge_ref(link_obj, sub, label):
        """Add one edge reference; always uses a binder if in a Palace Group,
        otherwise adds the whole object once."""
        if _in_any_palace_group(doc, link_obj):
            b = doc.addObject(_BINDER_TYPE, "PortEdge")
            b.Label = label
            b.Support = [(link_obj, [sub])]
            port_obj.addObject(b)
        elif id(link_obj) not in stray_added:
            port_obj.addObject(link_obj)
            stray_added.add(id(link_obj))

    # Face binders — both WavePort and LumpedPort (face-selection mode).
    if hasattr(port_obj, "PortFaces") and port_obj.PortFaces:
        for link_obj, subs in port_obj.PortFaces:
            for sub in (subs or []):
                _add_ref(link_obj, sub)

    # Integration edge — WavePort only.
    if hasattr(port_obj, "IntegrationEdge"):
        ie = port_obj.IntegrationEdge
        if ie and ie[0] and ie[1]:
            _add_edge_ref(ie[0], ie[1][0], "Integration Edge")

    # Port edges — LumpedPort edge-pair mode.
    if hasattr(port_obj, "PortEdges") and port_obj.PortEdges:
        for link_obj, subs in port_obj.PortEdges:
            for sub in (subs or []):
                _add_edge_ref(link_obj, sub, "Port Edge")
