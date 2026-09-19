"""
Mesh generation for Palace using the Netgen (netgen.occ) Python API.

Workflow
--------
1. Export the airbox+dielectric solids to STEP and each conductor group's
   fused solid to its own BREP (same fidelity Gmsh's pipeline gets today).
2. Load into netgen.occ, Glue() everything (background + conductors +
   embedded lumped-port faces) so OCC carves each conductor's cavity and
   each lumped port's internal boundary correctly.
3. Re-identify every post-Glue solid by center-of-gravity + volume matching
   against the pre-Glue shapes (netgen.occ's Glue() does not reliably
   propagate .name through nested containment -- verified empirically).
4. Before dropping anything: tag conductor solids and port faces with
   .bc(<attr>) directly on the OCC geometry. Netgen's .bc() sets a name
   property on the underlying face entity itself -- verified empirically
   that a tag set on a solid about to be dropped still reads back correctly
   from FaceDescriptor.bcname after that solid is excluded from the meshed
   shape, because the boundary face is the same shared entity the kept
   neighbor also references. This sidesteps needing any post-mesh
   domin/domout inference to recover "which removed conductor did this
   face belong to" -- domin/domout can't do that here anyway, since a
   conductor's domain index never exists in the final mesh once its volume
   is excluded before GenerateMesh() is even called (an earlier draft of
   this module tried the domin/domout approach and hit exactly this dead
   end). Ports are tagged after conductors so they take priority on any
   coincident face, mirroring palace.meshing's
   own "ports win" rule. A conductor/conductor contact face borders two
   dropped solids and simply never becomes part of the meshed shape's own
   boundary at all -- no explicit adjacency exclusion needed, unlike Gmsh.
5. Drop conductor-domain solids (Palace treats them as PEC boundary
   conditions, not meshed regions); wrap the remaining solids in a
   Compound (not a second Glue -- verified Compound preserves the .bc()/
   .maxh tags already set, and re-Glue's tag-preservation was untested).
6. GenerateMesh() with automatic local sizing (no manual maxh by default --
   verified empirically to beat manual per-solid sizing on real boards).
7. Read FaceDescriptor.bcname back for every boundary face -- ports and
   conductor-PEC faces already carry the explicit tag from step 4; any
   face nobody tagged keeps Netgen's own "default" bcname and falls back
   to the airbox's OuterBoundaryType.
8. SecondOrder() + Curve(2).
9. Export as Gmsh MSH2, then rewrite the physical-group tag columns: Netgen's
   own Gmsh2 exporter ignores .name entirely and auto-numbers domains as
   100000+domain_index and surfaces as FaceDescriptor.surfnr -- verified
   empirically, no way to control this via the netgen.occ/meshing API. This
   step remaps those to each FreeCAD object's real MeshAttribute number,
   which palace/config.py and features/mesh.py depend on exactly.

See GMSH_TO_NETGEN_MIGRATION_PLAN.md and the transition plan for the full
investigation this module is built from.
"""

import itertools
import os
import sys
import tempfile

import numpy as np

import FreeCAD

from palace.meshing import (
    _collect_material_bodies,
    _fuse_group_solids,
    _port_shape_parents,
    _validate_shape,
)


# ---------------------------------------------------------------------------
# Netgen import helper (mirrors palace.meshing._gmsh())
# ---------------------------------------------------------------------------

def _netgen():
    """Import netgen.occ + netgen.meshing.

    Verified to install cleanly with `pip install netgen-mesher` into
    FreeCAD's own bundled interpreter alongside gmsh -- both vendor their
    own OpenCASCADE build and coexist fine in the same process, either
    import order (confirmed empirically). No separate venv needed.
    """
    try:
        import netgen.occ as occ
        import netgen.meshing as ngm
        return occ, ngm
    except ImportError:
        pass

    home = FreeCAD.getHomePath()
    candidates = [
        os.path.join(home, "bin"),
        os.path.join(home, "lib"),
        os.path.join(home, "bin", "Lib", "site-packages"),
        home,
    ]
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
            import netgen.occ as occ
            import netgen.meshing as ngm
            return occ, ngm
        except ImportError:
            continue

    python_exe = os.path.join(home, "bin", "python.exe")
    raise RuntimeError(
        "The Netgen Python API (netgen-mesher) is not installed.\n\n"
        "Fix: open a terminal (as Administrator if FreeCAD is in Program Files)\n"
        "and run:\n\n"
        f'    "{python_exe}" -m pip install netgen-mesher\n\n'
        "Then restart FreeCAD and try Generate & Run again.\n"
        "The pip package bundles its own OpenCASCADE build and will not\n"
        "conflict with Gmsh's."
    )


# ---------------------------------------------------------------------------
# Object location (duplicated from palace.meshing.generate_mesh's inline
# scan, deliberately -- see module docstring in the transition plan for why
# palace/meshing.py itself is not touched by this module).
# ---------------------------------------------------------------------------

def _locate_palace_objects(doc):
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
    return sim, airbox, lumped_ports, wave_ports, impedance_boundaries


def _resolve_airbox_solid(airbox):
    if hasattr(airbox, "Group") and airbox.Group:
        return airbox.Group[0]
    if hasattr(airbox, "AirboxShape") and airbox.AirboxShape:
        return airbox.AirboxShape
    return None


# ---------------------------------------------------------------------------
# COM+volume matching (netgen.occ analog of palace.meshing._match_vol_by_com)
# ---------------------------------------------------------------------------

def _dist_sq(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _match_reference(com, reference):
    """Nearest-COM match against a (label, com, kind, attr) reference list.

    Netgen's Glue() does not reliably propagate .name through *nested*
    containment (airbox containing dielectric containing conductor -- this
    workbench's actual geometry model) even though it works for merely-
    touching or disjoint solids -- verified with a minimal repro. COM
    matching is the same technique palace.meshing._match_vol_by_com already
    uses on the Gmsh side, and is what actually works here.
    """
    best, best_d = None, float("inf")
    for entry in reference:
        d = _dist_sq(com, entry[1])
        if d < best_d:
            best_d, best = d, entry
    return best


# ---------------------------------------------------------------------------
# Lumped port face construction (handles the antipodal-node requirement for
# Palace's coaxial lumped-port bounding-ball check)
# ---------------------------------------------------------------------------

def _build_lumped_port_face(occ, fc_shape):
    """Rebuild a lumped port's face in netgen.occ from its FreeCAD geometry.

    For a circular (annular) port boundary, pure automatic Netgen sizing can
    produce an odd number of evenly-spaced boundary points with no pair
    exactly antipodal -- verified empirically (13 points, closest pair 27.7
    degrees off from antipodal). Palace's CoaxialElementData.GetBoundingBall()
    needs a genuine antipodal pair to correctly recover the port's true
    radius, exactly as documented in palace.meshing._apply_port_transfinite.

    Fix (also verified empirically): rebuild any circular boundary edge as
    two explicit semicircle arcs meeting at two diametrically-opposite
    points, forcing the mesher to preserve those exact two points as mesh
    vertices regardless of automatic sizing elsewhere. A polygonal (planar
    rectangular) port face needs no such treatment -- its corners are always
    preserved as mesh vertices by any mesher, and for a real board's port
    (verified against Microstrip_test_new.FCStd) the diagonal corners are
    already exactly antipodal through the true centroid.

    Returns a netgen.occ Face (or Wire-bounded Face for an annulus) with the
    same shape as fc_shape's face(s).
    """
    faces = fc_shape.Faces
    if len(faces) != 1:
        raise RuntimeError(
            f"Lumped port face has {len(faces)} faces; expected exactly 1."
        )
    fc_face = faces[0]

    circular_edges = [e for e in fc_face.Edges if e.Curve.__class__.__name__ == "Circle"]
    if not circular_edges or len(fc_face.Edges) != len(circular_edges):
        # Polygonal (or otherwise non-circular) boundary -- corners already
        # guarantee antipodal vertices for any convex shape with point
        # symmetry; import as-is via BREP.
        brep_path = tempfile.mktemp(suffix=".brep")
        try:
            fc_face.exportBrep(brep_path)
            shape = occ.OCCGeometry(brep_path).shape
        finally:
            try:
                os.unlink(brep_path)
            except OSError:
                pass
        return shape

    # Circular boundary (annulus or full disc) -- rebuild each circular edge
    # as two semicircle arcs split at diametrically-opposite points.
    wires = []
    for edge in circular_edges:
        circ = edge.Curve
        center = circ.Center
        radius = circ.Radius
        normal = circ.Axis
        # An arbitrary reference direction in the circle's plane, orthogonal
        # to normal, to seed the split points and initial tangent.
        ref = FreeCAD.Vector(1, 0, 0)
        if abs(normal.dot(ref)) > 0.9:
            ref = FreeCAD.Vector(0, 1, 0)
        u = normal.cross(ref)
        u.normalize()

        wp = occ.WorkPlane(occ.Axes(
            p=(center.x, center.y, center.z),
            n=(normal.x, normal.y, normal.z),
            h=(u.x, u.y, u.z),
        ))
        wp.MoveTo(radius, 0)
        wp.Direction(0, 1)
        wp.Arc(radius, 180)
        wp.Arc(radius, 180)
        wires.append(wp.Wire())

    if len(wires) == 1:
        shape = occ.Face(wires[0])
    elif len(wires) == 2:
        # Annulus: larger-radius wire is the outer boundary.
        areas = [(occ.Face(w).mass, w) for w in wires]
        areas.sort(key=lambda t: -t[0])
        outer_face = occ.Face(areas[0][1])
        inner_face = occ.Face(areas[1][1])
        shape = outer_face - inner_face
    else:
        raise RuntimeError(
            f"Lumped port face has {len(wires)} circular edges; expected 1 "
            "(full disc) or 2 (annulus)."
        )
    return shape


# ---------------------------------------------------------------------------
# Face identification via nearest-COM match against a FreeCAD reference face
# (analog of palace.meshing._faces_within_freecad_face).
# ---------------------------------------------------------------------------

def _face_plane(fc_face):
    n = fc_face.normalAt(0, 0)
    n.normalize()
    com = fc_face.CenterOfMass
    return (n.x, n.y, n.z, n.x * com.x + n.y * com.y + n.z * com.z)


def _match_occ_faces(candidate_faces, fc_face, min_tol=0.05):
    """Return the occ.Face objects among candidate_faces that are coplanar
    with and contained inside fc_face's boundary.

    Pre-mesh geometry-level match (mirrors palace.meshing.
    _faces_within_freecad_face's coplanar+containment technique -- distToShape
    against the real FreeCAD face, not a bounding-box heuristic), operating
    directly on netgen.occ Face objects rather than post-mesh triangle data.
    A FreeCAD face can correspond to several occ.Face pieces after Glue()
    fragmentation, so this returns every match, not just the first.

    Tolerance scales with fc_face's own size (sqrt(area) * 0.3, floored at
    min_tol, capped at max_tol) rather than using a single fixed absolute
    value. Verified necessary on a real board: a small (~2mm^2) annular coax
    wave port face matched a same-area, exactly-coplanar candidate whose
    netgen-computed centroid sat 0.255mm from the reference face --
    comfortably real geometry (confirmed by the matching area), just outside
    a fixed 0.05mm tolerance, apparently from a centroid-convention
    difference on an annular shape between FreeCAD's CenterOfMass and
    netgen's own Face.center. A relative tolerance absorbs this while still
    easily rejecting an unrelated ~136mm^2 candidate (the airbox wall) whose
    actual distance was 1.8mm. The cap matters just as much as the floor:
    without one, matching a large reference face (e.g. an AbsorbingFaces
    override spanning a whole airbox wall, thousands of mm^2) produces a
    multi-mm tolerance loose enough to indiscriminately sweep up unrelated
    nearby faces -- confirmed on a real board, every single boundary element
    in the whole mesh ended up re-tagged to that one override's attribute.

    An area cap is also required, independent of the tolerance: on a real
    wave port, one candidate fragment covered the *rest* of the airbox
    wall's open-air region (spanning nearly the entire wall, not just the
    port's own footprint) yet its own centroid still landed inside fc_face's
    boundary -- the wall was fragmented at the substrate/air interface
    (a real, unrelated dielectric boundary spanning the whole wall) but NOT
    at the port's own x-extent for that oversized fragment, and the port's
    rectangle happened to be x-centered within the wall, so the oversized
    fragment's centroid coincidentally fell inside fc_face's footprint --
    passing both the plane and distToShape checks despite being nowhere
    close to a genuine sub-piece of fc_face. A true fragment of fc_face can
    never have more area than fc_face itself, so any candidate exceeding it
    (beyond a small tessellation/roundoff slack) is rejected outright,
    regardless of centroid position.

    A flatness check (the candidate's own vertices must all lie within
    min_tol of fc_face's plane, not just its centroid) is required for the same
    reason but the opposite failure mode: on a real impedance boundary
    (a thin lumped-port box only 0.1mm tall), the box's own *vertical
    side walls* -- a completely different orientation from the target's
    flat top face -- still matched, because a side wall's centroid sits at
    the box's half-height, which happened to fall within tol of the top
    face's plane purely from the box being so short. Checking only the
    centroid can't tell a genuinely flat, coplanar sub-piece from a
    perpendicular face that merely straddles the same plane at its middle.
    """
    import Part as FCPart

    nx, ny, nz, plane_d = _face_plane(fc_face)
    max_tol = 0.5
    tol = min(max(min_tol, (fc_face.Area ** 0.5) * 0.3), max_tol)
    max_area = fc_face.Area * 1.05
    matched = []
    for f in candidate_faces:
        if f.mass > max_area:
            continue
        c = f.center
        if abs(nx * c[0] + ny * c[1] + nz * c[2] - plane_d) >= tol:
            continue
        # Deliberately NOT the scaled `tol` above -- that tolerance exists
        # to absorb a centroid-convention mismatch on the target's own
        # scale, not to judge a candidate's flatness. A perpendicular face
        # merely straddling the target plane at its middle would pass a
        # scaled tolerance just as easily as the centroid did; a genuine
        # coplanar sub-fragment's vertices sit on the plane to BREP
        # precision regardless of fc_face's size, so this stays a small
        # fixed tolerance.
        verts = list(f.vertices)
        if any(abs(nx * v.p[0] + ny * v.p[1] + nz * v.p[2] - plane_d) >= min_tol
               for v in verts):
            continue
        dist, _, _ = fc_face.distToShape(FCPart.Vertex(FreeCAD.Vector(*c)))
        if dist > tol:
            continue
        matched.append(f)
    return matched


def _bc_tag(attr):
    return f"attr:{attr}"


def _attr_from_bcname(bcname, default_attr):
    if bcname and bcname.startswith("attr:"):
        return int(bcname.split(":", 1)[1])
    return default_attr


# ---------------------------------------------------------------------------
# $MeshFormat version fix (Gmsh2 export post-processing)
# ---------------------------------------------------------------------------

def _fix_mesh_format_version(msh_path, log_fn):
    """Rewrite the $MeshFormat header's version field to "2.2".

    Netgen's own "Gmsh2 Format" exporter writes "2.000000 0 8" -- verified
    empirically by reading the actual file, not assumed -- which MFEM's
    Gmsh reader rejects outright ("MFEM abort: Gmsh file version < 2.2",
    found only when a real Palace run was tried against Netgen output, not
    caught by anything in this module's own testing since nothing here
    ever parses $MeshFormat itself). palace.meshing's own Gmsh pipeline
    never hits this because it explicitly forces
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2) before writing --
    Netgen's Python API has no equivalent option, so this patches the
    already-written file the same way _rewrite_physical_tags and
    _inject_line_elements already do for other gaps in Netgen's exporter.
    """
    with open(msh_path, "r") as f:
        lines = f.readlines()
    idx = lines.index("$MeshFormat\n") + 1
    parts = lines[idx].split()
    if parts and parts[0] != "2.2":
        old = parts[0]
        parts[0] = "2.2"
        lines[idx] = " ".join(parts) + "\n"
        with open(msh_path, "w") as f:
            f.writelines(lines)
        log_fn(f"Palace: Fixed $MeshFormat version ({old} -> 2.2) -- MFEM "
               f"rejects anything below 2.2.\n")


# ---------------------------------------------------------------------------
# Physical-group tag remap (Gmsh2 export post-processing)
# ---------------------------------------------------------------------------

_ELEM_TYPE_VOL = {4, 11}    # linear tet, quadratic tet
_ELEM_TYPE_SURF = {2, 9}    # linear tri, quadratic tri


def _rewrite_physical_tags(msh_path, vol_remap, surf_remap, drop_surfnrs, log_fn):
    """Rewrite the Gmsh2 $Elements physical-group tag columns in place.

    Netgen's own Gmsh2 exporter ignores .name entirely: volumes come out
    tagged 100000+domain_index and surfaces tagged FaceDescriptor.surfnr --
    verified empirically, not documented behavior, so every tag actually
    found in the file is checked against the remap and any unmapped tag
    raises loudly rather than silently writing a wrong physical group
    (Palace itself cross-references these numbers against config.py's
    "Attributes" lists -- a silent mismatch would misassign boundary
    conditions or materials without any visible error).

    drop_surfnrs are surface elements deliberately excluded (internal
    material-interface faces with no explicit tag -- see the caller) rather
    than unmapped by mistake; their element lines are removed outright
    instead of being rewritten, and the $Elements header count is adjusted
    to match so the file stays internally consistent for both MFEM and
    _inject_line_elements's own line count afterward.
    """
    with open(msh_path, "r") as f:
        lines = f.readlines()

    in_elements = False
    n_rewritten = 0
    n_dropped = 0
    unmapped = set()
    kept_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == "$Elements":
            in_elements = True
            kept_lines.append(line)
            continue
        if stripped == "$EndElements":
            in_elements = False
            kept_lines.append(line)
            continue
        if not in_elements:
            kept_lines.append(line)
            continue
        parts = stripped.split()
        if len(parts) < 4:
            kept_lines.append(line)
            continue
        try:
            etype = int(parts[1])
            n_tags = int(parts[2])
        except ValueError:
            kept_lines.append(line)
            continue
        if n_tags < 1:
            kept_lines.append(line)
            continue
        old_tag = int(parts[3])
        if etype in _ELEM_TYPE_VOL:
            new_tag = vol_remap.get(old_tag)
        elif etype in _ELEM_TYPE_SURF:
            if old_tag in drop_surfnrs:
                n_dropped += 1
                continue
            new_tag = surf_remap.get(old_tag)
        else:
            kept_lines.append(line)
            continue
        if new_tag is None:
            unmapped.add((etype, old_tag))
            kept_lines.append(line)
            continue
        parts[3] = str(new_tag)
        if n_tags >= 2:
            parts[4] = str(new_tag)
        kept_lines.append(" ".join(parts) + "\n")
        n_rewritten += 1

    if unmapped:
        raise RuntimeError(
            f"Netgen mesh export produced {len(unmapped)} element tag(s) with "
            f"no known physical-group mapping: {sorted(unmapped)}. This means "
            "some mesh region was not assigned a boundary condition or "
            "material -- refusing to write a mesh Palace would silently "
            "misinterpret."
        )

    if n_dropped:
        count_idx = kept_lines.index("$Elements\n") + 1
        n_remaining = int(kept_lines[count_idx].strip()) - n_dropped
        kept_lines[count_idx] = f"{n_remaining}\n"

    with open(msh_path, "w") as f:
        f.writelines(kept_lines)
    log_fn(f"Palace: Remapped {n_rewritten} element physical-group tag(s) to "
           f"their real MeshAttribute numbers"
           + (f", dropped {n_dropped} internal material-interface element(s)"
              if n_dropped else "") + ".\n")


# ---------------------------------------------------------------------------
# Wave-port outer boundary curves (1D PEC elements)
# ---------------------------------------------------------------------------
#
# MFEM needs 1D PEC elements at a wave port's own outer boundary curve --
# without them the wave port eigenvalue problem is unconstrained and
# produces a singular matrix (palace.meshing's own comment on its
# equivalent PortBoundaryCurves physical group). Netgen's Elements1D()/
# EdgeDescriptors() exist in the in-memory mesh, but mesh.Export(...,
# "Gmsh2 Format") never writes any type-1/type-8 (line) elements at all --
# verified empirically on a plain box: 12 tagged edges' worth of
# Elements1D() present in the mesh object, zero line elements in the
# exported file. Tagging an occ.Edge with .bc() before meshing also
# doesn't propagate to EdgeDescriptor.name the way it does for faces
# (verified: every EdgeDescriptor read back "default" regardless).
#
# Rather than fight either of those, this is computed directly from the
# ALREADY-CORRECTLY-TAGGED 2D mesh: an edge belongs to a wave port's own
# outer boundary if it borders exactly one wave-port-tagged triangle
# (mirrors palace.meshing's identical "a curve shared by two sub-faces is
# an interior substrate/air interface, not outer" counting logic, applied
# globally across all wave ports at once -- palace.meshing's own
# PortBoundaryCurves is a single combined group too, not one per port).
# The resulting elements are appended directly into the exported file's
# $Elements section, since nothing else will ever produce them.

def _triangle_edge_midside(points, corner_a, corner_b):
    """midside node for the edge between points[corner_a] and points[corner_b]
    of a Netgen 2nd-order triangle. Netgen's 6-node layout (verified
    empirically): points[3:] are the midsides, with points[3+i] opposite
    corner i (i.e. the midpoint of the edge between the OTHER two corners) --
    the standard Gmsh/VTK quadratic-triangle convention.
    """
    if len(points) < 6:
        return None
    opposite = 3 - corner_a - corner_b   # the corner index NOT in this edge
    return points[3 + opposite]


def _wave_port_boundary_edges(mesh, wave_port_surfnrs):
    """Return [((n0, n1), midside_or_None), ...] for edges on the outer
    boundary of the combined wave-port surface region.

    Matches palace.meshing's PortBoundaryCurves exactly: the outer boundary
    of the union of all wave-port faces gets a PEC line element regardless
    of whether that edge also touches a conductor (e.g. a microstrip port's
    edge coincident with the ground plane needs this too -- it is what
    constrains the 2-D wave-port eigenvalue problem there). palace.meshing
    does NOT exclude conductor-adjacent curves from this group; the
    analogous triple-edge conflict it avoids via cond_skip_planes is a
    fragment-based artifact (a separate, coincident conductor *face*
    sharing the same edge) that doesn't arise here -- ports win over
    conductors in the pre-mesh .bc() tagging pass, so there is only ever
    one 2-D triangle at that location, never two.
    """
    edge_count = {}
    edge_mid = {}
    for e in mesh.Elements2D():
        if e.index not in wave_port_surfnrs:
            continue
        pts_ids = list(e.points)
        for a in range(3):
            b = (a + 1) % 3
            key = frozenset((pts_ids[a], pts_ids[b]))
            edge_count[key] = edge_count.get(key, 0) + 1
            edge_mid[key] = _triangle_edge_midside(pts_ids, a, b)
    return [(tuple(k), edge_mid[k]) for k, c in edge_count.items() if c == 1]


def _inject_line_elements(msh_path, edges, attr, log_fn, label):
    """Append 1D line elements (attr-tagged) directly into an already-
    written Gmsh2 file's $Elements section -- see the module note above for
    why this can't just be mesh.Export()'d normally. 3-node curved lines
    (type 8) when a midside node is available, else 2-node (type 1).
    """
    if not edges:
        return
    with open(msh_path, "r") as f:
        lines = f.readlines()

    start = lines.index("$Elements\n")
    count_idx = start + 1
    end = lines.index("$EndElements\n")
    n_existing = int(lines[count_idx].strip())
    max_id = max((int(line.split()[0]) for line in lines[count_idx + 1:end]),
                 default=0)

    new_lines = []
    next_id = max_id + 1
    for (n0, n1), mid in edges:
        if mid is not None:
            new_lines.append(f"{next_id} 8 2 {attr} {attr} {n0} {n1} {mid}\n")
        else:
            new_lines.append(f"{next_id} 1 2 {attr} {attr} {n0} {n1}\n")
        next_id += 1

    lines[count_idx] = f"{n_existing + len(new_lines)}\n"
    lines[end:end] = new_lines

    with open(msh_path, "w") as f:
        f.writelines(lines)
    log_fn(f"Palace: Injected {len(new_lines)} {label} 1D element(s) "
           f"(attr {attr}) -- Netgen's own exporter never writes these.\n")


# ---------------------------------------------------------------------------
# Quality gate: real curved (2nd-order isoparametric) Jacobian sampling
# ---------------------------------------------------------------------------
#
# Netgen's own GetQualityHistogram()/CalcMinMaxAngle() only operate on the
# straight-sided mesh -- calling them after SecondOrder()+Curve() returns
# empty/garbage data, verified empirically. Gmsh's own quality gate runs
# AFTER curving, on the actual curved elements Palace solves on, so a
# straight-sided proxy isn't good enough -- this computes the Jacobian of
# the real P2 isoparametric map at several points inside each curved
# tetrahedron, exactly the "P2-isoparametric-Jacobian sampling" the original
# migration doc called for and flagged as needing to be built, not assumed
# to exist.
#
# Metric: the scaled Jacobian (det(J) normalized by the product of the three
# column norms of J) at each sample point, worst value over all samples
# wins for that element. This is a standard, simple FEM element-quality
# metric (used by e.g. ANSYS/ABAQUS) -- max ~1 for a "cube corner" shape,
# ~0.71 for a perfectly regular tetrahedron (verified against a synthetic
# regular tet), 0 for degenerate, and reliably negative for a genuine
# inversion including a LOCALIZED curvature-induced fold that a straight-
# sided-only check would miss entirely (verified against a synthetic
# element with one midside node pushed out to simulate exactly that).
# Not pixel-identical to Gmsh's own minSICN scale, but the existing default
# MeshQualityWarnThreshold=0.1 remains a sensible "close to degenerate"
# cutoff on this scale too, since a healthy element sits far above it.

_P2_EDGE_PAIRS = list(itertools.combinations(range(4), 2))  # matches Netgen's
                                                             # 10-node midside order (verified empirically)

_P2_REF_CORNERS = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
_P2_SAMPLE_POINTS = list(_P2_REF_CORNERS)
for _a, _b in _P2_EDGE_PAIRS:
    _ca, _cb = _P2_REF_CORNERS[_a], _P2_REF_CORNERS[_b]
    _P2_SAMPLE_POINTS.append(tuple((x + y) / 2 for x, y in zip(_ca, _cb)))
_P2_SAMPLE_POINTS.append((0.25, 0.25, 0.25))  # centroid


def _p2_shape_derivs(xi, eta, zeta):
    """dN_i/d(xi,eta,zeta) for the 10 P2 tetrahedron shape functions, at one
    reference-space point. Standard barycentric formulation: corner shape
    functions L_i(2L_i-1), midside shape functions 4*L_a*L_b for edge (a,b).
    """
    L = [1 - xi - eta - zeta, xi, eta, zeta]
    dL = [(-1.0, -1.0, -1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    dN = []
    for i in range(4):
        c = 4 * L[i] - 1
        dN.append(tuple(c * d for d in dL[i]))
    for a, b in _P2_EDGE_PAIRS:
        dN.append(tuple(4 * (dL[a][k] * L[b] + L[a] * dL[b][k]) for k in range(3)))
    return dN  # list of 10 (dx,dy,dz) tuples


# DN_ALL: (n_samples, 10, 3) shape-function-derivative table, precomputed
# once at import time -- the same values _p2_shape_derivs computes per call,
# just batched for the vectorized quality check below.
_DN_ALL = np.array([_p2_shape_derivs(*p) for p in _P2_SAMPLE_POINTS])


def _curved_tet_quality_batch(coords_all):
    """coords_all: (N, 10, 3) array of actual curved node positions for N
    elements, Netgen ordering. Returns (N,) worst scaled-Jacobian per
    element, vectorized across all elements and sample points at once.

    Sign flipped relative to the "textbook" scaled-Jacobian formula.
    Verified empirically (two independent ways: this formula, and a plain
    scalar triple product on just the 4 corners) that Netgen's own corner
    ordering for Elements3D() gives a NEGATIVE signed volume under the
    standard right-handed convention for every single element of a
    definitely-valid mesh (a plain box) -- a consistent chirality
    convention, not a real inversion (matches an identical finding earlier
    in this migration when checking straight-sided elements the same way).
    Flipping the sign here means a genuinely healthy element reads positive
    and an actual inversion still reads negative, as intended.

    A pure-Python per-element version of this (looping over elements and
    sample points, calling math.sqrt/manual 3x3 determinants) was tried
    first and measured at ~80s for 150k elements alone -- confirmed exactly
    equivalent numerically (0 mismatches across every element of a real
    test mesh) but too slow for a real board. This computes every
    element's Jacobian at every sample point in one batched einsum instead.
    """
    # J[n,s,j,k] = sum_i coords_all[n,i,j] * _DN_ALL[s,i,k]
    J = np.einsum('nij,sik->nsjk', coords_all, _DN_ALL)  # (N, S, 3, 3)
    det = -np.linalg.det(J)                              # (N, S)
    col_norms = np.linalg.norm(J, axis=2)                 # (N, S, 3)
    denom = col_norms[..., 0] * col_norms[..., 1] * col_norms[..., 2]
    safe_denom = np.where(denom > 1e-14, denom, 1.0)
    sj = np.where(denom > 1e-14, det / safe_denom, 0.0)
    return sj.min(axis=1)


def _check_curved_mesh_quality(mesh, log_fn, threshold):
    """Real curved-element quality gate -- see module note above. Matches
    _check_mesh_quality()'s exact dict contract so format_quality_warning()
    and every UI call site work unchanged regardless of backend.
    """
    if threshold <= 0.0:
        return {"min_quality": None, "n_elements": 0, "n_bad": 0,
                "bad_fraction": 0.0, "is_bad": False}

    els = list(mesh.Elements3D())
    n = len(els)
    if n == 0:
        return {"min_quality": None, "n_elements": 0, "n_bad": 0,
                "bad_fraction": 0.0, "is_bad": False}

    # Gather each element's 10 node coordinates, deduplicating shared nodes
    # via PointId.nr as a plain hashable key (MeshPoint objects from
    # mesh.Points() have no .nr of their own -- pts[nid] is still the
    # lookup, .nr is only used here to dedupe/index into our own array).
    pts = mesh.Points()
    node_index = {}
    coords_list = []
    elem_ids = np.empty((n, 10), dtype=np.int64)
    for row, e in enumerate(els):
        for col, nid in enumerate(e.points):
            key = nid.nr
            idx = node_index.get(key)
            if idx is None:
                idx = len(coords_list)
                node_index[key] = idx
                coords_list.append(tuple(pts[nid]))
            elem_ids[row, col] = idx
    coords_all = np.asarray(coords_list)[elem_ids]  # (N, 10, 3)

    quals = _curved_tet_quality_batch(coords_all)
    min_quality = float(quals.min())
    n_bad = int(np.count_nonzero(quals < threshold))

    result = {
        "min_quality": min_quality,
        "n_elements": n,
        "n_bad": n_bad,
        "bad_fraction": n_bad / n,
        "is_bad": n_bad > 0,
    }
    if result["is_bad"]:
        log_fn(
            f"Palace: WARNING — mesh quality check found {n_bad}/{n} tetrahedra "
            f"below quality {threshold:.3g} (worst = {min_quality:.4g}, curved "
            f"scaled-Jacobian). Degenerate/sliver elements can cause Palace "
            f"solver instability or inaccurate results.\n"
        )
    else:
        log_fn(f"Palace: Mesh quality check passed ({n} tetrahedra, worst = "
               f"{min_quality:.4g}).\n")
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_mesh_netgen(doc, output_dir, log_fn=None):
    """
    Generate a Netgen mesh for the active Palace simulation.

    Same signature and (mesh_path, geometry_path, quality) return contract
    as palace.meshing.generate_mesh() -- every existing call site
    (cmd_mesh.py, cmd_run.py, cmd_sweep.py via mesh_runner.py) works
    unchanged against either backend.

    Parameters
    ----------
    doc        : FreeCAD document
    output_dir : directory where geometry.step and mesh.msh will be written
    log_fn     : callable(str) for progress messages; defaults to
                 FreeCAD.Console.PrintMessage.

    Returns
    -------
    (str, str, dict) : absolute paths to the written .msh file and .step
                        file, and a mesh-quality summary.
    """
    if log_fn is None:
        log_fn = lambda msg: FreeCAD.Console.PrintMessage(
            msg if msg.endswith("\n") else msg + "\n"
        )

    import Part

    occ, ngm = _netgen()

    sim, airbox, lumped_ports, wave_ports, impedance_boundaries = (
        _locate_palace_objects(doc))

    if airbox is None:
        raise RuntimeError("No Airbox object found. Create an Airbox before generating the mesh.")
    _airbox_solid = _resolve_airbox_solid(airbox)
    if _airbox_solid is None:
        raise RuntimeError("Airbox has no shape assigned. Open the Airbox settings and add a solid.")

    all_ports = sorted(
        lumped_ports + wave_ports + impedance_boundaries,
        key=lambda o: o.PortIndex
    )

    diel_bodies, cond_bodies = _collect_material_bodies(doc)

    # --- Background export: airbox + dielectrics + extra port-parent solids,
    # exactly mirroring generate_mesh()'s export_objs list. ----------------
    export_objs = [_airbox_solid]
    for _, solid in diel_bodies:
        if solid not in export_objs:
            export_objs.append(solid)
    for extra in _port_shape_parents(doc):
        if extra not in export_objs:
            export_objs.append(extra)

    for obj in export_objs:
        _validate_shape(obj.Shape, obj.Label, log_fn)

    step_path = os.path.join(output_dir, "geometry.step")
    Part.export(export_objs, step_path)

    background_shape = occ.OCCGeometry(step_path).shape
    bg_solids = list(background_shape.solids)
    log_fn(f"Palace: {len(bg_solids)} background solid(s) loaded from STEP\n")

    # Reference list for COM matching: (label, com, kind, mesh_attribute)
    reference = []
    solid_manifest = [{"kind": "airbox", "label": _airbox_solid.Label,
                        "com": tuple(_airbox_solid.Shape.CenterOfGravity),
                        "attr": 1}]
    for grp, solid in diel_bodies:
        solid_manifest.append({
            "kind": "dielectric", "label": solid.Label,
            "com": tuple(solid.Shape.CenterOfGravity), "attr": grp.MeshAttribute,
        })
    for s in bg_solids:
        com = tuple(s.center)
        best, best_d = None, float("inf")
        for m in solid_manifest:
            d = _dist_sq(com, m["com"])
            if d < best_d:
                best_d, best = d, m
        reference.append((best["kind"] + ":" + best["label"], com, best["kind"], best["attr"]))
        s.name = best["kind"] + ":" + best["label"]

    # --- Conductors: fuse + BREP export, exactly mirroring
    # _import_conductor_solids's approach. --------------------------------
    by_group = {}
    order = []
    for grp, solid in cond_bodies:
        if grp not in by_group:
            by_group[grp] = []
            order.append(grp)
        by_group[grp].append(solid)

    conductor_shapes = []
    for grp in order:
        shape, label = _fuse_group_solids(grp, by_group[grp], log_fn)
        brep_path = tempfile.mktemp(suffix=".brep")
        try:
            shape.exportBrep(brep_path)
            cshape = occ.OCCGeometry(brep_path).shape
        finally:
            try:
                os.unlink(brep_path)
            except OSError:
                pass
        for s in cshape.solids:
            com = tuple(s.center)
            entry_label = "cond:" + grp.Label
            s.name = entry_label
            reference.append((entry_label, com, "conductor", grp.MeshAttribute))
        conductor_shapes.append(cshape)
        log_fn(f"Palace: Conductor '{grp.Label}' -> "
               f"{len(list(cshape.solids))} solid(s) imported\n")

    # --- Port faces needing explicit embedding -----------------------------
    # EVERY port face is embedded via its own individual BREP import into the
    # combined Glue() pass, never matched only against whatever the bulk
    # background STEP export happened to carry. Verified necessary even for
    # wave ports, whose PortFaces commonly reference a bare 2-D reference
    # Part::Plane object (not a real face of an existing background solid):
    # relying on that plane's geometry surviving Part.export()'s bulk,
    # mixed-dimension STEP export was tried first and found unreliable on a
    # real board -- two of three wave ports shared one physical airbox wall,
    # and the wall came through Glue() as one *unfragmented* whole-wall face,
    # not split at each port's own boundary, because the loose reference
    # planes' geometry didn't round-trip through the bulk STEP export
    # faithfully enough for OCC to fragment against. Individually
    # BREP-exporting and importing just the one face at a time -- exactly
    # palace.meshing's own proven pattern for wave ports, and already the
    # working approach here for lumped ports needing the antipodal-vertex
    # rebuild -- sidesteps the bulk-export fidelity question entirely.
    embed_shapes = []   # [(port_obj, shape)]

    def _brep_roundtrip(fc_face):
        brep_path = tempfile.mktemp(suffix=".brep")
        try:
            fc_face.exportBrep(brep_path)
            return occ.OCCGeometry(brep_path).shape
        finally:
            try:
                os.unlink(brep_path)
            except OSError:
                pass

    for port in wave_ports:
        for link_obj, subnames in (port.PortFaces or []):
            # Only embed when the face's parent has no solid geometry of its
            # own (a bare reference object, like Wilkinson's helper
            # Part::Plane objects). When the parent IS a real solid already
            # part of the background/conductor geometry (e.g. a wave port
            # referencing a face of its own dielectric body, as on the coax
            # board), that face is already genuinely part of what gets
            # glued -- separately BREP-importing and re-gluing the SAME face
            # as an extra standalone object confuses OCC's fragmentation of
            # the real solid at that exact location (verified: 0 of 3 wave
            # ports matched anything after mesh generation on that board,
            # vs. correct matches once this case skips explicit embedding
            # and matches directly against the already-fragmented
            # background instead).
            needs_embedding = len(link_obj.Shape.Solids) == 0
            for subname in subnames:
                try:
                    fc_face = link_obj.Shape.getElement(subname)
                except Exception as exc:
                    log_fn(f"Palace: WARNING — could not resolve wave port "
                           f"{port.PortIndex} face '{subname}': {exc}\n")
                    continue
                if needs_embedding:
                    embed_shapes.append((port, _brep_roundtrip(fc_face)))

    # ImpedanceBoundary shares the identical PortEdges/PortGeometryType/Shape
    # structure lumped ports use (only its electrical properties, Rs vs
    # R/L/C, differ -- irrelevant to meshing) -- confirmed on a real board:
    # Wilkinson's ImpedanceBoundary has real planar PortEdges data, but was
    # never embedded or matched here because this loop only iterated
    # lumped_ports, not impedance_boundaries too. That's the exact cause of
    # every "no mesh surfaces matched for port 4" warning seen throughout
    # testing -- not missing document data, a missing port-type check.
    for port in lumped_ports + impedance_boundaries:
        if not (hasattr(port, "PortEdges") and port.PortEdges):
            continue
        if not (hasattr(port, "Shape") and port.Shape.Faces):
            log_fn(f"Palace: WARNING — port {port.PortIndex} has PortEdges "
                   "but no computed Shape; run doc.recompute() before meshing.\n")
            continue
        try:
            eshape = _build_lumped_port_face(occ, port.Shape)
        except Exception as exc:
            log_fn(f"Palace: WARNING — could not build port "
                   f"{port.PortIndex} face: {exc}\n")
            continue
        embed_shapes.append((port, eshape))

    # --- Wave port integration edges: embedded + locally sized -------------
    # Palace's Z_PV impedance calc (_probe_points_along_edge, see
    # palace/config.py and CLAUDE.md's WavePort invariant) samples the FEM
    # solution along this edge for trapezoidal integration; palace.meshing's
    # own equivalent (_apply_port_transfinite's sibling logic for wave
    # ports) forces at least 8 segments along it "regardless of the global
    # cl_max setting" specifically so that integration accuracy doesn't
    # silently degrade on a coarsely-sized model. Embedding is the same
    # Glue()-based pattern already proven for lumped port faces and wave
    # port faces above; a bare 1-D edge glued into a solid conforms mesh
    # nodes to it without splitting the volume (verified: glued solid count
    # stayed at 1, not 2, in a minimal repro).
    integration_edges = []   # [(port, occ_edge_shape)]
    for port in wave_ports:
        ie = getattr(port, "IntegrationEdge", None)
        if not ie or not ie[0]:
            continue
        link_obj, subs = ie
        if not subs:
            continue
        try:
            fc_edge = link_obj.Shape.getElement(subs[0])
        except Exception as exc:
            log_fn(f"Palace: WARNING — could not resolve wave port "
                   f"{port.PortIndex} integration edge: {exc}\n")
            continue
        eshape = _brep_roundtrip(fc_edge)
        min_n = 8
        edge_len = fc_edge.Length
        for e in eshape.edges:
            e.maxh = max(edge_len / min_n, 0.01)
        integration_edges.append((port, eshape))
        log_fn(f"Palace: Wave port {port.PortIndex} integration edge "
               f"embedded (length {edge_len:.4g}, maxh <= "
               f"{edge_len/min_n:.4g} for >= {min_n} segments)\n")

    # --- Combined Glue: background + conductors + embedded port faces -----
    all_shapes = ([background_shape] + conductor_shapes + [s for _, s in embed_shapes]
                  + [s for _, s in integration_edges])
    n_in_solids = sum(len(list(s.solids)) for s in [background_shape] + conductor_shapes)
    log_fn(f"Palace: Gluing {n_in_solids} solid(s), "
           f"{len(embed_shapes)} embedded port face(s), and "
           f"{len(integration_edges)} integration edge(s)...\n")
    glued = occ.Glue(all_shapes)
    glued_solids = list(glued.solids)
    log_fn(f"Palace: Post-Glue: {len(glued_solids)} solid(s)\n")

    resolved = []   # (solid, label, kind, attr)
    for s in glued_solids:
        label, com, kind, attr = _match_reference(tuple(s.center), reference)
        s.name = label
        resolved.append((s, label, kind, attr))

    # --- Boundary-condition tagging via .bc() -- BEFORE dropping conductor
    # solids or generating the mesh. -----------------------------------------
    # netgen.occ's .bc() sets a name property directly on the underlying OCC
    # face entities (verified: calling it on a solid about to be dropped
    # still shows up on the shared boundary face as seen from the KEPT
    # neighboring solid -- the two solids reference the same face object).
    # This sidesteps needing any post-mesh domin/domout inference to recover
    # "which now-removed conductor did this boundary face belong to": the
    # tag simply survives, and FaceDescriptor.bcname reads back directly.
    #
    # Ordering matters: conductor bulk-tagging runs first, port face-precise
    # tagging runs after and overwrites it on any coincident face, giving
    # ports priority -- the same "ports take priority over conductor PEC"
    # rule palace.meshing's own comments document.
    # Deliberately NOT honoring a conductor group's Gmsh-era MeshSize
    # property as a flat face-level maxh here. Tried, and found actively
    # harmful on a real board: Gmsh's MeshSize is meant to drive a
    # *distance-tapered* Threshold field (_apply_refinement_fields, no
    # Netgen equivalent built yet) that relaxes back to the coarse global
    # size away from the feature. Applying the same raw number as an
    # unconditional cap across an entire conductor's faces is a much more
    # aggressive, much more expensive thing -- confirmed on
    # Coax_Directional_Bridge.FCStd, whose "Copper" group carries an
    # existing MeshSize=0.05mm (tuned for Gmsh's tapered field): applying
    # it as a flat cap here made GenerateMesh() run indefinitely (10+
    # minutes, still climbing) on a board that meshes in ~20s under pure
    # automatic sizing. This exactly reproduces the flat-cap anti-pattern
    # already found counterproductive during this migration's own spike
    # work. Automatic sizing alone, with no manual maxh at all, is what's
    # validated on real boards -- leave conductors on automatic until a
    # real distance-tapered field equivalent is built.
    for s, label, kind, attr in resolved:
        if kind != "conductor":
            continue
        s.bc(_bc_tag(attr))

    handled_port_indices = set()
    # Candidate faces for port (and outer-boundary-override) matching: only
    # BACKGROUND solids' own faces -- never a conductor's. A wave port's
    # defining plane commonly spans both the metal trace's own cross-section
    # (which must stay PEC, not become part of the port) and the surrounding
    # dielectric/air around it (the real port excitation area) -- exactly
    # why palace.meshing's own Gmsh pipeline has _touches_cond_interior to
    # reject conductor-touching port candidates. Verified empirically on a
    # real 3-port board: restricting candidates to background solids only
    # was necessary -- without it, 2 of 3 wave ports matched only the
    # conductor's own end-cap face (a single face, not the surrounding
    # background material), which then silently vanished once that
    # conductor's solid was dropped, since it was never a boundary of any
    # kept solid to begin with.
    background_faces = [f for s, _, kind, _ in resolved if kind != "conductor"
                         for f in s.faces]

    def _tag_port_faces(port, fc_faces):
        matched_total = []
        for fc_face in fc_faces:
            matches = _match_occ_faces(background_faces, fc_face)
            if not matches:
                log_fn(f"Palace: WARNING — no geometry matched port "
                       f"{port.PortIndex} face\n")
                continue
            for f in matches:
                f.bc(_bc_tag(port.MeshAttribute))
            matched_total.extend(matches)
        if matched_total:
            handled_port_indices.add(port.PortIndex)
            log_fn(f"Palace: Port {port.PortIndex} -> {len(matched_total)} "
                   f"face(s) tagged (attr {port.MeshAttribute})\n")

    # Ports embedded above (wave + lumped): match using the exact same
    # FreeCAD face geometry that was BREP-round-tripped and glued in, now
    # that the background has genuinely been fragmented at its boundary.
    embedded_ports = {port for port, _ in embed_shapes}
    for port in wave_ports:
        if port not in embedded_ports:
            continue
        fc_faces = []
        for link_obj, subnames in (port.PortFaces or []):
            for subname in subnames:
                try:
                    fc_faces.append(link_obj.Shape.getElement(subname))
                except Exception as exc:
                    log_fn(f"Palace: WARNING — could not resolve wave port "
                           f"{port.PortIndex} face '{subname}': {exc}\n")
        _tag_port_faces(port, fc_faces)
    for port in lumped_ports + impedance_boundaries:
        if port in embedded_ports:
            _tag_port_faces(port, [port.Shape.Faces[0]])

    for port in all_ports:
        if port.PortIndex in handled_port_indices:
            continue
        fc_faces = []
        for link_obj, subnames in (port.PortFaces or []):
            for subname in subnames:
                try:
                    fc_faces.append(link_obj.Shape.getElement(subname))
                except Exception as exc:
                    log_fn(f"Palace: WARNING — could not resolve port "
                           f"{port.PortIndex} face '{subname}': {exc}\n")
        _tag_port_faces(port, fc_faces)
        if port.PortIndex not in handled_port_indices:
            log_fn(f"Palace: WARNING — no mesh surfaces matched for port "
                   f"{port.PortIndex}\n")

    # Per-face outer-boundary overrides (PECFaces/PMCFaces/AbsorbingFaces),
    # applied last so they can override the eventual "default" bcname but
    # not clobber a port/conductor tag on a well-formed model.
    for prop_name, attr in (("PECFaces", 2), ("PMCFaces", 3), ("AbsorbingFaces", 4)):
        for link_obj, subnames in (getattr(airbox, prop_name, None) or []):
            for subname in (subnames or []):
                try:
                    fc_face = link_obj.Shape.getElement(subname)
                except Exception as exc:
                    log_fn(f"Palace: WARNING — could not resolve {prop_name} "
                           f"face '{subname}': {exc}\n")
                    continue
                matches = _match_occ_faces(background_faces, fc_face)
                for f in matches:
                    f.bc(_bc_tag(attr))

    # --- Drop conductor domains, mesh what's left ---------------------------
    keep_solids = [s for s, label, kind, attr in resolved if kind != "conductor"]
    drop_solids = [s for s, label, kind, attr in resolved if kind == "conductor"]
    log_fn(f"Palace: Keeping {len(keep_solids)} background solid(s), dropping "
           f"{len(drop_solids)} conductor solid(s) before mesh generation\n")

    if not keep_solids:
        raise RuntimeError(
            "No volumes found after conductor removal. Ensure the Airbox "
            "shape is a closed solid."
        )

    # Compound (not a second Glue) -- keep_solids are already mutually
    # conformal from the first Glue pass, and Compound is a pure bundling
    # operation verified to preserve the .bc()/.maxh face tags set above
    # (a re-Glue's tag-preservation was untested and best avoided).
    remaining = occ.Compound(keep_solids) if len(keep_solids) > 1 else keep_solids[0]
    geo = occ.OCCGeometry(remaining)

    log_fn("Palace: Generating mesh (Netgen automatic local sizing)...\n")
    mesh = geo.GenerateMesh()

    n_domains = max((e.index for e in mesh.Elements3D()), default=0)
    if n_domains == 0:
        raise RuntimeError("Netgen produced zero 3D elements.")

    # domain index -> attr, via the same COM-matched resolved list (a
    # domain's material name is whatever nearest-solid survived Glue).
    domain_attr = {}
    for i in range(1, n_domains + 1):
        mat_name = mesh.GetMaterial(i)
        match = next((r for r in resolved if r[1] == mat_name and r[2] != "conductor"), None)
        if match is None:
            raise RuntimeError(
                f"Netgen domain {i} ('{mat_name}') did not match any known "
                "background material after conductor removal -- geometry "
                "bookkeeping is out of sync."
            )
        domain_attr[i] = match[3]
        log_fn(f"Palace: Domain {i} ({match[1]}) -> attr {match[3]}\n")

    vol_remap = {100000 + i: attr for i, attr in domain_attr.items()}

    log_fn(f"Palace: 3D elements: {mesh.ne}\n")

    # --- Read back boundary-condition tags straight from FaceDescriptor.bcname,
    # set by .bc() before mesh generation. A face nobody tagged keeps Netgen's
    # own "default" bcname and falls back to the airbox's OuterBoundaryType.
    # A FaceDescriptor nobody tagged (.bc() never called on it) is NOT
    # necessarily a genuine exterior wall -- domin/domout (both populated
    # from Netgen's own domain-adjacency bookkeeping) distinguish the two
    # cases: domout == 0 means "nothing on the other side" (a true outer
    # boundary, correctly defaulted to the airbox's OuterBoundaryType), but
    # domout != 0 means a real kept domain sits on BOTH sides -- an internal
    # material interface (e.g. the dielectric/air boundary), which Palace
    # handles as an automatic continuity condition and must NOT receive any
    # boundary attribute at all. Verified on a real board: skipping this
    # check let three such interface face groups fall through to the PEC
    # default, silently PEC-shorting the tangential E-field across the
    # entire dielectric/air interface -- a board-wide short, not a local
    # defect, and the direct cause of a real "shorted mess" S-parameter
    # result. palace.meshing's own _assign_outer_boundary_groups excludes
    # these the same way, via getAdjacencies() bounding 2 volumes.
    default_attr = {"PEC": 2, "PMC": 3, "Absorbing": 4}[airbox.OuterBoundaryType]
    fds = mesh.FaceDescriptors()
    drop_surfnrs = {fd.surfnr for fd in fds
                    if fd.bcname == "default" and fd.domout != 0}
    surf_remap = {fd.surfnr: _attr_from_bcname(fd.bcname, default_attr)
                  for fd in fds if fd.surfnr not in drop_surfnrs}
    log_fn(f"Palace: {len(fds)} boundary face group(s); "
           f"{sum(1 for v in surf_remap.values() if v == default_attr)} default "
           f"({airbox.OuterBoundaryType}), {len(drop_surfnrs)} internal "
           "material-interface group(s) excluded, rest explicitly tagged.\n")

    # --- Second-order curving -----------------------------------------------
    mesh.SecondOrder()
    mesh.Curve(2)

    quality = _check_curved_mesh_quality(
        mesh, log_fn,
        _mesh_quality_threshold(doc),
    )

    # Wave ports' own outer boundary curve, PEC-tagged (attr 2, matching
    # palace.meshing's PortBoundaryCurves convention exactly) -- computed
    # only now, after curving, so the midside nodes needed for a proper
    # curved line element already exist.
    wave_port_attrs = {p.MeshAttribute for p in wave_ports}
    wave_port_surfnrs = {sn for sn, attr in surf_remap.items() if attr in wave_port_attrs}
    port_boundary_edges = (
        _wave_port_boundary_edges(mesh, wave_port_surfnrs)
        if wave_port_surfnrs else []
    )

    mesh_path = os.path.join(output_dir, "mesh.msh")
    mesh.Export(mesh_path, "Gmsh2 Format")
    _fix_mesh_format_version(mesh_path, log_fn)
    _rewrite_physical_tags(mesh_path, vol_remap, surf_remap, drop_surfnrs, log_fn)
    _inject_line_elements(mesh_path, port_boundary_edges, 2, log_fn,
                           "wave-port PEC boundary curve")

    return mesh_path, step_path, quality


def _mesh_quality_threshold(doc):
    from features import find_palace_mesh
    _mesh_obj = find_palace_mesh(doc)
    return getattr(_mesh_obj, "MeshQualityWarnThreshold", 0.1) if _mesh_obj else 0.1
