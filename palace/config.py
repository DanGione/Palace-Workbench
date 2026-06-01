"""Generate Palace JSON configuration from FreeCAD document objects."""
import json


# Maps FreeCAD direction enum values to Palace direction strings.
# Driven frequency properties are stored in Hz; Palace JSON expects GHz.
_DIR_MAP = {
    "X": "X", "-X": "-X",
    "Y": "Y", "-Y": "-Y",
    "Z": "Z", "-Z": "-Z",
    "+R": "+R", "-R": "-R",   # radial, for coaxial lumped ports
}


def _find_simulation(doc):
    for o in doc.Objects:
        if hasattr(o, "SimulationType") and hasattr(o, "PalaceBinary"):
            return o
    return None


def _find_airbox(doc):
    for o in doc.Objects:
        if hasattr(o, "OuterBoundaryType"):
            return o
    return None


def _find_lumped_ports(doc):
    ports = [o for o in doc.Objects
             if hasattr(o, "PortIndex") and hasattr(o, "R") and hasattr(o, "C")
             and not hasattr(o, "Rs")]   # exclude ImpedanceBoundary
    return sorted(ports, key=lambda o: o.PortIndex)


def _find_impedance_boundaries(doc):
    objs = [o for o in doc.Objects
            if hasattr(o, "Rs") and hasattr(o, "MeshAttribute")]
    return sorted(objs, key=lambda o: o.PortIndex)


def _find_wave_ports(doc):
    ports = [o for o in doc.Objects
             if hasattr(o, "PortIndex") and hasattr(o, "NumModes") and not hasattr(o, "R")]
    return sorted(ports, key=lambda o: o.PortIndex)


def _find_dielectric_groups(doc):
    return [o for o in doc.Objects
            if hasattr(o, "Permittivity") and hasattr(o, "MeshAttribute")]


def _find_conductor_groups(doc):
    return [o for o in doc.Objects
            if hasattr(o, "ConductorType") and hasattr(o, "MeshAttribute")]


def _build_boundaries(airbox, lumped_ports, wave_ports, conductor_groups,
                      impedance_boundaries=None, only_excitation=None):
    """Build the Boundaries config block.

    only_excitation: if set to a PortIndex int, only that port gets
    Excitation=True regardless of the per-port Excitation flag.  Used when
    running one Palace simulation per driven port for full S-matrix extraction.
    """
    boundaries = {}

    # Collect PEC surface attributes: outer boundary (attr 2) + PEC conductor groups.
    pec_attrs = []
    wave_port_pec_attrs = []  # Lossy conductor attrs — PEC for wave port mode only
    lossy_entries = []

    if airbox:
        # Fixed attribute scheme (must match meshing.py _assign_outer_boundary_groups):
        #   PEC outer faces → attr 2,  PMC outer faces → attr 3,  Absorbing outer → attr 4
        default = airbox.OuterBoundaryType
        pec_faces       = getattr(airbox, "PECFaces",       None) or []
        pmc_faces       = getattr(airbox, "PMCFaces",       None) or []
        absorbing_faces = getattr(airbox, "AbsorbingFaces", None) or []
        if default == "PEC"       or pec_faces:
            pec_attrs.append(2)
        if default == "PMC"       or pmc_faces:
            boundaries["PMC"]       = {"Attributes": [3]}
        if default == "Absorbing" or absorbing_faces:
            boundaries["Absorbing"] = {"Attributes": [4]}

    for cg in conductor_groups:
        if cg.ConductorType == "PEC":
            pec_attrs.append(cg.MeshAttribute)
        else:  # Lossy Conductor
            # WavePortPEC ensures Palace uses PEC at these surfaces when solving
            # the 2D wave port eigenvalue problem (correct ground plane / strip
            # mode), while Conductivity applies the surface impedance in the full
            # 3D driven simulation.  The two keys are independent in the Palace
            # JSON schema so the same attribute may appear in both.
            wave_port_pec_attrs.append(cg.MeshAttribute)
            entry = {
                "Attributes":   [cg.MeshAttribute],
                "Conductivity": cg.Conductivity,
            }
            permeability = getattr(cg, "Permeability", 1.0)
            if permeability != 1.0:
                entry["Permeability"] = permeability
            thickness = getattr(cg, "Thickness", 0.0)
            if thickness > 0.0:
                entry["Thickness"] = thickness
            lossy_entries.append(entry)

    if pec_attrs:
        boundaries["PEC"] = {"Attributes": pec_attrs}
    if wave_port_pec_attrs:
        boundaries["WavePortPEC"] = {"Attributes": wave_port_pec_attrs}
    if lossy_entries:
        boundaries["Conductivity"] = lossy_entries

    if impedance_boundaries:
        boundaries["Impedance"] = []
        for ib in impedance_boundaries:
            entry = {"Attributes": [ib.MeshAttribute]}
            if getattr(ib, "Rs", 0.0) != 0.0:
                entry["Rs"] = ib.Rs
            if getattr(ib, "Ls", 0.0) != 0.0:
                entry["Ls"] = ib.Ls
            if getattr(ib, "Cs", 0.0) != 0.0:
                entry["Cs"] = ib.Cs
            boundaries["Impedance"].append(entry)

    if lumped_ports:
        boundaries["LumpedPort"] = []
        for p in lumped_ports:
            entry = {
                "Index": p.PortIndex,
                "Attributes": [p.MeshAttribute],
                "Direction": _DIR_MAP.get(p.Direction, "X"),
                "R": p.R,
            }
            if p.L != 0.0:
                entry["L"] = p.L
            if p.C != 0.0:
                entry["C"] = p.C
            excited = p.Excitation if only_excitation is None else (p.PortIndex == only_excitation)
            if excited:
                entry["Excitation"] = True
            boundaries["LumpedPort"].append(entry)

    if wave_ports:
        boundaries["WavePort"] = []
        for p in wave_ports:
            entry = {
                "Index": p.PortIndex,
                "Attributes": [p.MeshAttribute],
                "Mode": p.NumModes,
            }
            excited = p.Excitation if only_excitation is None else (p.PortIndex == only_excitation)
            if excited:
                entry["Excitation"] = True
            boundaries["WavePort"].append(entry)

    return boundaries


def _build_solver(sim):
    solver = {"Order": sim.Order}

    if sim.SimulationType == "Driven":
        sweep_mode = getattr(sim, "DrivenSweepMode", "Linear")
        if sweep_mode == "Samples":
            import json as _json
            raw = getattr(sim, "DrivenSamplesJSON", "[]") or "[]"
            entries = _json.loads(raw)
            samples_out = []
            for e in entries:
                t = e.get("type", "linear")
                if t == "linear":
                    samples_out.append({
                        "Type":     "Linear",
                        "MinFreq":  e["min_freq_ghz"],
                        "MaxFreq":  e["max_freq_ghz"],
                        "FreqStep": e["freq_step_ghz"],
                    })
                elif t == "log":
                    samples_out.append({
                        "Type":    "Log",
                        "MinFreq": e["min_freq_ghz"],
                        "MaxFreq": e["max_freq_ghz"],
                        "NSample": int(e["num_samples"]),
                    })
                elif t == "point":
                    samples_out.append({
                        "Type": "Point",
                        "Freq": [e["freq_ghz"]],
                    })
            if not samples_out:
                raise RuntimeError("Samples sweep mode selected but no samples are defined.")
            driven_block = {"Samples": samples_out}
        else:
            # Palace driven frequencies are in GHz; properties are stored in Hz.
            driven_block = {
                "MinFreq":  sim.DrivenMinFreq  / 1e9,
                "MaxFreq":  sim.DrivenMaxFreq  / 1e9,
                "FreqStep": sim.DrivenFreqStep / 1e9,
            }

        # Adaptive sweep applies in both Linear and Samples modes.
        adaptive_tol = getattr(sim, "DrivenAdaptiveTol", 0.0)
        if adaptive_tol > 0.0:
            driven_block["AdaptiveTol"] = adaptive_tol
            driven_block["AdaptiveMaxSamples"] = getattr(sim, "DrivenAdaptiveMaxSamples", 10)
            cand = getattr(sim, "DrivenAdaptiveMaxCandidates", 0)
            if cand > 0:
                driven_block["AdaptiveMaxCandidates"] = cand
        solver["Driven"] = driven_block
    elif sim.SimulationType == "Eigenmode":
        # Palace eigenmode Target is in GHz; EigenFreqMin property is in Hz.
        solver["Eigenmode"] = {
            "N": sim.EigenNumModes,
            "Target": sim.EigenFreqMin / 1e9,
        }
    elif sim.SimulationType == "Electrostatic":
        solver["Electrostatic"] = {
            "MaxIter": sim.ElecMaxIter,
            "Tol": sim.ElecTol,
        }

    return solver


def _probe_grid_over_port_face(port_obj, m_cols=12, n_rows=8):
    """Generate m_cols×n_rows probe points covering the wave port face.

    Returns (pts, face_area_mm2) where pts is a flat list of (x,y,z) tuples
    (model units = mm, L0=0.001) in row-major order (col index varies fastest),
    and face_area_mm2 is the port face area in mm².
    Returns ([], 0.0) if PortFaces is not set or the face cannot be read.
    """
    faces = getattr(port_obj, "PortFaces", None)
    if not faces or not faces[0][0]:
        return [], 0.0
    link_obj, subs = faces[0]
    if not subs:
        return [], 0.0
    try:
        face = link_obj.Shape.getElement(subs[0])
        bb = face.BoundBox
        dx = bb.XMax - bb.XMin
        dy = bb.YMax - bb.YMin
        dz = bb.ZMax - bb.ZMin
        # Axis with smallest extent is the face normal direction.
        extents = {"X": dx, "Y": dy, "Z": dz}
        norm_ax = min(extents, key=extents.get)
        span_axes = [ax for ax in ("X", "Y", "Z") if ax != norm_ax]
        fixed_val = (getattr(bb, norm_ax + "Min") + getattr(bb, norm_ax + "Max")) / 2.0

        def _grid_vals(ax, m):
            lo = getattr(bb, ax + "Min")
            hi = getattr(bb, ax + "Max")
            return [lo + (i + 0.5) / m * (hi - lo) for i in range(m)]

        a1_vals = _grid_vals(span_axes[0], m_cols)
        a2_vals = _grid_vals(span_axes[1], n_rows)
        pts = []
        for v1 in a1_vals:
            for v2 in a2_vals:
                coord = {norm_ax: fixed_val, span_axes[0]: v1, span_axes[1]: v2}
                pts.append((coord["X"], coord["Y"], coord["Z"]))
        return pts, face.Area
    except Exception:
        return [], 0.0


def _probe_points_along_edge(port_obj, n_probes=8):
    """Sample n_probes evenly-spaced points along port_obj.IntegrationEdge.

    Returns list of (x, y, z) tuples in model units (mm with L0=0.001),
    ordered from the first vertex (inner conductor) to the last (outer conductor).
    Returns [] if IntegrationEdge is not set or the edge cannot be read.
    """
    ie = getattr(port_obj, "IntegrationEdge", None)
    if not ie or not ie[0]:
        return []
    link_obj, subs = ie
    if not subs:
        return []
    try:
        edge = link_obj.Shape.getElement(subs[0])
        pts = []
        for k in range(n_probes):
            t     = k / (n_probes - 1)
            param = edge.FirstParameter + t * (edge.LastParameter - edge.FirstParameter)
            v     = edge.valueAt(param)
            pts.append((v.x, v.y, v.z))
        return pts
    except Exception:
        return []


def generate_config(doc, only_excitation=None, output_override=None):
    """Return a Palace config dict from the FreeCAD document.

    only_excitation: PortIndex of the single port to drive (None = use each
    port's own Excitation flag).
    output_override: override sim.OutputDir with this string (used for
    per-excitation sub-directories in multi-pass runs).
    """
    sim = _find_simulation(doc)
    if not sim:
        raise RuntimeError("No Palace simulation object found in the document.")

    airbox               = _find_airbox(doc)
    lumped_ports         = _find_lumped_ports(doc)
    wave_ports           = _find_wave_ports(doc)
    diel_groups          = _find_dielectric_groups(doc)
    conductor_groups     = _find_conductor_groups(doc)
    impedance_boundaries = _find_impedance_boundaries(doc)

    output_dir = output_override if output_override is not None else sim.OutputDir

    # Build domain materials list.
    # Attribute 1 = airbox background (vacuum); additional entries for each
    # dielectric group.  LossTangent is omitted when zero.
    materials = [{"Attributes": [1], "Permittivity": 1.0, "Permeability": 1.0}]
    for dg in diel_groups:
        entry = {
            "Attributes":   [dg.MeshAttribute],
            "Permittivity": dg.Permittivity,
            "Permeability": dg.Permeability,
        }
        if dg.LossTangent != 0.0:
            entry["LossTan"] = dg.LossTangent
        materials.append(entry)

    # Build Domains.Postprocessing.Probe entries for wave ports.
    #   V-line probes: index = 1000 + port_idx*10 + k   (k=0..7 along IntegrationEdge)
    #   Power-grid probes: index = 2000 + (port_idx-1)*200 + k  (k=0..(M*N-1) over PortFace)
    # Both index ranges are reserved and must not conflict with user-defined probes.
    probes = []
    for wp in wave_ports:
        pts = _probe_points_along_edge(wp)
        for k, (x, y, z) in enumerate(pts):
            probes.append({
                "Index": 1000 + wp.PortIndex * 10 + k,
                "Center": [x, y, z],
            })
        grid_pts, _ = _probe_grid_over_port_face(wp)
        grid_base = 2000 + (wp.PortIndex - 1) * 200
        for k, (x, y, z) in enumerate(grid_pts):
            probes.append({"Index": grid_base + k, "Center": [x, y, z]})

    domains_block = {"Materials": materials}
    if probes:
        domains_block["Postprocessing"] = {"Probe": probes}

    return {
        "Problem": {
            "Type": sim.SimulationType,
            "Verbose": sim.Verbose,
            "Output": output_dir,
        },
        "Model": {
            "Mesh": sim.MeshFile,
            "L0": sim.L0,
        },
        "Domains": domains_block,
        "Boundaries": _build_boundaries(
            airbox, lumped_ports, wave_ports, conductor_groups,
            impedance_boundaries, only_excitation
        ),
        "Solver": _build_solver(sim),
    }


def write_config(doc, path):
    """Write the Palace JSON config to *path* and return the path."""
    cfg = generate_config(doc)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return path
