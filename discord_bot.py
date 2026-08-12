import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random
import re
import time
import logging
from datetime import datetime, timedelta
from config import DISCORD_TOKEN
from key_store import (
    create_key_for_user,
    delete_keys_by_discord_id,
    get_stats,
    cleanup_expired,
    GUILD_ID
)
from guild_key_system import (
    get_guild_config, init_guild_config, save_guild_config,
    create_session, get_session,
    update_session, get_pending_session,
    create_guild_key, delete_guild_keys_by_user,
    get_guild_key_stats, cleanup_expired_guild_keys,
    list_recent_keys,
    get_destination_url, get_script_profile, get_script_profiles,
    create_script_profile, update_script_profile, delete_script_profile,
    get_profile_by_name,
    SERVER_BASE_URL
)
from server_settings import (
    get_settings, update_settings, antispam_active,
)

logger = logging.getLogger(__name__)

TARGET_CHANNEL_ID = 1389210900489044048
AUTH_CHANNEL_ID = 1287714060716081183
LOG_CHANNEL_ID = 1270314848764559494
OWNER_ID = 1144213765424947251
DELAY_SECONDS = 1
BOOST_TEST_CHANNEL_ID = 1270301984897110148

DISCORD_KEY_EXPIRY_HOURS = 336

# --- Multi-tenant / public-bot settings -----------------------------------
# This single bot runs BOTH the owner's personal server (legacy premium key
# system + fun features) AND the public "/ks" Key-System-as-a-Service that any
# server can use. Everything personal is gated behind OWNER_GUILD_ID so it can
# never fire inside a customer's server.
OWNER_GUILD_ID = 1241797935100989594


def is_owner_guild(guild_id) -> bool:
    return guild_id is not None and guild_id == OWNER_GUILD_ID


MONITORED_CHANNELS = {
    1454200774044291345,
    1493410559331139697,
    1493410056883011776,
    1493409909235253380,
}

URL_PATTERN = re.compile(r'https?://\S+|www\.\S+')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)

recent_boosts = {}
pending_tasks = {}
last_meow_count = None
cute_symbols = [">///<", "^-^", "o///o", "x3"]
submitted_hwids = {}


async def send_good_boy_after_delay(user_id, channel):
    await asyncio.sleep(DELAY_SECONDS)
    if user_id in recent_boosts:
        await channel.send(f"<@{user_id}> good boy")
        recent_boosts.pop(user_id, None)
        pending_tasks.pop(user_id, None)


class HWIDModal(discord.ui.Modal, title="Enter Your HWID"):
    hwid = discord.ui.TextInput(label="Paste your HWID here", style=discord.TextStyle.short, placeholder="Example: ABCDEFGH-1234-IJKL-5678-MNOPQRSTUVW", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        # The manual HWID-authentication flow is an owner-server-only premium
        # feature; it DMs the owner, so it must never run from another guild.
        if not is_owner_guild(interaction.guild_id):
            await interaction.response.send_message(
                "This command isn't available in this server.", ephemeral=True)
            return

        user = interaction.user
        hwid_value = self.hwid.value.strip()
        now = datetime.utcnow()

        if len(hwid_value) < 35:
            await interaction.response.send_message("HWID too short. Must be at least 35 characters.", ephemeral=True)
            return

        if len(hwid_value) > 50:
            await interaction.response.send_message("HWID too long. Maximum 50 characters.", ephemeral=True)
            return

        if not re.fullmatch(r"[A-Za-z0-9-]+", hwid_value):
            await interaction.response.send_message("HWID contains invalid characters. Use only letters, numbers, and dashes.", ephemeral=True)
            return

        if hwid_value in submitted_hwids:
            last_time = submitted_hwids[hwid_value]
            if now - last_time < timedelta(hours=24):
                await interaction.response.send_message("This HWID has already been submitted in the last 24 hours.", ephemeral=True)
                return

        submitted_hwids[hwid_value] = now

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        owner = await bot.fetch_user(OWNER_ID)

        embed = discord.Embed(title="HWID Submitted", description="Your HWID has been sent to the owner for authentication.\n\nIf the owner (<@1144213765424947251>) is online, this usually takes up to 50 minutes. Otherwise, allow up to 15+ hours.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

        msg_embed = discord.Embed(title="New Authentication Request", color=discord.Color.blurple())
        msg_embed.add_field(name="Type", value="Premium", inline=False)
        msg_embed.add_field(name="User", value=f"{user.mention} ({user.id})", inline=False)
        msg_embed.add_field(name="HWID", value=f"{hwid_value}", inline=False)

        if log_channel:
            await log_channel.send(embed=msg_embed)
        if owner:
            try:
                await owner.send(embed=msg_embed)
            except:
                pass


class AuthButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Get Script", style=discord.ButtonStyle.primary)
    async def get_script(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.user.send("loadstring(game:HttpGet('https://raw.githubusercontent.com/vqmpjayZ/utils/refs/heads/main/CopyHWID.lua'))()")
            await interaction.response.send_message("Script sent to your DMs!", ephemeral=True)
        except:
            await interaction.response.send_message("Failed to DM the script. Check your privacy settings.", ephemeral=True)

    @discord.ui.button(label="Enter HWID", style=discord.ButtonStyle.success)
    async def enter_hwid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HWIDModal())


@bot.tree.command(name="authenticate", description="Authenticate your Premium access.", guild=discord.Object(id=GUILD_ID))
async def authenticate(interaction: discord.Interaction):
    if interaction.channel.id != AUTH_CHANNEL_ID:
        await interaction.response.send_message("You can only use this command in the designated authentication channel.", ephemeral=True)
        return

    embed = discord.Embed(title="Authenticate for Premium.", description=("Authenticate to get access Premium benefits, follow these steps:\n\n1 Run the following script in Roblox to copy your HWID:\n```lua\nloadstring(game:HttpGet('https://raw.githubusercontent.com/vqmpjayZ/utils/refs/heads/main/CopyHWID.lua'))()\n```\n\n2 Click 'Enter HWID' and submit your HWID.\n3 Wait to get authenticated by mods.\n\nIf the owner is online, authentication may take up to 50 minutes. Otherwise, allow up to 15+ hours."), color=discord.Color.blurple())
    view = AuthButtonView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="getkey", description="Generate your unique script key.", guild=discord.Object(id=GUILD_ID))
async def getkey(interaction: discord.Interaction):
    verified_role = interaction.guild.get_role(1270298463078453249)
    if verified_role not in interaction.user.roles:
        await interaction.response.send_message("You need the Verified role to use this command.", ephemeral=True)
        return

    key = create_key_for_user(interaction.user.id, interaction.user.name, DISCORD_KEY_EXPIRY_HOURS)

    if not key:
        await interaction.response.send_message("Key generation failed. Database may be unavailable. Contact the owner.", ephemeral=True)
        return

    expires_timestamp = int(time.time() + (DISCORD_KEY_EXPIRY_HOURS * 3600))

    embed = discord.Embed(title="\U0001f511 Your Script Key", color=discord.Color.green())
    embed.description = f"```{key}```"
    embed.add_field(name="Expires", value=f"<t:{expires_timestamp}:R>", inline=True)
    embed.add_field(name="Tied To", value=f"<@{interaction.user.id}>", inline=True)
    embed.add_field(name="HWID Lock", value="Locks on first use", inline=True)
    embed.set_footer(text="Leave the server = key dies. Do not share.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="resetkey", description="Reset your key and HWID lock.", guild=discord.Object(id=GUILD_ID))
async def resetkey(interaction: discord.Interaction):
    verified_role = interaction.guild.get_role(1270298463078453249)
    if verified_role not in interaction.user.roles:
        await interaction.response.send_message("You need the Verified role to use this command.", ephemeral=True)
        return

    count = delete_keys_by_discord_id(interaction.user.id)
    if count > 0:
        await interaction.response.send_message("\u267b\ufe0f Your old key has been wiped. Use `/getkey` to generate a fresh one.", ephemeral=True)
    else:
        await interaction.response.send_message("You don't have any active keys. Use `/getkey` to generate one.", ephemeral=True)


@bot.tree.command(name="revokekey", description="[Owner] Revoke a user's key.", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(user="The user whose key to revoke")
async def revokekey(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Only the owner can use this command.", ephemeral=True)
        return

    count = delete_keys_by_discord_id(user.id)
    if count > 0:
        await interaction.response.send_message(f"\U0001f5d1\ufe0f Revoked {count} key(s) for {user.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} has no active keys.", ephemeral=True)


@bot.tree.command(name="keystats", description="[Owner] View key system stats.", guild=discord.Object(id=GUILD_ID))
async def keystats(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Only the owner can use this command.", ephemeral=True)
        return

    stats = get_stats()

    embed = discord.Embed(title="\U0001f4ca Key System Stats", color=discord.Color.blurple())
    embed.add_field(name="Total Keys", value=str(stats["total"]), inline=True)
    embed.add_field(name="Active", value=str(stats["active"]), inline=True)
    embed.add_field(name="Expired", value=str(stats["expired"]), inline=True)
    embed.add_field(name="HWID Locked", value=str(stats["hwid_locked"]), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


BOOST_TYPES = {discord.MessageType.premium_guild_subscription}


class KeyClaimView(discord.ui.View):
    def __init__(self, session_token, gateway_url, guild_id, profile_id):
        super().__init__(timeout=1800)
        self.session_token = session_token
        self.gateway_url = gateway_url
        self.guild_id = guild_id
        self.profile_id = profile_id

        self.add_item(discord.ui.Button(
            label="🔗 Open Verification",
            style=discord.ButtonStyle.link,
            url=gateway_url
        ))

    @discord.ui.button(label="✅ Claim Key", style=discord.ButtonStyle.success, custom_id="claim_key")
    async def claim_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(self.session_token)

        if not session:
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.color = discord.Color.red()
            embed.clear_fields()
            embed.title = "❌ Session Expired"
            embed.description = "This session has expired. Run `/ks getkey` again."
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.custom_id:
                    item.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)
            return

        if str(interaction.user.id) != session.get('discord_id'):
            await interaction.response.send_message("❌ This isn't your session.", ephemeral=True)
            return

        if not session.get('completed'):
            await interaction.response.send_message(
                "⏳ You haven't completed verification yet.\n"
                "Click **Open Verification**, complete the task, then try again.",
                ephemeral=True)
            return

        if session.get('key_claimed'):
            await interaction.response.send_message("⚠️ Key already claimed for this session.", ephemeral=True)
            return

        profile = get_script_profile(self.profile_id)
        duration = profile.get('key_duration_hours', 24) if profile else 24

        key = create_guild_key(
            self.guild_id,
            interaction.user.id,
            interaction.user.name,
            duration,
            self.profile_id
        )

        if not key:
            await interaction.response.send_message("❌ Failed to generate key. Try again or contact an admin.", ephemeral=True)
            return

        update_session(self.session_token, {"key_claimed": True})

        expires_ts = int(time.time() + (duration * 3600))

        embed = discord.Embed(title="🔑 Your Key", color=discord.Color.green())
        embed.description = f"```{key}```"
        embed.add_field(name="Script", value=profile.get('name', 'Unknown') if profile else 'Unknown', inline=True)
        embed.add_field(name="Expires", value=f"<t:{expires_ts}:R>", inline=True)
        embed.add_field(name="HWID Lock", value="Locks on first use", inline=True)
        embed.set_footer(text="Do not share your key. Leave the server = key revoked.")

        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id:
                item.disabled = True
                if item.custom_id == "claim_key":
                    item.label = "✅ Key Claimed"

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📊 Check Status", style=discord.ButtonStyle.secondary, custom_id="check_status")
    async def check_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = get_session(self.session_token)

        if not session:
            await interaction.response.send_message("❌ Session expired. Run `/ks getkey` again.", ephemeral=True)
            return

        if str(interaction.user.id) != session.get('discord_id'):
            await interaction.response.send_message("❌ This isn't your session.", ephemeral=True)
            return

        if session.get('key_claimed'):
            status = "✅ Key already claimed"
        elif session.get('completed'):
            status = "✅ Verification complete — click **Claim Key**!"
        elif session.get('timer_started'):
            status = "⏳ Timer started — complete the verification task"
        else:
            status = "🔗 Click **Open Verification** to start"

        await interaction.response.send_message(status, ephemeral=True)


class ProfileSelectForKey(discord.ui.Select):
    def __init__(self, profiles, guild_id):
        self.guild_id = guild_id
        self.profiles_map = {}
        options = []
        for p in profiles:
            pid = p['profile_id']
            self.profiles_map[pid] = p
            type_label = "🔗 Ad-Link" if p['key_type'] == 'adlink' else "💬 Discord"
            options.append(discord.SelectOption(
                label=p['name'],
                value=pid,
                description=f"{type_label} | {p.get('key_duration_hours', 24)}h keys"
            ))
        super().__init__(placeholder="Select a script...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        profile_id = self.values[0]
        profile = self.profiles_map.get(profile_id)

        if not profile or not profile.get('enabled'):
            await interaction.response.send_message("❌ This profile is disabled.", ephemeral=True)
            return

        if profile.get('required_role_id'):
            role = interaction.guild.get_role(int(profile['required_role_id']))
            if role and role not in interaction.user.roles:
                await interaction.response.send_message(
                    f"❌ You need the {role.mention} role to get a key for **{profile['name']}**.", ephemeral=True)
                return

        # Acknowledge immediately; key generation does a DB write.
        await interaction.response.defer(ephemeral=True)

        if profile['key_type'] == 'discord':
            duration = profile.get('key_duration_hours', 24)

            key = create_guild_key(
                self.guild_id,
                interaction.user.id,
                interaction.user.name,
                duration,
                profile_id
            )

            if not key:
                await interaction.followup.send("❌ Failed to generate key.", ephemeral=True)
                return

            expires_ts = int(time.time() + (duration * 3600))

            embed = discord.Embed(title="🔑 Your Key", color=discord.Color.green())
            embed.description = f"```{key}```"
            embed.add_field(name="Script", value=profile['name'], inline=True)
            embed.add_field(name="Expires", value=f"<t:{expires_ts}:R>", inline=True)
            embed.add_field(name="HWID Lock", value="Locks on first use", inline=True)
            embed.set_footer(text="Do not share your key. Leave the server = key revoked.")

            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=None)

        elif profile['key_type'] == 'adlink':
            has_providers = any([
                profile.get('workink_url'),
                profile.get('lootlabs_url'),
                profile.get('linkvertise_url')
            ])
            if not has_providers:
                await interaction.response.send_message(
                    "❌ No verification links configured for this script. Ask an admin.", ephemeral=True)
                return

            pending = get_pending_session(interaction.user.id, self.guild_id, profile_id)
            if pending:
                gateway_url = f"{SERVER_BASE_URL}/ks/gateway/{pending['token']}"
                view = KeyClaimView(pending['token'], gateway_url, str(self.guild_id), profile_id)

                embed = discord.Embed(
                    title="🔑 Verification Already Complete!",
                    description=f"You already completed verification for **{profile['name']}**. Click **Claim Key** below.",
                    color=discord.Color.green()
                )
                await interaction.followup.edit_message(interaction.message.id, embed=embed, view=view)
                return

            token = create_session(
                self.guild_id,
                interaction.user.id,
                interaction.user.name,
                profile_id
            )

            if not token:
                await interaction.followup.send("❌ Failed to create session. Try again later.", ephemeral=True)
                return

            gateway_url = f"{SERVER_BASE_URL}/ks/gateway/{token}"
            view = KeyClaimView(token, gateway_url, str(self.guild_id), profile_id)

            embed = discord.Embed(title="🔑 Key Verification", color=discord.Color.blurple())
            embed.description = (
                f"**Getting key for: {profile['name']}**\n\n"
                "1️⃣ Click **Open Verification** below\n"
                "2️⃣ Choose a provider and complete the task\n"
                "3️⃣ Come back here and click **Claim Key**\n\n"
                "⏱️ Session expires in **30 minutes**"
            )
            embed.set_footer(text="Do not share verification links.")

            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=view)


class ProfileSelectView(discord.ui.View):
    def __init__(self, profiles, guild_id):
        super().__init__(timeout=120)
        self.add_item(ProfileSelectForKey(profiles, guild_id))


class SetupWizardModal(discord.ui.Modal):
    """One-shot configuration: collects the whole first script profile in one modal."""

    def __init__(self):
        super().__init__(title="Key System Setup", timeout=300)

        self.script_name = discord.ui.TextInput(
            label="Script Name",
            style=discord.TextStyle.short,
            placeholder="e.g. My ESP Script",
            min_length=2,
            max_length=50,
            required=True,
        )
        self.key_type = discord.ui.TextInput(
            label="Key Type",
            style=discord.TextStyle.short,
            placeholder="discord = instant key  |  adlink = link gate (work.ink etc.)",
            default="discord",
            min_length=4,
            max_length=7,
            required=True,
        )
        self.duration = discord.ui.TextInput(
            label="Key Duration (hours, 0 = never)",
            style=discord.TextStyle.short,
            placeholder="24",
            default="24",
            max_length=5,
            required=True,
        )
        self.required_role = discord.ui.TextInput(
            label="Required Role ID (optional)",
            style=discord.TextStyle.short,
            placeholder="Users need this role to /ks getkey. Blank = anyone.",
            required=False,
            max_length=30,
        )

        for item in (self.script_name, self.key_type, self.duration, self.required_role):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.script_name.value.strip()
        key_type_raw = self.key_type.value.strip().lower()
        if key_type_raw not in ("discord", "adlink"):
            await interaction.response.send_message(
                "❌ Key type must be `discord` or `adlink`. Run `/ks setup` again.",
                ephemeral=True,
            )
            return

        try:
            duration = int(float(self.duration.value.strip()))
        except ValueError:
            await interaction.response.send_message(
                "❌ Duration must be a number of hours.", ephemeral=True)
            return
        if duration < 0 or duration > 8760:
            await interaction.response.send_message(
                "❌ Duration must be between 1 and 8760 hours.", ephemeral=True)
            return

        role_id = None
        role = None
        role_raw = self.required_role.value.strip()
        if role_raw:
            digits = ''.join(ch for ch in role_raw if ch.isdigit())
            if not digits:
                await interaction.response.send_message(
                    "❌ Required Role must be a role ID (right-click role → Copy ID).",
                    ephemeral=True)
                return
            role = interaction.guild.get_role(int(digits))
            if not role:
                await interaction.response.send_message(
                    "❌ I couldn't find that role in this server. Check the ID and that "
                    "the bot has access to it.", ephemeral=True)
                return
            role_id = role.id

        # Acknowledge before DB writes to stay within Discord's 3s window.
        await interaction.response.defer(ephemeral=True)

        if get_profile_by_name(interaction.guild.id, name):
            await interaction.followup.send(
                f"❌ A script named **{name}** already exists.", ephemeral=True)
            return

        config = init_guild_config(
            interaction.guild.id, interaction.guild.name, interaction.user.id)
        if not config:
            await interaction.followup.send(
                "❌ Failed to initialize. Database may be unavailable.", ephemeral=True)
            return

        profiles = get_script_profiles(interaction.guild.id)
        if len(profiles) >= 10:
            await interaction.followup.send(
                "❌ Maximum 10 script profiles per server.", ephemeral=True)
            return

        profile = create_script_profile(
            interaction.guild.id, name, key_type_raw, duration, role_id)
        if not profile:
            await interaction.followup.send(
                "❌ Failed to create profile.", ephemeral=True)
            return

        embed = _build_setup_embed(interaction.guild, profile, role)
        await interaction.followup.send(embed=embed, ephemeral=True)


def _build_setup_embed(guild, profile, role=None):
    """Pretty post-setup summary with everything a script author needs."""
    is_adlink = profile['key_type'] == 'adlink'
    color = discord.Color.green() if not is_adlink else discord.Color.blurple()
    type_label = "🔗 Ad-Link (monetization gate)" if is_adlink else "💬 Discord (instant key + membership)"

    embed = discord.Embed(
        title="✅ Key System Ready",
        description=(
            f"Your key system for **{guild.name}** is live. Below is everything "
            "you need to wire it into your Roblox script."
        ),
        color=color,
    )

    embed.add_field(
        name="📋 Script",
        value=(
            f"**Name:** {profile['name']}\n"
            f"**Type:** {type_label}\n"
            f"**Key Duration:** {profile.get('key_duration_hours', 24)}h\n"
            f"**Required Role:** {role.mention if role else 'None'}"
        ),
        inline=False,
    )

    secret = profile['api_secret']
    embed.add_field(
        name="🔐 API Secret",
        value=f"||{secret}||",
        inline=True,
    )
    embed.add_field(
        name="🔗 Validation URL",
        value=f"`{SERVER_BASE_URL}/api/validate-guild-key`",
        inline=False,
    )

    lua = (
        "```lua\n"
        "DiscordValidation = {\n"
        "    Enabled = true,\n"
        f"    ValidateURL = '{SERVER_BASE_URL}/api/validate-guild-key',\n"
        f"    APISecret = '{secret}'\n"
        "},\n"
        "```"
    )
    embed.add_field(name="🤖 Roblox Script Config", value=lua, inline=False)

    if is_adlink:
        dest_url = get_destination_url(guild.id, profile['profile_id'])
        embed.add_field(
            name="📎 Ad-Link Destination URL",
            value=(
                f"Set this as your campaign's redirect/landing URL:\n```{dest_url}```\n"
                "Then run `/ks setlink` to add your work.ink / LootLabs / Linkvertise URLs."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="👥 How Users Get Keys",
            value="Users run `/ks getkey` in this server to receive their key instantly.",
            inline=False,
        )

    embed.set_footer(text="Keep the API secret private. Run /ks config anytime, /ks stats for stats.")
    return embed


# ---------------------------------------------------------------------------
# Interactive management panel (the buttons shown by /ks setup)
# ---------------------------------------------------------------------------

class AddScriptModal(discord.ui.Modal):
    """Modal for adding an additional script profile from the panel."""

    def __init__(self):
        super().__init__(title="Add Script", timeout=300)
        self.script_name = discord.ui.TextInput(
            label="Script Name", style=discord.TextStyle.short,
            placeholder="e.g. My ESP Script", min_length=2, max_length=50, required=True)
        self.key_type = discord.ui.TextInput(
            label="Key Type", style=discord.TextStyle.short,
            placeholder="discord = instant  |  adlink = link gate",
            default="discord", min_length=4, max_length=7, required=True)
        self.duration = discord.ui.TextInput(
            label="Key Duration (hours, 0 = never)", style=discord.TextStyle.short,
            default="24", max_length=5, required=True)
        self.required_role = discord.ui.TextInput(
            label="Required Role ID (optional)", style=discord.TextStyle.short,
            placeholder="Blank = anyone can get a key", required=False, max_length=30)
        for item in (self.script_name, self.key_type, self.duration, self.required_role):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.script_name.value.strip()
        key_type_raw = self.key_type.value.strip().lower()
        if key_type_raw not in ("discord", "adlink"):
            await interaction.response.send_message("❌ Key type must be `discord` or `adlink`.", ephemeral=True)
            return
        try:
            duration = int(float(self.duration.value.strip()))
        except ValueError:
            await interaction.response.send_message("❌ Duration must be a number of hours.", ephemeral=True)
            return
        if duration < 0 or duration > 8760:
            await interaction.response.send_message("❌ Duration must be between 1 and 8760 hours.", ephemeral=True)
            return

        role_id = None
        role = None
        role_raw = self.required_role.value.strip()
        if role_raw:
            digits = ''.join(ch for ch in role_raw if ch.isdigit())
            if not digits:
                await interaction.response.send_message("❌ Required Role must be a role ID.", ephemeral=True)
                return
            role = interaction.guild.get_role(int(digits))
            if not role:
                await interaction.response.send_message("❌ I couldn't find that role in this server.", ephemeral=True)
                return
            role_id = role.id

        # Acknowledge immediately so DB work can't trip the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        if get_profile_by_name(interaction.guild.id, name):
            await interaction.followup.send(f"❌ A script named **{name}** already exists.", ephemeral=True)
            return
        profiles = get_script_profiles(interaction.guild.id)
        if len(profiles) >= 10:
            await interaction.followup.send("❌ Maximum 10 script profiles per server.", ephemeral=True)
            return

        profile = create_script_profile(interaction.guild.id, name, key_type_raw, duration, role_id)
        if not profile:
            await interaction.followup.send("❌ Failed to create profile.", ephemeral=True)
            return

        embed = _build_setup_embed(interaction.guild, profile, role)
        await interaction.followup.send(embed=embed, ephemeral=True)


class RemoveScriptSelect(discord.ui.Select):
    def __init__(self, parent_view, profiles):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=p['name'][:100], value=p['profile_id'],
                                 description=f"{p['key_type']} · {p.get('key_duration_hours', 24)}h")
            for p in profiles
        ]
        super().__init__(placeholder="Choose a script to remove…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        profile_id = self.values[0]
        profile = get_script_profile(profile_id)
        name = profile['name'] if profile else profile_id
        delete_script_profile(profile_id)
        self.parent_view.reset_main()
        embed = build_dashboard_embed(interaction.guild)
        await interaction.followup.edit_message(
            interaction.message.id,
            content=f"🗑️ **{name}** and all its keys have been deleted.",
            embed=embed, view=self.parent_view)


class SetLinkScriptSelect(discord.ui.Select):
    def __init__(self, parent_view, profiles):
        self.parent_view = parent_view
        adlink = [p for p in profiles if p['key_type'] == 'adlink']
        options = [
            discord.SelectOption(label=p['name'][:100], value=p['profile_id'],
                                 description="Set work.ink / LootLabs / Linkvertise URLs")
            for p in adlink
        ]
        super().__init__(placeholder="Choose an ad-link script…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        profile = get_script_profile(self.values[0])
        if not profile:
            await interaction.response.send_message("❌ Profile not found.", ephemeral=True)
            return
        await interaction.response.send_modal(SetupLinksModal(profile))


class ManagementView(discord.ui.View):
    """Interactive admin panel driven by /ks setup."""

    def __init__(self):
        super().__init__(timeout=300)
        self._build_main()

    def reset_main(self):
        self.clear_items()
        self._build_main()

    def _build_main(self):
        self.add_item(self.AddScriptButton())
        self.add_item(self.RemoveScriptButton())
        self.add_item(self.SetLinksButton())
        self.add_item(self.MembershipButton())
        self.add_item(self.ToggleSystemButton())
        self.add_item(self.ViewConfigButton())

    class AddScriptButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Add Script", style=discord.ButtonStyle.success, emoji="➕", row=0)

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            profiles = get_script_profiles(interaction.guild.id)
            if len(profiles) >= 10:
                await interaction.response.send_message("❌ Maximum 10 script profiles per server.", ephemeral=True)
                return
            await interaction.response.send_modal(AddScriptModal())

    class RemoveScriptButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Remove Script", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            profiles = get_script_profiles(interaction.guild.id)
            if not profiles:
                await interaction.response.send_message("❌ No scripts to remove. Add one first.", ephemeral=True)
                return
            self.view.clear_items()
            self.view.add_item(RemoveScriptSelect(self.view, profiles))
            self.view.add_item(BackButton())
            await interaction.response.edit_message(
                content="Pick a script to delete:", embed=None, view=self.view)

    class SetLinksButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Set Links", style=discord.ButtonStyle.primary, emoji="🔗", row=0)

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            profiles = get_script_profiles(interaction.guild.id)
            adlink = [p for p in profiles if p['key_type'] == 'adlink']
            if not adlink:
                await interaction.response.send_message(
                    "❌ No ad-link scripts. Add one with type `adlink` to set monetization URLs.", ephemeral=True)
                return
            self.view.clear_items()
            self.view.add_item(SetLinkScriptSelect(self.view, adlink))
            self.view.add_item(BackButton())
            await interaction.response.edit_message(
                content="Pick an ad-link script:", embed=None, view=self.view)

    class MembershipButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Server Lock", style=discord.ButtonStyle.secondary, emoji="🔒", row=1)

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            profiles = get_script_profiles(interaction.guild.id)
            if not profiles:
                await interaction.response.send_message("❌ Add a script first.", ephemeral=True)
                return
            self.view.clear_items()
            self.view.add_item(MembershipSelect(self.view, profiles))
            self.view.add_item(BackButton())
            await interaction.response.edit_message(
                content=(
                    "__**Server Lock**__\n"
                    "**ON (default):** a key stops working the moment the user leaves this Discord server. "
                    "Use this when keys are a perk of being in your server.\n"
                    "**OFF:** keys keep working even after someone leaves."
                ),
                embed=None, view=self.view)

    class ToggleSystemButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Enable/Disable", style=discord.ButtonStyle.secondary, emoji="⏯️", row=1)

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            config = get_guild_config(interaction.guild.id)
            current = bool(config and config.get('enabled'))
            save_guild_config(interaction.guild.id, {"enabled": not current, "updated_at": time.time()})
            embed = build_dashboard_embed(interaction.guild)
            if current:
                msg = ("⏸️ Key system **disabled**. The script will reject every key "
                       "(users see 'key system disabled') until you enable it again.")
            else:
                msg = "▶️ Key system **enabled** — keys work normally."
            await interaction.followup.edit_message(
                interaction.message.id, content=msg, embed=embed, view=self.view)

    class ViewConfigButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="View Config", style=discord.ButtonStyle.secondary, emoji="📋", row=1)

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Admins only.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            profiles = get_script_profiles(interaction.guild.id)
            embed = discord.Embed(
                title="📋 Full Configuration",
                color=discord.Color.blurple(),
            )
            if not profiles:
                embed.description = "No scripts yet."
            for p in profiles:
                type_label = "🔗 Ad-Link" if p['key_type'] == 'adlink' else "💬 Discord"
                req_role = f"<@&{p['required_role_id']}>" if p.get('required_role_id') else "None"
                membership = "Required" if p.get('require_membership', True) else "Not required"
                links = []
                if p.get('workink_url'): links.append("Work.ink")
                if p.get('lootlabs_url'): links.append("LootLabs")
                if p.get('linkvertise_url'): links.append("Linkvertise")
                links_str = ", ".join(links) if links else "None"
                embed.add_field(
                    name=f"{type_label} — {p['name']}",
                    value=(
                        f"Duration: `{p.get('key_duration_hours', 24)}h`\n"
                        f"Required role: {req_role}\n"
                        f"Server lock: {membership}\n"
                        f"Ad links: {links_str}\n"
                        f"API secret: ||{p['api_secret'][:18]}...||"
                    ),
                    inline=False,
                )
            self.view.clear_items()
            self.view.add_item(BackButton())
            await interaction.followup.edit_message(
                interaction.message.id, content=None, embed=embed, view=self.view)


class BackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=2)

    async def callback(self, interaction: discord.Interaction):
        self.view.reset_main()
        embed = build_dashboard_embed(interaction.guild)
        await interaction.response.edit_message(content=None, embed=embed, view=self.view)


class MembershipSelect(discord.ui.Select):
    def __init__(self, parent_view, profiles):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label=p['name'][:100], value=p['profile_id'],
                description=f"Membership: {'ON' if p.get('require_membership', True) else 'OFF'}")
            for p in profiles
        ]
        super().__init__(placeholder="Choose a script to toggle membership…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        profile = get_script_profile(self.values[0])
        if not profile:
            await interaction.followup.send("❌ Profile not found.", ephemeral=True)
            return
        current = profile.get('require_membership', True)
        update_script_profile(profile['profile_id'], {"require_membership": not current})
        self.parent_view.reset_main()
        embed = build_dashboard_embed(interaction.guild)
        await interaction.followup.edit_message(
            interaction.message.id,
            content=f"{'🔒' if not current else '🔓'} Server lock **{'enabled' if not current else 'disabled'}** for **{profile['name']}**.",
            embed=embed, view=self.parent_view)



def build_dashboard_embed(guild):
    """The management panel embed shown by /ks setup when already configured."""
    config = get_guild_config(guild.id)
    profiles = get_script_profiles(guild.id)

    embed = discord.Embed(
        title="⚙️ Key System Dashboard",
        description=f"Managing **{guild.name}** — use the buttons below to add, remove, or configure scripts.",
        color=discord.Color.blurple(),
    )

    status = "✅ Enabled" if (config and config.get('enabled')) else "❌ Disabled"
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Scripts", value=str(len(profiles)), inline=True)

    if profiles:
        lines = []
        for p in profiles:
            # 🔗 = Ad-Link (user completes a work.ink/LootLabs link for a key)
            # 💬 = Discord (key issued instantly in the server)
            type_emoji = "🔗" if p['key_type'] == 'adlink' else "💬"
            type_word = "link-gate" if p['key_type'] == 'adlink' else "instant"
            status_emoji = "✅" if p.get('enabled') else "❌"
            dur = p.get('key_duration_hours', 24)
            dur_word = "never expires" if dur == 0 else f"{dur}h"
            req_role = f"<@&{p['required_role_id']}>" if p.get('required_role_id') else "anyone"
            lock = "server-locked" if p.get('require_membership', True) else "no lock"
            lines.append(
                f"{type_emoji} **{p['name']}** {status_emoji}\n"
                f"     `{type_word}` · ⏳ {dur_word} · 🔒 {lock} · role: {req_role}"
            )
        embed.add_field(name="Script Profiles", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="No scripts yet",
            value="Click **Add Script** below to create your first profile.",
            inline=False,
        )

    embed.set_footer(
        text="Users get keys with /ks getkey · /ks stats for stats · /antispam for scam protection"
    )
    return embed


class SetupLinksModal(discord.ui.Modal, title="Set Monetization Links"):
    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.workink_input = discord.ui.TextInput(
            label="Work.ink URL",
            style=discord.TextStyle.short,
            placeholder="https://work.ink/...",
            required=False,
            default=profile.get('workink_url', '') or ''
        )
        self.lootlabs_input = discord.ui.TextInput(
            label="LootLabs URL",
            style=discord.TextStyle.short,
            placeholder="https://lootlabs.gg/...",
            required=False,
            default=profile.get('lootlabs_url', '') or ''
        )
        self.linkvertise_input = discord.ui.TextInput(
            label="Linkvertise URL",
            style=discord.TextStyle.short,
            placeholder="https://linkvertise.com/...",
            required=False,
            default=profile.get('linkvertise_url', '') or ''
        )
        self.add_item(self.workink_input)
        self.add_item(self.lootlabs_input)
        self.add_item(self.linkvertise_input)

    async def on_submit(self, interaction: discord.Interaction):
        updates = {}
        set_links = []

        wi = self.workink_input.value.strip()
        ll = self.lootlabs_input.value.strip()
        lv = self.linkvertise_input.value.strip()

        if wi:
            if not wi.startswith('http'):
                await interaction.response.send_message("❌ Work.ink URL must start with http.", ephemeral=True)
                return
            updates['workink_url'] = wi
            set_links.append(f"⚡ Work.ink")
        else:
            updates['workink_url'] = ''

        if ll:
            if not ll.startswith('http'):
                await interaction.response.send_message("❌ LootLabs URL must start with http.", ephemeral=True)
                return
            updates['lootlabs_url'] = ll
            set_links.append(f"🎁 LootLabs")
        else:
            updates['lootlabs_url'] = ''

        if lv:
            if not lv.startswith('http'):
                await interaction.response.send_message("❌ Linkvertise URL must start with http.", ephemeral=True)
                return
            updates['linkvertise_url'] = lv
            set_links.append(f"🔗 Linkvertise")
        else:
            updates['linkvertise_url'] = ''

        update_script_profile(self.profile['profile_id'], updates)

        result = "Set: " + ", ".join(set_links) if set_links else "All links cleared"
        await interaction.response.send_message(f"✅ Links updated for **{self.profile['name']}**. {result}", ephemeral=True)


ks_group = app_commands.Group(name="ks", description="Key System commands")


@ks_group.command(name="setup", description="[Admin] Open the key system management panel.")
@app_commands.checks.has_permissions(administrator=True)
async def ks_setup(interaction: discord.Interaction):
    config = get_guild_config(interaction.guild.id)

    # First-time setup: launch the one-shot wizard modal.
    if not (config and config.get('enabled')):
        await interaction.response.send_modal(SetupWizardModal())
        return

    # Already set up: show the interactive management panel.
    embed = build_dashboard_embed(interaction.guild)
    await interaction.response.send_message(embed=embed, view=ManagementView(), ephemeral=True)


@ks_group.command(name="getkey", description="Get a script key.")
async def ks_getkey(interaction: discord.Interaction):
    config = get_guild_config(interaction.guild.id)
    if not config or not config.get('enabled'):
        await interaction.followup.send("❌ Key system is not set up for this server.", ephemeral=True)
        return

    profiles = get_script_profiles(interaction.guild.id)
    active_profiles = [p for p in profiles if p.get('enabled')]

    if not active_profiles:
        await interaction.followup.send("❌ No script profiles available.", ephemeral=True)
        return

    if len(active_profiles) == 1:
        profile = active_profiles[0]

        if profile.get('required_role_id'):
            role = interaction.guild.get_role(int(profile['required_role_id']))
            if role and role not in interaction.user.roles:
                await interaction.followup.send(
                    f"❌ You need the {role.mention} role to get a key for **{profile['name']}**.", ephemeral=True)
                return

        # Acknowledge before DB writes so /ks getkey never trips the 3s timeout.
        await interaction.response.defer(ephemeral=True)

        if profile['key_type'] == 'discord':
            duration = profile.get('key_duration_hours', 24)

            key = create_guild_key(
                interaction.guild.id,
                interaction.user.id,
                interaction.user.name,
                duration,
                profile['profile_id']
            )

            if not key:
                await interaction.followup.send("❌ Failed to generate key.", ephemeral=True)
                return

            expires_ts = int(time.time() + (duration * 3600))

            embed = discord.Embed(title="🔑 Your Key", color=discord.Color.green())
            embed.description = f"```{key}```"
            embed.add_field(name="Script", value=profile['name'], inline=True)
            embed.add_field(name="Expires", value=f"<t:{expires_ts}:R>", inline=True)
            embed.add_field(name="HWID Lock", value="Locks on first use", inline=True)
            embed.set_footer(text="Do not share your key. Leave the server = key revoked.")

            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        elif profile['key_type'] == 'adlink':
            has_providers = any([
                profile.get('workink_url'),
                profile.get('lootlabs_url'),
                profile.get('linkvertise_url')
            ])
            if not has_providers:
                await interaction.followup.send(
                    "❌ No verification links configured. Ask an admin.", ephemeral=True)
                return

            pending = get_pending_session(interaction.user.id, interaction.guild.id, profile['profile_id'])
            if pending:
                gateway_url = f"{SERVER_BASE_URL}/ks/gateway/{pending['token']}"
                view = KeyClaimView(pending['token'], gateway_url, str(interaction.guild.id), profile['profile_id'])

                embed = discord.Embed(
                    title="🔑 Verification Already Complete!",
                    description=f"Click **Claim Key** to get your key for **{profile['name']}**.",
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                return

            token = create_session(
                interaction.guild.id,
                interaction.user.id,
                interaction.user.name,
                profile['profile_id']
            )

            if not token:
                await interaction.followup.send("❌ Failed to create session.", ephemeral=True)
                return

            gateway_url = f"{SERVER_BASE_URL}/ks/gateway/{token}"
            view = KeyClaimView(token, gateway_url, str(interaction.guild.id), profile['profile_id'])

            embed = discord.Embed(title="🔑 Key Verification", color=discord.Color.blurple())
            embed.description = (
                f"**Getting key for: {profile['name']}**\n\n"
                "1️⃣ Click **Open Verification** below\n"
                "2️⃣ Choose a provider and complete the task\n"
                "3️⃣ Come back here and click **Claim Key**\n\n"
                "⏱️ Session expires in **30 minutes**"
            )
            embed.set_footer(text="Do not share verification links.")

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return

    view = ProfileSelectView(active_profiles, str(interaction.guild.id))
    embed = discord.Embed(title="🔑 Select a Script", color=discord.Color.blurple())
    embed.description = "Choose which script you need a key for:"
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@ks_group.command(name="resetkey", description="Reset your key and HWID lock for a script.")
@app_commands.describe(script_name="Name of the script (leave empty to reset all)")
async def ks_resetkey(interaction: discord.Interaction, script_name: str = None):
    config = get_guild_config(interaction.guild.id)
    if not config:
        await interaction.response.send_message("❌ Key system not set up.", ephemeral=True)
        return

    if script_name:
        profile = get_profile_by_name(interaction.guild.id, script_name)
        if not profile:
            await interaction.response.send_message(f"❌ No script named **{script_name}** found.", ephemeral=True)
            return
        count = delete_guild_keys_by_user(interaction.guild.id, interaction.user.id, profile['profile_id'])
    else:
        count = delete_guild_keys_by_user(interaction.guild.id, interaction.user.id)

    if count > 0:
        await interaction.response.send_message(
            f"♻️ {count} key(s) wiped. Run `/ks getkey` to get new ones.", ephemeral=True)
    else:
        await interaction.response.send_message("You don't have any active keys. Run `/ks getkey`.", ephemeral=True)


@ks_group.command(name="revokekey", description="[Admin] Revoke a user's key.")
@app_commands.describe(user="User whose key to revoke", script_name="Script name (optional)")
@app_commands.checks.has_permissions(administrator=True)
async def ks_revokekey(interaction: discord.Interaction, user: discord.Member, script_name: str = None):
    config = get_guild_config(interaction.guild.id)
    if not config:
        await interaction.response.send_message("❌ Key system not set up.", ephemeral=True)
        return

    profile_id = None
    if script_name:
        profile = get_profile_by_name(interaction.guild.id, script_name)
        if not profile:
            await interaction.response.send_message(f"❌ No script named **{script_name}** found.", ephemeral=True)
            return
        profile_id = profile['profile_id']

    count = delete_guild_keys_by_user(interaction.guild.id, user.id, profile_id)
    if count > 0:
        await interaction.response.send_message(f"🗑️ Revoked {count} key(s) for {user.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} has no active keys.", ephemeral=True)


@ks_group.command(name="stats", description="[Admin] View key system statistics.")
@app_commands.describe(script_name="Script name (optional)")
@app_commands.checks.has_permissions(administrator=True)
async def ks_stats(interaction: discord.Interaction, script_name: str = None):
    config = get_guild_config(interaction.guild.id)
    if not config:
        await interaction.response.send_message("❌ Key system not set up.", ephemeral=True)
        return

    profile_id = None
    title_suffix = ""
    if script_name:
        profile = get_profile_by_name(interaction.guild.id, script_name)
        if not profile:
            await interaction.response.send_message(f"❌ No script named **{script_name}** found.", ephemeral=True)
            return
        profile_id = profile['profile_id']
        title_suffix = f" — {script_name}"

    stats = get_guild_key_stats(interaction.guild.id, profile_id)

    embed = discord.Embed(
        title=f"📊 Key Stats{title_suffix}",
        description=f"Statistics for **{interaction.guild.name}**",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="🔑 Total Keys", value=str(stats['total']), inline=True)
    embed.add_field(name="✅ Active", value=str(stats['active']), inline=True)
    embed.add_field(name="⌛ Expired", value=str(stats['expired']), inline=True)
    embed.add_field(name="🔒 HWID Locked", value=str(stats['hwid_locked']), inline=True)

    # Per-script breakdown when viewing all scripts.
    if not script_name:
        profiles = get_script_profiles(interaction.guild.id)
        if profiles:
            lines = []
            for p in profiles:
                pstats = get_guild_key_stats(interaction.guild.id, p['profile_id'])
                type_emoji = "🔗" if p['key_type'] == 'adlink' else "💬"
                lines.append(
                    f"{type_emoji} **{p['name']}** — {pstats['active']} active / {pstats['total']} total"
                )
            embed.add_field(name="Per Script", value="\n".join(lines), inline=False)

    # Recent keys list.
    recent = list_recent_keys(interaction.guild.id, profile_id, limit=10)
    if recent:
        rows = []
        for k in recent:
            status = "✅" if k['active'] else "⌛"
            lock = "🔒" if k['hwid_locked'] else "  "
            rows.append(f"{status}{lock} `{k['key_masked']}` — **{k['owner']}** · {k['script']}")
        embed.add_field(name="Recent Keys", value="\n".join(rows), inline=False)

    embed.set_footer(text="Run /ks setup to manage scripts.")
    await interaction.response.send_message(embed=embed, ephemeral=True)



bot.tree.add_command(ks_group)


# ---------------------------------------------------------------------------
# /antispam setup — global anti-scam protection via an interactive panel
# (deletes messages with 4+ links/attachments unless they have real text)
# ---------------------------------------------------------------------------

class AntiSpamChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Pick channels — applies INSTANTLY (empty = all channels)",
            min_values=0,
            max_values=25,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channels = [str(c.id) for c in self.values]
        update_settings(interaction.guild.id, {
            "antispam_enabled": True,
            "antispam_channels": channels,
        })
        view = AntiSpamView()
        if channels:
            msg = ("🛡️ **Applied instantly.** Protection is now ON for the "
                   f"{len(channels)} selected channel(s). Pick again to change.")
        else:
            msg = "🛡️ **Applied instantly.** Protection is now ON for **every channel**."
        await interaction.followup.edit_message(
            interaction.message.id,
            content=msg, embed=build_antispam_embed(interaction.guild), view=view)


class AntiSpamView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AntiSpamChannelSelect())

    @discord.ui.button(label="Enable (all channels)", style=discord.ButtonStyle.success, emoji="🛡️", row=1)
    async def enable_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        update_settings(interaction.guild.id, {
            "antispam_enabled": True, "antispam_channels": []})
        view = AntiSpamView()
        await interaction.followup.edit_message(
            interaction.message.id,
            content="🛡️ Anti-scam **enabled server-wide**.",
            embed=build_antispam_embed(interaction.guild), view=view)

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger, emoji="🛑", row=1)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        update_settings(interaction.guild.id, {"antispam_enabled": False})
        view = AntiSpamView()
        await interaction.followup.edit_message(
            interaction.message.id,
            content="🛑 Anti-scam **disabled**.",
            embed=build_antispam_embed(interaction.guild), view=view)


def build_antispam_embed(guild):
    s = get_settings(guild.id)
    enabled = s.get("antispam_enabled", False)
    channels = s.get("antispam_channels") or []
    if not enabled:
        status = "🛑 **Disabled**"
        scope = "—"
    elif not channels:
        status = "🛡️ **Enabled**"
        scope = "Every channel"
    else:
        status = "🛡️ **Enabled**"
        scope = "\n".join(f"• <#{c}>" for c in channels)
    embed = discord.Embed(title="Anti-Scam Protection", color=discord.Color.blurple())
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Protected channels", value=scope, inline=True)
    embed.add_field(
        name="How to use",
        value=("📌 **Pick channels in the dropdown below — it applies instantly, no "
               "apply button.** Leave it empty to protect every channel. Use the "
               "buttons to enable-all or disable."),
        inline=False,
    )
    embed.add_field(
        name="What it deletes",
        value=("Messages with **4+ links or 4+ attachments** that have no real text "
               "(the fake MrBeast/Elon crypto scam posts). Captions with actual words are kept."),
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# /fun — toggle the bot's little fun features (meow, good boy)
# ---------------------------------------------------------------------------

class FunView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Meow: ON", style=discord.ButtonStyle.success, emoji="🐱", custom_id="fun_meow")
    async def toggle_meow(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        s = get_settings(interaction.guild.id)
        new_val = not s.get("fun_meow", True)
        update_settings(interaction.guild.id, {"fun_meow": new_val})
        button.label = f"Meow: {'ON' if new_val else 'OFF'}"
        button.style = discord.ButtonStyle.success if new_val else discord.ButtonStyle.secondary
        await interaction.followup.edit_message(
            interaction.message.id,
            content=f"🐱 Meow replies {'enabled' if new_val else 'disabled'}.",
            embed=build_fun_embed(interaction.guild), view=self)

    @discord.ui.button(label="Good boy: OFF", style=discord.ButtonStyle.secondary, emoji="🐶", custom_id="fun_goodboy")
    async def toggle_goodboy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        s = get_settings(interaction.guild.id)
        new_val = not s.get("fun_goodboy", False)
        update_settings(interaction.guild.id, {"fun_goodboy": new_val})
        button.label = f"Good boy: {'ON' if new_val else 'OFF'}"
        button.style = discord.ButtonStyle.success if new_val else discord.ButtonStyle.secondary
        await interaction.followup.edit_message(
            interaction.message.id,
            content=f"🐶 Good-boy boosts {'enabled' if new_val else 'disabled'}.",
            embed=build_fun_embed(interaction.guild), view=self)


def build_fun_embed(guild):
    s = get_settings(guild.id)
    meow = s.get("fun_meow", True)
    goodboy = s.get("fun_goodboy", False)
    embed = discord.Embed(title="🎉 Fun Features", color=discord.Color.blurple())
    embed.add_field(
        name=f"🐱 Meow replies — {'ON' if meow else 'OFF'}",
        value=("When someone sends a message that's just \"meow\", the bot replies with a "
               "random number of meows and a cute face. A silly easter egg. (On by default.)"),
        inline=False,
    )
    embed.add_field(
        name=f"🐶 Good boy — {'ON' if goodboy else 'OFF'}",
        value=("After someone boosts the server (or @mentions the bot asking for a good "
               "boy), it waits a moment then says \"good boy\". Off by default — can get "
               "spammy with frequent boosts."),
        inline=False,
    )
    embed.set_footer(text="Use the buttons below to toggle. Settings save instantly.")
    return embed


@bot.tree.command(name="fun", description="Toggle the bot's fun little features.")
@app_commands.checks.has_permissions(administrator=True)
async def fun_setup(interaction: discord.Interaction):
    # Set button labels/styles to match current saved state.
    view = FunView()
    s = get_settings(interaction.guild.id)
    meow = s.get("fun_meow", True)
    goodboy = s.get("fun_goodboy", False)
    view.toggle_meow.label = f"Meow: {'ON' if meow else 'OFF'}"
    view.toggle_meow.style = discord.ButtonStyle.success if meow else discord.ButtonStyle.secondary
    view.toggle_goodboy.label = f"Good boy: {'ON' if goodboy else 'OFF'}"
    view.toggle_goodboy.style = discord.ButtonStyle.success if goodboy else discord.ButtonStyle.secondary
    await interaction.response.send_message(embed=build_fun_embed(interaction.guild), view=view, ephemeral=True)


@bot.tree.command(name="antispam", description="Configure anti-scam link/attachment protection.")
@app_commands.checks.has_permissions(administrator=True)
async def antispam_setup(interaction: discord.Interaction):
    view = AntiSpamView()
    await interaction.response.send_message(
        embed=build_antispam_embed(interaction.guild), view=view, ephemeral=True)


@bot.event
async def on_member_remove(member):
    # Owner-server premium keys (legacy key_store).
    if is_owner_guild(member.guild.id):
        count = delete_keys_by_discord_id(member.id)
        if count > 0:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(title="\U0001f511 Key Auto-Revoked", color=discord.Color.red())
                embed.add_field(name="User", value=f"{member.name} ({member.id})", inline=False)
                embed.add_field(name="Reason", value="Left the server", inline=False)
                embed.add_field(name="Keys Revoked", value=str(count), inline=False)
                await log_channel.send(embed=embed)

    # Multi-tenant /ks keys for this (or any) guild.
    guild_config = get_guild_config(member.guild.id)
    if guild_config:
        guild_count = delete_guild_keys_by_user(member.guild.id, member.id)
        if guild_count > 0:
            # Log to the guild owner's DMs by default (we have no guaranteed
            # channel in a customer's server). Never leak this into the
            # owner-server's personal log channel.
            try:
                guild_owner = member.guild.owner
                if guild_owner:
                    embed = discord.Embed(title="🔑 Guild Key Auto-Revoked", color=discord.Color.orange())
                    embed.add_field(name="User", value=f"{member.name} ({member.id})", inline=False)
                    embed.add_field(name="Guild", value=f"{member.guild.name} ({member.guild.id})", inline=False)
                    embed.add_field(name="Keys Revoked", value=str(guild_count), inline=False)
                    await guild_owner.send(embed=embed)
            except Exception as e:
                logger.warning(f"Could not DM guild owner about auto-revoke: {e}")


@bot.event
async def on_message(message):
    global last_meow_count

    if message.author == bot.user:
        return

    # DMs / system messages: only process commands, ignore fun logic.
    if message.guild is None:
        await bot.process_commands(message)
        return

    if message.author.bot:
        await bot.process_commands(message)
        return

    # Anti-scam: delete messages flooded with links/attachments (the fake
    # MrBeast/Elon crypto scams). Runs in:
    #   - the owner's always-on MONITORED_CHANNELS, and
    #   - any channel where an admin enabled protection via /antispam.
    # If a message has attachments/links BUT also real text (not just emojis),
    # it's almost certainly legit — leave it alone. Scam posts are typically
    # attachments with no caption or just emoji gibberish.
    owner_protected = is_owner_guild(message.guild.id) and message.channel.id in MONITORED_CHANNELS
    if owner_protected or antispam_active(message.guild.id, message.channel.id):
        link_count = len(URL_PATTERN.findall(message.content or ""))
        attachment_count = len(message.attachments)

        is_flood = link_count >= 4 or attachment_count >= 4
        if is_flood:
            # Strip emojis + common decorative chars to see if there's actual
            # meaningful text. Discord custom emoji look like <:name:id>.
            text = message.content or ""
            text_without_custom = re.sub(r'<a?:\w+:\d+>', '', text)
            # Remove standard unicode emoji and pictographic/symbol ranges.
            emoji_pattern = re.compile(
                "["
                "\U0001F1E0-\U0001F1FF"  # flags
                "\U0001F300-\U0001F5FF"  # symbols & pictographs
                "\U0001F600-\U0001F64F"  # emoticons
                "\U0001F680-\U0001F6FF"  # transport & map
                "\U0001F700-\U0001F77F"
                "\U0001F900-\U0001F9FF"
                "\U00002600-\U000027BF"  # misc symbols/dingbats
                "\U0001FA70-\U0001FAFF"
                "\ufe0f"
                "]+", flags=re.UNICODE)
            stripped = emoji_pattern.sub('', text_without_custom)
            # Also drop URLs, whitespace, and punctuation; what's left is words.
            stripped = URL_PATTERN.sub('', stripped)
            stripped = re.sub(r'[\s\W_]+', '', stripped, flags=re.UNICODE)
            has_real_text = len(stripped) >= 3  # at least 3 word-chars

            if has_real_text:
                logger.info(
                    f"Keeping message {message.id} (has real caption text; "
                    f"links={link_count}, attachments={attachment_count})"
                )
            else:
                perms = message.channel.permissions_for(message.guild.me)
                if not perms.manage_messages:
                    logger.warning(
                        f"Missing Manage Messages permission in channel {message.channel.id}, "
                        f"could not delete message {message.id} from {message.author} "
                        f"(links={link_count}, attachments={attachment_count})"
                    )
                else:
                    try:
                        await message.delete()
                        logger.info(
                            f"Deleted scam-spam message {message.id} from {message.author} in "
                            f"channel {message.channel.id} (links={link_count}, attachments={attachment_count})"
                        )
                    except discord.Forbidden:
                        logger.warning(f"Forbidden: could not delete message {message.id}")
                    except discord.NotFound:
                        pass
                return

    content = message.content or ""
    fun = get_settings(message.guild.id)

    # "meow" easter egg — on by default, toggle with /fun.
    if fun.get("fun_meow", True):
        cleaned_content = re.sub(r'<@!?\d+>', '', content).strip()
        words = re.findall(r'\b\w+[!?.]*\b', cleaned_content)

        all_meows = all(re.match(r'meow[!?.]*$', word, re.IGNORECASE) for word in words) if words else False

        if all_meows and words:
            meow_weights = [5, 4, 3, 2, 1, 1]
            possible_counts = list(range(2, 8))

            if last_meow_count in possible_counts:
                last_index = possible_counts.index(last_meow_count)
                weights = meow_weights[:]
                weights[last_index] = 0
            else:
                weights = meow_weights

            meow_count = random.choices(possible_counts, weights=weights)[0]
            last_meow_count = meow_count
            punctuation = random.choice(["", "!", "!!", "."])
            symbol_chance = random.randint(1, 3)
            symbol = random.choice(cute_symbols) if symbol_chance == 1 else ""

            await message.reply(("meow " * meow_count).strip() + punctuation + (" " + symbol if symbol else ""), mention_author=False)

    # "good boy" — OFF by default, toggle with /fun. Fires on real Discord
    # boost messages, and as a little joke when someone @mentions the bot and
    # asks for a good boy.
    if fun.get("fun_goodboy", False):
        is_system_boost = message.type in BOOST_TYPES
        content_lower = content.lower()
        is_beg = ("good boy" in content_lower or "goodboy" in content_lower) and bot.user in message.mentions

        if is_system_boost or is_beg:
            if message.author:
                user_id = message.author.id

                if user_id not in recent_boosts:
                    recent_boosts[user_id] = True

                    if user_id in pending_tasks:
                        try:
                            pending_tasks[user_id].cancel()
                        except Exception:
                            pass

                    pending_tasks[user_id] = bot.loop.create_task(
                        send_good_boy_after_delay(user_id, message.channel))

    await bot.process_commands(message)


@bot.event
async def on_ready():
    print(f'Main bot logged in as {bot.user}')

    expired = cleanup_expired()
    if expired > 0:
        print(f"Cleaned up {expired} expired premium keys")

    guild_expired = cleanup_expired_guild_keys()
    if guild_expired > 0:
        print(f"Cleaned up {guild_expired} expired guild keys")

    try:
        await asyncio.sleep(5)

        # --- Instant command cleanup -------------------------------------
        # tree.sync() only upserts; it never removes commands that were
        # deleted or renamed in code, so stale/old commands linger for up to
        # an hour. We diff what's currently registered against what the bot
        # actually defines and delete the extras immediately.
        #
        # A command is "global" when it has no guild_ids binding. The /ks
        # group is global; the legacy premium commands are bound to the owner
        # guild and must NOT be counted as global.
        def _is_global(cmd):
            gids = getattr(cmd, "guild_ids", None)
            return not gids  # empty/None -> global

        desired_global = {c.name for c in bot.tree.get_commands(guild=None) if _is_global(c)}
        current_global = await bot.http.get_global_commands(bot.user.id)
        stale_global = [c["id"] for c in current_global if c["name"] not in desired_global]
        for cmd_id in stale_global:
            await bot.http.delete_global_command(bot.user.id, cmd_id)
            print(f"Deleted stale global command {cmd_id}")

        # Owner guild: ONLY the legacy premium commands are bound here
        # (authenticate/getkey/resetkey/revokekey/keystats). The /ks group is
        # GLOBAL and must NOT be copied to this guild — doing so made every /ks
        # subcommand show up twice (once global, once guild). Remove any guild
        # command whose name isn't one of the legacy commands.
        owner_guild = discord.Object(id=OWNER_GUILD_ID)
        legacy_names = {
            c.name for c in bot.tree.get_commands(guild=owner_guild)
            if getattr(c, "guild_ids", None) and OWNER_GUILD_ID in c.guild_ids
        }
        current_owner = await bot.http.get_guild_commands(bot.user.id, OWNER_GUILD_ID)
        # Delete everything that isn't a legacy command (this wipes the old
        # duplicate /ks guild copies instantly).
        stale_owner = [c["id"] for c in current_owner if c["name"] not in legacy_names]
        for cmd_id in stale_owner:
            await bot.http.delete_guild_command(bot.user.id, OWNER_GUILD_ID, cmd_id)
            print(f"Deleted stale/duplicate owner-guild command {cmd_id}")

        # Sync GLOBAL commands (/ks). They appear in every server — including
        # the owner's — as a single copy.
        global_synced = await bot.tree.sync()
        print(f"Synced {len(global_synced)} global commands (removed {len(stale_global)} stale)")

        # Sync the owner-guild legacy commands (no global copy mixed in).
        synced = await bot.tree.sync(guild=owner_guild)
        print(f"Synced {len(synced)} legacy commands to owner guild (removed {len(stale_owner)} stale/duplicate)")

    except discord.HTTPException as e:
        if e.status == 429:
            print("Rate limited - commands already synced, skipping")
        else:
            print(f"Command sync error: {e}")
    except Exception as e:
        print(f"Command sync failed: {e}")


def start_bot():
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"Main bot error: {e}")


if __name__ == "__main__":
    start_bot()
