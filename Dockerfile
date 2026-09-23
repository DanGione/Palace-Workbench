ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    VNC_PASSWORD=freecad \
    VNC_RESOLUTION=1920x1080 \
    DISPLAY=:1

# ── System packages ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget ca-certificates file unzip patchelf \
    build-essential cmake ninja-build gfortran pkg-config \
    libopenmpi-dev openmpi-bin \
    liblapack-dev libopenblas-dev \
    zlib1g-dev \
    python3 python3-pip python3-dev python3-venv \
    tigervnc-standalone-server tigervnc-common \
    novnc websockify \
    openbox obconf \
    xfonts-base xfonts-100dpi xfonts-75dpi fonts-dejavu \
    libgl1-mesa-glx libgl1-mesa-dri libglu1-mesa mesa-utils \
    libxrender1 libxext6 libxi6 libxrandr2 libxtst6 \
    libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xfixes0 \
    dbus-x11 \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# ── Miniconda ────────────────────────────────────────────────────────────────
# Pinned to a py312 build (not "latest") because conda-forge's freecad=1.0.0
# has no build past Python 3.13 — "latest" drifts forward (now py314) and
# breaks the freecad install below with an unsatisfiable-environment error.
ENV CONDA_DIR=/opt/conda
RUN wget -q https://repo.anaconda.com/miniconda/Miniconda3-py312_26.5.3-1-Linux-x86_64.sh \
        -O /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p ${CONDA_DIR} \
    && rm /tmp/miniconda.sh \
    && ${CONDA_DIR}/bin/conda clean -afy

ENV PATH=${CONDA_DIR}/bin:${PATH}

# ── FreeCAD 1.0 (via conda-forge, full GUI build) ───────────────────────────
RUN conda install -y -c conda-forge --override-channels "freecad=1.0.0" \
    && conda clean -afy

# ── Python deps pre-installed into FreeCAD's conda environment ──────────────
# netgen-mesher: verified to import cleanly (netgen.occ) alongside gmsh in the
# same FreeCAD interpreter, both import orders, no OCC symbol conflict despite
# each vendoring its own OpenCASCADE build -- no separate venv needed.
RUN pip install --no-cache-dir gmsh netgen-mesher numpy scipy cmake matplotlib xarray netCDF4

# ── Palace (AWSLabs) — builds all dependencies via CMake superbuild ──────────
# Pinned to v0.18.1 (latest tagged release as of writing) instead of tracking
# main — an unpinned clone silently picks up whatever's newest upstream on
# every image rebuild, making builds non-reproducible and liable to break.
# Bumped from v0.17.0 -- see CLAUDE.md's "GPU runs SIGFPE at FE order >= 2"
# entry and PALACE_CUDA_ORDER2_HANDOFF.md for why.
#
# PALACE_WITH_CUDA/PALACE_CUDA_ARCHITECTURES: OFF/unused by default. With
# PALACE_WITH_CUDA=OFF the extra -D flags below are inert (PALACE_WITH_CUDA
# is a plain `OFF CACHE BOOL` in Palace's own CMakeLists.txt, confirmed at
# the v0.18.1 tag; CMAKE_CUDA_ARCHITECTURES is a no-op unless something
# enable_language(CUDA)s, which is itself gated behind PALACE_WITH_CUDA) —
# functionally the same CPU-only build as before this file was parameterized,
# though this has only been confirmed by reading Palace's CMakeLists, not by
# diffing an actual before/after build. Set PALACE_WITH_CUDA=ON with
# BASE_IMAGE=nvidia/cuda:...-devel-... to build the GPU variant (see
# docker-publish.yml's build-and-push-cuda job).
# TODO(GPU): this bakes the CUDA compiler/devel toolkit into the final image
# because Palace is built and installed in the same stage it's used from.
# Once the CUDA path is proven on real hardware, switch to a multi-stage
# build (compile in a `-devel` stage, copy the installed artifacts into a
# `-runtime` final stage) to drop several GB of compiler/headers nothing at
# runtime needs.
#
# Palace's own CMake superbuild has a bug (confirmed against upstream
# cmake/ExternalPalace.cmake at both the v0.17.0 and v0.18.1 tags -- the
# target line is byte-identical, only CMAKE_CUDA_ARCHITECTURES forwarding
# moved into a new palace_append_cuda_architectures() helper macro nearby,
# so the sed below still matches and applies correctly): the nested
# ExternalProject_Add(palace ...) step that configures Palace's real library
# source forwards -DCMAKE_CUDA_COMPILER/-DCMAKE_CUDA_FLAGS/-DCMAKE_CUDA_ARCHITECTURES
# to that inner configure, but never -DCMAKE_CUDA_HOST_COMPILER -- so neither
# passing it as a -D flag nor exporting it as an env var on this outer `cmake`
# invocation ever reaches that inner step (both tried and confirmed
# ineffective against the devcontainer's identical build: same
# BLTSetupCUDA.cmake:32 "CUDA language enabled prior to setting
# CMAKE_CUDA_HOST_COMPILER" failure at palace-cmake/src/palace-stamp/
# palace-configure either way). The sed patch below adds the missing forward
# right after cloning, alongside the existing CMAKE_CUDA_FLAGS line in that
# same file's "Configure GPU support" block -- a no-op when
# PALACE_WITH_CUDA=OFF, since that whole block (and thus the sed'd-in line)
# only gets appended to PALACE_OPTIONS when PALACE_WITH_CUDA is true.
#
# libCEED (a Palace CUDA dependency) build-time-links against the CUDA
# *driver* API (cuLaunchKernel etc.) via libceed.so's own NEEDED entry on
# libcuda.so.1, but nvidia/cuda's `-devel` images have no real driver
# installed at build time (GPU passthrough via --gpus is a
# container-*runtime* feature, not available during `docker build`). Two
# wrong attempts before landing on the real fix, both empirically verified,
# not just reasoned about:
# - LIBRARY_PATH=/usr/local/cuda/lib64/stubs (tried first) -- had no effect;
#   confirmed via a cheap standalone repro in a throwaway container (no
#   3rd Palace rebuild needed) that LIBRARY_PATH only affects how gcc
#   resolves an explicit `-l<name>` flag -- irrelevant here, since nothing
#   passes `-lcuda` at this point; the actual failure is `ld` trying to
#   resolve libceed.so's own *indirect* NEEDED(libcuda.so.1) entry, which
#   LIBRARY_PATH does not influence at all.
# - LD_LIBRARY_PATH (or -Wl,-rpath-link) pointed at the stubs dir directly
#   -- still failed in the same repro: the stubs dir only contains an
#   unversioned `libcuda.so`, but ld's indirect-NEEDED search looks for the
#   exact SONAME `libcuda.so.1` (as literally hinted by ld's own warning:
#   "needed by libceed.so, not found (try using -rpath or -rpath-link)"),
#   and no file by that exact name exists there.
# - Actual fix, confirmed in the same repro: symlink libcuda.so.1 ->
#   libcuda.so (creating the exact versioned name ld searches for), then
#   LD_LIBRARY_PATH pointed at it. Deliberately `export`ed inside this one
#   RUN's shell (inherited by every child process in its tree, including
#   nested try_compile sub-builds) rather than a Dockerfile-wide `ENV` --
#   unlike LIBRARY_PATH (build-time only), LD_LIBRARY_PATH also affects the
#   *running* container, and a persistent ENV pointing the dynamic loader at
#   this stub could make it silently prefer the fake stub over the real
#   driver library mounted in at actual runtime via --gpus=all. The symlink
#   itself is gated behind PALACE_WITH_CUDA=ON (unlike the harmless-either-way
#   LD_LIBRARY_PATH export below it) since /usr/local/cuda/lib64/stubs
#   doesn't exist at all on the default ubuntu:22.04 base image, and `ln -s`
#   into a nonexistent directory is a hard error, not a harmless no-op.
#
# A real GPU run at FE order >= 2 against a large model SIGFPEs: an integer
# modulo-by-zero in SLEPc's CUDA basis-vector routine
# (BVMultInPlace_BLAS_CUDA, src/sys/classes/bv/impls/cuda/bvcuda.cu -- read
# directly from a real clone of the exact v3.24.1 tag Palace pins, not
# inferred), reached via the Chebyshev smoother's largest-eigenvalue estimate
# (GetLambdaMax -> slepc::GetMaxSingularValue -> SVDSolve_Cross). Full
# bisection, backtrace, registers, and the exact faulting line are in
# CLAUDE.md's "GPU runs SIGFPE at FE order >= 2" entry and
# PALACE_CUDA_ORDER2_HANDOFF.md. Root cause: when a `cudaMemGetInfo()` check
# finds too little free GPU memory to hold the whole result matrix at once,
# that function computes a fallback batch size as
# `freemem/(m*sizeof(PetscScalar))` -- appears to divide by the wrong
# dimension (m, the huge ~1.3M-unknown operator size, rather than n, the
# small number of vectors actually being multiplied) -- floors to 0 in
# integer division once m is large enough relative to free memory, then
# crashes on `m % bs` two lines later with no zero-guard. The v0.18.1 bump
# above does NOT fix this -- confirmed by diffing PR #837 against
# palace/linalg/chebyshev.cpp at v0.17.0 vs v0.18.1: it only fixes a
# correctness bug on the branch already using the safe HEP path (real
# operators); the complex-operator branch this project's lossy/driven
# simulations hit still takes the same crashing SVD/CUDA route, unchanged
# across both versions -- reproduced fresh against v0.18.1 on the real board
# (projects/Coax_Directional_Bridge.FCStd) with byte-identical symptoms
# before concluding this.
#
# Decided NOT to patch this (a real, upstream, well-understood SLEPc bug,
# not something dubious) after weighing it against pivoting to cuDSS
# instead, below: it's genuinely a Palace/PETSc/SLEPc source patch either
# way, which we'd rather not carry indefinitely, and cuDSS is a real
# alternative that avoids needing to. (A CPU-only PETSc/SLEPc build was also
# tried and abandoned -- it compiles fine but Palace's own
# palace/linalg/petsc.hpp has a deliberate `#error "Mismatch between MFEM
# and PETSc CUDA support!"` guard forbidding exactly that split
# configuration, a second source patch on top of the SLEPc one that felt
# like compounding risk rather than resolving it.) Order 1 GPU is
# unaffected either way (single multigrid level, never reaches this code
# path) and stays comfortably within memory budget on this hardware (~4.0
# GiB peak measured vs. this card's 8 GiB, order 2 by contrast climbs to
# ~7.8 GiB right before crashing) -- see CLAUDE.md's GPU memory notes.
ARG PALACE_WITH_CUDA=OFF
# Volta(70) Turing(75) Ampere(80,86) Ada(89) Hopper(90) Blackwell(100,120) —
# broad default so the published image works across GPU generations without
# knowing the pulling user's exact card; narrow this for a faster local/dev
# build targeting just your own GPU's compute capability.
ARG PALACE_CUDA_ARCHITECTURES=70;75;80;86;89;90;100;120
# NVIDIA cuDSS (GPU-resident sparse direct solver, selectable at runtime via
# Solver.Linear.Type="cuDSS") -- added in Palace 0.18.0 (PR 717), requires
# PALACE_WITH_CUDA=ON, unavailable at all on v0.17.0. Targets the actual
# measured bottleneck for this project's real sweeps: wave-port boundary
# modes get recomputed once per output frequency and dominate a realistic
# sweep's wall clock (~89% of it, per PALACE_CUDA_ORDER2_HANDOFF.md's timing
# breakdown), far more than the main 3-D solve itself.
#
# Not bundled in the CUDA Toolkit base image and not pip/conda-installable
# for this purpose -- Palace's own install docs require CUDSS_DIR to point
# at a real NVIDIA cuDSS *archive* install. The exact tarball URL below
# comes from NVIDIA's own redistributable manifest
# (developer.download.nvidia.com/compute/cudss/redist/redistrib_0.7.1.json),
# not guessed: cuDSS 0.7.1.4, the CUDA 12 Linux x86_64 build.
#
# Pinned to 0.7.1, NOT the newest 0.8.0 release -- confirmed via a real build
# attempt against 0.8.0.10 that it fails to compile: 15 errors in
# extern/mfem/linalg/cudss.cpp ("too few arguments in function call" for
# cudssMatrixCreateCsr/cudssMatrixCreateDn). Root-caused, not just observed:
# Palace vendors cuDSS support into MFEM via its own patch
# (cmake/ExternalMFEM.cmake downloads extern/patch/mfem/mfem_pr5124_cudss.diff,
# backporting MFEM's own still-unmerged upstream PR #5124, since MFEM's
# pinned commit has no cuDSS integration of its own at all -- confirmed by
# cloning MFEM's real repo at Palace's exact pinned commit and finding zero
# files matching *cudss* anywhere in it). That vendored patch calls
# cudssMatrixCreateCsr with a single combined index-type argument
# (CUDA_R_32I) -- a 13-argument signature. cuDSS 0.8.0 changed this function
# to take two separate index-type arguments (14 args) -- confirmed by
# reading MFEM's own upstream `master` branch, which already handles this
# via `#if CUDSS_VERSION >= 800`, a check the older vendored patch has no
# equivalent of. 0.7.1 is the newest release before that breaking change.
#
# Licensing note, deliberately left unresolved here: cuDSS ships under its
# own NVIDIA license, separate from -- and less clearly redistributable
# than -- the CUDA Toolkit math libraries (cuBLAS/cuSPARSE/cuSOLVER/etc.)
# already in this image, which have an explicit "Attachment A"
# redistributable-components list in the CUDA EULA. cuDSS's own agreement
# has no equivalent list; general "incorporated into a software application"
# language plausibly covers a real application like this one, but that is
# not a confirmed legal reading. PALACE_WITH_CUDSS defaults OFF specifically
# so this is never baked into anything published to GHCR without that being
# its own deliberate decision -- local builds/testing only until resolved.
#
# Palace's own PR #826 (its cuDSS integration) notes its MPI communication
# plugin is compiled from source against whichever MPI Palace itself uses --
# NVIDIA only ships a prebuilt Open MPI binary, so an MPICH-based build
# would silently link the wrong one. This image already uses Open MPI
# (libopenmpi-dev/openmpi-bin, installed earlier in this file), so this
# should be a non-issue here, but is exactly the kind of thing worth
# rechecking in the build log first if this step ever fails confusingly.
ARG PALACE_WITH_CUDSS=OFF
RUN if [ "${PALACE_WITH_CUDA}" = "ON" ]; then \
      ln -s /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1; \
    fi \
    && if [ "${PALACE_WITH_CUDSS}" = "ON" ]; then \
         mkdir -p /opt/cudss \
         && curl -fsSL https://developer.download.nvidia.com/compute/cudss/redist/libcudss/linux-x86_64/libcudss-linux-x86_64-0.7.1.4_cuda12-archive.tar.xz \
            | tar -xJ -C /opt/cudss --strip-components=1; \
       fi \
    && export LD_LIBRARY_PATH=/usr/local/cuda/lib64/stubs \
    && git clone --depth=1 --branch v0.18.1 https://github.com/awslabs/palace.git /tmp/palace-src \
    && sed -i 's|"-DCMAKE_CUDA_FLAGS=${CMAKE_CUDA_FLAGS}"|"-DCMAKE_CUDA_FLAGS=${CMAKE_CUDA_FLAGS}"\n    "-DCMAKE_CUDA_HOST_COMPILER=${CMAKE_CUDA_HOST_COMPILER}"|' /tmp/palace-src/cmake/ExternalPalace.cmake \
    && cmake -S /tmp/palace-src -B /tmp/palace-src/build \
             -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX=/usr/local \
             -DPALACE_WITH_CUDA=${PALACE_WITH_CUDA} \
             -DCMAKE_CUDA_ARCHITECTURES="${PALACE_CUDA_ARCHITECTURES}" \
             -DCMAKE_CUDA_HOST_COMPILER=$(command -v g++) \
             -DPALACE_WITH_CUDSS=${PALACE_WITH_CUDSS} \
             -DCUDSS_DIR=/opt/cudss \
    && cmake --build /tmp/palace-src/build -j$(nproc) \
    && cmake --install /tmp/palace-src/build \
    && rm -rf /tmp/palace-src \
    && if [ "${PALACE_WITH_CUDA}" = "ON" ]; then \
         mkdir -p /usr/local/share \
         && touch /usr/local/share/palace-cuda-enabled; \
       fi
# The filename above (palace-cuda-enabled) must match _CUDA_SENTINEL_NAME in
# palace/runner.py exactly -- nothing enforces this beyond this comment pair,
# so grep both locations before renaming/relocating either one.

# ── Non-root user ─────────────────────────────────────────────────────────────
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=${USER_UID}

RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" \
         > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

# ── VNC + Openbox user config ─────────────────────────────────────────────────
USER ${USERNAME}

RUN mkdir -p /home/${USERNAME}/.vnc /home/${USERNAME}/.config/openbox

RUN printf '#!/bin/bash\nexport LIBGL_ALWAYS_SOFTWARE=1\nexec openbox-session\n' \
        > /home/${USERNAME}/.vnc/xstartup \
    && chmod +x /home/${USERNAME}/.vnc/xstartup

# Openbox autostart: launch FreeCAD automatically when the desktop starts
# QT_ENABLE_HIGHDPI_SCALING=0 disables Qt 5.14+ auto HiDPI scaling (QT_AUTO_SCREEN_SCALE_FACTOR
# is ignored in Qt 5.14+ and QT_ENABLE_HIGHDPI_SCALING is the correct override).
RUN printf '#!/bin/bash\nexport QT_AUTO_SCREEN_SCALE_FACTOR=0\nexport QT_SCALE_FACTOR=1\nexport QT_ENABLE_HIGHDPI_SCALING=0\nexport QT_FONT_DPI=96\nfreecad &\n' \
        > /home/${USERNAME}/.config/openbox/autostart \
    && chmod +x /home/${USERNAME}/.config/openbox/autostart

USER root

# ── Workbench source (baked in — no bind-mount needed) ───────────────────────
COPY --chown=${USERNAME}:${USERNAME} . /opt/palace-workbench
RUN find /opt/palace-workbench -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# ── User project files directory ─────────────────────────────────────────────
RUN mkdir -p /projects && chown ${USERNAME}:${USERNAME} /projects
VOLUME /projects

# ── GUI startup script (uses root-level version; symlinks to /opt/palace-workbench)
COPY start-gui.sh /usr/local/bin/start-gui
RUN chmod +x /usr/local/bin/start-gui

WORKDIR /projects
USER ${USERNAME}

EXPOSE 5901 6080
CMD ["/usr/local/bin/start-gui"]
