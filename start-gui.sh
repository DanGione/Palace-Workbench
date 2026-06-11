#!/bin/bash
set -e

# ── Register workbench with FreeCAD ──────────────────────────────────────────
# FreeCAD searches for user workbenches in its user Mod directory.
# The conda-forge 1.0 build uses the XDG path; older builds use ~/.FreeCAD.
# We symlink both so the workbench is found regardless of which path is active.
for MOD_DIR in "${HOME}/.local/share/FreeCAD/Mod" "${HOME}/.FreeCAD/Mod"; do
    mkdir -p "${MOD_DIR}"
    ln -sfn /workspace "${MOD_DIR}/PalaceWorkbench"
done

mkdir -p ~/.vnc

# Reset FreeCAD's saved window geometry to the VNC desktop origin.
# FreeCAD persists its window position across sessions; a stale negative-offset
# value (e.g. "-54 -30 1920 1080" from a previous window-manager decoration
# interaction) permanently places the title bar off-screen on every restart.
VNC_RES="${VNC_RESOLUTION:-1920x1080}"
VNC_W="${VNC_RES%x*}"
VNC_H="${VNC_RES#*x}"
python3 - "$VNC_W" "$VNC_H" << 'PYEOF'
import re, os, sys
w, h = sys.argv[1], sys.argv[2]
cfg = os.path.expanduser('~/.config/FreeCAD/user.cfg')
if os.path.exists(cfg):
    txt = open(cfg).read()
    txt = re.sub(r'(<FCText Name="Geometry">)[^<]*(</FCText>)',
                 rf'\g<1>0 0 {w} {h}\g<2>', txt)
    open(cfg, 'w').write(txt)
PYEOF

# Regenerate xstartup with Qt display-scaling env vars.
# QT_AUTO_SCREEN_SCALE_FACTOR=0 prevents Qt from reading a bogus DPI from the
# VNC server and scaling the UI at 2×, which shifts rendered content from click
# coordinates and makes FreeCAD appear offset.
cat > ~/.vnc/xstartup << 'XSTARTUP'
#!/bin/bash
export LIBGL_ALWAYS_SOFTWARE=1
export QT_AUTO_SCREEN_SCALE_FACTOR=0
export QT_SCALE_FACTOR=1
export QT_ENABLE_HIGHDPI_SCALING=0
export QT_FONT_DPI=96
export QT_X11_NO_MITSHM=1
exec openbox-session
XSTARTUP
chmod +x ~/.vnc/xstartup

# Kill any stale session from a previous container start
pkill -f "websockify.*6080" 2>/dev/null || true
tigervncserver -kill :1 2>/dev/null || true
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 2>/dev/null || true

# Start TigerVNC with no authentication — the Docker port-bind is the security boundary.
# VncAuth is skipped to avoid the vncpasswd DES-encoding complexity (vncpasswd is absent in
# TigerVNC 1.12+ Ubuntu packages and Python DES libs are deprecated/provider-disabled).
tigervncserver :1 \
    -geometry "${VNC_RESOLUTION:-1920x1080}" \
    -depth 24 \
    -dpi 96 \
    -localhost no \
    -SecurityTypes None \
    --I-KNOW-THIS-IS-INSECURE

printf '\n'
printf '╔══════════════════════════════════════════════════════════════════════════════════╗\n'
printf '║  FreeCAD GUI is starting on the virtual desktop.                                 ║\n'
printf '║                                                                                  ║\n'
printf '║  Browser: http://localhost:6080/vnc.html?autoconnect=1&resize=scale              ║\n'
printf '║  VNC:     localhost:5901  (no password)                                          ║\n'
printf '╚══════════════════════════════════════════════════════════════════════════════════╝\n'
printf '\n'

# Start noVNC / websockify in the foreground (keeps the process alive)
exec websockify \
    --web /usr/share/novnc/ \
    6080 \
    localhost:5901
