"""Utility finders shared across feature and command modules."""


def find_simulation(doc):
    for obj in doc.Objects:
        if hasattr(obj, "SimulationType") and hasattr(obj, "PalaceBinary"):
            return obj
    return None


def find_airbox(doc):
    for obj in doc.Objects:
        if hasattr(obj, "OuterBoundaryType"):
            return obj
    return None


def find_lumped_ports(doc):
    ports = [
        obj for obj in doc.Objects
        if hasattr(obj, "PortIndex") and hasattr(obj, "R") and hasattr(obj, "C")
        and not hasattr(obj, "Rs")   # exclude ImpedanceBoundary
    ]
    return sorted(ports, key=lambda o: o.PortIndex)


def find_wave_ports(doc):
    ports = [
        obj for obj in doc.Objects
        if hasattr(obj, "PortIndex") and hasattr(obj, "NumModes") and not hasattr(obj, "R")
    ]
    return sorted(ports, key=lambda o: o.PortIndex)


def find_dielectric_groups(doc):
    return [obj for obj in doc.Objects
            if hasattr(obj, "Permittivity") and hasattr(obj, "MeshAttribute")]


def find_conductor_groups(doc):
    return [obj for obj in doc.Objects
            if hasattr(obj, "ConductorType") and hasattr(obj, "MeshAttribute")]


def find_components(doc):
    return [obj for obj in doc.Objects
            if hasattr(obj, "ChildBasePlacements") and hasattr(obj, "ComponentKind")]


def find_port_group(doc):
    for obj in doc.Objects:
        if hasattr(obj, "IsPortGroup"):
            return obj
    return None


def find_impedance_boundary_group(doc):
    for obj in doc.Objects:
        if hasattr(obj, "IsImpedanceBoundaryGroup"):
            return obj
    return None


def find_component_group(doc):
    for obj in doc.Objects:
        if hasattr(obj, "IsComponentGroup"):
            return obj
    return None


def find_impedance_boundaries(doc):
    objs = [obj for obj in doc.Objects
            if hasattr(obj, "Rs") and hasattr(obj, "MeshAttribute")]
    return sorted(objs, key=lambda o: o.PortIndex)


def get_available_s_params(doc):
    """Return list of (row, col) S-param index pairs producible by the current port config.

    For each excited port j, Palace measures S_ij at all port indices i.
    Returns an empty list if no ports exist.
    """
    if doc is None:
        return []
    all_ports = find_lumped_ports(doc) + find_wave_ports(doc)
    all_idx   = sorted({p.PortIndex for p in all_ports})
    excited   = sorted({p.PortIndex for p in all_ports if getattr(p, "Excitation", True)})
    return [(i, j) for j in excited for i in all_idx]


_IMPEDANCE_INDEX_BASE = 1000


def next_port_index(doc):
    """Next PortIndex for a new excitation port (LumpedPort/WavePort).

    Impedance boundaries are deliberately excluded (see next_impedance_index)
    so stimulus ports always start at 1 and number contiguously among
    themselves, regardless of how many impedance boundaries exist or when
    they were created.
    """
    all_ports = find_lumped_ports(doc) + find_wave_ports(doc)
    if not all_ports:
        return 1
    return max(p.PortIndex for p in all_ports) + 1


def next_impedance_index(doc):
    """Next PortIndex for a new ImpedanceBoundary.

    Starts at _IMPEDANCE_INDEX_BASE, far above any realistic excitation port
    count, so impedance boundaries never shift where LumpedPort/WavePort
    numbering starts.
    """
    boundaries = find_impedance_boundaries(doc)
    if not boundaries:
        return _IMPEDANCE_INDEX_BASE
    return max(_IMPEDANCE_INDEX_BASE, max(b.PortIndex for b in boundaries) + 1)


def find_sweep(doc):
    for obj in doc.Objects:
        if hasattr(obj, "SweepParams") and hasattr(obj, "SweepMode"):
            return obj
    return None


def find_palace_mesh(doc):
    for obj in doc.Objects:
        if (hasattr(obj, "MeshFile") and
                hasattr(obj, "MeshCharacteristicLengthMax") and
                not hasattr(obj, "SimulationType")):
            return obj
    return None


def add_to_simulation(doc, obj):
    """Add obj to the PalaceSimulation group if one exists and obj is not already in it."""
    sim = find_simulation(doc)
    if sim is None or not hasattr(sim, "Group"):
        return
    if obj not in sim.Group:
        try:
            sim.addObject(obj)
        except Exception:
            pass
