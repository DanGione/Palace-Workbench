"""Standalone geometry review script for the SMD component prototype (Phase 1).

Builds a few example SMD chip arrangements (resistor/capacitor/inductor,
various sizes, with/without pads and solder joints) using
``palace/smd_geometry.py``, wires them into DielectricGroup/ConductorGroup/
ImpedanceBoundary features via the existing factory functions, and saves the
result to ``smd_prototype.FCStd`` at the repo root for visual review in the
FreeCAD GUI.

This is NOT the final GUI feature -- there is no command/task panel/live
preview here, just a script to validate the geometric structure before that
work is planned. Run with::

    freecadcmd scripts/smd_prototype.py [output_path.FCStd]
"""

import os
import sys

import FreeCAD

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from palace.smd_geometry import build_smd_component, find_edge_by_midpoint  # noqa: E402
from features.material_group import (  # noqa: E402
    get_or_create_dielectric_group,
    get_or_create_conductor_group,
)
from features.impedance_boundary import create_impedance_boundary  # noqa: E402

# Shared material names -- every SMD component adds into these same groups
# rather than creating a group pair per component, per the confirmed design.
CERAMIC_MATERIAL = "SMD Ceramic Body"
COATING_MATERIAL = "SMD Insulating Coating"
CONDUCTOR_MATERIAL = "SMD Conductors"

CERAMIC_PERMITTIVITY = 9.0   # generic alumina-class default; refine per-kind later
COATING_PERMITTIVITY = 4.0   # generic epoxy default

CERAMIC_COLOR = (0.82, 0.70, 0.55)
COATING_COLOR = (0.12, 0.12, 0.12)
CONDUCTOR_COLOR = (0.80, 0.80, 0.82)

LAYOUT_PITCH = 6.0  # mm; safe for footprints up to ~2mm (largest EIA size here)


def add_solid(doc, name, solid, offset):
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = solid.translated(offset)
    return obj


def place_example(doc, label, offset, size_code, include_pads, include_solder_joint, port_index,
                  position_mode="Flipped"):
    parts = build_smd_component(size_code, include_pads, include_solder_joint,
                                 position_mode=position_mode)

    dielectric_objs = {}
    for name, material_key, solid in parts["dielectric_layers"]:
        obj = add_solid(doc, f"{label}_{name.title().replace('_', '')}", solid, offset)
        dielectric_objs.setdefault(material_key, []).append(obj)

    c = parts["contacts"]
    contact_objs = [
        add_solid(doc, f"{label}_ContactL_Lower", c["left_lower"], offset),
        add_solid(doc, f"{label}_ContactL_Upper", c["left_upper"], offset),
        add_solid(doc, f"{label}_ContactR_Lower", c["right_lower"], offset),
        add_solid(doc, f"{label}_ContactR_Upper", c["right_upper"], offset),
    ]
    contact_left_lower_obj = contact_objs[0]
    contact_right_lower_obj = contact_objs[2]

    conductor_objs = list(contact_objs)
    if parts["pads"]:
        p1, p2 = parts["pads"]
        conductor_objs.append(add_solid(doc, f"{label}_PadL", p1, offset))
        conductor_objs.append(add_solid(doc, f"{label}_PadR", p2, offset))
    if parts["solder"]:
        s1, s2 = parts["solder"]
        conductor_objs.append(add_solid(doc, f"{label}_SolderL", s1, offset))
        conductor_objs.append(add_solid(doc, f"{label}_SolderR", s2, offset))

    get_or_create_dielectric_group(
        doc, CERAMIC_MATERIAL, CERAMIC_PERMITTIVITY, CERAMIC_COLOR, solids=dielectric_objs.get("body", [])
    )
    get_or_create_dielectric_group(
        doc, COATING_MATERIAL, COATING_PERMITTIVITY, COATING_COLOR, solids=dielectric_objs.get("coating", [])
    )
    get_or_create_conductor_group(
        doc, CONDUCTOR_MATERIAL, CONDUCTOR_COLOR, solids=conductor_objs
    )

    gap_left = parts["contacts"]["gap_left"] + offset
    gap_right = parts["contacts"]["gap_right"] + offset
    name_left, _ = find_edge_by_midpoint(contact_left_lower_obj.Shape, gap_left)
    name_right, _ = find_edge_by_midpoint(contact_right_lower_obj.Shape, gap_right)
    create_impedance_boundary(
        doc, index=port_index,
        edge_refs=[(contact_left_lower_obj, name_left), (contact_right_lower_obj, name_right)],
    )

    FreeCAD.Console.PrintMessage(
        f"{label}: size={size_code} pads={include_pads} solder={include_solder_joint}\n"
    )


def main():
    doc = FreeCAD.newDocument("SMD_Prototype")

    place_example(doc, "R0402", FreeCAD.Vector(0, 0, 0), "0402",
                  include_pads=True, include_solder_joint=True, port_index=1)
    place_example(doc, "C0603", FreeCAD.Vector(LAYOUT_PITCH, 0, 0), "0603",
                  include_pads=True, include_solder_joint=False, port_index=2)
    place_example(doc, "L0805", FreeCAD.Vector(2 * LAYOUT_PITCH, 0, 0), "0805",
                  include_pads=False, include_solder_joint=False, port_index=3)

    # Second row: same 0603 size, one per impedance-boundary position_mode,
    # for visual comparison of the three dielectric-layer arrangements.
    place_example(doc, "R0603_Conventional", FreeCAD.Vector(0, LAYOUT_PITCH, 0), "0603",
                  include_pads=True, include_solder_joint=True, port_index=4,
                  position_mode="Conventional")
    place_example(doc, "C0603_Middle", FreeCAD.Vector(LAYOUT_PITCH, LAYOUT_PITCH, 0), "0603",
                  include_pads=True, include_solder_joint=True, port_index=5,
                  position_mode="Middle")

    doc.recompute()

    # Note: under freecadcmd, sys.argv is ["freecadcmd", "<this script>", ...],
    # so an optional output-path override is argv[2], not argv[1].
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(__file__), "..", "smd_prototype.FCStd"
    )
    out_path = os.path.abspath(out_path)
    doc.saveAs(out_path)
    FreeCAD.Console.PrintMessage(f"Saved {out_path}\n")


# freecadcmd executes scripts with __name__ set to the script's basename, not
# "__main__", so call main() unconditionally rather than guarding on that.
main()
