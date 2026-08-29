"""
Kryos engine runner — executes the Lua obfuscator (kryos.lua) with Lua 5.4+.

kryos.lua is feariosz0's self-contained Lua obfuscator ("Kryos v16.0").
It is run as a CLI:   lua5.4 kryos.lua <input.lua> [seed]
It reads the input file, runs the VM-compiler + macro expansion
(KRS_ENCSTR / KRS_ENCNUM / KRS_NOVIRTUALIZE), and prints the obfuscated
script to stdout.

Requirements on the host (see Dockerfile at the repo root):
    apt-get install -y lua5.4        # 5.3+ needed (uses <<, >>, //, bit ops)

Tunables via env:
    KRS_LUA_BIN        path to the Lua interpreter (default: lua5.4, then lua)
    OBF_ENGINE_TIMEOUT seconds before a run is killed (default 180)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_LUA = os.path.join(HERE, "kryos.lua")

BANNER = "This script is protected with Kryos v16.0"


def _find_lua():
    bin_name = os.environ.get("KRS_LUA_BIN", "").strip()
    candidates = [bin_name] if bin_name else ["lua5.4", "lua"]
    for c in candidates:
        path = shutil.which(c)
        if path:
            return path
    raise RuntimeError(
        "Lua 5.3+ is not installed. Install it (apt-get install lua5.4) or set "
        "KRS_LUA_BIN to a compatible interpreter, e.g. KRS_LUA_BIN=/usr/bin/lua5.4"
    )


def _check_version(lua_bin):
    if getattr(_check_version, "ok", False):
        return
    try:
        out = subprocess.run(
            [lua_bin, "-v"], capture_output=True, timeout=10
        ).stdout.decode("utf-8", "replace")
    except Exception:
        out = ""
    if "5.3" not in out and "5.4" not in out:
        raise RuntimeError(
            f"{lua_bin} is not Lua 5.3/5.4 (got: {out.strip() or 'unknown'}). "
            "Kryos uses bitwise operators and needs Lua 5.3+."
        )
    _check_version.ok = True


def obfuscate(source: str) -> str:
    from obfuscator import ObfuscationError  # safe: obfuscator is fully imported

    try:
        if not os.path.isfile(ENGINE_LUA):
            raise ObfuscationError(
                f"kryos.lua missing in {HERE} — the engine file was not deployed."
            )

        lua_bin = _find_lua()
        _check_version(lua_bin)

        timeout = int(os.environ.get("OBF_ENGINE_TIMEOUT", "180") or 180)

        # Random seed per run so junk names/code differ every time.
        seed = str(int.from_bytes(os.urandom(6), "big") % 2 ** 31)

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".lua", prefix="krs_src_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(source)

            try:
                proc = subprocess.run(
                    [lua_bin, ENGINE_LUA, tmp_path, seed],
                    capture_output=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise ObfuscationError(
                    f"Kryos took longer than {timeout}s and was killed. "
                    "Try a smaller script or raise OBF_ENGINE_TIMEOUT."
                )

            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace").strip()
                raise ObfuscationError(
                    "Kryos errored: " + (err[-2000:] or "no error output")
                )

            result = proc.stdout.decode("utf-8", "replace")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    except ObfuscationError:
        raise
    except Exception as exc:
        raise ObfuscationError(str(exc)) from exc

    if BANNER not in result:
        raise ObfuscationError(
            "Kryos output is missing its banner — the engine did not produce "
            "a valid obfuscated script."
        )
    if not result.strip():
        raise ObfuscationError("Kryos produced an empty result.")

    return result
