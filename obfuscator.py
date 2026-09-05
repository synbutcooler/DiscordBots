"""
OBF command support — bridges the Discord bot to the Kryos Lua obfuscator.

Commands added by register_obf_commands():
    .obf / !obf  (DMs only)
        DM the bot, attach a .lua file (or paste code after the command),
        get the obfuscated script back as an attachment. Nothing is ever
        posted in a server channel, so nothing leaks.
    /obf  (owner guild, ephemeral)
        Informational only — it does NOT obfuscate. Points people at the
        bot's DMs and credits feariosz0, who wrote the Kryos engine.
    .obfunlock
        Shows remaining unlock time, or hands out a LootLabs checkpoint link
        that buys OBF_UNLOCK_HOURS (default 24) of access. See obf_access.py.
    .obfhelp / !obfhelp
        Prints the KRS_* macro cheatsheet.

Who may use it (in order):
    1. The bot owner (OWNER_ID)                        -> always allowed
    2. Members of the owner guild with Administrator   -> allowed
    3. Members of the owner guild holding any role in
       OBF_ALLOWED_ROLE_IDS (comma-separated env var)  -> allowed

Because DMs carry no server context, non-owner access is checked against the
owner guild: you must be a member there, then the usual permission/role rules
apply. Everyone else gets a plain "nope".

ENGINE — already wired:
    obfuscator_engine/engine.py is SELF-CONTAINED: the Kryos v16.2 engine is
    embedded (encrypted; needs the KRS_ENGINE_KEY env var) and a static
    Lua 5.4.8 interpreter is embedded too. Use tools/update_obfuscator.py
    with your existing key to package the new source at level 3 only.
    Nothing to install, nothing else
    to deploy. Optionally override with OBF_ENGINE_CMD (stdin -> stdout CLI)
    or KRS_LUA_BIN (different Lua 5.3+ binary).
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
    """No engine reachable."""


# Shared blurb for /obf and .obfhelp. The obfuscator is feariosz0's Kryos
# engine; obfuscation itself only happens in DMs through .obf.
OBF_INFO = (
    "🔐 **Lua obfuscator — Kryos v16.2, made by feariosz0**\n\n"
    "The bundled engine uses **level 3 only** — no level argument needed.\n\n"
    "This command doesn't obfuscate anything itself. To obfuscate a script:\n"
    "1. **DM this bot**\n"
    "2. Attach your `.lua` file\n"
    "3. Type `.obf`\n\n"
    "…or paste code inline with `.obf <code>`. "
    "Everything stays in DMs, so your source and the result never touch a "
    "server channel. `.obfhelp` lists the KRS macros."
)


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

    # Administrator permission (Members only; DMs have no guild context)
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


# Engine runner ----------------------------------------------------------------
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
        limit_str = f"{limit_mb:g} MB" if limit_mb >= 1 else f"{OBF_MAX_BYTES:,} bytes"
        raise ObfuscationError(
            f"Script is too large (max {limit_str}). "
            "The obfuscator runs on the bot's server, mind the size."
        )

    cli_cmd = os.environ.get("OBF_ENGINE_CMD", "").strip()
    if cli_cmd:
        return _run_cli_engine(cli_cmd, source, timeout)

    # obfuscator_engine/engine.py is the self-contained Kryos runner.
    engine_path = ENGINE_DIR / "engine.py"
    if not engine_path.is_file():
        raise EngineNotConfigured(
            f"No engine at {engine_path} — obfuscator_engine/engine.py was not "
            "deployed. Upload it and redeploy."
        )
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    try:
        fn = __import__("engine", fromlist=["obfuscate"]).obfuscate
    except Exception as exc:
        raise EngineNotConfigured(
            f"Engine module failed to import: {exc}"
        ) from exc

    try:
        result = fn(source) if options is None else fn(source, options)
    except ObfuscationError:
        raise
    except Exception as exc:
        raise ObfuscationError(str(exc)) from exc

    if not isinstance(result, str) or not result.strip():
        raise ObfuscationError("Engine returned an empty result.")

    out_len = len(result.encode("utf-8"))
    if out_len > OBF_MAX_OUTPUT:
        raise ObfuscationError(
            f"Obfuscated output is {out_len / 1_000_000:.1f} MB — too big for a "
            "Discord attachment. Split the script into smaller pieces."
        )
    return result


# Discord commands -------------------------------------------------------------
def register_obf_commands(bot, owner_id: int, guild_id: int):
    """Attach the /obf, .obf, .obfunlock and .obfhelp commands to the bot."""
    import discord
    from discord import app_commands

    import obf_access

    async def _member_from_guild(user_id):
        """Resolve a DM user to a Member in the owner guild (roles/permissions)."""
        guild = bot.get_guild(guild_id)
        if guild is None:
            return None
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            return None

    def _brief(src_len, out_len, seconds):
        return (f"Obfuscated **{src_len:,}** -> **{out_len:,}** chars "
                f"in {seconds:.1f}s.")

    async def _run_and_reply(send, source, source_label):
        loop = asyncio.get_running_loop()
        started = time.monotonic()
        try:
            out = await loop.run_in_executor(None, run_engine, source)
        except EngineNotConfigured as exc:
            await send(f"⚠️ {exc}", file=None)
            return
        except ObfuscationError as exc:
            await send(f"❌ Obfuscation failed: {exc}", file=None)
            return
        elapsed = time.monotonic() - started

        buf = io.BytesIO(out.encode("utf-8"))
        file = discord.File(buf, filename="obfuscated.lua")
        await send(
            f"✅ {_brief(len(source), len(out), elapsed)}\n"
            f"Source: {source_label}",
            file=file,
        )

    async def _authorize_from_dm(user):
        """Privilege check used by .obfhelp: owner, or admin/role in the guild."""
        if user.id == owner_id:
            return True, ""
        member = await _member_from_guild(user.id)
        if member is None:
            return False, ("This is private — you need to be a member of the "
                           "bot's server to use the obfuscator. Join first, "
                           "then DM the bot.")
        return check_authorized(member, owner_id)

    def _fmt_left(seconds: int) -> str:
        hours, rem = divmod(int(seconds), 3600)
        return f"{hours}h {rem // 60}m" if hours else f"{rem // 60}m"

    class ObfClaimView(discord.ui.View):
        """Discord half of the existing website verification/claim flow."""

        def __init__(self, offer, user_id):
            super().__init__(timeout=1800)
            self.session_token = offer["session_token"]
            self.user_id = int(user_id)
            self.add_item(discord.ui.Button(
                label="🔗 Open Verification",
                style=discord.ButtonStyle.link,
                url=offer["gateway_url"],
            ))

        @discord.ui.button(
            label="✅ Claim Access", style=discord.ButtonStyle.success
        )
        async def claim_access(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "❌ This isn't your verification session.", ephemeral=True
                )
                return
            try:
                expires_at = await asyncio.to_thread(
                    obf_access.claim_verification,
                    self.session_token,
                    interaction.user.id,
                )
            except obf_access.AccessError as exc:
                await interaction.response.send_message(f"⏳ {exc}", ephemeral=True)
                return
            except Exception:
                await interaction.response.send_message(
                    "⚠️ Couldn't activate access right now. Try again in a minute.",
                    ephemeral=True,
                )
                return

            expires_ts = int(expires_at)
            embed = discord.Embed(
                title="✅ Obfuscator Unlocked",
                description=(
                    "Your website verification was confirmed. You can now DM "
                    "`.obf` with your `.lua` file."
                ),
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Access Expires", value=f"<t:{expires_ts}:R>", inline=True
            )
            embed.set_footer(text="A failed obfuscation does not consume the unlock.")
            for item in self.children:
                if isinstance(item, discord.ui.Button) and not item.url:
                    item.disabled = True
            button.label = "✅ Access Claimed"
            await interaction.response.edit_message(
                content=None, embed=embed, view=self
            )
            self.stop()

    async def _unlock_prompt(user):
        """Create/resume the normal Vadrifts key-system gateway session."""
        try:
            offer = await asyncio.to_thread(
                obf_access.create_verification_offer,
                user.id,
                str(user),
                guild_id,
            )
        except obf_access.AccessError as exc:
            return f"⚠️ I couldn't create a verification session: {exc}"

        view = ObfClaimView(offer, user.id)
        if offer.get("completed"):
            embed = discord.Embed(
                title="✅ Verification Already Complete",
                description=(
                    "The website already confirmed your LootLabs task. "
                    "Click **Claim Access** below."
                ),
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title="🔒 Obfuscator Verification",
                description=(
                    f"**Unlocking: {offer['profile_name']}**\n\n"
                    "1️⃣ Click **Open Verification** below\n"
                    "2️⃣ On Vadrifts, click **LootLabs** and complete the task\n"
                    "3️⃣ Return here and click **Claim Access**\n\n"
                    "⏱️ The website session expires in **30 minutes**"
                ),
                color=discord.Color.blurple(),
            )
        embed.set_footer(
            text=f"One verification unlocks {obf_access._unlock_hours()} hours."
        )
        return embed, view

    async def _send_access_denial(ctx, payload):
        if isinstance(payload, tuple):
            embed, view = payload
            await ctx.send(embed=embed, view=view)
        else:
            await ctx.send(payload)

    async def _check_obf_access(user):
        """Gate for .obf: bypass IDs pass; others use the shared /ks flow."""
        if (obf_access.gate_disabled()
                or user.id == owner_id
                or user.id in obf_access.bypass_ids()):
            return True, ""

        member = await _member_from_guild(user.id)
        if member is None:
            return False, ("You need to be a member of the bot's server to use "
                           "the obfuscator. Join first, then DM the bot.")

        try:
            if obf_access.has_access(user.id):
                return True, ""
        except obf_access.AccessError as exc:
            return False, f"⚠️ {exc}"

        return False, await _unlock_prompt(user)

    # --- .obf (DMs only, attachment or inline) --------------------------------
    @bot.command(name="obf", aliases=["obfuscate"])
    async def obf_dm(ctx, *, code: str = None):
        if ctx.guild is not None:
            await ctx.send(
                "🤫 This runs in DMs only — attach your `.lua` file here: "
                "DM the bot and send the script as an attachment. "
                "Keeps the source and the result private."
            )
            return

        ok, why = await _check_obf_access(ctx.author)
        if not ok:
            await _send_access_denial(ctx, why)
            return

        source = None
        label = None
        if ctx.message.attachments:
            att = ctx.message.attachments[0]
            label = att.filename
            if att.size > OBF_MAX_BYTES:
                await ctx.send(
                    f"❌ `{att.filename}` is too large "
                    f"(max {OBF_MAX_BYTES / 1_000_000:.0f} MB).")
                return
            try:
                raw = await att.read()
            except Exception as exc:
                await ctx.send(f"❌ Could not read the attachment: {exc}")
                return
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError:
                await ctx.send("❌ Attachment is not valid UTF-8 text "
                               "(is it a .lua file?).")
                return
        elif code and code.strip():
            source = code
            label = "pasted code"
        else:
            await ctx.send(
                "Send it like this:\n"
                "1. **DM** this bot\n"
                "2. Attach your `.lua` script\n"
                "3. Type `.obf`\n\n"
                "…or just type `.obf <code>` and I'll run that inline. "
                "`.obfhelp` shows the KRS macros."
            )
            return

        # Cooldown only starts counting when a real obfuscation begins.
        ok2, wait = check_cooldown(ctx.author.id)
        if not ok2:
            await ctx.send(f"⏳ Cooldown — try again in {wait}s.")
            return

        async with ctx.typing():
            async def send(content, file=None):
                if file is not None:
                    await ctx.send(content=content, file=file)
                else:
                    await ctx.send(content=content)

            await _run_and_reply(send, source, label)

    # --- /obf (owner guild, informational — deliberately does NOT obfuscate) --
    # Obfuscation only ever happens in DMs via .obf, so nothing lands in a
    # channel. This command exists purely to point people there.
    @bot.tree.command(
        name="obf",
        description=(
            "Kryos v16.2 by feariosz0 — level 3 only. DM this bot and use .obf."
        ),
        guild=discord.Object(id=guild_id),
    )
    async def obf_slash(interaction: discord.Interaction):
        await interaction.response.send_message(OBF_INFO, ephemeral=True)

    # --- .obfunlock (get/refresh a checkpoint link, show remaining time) ------
    @bot.command(name="obfunlock", aliases=["obfkey"])
    async def obfunlock(ctx):
        if ctx.guild is not None:
            await ctx.send("🤫 DM me `.obfunlock` — links shouldn't be public.")
            return

        if (obf_access.gate_disabled()
                or ctx.author.id == owner_id
                or ctx.author.id in obf_access.bypass_ids()):
            await ctx.send("✅ You're on the bypass list — no checkpoint needed. "
                           "Just DM me `.obf` with your script.")
            return

        try:
            left = obf_access.seconds_left(ctx.author.id)
            if left > 0:
                await ctx.send(f"✅ Already unlocked — **{_fmt_left(left)}** left. "
                               "DM me `.obf` with your `.lua` attached.")
                return
            await _send_access_denial(ctx, await _unlock_prompt(ctx.author))
        except obf_access.AccessError as exc:
            await ctx.send(f"⚠️ {exc}")

    # --- .obfhelp -------------------------------------------------------------
    @bot.command(name="obfhelp")
    async def obfhelp(ctx):
        ok, why = await _authorize_from_dm(ctx.author) if ctx.guild is None \
            else check_authorized(ctx.author, owner_id)
        if not ok:
            await ctx.send(why)
            return
        await ctx.send(
            "**KRS macros** (inside your script, before obfuscating):\n"
            "```lua\n"
            "local str = KRS_ENCSTR(\"test string\")   -- string stored encrypted\n"
            "local num = KRS_ENCNUM(36482)             -- number stored encrypted\n"
            "local f = KRS_NOVIRTUALIZE(function()     -- keep this function OUT of the VM\n"
            "    print(\"hi\")\n"
            "end)\n"
            "f()\n"
            "```\n"
            "`KRS_NOVIRTUALIZE` leaves the function as normal Lua (still encoded), "
            "so it's not run through the VM. Use it when a function misbehaves, "
            "is performance-critical, or when something like `ColorSequence` "
            "gets called at a time the VM can't handle yet.\n\n"
            "Obfuscator by **feariosz0** (Kryos v16.2, **level 3 only**).\n"
            "Usage: DM the bot → attach `.lua` → type `.obf`"
        )
