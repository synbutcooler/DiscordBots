"""
OBF command support — bridges the Discord bot to the Kryos Lua obfuscator.

Commands added by register_obf_commands():
    /obf [code]        Slash command. Paste the Lua source (max ~6000 chars).
                       For bigger scripts use !obf with a .lua attachment.
    !obf [code]        Prefix command. Uses the rest of the message, or the
                       attached file if the message has an attachment.
    !obfhelp           Quick reference for the KRS_* macros.

Who may use it (in order):
    1. The bot owner (OWNER_ID)                        -> always allowed
    2. Members with Administrator permission            -> allowed
    3. Members holding any role in OBF_ALLOWED_ROLE_IDS -> allowed
       (comma-separated role IDs in the environment, e.g.
        OBF_ALLOWED_ROLE_IDS=123456789012345678,987654321098765432)

ENGINE — already wired:
    obfuscator_engine/engine.py runs obfuscator_engine/kryos.lua (Kryos v16.0)
    through a system lua5.4 (5.3+) interpreter, via subprocess. Nothing to
    install on the bot side beyond lua5.4 on the host (see Dockerfile).

    If you ever swap engines: either replace obfuscate() in
    obfuscator_engine/engine.py, or set OBF_ENGINE_CMD to a CLI command that
    reads the Lua source on stdin and writes the obfuscated result to stdout.
"""

import asyncio
import io
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

__all__ = ["ObfuscationError", "EngineNotConfigured", "run_engine",
           "register_obf_commands", "check_authorized", "check_cooldown"]

ENGINE_DIR = Path(__file__).resolve().parent / "obfuscator_engine"

# Environment knobs -----------------------------------------------------------
def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


OBF_TIMEOUT = _env_int("OBF_ENGINE_TIMEOUT", 180)         # seconds
OBF_COOLDOWN = _env_int("OBF_COOLDOWN_SECONDS", 30)       # seconds per user
OBF_MAX_BYTES = _env_int("OBF_MAX_SOURCE_BYTES", 2_000_000)     # ~2 MB source cap
OBF_MAX_OUTPUT = _env_int("OBF_MAX_OUTPUT_BYTES", 8_000_000)     # ~8 MB output cap


class ObfuscationError(RuntimeError):
    """Engine ran but failed (bad input, syntax error, crash...)."""


class EngineNotConfigured(ObfuscationError):
    """No engine reachable — the friend's code hasn't been dropped in yet."""


# Authorisation ----------------------------------------------------------------
def _allowed_role_ids():
    raw = os.environ.get("OBF_ALLOWED_ROLE_IDS", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def check_authorized(user, owner_id: int):
    """Return (ok, reason). 'user' is a discord.Member-ish object."""
    if user is None:
        return False, "Could not identify who you are."

    if user.id == owner_id:
        return True, ""

    # Administrator permission (works for Members; DM users have no guild)
    guild_perms = getattr(user, "guild_permissions", None)
    if guild_perms is not None and getattr(guild_perms, "administrator", False):
        return True, ""

    # Any of the configured roles?
    allowed = _allowed_role_ids()
    if allowed:
        roles = getattr(user, "roles", None) or []
        user_role_ids = {getattr(r, "id", r) for r in roles}
        if user_role_ids & allowed:
            return True, ""

    if allowed:
        return False, ("This command is restricted: you need the bot owner, "
                       "Administrator, or one of the OBF_ALLOWED_ROLE_IDS roles.")
    return False, "This command is restricted to the bot owner or Administrators."


# Cooldown ---------------------------------------------------------------------
_last_run = {}  # user_id -> time.monotonic()


def check_cooldown(user_id: int, seconds: int = None):
    """Return (ok, retry_after_seconds)."""
    seconds = seconds if seconds is not None else OBF_COOLDOWN
    now = time.monotonic()
    last = _last_run.get(user_id, 0)
    if now - last < seconds:
        return False, int(seconds - (now - last)) + 1
    _last_run[user_id] = now
    return True, 0


# Engine loader ----------------------------------------------------------------
def _load_python_engine():
    """Find the friend's obfuscator inside obfuscator_engine/ and return a callable."""
    if not ENGINE_DIR.is_dir():
        raise EngineNotConfigured(
            f"No obfuscator_engine/ folder next to obfuscator.py (looked at {ENGINE_DIR})."
        )

    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))

    import importlib

    for mod_name in ("engine", "obfuscator", "main", "krs_obfuscator"):
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            # Missing *dependency* of the engine (e.g. luaparser) is a real error.
            if exc.name != mod_name:
                raise ObfuscationError(
                    f"Engine module '{mod_name}' is missing a dependency: {exc.name}. "
                    f"Install it (pip install {exc.name}) and restart the bot."
                ) from exc
            continue
        except Exception as exc:  # engine crashed on import
            raise ObfuscationError(f"Engine module '{mod_name}' failed to import: {exc}") from exc

        for fn_name in ("obfuscate", "run", "main"):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                return fn
        raise ObfuscationError(
            f"Engine module '{mod_name}' has no callable named obfuscate/run/main."
        )

    raise EngineNotConfigured(
        "No engine found in obfuscator_engine/. Drop your friend's obfuscator "
        "code in there (e.g. as engine.py with def obfuscate(source): ...) "
        "or set OBF_ENGINE_CMD to a CLI command that reads stdin and writes "
        "the obfuscated script to stdout. See README_OBF.md."
    )


def _run_cli_engine(cmd, source: str, timeout: int):
    proc = subprocess.run(
        shlex.split(cmd),
        input=source.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise ObfuscationError(f"Engine (OBF_ENGINE_CMD) exited with code "
                               f"{proc.returncode}: {stderr[-2000:] or 'no stderr'}")
    out = proc.stdout.decode("utf-8", "replace")
    if not out.strip():
        raise ObfuscationError("Engine (OBF_ENGINE_CMD) produced no output.")
    return out


def run_engine(source: str, timeout: int = None, options: dict = None):
    """
    Run the obfuscator synchronously. Blocks — call via asyncio.to_thread().

    Raises ObfuscationError / EngineNotConfigured on failure.
    """
    timeout = timeout or OBF_TIMEOUT
    if not isinstance(source, str) or not source.strip():
        raise ObfuscationError("Empty script — nothing to obfuscate.")
    if len(source.encode("utf-8")) > OBF_MAX_BYTES:
        limit_mb = OBF_MAX_BYTES / 1_000_000
        if limit_mb < 1:
            limit_str = f"{OBF_MAX_BYTES:,} bytes"
        else:
            limit_str = f"{limit_mb:g} MB"
        raise ObfuscationError(
            f"Script is too large (max {limit_str}). "
            "The obfuscator runs on the bot's server, mind the size."
        )

    cli_cmd = os.environ.get("OBF_ENGINE_CMD", "").strip()
    if cli_cmd:
        return _run_cli_engine(cli_cmd, source, timeout)

    fn = _load_python_engine()
    try:
        result = fn(source, options) if options else fn(source)
    except ObfuscationError:
        raise
    except TypeError:
        # Some engines only take the source string.
        result = fn(source)
    except Exception as exc:
        raise ObfuscationError(f"Engine raised {type(exc).__name__}: {exc}") from exc

    if not isinstance(result, str) or not result.strip():
        raise ObfuscationError("Engine returned an empty result.")

    out_len = len(result.encode("utf-8"))
    if out_len > OBF_MAX_OUTPUT:
        raise ObfuscationError(
            f"Obfuscated output is {out_len / 1_000_000:.1f} MB — too big for a "
            "Discord attachment. Split the script into smaller pieces and "
            "obfuscate them separately (raise OBF_MAX_OUTPUT_BYTES only if you "
            "tested that the bot can still upload it)."
        )
    return result


# Discord commands -------------------------------------------------------------
def register_obf_commands(bot, owner_id: int, guild_id: int):
    """Attach the /obf, !obf and !obfhelp commands to the bot."""
    import discord
    from discord import app_commands

    def _brief(src_len, out_len, seconds):
        return (f"Obfuscated **{src_len:,}** -> **{out_len:,}** chars "
                f"in {seconds:.1f}s.")

    async def _run_and_reply(send, source, source_label):
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        try:
            out = await loop.run_in_executor(None, run_engine, source)
        except EngineNotConfigured as exc:
            await send(f"⚠️ {exc}", file=None, ephemeral=True)
            return
        except ObfuscationError as exc:
            await send(f"❌ Obfuscation failed: {exc}", file=None, ephemeral=True)
            return
        elapsed = time.monotonic() - started

        buf = io.BytesIO(out.encode("utf-8"))
        file = discord.File(buf, filename="obfuscated.lua")
        await send(
            f"✅ {_brief(len(source), len(out), elapsed)}\n"
            f"Source: {source_label}",
            file=file, ephemeral=True,
        )

    # --- /obf ----------------------------------------------------------------
    @bot.tree.command(
        name="obf",
        description="[Owner/Admin] Obfuscate a Lua script with the KRS engine.",
        guild=discord.Object(id=guild_id),
    )
    @app_commands.describe(
        code="Lua source to obfuscate (or run !obf with a .lua attachment for big scripts)"
    )
    async def obf_slash(interaction: discord.Interaction, code: str = None):
        ok, why = check_authorized(interaction.user, owner_id)
        if not ok:
            await interaction.response.send_message(why, ephemeral=True)
            return

        if not (code or "").strip():
            await interaction.response.send_message(
                "Paste the script as the `code` argument (e.g. `/obf code: local x = 1`), "
                "or for longer scripts use `!obf` with a `.lua` file attached.",
                ephemeral=True,
            )
            return

        ok2, wait = check_cooldown(interaction.user.id)
        if not ok2:
            await interaction.response.send_message(
                f"⏳ Cooldown — try again in {wait}s.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        async def send(content, file=None, ephemeral=True):
            if file is not None:
                await interaction.followup.send(content=content, file=file, ephemeral=True)
            else:
                await interaction.followup.send(content=content, ephemeral=True)

        await _run_and_reply(send, code, "pasted code")

    # --- !obf ----------------------------------------------------------------
    @bot.command(name="obf", aliases=["obfuscate"])
    async def obf_prefix(ctx, *, code: str = None):
        ok, why = check_authorized(ctx.author, owner_id)
        if not ok:
            await ctx.send(why)
            return

        source = None
        label = "pasted code"
        if code and code.strip():
            source = code
        elif ctx.message.attachments:
            att = ctx.message.attachments[0]
            label = att.filename
            if att.size > OBF_MAX_BYTES:
                await ctx.send(f"❌ `{att.filename}` is too large (max {OBF_MAX_BYTES // 1_000_000} MB).")
                return
            raw = await att.read()
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError:
                await ctx.send("❌ Attachment is not valid UTF-8 text.")
                return
        else:
            await ctx.send(
                "Usage: `!obf <lua code>` or attach a `.lua` file to the same message.\n"
                "Tip: bigger scripts → attach the file. See `!obfhelp` for the macros."
            )
            return

        ok2, wait = check_cooldown(ctx.author.id)
        if not ok2:
            await ctx.send(f"⏳ Cooldown — try again in {wait}s.")
            return

        async with ctx.typing():
            async def send(content, file=None, ephemeral=True):
                if file is not None:
                    await ctx.send(content=content, file=file)
                else:
                    await ctx.send(content=content)

            await _run_and_reply(send, source, label)

    # --- !obfhelp ------------------------------------------------------------
    @bot.command(name="obfhelp")
    async def obfhelp(ctx):
        ok, why = check_authorized(ctx.author, owner_id)
        if not ok:
            await ctx.send(why)
            return
        await ctx.send(
            "**KRS macros** (inside your script, before obfuscating):\n"
            "```lua\n"
            "local str = KRS_ENCSTR(\"test string\")   -- string is stored encrypted\n"
            "local num = KRS_ENCNUM(36482)             -- number is stored encrypted\n"
            "local f = KRS_NOVIRTUALIZE(function()     -- keep this function OUT of the VM\n"
            "    print(\"hi\")\n"
            "end)\n"
            "f()\n"
            "```\n"
            "`KRS_NOVIRTUALIZE` leaves the function as normal Lua (still encoded), "
            "so it's not run through the VM. Use it when a function misbehaves, "
            "is performance-critical, or when something like `ColorSequence` "
            "gets called at a time the VM can't handle yet."
        )
