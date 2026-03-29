import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random
import re
import time
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
    delete_guild_config, create_session, get_session,
    update_session, get_pending_session,
    create_guild_key, delete_guild_keys_by_user,
    get_guild_key_stats, cleanup_expired_guild_keys,
    get_destination_url, get_script_profile, get_script_profiles,
    create_script_profile, update_script_profile, delete_script_profile,
    get_profile_by_name,
    SERVER_BASE_URL
)

TARGET_CHANNEL_ID = 1389210900489044048
AUTH_CHANNEL_ID = 1287714060716081183
LOG_CHANNEL_ID = 1270314848764559494
OWNER_ID = 1144213765424947251
DELAY_SECONDS = 1
BOOST_TEST_CHANNEL_ID = 1270301984897110148

DISCORD_KEY_EXPIRY_HOURS = 336

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
                await interaction.response.send_message("❌ Failed to generate key.", ephemeral=True)
                return

            expires_ts = int(time.time() + (duration * 3600))

            embed = discord.Embed(title="🔑 Your Key", color=discord.Color.green())
            embed.description = f"```{key}```"
            embed.add_field(name="Script", value=profile['name'], inline=True)
            embed.add_field(name="Expires", value=f"<t:{expires_ts}:R>", inline=True)
            embed.add_field(name="HWID Lock", value="Locks on first use", inline=True)
            embed.set_footer(text="Do not share your key. Leave the server = key revoked.")

            await interaction.response.edit_message(embed=embed, view=None)

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
                await interaction.response.edit_message(embed=embed, view=view)
                return

            token = create_session(
                self.guild_id,
                interaction.user.id,
                interaction.user.name,
                profile_id
            )

            if not token:
                await interaction.response.send_message("❌ Failed to create session. Try again later.", ephemeral=True)
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

            await interaction.response.edit_message(embed=embed, view=view)


class ProfileSelectView(discord.ui.View):
    def __init__(self, profiles, guild_id):
        super().__init__(timeout=120)
        self.add_item(ProfileSelectForKey(profiles, guild_id))


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


@ks_group.command(name="setup", description="[Admin] Initialize the key system for this server.")
@app_commands.checks.has_permissions(administrator=True)
async def ks_setup(interaction: discord.Interaction):
    config = init_guild_config(
        interaction.guild.id,
        interaction.guild.name,
        interaction.user.id
    )

    if not config:
        await interaction.response.send_message(
            "❌ Failed to initialize. Database may be unavailable.", ephemeral=True)
        return

    profiles = get_script_profiles(interaction.guild.id)

    embed = discord.Embed(title="⚙️ Key System", color=discord.Color.blurple())

    if profiles:
        embed.description = (
            f"Key system is **active** with **{len(profiles)}** script profile(s).\n\n"
            "**Commands:**\n"
            "`/ks addscript` — Add a new script profile\n"
            "`/ks setlink` — Set monetization links for ad-link profiles\n"
            "`/ks config` — View full configuration\n"
            "`/ks removescript` — Remove a script profile\n\n"
            "**Users run** `/ks getkey` **to get keys.**"
        )
        for p in profiles:
            type_emoji = "🔗" if p['key_type'] == 'adlink' else "💬"
            status = "✅" if p.get('enabled') else "❌"
            embed.add_field(
                name=f"{type_emoji} {p['name']} {status}",
                value=f"Duration: {p.get('key_duration_hours', 24)}h | Secret: ||{p.get('api_secret', 'N/A')[:16]}...||",
                inline=False
            )
    else:
        embed.description = (
            "Key system initialized! Now create your first script profile.\n\n"
            "**Key types:**\n"
            "💬 `discord` — Key given instantly, forces server membership\n"
            "🔗 `adlink` — Key given after completing a monetization link\n\n"
            "**Run:** `/ks addscript name: MyScript key_type: discord`"
        )

    embed.set_footer(text="Use /ks config to view full settings anytime.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@ks_group.command(name="addscript", description="[Admin] Add a script profile to your key system.")
@app_commands.describe(
    name="Name for this script (e.g. 'My ESP Script')",
    key_type="Type of key system",
    key_duration="How long keys last in hours (default: 24)",
    required_role="Role required to get a key (optional)"
)
@app_commands.choices(key_type=[
    app_commands.Choice(name="Discord (instant key, membership required)", value="discord"),
    app_commands.Choice(name="Ad-Link (monetization link required)", value="adlink"),
])
@app_commands.checks.has_permissions(administrator=True)
async def ks_addscript(
    interaction: discord.Interaction,
    name: str,
    key_type: app_commands.Choice[str],
    key_duration: int = 24,
    required_role: discord.Role = None
):
    config = get_guild_config(interaction.guild.id)
    if not config:
        await interaction.response.send_message("❌ Run `/ks setup` first.", ephemeral=True)
        return

    existing = get_profile_by_name(interaction.guild.id, name)
    if existing:
        await interaction.response.send_message(f"❌ A profile named **{name}** already exists.", ephemeral=True)
        return

    profiles = get_script_profiles(interaction.guild.id)
    if len(profiles) >= 10:
        await interaction.response.send_message("❌ Maximum 10 script profiles per server.", ephemeral=True)
        return

    profile = create_script_profile(
        interaction.guild.id,
        name,
        key_type.value,
        key_duration,
        required_role.id if required_role else None
    )

    if not profile:
        await interaction.response.send_message("❌ Failed to create profile.", ephemeral=True)
        return

    embed = discord.Embed(title="✅ Script Profile Created", color=discord.Color.green())

    info_lines = [
        f"**Name:** {name}",
        f"**Type:** {key_type.name}",
        f"**Key Duration:** {key_duration}h",
    ]

    if required_role:
        info_lines.append(f"**Required Role:** {required_role.mention}")

    info_lines.append(f"\n**🔐 API Secret:**\n||{profile['api_secret']}||")
    info_lines.append(f"\n**🔗 Validation URL:**\n```{SERVER_BASE_URL}/api/validate-guild-key```")

    if key_type.value == 'adlink':
        dest_url = get_destination_url(interaction.guild.id, profile['profile_id'])
        info_lines.append(f"\n**📎 Destination URL** (set this in your campaign):\n```{dest_url}```")
        info_lines.append("\nNext: Run `/ks setlink` to register your campaign URL.")
    else:
        info_lines.append("\nUsers can get keys immediately with `/ks getkey`.")

    embed.description = "\n".join(info_lines)

    lua_config = (
        "```lua\nDiscordValidation = {\n"
        "    Enabled = true,\n"
        f"    ValidateURL = '{SERVER_BASE_URL}/api/validate-guild-key',\n"
        f"    APISecret = '{profile['api_secret']}'\n"
        "},\n```"
    )
    embed.add_field(name="Roblox Script Config", value=lua_config, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@ks_group.command(name="setlink", description="[Admin] Set monetization links for an ad-link script.")
@app_commands.describe(script_name="Name of the script profile")
@app_commands.checks.has_permissions(administrator=True)
async def ks_setlink(interaction: discord.Interaction, script_name: str):
    config = get_guild_config(interaction.guild.id)
    if not config:
        await interaction.response.send_message("❌ Run `/ks setup` first.", ephemeral=True)
        return

    profile = get_profile_by_name(interaction.guild.id, script_name)
    if not profile:
        await interaction.response.send_message(
            f"❌ No script profile named **{script_name}** found.", ephemeral=True)
        return

    if profile['key_type'] != 'adlink':
        await interaction.response.send_message(
            f"❌ **{script_name}** is a Discord-type profile. Links are only for ad-link profiles.", ephemeral=True)
        return

    await interaction.response.send_modal(SetupLinksModal(profile))


@ks_group.command(name="config", description="[Admin] View current key system configuration.")
@app_commands.checks.has_permissions(administrator=True)
async def ks_config(interaction: discord.Interaction):
    config = get_guild_config(interaction.guild.id)
    if not config:
        await interaction.response.send_message("❌ Key system not set up. Run `/ks setup` first.", ephemeral=True)
        return

    profiles = get_script_profiles(interaction.guild.id)

    embed = discord.Embed(title="⚙️ Key System Config", color=discord.Color.blurple())

    status_line = "✅ Enabled" if config.get('enabled') else "❌ Disabled"
    embed.description = f"**Status:** {status_line}\n**Script Profiles:** {len(profiles)}"

    if not profiles:
        embed.add_field(name="Scripts", value="None — use `/ks addscript`", inline=False)
    else:
        for p in profiles:
            type_emoji = "🔗" if p['key_type'] == 'adlink' else "💬"
            status = "✅" if p.get('enabled') else "❌"

            lines = [f"Type: {p['key_type']} | Duration: {p.get('key_duration_hours', 24)}h"]

            if p.get('required_role_id'):
                lines.append(f"Role: <@&{p['required_role_id']}>")

            if p['key_type'] == 'adlink':
                providers = []
                if p.get('workink_url'):
                    providers.append("⚡WI")
                if p.get('lootlabs_url'):
                    providers.append("🎁LL")
                if p.get('linkvertise_url'):
                    providers.append("🔗LV")
                if providers:
                    lines.append(f"Providers: {' '.join(providers)}")
                else:
                    lines.append("⚠️ No links set — use `/ks setlink`")

            lines.append(f"Secret: ||{p.get('api_secret', 'N/A')}||")

            embed.add_field(
                name=f"{type_emoji} {p['name']} {status}",
                value="\n".join(lines),
                inline=False
            )

    embed.add_field(
        name="Validation URL",
        value=f"```{SERVER_BASE_URL}/api/validate-guild-key```",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@ks_group.command(name="removescript", description="[Admin] Remove a script profile.")
@app_commands.describe(script_name="Name of the script profile to remove")
@app_commands.checks.has_permissions(administrator=True)
async def ks_removescript(interaction: discord.Interaction, script_name: str):
    profile = get_profile_by_name(interaction.guild.id, script_name)
    if not profile:
        await interaction.response.send_message(f"❌ No profile named **{script_name}** found.", ephemeral=True)
        return

    delete_script_profile(profile['profile_id'])
    await interaction.response.send_message(
        f"🗑️ **{script_name}** and all its keys have been deleted.", ephemeral=True)


@ks_group.command(name="getkey", description="Get a script key.")
async def ks_getkey(interaction: discord.Interaction):
    config = get_guild_config(interaction.guild.id)
    if not config or not config.get('enabled'):
        await interaction.response.send_message("❌ Key system is not set up for this server.", ephemeral=True)
        return

    profiles = get_script_profiles(interaction.guild.id)
    active_profiles = [p for p in profiles if p.get('enabled')]

    if not active_profiles:
        await interaction.response.send_message("❌ No script profiles available.", ephemeral=True)
        return

    if len(active_profiles) == 1:
        profile = active_profiles[0]

        if profile.get('required_role_id'):
            role = interaction.guild.get_role(int(profile['required_role_id']))
            if role and role not in interaction.user.roles:
                await interaction.response.send_message(
                    f"❌ You need the {role.mention} role to get a key for **{profile['name']}**.", ephemeral=True)
                return

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
                await interaction.response.send_message("❌ Failed to generate key.", ephemeral=True)
                return

            expires_ts = int(time.time() + (duration * 3600))

            embed = discord.Embed(title="🔑 Your Key", color=discord.Color.green())
            embed.description = f"```{key}```"
            embed.add_field(name="Script", value=profile['name'], inline=True)
            embed.add_field(name="Expires", value=f"<t:{expires_ts}:R>", inline=True)
            embed.add_field(name="HWID Lock", value="Locks on first use", inline=True)
            embed.set_footer(text="Do not share your key. Leave the server = key revoked.")

            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        elif profile['key_type'] == 'adlink':
            has_providers = any([
                profile.get('workink_url'),
                profile.get('lootlabs_url'),
                profile.get('linkvertise_url')
            ])
            if not has_providers:
                await interaction.response.send_message(
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
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                return

            token = create_session(
                interaction.guild.id,
                interaction.user.id,
                interaction.user.name,
                profile['profile_id']
            )

            if not token:
                await interaction.response.send_message("❌ Failed to create session.", ephemeral=True)
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

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

    view = ProfileSelectView(active_profiles, str(interaction.guild.id))
    embed = discord.Embed(title="🔑 Select a Script", color=discord.Color.blurple())
    embed.description = "Choose which script you need a key for:"
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


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

    embed = discord.Embed(title=f"📊 Key Stats{title_suffix}", color=discord.Color.blurple())
    embed.add_field(name="Total Keys", value=str(stats['total']), inline=True)
    embed.add_field(name="Active", value=str(stats['active']), inline=True)
    embed.add_field(name="Expired", value=str(stats['expired']), inline=True)
    embed.add_field(name="HWID Locked", value=str(stats['hwid_locked']), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@ks_group.command(name="disable", description="[Admin] Disable the key system.")
@app_commands.checks.has_permissions(administrator=True)
async def ks_disable(interaction: discord.Interaction):
    config = get_guild_config(interaction.guild.id)
    if not config:
        await interaction.response.send_message("❌ Nothing to disable.", ephemeral=True)
        return

    save_guild_config(interaction.guild.id, {"enabled": False, "updated_at": time.time()})
    await interaction.response.send_message("🔒 Key system disabled. Run `/ks setup` to re-enable.", ephemeral=True)


@ks_group.command(name="toggle-membership", description="[Admin] Toggle membership requirement for a script.")
@app_commands.describe(script_name="Name of the script profile")
@app_commands.checks.has_permissions(administrator=True)
async def ks_toggle_membership(interaction: discord.Interaction, script_name: str):
    profile = get_profile_by_name(interaction.guild.id, script_name)
    if not profile:
        await interaction.response.send_message(f"❌ No script named **{script_name}** found.", ephemeral=True)
        return

    current = profile.get('require_membership', True)
    update_script_profile(profile['profile_id'], {"require_membership": not current})

    status = "disabled" if current else "enabled"
    await interaction.response.send_message(
        f"{'🔓' if current else '🔒'} Membership requirement **{status}** for **{script_name}**.", ephemeral=True)


bot.tree.add_command(ks_group)


@bot.event
async def on_member_remove(member):
    if member.guild.id == GUILD_ID:
        count = delete_keys_by_discord_id(member.id)
        if count > 0:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(title="\U0001f511 Key Auto-Revoked", color=discord.Color.red())
                embed.add_field(name="User", value=f"{member.name} ({member.id})", inline=False)
                embed.add_field(name="Reason", value="Left the server", inline=False)
                embed.add_field(name="Keys Revoked", value=str(count), inline=False)
                await log_channel.send(embed=embed)

    guild_config = get_guild_config(member.guild.id)
    if guild_config:
        guild_count = delete_guild_keys_by_user(member.guild.id, member.id)
        if guild_count > 0:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(title="🔑 Guild Key Auto-Revoked", color=discord.Color.orange())
                embed.add_field(name="User", value=f"{member.name} ({member.id})", inline=False)
                embed.add_field(name="Guild", value=f"{member.guild.name} ({member.guild.id})", inline=False)
                embed.add_field(name="Keys Revoked", value=str(guild_count), inline=False)
                await log_channel.send(embed=embed)


@bot.event
async def on_message(message):
    global last_meow_count

    if message.author == bot.user:
        return

    if message.author.bot:
        await bot.process_commands(message)
        return

    content = message.content or ""
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

    boost_channels = {TARGET_CHANNEL_ID, BOOST_TEST_CHANNEL_ID}

    if message.channel.id in boost_channels:
        content_lower = content.lower()
        is_system_boost = message.type in BOOST_TYPES

        is_text_boost = any(pattern in content_lower for pattern in [
            "just boosted the server",
            "boosted the server"
        ])

        is_text_boost = is_text_boost and not is_system_boost

        if is_text_boost or is_system_boost:
            if not message.author:
                return

            user_id = message.author.id

            if user_id not in recent_boosts:
                recent_boosts[user_id] = True

                if user_id in pending_tasks:
                    try:
                        pending_tasks[user_id].cancel()
                    except:
                        pass

                pending_tasks[user_id] = bot.loop.create_task(send_good_boy_after_delay(user_id, message.channel))

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

        guild = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} guild commands")

        global_synced = await bot.tree.sync()
        print(f"Synced {len(global_synced)} global commands")

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
