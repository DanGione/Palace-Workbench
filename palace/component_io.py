"""Export/import Component bundles (.palcomp) for reuse across projects.

A bundle is a zip containing:
  geometry.step  -- the component's master solids, exported via Part.export()
  manifest.json  -- per-solid role/material/placement metadata

Material properties are captured in *full* in the manifest, not just the
material's name, since the importing document can't assume a matching
material group already exists there. import_component() never silently
overwrites an existing, differently-defined material with the same name --
see detect_collisions()/MaterialCollision below, which the caller (see
commands/cmd_component_io.py) must resolve before calling import_component().

Ordering caveat: Part.export() writes one solid per input master object, in
the order given; Part.read(...).Solids recovers them assuming STEP preserves
that sequential order. This holds for every case exercised so far, but is a
property of the STEP interchange format, not something this module can
independently guarantee.
"""

import json
import math
import os
import shutil
import zipfile

import FreeCAD

from features import find_dielectric_groups, find_conductor_groups
from features.component import create_component_container, add_solid_pair
from features.material_group import (
    get_or_create_dielectric_group, get_or_create_conductor_group,
    find_dielectric_group_by_material, find_conductor_group_by_material,
)

_FORMAT_VERSION = 1


def _placement_to_dict(pl):
    return {"position": [pl.Base.x, pl.Base.y, pl.Base.z], "quaternion": list(pl.Rotation.Q)}


def _placement_from_dict(d):
    pos = FreeCAD.Vector(*d["position"])
    rot = FreeCAD.Rotation(*d["quaternion"])
    return FreeCAD.Placement(pos, rot)


def _dielectric_properties(grp):
    return {
        "permittivity": grp.Permittivity,
        "permeability": grp.Permeability,
        "loss_tangent": grp.LossTangent,
    }


def _conductor_properties(grp):
    return {
        "conductor_type": grp.ConductorType,
        "conductivity": grp.Conductivity,
        "permeability": grp.Permeability,
        "thickness": grp.Thickness,
    }


def _role_material_and_properties(doc, shadow):
    if shadow is None:
        return None, None, {}
    for g in find_dielectric_groups(doc):
        if shadow in g.Group:
            return "Dielectric", g.MaterialName, _dielectric_properties(g)
    for g in find_conductor_groups(doc):
        if shadow in g.Group:
            return "Conductor", g.MaterialName, _conductor_properties(g)
    return None, None, {}


def export_component(container, dest_path):
    """Write *container* (a Component -- see features/component.py) to *dest_path*."""
    doc = container.Document
    masters = list(container.Group)
    shadows = list(getattr(container, "ShadowSolids", []))
    bases = list(getattr(container, "ChildBasePlacements", []))

    solids_meta = []
    for i, master in enumerate(masters):
        shadow = shadows[i] if i < len(shadows) else None
        role, material_name, properties = _role_material_and_properties(doc, shadow)
        base = bases[i] if i < len(bases) else FreeCAD.Placement()
        solids_meta.append({
            "label": master.Label,
            "role": role,
            "material_name": material_name,
            "material_properties": properties,
            "base_placement": _placement_to_dict(base),
        })

    scratch_dir = doc.getTempFileName("component_export")
    os.makedirs(scratch_dir, exist_ok=True)
    try:
        import Part
        step_path = os.path.join(scratch_dir, "geometry.step")
        Part.export(masters, step_path)

        manifest = {
            "format_version": _FORMAT_VERSION,
            "component_kind": getattr(container, "ComponentKind", ""),
            "component_name": container.Label,
            "solids": solids_meta,
        }
        manifest_path = os.path.join(scratch_dir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(step_path, "geometry.step")
            zf.write(manifest_path, "manifest.json")
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


class MaterialCollision:
    """A manifest material name that already exists in the target document
    with different properties -- the caller must decide, per collision,
    whether to reuse the existing definition or import under a new name."""

    def __init__(self, role, material_name, imported_properties):
        self.role = role
        self.material_name = material_name
        self.imported_properties = imported_properties


def _properties_match(existing_group, role, imported_properties, rel_tol=1e-6, abs_tol=1e-12):
    current = _dielectric_properties(existing_group) if role == "Dielectric" \
        else _conductor_properties(existing_group)
    for key, imported_val in imported_properties.items():
        current_val = current.get(key)
        if isinstance(imported_val, str) or isinstance(current_val, str):
            if imported_val != current_val:
                return False
        elif current_val is None or not math.isclose(current_val, imported_val,
                                                       rel_tol=rel_tol, abs_tol=abs_tol):
            return False
    return True


def read_manifest(bundle_path, scratch_dir):
    """Extract *bundle_path* into *scratch_dir* and return its manifest dict."""
    with zipfile.ZipFile(bundle_path, "r") as zf:
        zf.extractall(scratch_dir)
    with open(os.path.join(scratch_dir, "manifest.json")) as f:
        return json.load(f)


def detect_collisions(doc, manifest):
    """Return a MaterialCollision for every (role, material_name) in
    *manifest* that already exists in *doc* with *different* properties.

    A name that doesn't exist yet, or exists with matching properties, is
    not a collision -- it will be silently created/reused during import.
    """
    collisions = []
    seen = set()
    for entry in manifest["solids"]:
        role, name = entry["role"], entry["material_name"]
        if role is None or name is None or (role, name) in seen:
            continue
        seen.add((role, name))
        finder = find_dielectric_group_by_material if role == "Dielectric" else find_conductor_group_by_material
        existing = finder(doc, name)
        if existing is not None and not _properties_match(existing, role, entry["material_properties"]):
            collisions.append(MaterialCollision(role, name, entry["material_properties"]))
    return collisions


def unique_material_name(doc, role, base_name):
    """A name not currently used by any *role* material group in *doc*."""
    finder = find_dielectric_group_by_material if role == "Dielectric" else find_conductor_group_by_material
    candidate = f"{base_name} (imported)"
    n = 2
    while finder(doc, candidate) is not None:
        candidate = f"{base_name} (imported {n})"
        n += 1
    return candidate


def _resolve_material_group(doc, role, resolved_name, props, shadow, keep_existing):
    """Add *shadow* to the (role, resolved_name) material group, creating it
    from *props* if it doesn't exist yet. If *keep_existing* is True, an
    already-existing group's properties are left untouched -- only *shadow*
    is added -- since the caller has already decided (via the collision
    dialog) to keep the document's current definition rather than the
    manifest's.
    """
    if keep_existing:
        finder = find_dielectric_group_by_material if role == "Dielectric" else find_conductor_group_by_material
        grp = finder(doc, resolved_name)
        grp.addObject(shadow)
        doc.recompute()
        return grp

    if role == "Dielectric":
        return get_or_create_dielectric_group(
            doc, resolved_name,
            permittivity=props.get("permittivity", 1.0),
            permeability=props.get("permeability", 1.0),
            loss_tangent=props.get("loss_tangent", 0.0),
            solids=[shadow],
        )
    grp = get_or_create_conductor_group(
        doc, resolved_name, conductor_type=props.get("conductor_type", "PEC"), solids=[shadow],
    )
    # get_or_create_conductor_group has no conductivity/permeability/thickness
    # kwargs of its own (rarely customized at creation time elsewhere) -- only
    # safe to set them here because keep_existing is False: either this call
    # just created the group fresh (no prior values to clobber), or it found
    # one whose properties detect_collisions() already confirmed match the
    # manifest (so re-setting them is a no-op).
    grp.Conductivity = props.get("conductivity", grp.Conductivity)
    grp.Permeability = props.get("permeability", grp.Permeability)
    grp.Thickness = props.get("thickness", grp.Thickness)
    return grp


def import_component(doc, bundle_path, keep_existing_for=None, rename_to=None, base_name="Component"):
    """Import a .palcomp bundle into *doc*. Returns the created Component.

    keep_existing_for : set of (role, material_name) pairs where the caller
        has decided to reuse the target document's current material
        definition verbatim (see detect_collisions()) -- properties are not
        overwritten for these, only the shadow solid is added to the group.
    rename_to : dict mapping a colliding (role, material_name) pair to a
        disambiguated new name (see unique_material_name()) to import it
        under instead -- a fresh group is created using the manifest's
        properties in full.
    base_name : fallback container name when the bundle predates
        component_name being recorded in the manifest (see export_component).
    """
    keep_existing_for = keep_existing_for or set()
    rename_to = rename_to or {}

    scratch_dir = doc.getTempFileName("component_import")
    os.makedirs(scratch_dir, exist_ok=True)
    try:
        manifest = read_manifest(bundle_path, scratch_dir)

        import Part
        shape = Part.read(os.path.join(scratch_dir, "geometry.step"))
        solids = shape.Solids

        component_name = manifest.get("component_name") or base_name
        container = create_component_container(
            doc, kind=manifest.get("component_kind", "Selection"), base_name=component_name,
        )
        # create_component_container() doesn't set Label itself (its Name
        # argument is only a hint -- FreeCAD may disambiguate it silently on
        # a Name collision and leave Label defaulted to that disambiguated
        # Name instead of the requested text). Set it explicitly so the
        # exported component's name survives the round trip, the same
        # reasoning as features/component.py's _add_solid fix for the
        # master/shadow solids themselves.
        container.Label = component_name

        for i, entry in enumerate(manifest["solids"]):
            if i >= len(solids):
                break
            base_placement = _placement_from_dict(entry["base_placement"])
            _master, shadow = add_solid_pair(doc, container, entry["label"], solids[i], base_placement)

            role, material_name = entry["role"], entry["material_name"]
            if role is None or material_name is None:
                continue
            key = (role, material_name)
            resolved_name = rename_to.get(key, material_name)
            _resolve_material_group(
                doc, role, resolved_name, entry["material_properties"], shadow,
                keep_existing=key in keep_existing_for,
            )

        doc.recompute()
        return container
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
