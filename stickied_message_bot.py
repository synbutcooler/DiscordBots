import os
import json
import time
import redis
import discord
import asyncio
from discord.ext import commands
from discord import Embed, app_commands
from config import STICKIED_TOKEN

REDIS_URL = os.environ.get("REDIS_URL")
if not REDIS_URL:
    print("❌ REDIS_URL not set! Data will NOT persist between restarts.")
    r = None
else:
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True, ssl_cert_reqs=None)
        r.ping()
        print("✅ Connected to Redis")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        r = None

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.webhooks = True
bot = commands.Bot(command_prefix="?", intents=intents)

stickied_messages = {}

def save_data():
    if r:
        try:
            r.set("stickied_messages", json.dumps(stickied_messages))
        except Exception as e:
            print(f"❌ Redis save failed: {e}")

def load_data():
    global stickied_messages
    if r:
        try:
            raw = r.get("stickied_messages")
            stickied_messages = json.loads(raw) if raw else {}
            print(f"✅ Loaded {len(stickied_messages)} stickied channels from Redis")
            return
        except Exception as e:
            print(f"❌ Redis load failed: {e}")
    stickied_messages = {}

@bot.event
async def on_ready():
    load_data()
    print(f'Stickied bot logged in as {bot.user}')
    try:
        await asyncio.sleep(3)
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global commands")
    except discord.HTTPException as e:
        if e.status == 429:
            print("⚠️ Rate limited - commands already synced, skipping")
        else:
            print(f"Command sync error: {e}")
    except Exception as e:
        print(f"Slash command sync failed: {e}")

def create_embed_from_data(data):
    embed = Embed(
        title=data.get("title"),
        description=data.get("description")
    )

    if data.get("color"):
        try:
            embed.color = int(data["color"].replace("#", ""), 16)
        except:
            embed.color = 0x9c88ff
    else:
        embed.color = 0x9c88ff

    if data.get("footer"):
        embed.set_footer(text=data["footer"])

    if data.get("image"):
        embed.set_image(url=data["image"])

    if data.get("thumbnail"):
        embed.set_thumbnail(url=data["thumbnail"])

    return embed

async def get_or_create_webhook(channel):
    webhooks = await channel.webhooks()
    webhook = discord.utils.get(webhooks, name="Stickied Bot")

    if webhook is None:
        webhook = await channel.create_webhook(name="Stickied Bot")

    return webhook

def get_channel_key(guild_id, channel_id):
    return f"{guild_id}_{channel_id}"

@bot.tree.command(name="stick", description="Set a stickied text message.")
@app_commands.default_permissions(manage_messages=True)
async def stick(
    interaction: discord.Interaction,
    message: str,
    channel: discord.TextChannel = None,
    cooldown: int = 0,
    use_webhook: bool = False,
    webhook_name: str = None,
    webhook_avatar: str = None
):
    await interaction.response.defer(ephemeral=True)

    target_channel = channel or interaction.channel
    channel_key = get_channel_key(interaction.guild_id, target_channel.id)

    stickied_messages[channel_key] = {
        "content": message,
        "embed": None,
        "last_message": None,
        "cooldown": cooldown,
        "last_sent": 0,
        "use_webhook": use_webhook,
        "webhook_name": webhook_name,
        "webhook_avatar": webhook_avatar
    }
    save_data()

    try:
        if use_webhook:
            webhook = await get_or_create_webhook(target_channel)
            msg = await webhook.send(
                content=message,
                username=webhook_name or "Stickied Message",
                avatar_url=webhook_avatar,
                wait=True
            )
        else:
            msg = await target_channel.send(message)

        stickied_messages[channel_key]["last_message"] = msg.id
        stickied_messages[channel_key]["last_sent"] = time.time()
        save_data()

        await interaction.followup.send(f"✅ Stickied message set in {target_channel.mention}!")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@bot.command(name="stick")
@commands.has_permissions(manage_messages=True)
async def stick_prefix(ctx, *, message: str):
    channel_key = get_channel_key(ctx.guild.id, ctx.channel.id)

    stickied_messages[channel_key] = {
        "content": message,
        "embed": None,
        "last_message": None,
        "cooldown": 0,
        "last_sent": 0,
        "use_webhook": False,
        "webhook_name": None,
        "webhook_avatar": None
    }
    save_data()

    try:
        await ctx.message.delete()
    except:
        pass

    try:
        msg = await ctx.channel.send(message)
        stickied_messages[channel_key]["last_message"] = msg.id
        stickied_messages[channel_key]["last_sent"] = time.time()
        save_data()

        confirm = await ctx.send("✅ Stickied message set!")
        await confirm.delete(delay=3)
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

@bot.command(name="cooldown")
@commands.has_permissions(manage_messages=True)
async def set_cooldown(ctx, seconds: int):
    channel_key = get_channel_key(ctx.guild.id, ctx.channel.id)

    if channel_key not in stickied_messages:
        await ctx.send("❌ No stickied message in this channel. Use `?stick` first.")
        return

    if seconds < 0:
        await ctx.send("❌ Cooldown must be 0 or greater.")
        return

    stickied_messages[channel_key]["cooldown"] = seconds
    save_data()

    try:
        await ctx.message.delete()
    except:
        pass

    confirm = await ctx.send(f"✅ Cooldown set to {seconds} seconds!")
    await confirm.delete(delay=3)

@bot.command(name="stickwh")
@commands.has_permissions(manage_messages=True)
async def stick_webhook_prefix(ctx, webhook_name: str, *, message: str):
    channel_key = get_channel_key(ctx.guild.id, ctx.channel.id)

    stickied_messages[channel_key] = {
        "content": message,
        "embed": None,
        "last_message": None,
        "cooldown": 0,
        "last_sent": 0,
        "use_webhook": True,
        "webhook_name": webhook_name,
        "webhook_avatar": None
    }
    save_data()

    try:
        await ctx.message.delete()
    except:
        pass

    try:
        webhook = await get_or_create_webhook(ctx.channel)
        msg = await webhook.send(
            content=message,
            username=webhook_name,
            wait=True
        )
        stickied_messages[channel_key]["last_message"] = msg.id
        stickied_messages[channel_key]["last_sent"] = time.time()
        save_data()

        confirm = await ctx.send("✅ Stickied webhook message set!")
        await confirm.delete(delay=3)
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

@bot.command(name="unstick")
@commands.has_permissions(manage_messages=True)
async def unstick_prefix(ctx):
    channel_key = get_channel_key(ctx.guild.id, ctx.channel.id)

    if channel_key in stickied_messages:
        if stickied_messages[channel_key].get("last_message"):
            try:
                msg = await ctx.channel.fetch_message(stickied_messages[channel_key]["last_message"])
                await msg.delete()
            except:
                pass

        del stickied_messages[channel_key]
        save_data()

        try:
            await ctx.message.delete()
        except:
            pass

        confirm = await ctx.send("✅ Stickied message removed!")
        await confirm.delete(delay=3)
    else:
        await ctx.send("❌ No stickied message in this channel.")

@bot.tree.command(name="stickembed", description="Set a stickied embed message.")
@app_commands.default_permissions(manage_messages=True)
async def stickembed(
    interaction: discord.Interaction,
    title: str,
    description: str,
    channel: discord.TextChannel = None,
    cooldown: int = 0,
    color: str = None,
    footer: str = None,
    image_url: str = None,
    thumbnail_url: str = None,
    use_webhook: bool = False,
    webhook_name: str = None,
    webhook_avatar: str = None
):
    await interaction.response.defer(ephemeral=True)

    target_channel = channel or interaction.channel
    channel_key = get_channel_key(interaction.guild_id, target_channel.id)

    embed_data = {
        "title": title,
        "description": description,
        "color": color,
        "footer": footer,
        "image": image_url,
        "thumbnail": thumbnail_url
    }

    stickied_messages[channel_key] = {
        "content": None,
        "embed": embed_data,
        "last_message": None,
        "cooldown": cooldown,
        "last_sent": 0,
        "use_webhook": use_webhook,
        "webhook_name": webhook_name,
        "webhook_avatar": webhook_avatar
    }
    save_data()

    try:
        embed = create_embed_from_data(embed_data)

        if use_webhook:
            webhook = await get_or_create_webhook(target_channel)
            msg = await webhook.send(
                embed=embed,
                username=webhook_name or "Stickied Message",
                avatar_url=webhook_avatar,
                wait=True
            )
        else:
            msg = await target_channel.send(embed=embed)

        stickied_messages[channel_key]["last_message"] = msg.id
        stickied_messages[channel_key]["last_sent"] = time.time()
        save_data()

        await interaction.followup.send(f"✅ Stickied embed set in {target_channel.mention}!")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@bot.tree.command(name="unstick", description="Remove stickied message from a channel.")
@app_commands.default_permissions(manage_messages=True)
async def unstick(interaction: discord.Interaction, channel: discord.TextChannel = None):
    await interaction.response.defer(ephemeral=True)

    target_channel = channel or interaction.channel
    channel_key = get_channel_key(interaction.guild_id, target_channel.id)

    if channel_key in stickied_messages:
        if stickied_messages[channel_key].get("last_message"):
            try:
                msg = await target_channel.fetch_message(stickied_messages[channel_key]["last_message"])
                await msg.delete()
            except:
                pass

        del stickied_messages[channel_key]
        save_data()
        await interaction.followup.send(f"✅ Stickied message removed from {target_channel.mention}.")
    else:
        await interaction.followup.send(f"❌ No stickied message in {target_channel.mention}.")

@bot.tree.command(name="list", description="List all active stickied messages in this server.")
@app_commands.default_permissions(manage_messages=True)
async def list_stickied(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    server_stickied = {k: v for k, v in stickied_messages.items() if k.startswith(f"{interaction.guild_id}_")}

    if not server_stickied:
        await interaction.followup.send("No stickied messages set in this server.")
        return

    embed = Embed(
        title="📌 Active Stickied Messages",
        description=f"Total: {len(server_stickied)} channel(s)",
        color=0x9c88ff
    )

    for channel_key, data in server_stickied.items():
        channel_id = int(channel_key.split("_")[1])
        channel = bot.get_channel(channel_id)
        if channel:
            content_preview = data.get("content") or f"Embed: {data['embed']['title']}"
            if len(content_preview) > 50:
                content_preview = content_preview[:50] + "..."

            cooldown_text = f" • {data['cooldown']}s cooldown" if data.get('cooldown', 0) > 0 else ""
            webhook_text = " • Webhook" if data.get('use_webhook') else ""
            embed.add_field(
                name=f"#{channel.name}",
                value=f"`{content_preview}`{cooldown_text}{webhook_text}",
                inline=False
            )

    embed.set_footer(text="Use /unstick or ?unstick to remove")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="help", description="Show bot commands and info.")
async def help_command(interaction: discord.Interaction):
    embed = Embed(
        title="📌 Stickied Bot",
        description="Keep important messages pinned at the bottom of your channels!",
        color=0x9c88ff
    )

    embed.add_field(
        name="📝 Slash Commands",
        value=(
            "`/stick` - Set text stickied (single line)\n"
            "`/stickembed` - Set embed stickied\n"
            "`/unstick` - Remove stickied\n"
            "`/list` - View all stickied messages"
        ),
        inline=False
    )

    embed.add_field(
        name="📝 Prefix Commands (multi-line support)",
        value=(
            "`?stick <message>` - Set text stickied with line breaks\n"
            "`?stickwh <name> <message>` - Same but with webhook name\n"
            "`?cooldown <seconds>` - Update cooldown for current channel\n"
            "`?unstick` - Remove stickied"
        ),
        inline=False
    )

    embed.add_field(
        name="✨ Slash Command Options",
        value=(
            "`channel` - Target channel\n"
            "`cooldown` - Seconds between re-sticks\n"
            "`color` - Hex color (embeds)\n"
            "`footer` - Footer text (embeds)\n"
            "`image_url` - Large image (embeds)\n"
            "`thumbnail_url` - Small image (embeds)\n"
            "`use_webhook` - Custom appearance\n"
            "`webhook_name` - Custom name\n"
            "`webhook_avatar` - Custom avatar URL"
        ),
        inline=False
    )

    embed.add_field(
        name="💡 Tips",
        value=(
            "• Use `?stick` for multi-line messages\n"
            "• Use `?cooldown` to adjust cooldown after sticking\n"
            "• `cooldown` prevents spam\n"
            "• Messages send instantly when set\n"
            "• Colors use hex codes (e.g. 9c88ff)"
        ),
        inline=False
    )

    embed.set_footer(text="Made with 💜 by Vadrifts • Requires Manage Messages")

    await interaction.response.send_message(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not message.guild:
        return

    await bot.process_commands(message)

    if message.content.startswith("?"):
        return

    channel_key = get_channel_key(message.guild.id, message.channel.id)

    if channel_key in stickied_messages:
        data = stickied_messages[channel_key]

        cooldown = data.get("cooldown", 0)
        last_sent = data.get("last_sent", 0)

        if cooldown > 0 and (time.time() - last_sent) < cooldown:
            return

        if data["last_message"]:
            try:
                old_msg = await message.channel.fetch_message(data["last_message"])
                await old_msg.delete()
            except:
                pass

        try:
            if data.get("use_webhook"):
                webhook = await get_or_create_webhook(message.channel)

                if data["embed"]:
                    embed = create_embed_from_data(data["embed"])
                    new_msg = await webhook.send(
                        embed=embed,
                        username=data.get("webhook_name") or "Stickied Message",
                        avatar_url=data.get("webhook_avatar"),
                        wait=True
                    )
                else:
                    new_msg = await webhook.send(
                        content=data["content"],
                        username=data.get("webhook_name") or "Stickied Message",
                        avatar_url=data.get("webhook_avatar"),
                        wait=True
                    )
            else:
                if data["embed"]:
                    embed = create_embed_from_data(data["embed"])
                    new_msg = await message.channel.send(embed=embed)
                else:
                    new_msg = await message.channel.send(data["content"])

            stickied_messages[channel_key]["last_message"] = new_msg.id
            stickied_messages[channel_key]["last_sent"] = time.time()
            save_data()
        except Exception as e:
            print(f"Error sending stickied message: {e}")

def start_stickied_bot():
    if not STICKIED_TOKEN:
        print("ERROR: STICKIED_TOKEN environment variable not set!")
        return
    try:
        bot.run(STICKIED_TOKEN)
    except Exception as e:
        print(f"Stickied bot error: {e}")

if __name__ == "__main__":
    start_stickied_bot()
