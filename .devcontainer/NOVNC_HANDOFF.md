# noVNC Setup — Current State & History

## How it works

`postStartCommand` in `devcontainer.json` runs `start-gui &` on every container start.
`start-gui.sh` does three things:

1. Kills any stale VNC / websockify processes from a previous run
2. Starts **TigerVNC** on display `:1` (port 5901) with no authentication
3. Starts **websockify** in the foreground, proxying port 6080 → 5901 and serving the noVNC web client

| Method | URL / Address |
|--------|---------------|
| Browser (noVNC) | `http://localhost:6080/vnc.html` |
| VNC client | `localhost:5901` |
| Password | none |

---

## Why no authentication

TigerVNC 1.12+ (Ubuntu 22.04) removed the `vncpasswd` binary. The password file must
be written manually using DES encryption. Two things made this unreliable:

1. **Wrong key**: TigerVNC's internal DES (`d3des.cxx`) bit-reverses each key byte before
   the key schedule. The raw obfuscation key `[23, 82, 107, 6, 35, 78, 88, 7]` must be
   translated to `[0xE8, 0x4A, 0xD6, 0x60, 0xC4, 0x72, 0x1A, 0xE0]` for standard DES.
   The previous fix used the raw key, so the passwd file was always wrong and every auth
   attempt failed.

2. **No working DES**: `openssl enc -des-ecb` is disabled in this image's OpenSSL provider,
   and `TripleDES` was removed from `cryptography.hazmat.primitives` in recent library versions.

Since VS Code's port forwarding is the actual security boundary, VNC auth adds nothing.
The server is started with `-SecurityTypes None --I-KNOW-THIS-IS-INSECURE`.

---

## What was tried before (and why it failed)

| Attempt | Problem |
|---------|---------|
| Python + `TripleDES` with key `[23, 82, 107, 6, 35, 78, 88, 7]` | Wrong key (missing bit-reversal); auth failed every time |
| `openssl enc -des-ecb` | DES-ECB disabled in the image's OpenSSL legacy provider |
| `TripleDES` from `cryptography.hazmat.primitives` | Deprecated/moved to `decrepit` submodule in newer versions |

---

## If VncAuth is ever needed again

The correct standard-DES key for the vncpasswd file format is:

```python
obfuscation_key = bytes([0xE8, 0x4A, 0xD6, 0x60, 0xC4, 0x72, 0x1A, 0xE0])
```

Compute with:

```python
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES  # or primitives fallback
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.backends import default_backend

plaintext = password.encode()[:8].ljust(8, b'\x00')
c = Cipher(TripleDES(obfuscation_key * 3), modes.ECB(), backend=default_backend())
passwd_bytes = c.encryptor().update(plaintext)  # write these 8 bytes to ~/.vnc/passwd
```

Then start the server with `-SecurityTypes VncAuth` (drop `--I-KNOW-THIS-IS-INSECURE`).

Also add `pkill -f "websockify.*6080"` **before** `tigervncserver -kill :1` in the cleanup
step — a stale websockify retrying auth against a fresh VNC session will hit the 5-failure
blacklist and cause "Failed to connect" in the browser even after the server restarts.
