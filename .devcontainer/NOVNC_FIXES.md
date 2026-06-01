# noVNC "Failed to connect to server" — Root Cause & Fixes

## Symptoms
- noVNC at `http://localhost:6080/vnc.html` showed **"Failed to connect to server"**
- After partial fix: VNC connected but showed **"Authentication failure"** for the correct password `freecad`

---

## Root Cause 1 — Wrong VNC password file encoding

**File:** `.devcontainer/start-gui.sh` (the Python heredoc)

The original script was generating the passwd file using the VNCAuth *challenge-response* algorithm:

```
DES(key=bit_reversed_password, plaintext=0x00 * 8)
```

The TigerVNC passwd **file** format is the opposite: it stores the password bytes
DES-encrypted with a fixed obfuscation key, so the server can recover the plaintext
password at startup for use in challenge-response:

```
DES(key=[23, 82, 107, 6, 35, 78, 88, 7], plaintext=password_bytes_padded_to_8)
```

This is what `vncpasswd` (removed from the Ubuntu 22.04 TigerVNC packages) produced.
The two outputs are completely different byte strings, so every auth attempt failed
regardless of the password entered.

**Fix:** Replaced the Python encoding logic to use the correct fixed obfuscation key.

---

## Root Cause 2 — Stale websockify blacklisting 127.0.0.1

**File:** `devcontainer.json` → `postStartCommand: "start-gui &"`

`postStartCommand` re-runs on every VSCode reconnect to the container. Each run:
1. Killed the VNC server and started a fresh one (resetting its state)
2. **Did not kill the existing websockify process**

The stale websockify kept trying to proxy new browser connections to the newly started
VNC server. Because the password file encoding was wrong, every attempt triggered an
auth failure. After 5 consecutive failures from `127.0.0.1`, TigerVNC blacklists that
IP — blocking all further connections even before the handshake, which surfaces as
"Failed to connect to server" in noVNC rather than an auth error.

**Fix:** Added `pkill -f "websockify.*6080" 2>/dev/null || true` *before* the VNC kill
in the cleanup section, so no stale websockify survives to hammer the new VNC session.

---

## Changes Made

### `.devcontainer/start-gui.sh`

```diff
-# Kill any stale session from a previous container start
+# Kill any stale session from a previous container start
+pkill -f "websockify.*6080" 2>/dev/null || true   # kill before VNC so it can't retry-auth against the new session
 tigervncserver -kill :1 2>/dev/null || true
 rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 2>/dev/null || true
```

```diff
 python3 - "${VNC_PASSWORD:-freecad}" <<'PYEOF'
-import sys, struct
-
-def _reverse_bits(b):
-    return int(f"{b:08b}"[::-1], 2)
-
-def vnc_encode_password(pw):
-    key = bytes(_reverse_bits(c) for c in (pw.encode()[:8]).ljust(8, b'\x00'))
-    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
-    from cryptography.hazmat.backends import default_backend
-    c = Cipher(algorithms.TripleDES(key * 3), modes.ECB(), backend=default_backend())
-    e = c.encryptor()
-    return e.update(b'\x00' * 8) + e.finalize()
-
-import os, stat
-passwd_path = os.path.expanduser("~/.vnc/passwd")
-with open(passwd_path, "wb") as f:
-    f.write(vnc_encode_password(sys.argv[1]))
-os.chmod(passwd_path, stat.S_IRUSR | stat.S_IWUSR)
+import sys, os, stat
+from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
+from cryptography.hazmat.backends import default_backend
+
+def vnc_encode_password(pw):
+    # TigerVNC vncpasswd format: password bytes DES-encrypted with a fixed obfuscation key.
+    # The server decrypts this at startup to recover the plaintext password for challenge-response.
+    obfuscation_key = bytes([23, 82, 107, 6, 35, 78, 88, 7])
+    plaintext = pw.encode()[:8].ljust(8, b'\x00')
+    c = Cipher(algorithms.TripleDES(obfuscation_key * 3), modes.ECB(), backend=default_backend())
+    return c.encryptor().update(plaintext)
+
+passwd_path = os.path.expanduser("~/.vnc/passwd")
+with open(passwd_path, "wb") as f:
+    f.write(vnc_encode_password(sys.argv[1]))
+os.chmod(passwd_path, stat.S_IRUSR | stat.S_IWUSR)
 PYEOF
```

---

## Access

| Method | URL / Address | Password |
|--------|---------------|----------|
| Browser (noVNC) | `http://localhost:6080/vnc.html` | `freecad` |
| VNC client | `localhost:5901` | `freecad` |

Note: `onAutoForward: openBrowser` in `devcontainer.json` opens `http://localhost:6080/`
which serves a directory listing — navigate manually to `/vnc.html`.
