import pytest

from palace import smd_geometry as smd

ALL_SIZES = sorted(smd.EIA_SIZE_TABLE)
ALL_POSITION_MODES = list(smd.POSITION_MODES)


def _all_solids(parts):
    solids = [solid for _name, _material_key, solid in parts["dielectric_layers"]]
    c = parts["contacts"]
    solids += [c["left_lower"], c["left_upper"], c["right_lower"], c["right_upper"]]
    if parts["pads"]:
        solids += list(parts["pads"])
    if parts["solder"]:
        solids += list(parts["solder"])
    return solids


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_get_size_params_known_codes(size_code):
    p = smd.get_size_params(size_code)
    assert p["body_length"] > p["contact_wrap"] * 2
    assert p["coating_thickness"] > 0
    assert p["pad_thickness"] > 0


def test_get_size_params_unknown_code_raises():
    with pytest.raises(KeyError):
        smd.get_size_params("9999")


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_build_ceramic_body_valid(size_code):
    p = smd.get_size_params(size_code)
    body = smd.build_ceramic_body(p)
    assert body.isValid()
    assert body.Volume > 0


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_build_coating_valid_and_below_body(size_code):
    p = smd.get_size_params(size_code)
    body = smd.build_ceramic_body(p)
    coating = smd.build_coating(p)
    assert coating.isValid()
    assert coating.Volume > 0
    assert coating.BoundBox.ZMax == pytest.approx(body.BoundBox.ZMin, abs=1e-9)


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_build_contact_pair_valid_and_non_overlapping(size_code):
    p = smd.get_size_params(size_code)
    c = smd.build_contact_pair(p)
    solids = [c["left_lower"], c["left_upper"], c["right_lower"], c["right_upper"]]
    for s in solids:
        assert s.isValid()
        assert s.Volume > 0
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            assert solids[i].common(solids[j]).Volume < 1e-9


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_build_pad_pair_valid(size_code):
    p = smd.get_size_params(size_code)
    left, right = smd.build_pad_pair(p)
    assert left.isValid() and left.Volume > 0
    assert right.isValid() and right.Volume > 0
    assert left.common(right).Volume < 1e-9


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_build_solder_joint_pair_valid(size_code):
    p = smd.get_size_params(size_code)
    contacts = smd.build_contact_pair(p)
    left, right = smd.build_solder_joint_pair(p, contacts)
    assert left.isValid() and left.Volume > 0
    assert right.isValid() and right.Volume > 0


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_build_solder_joint_pair_touches_pad_and_avoids_contact(size_code):
    p = smd.get_size_params(size_code)
    contacts = smd.build_contact_pair(p)
    left, right = smd.build_solder_joint_pair(p, contacts)
    pad_left, pad_right = smd.build_pad_pair(p)
    contact_left = contacts["left_lower"].fuse(contacts["left_upper"])
    contact_right = contacts["right_lower"].fuse(contacts["right_upper"])

    # Meniscus rests on the pad (touching, not overlapping) and never
    # intrudes into the contact volume it wraps around.
    assert left.common(pad_left).Volume < 1e-9
    assert left.common(contact_left).Volume < 1e-9
    assert right.common(pad_right).Volume < 1e-9
    assert right.common(contact_right).Volume < 1e-9

    # Meniscus base (at the pad's own top face) should be as wide as the pad;
    # it should feather to ~zero thickness by the top of the contact stack.
    assert left.BoundBox.ZMin == pytest.approx(p["z_offset"], abs=1e-9)
    z_top = p["z_offset"] + p["coating_thickness"] + p["body_height"]
    assert left.BoundBox.ZMax == pytest.approx(z_top, abs=1e-6)


@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_build_solder_joint_pair_base_matches_pad_footprint(size_code):
    # The meniscus's footprint at its base must match the pad's own shape
    # exactly (both X and Y extents), not just be "as wide as the pad" --
    # physically, solder wets the whole exposed pad, not a slice of it. The
    # wedge tapers monotonically from the pad's rectangle down to the
    # (smaller) contact's rectangle, so the widest point of the whole solid
    # is at the base -- its overall BoundBox X/Y extent must equal the pad's.
    p = smd.get_size_params(size_code)
    contacts = smd.build_contact_pair(p)
    left, right = smd.build_solder_joint_pair(p, contacts)
    pad_left, pad_right = smd.build_pad_pair(p)

    for meniscus, pad in ((left, pad_left), (right, pad_right)):
        m_bbox, p_bbox = meniscus.BoundBox, pad.BoundBox
        assert m_bbox.XMin == pytest.approx(p_bbox.XMin, abs=1e-6)
        assert m_bbox.XMax == pytest.approx(p_bbox.XMax, abs=1e-6)
        assert m_bbox.YMin == pytest.approx(p_bbox.YMin, abs=1e-6)
        assert m_bbox.YMax == pytest.approx(p_bbox.YMax, abs=1e-6)


def test_find_edge_by_midpoint_matches_expected_edge():
    import Part
    import FreeCAD

    box = Part.makeBox(1, 1, 1)
    # Midpoint of the bottom face's edge along X at Y=0, Z=0.
    target = FreeCAD.Vector(0.5, 0.0, 0.0)
    name, edge = smd.find_edge_by_midpoint(box, target)
    assert name.startswith("Edge")
    assert (edge.CenterOfMass - target).Length < 1e-9


def test_find_edge_by_midpoint_raises_when_not_found():
    import Part
    import FreeCAD

    box = Part.makeBox(1, 1, 1)
    with pytest.raises(RuntimeError):
        smd.find_edge_by_midpoint(box, FreeCAD.Vector(99, 99, 99))


@pytest.mark.parametrize("size_code", ALL_SIZES)
@pytest.mark.parametrize(
    "include_pads,include_solder", [(True, True), (True, False), (False, False)]
)
@pytest.mark.parametrize("position_mode", ALL_POSITION_MODES)
def test_build_smd_component_no_overlaps(size_code, include_pads, include_solder, position_mode):
    parts = smd.build_smd_component(size_code, include_pads, include_solder,
                                     position_mode=position_mode)
    solids = _all_solids(parts)
    for s in solids:
        assert s.isValid()
        assert s.Volume > 0
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            assert solids[i].common(solids[j]).Volume < 1e-9


def test_build_smd_component_pads_false_forces_no_solder():
    parts = smd.build_smd_component("0402", include_pads=False, include_solder_joint=True)
    assert parts["pads"] is None
    assert parts["solder"] is None


@pytest.mark.parametrize("include_solder", [True, False])
def test_build_smd_component_contact_position_independent_of_solder_joint(include_solder):
    # The component must sit directly on top of the pads (contact bottom ==
    # pad_thickness, i.e. the pad's own top face) whether or not a solder
    # joint is generated -- the solder joint is cosmetic, not a spacer.
    parts = smd.build_smd_component("0402", include_pads=True, include_solder_joint=include_solder)
    pad_thickness = parts["params"]["pad_thickness"]
    assert parts["contacts"]["left_lower"].BoundBox.ZMin == pytest.approx(pad_thickness, abs=1e-9)


def test_build_smd_component_contact_sits_at_z0_without_pads():
    # With no copper layer being modeled, the component sits directly on the
    # attached surface (Z=0) instead of elevated by a pad's thickness.
    parts = smd.build_smd_component("0402", include_pads=False, include_solder_joint=False)
    assert parts["contacts"]["left_lower"].BoundBox.ZMin == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("position_mode", ALL_POSITION_MODES)
def test_build_smd_component_impedance_boundary_edges_resolvable(position_mode):
    from palace.port_geometry import build_port_shape

    parts = smd.build_smd_component("0402", include_pads=True, include_solder_joint=True,
                                     position_mode=position_mode)
    c = parts["contacts"]
    _, edge_left = smd.find_edge_by_midpoint(c["left_lower"], c["gap_left"])
    _, edge_right = smd.find_edge_by_midpoint(c["right_lower"], c["gap_right"])
    shape, kind = build_port_shape(edge_left, edge_right)
    assert kind == "planar"
    assert shape.isValid()
    assert shape.Area > 0


# ---------------------------------------------------------------------------
# position_mode: get_boundary_height / build_dielectric_layers / build_contact_pair
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size_code", ALL_SIZES)
def test_get_boundary_height_per_mode(size_code):
    p = smd.get_size_params(size_code)
    assert smd.get_boundary_height(p, "Flipped") == pytest.approx(p["coating_thickness"])
    assert smd.get_boundary_height(p, "Conventional") == pytest.approx(p["body_height"])
    assert smd.get_boundary_height(p, "Middle") == pytest.approx(p["body_height"] / 2.0)


def test_get_boundary_height_unknown_mode_raises():
    p = smd.get_size_params("0402")
    with pytest.raises(ValueError):
        smd.get_boundary_height(p, "Sideways")


@pytest.mark.parametrize("size_code", ALL_SIZES)
@pytest.mark.parametrize("position_mode", ALL_POSITION_MODES)
def test_build_dielectric_layers_contiguous_and_total_height_invariant(size_code, position_mode):
    p = smd.get_size_params(size_code)
    layers = smd.build_dielectric_layers(p, position_mode)
    solids = [s for _n, _m, s in layers]

    # Non-overlapping and each individually valid.
    for s in solids:
        assert s.isValid()
        assert s.Volume > 0
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            assert solids[i].common(solids[j]).Volume < 1e-9

    # Collectively span [z_offset, z_offset + total_height] with no gaps --
    # total_height is invariant across all 3 modes.
    total_height = p["coating_thickness"] + p["body_height"]
    z_min = min(s.BoundBox.ZMin for s in solids)
    z_max = max(s.BoundBox.ZMax for s in solids)
    assert z_min == pytest.approx(p["z_offset"], abs=1e-9)
    assert z_max == pytest.approx(p["z_offset"] + total_height, abs=1e-9)
    combined_volume = sum(s.Volume for s in solids)
    fused = solids[0]
    for s in solids[1:]:
        fused = fused.fuse(s)
    assert fused.Volume == pytest.approx(combined_volume, rel=1e-6)


def test_build_dielectric_layers_middle_has_three_solids_body_split_in_half():
    p = smd.get_size_params("0402")
    layers = smd.build_dielectric_layers(p, "Middle")
    names = [n for n, _m, _s in layers]
    assert names == ["body_lower", "body_upper", "coating"]
    material_keys = {n: mk for n, mk, _s in layers}
    assert material_keys["body_lower"] == "body"
    assert material_keys["body_upper"] == "body"
    assert material_keys["coating"] == "coating"

    solids = {n: s for n, _mk, s in layers}
    half = p["body_height"] / 2.0
    assert solids["body_lower"].BoundBox.ZMax - solids["body_lower"].BoundBox.ZMin == pytest.approx(half)
    assert solids["body_upper"].BoundBox.ZMax - solids["body_upper"].BoundBox.ZMin == pytest.approx(half)
    # The boundary sits strictly between body_lower and body_upper -- NOT
    # touching the coating, which stays a separate layer stacked on top.
    assert solids["body_lower"].BoundBox.ZMax == pytest.approx(solids["body_upper"].BoundBox.ZMin)
    assert solids["body_upper"].BoundBox.ZMax == pytest.approx(solids["coating"].BoundBox.ZMin)


def test_build_dielectric_layers_unknown_mode_raises():
    p = smd.get_size_params("0402")
    with pytest.raises(ValueError):
        smd.build_dielectric_layers(p, "Sideways")


@pytest.mark.parametrize("size_code", ALL_SIZES)
@pytest.mark.parametrize("position_mode", ALL_POSITION_MODES)
def test_build_contact_pair_split_height_matches_boundary_height(size_code, position_mode):
    p = smd.get_size_params(size_code)
    boundary_height = smd.get_boundary_height(p, position_mode)
    c = smd.build_contact_pair(p, boundary_height=boundary_height)
    total_height = p["coating_thickness"] + p["body_height"]

    lower_height = c["left_lower"].BoundBox.ZMax - c["left_lower"].BoundBox.ZMin
    upper_height = c["left_upper"].BoundBox.ZMax - c["left_upper"].BoundBox.ZMin
    assert lower_height == pytest.approx(boundary_height)
    assert upper_height == pytest.approx(total_height - boundary_height)
    assert c["left_lower"].BoundBox.ZMax == pytest.approx(c["left_upper"].BoundBox.ZMin)


def test_build_contact_pair_default_boundary_height_matches_flipped_mode():
    # No boundary_height passed -- must reproduce the exact pre-feature
    # behavior (split at coating_thickness) for existing standalone callers.
    p = smd.get_size_params("0402")
    c_default = smd.build_contact_pair(p)
    c_flipped = smd.build_contact_pair(p, boundary_height=p["coating_thickness"])
    assert c_default["left_lower"].BoundBox.ZMax == pytest.approx(c_flipped["left_lower"].BoundBox.ZMax)
    assert c_default["gap_left"] == c_flipped["gap_left"]
