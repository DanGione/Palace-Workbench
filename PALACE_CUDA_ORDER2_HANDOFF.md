# Handoff: Palace CUDA build crashes at FE order ≥ 2 on large models

**Audience:** an agent/developer working on the host, outside the devcontainer,
driving a Docker rebuild of the `:cuda` image.

**Goal:** recover FE order 2 on GPU for large models. Today it dies with SIGFPE
during preconditioner setup.

**Start at §4b — upgrade to Palace 0.18.1.** Upstream fixed the exact code path
that faults here, so the version bump may resolve this on its own. Only if that
fails, fall back to §5 (compile PETSc + SLEPc **without** CUDA while MFEM /
hypre / libCEED keep it) and then §6 (patch SLEPc).

All findings below were measured inside the devcontainer on real hardware; none
are inferred from documentation. Where something is unverified it says so.

## 0. Status update (2026-09-23, host session, both Dockerfiles)

**§4b is now disproven, not just unconfirmed.** Diffed PR #837 against
`palace/linalg/chebyshev.cpp` at v0.17.0 vs v0.18.1 before touching the build:
the PR only fixes a correctness bug on the branch already using the safe HEP
path (real operators); the complex-operator branch still hardcodes
`SpectralNorm(..., herm=false)` — the exact SVD/CUDA path that crashes —
unchanged across both versions. Bumped both Dockerfiles to v0.18.1 anyway (sed
patch target confirmed byte-identical at the new tag) and re-ran the §7 repro
against the real board on real hardware: **byte-identical SIGFPE**, same
`Level 1 (p = 2): 1,278,924 unknowns`, same signal 8, immediately after
"Assembling multigrid hierarchy". Not a guess.

**§5 hits a new blocker §5 didn't anticipate.** Wrapped
`extern/CMakeLists.txt`'s `include(ExternalSLEPc)` to save/force-OFF/restore
`PALACE_WITH_CUDA` around just that include (confirmed necessary: that file
`include()`s every dependency unscoped, and SLEPc's include happens *before*
hypre/libCEED/MFEM in file order, so an unrestored override would have
silently stripped CUDA from those too), and patched
`palace/linalg/slepc.cpp`'s `PetscVecType()` to never select `VECCUDA` (§5's
own "predicted failure mode 1", confirmed real by reading the source: it
branches on MFEM's runtime device state, not PETSc's own compile-time CUDA
support). PETSc and SLEPc both built successfully without CUDA. **But Palace's
own `palace/linalg/petsc.hpp:23-32` has an explicit, deliberate compile-time
guard**:
```c
#if (defined(PETSC_HAVE_CUDA) && !defined(MFEM_USE_CUDA)) || \
    (!defined(PETSC_HAVE_CUDA) && defined(MFEM_USE_CUDA))
#error "Mismatch between MFEM and PETSc CUDA support!"
#endif
```
(symmetric guard for HIP right below it) — this is the *only* CUDA-related
content in that whole header, no other runtime logic in it depends on the
coupling. Palace's maintainers deliberately forbid exactly the split
configuration §5 requires. Stopped here rather than patching a third thing
around this — a deliberate upstream guard warrants a decision, not another
unilateral sed. The evidence so far (this guard's isolation, plus the earlier
`MatShellSetVecType`/memory-type-aware-API evidence in §5 below) suggests
patching it out is *probably* safe, but that's not proven the way the other
two patches were before landing them.

**Net effect after all three confirmed problems**: order ≥ 2 GPU on large
models is still broken on both v0.17.0 and v0.18.1. §5 (CPU-only PETSc/SLEPc)
remains the most promising path but needs one more decision before the next
build attempt: patch out the `petsc.hpp` guard too, or take a different
route entirely (§6, or accept order 1 / CPU as the standing workaround for
large models). Both Dockerfiles currently have all patches applied *up to and
including* the `PetscVecType()` fix, but NOT the `petsc.hpp` guard removal —
the last build attempt failed at compile time on that guard, nothing has
been rebuilt successfully with it removed.

## 1. Final decision (2026-09-23, same session): §6 confirmed, not applied — pivoted to cuDSS

**Found the exact SLEPc source line, closing out the "which zero divisor"
question §6 left open.** Cloned the real v3.24.1 tag directly
(`git clone --branch v3.24.1 https://gitlab.com/slepc/slepc.git`) and read
`src/sys/classes/bv/impls/cuda/bvcuda.cu`. `BVMultInPlace_BLAS_CUDA`
computes its memory-limited fallback batch size as
`freemem/(m*sizeof(PetscScalar))` — dividing by `m` (the huge operator
size) rather than `n` (the small vector count; `d_work` is sized
`bs*n*sizeof(PetscScalar)`, so `n` is the memory-correct divisor) — which
collapses to 0 for large `m`, then faults two lines later on an unguarded
`m % bs`. Exactly matches the gdb registers from §3
(`rax=1278924`=`m`, `rbx=0`=`bs`). This is worth reporting upstream on its
own merits regardless of what this project does about it.

**Decided not to apply any fix (§5's `petsc.hpp` guard removal, or §6's
`bs` clamp/formula fix) after weighing the cost of carrying a source patch
against pivoting to a different, equally real win: NVIDIA cuDSS.**
Reasoning:
- A defensive `bs≥1` clamp was considered and rejected — with `m≈1.28M`,
  `bs=1` would mean **1.28 million** individual tiny `cublasXgemm`/
  `cudaMemcpy2D` calls in the surrounding loop, the same
  "survives-but-unusable" failure mode already documented for
  `MGMaxLevels=1`.
- The more-correct fix (divide by `n` instead of `m`) is a real, well-
  understood SLEPc source patch, but still a Palace/PETSc/SLEPc patch we'd
  carry indefinitely across every future version bump.
- §5's own blocker (`petsc.hpp`'s deliberate `#error` guard) would still
  need patching too if going that route, compounding the maintenance
  surface further.
- Tested directly whether a *different* `Solver.Linear.Type` sidesteps the
  bug entirely, since direct solvers don't need Chebyshev smoothing:
  `Solver.Linear.Type = "SuperLU"` (already built into the image) against
  the same order-2 crashing config still crashed identically. Palace's
  `Linear.Type` only chooses the multigrid hierarchy's *coarse-level*
  solver — the intermediate-level Chebyshev smoother (where the bug lives)
  runs regardless. This ruled out "just pick a different solver" as a
  workaround for the crash specifically, but confirmed cuDSS wouldn't have
  fixed it either, before spending a build cycle finding that out the hard
  way.

**What actually shipped:** both Dockerfiles reverted to the clean,
proven-working v0.18.1 state (removed the §5 `extern/CMakeLists.txt` and
`slepc.cpp` patches entirely — neither is present anymore), with NVIDIA
cuDSS added as a genuinely separate, independent feature
(`PALACE_WITH_CUDSS`, downloaded from NVIDIA's own redistributable
manifest, `CUDSS_DIR=/opt/cudss`). cuDSS targets the workload that actually
dominates a real sweep regardless of FE order — wave-port boundary modes,
recomputed once per output frequency, ~89% of wall clock per §8 below — not
the order-2 SIGFPE, which remains open. Order 1 GPU (unaffected by any of
this) and Device=CPU at order 2+ remain the standing guidance for large
models. Full writeup, including the licensing caveat this raised, is in
`CLAUDE.md`'s "GPU runs SIGFPE at FE order ≥ 2" and "NVIDIA cuDSS sparse
direct solver" entries — this file is the narrative history, that's the
maintained reference.

**Final result, same session: cuDSS built and verified working.** First
build attempt (cuDSS 0.8.0.10, the newest release) failed to compile — 15
errors in `extern/mfem/linalg/cudss.cpp`, root-caused to a real API
breaking change in cuDSS 0.8.0 that Palace's own vendored MFEM patch
(`mfem_pr5124_cudss.diff`, backporting MFEM's still-unmerged upstream PR
#5124) predates. Pinned to cuDSS 0.7.1.4 instead (the newest release before
that break) and rebuilt clean. Then verified at runtime, not just build
time: a real, full 400/400-point order-1 GPU sweep against
`Coax_Directional_Bridge.FCStd` with `Solver.Linear.Type: "cuDSS"`
completed with exit 0 and physically sane S-parameters throughout. That run
also reconfirmed the wave-port bottleneck at full scale (not just the
original 3-point sample): the Wave Ports timer read 4182.6s of 4960.3s
total (~84%). Full detail in `CLAUDE.md`'s "NVIDIA cuDSS sparse direct
solver" entry. Not yet done: a direct A/B timing comparison against the
default solver (this run confirms correctness, not speedup), and the
`.devcontainer/Dockerfile` has the identical fix applied but has not
itself been rebuilt/tested (that's a VS Code-triggered rebuild, the user's
call, same as always in this repo).

---

## 1. Environment

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 3060 Ti, 8192 MiB |
| Platform | WSL2 (`6.18.33.2-microsoft-standard-WSL2`), driver 616.92, CUDA runtime 12.8 |
| Host RAM | 23 GiB total |
| Palace | v0.17.0 (`Git changeset ID: v0.17.0-dirty`), built by the repo `Dockerfile` |
| PETSc / SLEPc | **3.24.3 / 3.24.1** — both current releases, so this is not a stale-version bug |
| MFEM | `MFEM_USE_CUDA`, `MFEM_USE_UMPIRE`, `MFEM_USE_CEED` undefined, `MFEM_USE_OPENMP` undefined |
| hypre | `HYPRE_USING_CUDA`, `HYPRE_USING_DEVICE_MEMORY`, `HYPRE_USING_UMPIRE*`; `HYPRE_USING_UNIFIED_MEMORY` and `HYPRE_USING_GPU_AWARE_MPI` **undefined** |
| libCEED backend at runtime | `/gpu/cuda/magma` |
| Palace runtime banner | `Device configuration: cuda,cpu` / `Memory configuration: host-umpire,cuda-umpire` |

The build is `Dockerfile` lines ~126–152: it clones `v0.17.0`, seds
`cmake/ExternalPalace.cmake` to inject `CMAKE_CUDA_HOST_COMPILER`, then
configures with `-DPALACE_WITH_CUDA=ON` and
`-DCMAKE_CUDA_ARCHITECTURES="70;75;80;86;89;90;100;120"`. **There is already a
`sed` precedent in that RUN layer**, which matters for §5.

---

## 2. Symptom

A `Device=GPU` run of `projects/Coax_Directional_Bridge.FCStd` (187,415
elements, FE order 2) dies immediately after Palace prints the multigrid
hierarchy:

```
Assembling multigrid hierarchy:
 Level 0 (p = 1): 246279 unknowns
 Level 1 (p = 2): 1278924 unknowns
 Level 0 (auxiliary) (p = 1): 40504 unknowns
 Level 1 (auxiliary) (p = 2): 286783 unknowns
<crash>
```

Under mpirun: `exited on signal 8 (Floating point exception)`. Run directly:
exit code 136 (= 128 + 8).

**GPU memory is full when this happens** (observed climbing 554 MiB → 7771 MiB
during the run), which makes it look like an out-of-memory failure. **It is
not.** On x86-64 SIGFPE is an integer divide-by-zero; floating-point ops
produce inf/NaN rather than trapping. Do not re-diagnose this as OOM.

---

## 3. Root cause — backtrace

`gdb -batch -ex run -ex "bt 25" --args /usr/local/bin/palace-x86_64.bin <config>`:

```
#0  BVMultInPlace_BLAS_CUDA ()            <-- SLEPc, CUDA basis-vector path
#1  BVMultInPlace_Mat_CUDA ()
#2  BVMultInPlace ()
#3  EPSSolve_KrylovSchur_Default ()
#4  EPSSolve ()
#5  SVDSolve_Cross ()
#6  SVDSolve ()
#7  palace::slepc::GetMaxSingularValue(ompi_communicator_t*, palace::ComplexOperator const&, bool, double, int)
#8  palace::(anonymous namespace)::GetLambdaMax(...)
#9  palace::ChebyshevSmoother<palace::ComplexOperator>::SetOperator(...)
#10 palace::DistRelaxationSmoother<palace::ComplexOperator>::SetOperators(...)
#11 palace::GeometricMultigridSolver<palace::ComplexOperator>::SetOperator(...)
#12 palace::BaseKspSolver<palace::ComplexOperator>::SetOperators(...)
#13 palace::RomOperator::SolveHDM(int, double, palace::ComplexVector&)
#14 palace::DrivenSolver::SweepAdaptive(palace::SpaceOperator&) const
```

Registers at the fault:

```
rax  0x1383cc  1278924      <-- the ND p=2 unknown count (the vector length N)
rbx  0x0       0            <-- the divisor
rcx  0x1       1
```

So: `N / 0`. The Chebyshev smoother needs a largest-eigenvalue estimate; Palace
obtains it via SLEPc (`SVDSolve_Cross` → `EPSSolve` → BV ops); on GPU that runs
SLEPc's **CUDA** BV routines, which divide by zero for this input.

`gdb` is **not** in the image — it was `apt-get install`ed at runtime to obtain
this. Consider adding it to `.devcontainer/Dockerfile` if you want it to persist.

---

## 4. Bisection — what actually triggers it

Same mesh throughout, one variable changed at a time:

| Variant | Hierarchy | Result |
|---|---|---|
| Order 1, GPU | single level, 246,279 | **runs** (reached iteration 17/400, sane S-params) |
| Order 2, GPU, adaptive sweep | 2 levels, **1,278,924** smoothed | **SIGFPE** |
| Order 2, GPU, uniform sweep | 2 levels, 1,278,924 smoothed | **SIGFPE**, same point |
| Order 2, GPU, `Linear.MGMaxLevels=1` | single level, 1,278,924 | **no crash** (ran the full 420 s window; exit 124 from `timeout`, not 136) |
| Order 2, GPU, small model¹ | 2 levels, **27,016** smoothed | **runs to completion** (exit 0) |

¹ `examples/Microstrip_test_new.FCStd`, 3,787 elements.

**Therefore it is NOT simply "order ≥ 2 fails on GPU".** That was the first
reading and the small-model test disproved it. It takes **both**:

- a **Chebyshev-smoothed level** — i.e. a non-coarsest level, which only exists
  when the p-multigrid hierarchy has more than one level (FE order ≥ 2); and
- that level being **large**. A *single* level of 1,278,924 unknowns is fine
  (`MGMaxLevels=1` row); *two* levels at 27,016 are fine. Bracket for the
  threshold: **27,016 works, 1,278,924 faults.** Not narrowed further.

This bracket, plus `N / 0`, is consistent with SLEPc computing a block size as
something like `workspace_or_limit / N` in integer arithmetic, which floors to
0 once N is large, and then dividing by it. **Unverified** — SLEPc source was
not available in the image to confirm. Worth confirming before filing upstream.

### Also ruled out

- **The adaptive/PROM sweep is not involved.** A plain uniform sweep crashes
  identically, despite `SweepAdaptive` appearing in the backtrace as the caller.
- **`PETSC_OPTIONS="-bv_type vecs"` and `"-bv_type svec"`: ignored.** Still
  crashes. Palace sets the BV/vec type internally (see §5), so the option never
  takes effect.
- **`Solver.Linear.Type = "Jacobi"`:** rejected by Palace before any solve —
  `MFEM abort: Unsupported linear solver type for boundary mode solver!` Not a
  route.
- **`MGMaxLevels=1` is not a usable workaround** even though it dodges the
  crash: a single-level hierarchy is a far weaker preconditioner and that run
  produced **no solver iteration output at all in 420 s**, versus order 1
  reaching iteration 17/400 in ~440 s.
- **ARPACK is not built in this image**, and `GetMaxSingularValue` exists only
  in the `palace::slepc` namespace — there is no non-SLEPc path to this
  estimate.
- **Upgrading SLEPc is not a fix**: 3.24.1 is current.

---

## 4b. TRY THIS FIRST — upgrade to Palace 0.18.1

Added after the rest of this document was written; it **reorders the plan**.
Palace 0.18.0 (2026-09-09) and 0.18.1 (2026-09-21) exist. From the upstream
`CHANGELOG.md`, 0.18.0 contains:

> Fixed smoother spectral estimates to pass a Hermitian similarity operator to
> the HEP solver. [PR 837](https://github.com/awslabs/palace/pull/837)

**That is the exact code path that crashes here.** "Smoother spectral
estimates" is `ChebyshevSmoother::SetOperator` → `GetLambdaMax`. In v0.17.0
that estimate goes through an **SVD** (`SVDSolve_Cross` → `EPSSolve_KrylovSchur`
→ `BVMultInPlace_Mat_CUDA`, frame #0–#6 in §3). Routing it to an **HEP** solver
with a Hermitian operator changes which SLEPc BV routines run, and plausibly
stops touching the faulting one.

**Not confirmed** — the PR is described as a correctness fix, not a CUDA fix,
and it may leave the same BV path in place. But it is far cheaper to test than
the PETSc/SLEPc surgery in §5, and if it works the entire §5/§6 effort is
unnecessary. **Bump the version and re-run the §7 reproduction before anything
else.**

0.18.x also brings, relevant to this project specifically:

- **cuDSS** — `config["Solver"]["Linear"]["Type"] = "cuDSS"`, build flag
  `PALACE_WITH_CUDSS=ON` (requires `PALACE_WITH_CUDA=ON`), for the KSP and wave
  port solvers (0.18.0 PR 717). v0.17.0 has **zero** cuDSS support, so this is
  the only route to it. See §8 for how much it can actually be worth here.
- **Wave-port fixes matching this project's geometry.** 0.18.0 PR 778 fixed
  "a bug in the 2D mode eigensolver that sometimes led to crashes when
  waveports were used in conjunction with non-zero conductivity materials" —
  these boards have both (σ = 5.96e7 S/m Robin BCs *and* three wave ports) —
  and a missing complex permittivity term for lossy cross-sections. 0.18.1
  PR 921 improved BoundaryMode convergence with lossy BCs.
- **Performance in the phase that actually dominates** (see §8): 0.18.0
  PR 824/825 move boundary postprocessing and visualization-field evaluation
  onto libCEED/GPU; PR 876 omits exact-zero material terms from fine-level
  partial assembly. 0.18.1 reduces repeated work in complex operators and in
  PROM construction, and buffers adaptive-sweep CSV writes (≤ once per 10 s)
  instead of rewriting a growing table at every output frequency.

### Headaches to expect on the bump

1. **The Dockerfile's `sed` is version-pinned.** It rewrites
   `cmake/ExternalPalace.cmake` to inject `CMAKE_CUDA_HOST_COMPILER`. If that
   line moved or changed in 0.18.x the `sed` silently matches nothing and the
   build fails in exactly the confusing `BLTSetupCUDA.cmake` way the
   Dockerfile's own comments document. Verify the `sed` still applies.
2. **Behaviour changes that alter results or timing:**
   - 0.18.1 PR 921 fixed "numeric wave ports ignoring their configured
     `MaxIts` and `KSPTol`". **This workbench writes both**
     (`palace/config.py` ~lines 166-169, per-port, when non-zero). They were
     silently ignored in 0.17.0 and will start taking effect — convergence and
     runtime will change for any document that sets them.
   - 0.18.0 PR 841: purely reactive *passive* lumped ports now report a finite
     `port-S.csv` row where it was identically zero, and `inc<idx>` columns of
     `port-V.csv`/`port-I.csv` change likewise.
   - 0.18.0 PR 778: eigenmode runs with frequency-dependent BCs are now
     evaluated at the true complex eigenfrequency.
   - 0.18.1 PR 886: a modal correction changes wave-port S-parameters for
     TM/hybrid modes (explicitly **TEM/TE unchanged**, so coax is likely
     unaffected — but verify against a known-good result).
3. **Output-parsing risk.** `palace/post_process.py` matches
   `Re{Z_PV[N]}` / `Im{Z_PV[N]}` in `port-Z.csv` by regex, and the workbench
   parses `port-S.csv`, `port-Z.csv`, `probe-E.csv`, `probe-B.csv`. Diff the
   actual CSV headers from a 0.18 run against 0.17 before trusting
   `CharacteristicZ` extraction — a silent header rename breaks it quietly.
4. **SchemaVer now exists** (1-1-0 … 1-7-0, with machine-readable
   compatibility data as of 0.18.1). Run the upstream `validate-config` tool
   against configs generated by `palace/config.py` as a cheap gate. None of the
   keys the workbench emits appear in the changelog as removed, but this is
   worth checking rather than assuming.
5. **Change one thing at a time.** Bump Palace with `PALACE_WITH_CUDSS=OFF`
   first and verify; add cuDSS as a separate revision. The `:cuda` image is
   already noted in CI as unproven on real GPU hardware — two new variables at
   once makes a failure unattributable.
6. `SaveAdaptMesh` output became `.meshgz` — irrelevant here, the workbench
   never sets `Refinement`/`SaveAdaptMesh` (verified: 0 references in
   `palace/config.py`).

## 5. Proposed fix — build PETSc/SLEPc without CUDA

**Rationale.** SLEPc's role in a driven simulation is small: wave-port boundary
modes (2-D eigenproblems) and this λmax estimate (a few dozen operator applies,
once per operator setup — not per Krylov iteration of the main solve). The
heavy work — operator assembly (libCEED), AMG and the Krylov solve (hypre/MFEM)
— does not involve PETSc at all. Moving only PETSc/SLEPc to the host should
avoid the faulting code path at modest cost.

**Evidence this is feasible without patching Palace's C++** — from
disassembling Palace's own SLEPc wrapper in the shipped binary:

1. **Palace never hands PETSc a raw device pointer.** No
   `VecCreateMPICUDAWithArray`, no `VecCreateSeqCUDAWithArray`, no
   `VecCUDAPlaceArray` call sites in `palace::slepc::*`. It uses PETSc's
   memory-type-**aware** API instead — `VecGetArrayReadAndMemType` (12 call
   sites), `VecGetArrayWriteAndMemType` (11) — which works against host or
   device vectors and reports which it returned. This was the main risk and it
   is absent.
2. **The vector type is a runtime string.** `MatShellSetVecType` (8 call sites)
   is called with one of `"cuda"`, `"hip"`, `"standard"` — all three string
   constants are present in the binary. With a CPU-only PETSc this should
   resolve to `"standard"` and SLEPc's CUDA BV path is never entered.
3. **`palace::slepc::ConfigurePetscDevice()` only sets two PETSc options** —
   `-use_gpu_aware_mpi 0` and `-device_select_cuda <n>` (confirmed by reading
   the `PetscOptionsSetValue` string arguments). PETSc's device involvement is
   configured, not baked into how Palace passes data.

**What to change.** Palace's superbuild builds PETSc and SLEPc itself; the
CUDA flags are gated on `PALACE_WITH_CUDA`. Inspect, in the `v0.17.0` source
tree that the Dockerfile clones to `/tmp/palace-src`:

- `cmake/ExternalPETSc.cmake`
- `cmake/ExternalSLEPc.cmake`
- `cmake/ExternalPalace.cmake` (already sed'ed in the Dockerfile)

Remove `--with-cuda` (and any `--with-cudac` / CUDA arch flags) from the PETSc
configure invocation, leaving every other dependency's CUDA settings untouched.
Apply it as another `sed -i` in the same `RUN` layer that already seds
`ExternalPalace.cmake`, so the change is visible in the Dockerfile rather than
hidden in a patch file.

### Predicted failure modes, in the order you are likely to hit them

1. **`Unknown vector type: cuda` at runtime.** This happens if Palace's
   `"cuda"`-vs-`"standard"` selection is gated on `mfem::Device` rather than on
   `#if defined(PETSC_HAVE_CUDA)`. Could not be determined from the binary —
   **check the source** (`palace/linalg/slepc.cpp`, look for `MatShellSetVecType`
   and the `VECCUDA`/`"cuda"` selection). If it is device-gated, add a small sed
   forcing `"standard"`, or gate it on `PETSC_HAVE_CUDA`.
2. **PETSc configure rejects the combination** (e.g. it wants CUDA because
   hypre was built with it). PETSc and hypre are separate builds here; if
   Palace's superbuild passes hypre to PETSc with `--with-hypre`, a CPU PETSc
   against a CUDA hypre may not configure. If so, the fallback is §6.
3. **Compile error in Palace's slepc wrapper** referencing CUDA-only PETSc
   symbols. Unlikely given evidence item 1, but if it happens, the wrapper is
   the only file needing attention.

---

## 6. Fallback if §5 proves unworkable

Patch SLEPc's `BVMultInPlace_BLAS_CUDA` in the superbuild (add a guard for a
zero block size, or clamp it to ≥ 1). This needs the SLEPc source, which the
superbuild downloads — add a `sed`/patch step against it. Confirm the actual
divisor first; do not guess at the expression.

Either way, **this is worth reporting upstream** (SLEPc, and/or Palace). A good
report contains: the backtrace in §3, the registers (`N / 0`), the
27,016-works / 1,278,924-faults bracket, SLEPc 3.24.1 + PETSc 3.24.3 + CUDA
12.8 on WSL2, and the fact that a single-level hierarchy of the same size does
not fault.

---

## 7. Reproduce and verify (inside the container)

Generating inputs, from a Python with FreeCAD importable
(`sys.path.insert(0, "/opt/conda/lib")` then `/workspace`):

```python
doc = FreeCAD.openDocument("<copy of>/projects/Coax_Directional_Bridge.FCStd")
sim = find_simulation(doc)
# Set BOTH explicitly -- never inherit them from whatever the document was last
# saved with. The bug needs order 2 (see §4): a document saved at order 1
# reproduces nothing and the run will look like a pass. The order-1 GPU path is
# the current user-facing workaround, so the document genuinely may be sitting
# at order 1.
sim.Order  = 2
sim.Device = "GPU"
from palace.mesh_dispatch import generate_mesh
mesh_path, geom, quality = generate_mesh(doc, mesh_dir) # ~45 s, Netgen backend
sim.MeshFile = mesh_path
from palace.config import generate_config                # only_excitation=1
```

Sanity-check the generated JSON before running it -- `Solver.Order` must be `2`
and `Solver.Device` must be `"GPU"`, and the run must print the **two-level**
hierarchy from §2 (`Level 0 (p = 1)` *and* `Level 1 (p = 2)`). A single-level
hierarchy means order 1 slipped through and the test is invalid.

Then `/usr/local/bin/palace-x86_64.bin config.json`. Expect exit **136** today.
Use `stdbuf -o0 -e0` — stdout is block-buffered and a crash loses the tail
otherwise, which will mislead you about where it died.

**Success criterion for the rebuild:** that same order-2 GPU config gets past
`Assembling multigrid hierarchy` and begins emitting solver iterations.

**Regression checks:** `examples/Microstrip_test_new.FCStd` at order 2 on GPU
must still complete (it does today — do not break the working case), and the
order-1 GPU path must still work.

---

## 8. Performance context — is this rebuild worth it?

Same mesh (187,415 elements), 3 frequency points, uniform sweep, single rank
(matching how the GPU was measured; `NumProcesses` defaults to 1 and the
workbench forces 1 on GPU):

| Config | Sweep (`It 3/3`) | Total wall | Peak mem |
|---|---|---|---|
| Order 1, **GPU** | **59.6 s** | **236 s** | 4.0 G |
| Order 1, CPU | 99.9 s | 352 s | 4.7 G |
| Order 2, CPU | **1780 s** | (killed during postprocessing) | — |

So the GPU is worth **~1.5x overall / ~1.7x on the solve** at order 1 — real but
modest. If order 2 on GPU scales similarly, the rebuild is worth roughly 1.7x on
order-2 runs, i.e. ~1780 s → ~1000 s on this 3-point case. Meaningful, not
transformative. Caveats: the order-2 CPU figure overlapped a concurrent GPU
simulation on the same machine and is therefore inflated by an unknown amount,
and the RTX 3060 Ti runs FP64 at 1/64 of FP32 — a data-center card with 1:2
FP64 would likely show a far larger GPU advantage (reasoning, not measured).

**Do NOT use multi-rank CPU as a baseline.** `mpirun -n 4` **hangs** on this
model: a `gdb` backtrace showed all ranks blocked in
`hypre_LocateAssumedPartition` → `PMPI_Recv`, under
`hypre_ParCSRMatrixCreateAssumedPartition` → `hypre_MatvecCommPkgCreate` →
`hypre_ParCSRMatrixExtractBExt` → `hypre_ParCSRMatrixRAPKTHost` (AMG setup),
with the identical frame 25 s apart. All 4 ranks sat at 100% CPU, which is
Open MPI busy-polling in `opal_progress`, not work. Ruled out: `/dev/shm` was
clean (352 K of 64 M, 4 vader segments as expected) and memory was ~1 GiB/rank.
Forcing full assembly (`PartialAssemblyOrder = 99`, confirmed by
`Operator assembly level: Full`) hung identically, so assembly level is not
involved. **This is an unrelated open bug worth its own investigation** — it
means CPU runs are effectively limited to one rank on models this size. Not
known whether it is specific to this container's Open MPI, to hypre built with
CUDA running on the host, or to this model. `mpirun -n 8` fails earlier still
(`not enough slots available`).

**Update (follow-up session): root-caused further, and the "hypre built with
CUDA" possibility above is now the confirmed leading suspect.** Reproduced
independently at rank **2** (not just 4), with a live backtrace this time,
stuck in the identical `hypre_ParCSRCommPkgCreateApart` family but inside the
much smaller 2-D wave-port eigensolve setup rather than the main 3-D system —
confirms this isn't a matrix-size issue, it's any >1-rank hypre distributed
matrix construction. Ruled out CUDA *transport* specifically (`ompi_info`
shows this Open MPI build has no CUDA GPU-buffer support compiled in at all).
Then ran the identical rank=2 test on the plain non-CUDA `:dev` image
(same host, same Open MPI 4.1.2, still on Palace v0.17.0) — it completed
cleanly in 8.2s. Full writeup and decision (shelve root-causing it, keep the
CUDA image as an opt-in single-rank-only environment rather than the
devcontainer default) is in `CLAUDE.md`'s "Multi-rank CPU is broken in the
CUDA-enabled build" entry.

### Where the time actually goes, and what that implies for cuDSS

From Palace's own elapsed-time report (GPU, order 1, 3 points, 236 s total;
indented rows are exclusive sub-categories, parents are remainders):

| Phase | Time | Share |
|---|---|---|
| Operator Construction (+ Wave Ports 17.2 s) | 145.5 s | 62% |
| Linear Solve (Preconditioner 36.3 + Setup 11.4 + solve 1.9) | 49.6 s | 21% |
| Mesh preprocessing | 18.1 s | 8% |
| Disk IO | 14.9 s | 6% |

**That table understates wave ports badly for real runs, because it is only 3
output frequencies.** Palace recomputes wave-port boundary modes **once per
output frequency** (verified: 19 output frequencies produced exactly 19
`Calculating boundary modes at wave ports` blocks). Measured per-frequency cost
in the adaptive online phase was **6.8 s** (It 1 at 332 s → It 19 at 454 s), of
which ~5.7 s falls in the `Wave Ports` timer. For the real 400-point sweep that
projects to **~45 min of wave-port work against ~5.5 min of one-time setup —
roughly 89% of the run.**

Consequences:
- **cuDSS is aimed at the right bucket**, since 0.18.0 PR 717 makes it
  selectable for the KSP *and wave port* solvers.
- **But the split inside that 5.7 s is unmeasured**: the 2-D port eigenproblem
  (cuDSS would help) versus re-assembling the frequency-dependent port Robin
  operator and projecting it into the 3-D system / PROM basis (cuDSS would not;
  0.18.1's "reduced repeated work in complex operators" and "reduced repeated
  work in PROM construction" would). Palace filing its `Wave Ports` timer under
  *Operator Construction* hints at the latter. Settle this with stack samples
  during the boundary-mode phase before investing in cuDSS.
- **A free lever exists today, no rebuild:** output density is a direct
  multiplier on the dominant cost. The sweep is 0.025–10 GHz at 0.025 GHz =
  400 points; PROM accuracy comes from the greedy sampling (`AdaptiveTol`,
  `AdaptiveMaxSamples`), not output density, so coarsening to 0.05 GHz should
  roughly halve wall clock with no loss of model fidelity. **Untested** —
  verify before relying on it.

## 9. Related, and a likely red herring

The investigation started from "the GPU run eats all dedicated memory and never
touches shared GPU memory". Measured directly against `libcudart` on this
machine:

| Allocation | 12 GiB requested on the 8 GiB card |
|---|---|
| `cudaMalloc` | **succeeded** — allocated, written and synchronized; `nvidia-smi` plateaued at ~7.8 GiB while the remainder came from WDDM shared system memory |
| `cudaMallocManaged` | **failed** — `out of memory` |

hypre here is `HYPRE_USING_DEVICE_MEMORY` (unified memory `#undef`), i.e. on
the spillable path. So shared GPU memory **is** reachable and is not the
blocker. Memory pressure is a correlate of this crash, not its cause.

---

## 10. Workbench-side changes already committed (context, no action needed)

- `palace/run_diagnosis.py` — maps a failed run to an explanation, including
  this SIGFPE. Parses mpirun's `exited on signal N` line, because mpirun
  substitutes its own exit code. If the fix lands, **update the SIGFPE message
  and `docs/troubleshooting.md`**, which currently tell users to drop to order 1
  or use CPU.
- `palace/gpu_memory.py` — pre-flight `nvidia-smi` report before every GPU launch.
- `commands/cmd_run.py` — feeds the first 40 lines plus a 200-line tail to the
  diagnosis.
- `CLAUDE.md` — full invariant entry for this bug.
