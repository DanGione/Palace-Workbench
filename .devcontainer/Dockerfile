FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    # VNC defaults (override at runtime with -e flags if needed)
    VNC_PASSWORD=freecad \
    VNC_RESOLUTION=1920x1080 \
    DISPLAY=:1

# ── System packages ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core utilities
    git curl wget ca-certificates file unzip patchelf \
    # Build tools (needed by Palace CMake superbuild)
    build-essential cmake ninja-build gfortran pkg-config \
    # MPI (Palace requires an MPI implementation)
    libopenmpi-dev openmpi-bin \
    # BLAS/LAPACK
    liblapack-dev libopenblas-dev \
    # zlib — used by several Palace superbuild dependencies
    zlib1g-dev \
    # Python runtime
    python3 python3-pip python3-dev python3-venv \
    # VNC server
    tigervnc-standalone-server tigervnc-common \
    # Browser-based VNC client
    novnc websockify \
    # Lightweight window manager
    openbox obconf \
    # Fonts (required by Qt / FreeCAD)
    xfonts-base xfonts-100dpi xfonts-75dpi fonts-dejavu \
    # Mesa OpenGL — software rendering via llvmpipe inside VNC
    libgl1-mesa-glx libgl1-mesa-dri libglu1-mesa mesa-utils \
    # X11 client libraries FreeCAD / Qt need
    libxrender1 libxext6 libxi6 libxrandr2 libxtst6 \
    libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xfixes0 \
    # D-Bus (required by Qt applications)
    dbus-x11 \
    # Shell / user setup
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

# ── gmsh (Python API + CLI) ──────────────────────────────────────────────────
RUN pip install --no-cache-dir gmsh numpy scipy cmake matplotlib xarray netCDF4

# ── Palace (AWSLabs) — latest source from GitHub ─────────────────────────────
# Palace ships a CMake superbuild that downloads and compiles all of its own
# dependencies (MFEM, HYPRE, libCEED, SuperLU_DIST, nlohmann/json, fmt, …).
# Only system-level MPI, BLAS/LAPACK, and zlib are consumed from the OS.
# The source tree is deleted after installation to keep the image lean.
RUN git clone --depth=1 https://github.com/awslabs/palace.git /tmp/palace-src \
    && cmake -S /tmp/palace-src -B /tmp/palace-src/build \
             -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX=/usr/local \
    && cmake --build /tmp/palace-src/build -j$(nproc) \
    && cmake --install /tmp/palace-src/build \
    && rm -rf /tmp/palace-src

# ── Non-root dev user (VS Code dev-container convention) ─────────────────────
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

# xstartup: launch openbox as the window manager for the VNC session
RUN printf '#!/bin/bash\nexport LIBGL_ALWAYS_SOFTWARE=1\nexec openbox-session\n' \
        > /home/${USERNAME}/.vnc/xstartup \
    && chmod +x /home/${USERNAME}/.vnc/xstartup

# Openbox autostart: launch FreeCAD automatically when the desktop starts
RUN printf '#!/bin/bash\nfreecad &\n' \
        > /home/${USERNAME}/.config/openbox/autostart \
    && chmod +x /home/${USERNAME}/.config/openbox/autostart

USER root

# ── GUI startup script ────────────────────────────────────────────────────────
COPY .devcontainer/start-gui.sh /usr/local/bin/start-gui
RUN chmod +x /usr/local/bin/start-gui

WORKDIR /workspace
USER ${USERNAME}

EXPOSE 5901 6080
