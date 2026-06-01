"""
Headless end-to-end test for the Palace FreeCAD workbench.

Run with:
    freecadcmd /workspace/test_headless.py

Exercises: meshing.py → config.py → Palace binary.
Pass criterion: S[2][1] < 0.05 dB and S[1][1] < -35 dB across 1-5 GHz.
"""

import sys
import os
import json
import math
import tempfile
import subprocess

# Make the workbench importable
sys.path.insert(0, "/workspace")

import FreeCAD
import Part

PALACE_BIN = (
    "/opt/spack/opt/spack/linux-ubuntu22.04-icelake/gcc-11.4.0/"
    "palace-0.12.0-eqguemtopoeez24knhosweng35twldaa/bin/palace-x86_64.bin"
)

# Coaxial geometry: Z0 = 60*ln(3.5/1.52) ≈ 50.0 Ω
R_INNER = 1.52
R_OUTER = 3.50
LENGTH  = 10.0

def log(msg):
    FreeCAD.Console.PrintMessage(f"[test] {msg}\n")

# ── Document ──────────────────────────────────────────────────────────────────
doc = FreeCAD.newDocument("HeadlessTest")

outer_cyl  = Part.makeCylinder(R_OUTER, LENGTH)
inner_cyl  = Part.makeCylinder(R_INNER, LENGTH)
coax_shape = outer_cyl.cut(inner_cyl)

domain_obj       = doc.addObject("Part::Feature", "CoaxDomain")
domain_obj.Shape = coax_shape
doc.recompute()
log(f"Geometry created: {len(coax_shape.Faces)} faces")

# ── Find port faces ────────────────────────────────────────────────────────────
def flat_face_at_z(shape, z_target, tol=0.5):
    for idx, face in enumerate(shape.Faces, start=1):
        if "Plane" in type(face.Surface).__name__:
            if abs(face.CenterOfMass.z - z_target) < tol:
                return f"Face{idx}"
    raise RuntimeError(f"No flat face found at z={z_target}")

face_p1 = flat_face_at_z(coax_shape, 0.0)
face_p2 = flat_face_at_z(coax_shape, LENGTH)
log(f"Port faces: {face_p1} (z=0)  {face_p2} (z={LENGTH})")

# ── Palace workbench objects ───────────────────────────────────────────────────
from features.simulation  import create_simulation
from features.airbox      import create_airbox
from features.lumped_port import create_lumped_port

sim = create_simulation(doc)
sim.SimulationType = "Driven"
sim.DrivenMinFreq  = 1e9
sim.DrivenMaxFreq  = 5e9
sim.DrivenFreqStep = 1e9
sim.L0             = 1e-3
sim.Order          = 2
sim.Verbose        = 1
sim.OutputDir      = "output/"

airbox = create_airbox(doc, shape_obj=domain_obj)
airbox.OuterBoundaryType = "PEC"

port1 = create_lumped_port(doc, index=1, faces=[(domain_obj, [face_p1])])
port1.Direction  = "+R"
port1.R          = 50.0
port1.Excitation = True

port2 = create_lumped_port(doc, index=2, faces=[(domain_obj, [face_p2])])
port2.Direction  = "+R"
port2.R          = 50.0
port2.Excitation = False

doc.recompute()
log("Palace objects created")

# ── Mesh ──────────────────────────────────────────────────────────────────────
from palace.meshing import generate_mesh
from palace.config  import write_config

output_dir = tempfile.mkdtemp(prefix="palace_test_")
log(f"Output dir: {output_dir}")

mesh_path = generate_mesh(doc, output_dir)
log(f"Mesh written: {mesh_path}")

# ── Config ────────────────────────────────────────────────────────────────────
# Override mesh path and output dir to absolute paths for the headless run
from palace.config import generate_config
cfg = generate_config(doc)
cfg["Model"]["Mesh"] = mesh_path
cfg["Problem"]["Output"] = os.path.join(output_dir, "output") + "/"

config_path = os.path.join(output_dir, "palace_config.json")
with open(config_path, "w") as fh:
    json.dump(cfg, fh, indent=2)
log(f"Config written: {config_path}")

with open(config_path) as fh:
    cfg_check = json.load(fh)
log(f"Freq range: {cfg_check['Solver']['Driven']['MinFreq']}–"
    f"{cfg_check['Solver']['Driven']['MaxFreq']} GHz")

ports = cfg_check["Boundaries"]["LumpedPort"]
for p in ports:
    log(f"  Port {p['Index']}: dir={p['Direction']} R={p['R']} "
        f"excitation={p.get('Excitation', False)}")

# ── Run Palace ────────────────────────────────────────────────────────────────
if not os.path.isfile(PALACE_BIN):
    log(f"Palace binary not found at {PALACE_BIN} — skipping solve")
    sys.exit(0)

log("Running Palace…")
os.makedirs(os.path.join(output_dir, "output"), exist_ok=True)
result = subprocess.run(
    ["/usr/bin/mpirun", "-n", "1", PALACE_BIN, config_path],
    capture_output=True, text=True, cwd=output_dir
)
if result.returncode != 0:
    log("Palace FAILED:")
    log(result.stderr[-3000:])
    sys.exit(1)

# ── Check results ─────────────────────────────────────────────────────────────
s_csv = os.path.join(output_dir, "output", "port-S.csv")
if not os.path.isfile(s_csv):
    log(f"Result file not found: {s_csv}")
    sys.exit(1)

log("\nS-parameter results:")
s11_max = -999.0
s21_max = -999.0
with open(s_csv) as fh:
    header = fh.readline()
    for line in fh:
        parts = [float(x) for x in line.split(",")]
        f, s11_db, _, s21_db, _ = parts
        log(f"  f={f:.1f} GHz  S11={s11_db:+.2f} dB  S21={s21_db:+.3f} dB")
        s11_max = max(s11_max, s11_db)
        s21_max = max(s21_max, s21_db)

log("")
PASS = True
if s21_max > 0.05:
    log(f"FAIL: S21 max = {s21_max:.3f} dB  (limit: +0.05 dB)")
    PASS = False
else:
    log(f"PASS: S21 max = {s21_max:.3f} dB  ✓")

if s11_max > -35.0:
    log(f"FAIL: S11 max = {s11_max:.1f} dB  (limit: -35 dB)")
    PASS = False
else:
    log(f"PASS: S11 max = {s11_max:.1f} dB  ✓")

sys.exit(0 if PASS else 1)
