import FreeCAD
import os
from features import add_to_simulation

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons",
                     "ImpedanceBoundary.svg")

_ALL_DIRECTIONS = ["+R", "-R", "X", "-X", "Y", "-Y", "Z", "-Z"]

_BOUNDARY_COLOR = (0.502, 0.0, 0.502)   # Purple
_BOUNDARY_TRANSP = 30


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


def _compute_ar(obj):
    """Return the aspect ratio (length/width) for the port face.

    Returns 0.0 if the direction is radial or the face cannot be read.
    """
    direction = getattr(obj, "Direction", "X")
    if direction in ("+R", "-R"):
        return 0.0

    # Resolve a face to measure: prefer PortFaces, fall back to built Shape.
    fc_face = None
    if hasattr(obj, "PortFaces") and obj.PortFaces:
        try:
            link_obj, subs = obj.PortFaces[0]
            if subs:
                fc_face = link_obj.Shape.getElement(subs[0])
        except Exception:
            pass
    if fc_face is None and hasattr(obj, "Shape") and getattr(obj.Shape, "Faces", None):
        fc_face = obj.Shape.Faces[0]

    if fc_face is None:
        return 0.0

    try:
        bb = fc_face.BoundBox
        extents = {
            "X": bb.XMax - bb.XMin,
            "Y": bb.YMax - bb.YMin,
            "Z": bb.ZMax - bb.ZMin,
        }
        # Face normal axis = smallest extent
        normal_ax = min(extents, key=extents.get)
        in_plane = [ax for ax in ("X", "Y", "Z") if ax != normal_ax]

        dir_ax = direction.lstrip("+-")   # "X", "Y", or "Z"
        if dir_ax not in in_plane:
            return 0.0  # Direction is the normal — invalid

        length = extents[dir_ax]
        width_ax = [ax for ax in in_plane if ax != dir_ax][0]
        width = extents[width_ax]

        return length / width if width > 0 else 0.0
    except Exception:
        return 0.0


def _update_readonly(obj):
    """Toggle ReadOnly status on R/L/C and Rs/Ls/Cs based on SurfaceMode."""
    surface_mode = getattr(obj, "SurfaceMode", False)
    computed = ("Rs", "Ls", "Cs", "AspectRatio")
    user_total = ("R", "L", "C")

    if surface_mode:
        # User owns Rs/Ls/Cs; R/L/C are computed back
        for name in user_total:
            try:
                obj.setPropertyStatus(name, "ReadOnly")
            except Exception:
                pass
        for name in computed:
            try:
                obj.setPropertyStatus(name, "-ReadOnly")
            except Exception:
                pass
        # AspectRatio always read-only
        try:
            obj.setPropertyStatus("AspectRatio", "ReadOnly")
        except Exception:
            pass
    else:
        # User owns R/L/C; Rs/Ls/Cs are computed
        for name in user_total:
            try:
                obj.setPropertyStatus(name, "-ReadOnly")
            except Exception:
                pass
        for name in computed:
            try:
                obj.setPropertyStatus(name, "ReadOnly")
            except Exception:
                pass


class ImpedanceBoundary:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyInteger", "PortIndex", "Port",
             "Palace boundary index (1-based)", 1)
        _add(obj, "App::PropertyLinkSubList", "PortFaces", "Port",
             "Faces that define this impedance boundary surface (face-selection mode)")
        _add(obj, "App::PropertyLinkSubList", "PortEdges", "Port",
             "Two edges defining the boundary cross-section (edge-pair mode)")
        _add(obj, "App::PropertyString", "PortGeometryType", "Port",
             "Geometry type detected from edge pair: 'annular', 'planar', or ''", "")
        _add(obj, "App::PropertyEnumeration", "Direction", "Port",
             "Direction of current flow across the impedance surface")
        if hasattr(obj, "Direction"):
            obj.Direction = _ALL_DIRECTIONS

        _add(obj, "App::PropertyBool", "SurfaceMode", "Impedance",
             "When True the user sets Rs/Ls/Cs directly; "
             "when False R/L/C are primary and Rs/Ls/Cs are computed", False)

        _add(obj, "App::PropertyFloat", "R", "Impedance",
             "Total resistance across the boundary (Ω)", 50.0)
        _add(obj, "App::PropertyFloat", "L", "Impedance",
             "Total inductance across the boundary (H)", 0.0)
        _add(obj, "App::PropertyFloat", "C", "Impedance",
             "Total capacitance across the boundary (F)", 0.0)

        _add(obj, "App::PropertyFloat", "Rs", "Impedance",
             "Surface resistance (Ω/sq) — computed from R and aspect ratio", 0.0)
        _add(obj, "App::PropertyFloat", "Ls", "Impedance",
             "Surface inductance (H/sq) — computed from L and aspect ratio", 0.0)
        _add(obj, "App::PropertyFloat", "Cs", "Impedance",
             "Surface capacitance (F/sq) — computed from C and aspect ratio", 0.0)
        _add(obj, "App::PropertyFloat", "AspectRatio", "Impedance",
             "Geometric aspect ratio length/width (read-only, derived from face geometry)", 0.0)

        _add(obj, "App::PropertyInteger", "MeshAttribute", "Mesh",
             "Gmsh physical surface attribute number", 11)
        _add(obj, "App::PropertyFloat", "MeshSize", "Mesh",
             "Element size at this impedance boundary surface "
             "(0 = use global conductor mesh size)", 0.0)
        _add(obj, "App::PropertyFloat", "MeshRefineDistance", "Mesh",
             "Distance over which mesh transitions from MeshSize to background size "
             "(0 = use global refine distance)", 0.0)

        _add(obj, "App::PropertyColor",   "GroupColor",        "Display",
             "Color propagated to all child shapes", _BOUNDARY_COLOR)
        _add(obj, "App::PropertyInteger", "GroupTransparency", "Display",
             "Transparency 0-100 propagated to all child shapes", _BOUNDARY_TRANSP)

        _update_readonly(obj)

    def execute(self, obj):
        # Build face shape from edge pair (same as LumpedPort)
        if hasattr(obj, "PortEdges") and obj.PortEdges:
            edges = []
            for link_obj, subs in obj.PortEdges:
                for sub in (subs or []):
                    try:
                        edges.append(link_obj.Shape.getElement(sub))
                    except Exception:
                        pass
            if len(edges) >= 2:
                try:
                    from palace.port_geometry import build_port_shape
                    shape, kind = build_port_shape(edges[0], edges[1])
                    obj.Shape = shape
                    obj.PortGeometryType = kind
                except Exception as exc:
                    FreeCAD.Console.PrintError(
                        f"Palace: impedance boundary shape error: {exc}\n"
                    )

        # Compute aspect ratio and derive surface / total values
        ar = _compute_ar(obj)
        obj.AspectRatio = ar

        if not obj.SurfaceMode and ar > 0:
            obj.Rs = obj.R / ar
            obj.Ls = obj.L / ar
            obj.Cs = obj.C * ar
        elif obj.SurfaceMode and ar > 0:
            obj.R = obj.Rs * ar
            obj.L = obj.Ls * ar
            obj.C = obj.Cs / ar if obj.Cs != 0 else 0.0

        _update_readonly(obj)

    def onChanged(self, obj, prop):
        if prop == "SurfaceMode":
            _update_readonly(obj)
        if prop in ("GroupColor", "GroupTransparency"):
            from features.material_group import _propagate_from_data
            _propagate_from_data(obj)

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        if not hasattr(obj, "Group"):
            try:
                obj.addExtension("App::GroupExtensionPython")
            except Exception:
                pass
        if FreeCAD.GuiUp and obj.ViewObject is not None:
            try:
                obj.ViewObject.removeExtension(
                    "Gui::ViewProviderGroupExtensionPython", None
                )
            except Exception:
                pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderImpedanceBoundary:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object
        from pivy import coin
        self.node = coin.SoSeparator()
        vobj.addDisplayMode(self.node, "Group")

    def claimChildren(self):
        return getattr(self.Object, "Group", [])

    def getDisplayModes(self, vobj):
        return ["Group"]

    def getDefaultDisplayMode(self):
        return "Group"

    def onChanged(self, vobj, prop):
        if prop == "Visibility":
            for child in getattr(self.Object, "Group", []):
                if child.ViewObject is not None:
                    child.ViewObject.Visibility = vobj.Visibility

    def updateData(self, fp, prop):
        if prop == "Group":
            from features.material_group import _propagate_from_data
            _propagate_from_data(fp)

    def getIcon(self):
        return _ICON

    def setEdit(self, vobj, mode=0):
        from panels.impedance_boundary_panel import ImpedanceBoundaryPanel
        from panels import show_palace_panel
        show_palace_panel(ImpedanceBoundaryPanel(vobj.Object))
        return True

    def unsetEdit(self, vobj, mode=0):
        import FreeCADGui
        FreeCADGui.Control.closeDialog()
        return False

    def doubleClicked(self, vobj):
        self.setEdit(vobj)
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def create_impedance_boundary(doc, index=1, faces=None, edge_refs=None):
    """Create an impedance boundary feature object.

    Parameters
    ----------
    faces     : list of (obj, [sub_names]) for face-selection mode
    edge_refs : list of (obj, sub_name) pairs — exactly 2 edges for edge-pair mode
    """
    obj = doc.addObject("Part::FeaturePython", f"PalaceImpedanceBoundary{index}")
    obj.addExtension("App::GroupExtensionPython")
    ImpedanceBoundary(obj)
    obj.PortIndex = index
    obj.MeshAttribute = 10 + index

    if edge_refs and len(edge_refs) >= 2:
        by_obj = {}
        for ref_obj, sub in edge_refs[:2]:
            key = id(ref_obj)
            if key not in by_obj:
                by_obj[key] = (ref_obj, [])
            by_obj[key][1].append(sub)
        obj.PortEdges = [(ref_obj, subs) for ref_obj, subs in by_obj.values()]

        # Direction defaults to "+R" (radial, for annular/coaxial geometry) --
        # for a planar boundary that's invalid and silently zeroes _compute_ar(),
        # so Rs/Ls/Cs never get derived from R/L/C. Auto-detect the major axis
        # for planar edge pairs instead; leave annular ones at "+R".
        try:
            e1 = edge_refs[0][0].Shape.getElement(edge_refs[0][1])
            e2 = edge_refs[1][0].Shape.getElement(edge_refs[1][1])
            from palace.port_geometry import classify_edge_pair, detect_axis_direction
            kind, _, _ = classify_edge_pair(e1, e2)
            if kind == "planar":
                obj.Direction = detect_axis_direction(e1, e2)
        except Exception:
            pass
    elif faces:
        obj.PortFaces = faces

    if obj.ViewObject is not None:
        ViewProviderImpedanceBoundary(obj.ViewObject)
    add_to_simulation(doc, obj)
    doc.recompute()
    return obj
