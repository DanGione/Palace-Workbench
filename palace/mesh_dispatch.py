"""Meshing backend dispatch: Gmsh vs Netgen, chosen by the active Simulation
object's MeshBackend property.

This is the ONLY thing that changes behavior based on MeshBackend --
palace/meshing.py (the battle-tested Gmsh pipeline, ~1800 lines of hard-won
invariants) and palace/netgen_meshing.py stay fully independent and
untouched by this module, exactly as they were built and validated. Every
existing call site imports generate_mesh from HERE instead of directly from
palace.meshing, so all four of them (commands/cmd_run.py's direct
synchronous call, and commands/cmd_mesh.py + both commands/cmd_sweep.py
coordinators via palace/mesh_runner.py -> palace/mesh_worker_main.py's
subprocess call) get the right backend automatically, with zero per-call-
site branching and zero changes to either backend's own code.
"""


def _mesh_backend(doc):
    from features import find_simulation
    sim = find_simulation(doc)
    return getattr(sim, "MeshBackend", "Gmsh") if sim else "Gmsh"


def generate_mesh(doc, output_dir, log_fn=None):
    """Same (mesh_path, geometry_path, quality) contract as
    palace.meshing.generate_mesh() / palace.netgen_meshing.generate_mesh_netgen()
    -- dispatches to whichever backend doc's Simulation.MeshBackend selects.
    """
    if _mesh_backend(doc) == "Netgen":
        from palace.netgen_meshing import generate_mesh_netgen
        return generate_mesh_netgen(doc, output_dir, log_fn=log_fn)
    from palace.meshing import generate_mesh as _generate_mesh_gmsh
    return _generate_mesh_gmsh(doc, output_dir, log_fn=log_fn)
