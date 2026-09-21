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
# Pinned to v0.17.0 (latest tagged release as of writing) instead of tracking
# main — an unpinned clone silently picks up whatever's newest upstream on
# every image rebuild, making builds non-reproducible and liable to break.
#
# PALACE_WITH_CUDA/PALACE_CUDA_ARCHITECTURES: OFF/unused by default. With
# PALACE_WITH_CUDA=OFF the extra -D flags below are inert (PALACE_WITH_CUDA
# is a plain `OFF CACHE BOOL` in Palace's own CMakeLists.txt, confirmed at
# the v0.17.0 tag; CMAKE_CUDA_ARCHITECTURES is a no-op unless something
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
ARG PALACE_WITH_CUDA=OFF
# Volta(70) Turing(75) Ampere(80,86) Ada(89) Hopper(90) Blackwell(100,120) —
# broad default so the published image works across GPU generations without
# knowing the pulling user's exact card; narrow this for a faster local/dev
# build targeting just your own GPU's compute capability.
ARG PALACE_CUDA_ARCHITECTURES=70;75;80;86;89;90;100;120
RUN git clone --depth=1 --branch v0.17.0 https://github.com/awslabs/palace.git /tmp/palace-src \
    && cmake -S /tmp/palace-src -B /tmp/palace-src/build \
             -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX=/usr/local \
             -DPALACE_WITH_CUDA=${PALACE_WITH_CUDA} \
             -DCMAKE_CUDA_ARCHITECTURES="${PALACE_CUDA_ARCHITECTURES}" \
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
