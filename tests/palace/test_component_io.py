"""Coverage for palace/component_io.py: Component bundle export/import and
the material-name collision detection that guards against silently
overwriting a same-named-but-different material in the target document.
"""

import FreeCAD
import Part
import pytest

from features import find_dielectric_groups, find_conductor_groups
from features.component import create_component_from_solids
from features.material_group import get_or_create_conductor_group
from palace.component_io import (
    export_component, import_component, detect_collisions, unique_material_name,
)


@pytest.fixture
def doc():
    d = FreeCAD.newDocument("TestComponentIO")
    yield d
    FreeCAD.closeDocument(d.Name)


def _make_solid(doc, name, position=(0, 0, 0)):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = Part.makeBox(1, 1, 1)
    obj.Placement = FreeCAD.Placement(FreeCAD.Vector(*position), FreeCAD.Rotation())
    return obj


def _sample_manifest():
    return {
        "format_version": 1,
        "component_kind": "Selection",
        "solids": [{
            "label": "Pad",
            "role": "Conductor",
            "material_name": "Copper",
            "material_properties": {
                "conductor_type": "PEC", "conductivity": 5.96e7,
                "permeability": 1.0, "thickness": 0.0,
            },
            "base_placement": {"position": [0, 0, 0], "quaternion": [0, 0, 0, 1]},
        }],
    }


def test_detect_collisions_no_conflict_when_material_absent(doc):
    assert detect_collisions(doc, _sample_manifest()) == []


def test_detect_collisions_no_conflict_when_properties_match(doc):
    get_or_create_conductor_group(doc, "Copper", conductor_type="PEC")
    grp = [g for g in find_conductor_groups(doc) if g.MaterialName == "Copper"][0]
    grp.Conductivity = 5.96e7
    grp.Permeability = 1.0
    grp.Thickness = 0.0
    assert detect_collisions(doc, _sample_manifest()) == []


def test_detect_collisions_flags_differing_properties(doc):
    get_or_create_conductor_group(doc, "Copper", conductor_type="PEC")
    grp = [g for g in find_conductor_groups(doc) if g.MaterialName == "Copper"][0]
    grp.Conductivity = 1.0e6   # deliberately different from the manifest's 5.96e7

    collisions = detect_collisions(doc, _sample_manifest())
    assert len(collisions) == 1
    assert collisions[0].material_name == "Copper"
    assert collisions[0].role == "Conductor"


def test_unique_material_name_avoids_existing_names(doc):
    get_or_create_conductor_group(doc, "Copper (imported)")
    name = unique_material_name(doc, "Conductor", "Copper")
    assert name == "Copper (imported 2)"


def test_export_import_round_trip_preserves_geometry_and_materials(doc, tmp_path):
    src_a = _make_solid(doc, "SrcA", position=(0, 0, 0))
    src_b = _make_solid(doc, "SrcB", position=(10, 0, 0))
    doc.recompute()

    container = create_component_from_solids(doc, [
        (src_a, "Conductor", "RoundTripCopper"),
        (src_b, "Dielectric", "RoundTripFR4"),
    ])
    container.Label = "MyConnector"

    bundle_path = str(tmp_path / "test.palcomp")
    export_component(container, bundle_path)

    doc2 = FreeCAD.newDocument("TestComponentIOImport")
    try:
        imported = import_component(doc2, bundle_path)
        assert len(imported.ShadowSolids) == 2
        assert imported.Label == "MyConnector"

        cond = [g for g in find_conductor_groups(doc2) if g.MaterialName == "RoundTripCopper"]
        diel = [g for g in find_dielectric_groups(doc2) if g.MaterialName == "RoundTripFR4"]
        assert len(cond) == 1 and len(cond[0].Group) == 1
        assert len(diel) == 1 and len(diel[0].Group) == 1

        offset = (imported.ShadowSolids[1].Placement.Base - imported.ShadowSolids[0].Placement.Base)
        assert abs(offset.Length - 10.0) < 1e-3
    finally:
        FreeCAD.closeDocument(doc2.Name)


def test_import_falls_back_to_base_name_when_manifest_lacks_component_name(doc, tmp_path):
    """Bundles exported before component_name was recorded in the manifest
    must still import cleanly, using the base_name fallback."""
    import json
    import zipfile

    src = _make_solid(doc, "Src")
    doc.recompute()
    container = create_component_from_solids(doc, [(src, "Conductor", "LegacyCopper")])

    bundle_path = str(tmp_path / "legacy.palcomp")
    export_component(container, bundle_path)

    # Simulate a bundle exported before component_name existed in the manifest.
    with zipfile.ZipFile(bundle_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        geometry = zf.read("geometry.step")
    del manifest["component_name"]
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("geometry.step", geometry)
        zf.writestr("manifest.json", json.dumps(manifest))

    imported = import_component(doc, bundle_path, base_name="LegacyImport")
    assert imported.Label == "LegacyImport"
