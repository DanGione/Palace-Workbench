"""
PalaceMesh feature object.

Holds mesh generation settings (characteristic lengths) and the path to the
last-generated .msh file.  Double-clicking opens MeshPanel where the engineer
can inspect element/node counts and refine sizing before running a long solve.

Uses Mesh::FeaturePython so the built-in C++ Mesh ViewProvider triggers a reliable
viewport refresh whenever obj.Mesh is set in execute().  The Python VP registers two
additional display modes built from coin3D:

  "Colored Wireframe"  — surface triangles only, colored by Gmsh physical-group attribute
  "With Volumes"       — same surface mesh + tet faces in thin lines for interior material
                         regions (airbox = blue, dielectric = green, etc.)

Refresh path:
  CmdMesh.Activated() → execute() → obj.Mesh = ... → C++ VP repaint
                                                    → Python VP updateData("Mesh")
                                                       → _update_colors()

Identification pattern (used by features/__init__.py find_palace_mesh):
    hasattr(obj, "MeshFile")
    and hasattr(obj, "MeshCharacteristicLengthMax")
    and not hasattr(obj, "SimulationType")
"""

import os
import FreeCAD
from features import add_to_simulation

_ICON = os.path.join(os.path.dirname(__file__), "..", "resources", "icons", "Mesh.svg")

# Tet face index tuples — each tet (n0,n1,n2,n3) has these 4 triangular faces.
_TET_FACES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))


def _add(obj, type_, name, group, tip, value=None):
    if not hasattr(obj, name):
        obj.addProperty(type_, name, group, tip)
        if value is not None:
            setattr(obj, name, value)


# ---------------------------------------------------------------------------
# MSH v2.2 parser — surfaces + volumes
# ---------------------------------------------------------------------------

def _parse_msh2(path):
    """Parse a Gmsh MSH v2.2 file and return mesh data for display.

    Returns (verts, surf_tris, surf_groups, vol_tris, vol_groups):
      verts:       list of (x,y,z) tuples — compact vertex array (model units = mm)
      surf_tris:   list of (i0,i1,i2) 0-based indices into verts
      surf_groups: list of int — physical-group attribute per surf_tri
      vol_tris:    list of (i0,i1,i2) — one entry per triangular face of each tet element
      vol_groups:  list of int — physical-group attribute per vol_tri

    Surface triangle Gmsh element types: 2 (lin-tri, 3 nodes), 9 (quad-tri, 6 nodes →
    only 3 corner nodes used).
    Tet element types: 4 (lin-tet, 4 nodes), 11 (quad-tet, 10 nodes → only 4 corner nodes).
    Volume tets are decomposed into 4 triangular faces for display.
    """
    _SURF_TYPES = {2: 3, 9: 3}    # etype → n_corner_nodes
    _VOL_TYPES  = {4: 4, 11: 4}   # etype → n_corner_nodes

    node_coords = {}   # node_id (1-based) → (x, y, z)
    raw_surf = []      # (phys_group, (n0, n1, n2)) with 1-based node IDs
    raw_vols = []      # (phys_group, (n0, n1, n2, n3)) with 1-based node IDs

    with open(path, "r", errors="replace") as f:
        section = None
        skip_next = False
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("$End"):
                section = None
                skip_next = False
                continue
            if line.startswith("$"):
                section = line
                skip_next = True
                continue
            if skip_next:
                skip_next = False
                continue

            if section == "$Nodes":
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        nid = int(parts[0])
                        node_coords[nid] = (
                            float(parts[1]), float(parts[2]), float(parts[3])
                        )
                    except (ValueError, IndexError):
                        pass

            elif section == "$Elements":
                parts = line.split()
                if len(parts) < 4:
                    continue
                try:
                    etype  = int(parts[1])
                    n_tags = int(parts[2])
                    phys   = int(parts[3]) if n_tags >= 1 else 0
                    ns     = 3 + n_tags   # index of first node ID column

                    if etype in _SURF_TYPES:
                        nc = _SURF_TYPES[etype]
                        if len(parts) < ns + nc:
                            continue
                        raw_surf.append((phys,
                            tuple(int(parts[ns + i]) for i in range(nc))))

                    elif etype in _VOL_TYPES:
                        nc = _VOL_TYPES[etype]
                        if len(parts) < ns + nc:
                            continue
                        raw_vols.append((phys,
                            tuple(int(parts[ns + i]) for i in range(nc))))
                except (ValueError, IndexError):
                    pass

    if not raw_surf and not raw_vols:
        return [], [], [], [], []

    # Build compact vertex array from nodes used by either surface tris or tets.
    used_ids = set()
    for _, tri in raw_surf:
        used_ids.update(tri)
    for _, tet in raw_vols:
        used_ids.update(tet)

    id_to_idx = {}
    verts = []
    for nid in sorted(used_ids):
        if nid in node_coords:
            id_to_idx[nid] = len(verts)
            verts.append(node_coords[nid])

    if not verts:
        return [], [], [], [], []

    surf_tris, surf_groups = [], []
    for grp, tri in raw_surf:
        try:
            surf_tris.append((id_to_idx[tri[0]], id_to_idx[tri[1]], id_to_idx[tri[2]]))
            surf_groups.append(grp)
        except KeyError:
            pass

    vol_tris, vol_groups = [], []
    for grp, tet in raw_vols:
        for a, b, c in _TET_FACES:
            try:
                vol_tris.append((id_to_idx[tet[a]], id_to_idx[tet[b]], id_to_idx[tet[c]]))
                vol_groups.append(grp)
            except KeyError:
                pass

    return verts, surf_tris, surf_groups, vol_tris, vol_groups


def _parse_msh2_surface(path):
    """Thin wrapper — returns only the surface-triangle portion of _parse_msh2."""
    verts, surf_tris, surf_groups, _, _ = _parse_msh2(path)
    return verts, surf_tris, surf_groups


# ---------------------------------------------------------------------------
# Physical-group attribute → RGB color, split by mesh dimension
# ---------------------------------------------------------------------------

def _build_surf_color_map(doc):
    """Return {attr_int: (r,g,b)} for 2-D surface physical-group attributes."""
    from features import (find_airbox, find_lumped_ports, find_wave_ports,
                          find_conductor_groups)
    m = {}
    airbox = find_airbox(doc)
    gc = getattr(airbox, "GroupColor", None) if airbox else None
    m[2] = tuple(gc[:3]) if gc else (0.40, 0.60, 1.00)   # PEC outer — blue
    m[3] = (0.75, 0.30, 0.75)                              # PMC outer — purple
    m[4] = (0.00, 0.65, 0.65)                              # Absorbing — teal
    _PORT = (1.00, 0.50, 0.00)
    for p in find_lumped_ports(doc):
        attr = getattr(p, "MeshAttribute", 10 + getattr(p, "PortIndex", 0))
        try:    sc = p.ViewObject.ShapeColor; m[attr] = tuple(sc[:3])
        except: m[attr] = _PORT
    for p in find_wave_ports(doc):
        attr = getattr(p, "MeshAttribute", 10 + getattr(p, "PortIndex", 0))
        m.setdefault(attr, _PORT)
    for grp in find_conductor_groups(doc):
        attr = getattr(grp, "MeshAttribute", 100)
        gc = getattr(grp, "GroupColor", None)
        m[attr] = tuple(gc[:3]) if gc else (0.722, 0.451, 0.200)
    return m


def _build_vol_color_map(doc):
    """Return {attr_int: (r,g,b)} for 3-D volume physical-group attributes."""
    from features import find_airbox, find_dielectric_groups
    m = {}
    airbox = find_airbox(doc)
    gc = getattr(airbox, "GroupColor", None) if airbox else None
    m[1] = tuple(gc[:3]) if gc else (0.40, 0.60, 1.00)    # airbox interior — blue
    for grp in find_dielectric_groups(doc):
        attr = getattr(grp, "MeshAttribute", None)
        if attr is not None:
            gc = getattr(grp, "GroupColor", None)
            m[attr] = tuple(gc[:3]) if gc else (0.204, 0.788, 0.204)
    return m


def _build_surf_attr_labels(doc):
    """Return {attr_int: label_str} for 2-D surface physical-group attributes."""
    from features import (find_lumped_ports, find_wave_ports, find_conductor_groups)
    labels = {
        2: "Airbox outer (PEC boundary)",
        3: "Outer boundary (PMC)",
        4: "Outer boundary (Absorbing)",
    }
    for p in find_lumped_ports(doc):
        attr = getattr(p, "MeshAttribute", 10 + getattr(p, "PortIndex", 0))
        labels[attr] = f"Lumped port {getattr(p, 'PortIndex', attr - 10)}"
    for p in find_wave_ports(doc):
        attr = getattr(p, "MeshAttribute", 10 + getattr(p, "PortIndex", 0))
        labels.setdefault(attr, f"Wave port {getattr(p, 'PortIndex', attr - 10)}")
    for grp in find_conductor_groups(doc):
        attr = getattr(grp, "MeshAttribute", None)
        if attr is not None:
            labels[attr] = f"Conductor: {grp.Label}"
    return labels


def _build_vol_attr_labels(doc):
    """Return {attr_int: label_str} for 3-D volume physical-group attributes."""
    from features import find_dielectric_groups
    labels = {1: "Airbox (interior volume)"}
    for grp in find_dielectric_groups(doc):
        attr = getattr(grp, "MeshAttribute", None)
        if attr is not None:
            labels[attr] = f"Dielectric: {grp.Label}"
    return labels


# ---------------------------------------------------------------------------
# Feature proxy
# ---------------------------------------------------------------------------

class PalaceMesh:
    def __init__(self, obj):
        obj.Proxy = self
        self._init_properties(obj)

    def _init_properties(self, obj):
        _add(obj, "App::PropertyFileIncluded", "MeshFile", "Mesh",
             "Embedded Gmsh .msh file (generated automatically)", "")
        _add(obj, "App::PropertyFileIncluded", "GeometryFile", "Mesh",
             "Embedded STEP snapshot of the meshed geometry (generated automatically)", "")
        _add(obj, "App::PropertyFloat", "MeshCharacteristicLengthMax", "Mesh",
             "Gmsh global maximum element size in model units (0 = Gmsh default)", 0.0)
        _add(obj, "App::PropertyFloat", "MeshCharacteristicLengthMin", "Mesh",
             "Gmsh global minimum element size in model units (0 = Gmsh default)", 0.0)
        _add(obj, "App::PropertyFloat", "MeshConductorSize", "Refinement",
             "Element size (mm) right at conductor and dielectric surfaces "
             "(SizeMin of Distance/Threshold field). 0 = disabled.", 0.0)
        _add(obj, "App::PropertyFloat", "MeshPortSize", "Refinement",
             "Element size (mm) right at wave/lumped port faces and integration edges. "
             "0 = disabled.", 0.0)
        _add(obj, "App::PropertyFloat", "MeshRefineDistance", "Refinement",
             "Distance (mm) over which refinement transitions to global cl_max. "
             "0 = auto (30% of model extent).", 0.0)
        _add(obj, "App::PropertyFloat", "MeshQualityWarnThreshold", "Refinement",
             "Minimum acceptable tetrahedron quality (Gmsh minSICN, 0-1) before the "
             "post-mesh quality check warns. 0 = disabled.", 0.1)
        _add(obj, "App::PropertyIntegerList", "HiddenSurfAttributes", "Mesh",
             "Surface physical-group attribute numbers hidden from the 3D view", [])
        _add(obj, "App::PropertyIntegerList", "HiddenVolAttributes", "Mesh",
             "Volume physical-group attribute numbers hidden from the 3D view", [])

    def execute(self, obj):
        # obj.Mesh only exists on Mesh::FeaturePython; guard for old App::FeaturePython
        # documents opened after a code upgrade.
        if not hasattr(obj, "Mesh"):
            return
        mesh_file = getattr(obj, "MeshFile", "")
        if not mesh_file or not os.path.isfile(mesh_file):
            return
        try:
            import Mesh as MeshMod
            verts, surf_tris, surf_groups, vol_tris, vol_groups = _parse_msh2(mesh_file)
            if not verts:
                return
            # Store parsed data on the VP proxy so updateData("Mesh") can colour it
            # without parsing the file a second time.
            if FreeCAD.GuiUp and obj.ViewObject and obj.ViewObject.Proxy:
                vp = obj.ViewObject.Proxy
                vp._parsed = (verts, surf_tris, surf_groups, vol_tris, vol_groups)
                vp._known_surf_attrs = sorted(set(surf_groups))
                vp._known_vol_attrs  = sorted(set(vol_groups))
            # Setting obj.Mesh triggers the C++ Mesh ViewProvider repaint, which in
            # turn fires Python VP updateData("Mesh").
            obj.Mesh = MeshMod.Mesh(
                [(verts[t[0]], verts[t[1]], verts[t[2]]) for t in surf_tris]
            )
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"Palace: Could not build mesh display: {exc}\n"
            )

    def onChanged(self, obj, prop):
        pass

    def onDocumentRestored(self, obj):
        self._init_properties(obj)
        from palace.embedded_files import migrate_legacy_string_property, legacy_sibling_path
        migrate_legacy_string_property(
            obj, "MeshFile", "Mesh",
            "Embedded Gmsh .msh file (generated automatically)",
            legacy_hint=legacy_sibling_path(obj.Document, "mesh.msh"),
        )
        migrate_legacy_string_property(
            obj, "GeometryFile", "Mesh",
            "Embedded STEP snapshot of the meshed geometry (generated automatically)",
            legacy_hint=legacy_sibling_path(obj.Document, "geometry.step"),
        )

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ---------------------------------------------------------------------------
# View provider
# ---------------------------------------------------------------------------

class ViewProviderPalaceMesh:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        from pivy import coin

        self.ViewObject = vobj
        self.Object = vobj.Object

        # --- Shared geometry nodes ---
        self._coords   = coin.SoCoordinate3()
        self._face_set = coin.SoIndexedFaceSet()

        self._mat_bind = coin.SoMaterialBinding()
        self._mat_bind.value.setValue(coin.SoMaterialBinding.PER_FACE)
        self._material = coin.SoMaterial()

        hints = coin.SoShapeHints()
        hints.shapeType.setValue(coin.SoShapeHints.UNKNOWN_SHAPE_TYPE)
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        hints.creaseAngle.setValue(0.0)

        surf_ds = coin.SoDrawStyle()
        surf_ds.style.setValue(coin.SoDrawStyle.LINES)
        surf_ds.lineWidth.setValue(1.0)

        # --- "Colored Wireframe" separator: surface triangles ---
        surf_sep = coin.SoSeparator()
        surf_sep.addChild(hints)
        surf_sep.addChild(surf_ds)
        surf_sep.addChild(self._mat_bind)
        surf_sep.addChild(self._material)
        surf_sep.addChild(self._coords)
        surf_sep.addChild(self._face_set)
        self._sep_colored = surf_sep
        vobj.addDisplayMode(surf_sep, "Colored Wireframe")

        # --- Volume (tet) geometry nodes ---
        self._vol_coords   = coin.SoCoordinate3()
        self._vol_face_set = coin.SoIndexedFaceSet()

        self._vol_mat_bind = coin.SoMaterialBinding()
        self._vol_mat_bind.value.setValue(coin.SoMaterialBinding.PER_FACE)
        self._vol_material = coin.SoMaterial()

        vol_ds = coin.SoDrawStyle()
        vol_ds.style.setValue(coin.SoDrawStyle.LINES)
        vol_ds.lineWidth.setValue(0.5)   # thinner than surface lines

        vol_sep = coin.SoSeparator()
        vol_sep.addChild(hints)           # shared SoShapeHints — valid coin3D DAG
        vol_sep.addChild(vol_ds)
        vol_sep.addChild(self._vol_mat_bind)
        vol_sep.addChild(self._vol_material)
        vol_sep.addChild(self._vol_coords)
        vol_sep.addChild(self._vol_face_set)
        self._vol_sep = vol_sep

        # --- "With Volumes" separator: surface + tet interior ---
        with_vol_sep = coin.SoSeparator()
        with_vol_sep.addChild(surf_sep)   # reuse surface sep (coin3D DAG is fine)
        with_vol_sep.addChild(vol_sep)
        vobj.addDisplayMode(with_vol_sep, "With Volumes")

        # Activate our colored mode as the default for new objects.
        try:
            vobj.DisplayMode = "Colored Wireframe"
        except Exception:
            pass

        # If execute() ran before attach() (fresh object created while generate_mesh
        # blocked the Qt event loop), _parsed is already set — paint the mesh now.
        if getattr(self, "_parsed", None):
            self._update_colors()

    def updateData(self, obj, prop):
        if prop == "Mesh":
            self._update_colors()

    def _update_colors(self):
        if not hasattr(self, "_coords"):
            return

        parsed = getattr(self, "_parsed", None)
        if not parsed:
            # Fallback: parse from file (used on document restore when _parsed not yet set).
            path = getattr(self.Object, "MeshFile", "")
            if not path or not os.path.isfile(path):
                return
            verts, surf_tris, surf_groups, vol_tris, vol_groups = _parse_msh2(path)
            if not verts:
                return
        else:
            verts, surf_tris, surf_groups, vol_tris, vol_groups = parsed

        try:
            surf_map    = _build_surf_color_map(self.Object.Document)
            vol_map     = _build_vol_color_map(self.Object.Document)
            _DEFAULT    = (0.50, 0.50, 0.50)
            hidden_surf = set(getattr(self.Object, "HiddenSurfAttributes", []) or [])
            hidden_vol  = set(getattr(self.Object, "HiddenVolAttributes",  []) or [])

            # Surface triangles — full-brightness, 1px lines
            self._coords.point.setValues(0, len(verts), verts)
            s_idx, s_colors = [], []
            for i, tri in enumerate(surf_tris):
                if surf_groups[i] in hidden_surf:
                    continue
                s_idx += [tri[0], tri[1], tri[2], -1]
                s_colors.append(surf_map.get(surf_groups[i], _DEFAULT))
            self._face_set.coordIndex.setValues(0, len(s_idx), s_idx)
            self._face_set.coordIndex.setNum(len(s_idx))
            self._material.diffuseColor.setValues(0, len(s_colors), s_colors)
            self._material.diffuseColor.setNum(len(s_colors))

            # Volume tet faces — dimmed (60%), 0.5px lines
            if vol_tris and hasattr(self, "_vol_coords"):
                self._vol_coords.point.setValues(0, len(verts), verts)
                v_idx, v_colors = [], []
                for i, tri in enumerate(vol_tris):
                    if vol_groups[i] in hidden_vol:
                        continue
                    v_idx += [tri[0], tri[1], tri[2], -1]
                    r, g, b = vol_map.get(vol_groups[i], _DEFAULT)
                    v_colors.append((r * 0.60, g * 0.60, b * 0.60))
                self._vol_face_set.coordIndex.setValues(0, len(v_idx), v_idx)
                self._vol_face_set.coordIndex.setNum(len(v_idx))
                self._vol_material.diffuseColor.setValues(0, len(v_colors), v_colors)
                self._vol_material.diffuseColor.setNum(len(v_colors))

        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"Palace: mesh color update failed: {exc}\n"
            )

    def onChanged(self, vobj, prop):
        return

    def getDisplayModes(self, vobj):
        return ["Colored Wireframe", "With Volumes"]

    def getDefaultDisplayMode(self):
        return "Colored Wireframe"

    def setDisplayMode(self, mode):
        return mode

    def getIcon(self):
        return _ICON

    def setEdit(self, vobj, mode=0):
        from panels.mesh_panel import MeshPanel
        from panels import show_palace_panel
        show_palace_panel(MeshPanel(vobj.Object))
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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_palace_mesh(doc):
    """Return the existing PalaceMesh object, or create one if absent."""
    from features import find_palace_mesh
    existing = find_palace_mesh(doc)
    if existing:
        return existing
    # Mesh::FeaturePython: C++ Mesh VP drives the viewport-refresh cycle when
    # obj.Mesh is set in execute(); Python VP adds the colored display modes.
    obj = doc.addObject("Mesh::FeaturePython", "PalaceMesh")
    PalaceMesh(obj)
    if obj.ViewObject is not None:
        ViewProviderPalaceMesh(obj.ViewObject)
        # attach() already set DisplayMode = "Colored Wireframe"; this is a belt-and-
        # suspenders call for FreeCAD versions where attach() fires before the scene
        # is fully connected.
        try:
            obj.ViewObject.DisplayMode = "Colored Wireframe"
        except Exception:
            pass
    add_to_simulation(doc, obj)
    doc.recompute()
    return obj
