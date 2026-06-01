"""
Mesh generation for Palace using the Gmsh Python API.

Workflow
--------
1. Export the airbox solid (and any extra solid bodies hosting port faces) to STEP.
2. Import into Gmsh via the OCC kernel.
3. Match each port's selected FreeCAD faces to the closest Gmsh surface entity
   by comparing face bounding-box centres.
4. Assign Gmsh physical groups:
     - Volume 1          → domain (the airbox interior)
     - Surface 2         → outer boundary (faces not claimed by any port)
     - Surface 10+i      → port i
5. Generate a 3D tetrahedral mesh and export as .msh2.
"""

import math
import os
import sys
import tempfile
import FreeCAD


# ---------------------------------------------------------------------------
# Gmsh import helper
# ---------------------------------------------------------------------------

def _gmsh():
    """
    Import the Gmsh Python API.

    FreeCAD 1.0 ships gmsh.exe and gmsh.dll but NOT the Python wrapper (gmsh.py).
    If this raises RuntimeError, install the API once with:

        "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" -m pip install gmsh

    Then restart FreeCAD.
    """
    try:
        import gmsh
        return gmsh
    except ImportError:
        pass

    # Build a list of candidate directories to search.
    home = FreeCAD.getHomePath()
    candidates = [
        os.path.join(home, "bin"),
        os.path.join(home, "lib"),
        os.path.join(home, "bin", "Lib", "site-packages"),
        home,
    ]
    # Include the user site-packages directory (where pip --user installs go).
    try:
        import site as _site
        user_sp = _site.getusersitepackages()
        if user_sp not in candidates:
            candidates.insert(0, user_sp)
    except Exception:
        pass

    for candidate in candidates:
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)
        try:
            import gmsh
            return gmsh
        except ImportError:
            continue

    python_exe = os.path.join(home, "bin", "python.exe")
    raise RuntimeError(
        "The Gmsh Python API (gmsh.py) is not installed.\n\n"
        "FreeCAD ships gmsh.exe and gmsh.dll but not the Python wrapper.\n"
        "Fix: open a terminal (as Administrator if FreeCAD is in Program Files)\n"
        "and run:\n\n"
        f'    "{python_exe}" -m pip install gmsh\n\n'
        "Then restart FreeCAD and try Generate & Run again.\n"
        "The pip package bundles its own gmsh library and will not\n"
        "conflict with FreeCAD's gmsh.dll."
    )


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _face_centre_mm(link_obj, subname):
    """Return the centre-of-mass of a FreeCAD face in millimetres."""
    face = link_obj.Shape.getElement(subname)
    c = face.CenterOfMass          # FreeCAD.Vector, already in mm
    return (c.x, c.y, c.z)


def _gmsh_bbox_centre(gmsh, dim, tag):
    xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(dim, tag)
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def _dist_sq(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _closest_surface(gmsh, target, all_surfaces, used):
    best_tag, best_d = None, float("inf")
    for _, tag in all_surfaces:
        if tag in used:
            continue
        d = _dist_sq(_gmsh_bbox_centre(gmsh, 2, tag), target)
        if d < best_d:
            best_d, best_tag = d, tag
    return best_tag


def _coplanar_outer_surfaces(gmsh, normal, d, all_surfaces, used_surface_tags, tol=0.01):
    """Return all single-volume-adjacent Gmsh surface tags that lie on the given plane.

    normal : (nx, ny, nz) unit normal of the plane
    d      : signed plane distance from origin (normal · x == d for points on plane)
    tol    : coplanarity threshold in mm
    """
    result = []
    for _, tag in all_surfaces:
        if tag in used_surface_tags:
            continue
        up_vols, _ = gmsh.model.getAdjacencies(2, tag)
        if len(up_vols) != 1:
            continue
        cx, cy, cz = _gmsh_bbox_centre(gmsh, 2, tag)
        dist = abs(normal[0] * cx + normal[1] * cy + normal[2] * cz - d)
        if dist < tol:
            result.append(tag)
    return result


def _faces_within_freecad_face(gmsh, fc_face, all_surfaces, used_surface_tags, tol=0.01):
    """Return single-volume-adjacent Gmsh surfaces that lie within fc_face's 2D extent.

    After occ.fragment() splits a FreeCAD face into several Gmsh sub-surfaces, this
    collects all sub-surfaces whose bbox centre is:
      1. Coplanar with fc_face (within tol mm of the face's plane equation).
      2. Within the face boundary — tested via distToShape (BRepExtrema_DistShapeShape),
         which returns 0 for interior points and a positive distance for points outside
         the face boundary, even when coplanar.

    Note: fc_face.isInside() is NOT used here. That method calls BRepClass3d_SolidClassifier
    which is designed for 3D closed-solid containment and gives unreliable results for
    naked Part.Face objects.

    Unlike _coplanar_outer_surfaces this does NOT expand beyond the selected face,
    so multiple ports or BCs on the same plane cannot bleed into each other.
    """
    import Part as _Part
    n = fc_face.normalAt(0, 0)
    n.normalize()
    com = fc_face.CenterOfMass
    plane_d = n.x * com.x + n.y * com.y + n.z * com.z

    result = []
    for _, tag in all_surfaces:
        if tag in used_surface_tags:
            continue
        up_vols, _ = gmsh.model.getAdjacencies(2, tag)
        if len(up_vols) != 1:
            continue
        cx, cy, cz = _gmsh_bbox_centre(gmsh, 2, tag)
        if abs(n.x * cx + n.y * cy + n.z * cz - plane_d) >= tol:
            continue
        dist, _, _ = fc_face.distToShape(_Part.Vertex(FreeCAD.Vector(cx, cy, cz)))
        if dist > tol:
            continue
        result.append(tag)
    return result


# ---------------------------------------------------------------------------
# Collect unique parent objects referenced by ports
# ---------------------------------------------------------------------------

def _port_shape_parents(doc):
    parents = set()
    for obj in doc.Objects:
        if hasattr(obj, "PortFaces"):
            for link_obj, _ in (obj.PortFaces or []):
                parents.add(link_obj)
    return parents


# ---------------------------------------------------------------------------
# Material group helpers
# ---------------------------------------------------------------------------

def _collect_material_bodies(doc):
    """
    Return two lists of (group_obj, freecad_solid) pairs — one for dielectric
    groups and one for conductor groups.  Only solids that have a Shape are
    included.
    """
    diel_bodies = []
    cond_bodies = []
    for obj in doc.Objects:
        if hasattr(obj, "Permittivity") and hasattr(obj, "MeshAttribute"):
            for child in (obj.Group if hasattr(obj, "Group") else []):
                if hasattr(child, "Shape"):
                    diel_bodies.append((obj, child))
        elif hasattr(obj, "ConductorType") and hasattr(obj, "MeshAttribute"):
            for child in (obj.Group if hasattr(obj, "Group") else []):
                if hasattr(child, "Shape"):
                    cond_bodies.append((obj, child))
    return diel_bodies, cond_bodies


def _match_vol_by_com(gmsh, solid, all_vol_tags):
    """Find the Gmsh volume whose bbox centre is closest to the FreeCAD solid's COM."""
    com = solid.Shape.CenterOfMass   # FreeCAD.Vector in mm
    target = (com.x, com.y, com.z)
    best_tag, best_d = None, float("inf")
    for tag in all_vol_tags:
        c = _gmsh_bbox_centre(gmsh, 3, tag)
        d = _dist_sq(c, target)
        if d < best_d:
            best_d, best_tag = d, tag
    return best_tag


def _assign_material_physical_groups(gmsh, log_fn,
                                     airbox_solid,
                                     diel_bodies,
                                     all_vol_tags_before_frag):
    """
    Fragment airbox + dielectric solids and assign Gmsh physical groups:
      - Physical volume 1          → airbox background (vacuum)
      - Physical volume dg.Mesh... → each dielectric domain

    Conductor solid bodies are NOT included here.  Their surfaces are imported
    as standalone 2D BRep surfaces later (see _import_conductor_solid_faces) and
    embedded via the combined port+conductor occ.fragment() pass.  This ensures
    no conductor volume ever enters the mesh, avoiding the MFEM STable3D crash
    that occurs when boundary triangles have no adjacent element.
    """
    airbox_vtag = _match_vol_by_com(gmsh, airbox_solid, all_vol_tags_before_frag)
    if airbox_vtag is None:
        raise RuntimeError("Could not match airbox volume in Gmsh after import.")

    input_dim_tags = [(3, airbox_vtag)]
    diel_input_indices = []

    for grp, solid in diel_bodies:
        tag = _match_vol_by_com(gmsh, solid, all_vol_tags_before_frag)
        if tag is None:
            log_fn(f"Palace: WARNING — could not match dielectric solid '{solid.Label}' "
                   "in Gmsh; it will be skipped.\n")
            diel_input_indices.append((grp, None))
            continue
        diel_input_indices.append((grp, len(input_dim_tags)))
        input_dim_tags.append((3, tag))

    log_fn(f"Palace: Fragmenting {len(input_dim_tags)} volume(s) for material regions…\n")
    out_dim_tags, out_map = gmsh.model.occ.fragment(input_dim_tags, [])
    gmsh.model.occ.synchronize()

    # occ.fragment() puts ALL output volumes in outDimTagsMap[0] for the airbox
    # (every carved sub-volume geometrically coincides with the original solid).
    # Subtract dielectric volumes so only true background ends up in PG 1.
    diel_attr_volumes = {}
    for grp, idx in diel_input_indices:
        if idx is None:
            continue
        vol_tags = [t for d, t in out_map[idx] if d == 3]
        attr = grp.MeshAttribute
        if attr not in diel_attr_volumes:
            diel_attr_volumes[attr] = (grp, [])
        diel_attr_volumes[attr][1].extend(vol_tags)

    non_bg_vol_tags = set()
    for _, vol_tags in diel_attr_volumes.values():
        non_bg_vol_tags.update(vol_tags)

    bg_vol_tags = [t for d, t in out_map[0] if d == 3 and t not in non_bg_vol_tags]
    gmsh.model.addPhysicalGroup(3, bg_vol_tags, 1)
    gmsh.model.setPhysicalName(3, 1, "Airbox_Background")
    log_fn(f"Palace: Airbox background → volume tags {bg_vol_tags} (attr 1)\n")

    for attr, (grp, vol_tags) in diel_attr_volumes.items():
        gmsh.model.addPhysicalGroup(3, vol_tags, attr)
        gmsh.model.setPhysicalName(3, attr, f"Dielectric_{attr}")
        log_fn(f"Palace: Dielectric '{grp.Label}' → volume tags {vol_tags} "
               f"(attr {attr})\n")


def _import_conductor_solid_faces(gmsh, cond_bodies, log_fn, skip_normals=None):
    """
    For each conductor solid body, export every face as a BRep 2-D surface and
    import it into Gmsh as a standalone OCC surface.

    Returns list of (cg_obj, [gmsh_surf_tags]) — one entry per conductor group,
    aggregating all faces from all child solids in that group.  Surfaces are
    imported but NOT yet fragmented; call occ.fragment() afterwards.

    skip_normals : list of FreeCAD.Vector, optional
        Conductor faces whose outward normal is parallel to any of these
        (|dot product| > 0.99) are skipped.  Pass port face normals to
        exclude conductor end faces that share a plane with a port face,
        which would otherwise create triple-edges causing an MFEM crash.
    """
    import Part
    result = []
    for grp, solid in cond_bodies:
        surf_tags_for_grp = []
        for face_idx, face in enumerate(solid.Shape.Faces):
            if skip_normals:
                try:
                    fn = face.normalAt(0, 0)
                    fn.normalize()
                    if any(abs(fn.dot(pn)) > 0.99 for pn in skip_normals):
                        log_fn(f"Palace: Skipping conductor face {face_idx} of "
                               f"'{solid.Label}' (end face parallel to a port)\n")
                        continue
                except Exception:
                    pass
            brep_path = tempfile.mktemp(suffix=".brep")
            try:
                face.exportBrep(brep_path)
                surf_before = {t for _, t in gmsh.model.getEntities(2)}
                gmsh.model.occ.importShapes(brep_path)
                gmsh.model.occ.synchronize()
                new_tags = [t for _, t in gmsh.model.getEntities(2)
                            if t not in surf_before]
                surf_tags_for_grp.extend(new_tags)
            except Exception as exc:
                log_fn(f"Palace: WARNING — could not import face {face_idx} of "
                       f"conductor solid '{solid.Label}': {exc}\n")
            finally:
                try:
                    os.unlink(brep_path)
                except OSError:
                    pass
        if surf_tags_for_grp:
            log_fn(f"Palace: Conductor '{grp.Label}' solid '{solid.Label}' → "
                   f"{len(surf_tags_for_grp)} face surface(s) imported\n")
        result.append((grp, surf_tags_for_grp))
    return result


# ---------------------------------------------------------------------------
# Volume physical-group update after a secondary occ.fragment() pass
# ---------------------------------------------------------------------------

def _update_vol_physical_groups_after_fragment(gmsh, old_vol_tags, out_map,
                                               vol_pg_info, log_fn=None):
    """Fix up volume physical groups after occ.fragment() splits volumes.

    occ.synchronize() called after occ.fragment() silently removes physical
    groups whose member entity tags are invalidated by the fragment.  To work
    around this, the caller must snapshot vol_pg_info BEFORE calling fragment
    and pass it here so the rebuild uses pre-fragment data, not post-sync data.

    Parameters
    ----------
    old_vol_tags : list of int
        Volume entity tags (Gmsh IDs) passed as objects to fragment(), in order.
        out_map[i] gives the post-fragment entities for old_vol_tags[i].
    out_map : list of list of (dim, tag)
        outDimTagsMap from occ.fragment() (objects first, then tools).
    vol_pg_info : dict
        Pre-fragment snapshot: pg_tag → (name, frozenset_of_old_entity_tags).
        Must be captured BEFORE occ.fragment()+synchronize().
    log_fn : callable, optional
        Called with diagnostic messages.
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    if not vol_pg_info:
        return

    vol_to_pg = {vtag: pg_tag
                 for pg_tag, (_, vols) in vol_pg_info.items()
                 for vtag in vols}

    pg_to_new_vols = {}
    for i, old_vtag in enumerate(old_vol_tags):
        pg_tag = vol_to_pg.get(old_vtag)
        new_vtags = [t for d, t in out_map[i] if d == 3]
        if pg_tag is not None:
            _log(f"Palace: [vol-PG rebuild]   vol {old_vtag} (PG {pg_tag}) "
                 f"→ new {new_vtags}\n")
            pg_to_new_vols.setdefault(pg_tag, []).extend(new_vtags)

    # Remove any surviving volume PGs (some may have already been auto-removed
    # by synchronize(); removePhysicalGroups is a no-op for non-existent ones).
    existing_pgs = {pg_tag for _, pg_tag in gmsh.model.getPhysicalGroups(3)}
    gmsh.model.removePhysicalGroups(
        [(3, pg_tag) for pg_tag in vol_pg_info if pg_tag in existing_pgs])

    for pg_tag, (name, _) in vol_pg_info.items():
        new_vols = pg_to_new_vols.get(pg_tag, [])
        _log(f"Palace: [vol-PG rebuild] Rebuilding PG {pg_tag} '{name}' "
             f"with vols {new_vols}\n")
        if new_vols:
            gmsh.model.addPhysicalGroup(3, new_vols, pg_tag)
            gmsh.model.setPhysicalName(3, pg_tag, name)
        else:
            _log(f"Palace: WARNING — vol PG {pg_tag} '{name}' lost all "
                 "entities after port fragment; check geometry.\n")


# ---------------------------------------------------------------------------
# Port boundary transfinite constraints
# ---------------------------------------------------------------------------

def _apply_port_transfinite(gmsh, lumped_surf_tags, cl_max_mm):
    """
    Set transfinite constraints on all boundary curves of lumped port faces.

    Two requirements for Palace's coaxial ('+R') lumped port:
      1. Bounding-ball check: Palace's CoaxialElementData calls
         GetBoundingBall(), which needs diametrically-opposite nodes on the
         outer circle.  Without an even-N transfinite, the default mesh may
         have no antipodal pair and the check fails.
      2. Area accuracy: Palace computes ra = sqrt(R_outer² − A_mesh/π).
         With too few nodes on either boundary circle the triangulated area
         diverges from the true annular area and ra (and Rs) are wrong.
         For order-2 (curved) elements the area is exact regardless of N,
         but N must still be even to satisfy the bounding-ball requirement.

    Target segment length = cl_max_mm / 2 (finer than the volume mesh).
    N is rounded up to the nearest even integer (≥ 8).
    """
    MIN_N = 8
    for surf_tag in lumped_surf_tags:
        for _, ctag in gmsh.model.getBoundary([(2, surf_tag)]):
            xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(1, abs(ctag))
            # For a full circle of radius R the bounding box spans 2R in each
            # in-plane direction, so circumference ≈ π × max_extent.
            max_extent = max(xmax - xmin, ymax - ymin, zmax - zmin)
            circumference = math.pi * max_extent
            target_seg = max(cl_max_mm / 2.0, 0.05)
            n = max(MIN_N, math.ceil(circumference / target_seg))
            n = n if n % 2 == 0 else n + 1   # must be even for antipodal nodes
            gmsh.model.mesh.setTransfiniteCurve(abs(ctag), n + 1)


# ---------------------------------------------------------------------------
# Geometry-aware mesh refinement fields
# ---------------------------------------------------------------------------

def _apply_refinement_fields(
        gmsh, cl_max,
        cond_data, port_surf_tags, integ_curve_tags, diel_data,
        global_cond_size, port_size, refine_dist, log_fn,
        imp_data=()):
    """Add Gmsh Distance+Threshold fields to refine mesh near geometric features.

    Per-object sizing: each ConductorGroup, DielectricGroup, and
    ImpedanceBoundary may carry a MeshSize property; when non-zero it
    overrides the group default for that object.  A separate
    Distance+Threshold field pair is created per object so that the Min
    background field yields the locally finest constraint.

    Zones:
    - Per conductor group      → SizeMin = group.MeshSize or global_cond_size
    - Per dielectric group     → SizeMin = group.MeshSize or global_cond_size
    - Per impedance boundary   → SizeMin = group.MeshSize or global_cond_size
    - Port faces (wave+lumped) → SizeMin = port_size
    - Integration edge curves  → SizeMin = port_size / 2

    cond_data / diel_data / imp_data : list of (group_obj, [gmsh_surf_tag, ...])
    """
    # Estimate reference size and transition distance from model extent.
    xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
    max_extent = max(xmax - xmin, ymax - ymin, zmax - zmin)
    cl_max_ref = cl_max if cl_max > 0.0 else max(max_extent / 15.0, 0.01)
    dist_max   = refine_dist if refine_dist > 0.0 else max_extent * 0.3

    thresh_ids = []

    def _add_distance_threshold(surf_tags, curve_tags, size_min, d_max):
        if size_min <= 0.0:
            return
        all_surfs  = [t for t in surf_tags  if t]
        all_curves = [t for t in curve_tags if t]
        if not all_surfs and not all_curves:
            return
        fid = gmsh.model.mesh.field.add("Distance")
        if all_surfs:
            try:
                gmsh.model.mesh.field.setNumbers(fid, "SurfacesList", all_surfs)
            except Exception:
                gmsh.model.mesh.field.setNumbers(fid, "FacesList", all_surfs)
        if all_curves:
            gmsh.model.mesh.field.setNumbers(fid, "CurvesList", all_curves)
        tid = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(tid, "InField",  fid)
        gmsh.model.mesh.field.setNumber(tid, "SizeMin",  size_min)
        gmsh.model.mesh.field.setNumber(tid, "SizeMax",  cl_max_ref)
        gmsh.model.mesh.field.setNumber(tid, "DistMin",  0.0)
        gmsh.model.mesh.field.setNumber(tid, "DistMax",  d_max)
        thresh_ids.append(tid)

    # Per-conductor group: individual field so sizes compose via Min
    for grp, tags in cond_data:
        if not tags:
            continue
        size     = (getattr(grp, "MeshSize",          0.0) or 0.0) or global_cond_size
        grp_dist = (getattr(grp, "MeshRefineDistance", 0.0) or 0.0) or dist_max
        _add_distance_threshold(tags, [], size, grp_dist)

    # Per-dielectric group: individual field
    for grp, tags in diel_data:
        if not tags:
            continue
        size     = (getattr(grp, "MeshSize",          0.0) or 0.0) or global_cond_size
        grp_dist = (getattr(grp, "MeshRefineDistance", 0.0) or 0.0) or dist_max
        _add_distance_threshold(tags, [], size, grp_dist)

    # Per impedance boundary: individual field (does NOT participate in the
    # port_size zone — impedance boundaries are passive BCs, not excitation ports)
    for grp, tags in imp_data:
        if not tags:
            continue
        size     = (getattr(grp, "MeshSize",          0.0) or 0.0) or global_cond_size
        grp_dist = (getattr(grp, "MeshRefineDistance", 0.0) or 0.0) or dist_max
        _add_distance_threshold(tags, [], size, grp_dist)

    # Port face zone
    _add_distance_threshold(port_surf_tags, [], port_size, dist_max)

    # Integration edge zone — tighter transition distance for a 1-D feature
    _add_distance_threshold([], integ_curve_tags,
                            port_size / 2.0 if port_size > 0.0 else 0.0,
                            dist_max * 0.4)

    if not thresh_ids:
        return

    mid = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(mid, "FieldsList", thresh_ids)
    gmsh.model.mesh.field.setAsBackgroundMesh(mid)

    n_cond = sum(len(tags) for _, tags in cond_data)
    n_diel = sum(len(tags) for _, tags in diel_data)
    n_port = len(port_surf_tags)
    n_edge = len(integ_curve_tags)
    log_fn(
        f"Palace: Refinement fields — "
        f"{len(cond_data)} conductor group(s) ({n_cond} surface(s)), "
        f"{len(diel_data)} dielectric group(s) ({n_diel} surface(s)), "
        f"port zone ({n_port} surface(s)), "
        f"integration edge zone ({n_edge} curve(s)) — background mesh set.\n"
    )


# ---------------------------------------------------------------------------
# Outer boundary physical-group assignment
# ---------------------------------------------------------------------------

def _assign_outer_boundary_groups(gmsh, airbox, used_surface_tags, all_surfaces, log_fn):
    """Partition outer airbox surfaces into Gmsh physical groups by BC type.

    Fixed attribute scheme (shared with config.py):
        PEC outer faces      → attr 2
        PMC outer faces      → attr 3
        Absorbing outer faces → attr 4

    Per-face overrides (airbox.PECFaces / PMCFaces / AbsorbingFaces) are matched
    first by coplanar surface collection; remaining single-volume-adjacent surfaces
    fall to the default BC.
    """
    attr_map = {"PEC": 2, "PMC": 3, "Absorbing": 4}
    bc_tags  = {"PEC": [], "PMC": [], "Absorbing": []}

    # Step 1: match explicitly assigned faces — collect all Gmsh sub-surfaces that
    # lie within the selected FreeCAD face's bounding box.  After occ.fragment()
    # embeds dielectrics and conductors, a FreeCAD airbox face is split into several
    # Gmsh sub-surfaces; _faces_within_freecad_face captures all of them while
    # staying confined to the selected face's extent (important when two different
    # BC types share the same plane).
    for bc_type, prop_name in [("PEC",       "PECFaces"),
                                ("PMC",       "PMCFaces"),
                                ("Absorbing", "AbsorbingFaces")]:
        for link_obj, subnames in (getattr(airbox, prop_name, None) or []):
            for subname in (subnames or []):
                try:
                    fc_face = link_obj.Shape.getElement(subname)
                    tags = _faces_within_freecad_face(
                        gmsh, fc_face, all_surfaces, used_surface_tags,
                    )
                    for tag in tags:
                        bc_tags[bc_type].append(tag)
                        used_surface_tags.add(tag)
                    if not tags:
                        log_fn(f"Palace: WARNING — no Gmsh sub-surfaces found within "
                               f"{bc_type} face '{subname}'\n")
                except Exception as exc:
                    log_fn(f"Palace: WARNING — could not match per-face BC "
                           f"'{subname}': {exc}\n")

    # Step 2: remaining single-volume-adjacent surfaces → default BC type.
    default_bc = airbox.OuterBoundaryType
    for _, tag in all_surfaces:
        if tag in used_surface_tags:
            continue
        up_vols, _ = gmsh.model.getAdjacencies(2, tag)
        if len(up_vols) == 1:
            bc_tags[default_bc].append(tag)

    # Step 3: register one physical group per active BC type.
    for bc_type, tags in bc_tags.items():
        if tags:
            attr = attr_map[bc_type]
            gmsh.model.addPhysicalGroup(2, tags, attr)
            gmsh.model.setPhysicalName(2, attr, f"OuterBoundary_{bc_type}")
            log_fn(f"Palace: Outer BC {bc_type} → {len(tags)} surface(s) (attr {attr})\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_gmsh_main_thread():
    """Call from the main thread to satisfy Gmsh's signal-handler requirement.

    Returns an initialised gmsh module ready to pass to generate_mesh() as
    the *_gmsh_instance* argument so generate_mesh() can skip its own
    initialize() call inside a background thread.
    """
    mod = _gmsh()
    mod.initialize()
    return mod


def generate_mesh(doc, output_dir, log_fn=None, _gmsh_instance=None):
    """
    Generate a Gmsh mesh for the active Palace simulation.

    Parameters
    ----------
    doc        : FreeCAD document
    output_dir : directory where geometry.step and mesh.msh will be written
    log_fn     : callable(str) for progress messages; defaults to
                 FreeCAD.Console.PrintMessage.  The Palace debug console
                 passes its write() method here so Gmsh output streams to
                 the panel in real time.

    Returns
    -------
    str : absolute path to the written .msh file
    """
    if log_fn is None:
        log_fn = lambda msg: FreeCAD.Console.PrintMessage(
            msg if msg.endswith("\n") else msg + "\n"
        )

    import Part

    # Locate Palace objects
    sim = airbox = None
    lumped_ports, wave_ports, impedance_boundaries = [], [], []
    for obj in doc.Objects:
        if hasattr(obj, "SimulationType") and hasattr(obj, "PalaceBinary"):
            sim = obj
        elif hasattr(obj, "OuterBoundaryType"):
            airbox = obj
        elif hasattr(obj, "Rs") and hasattr(obj, "MeshAttribute"):
            impedance_boundaries.append(obj)
        elif hasattr(obj, "PortIndex") and hasattr(obj, "R") and hasattr(obj, "C"):
            lumped_ports.append(obj)
        elif hasattr(obj, "PortIndex") and hasattr(obj, "NumModes") and not hasattr(obj, "R"):
            wave_ports.append(obj)

    if airbox is None:
        raise RuntimeError("No Airbox object found. Create an Airbox before generating the mesh.")
    # Resolve the airbox solid — support both new Group-based and legacy AirboxShape patterns
    # so that existing FCStd documents continue to work while execute() migrates them.
    if hasattr(airbox, "Group") and airbox.Group:
        _airbox_solid = airbox.Group[0]
    elif hasattr(airbox, "AirboxShape") and airbox.AirboxShape:
        _airbox_solid = airbox.AirboxShape
    else:
        _airbox_solid = None

    if _airbox_solid is None:
        raise RuntimeError("Airbox has no shape assigned. Open the Airbox settings and add a solid.")

    all_ports = sorted(
        lumped_ports + wave_ports + impedance_boundaries,
        key=lambda o: o.PortIndex
    )

    # Collect material group bodies
    diel_bodies, cond_bodies = _collect_material_bodies(doc)
    has_dielectrics = bool(diel_bodies)

    # Collect all solid bodies to export (airbox first, then dielectric solids,
    # then any extra port-parent solids not already included).
    # Conductor solids are deliberately excluded — their faces are imported later
    # as standalone BRep 2-D surfaces so no conductor volume enters the mesh
    # (avoiding the MFEM STable3D crash from boundary triangles with no adjacent
    # volume element).
    export_objs = [_airbox_solid]
    for _, solid in diel_bodies:
        if solid not in export_objs:
            export_objs.append(solid)
    for extra in _port_shape_parents(doc):
        if extra not in export_objs:
            export_objs.append(extra)

    step_path = os.path.join(output_dir, "geometry.step")
    Part.export(export_objs, step_path)

    if _gmsh_instance is not None:
        gmsh = _gmsh_instance
    else:
        gmsh = _gmsh()
        gmsh.initialize()
    gmsh.logger.start()
    gmsh.model.add("palace")

    # _flush_gmsh forwards any Gmsh log lines accumulated since the last call
    # to log_fn.  gmsh.logger.get() returns the full accumulated list every
    # time (it does NOT consume), so we track an offset ourselves.
    _gmsh_log_offset = [0]

    def _flush_gmsh():
        all_msgs = gmsh.logger.get()
        new = all_msgs[_gmsh_log_offset[0]:]
        _gmsh_log_offset[0] = len(all_msgs)
        for msg in new:
            log_fn(msg + "\n")

    try:
        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()
        _flush_gmsh()

        all_volumes = gmsh.model.getEntities(3)
        all_surfaces = gmsh.model.getEntities(2)

        if not all_volumes:
            raise RuntimeError(
                "No volumes found after importing geometry. "
                "Ensure the Airbox shape is a closed solid."
            )

        used_surface_tags = set()
        wave_port_surface_tags = set()   # only wave ports need PEC 1D boundary elements
        lumped_port_surface_tags = set() # lumped ports need transfinite boundary circles

        # --- Material group fragmentation (dielectrics only) -------------------
        # Must run before port surface matching because fragment() renumbers
        # Gmsh entities.  Conductor solid bodies are NOT included here — their
        # faces are imported as BRep 2-D surfaces after the port import loop and
        # embedded into the mesh via the combined port+conductor fragment pass.
        if has_dielectrics:
            all_vol_tags_pre = [t for _, t in all_volumes]
            _assign_material_physical_groups(
                gmsh, log_fn,
                _airbox_solid,
                diel_bodies,
                all_vol_tags_pre,
            )
            # Refresh surface list after fragment() renumbers entities
            all_surfaces = gmsh.model.getEntities(2)
        # ----------------------------------------------------------------------

        # --- Edge-pair ports: planar (occ.fragment) and annular (COM match) ------
        # Planar port faces must be embedded geometrically via occ.fragment(),
        # not mesh.embed(), because the Delaunay boundary-recovery step cannot
        # reliably conform to thin constraint surfaces.  We import each port
        # face as a standalone OCC surface, then fragment ALL current volumes
        # with ALL port surfaces in one pass so Gmsh handles any adjacency
        # between port faces and material-group boundaries cleanly.
        handled_port_indices = set()

        # Collect planar edge-pair ports that have a computed Shape.
        planar_port_surf_list = []   # [(port_obj, [gmsh_surf_tags]), ...]
        for port in lumped_ports + impedance_boundaries:
            if not (hasattr(port, "PortEdges") and port.PortEdges):
                continue
            if getattr(port, "PortGeometryType", "") != "planar":
                continue
            if not (hasattr(port, "Shape") and port.Shape.Faces):
                log_fn(f"Palace: WARNING — port {port.PortIndex} has PortEdges but no "
                       "computed Shape; run doc.recompute() before meshing.\n")
                continue
            brep_path = tempfile.mktemp(suffix=".brep")
            try:
                port.Shape.exportBrep(brep_path)
                surf_before = {t for _, t in gmsh.model.getEntities(2)}
                gmsh.model.occ.importShapes(brep_path)
                gmsh.model.occ.synchronize()
            finally:
                try:
                    os.unlink(brep_path)
                except OSError:
                    pass
            new_tags = [t for _, t in gmsh.model.getEntities(2)
                        if t not in surf_before]
            if new_tags:
                planar_port_surf_list.append((port, new_tags))
                log_fn(f"Palace: Imported planar port {port.PortIndex} face → "
                       f"surf tags {new_tags}\n")
            else:
                log_fn(f"Palace: WARNING — no new surfaces after BRep import for "
                       f"port {port.PortIndex}.\n")

        # Collect wave port faces as BRep surfaces for geometric fragmentation.
        # Wave ports suffer from the same problem as planar lumped ports: the
        # airbox end face is not split at the wave port boundary edges until we
        # embed the port face as an OCC tool surface.  Without this, the Gmsh
        # sub-surfaces on the end face span the full airbox width and their
        # bbox centres accidentally fall inside any containment check we try,
        # so the wave port PG silently absorbs the entire end face.
        # Fragmenting with the wave port face BRep creates the boundary seams
        # and lets out_map assign only the correct sub-surfaces to the port PG.
        wave_port_surf_list = []   # [(port_obj, [gmsh_surf_tags]), ...]
        for port in wave_ports:
            port_orig_tags = []
            for link_obj, subnames in (port.PortFaces or []):
                for subname in subnames:
                    try:
                        fc_face = link_obj.Shape.getElement(subname)
                        brep_path = tempfile.mktemp(suffix=".brep")
                        try:
                            fc_face.exportBrep(brep_path)
                            surf_before = {t for _, t in gmsh.model.getEntities(2)}
                            gmsh.model.occ.importShapes(brep_path)
                            gmsh.model.occ.synchronize()
                        finally:
                            try:
                                os.unlink(brep_path)
                            except OSError:
                                pass
                        new_tags = [t for _, t in gmsh.model.getEntities(2)
                                    if t not in surf_before]
                        if new_tags:
                            port_orig_tags.extend(new_tags)
                            log_fn(f"Palace: Imported wave port {port.PortIndex} "
                                   f"face '{subname}' → surf tags {new_tags}\n")
                        else:
                            log_fn(f"Palace: WARNING — no new surfaces after BRep "
                                   f"import for wave port {port.PortIndex} "
                                   f"face '{subname}'.\n")
                    except Exception as exc:
                        FreeCAD.Console.PrintWarning(
                            f"Palace meshing: could not import wave port "
                            f"{port.PortIndex} face '{subname}': {exc}\n"
                        )
            if port_orig_tags:
                wave_port_surf_list.append((port, port_orig_tags))

        # Import integration edge BReps for wave ports that have one set.
        # Including these 1-D curves in the combined fragment forces Gmsh to
        # place mesh nodes exactly on the probe path, reducing field-
        # interpolation error at the Palace probe coordinates.
        wave_port_edge_list = []   # [(port_obj, [gmsh_curve_tags]), ...]
        for port in wave_ports:
            ie = getattr(port, "IntegrationEdge", None)
            if not ie or not ie[0]:
                continue
            link_obj, subs = ie
            if not subs:
                continue
            try:
                fc_edge = link_obj.Shape.getElement(subs[0])
                brep_path = tempfile.mktemp(suffix=".brep")
                try:
                    fc_edge.exportBrep(brep_path)
                    curve_before = {t for _, t in gmsh.model.getEntities(1)}
                    gmsh.model.occ.importShapes(brep_path)
                    gmsh.model.occ.synchronize()
                finally:
                    try:
                        os.unlink(brep_path)
                    except OSError:
                        pass
                new_ctags = [t for _, t in gmsh.model.getEntities(1)
                             if t not in curve_before]
                if new_ctags:
                    wave_port_edge_list.append((port, new_ctags))
                    log_fn(f"Palace: Imported wave port {port.PortIndex} "
                           f"integration edge → curve tags {new_ctags}\n")
                else:
                    log_fn(f"Palace: WARNING — no new curves after BRep import "
                           f"for wave port {port.PortIndex} integration edge.\n")
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"Palace meshing: could not import integration edge "
                    f"for wave port {port.PortIndex}: {exc}\n"
                )

        # Collect normals of port faces so conductor end faces at the same
        # cross-section can be skipped — they create triple-edges with port
        # surface elements that MFEM rejects in AddSegmentFaceElement.
        # This applies to both planar lumped ports AND wave ports: the
        # conductor end-face (2-D, attr 100), the wave port face (2-D, attr 11),
        # and a PortBoundaryCurve (1-D, attr 2) would all share the same outer
        # edge, which triggers the MFEM AddSegmentFaceElement assertion.
        # The wave port 2D eigenvalue correctly sees PEC at conductor boundaries
        # because config.py emits WavePortPEC for Lossy Conductor groups, which
        # is applied by Palace regardless of whether the ±Y end-faces are present.
        port_face_normals = []
        for port in lumped_ports:
            if getattr(port, "PortGeometryType", "") == "planar":
                if hasattr(port, "Shape") and port.Shape.Faces:
                    try:
                        n = port.Shape.Faces[0].normalAt(0, 0)
                        n.normalize()
                        port_face_normals.append(n)
                    except Exception:
                        pass
        for port in wave_ports:
            for link_obj, subnames in (port.PortFaces or []):
                for subname in subnames:
                    try:
                        fc_face = link_obj.Shape.getElement(subname)
                        n = fc_face.normalAt(0, 0)
                        n.normalize()
                        port_face_normals.append(n)
                    except Exception:
                        pass

        # Import conductor solid faces as standalone BRep 2-D surfaces.
        # Done after the planar port import so that both sets of surfaces enter
        # the combined fragment together.  No conductor volume is ever added to
        # the Gmsh model, eliminating the MFEM STable3D crash.
        cond_brep_surf_list = (
            _import_conductor_solid_faces(
                gmsh, cond_bodies, log_fn,
                skip_normals=port_face_normals if port_face_normals else None,
            )
            if cond_bodies else []
        )

        # Fragment all volumes with planar port surfaces AND conductor surfaces
        # in one call.
        #
        # IMPORTANT: the fragment may also split existing tracked surfaces (e.g.
        # the top face of a conductor ground-plane solid is cut by the port face
        # edge).  To keep physical groups and used_surface_tags consistent, we
        # pass every currently-tracked surface as an explicit tool object so that
        # out_map gives us an old→new entity-tag mapping for them too.
        imp_data = []   # (imp_obj, [gmsh_surf_tags]) — populated by all three port paths below

        if (planar_port_surf_list or wave_port_surf_list
                or cond_brep_surf_list or wave_port_edge_list):
            all_vol_tags_now    = [t for _, t in gmsh.model.getEntities(3)]
            all_planar_tags     = [t for _, tags in planar_port_surf_list for t in tags]
            all_wave_port_tags  = [t for _, tags in wave_port_surf_list for t in tags]
            all_cond_brep_tags  = [t for _, tags in cond_brep_surf_list for t in tags]
            all_integ_curve_tags = [t for _, tags in wave_port_edge_list for t in tags]

            # Snapshot ALL physical-group metadata BEFORE the fragment.
            # occ.synchronize() after occ.fragment() silently removes physical
            # groups whose member entity tags are invalidated — so we must save
            # them here, not re-query after the fragment.

            # 3-D (volume) physical groups
            vol_pg_info_snap = {}   # pg_tag → (name, frozenset_of_entity_tags)
            for _, pg_tag in gmsh.model.getPhysicalGroups(3):
                name = gmsh.model.getPhysicalName(3, pg_tag)
                vols = frozenset(gmsh.model.getEntitiesForPhysicalGroup(3, pg_tag))
                vol_pg_info_snap[pg_tag] = (name, vols)

            # Surfaces currently in physical groups that might be split.
            current_all_surf_tags = {t for _, t in gmsh.model.getEntities(2)}
            tracked_surf_tags = sorted(used_surface_tags & current_all_surf_tags)

            # 2-D (surface) physical groups
            surf_pg_info = {}   # pg_tag → (name, [old_entity_tags])
            for _, pg_tag in gmsh.model.getPhysicalGroups(2):
                name    = gmsh.model.getPhysicalName(2, pg_tag)
                entities = list(gmsh.model.getEntitiesForPhysicalGroup(2, pg_tag))
                surf_pg_info[pg_tag] = (name, entities)

            # Tool list: planar lumped ports, then wave ports, then conductors,
            # then tracked surfaces.  Integration edge curves are appended as
            # 1-D tools after all surface tools so that the surface old→new map
            # index arithmetic is unchanged.
            all_tool_tags = all_planar_tags + all_wave_port_tags + all_cond_brep_tags + tracked_surf_tags
            vol_dim_tags  = [(3, t) for t in all_vol_tags_now]
            surf_dim_tags = ([(2, t) for t in all_tool_tags]
                             + [(1, t) for t in all_integ_curve_tags])

            log_fn(f"Palace: Fragmenting {len(all_vol_tags_now)} volume(s) with "
                   f"{len(all_planar_tags)} planar port surface(s), "
                   f"{len(all_wave_port_tags)} wave port surface(s), "
                   f"{len(all_cond_brep_tags)} conductor surface(s), "
                   f"{len(all_integ_curve_tags)} integration edge(s) "
                   f"(+ {len(tracked_surf_tags)} tracked surface(s))…\n")
            _, out_map_ports = gmsh.model.occ.fragment(vol_dim_tags, surf_dim_tags)
            gmsh.model.occ.synchronize()
            _flush_gmsh()

            # Build old→new surface tag map from the surface-tool portion of out_map.
            # Curve tools sit beyond the surface tools in out_map; they are handled
            # separately below and do not affect this loop's index arithmetic.
            n_vols = len(vol_dim_tags)
            old_to_new_surf = {}
            for i, old_tag in enumerate(all_tool_tags):
                new_tags_out = [t for d, t in out_map_ports[n_vols + i] if d == 2]
                old_to_new_surf[old_tag] = new_tags_out

            # Log how integration edge curves were split by the fragment.
            n_surf_tools = len(all_tool_tags)
            for j, old_ctag in enumerate(all_integ_curve_tags):
                new_ctags = [t for d, t in out_map_ports[n_vols + n_surf_tools + j] if d == 1]
                log_fn(f"Palace: Integration edge curve {old_ctag} → {new_ctags} after fragment\n")

            # Rebuild volume physical groups from the pre-fragment snapshot.
            _update_vol_physical_groups_after_fragment(
                gmsh, all_vol_tags_now, out_map_ports,
                vol_pg_info=vol_pg_info_snap, log_fn=log_fn)

            # Rebuild existing 2D physical groups using the new entity tags.
            still_valid = {t for _, t in gmsh.model.getEntities(2)}
            if surf_pg_info:
                gmsh.model.removePhysicalGroups([(2, pg_tag) for pg_tag in surf_pg_info])
            for pg_tag, (name, old_entities) in surf_pg_info.items():
                new_entities = []
                for old_tag in old_entities:
                    if old_tag in old_to_new_surf:
                        new_entities.extend(old_to_new_surf[old_tag])
                    elif old_tag in still_valid:
                        new_entities.append(old_tag)
                if new_entities:
                    gmsh.model.addPhysicalGroup(2, new_entities, pg_tag)
                    gmsh.model.setPhysicalName(2, pg_tag, name)

            # Update used_surface_tags to reflect new entity tags.
            new_used = set()
            for old_tag in used_surface_tags:
                if old_tag in old_to_new_surf:
                    new_used.update(old_to_new_surf[old_tag])
                elif old_tag in still_valid:
                    new_used.add(old_tag)
            used_surface_tags = new_used

            # Assign port surface physical groups from the port portion of out_map.
            imp_data = []   # (imp_obj, [post-fragment surf tags]) — built here
            flat_idx = 0
            for port, orig_tags in planar_port_surf_list:
                port_surf_final = []
                for _ in orig_tags:
                    output = [t for d, t in out_map_ports[n_vols + flat_idx] if d == 2]
                    port_surf_final.extend(output)
                    flat_idx += 1
                if port_surf_final:
                    gmsh.model.addPhysicalGroup(2, port_surf_final, port.MeshAttribute)
                    gmsh.model.setPhysicalName(2, port.MeshAttribute,
                                               f"Port_{port.PortIndex}")
                    used_surface_tags.update(port_surf_final)
                    lumped_port_surface_tags.update(port_surf_final)
                    handled_port_indices.add(port.PortIndex)
                    log_fn(f"Palace: Port {port.PortIndex} (planar, fragment) → "
                           f"surf tags {port_surf_final} "
                           f"(attr {port.MeshAttribute})\n")
                    if hasattr(port, "Rs"):   # ImpedanceBoundary — track for per-obj sizing
                        imp_data.append((port, list(port_surf_final)))
                else:
                    log_fn(f"Palace: WARNING — port {port.PortIndex} produced no "
                           "output surfaces after fragment; check that the port "
                           "face lies within the airbox.\n")

            # Assign wave port physical groups from the fragmented wave port BRep surfaces.
            # Use old_to_new_surf (same as conductors) rather than flat_idx so the
            # accounting is independent of planar port count.
            for port, orig_tags in wave_port_surf_list:
                port_surf_final = []
                for old_tag in orig_tags:
                    port_surf_final.extend(old_to_new_surf.get(old_tag, []))
                port_surf_final = [t for t in port_surf_final
                                   if t not in used_surface_tags]
                if port_surf_final:
                    gmsh.model.addPhysicalGroup(2, port_surf_final, port.MeshAttribute)
                    gmsh.model.setPhysicalName(2, port.MeshAttribute,
                                               f"Port_{port.PortIndex}")
                    used_surface_tags.update(port_surf_final)
                    wave_port_surface_tags.update(port_surf_final)
                    handled_port_indices.add(port.PortIndex)
                    log_fn(f"Palace: Port {port.PortIndex} (wave, fragment) → "
                           f"surf tags {port_surf_final} "
                           f"(attr {port.MeshAttribute})\n")
                else:
                    log_fn(f"Palace: WARNING — wave port {port.PortIndex} produced "
                           "no output surfaces after fragment; check that the port "
                           "face lies on the airbox boundary.\n")

            # Assign conductor PEC physical groups from conductor BRep surfaces.
            # Ports take priority: skip surface tags already in used_surface_tags.
            # Multiple conductor groups may share the same MeshAttribute, so
            # accumulate into a per-attr list then register one PG per attr.
            cond_attr_surf_map = {}   # attr → [final surf tags]
            for grp, orig_cond_tags in cond_brep_surf_list:
                attr = grp.MeshAttribute
                for old_tag in orig_cond_tags:
                    new_tags_for_face = old_to_new_surf.get(old_tag, [old_tag])
                    unclaimed = [t for t in new_tags_for_face
                                 if t not in used_surface_tags]
                    cond_attr_surf_map.setdefault(attr, []).extend(unclaimed)
                    used_surface_tags.update(unclaimed)

            for attr, surf_tags in cond_attr_surf_map.items():
                if surf_tags:
                    gmsh.model.addPhysicalGroup(2, surf_tags, attr)
                    gmsh.model.setPhysicalName(2, attr, f"Conductor_{attr}")
                    log_fn(f"Palace: Conductor (attr {attr}) → "
                           f"surf tags {surf_tags}\n")

            # Refresh after planar port+conductor fragment
            all_surfaces = gmsh.model.getEntities(2)
            all_volumes  = gmsh.model.getEntities(3)

        # Annular edge-pair ports: the face already exists as an airbox boundary;
        # match by comparing the port Shape's centre-of-mass to Gmsh surfaces.
        for port in lumped_ports + impedance_boundaries:
            if not (hasattr(port, "PortEdges") and port.PortEdges):
                continue
            if getattr(port, "PortGeometryType", "") != "annular":
                continue
            if not (hasattr(port, "Shape") and port.Shape.Faces):
                log_fn(f"Palace: WARNING — port {port.PortIndex} has PortEdges but no "
                       "computed Shape; run doc.recompute() before meshing.\n")
                continue
            com = port.Shape.CenterOfMass
            target = (com.x, com.y, com.z)
            tag = _closest_surface(gmsh, target,
                                   gmsh.model.getEntities(2), used_surface_tags)
            if tag is not None:
                gmsh.model.addPhysicalGroup(2, [tag], port.MeshAttribute)
                gmsh.model.setPhysicalName(2, port.MeshAttribute,
                                           f"Port_{port.PortIndex}")
                used_surface_tags.add(tag)
                lumped_port_surface_tags.add(tag)
                handled_port_indices.add(port.PortIndex)
                if hasattr(port, "Rs"):   # ImpedanceBoundary — track for per-obj sizing
                    imp_data.append((port, [tag]))
                log_fn(f"Palace: Port {port.PortIndex} (annular, COM-matched) → "
                       f"surface tag {tag} (attr {port.MeshAttribute})\n")
            else:
                log_fn(f"Palace: WARNING — could not match annular port "
                       f"{port.PortIndex} to any Gmsh surface by COM.\n")
        # -----------------------------------------------------------------------

        # Assign a physical group for each port
        for port in all_ports:
            if port.PortIndex in handled_port_indices:
                continue   # already processed above as an edge-pair port
            is_wave_port = hasattr(port, "NumModes") and not hasattr(port, "R")
            port_tags = []
            for link_obj, subnames in (port.PortFaces or []):
                for subname in subnames:
                    try:
                        if is_wave_port:
                            fc_face = link_obj.Shape.getElement(subname)
                            _bb = fc_face.BoundBox
                            _fn = fc_face.normalAt(0, 0); _fn.normalize()
                            log_fn(
                                f"Palace: WavePort {port.PortIndex} PortFace '{subname}' "
                                f"on '{link_obj.Label}'\n"
                                f"  normal=({_fn.x:.4f},{_fn.y:.4f},{_fn.z:.4f}) "
                                f"BBox X[{_bb.XMin:.3f},{_bb.XMax:.3f}] "
                                f"Y[{_bb.YMin:.3f},{_bb.YMax:.3f}] "
                                f"Z[{_bb.ZMin:.3f},{_bb.ZMax:.3f}]\n"
                            )
                            tags = _faces_within_freecad_face(
                                gmsh, fc_face, all_surfaces, used_surface_tags,
                            )
                            for _t in tags:
                                _cx, _cy, _cz = _gmsh_bbox_centre(gmsh, 2, _t)
                                log_fn(f"  → surf {_t} ctr=({_cx:.3f},{_cy:.3f},{_cz:.3f})\n")
                            for tag in tags:
                                port_tags.append(tag)
                                used_surface_tags.add(tag)
                                wave_port_surface_tags.add(tag)
                            if not tags:
                                FreeCAD.Console.PrintWarning(
                                    f"Palace meshing: no Gmsh sub-surfaces found within "
                                    f"wave port {port.PortIndex} face '{subname}'\n"
                                )
                            else:
                                log_fn(
                                    f"Palace: Wave port {port.PortIndex} — "
                                    f"{len(tags)} coplanar surface(s) collected "
                                    f"(attr {port.MeshAttribute})\n"
                                )
                        else:
                            # Lumped ports: single closest-surface match.
                            centre = _face_centre_mm(link_obj, subname)
                            tag = _closest_surface(gmsh, centre, all_surfaces, used_surface_tags)
                            if tag is not None:
                                port_tags.append(tag)
                                used_surface_tags.add(tag)
                                lumped_port_surface_tags.add(tag)
                    except Exception as exc:
                        FreeCAD.Console.PrintWarning(
                            f"Palace meshing: could not match face '{subname}': {exc}\n"
                        )

            if port_tags:
                gmsh.model.addPhysicalGroup(2, port_tags, port.MeshAttribute)
                gmsh.model.setPhysicalName(2, port.MeshAttribute,
                                           f"Port_{port.PortIndex}")
                log_fn(
                    f"Palace: Port {port.PortIndex} → surface tags {port_tags} "
                    f"(attribute {port.MeshAttribute})\n"
                )
                if hasattr(port, "Rs"):   # ImpedanceBoundary — track for per-obj sizing
                    imp_data.append((port, list(port_tags)))
            else:
                FreeCAD.Console.PrintWarning(
                    f"Palace: No Gmsh surfaces matched for port {port.PortIndex}. "
                    "Check that port faces are selected on the airbox geometry.\n"
                )

        # Outer boundary: partition remaining outer surfaces by BC type.
        # Interior material-interface surfaces (adjacent to two volumes) are
        # excluded — Palace applies continuity there automatically, and
        # including them causes triple-edge MFEM rejections.
        _assign_outer_boundary_groups(
            gmsh, airbox, used_surface_tags, all_surfaces, log_fn)

        # Add physical line groups for boundary curves of wave port faces only.
        # MFEM needs 1D elements at wave port edges so Palace can apply PEC
        # boundary conditions there; without them the wave port eigenvalue
        # problem is unconstrained and produces a singular matrix.
        # Lumped ports must NOT have their boundary curves added here — doing so
        # would assign PEC attribute 2 to rings around the port face and
        # short-circuit the Robin impedance excitation.
        #
        # With multiple coplanar sub-faces in one wave port, only OUTER curves
        # (belonging to exactly one sub-face) should be in PortBoundaryCurves.
        # Curves shared by two sub-faces are interior substrate/air interfaces;
        # applying PEC there would incorrectly pin the field at the interface.
        port_curve_counts = {}
        for surf_tag in wave_port_surface_tags:
            for _, curve_tag in gmsh.model.getBoundary([(2, surf_tag)], oriented=False):
                ct = abs(curve_tag)
                port_curve_counts[ct] = port_curve_counts.get(ct, 0) + 1
        outer_curves = [ct for ct, count in port_curve_counts.items() if count == 1]
        if outer_curves:
            gmsh.model.addPhysicalGroup(1, outer_curves, 2)
            gmsh.model.setPhysicalName(1, 2, "PortBoundaryCurves")

        # Volume domain — only needed when no dielectric fragment assigned vol PGs
        if not has_dielectrics:
            vol_tags = [tag for _, tag in all_volumes]
            gmsh.model.addPhysicalGroup(3, vol_tags, 1)
            gmsh.model.setPhysicalName(3, 1, "Domain")

        # Mesh options
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)   # Delaunay
        gmsh.option.setNumber("Mesh.Optimize", 1)
        # Netgen optimizer crashes with SIGSEGV on degenerate tets that arise
        # around thin embedded conductor surfaces (badmax = 1e+24 illegal tets).
        # Gmsh's own optimizer (Mesh.Optimize) is sufficient.
        gmsh.option.setNumber("Mesh.OptimizeNetgen", 0)

        # Characteristic lengths: PalaceMesh settings take priority; fall back to
        # SimulationContainer for backward compatibility with older documents.
        from features import find_palace_mesh
        _mesh_obj = find_palace_mesh(doc)
        cl_max = (
            getattr(_mesh_obj, "MeshCharacteristicLengthMax", 0.0)
            if _mesh_obj else 0.0
        ) or (getattr(sim, "MeshCharacteristicLengthMax", 0.0) if sim is not None else 0.0) \
          or (getattr(airbox, "MeshSize", 0.0) or 0.0)
        cl_min = (
            getattr(_mesh_obj, "MeshCharacteristicLengthMin", 0.0)
            if _mesh_obj else 0.0
        ) or (getattr(sim, "MeshCharacteristicLengthMin", 0.0) if sim is not None else 0.0)

        max_str = f"{cl_max:.4g}" if cl_max > 0.0 else "auto"
        min_str = f"{cl_min:.4g}" if cl_min > 0.0 else "auto"
        log_fn(f"Palace: Element size limits — max={max_str}, min={min_str} (model units)\n")

        # Gmsh renamed Mesh.CharacteristicLength* to Mesh.MeshSize* in v4.9.
        # Try the current name first; fall back to the old alias for older installs.
        def _set_size_opt(name_new, name_old, value):
            try:
                gmsh.option.setNumber(name_new, value)
            except Exception:
                gmsh.option.setNumber(name_old, value)

        if cl_max > 0.0:
            _set_size_opt("Mesh.MeshSizeMax", "Mesh.CharacteristicLengthMax", cl_max)
        if cl_min > 0.0:
            _set_size_opt("Mesh.MeshSizeMin", "Mesh.CharacteristicLengthMin", cl_min)

        # Apply transfinite constraints to lumped port face boundary circles.
        # Required for Palace's coaxial bounding-ball check and area accuracy.
        # ImpedanceBoundary faces are excluded — their straight edges don't need
        # the bounding-ball constraint and transfinite over-constrains them to ≥8
        # nodes per edge, making the mesh far finer than the specified MeshSize.
        _imp_surf_set = {t for _, tags in imp_data for t in tags}
        _lumped_only  = lumped_port_surface_tags - _imp_surf_set
        if _lumped_only:
            cl_max_for_transfinite = cl_max if cl_max > 0.0 else 1.0  # default 1 mm
            _apply_port_transfinite(gmsh, _lumped_only, cl_max_for_transfinite)

        # Apply transfinite constraints to wave port integration edge curves.
        # Ensures the probe path has adequate node density for trapezoidal
        # Z_c integration regardless of the global cl_max setting.
        _port_size = getattr(_mesh_obj, "MeshPortSize", 0.0) if _mesh_obj else 0.0
        _cl_max_ref = cl_max if cl_max > 0.0 else 1.0
        if wave_port_edge_list:
            for _, ctags in wave_port_edge_list:
                for ctag in ctags:
                    xmin_, ymin_, zmin_, xmax_, ymax_, zmax_ = (
                        gmsh.model.getBoundingBox(1, ctag))
                    edge_len = max(xmax_ - xmin_, ymax_ - ymin_, zmax_ - zmin_)
                    seg_size = (_port_size / 2.0 if _port_size > 0.0
                                else _cl_max_ref / 6.0)
                    n = max(8, math.ceil(edge_len / max(seg_size, 0.01)))
                    gmsh.model.mesh.setTransfiniteCurve(ctag, n + 1)

        # Collect dielectric boundary surfaces per group (mirrors cond_brep_surf_list).
        # Surfaces bounding a non-background volume, excluding already-assigned port/BC surfaces.
        diel_data = []
        if has_dielectrics:
            attr_to_diel_grp = {grp.MeshAttribute: grp for grp, _ in diel_bodies}
            for _, pg_tag in gmsh.model.getPhysicalGroups(3):
                grp = attr_to_diel_grp.get(pg_tag)
                if grp is None:
                    continue
                vols = gmsh.model.getEntitiesForPhysicalGroup(3, pg_tag)
                surf_tags = set()
                for vtag in vols:
                    for _, stag in gmsh.model.getBoundary([(3, vtag)], oriented=False):
                        stag_abs = abs(stag)
                        if stag_abs not in used_surface_tags:
                            surf_tags.add(stag_abs)
                diel_data.append((grp, list(surf_tags)))

        # Apply geometry-aware Distance/Threshold mesh size fields.
        # Per-object MeshSize overrides global MeshConductorSize when non-zero.
        # Impedance boundary surfaces are excluded from the port_size zone and
        # handled separately via imp_data so the user can control their size.
        _imp_surface_tags = {t for _, tags in imp_data for t in tags}
        _apply_refinement_fields(
            gmsh,
            cl_max           = cl_max,
            cond_data        = cond_brep_surf_list,
            port_surf_tags   = list(
                (wave_port_surface_tags | lumped_port_surface_tags) - _imp_surface_tags
            ),
            integ_curve_tags = [t for _, tags in wave_port_edge_list for t in tags],
            diel_data        = diel_data,
            imp_data         = imp_data,
            global_cond_size = getattr(_mesh_obj, "MeshConductorSize", 0.0) if _mesh_obj else 0.0,
            port_size        = _port_size,
            refine_dist      = getattr(_mesh_obj, "MeshRefineDistance", 0.0) if _mesh_obj else 0.0,
            log_fn           = log_fn,
        )

        log_fn("Palace: Running Gmsh 3D mesh generation…\n")
        gmsh.model.mesh.generate(3)
        _flush_gmsh()

        # Use order-2 elements so that boundary curve midpoint nodes lie exactly
        # on the circle geometry.  Palace's bounding-ball check for cylindrical
        # lumped ports requires chord midpoints to be on the arc; linear (order-1)
        # segments place them at the apothem (slightly inside), which fails the
        # precision threshold.  MFEM accepts second-order meshes fine.
        gmsh.model.mesh.setOrder(2)
        _flush_gmsh()

        # MFEM (used by Palace) requires MSH v2.2; v4 triggers a "vertices indices
        # are not unique" abort due to non-contiguous node numbering in that format.
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        mesh_path = os.path.join(output_dir, "mesh.msh")
        gmsh.write(mesh_path)
        _flush_gmsh()

        return mesh_path

    finally:
        gmsh.logger.stop()
        gmsh.finalize()
