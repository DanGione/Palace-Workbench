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


def find_impedance_boundaries(doc):
    objs = [obj for obj in doc.Objects
            if hasattr(obj, "Rs") and hasattr(obj, "MeshAttribute")]
    return sorted(objs, key=lambda o: o.PortIndex)


def next_port_index(doc):
    all_ports = (find_lumped_ports(doc) + find_wave_ports(doc)
                 + find_impedance_boundaries(doc))
    if not all_ports:
        return 1
    return max(p.PortIndex for p in all_ports) + 1


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
