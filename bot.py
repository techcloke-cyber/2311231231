"""
╔══════════════════════════════════════════════════════════════════╗
║           ADVANCED DISCORD BOT v2 — bot.py                     ║
║  Ticket Departments · Minecraft Link · Verification · AutoMod  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
import json, os, asyncio, datetime, re, aiohttp
from dotenv import load_dotenv

# ──────────────────────────────────────────────
#  CONFIG & ENV
# ──────────────────────────────────────────────

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    # Core IDs
    "staff_role_id": 1473465114064588902,   # Role pinged + can manage tickets
    "log_channel_id": 0,
    "ticket_category_id": 0,
    "welcome_channel_id": 0,
    "member_counter_channel_id": 0,
    "announcement_channel_id": 0,
    "verification_channel_id": 0,
    "verified_role_id": 0,

    # Welcome
    "welcome_message": "Welcome to the server, {user}! 🎉",
    "welcome_emoji": "👋",

    # AutoMod
    "banned_words": ["badword1", "badword2"],
    "anti_link": True,
    "anti_spam_threshold": 5,

    # Minecraft
    "minecraft_server_ip": "",
    "minecraft_server_port": 25565,
    "minecraft_status_channel_id": 0,
    "minecraft_events_channel_id": 0,
}

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        print(f"[CONFIG] Created {CONFIG_FILE}. Fill in your IDs and restart.")
    with open(CONFIG_FILE) as f:
        data = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        if k not in data:
            data[k] = v
    save_config(data)
    return data

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

config = load_config()

# Ticket claim tracking {channel_id: claimer_user_id}
ticket_claims: dict[int, int] = {}
# Spam tracking
spam_tracker: dict[int, list[float]] = {}

# ──────────────────────────────────────────────
#  BOT SETUP
# ──────────────────────────────────────────────

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def make_embed(
    title="", description="",
    color=discord.Color.blurple(),
    footer="", fields: list = None,
    thumbnail_url=""
) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color,
                      timestamp=datetime.datetime.utcnow())
    if footer:
        e.set_footer(text=footer)
    if thumbnail_url:
        e.set_thumbnail(url=thumbnail_url)
    for name, value, inline in (fields or []):
        e.add_field(name=name, value=value, inline=inline)
    return e

async def get_log_channel(guild: discord.Guild):
    cid = config.get("log_channel_id", 0)
    return guild.get_channel(cid) if cid else None

def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    sid = config.get("staff_role_id", 0)
    return any(r.id == sid for r in interaction.user.roles)

async def log_mod(guild, action, target, reason, actor=None):
    ch = await get_log_channel(guild)
    if not ch:
        return
    desc = f"**User:** {target.mention} (`{target.id}`)\n**Reason:** {reason}"
    if actor:
        desc += f"\n**By:** {actor.mention}"
    await ch.send(embed=make_embed(
        title=f"🛡️ {action}", description=desc,
        color=discord.Color.orange(), footer="Moderation Log"
    ))

# ══════════════════════════════════════════════
#  SECTION 1 — TICKET SYSTEM (with departments)
# ══════════════════════════════════════════════

DEPARTMENTS = {
    "general":      ("⚙️", "General Assistance", "Technical aid or bugs"),
    "sponsorships": ("💎", "Sponsorships",        "Partner applications"),
}

class DepartmentSelect(ui.Select):
    """Dropdown to pick a department when opening a ticket."""
    def __init__(self):
        options = [
            discord.SelectOption(label=label, description=desc,
                                 emoji=emoji, value=key)
            for key, (emoji, label, desc) in DEPARTMENTS.items()
        ]
        super().__init__(placeholder="Select Department...",
                         options=options, custom_id="dept_select")

    async def callback(self, interaction: discord.Interaction):
        dept_key = self.values[0]
        emoji, label, desc = DEPARTMENTS[dept_key]
        await create_ticket_channel(interaction, dept_key, label, emoji)


class TicketPanelView(ui.View):
    """Persistent panel view with department dropdown."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DepartmentSelect())


async def create_ticket_channel(interaction: discord.Interaction,
                                dept_key: str, dept_label: str, dept_emoji: str):
    guild = interaction.guild
    user = interaction.user
    staff_role = guild.get_role(config.get("staff_role_id", 0))

    # Prevent duplicate tickets per department
    safe_name = user.name.lower().replace(" ", "-")
    existing = discord.utils.get(guild.text_channels,
                                 name=f"{dept_key}-{safe_name}")
    if existing:
        await interaction.response.send_message(
            f"You already have an open ticket: {existing.mention}", ephemeral=True)
        return

    cat_id = config.get("ticket_category_id", 0)
    category = guild.get_channel(cat_id) if cat_id else None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                          read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                              manage_channels=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True)

    try:
        channel = await guild.create_text_channel(
            name=f"{dept_key}-{safe_name}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket | {dept_label} | {user} (ID: {user.id})"
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Missing permissions to create ticket channel.", ephemeral=True)
        return

    e = make_embed(
        title=f"{dept_emoji} {dept_label} — Support Ticket",
        description=(
            f"Hello {user.mention}! A staff member will be with you shortly.\n\n"
            f"**Department:** {dept_emoji} {dept_label}\n"
            f"**Opened by:** {user.mention}\n\n"
            "Please describe your issue in detail below."
        ),
        color=discord.Color.blurple(),
        footer="Use the buttons below to manage this ticket."
    )

    ping_msg = staff_role.mention if staff_role else "@staff"
    await channel.send(
        content=f"{ping_msg} — new ticket from {user.mention}",
        embed=e,
        view=TicketManageView()
    )

    await interaction.response.send_message(
        f"✅ Your **{dept_label}** ticket: {channel.mention}", ephemeral=True)

    log_ch = await get_log_channel(guild)
    if log_ch:
        await log_ch.send(embed=make_embed(
            title="🎫 Ticket Opened",
            description=(
                f"**User:** {user.mention}\n"
                f"**Department:** {dept_emoji} {dept_label}\n"
                f"**Channel:** {channel.mention}"
            ),
            color=discord.Color.green(), footer="Ticket System"
        ))


class TicketManageView(ui.View):
    """Buttons inside a ticket: Claim · Close · Add User."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✋ Claim", style=discord.ButtonStyle.blurple,
               custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        ticket_claims[interaction.channel.id] = interaction.user.id
        button.label = f"✋ Claimed by {interaction.user.display_name}"
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.channel.send(embed=make_embed(
            title="✋ Ticket Claimed",
            description=f"This ticket has been claimed by {interaction.user.mention}.",
            color=discord.Color.blurple()
        ))

    @ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger,
               custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")

        log_ch = await get_log_channel(interaction.guild)
        if log_ch:
            claimer_id = ticket_claims.get(interaction.channel.id)
            claimer = f"<@{claimer_id}>" if claimer_id else "Unclaimed"
            await log_ch.send(embed=make_embed(
                title="🎫 Ticket Closed",
                description=(
                    f"**Channel:** `{interaction.channel.name}`\n"
                    f"**Closed by:** {interaction.user.mention}\n"
                    f"**Claimed by:** {claimer}"
                ),
                color=discord.Color.red(), footer="Ticket System"
            ))

        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except discord.HTTPException:
            pass

    @ui.button(label="➕ Add User", style=discord.ButtonStyle.secondary,
               custom_id="ticket_adduser")
    async def add_user(self, interaction: discord.Interaction, button: ui.Button):
        if not is_staff(interaction):
            await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Mention the user to add (type their @mention in chat):", ephemeral=True)

        def check(m):
            return (m.author == interaction.user and
                    m.channel == interaction.channel and m.mentions)

        try:
            msg = await bot.wait_for("message", check=check, timeout=30)
            for member in msg.mentions:
                await interaction.channel.set_permissions(
                    member, view_channel=True, send_messages=True)
            await interaction.channel.send(embed=make_embed(
                title="➕ User Added",
                description=f"Added {', '.join(m.mention for m in msg.mentions)}.",
                color=discord.Color.green()
            ))
        except asyncio.TimeoutError:
            pass


# ══════════════════════════════════════════════
#  SECTION 2 — VERIFICATION SYSTEM
# ══════════════════════════════════════════════

class VerifyButton(ui.View):
    """Persistent verify button."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✅ Verify Me", style=discord.ButtonStyle.green,
               custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        verified_role_id = config.get("verified_role_id", 0)
        if not verified_role_id:
            await interaction.response.send_message(
                "❌ Verification role not configured.", ephemeral=True)
            return
        role = guild.get_role(verified_role_id)
        if not role:
            await interaction.response.send_message(
                "❌ Verified role not found.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(
                "✅ You are already verified!", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="Verified via button")
            await interaction.response.send_message(embed=make_embed(
                title="✅ Verified!",
                description=f"You've been given the **{role.name}** role. Welcome!",
                color=discord.Color.green()
            ), ephemeral=True)
            log_ch = await get_log_channel(guild)
            if log_ch:
                await log_ch.send(embed=make_embed(
                    title="✅ Member Verified",
                    description=f"{interaction.user.mention} self-verified.",
                    color=discord.Color.green(), footer="Verification System"
                ))
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I can't assign that role.", ephemeral=True)


# ══════════════════════════════════════════════
#  SECTION 3 — MINECRAFT INTEGRATION
# ══════════════════════════════════════════════

async def fetch_mc_status(ip: str, port: int = 25565) -> dict | None:
    """Query mcsrvstat.us API — no plugin needed, works with any public Java server."""
    url = f"https://api.mcsrvstat.us/2/{ip}:{port}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        print(f"[MC] API error: {e}")
    return None


@tasks.loop(minutes=5)
async def update_mc_counter():
    """Update the Minecraft player count voice channel every 5 minutes."""
    ip = config.get("minecraft_server_ip", "")
    ch_id = config.get("minecraft_status_channel_id", 0)
    if not ip or not ch_id:
        return
    data = await fetch_mc_status(ip, config.get("minecraft_server_port", 25565))
    for guild in bot.guilds:
        channel = guild.get_channel(ch_id)
        if not channel:
            continue
        if data and data.get("online"):
            current = data.get("players", {}).get("online", 0)
            maximum = data.get("players", {}).get("max", 0)
            new_name = f"⛏️ MC: {current}/{maximum} online"
        else:
            new_name = "⛏️ MC: Offline"
        try:
            if channel.name != new_name:
                await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException):
            pass

@update_mc_counter.before_loop
async def before_mc():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
#  SECTION 4 — AUTO-MODERATION
# ══════════════════════════════════════════════

URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)

async def handle_automod(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    content_lower = message.content.lower()
    author = message.author
    guild = message.guild

    for word in config.get("banned_words", []):
        if word.lower() in content_lower:
            await message.delete()
            await log_mod(guild, "🚫 Banned Word", author,
                          f"Message contained: `{word}`")
            return

    if config.get("anti_link", True):
        if not (author.guild_permissions.administrator or
                any(r.id == config.get("staff_role_id", 0) for r in author.roles)):
            if URL_RE.search(message.content):
                await message.delete()
                await log_mod(guild, "🔗 Anti-Link", author, "Deleted a link.")
                return

    now = asyncio.get_event_loop().time()
    uid = author.id
    spam_tracker.setdefault(uid, [])
    spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < 5]
    spam_tracker[uid].append(now)
    if len(spam_tracker[uid]) >= config.get("anti_spam_threshold", 5):
        spam_tracker[uid] = []
        try:
            await author.timeout(datetime.timedelta(minutes=5), reason="AutoMod: spam")
            await log_mod(guild, "⏱️ Auto-Timeout", author, "Spam detected.")
        except discord.Forbidden:
            pass


# ══════════════════════════════════════════════
#  SECTION 5 — LIVE MEMBER COUNTER
# ══════════════════════════════════════════════

@tasks.loop(minutes=5)
async def update_member_counter():
    ch_id = config.get("member_counter_channel_id", 0)
    if not ch_id:
        return
    for guild in bot.guilds:
        channel = guild.get_channel(ch_id)
        if not channel:
            continue
        online = sum(1 for m in guild.members
                     if m.status != discord.Status.offline and not m.bot)
        new_name = f"🟢 Online: {online}"
        try:
            if channel.name != new_name:
                await channel.edit(name=new_name)
        except (discord.Forbidden, discord.HTTPException):
            pass

@update_member_counter.before_loop
async def before_counter():
    await bot.wait_until_ready()


# ══════════════════════════════════════════════
#  SECTION 6 — WELCOME
# ══════════════════════════════════════════════

@bot.event
async def on_member_join(member: discord.Member):
    ch_id = config.get("welcome_channel_id", 0)
    if not ch_id:
        return
    channel = member.guild.get_channel(ch_id)
    if not channel:
        return
    msg = (config.get("welcome_message", DEFAULT_CONFIG["welcome_message"])
           .replace("{user}", member.mention)
           .replace("{server}", member.guild.name))
    e = make_embed(
        title="👋 Welcome!",
        description=msg,
        color=discord.Color.green(),
        fields=[
            ("Member #", str(member.guild.member_count), True),
            ("Account Created", member.created_at.strftime("%Y-%m-%d"), True),
        ],
        thumbnail_url=str(member.display_avatar.url)
    )
    try:
        sent = await channel.send(embed=e)
        await sent.add_reaction(config.get("welcome_emoji", "👋"))
    except (discord.Forbidden, discord.HTTPException):
        pass


# ══════════════════════════════════════════════
#  SECTION 7 — SLASH COMMANDS
# ══════════════════════════════════════════════

# ── /panel ─────────────────────────────────────
@tree.command(name="panel", description="Post the ticket panel with department dropdown. (Admin)")
async def panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    e = make_embed(
        title="📋 Internal Support & Partnerships",
        description=(
            "Welcome to the official internal portal. "
            "Use the menu below to open a ticket in the correct department.\n\n"
            "**⚙️ General Support**\n*Bug reports, technical issues, or player assistance.*\n\n"
            "**💎 Sponsorships**\n*Creator applications and brand collaborations.*\n\n"
            "**Response Time**\nOur administration team usually responds within 12–24 hours."
        ),
        color=discord.Color.blurple(),
        footer="Internal SMP • Help Desk"
    )
    if interaction.guild.icon:
        e.set_thumbnail(url=interaction.guild.icon.url)
    await interaction.channel.send(embed=e, view=TicketPanelView())
    await interaction.response.send_message("✅ Ticket panel sent!", ephemeral=True)


# ── /setup_verification ────────────────────────
@tree.command(name="setup_verification",
              description="Post the verification panel. (Admin)")
@app_commands.describe(channel="Channel to post it in.",
                       verified_role="Role to give verified members.")
async def setup_verification(interaction: discord.Interaction,
                              channel: discord.TextChannel,
                              verified_role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["verification_channel_id"] = channel.id
    config["verified_role_id"] = verified_role.id
    save_config(config)
    e = make_embed(
        title="✅ Verification",
        description=(
            f"Click the button below to verify yourself and gain access.\n\n"
            f"You will receive the **{verified_role.name}** role."
        ),
        color=discord.Color.green(), footer="Click the button to verify."
    )
    await channel.send(embed=e, view=VerifyButton())
    await interaction.response.send_message(
        f"✅ Verification panel posted in {channel.mention}!", ephemeral=True)


# ── /setup_minecraft ───────────────────────────
@tree.command(name="setup_minecraft",
              description="Configure the Minecraft integration. (Admin)")
@app_commands.describe(
    server_ip="Your Minecraft server IP",
    port="Server port (default 25565)",
    status_channel="Voice channel to show live player count",
    events_channel="Text channel for MC event announcements"
)
async def setup_minecraft(interaction: discord.Interaction,
                           server_ip: str, port: int = 25565,
                           status_channel: discord.VoiceChannel = None,
                           events_channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    config["minecraft_server_ip"] = server_ip
    config["minecraft_server_port"] = port
    if status_channel:
        config["minecraft_status_channel_id"] = status_channel.id
    if events_channel:
        config["minecraft_events_channel_id"] = events_channel.id
    save_config(config)
    await interaction.response.send_message(embed=make_embed(
        title="⛏️ Minecraft Integration Configured",
        description=(
            f"**Server IP:** `{server_ip}:{port}`\n"
            f"**Status Channel:** {status_channel.mention if status_channel else 'Not set'}\n"
            f"**Events Channel:** {events_channel.mention if events_channel else 'Not set'}\n\n"
            "Player count updates every 5 minutes."
        ),
        color=discord.Color.green()
    ), ephemeral=True)


# ── /mc_status ─────────────────────────────────
@tree.command(name="mc_status", description="Check the Minecraft server status.")
async def mc_status(interaction: discord.Interaction):
    ip = config.get("minecraft_server_ip", "")
    if not ip:
        await interaction.response.send_message(
            "❌ Not configured. Use `/setup_minecraft`.", ephemeral=True)
        return
    await interaction.response.defer()
    port = config.get("minecraft_server_port", 25565)
    data = await fetch_mc_status(ip, port)

    if not data or not data.get("online"):
        await interaction.followup.send(embed=make_embed(
            title="⛏️ Minecraft Server",
            description=f"`{ip}:{port}` is **offline** or unreachable.",
            color=discord.Color.red()
        ))
        return

    players = data.get("players", {})
    online = players.get("online", 0)
    maximum = players.get("max", 0)
    player_list = players.get("list", [])
    version = data.get("version", "Unknown")
    motd_clean = " ".join(data.get("motd", {}).get("clean", ["No MOTD"]))

    fields = [
        ("🟢 Status", "Online", True),
        ("👥 Players", f"{online}/{maximum}", True),
        ("🔖 Version", version, True),
        ("📝 MOTD", motd_clean, False),
    ]
    if player_list:
        fields.append(("Online Players", ", ".join(player_list[:20]) or "Hidden", False))

    await interaction.followup.send(embed=make_embed(
        title=f"⛏️ {ip}", color=discord.Color.green(), fields=fields
    ))


# ── /mc_event ──────────────────────────────────
@tree.command(name="mc_event", description="Announce a custom Minecraft event. (Staff)")
@app_commands.describe(title="Event title", description="Event details",
                       starts_in="When does it start? e.g. 'Today at 6PM EST'")
async def mc_event(interaction: discord.Interaction, title: str,
                   description: str, starts_in: str):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    ch_id = config.get("minecraft_events_channel_id", 0)
    channel = interaction.guild.get_channel(ch_id) if ch_id else interaction.channel
    e = make_embed(
        title=f"⛏️ Minecraft Event — {title}",
        description=(
            f"{description}\n\n"
            f"⏰ **Starts:** {starts_in}\n"
            f"📣 **Announced by:** {interaction.user.mention}"
        ),
        color=discord.Color.green(), footer="Minecraft Events"
    )
    await channel.send("@everyone", embed=e)
    await interaction.response.send_message("✅ Event announced!", ephemeral=True)


# ── /ping ──────────────────────────────────────
@tree.command(name="ping", description="Check bot latency.")
async def ping(interaction: discord.Interaction):
    ms = round(bot.latency * 1000)
    color = (discord.Color.green() if ms < 100
             else discord.Color.orange() if ms < 200
             else discord.Color.red())
    await interaction.response.send_message(embed=make_embed(
        title="🏓 Pong!", description=f"Websocket latency: **{ms}ms**", color=color
    ))


# ── /userinfo ──────────────────────────────────
@tree.command(name="userinfo", description="Show info about a user.")
@app_commands.describe(member="The member to look up.")
async def userinfo(interaction: discord.Interaction,
                   member: discord.Member = None):
    member = member or interaction.user
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    e = make_embed(
        title=f"👤 {member}", color=member.color,
        thumbnail_url=str(member.display_avatar.url),
        fields=[
            ("ID", str(member.id), True),
            ("Nickname", member.nick or "None", True),
            ("Bot?", "Yes" if member.bot else "No", True),
            ("Joined Server",
             member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "?", True),
            ("Account Created", member.created_at.strftime("%Y-%m-%d"), True),
            ("Top Role", member.top_role.mention, True),
            (f"Roles ({len(roles)})", " ".join(roles[:10]) or "None", False),
        ]
    )
    await interaction.response.send_message(embed=e)


# ── /serverinfo ────────────────────────────────
@tree.command(name="serverinfo", description="Display server information.")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    e = make_embed(
        title=f"🌐 {g.name}", color=discord.Color.blurple(),
        footer=f"ID: {g.id}",
        thumbnail_url=str(g.icon.url) if g.icon else "",
        fields=[
            ("Owner", f"<@{g.owner_id}>", True),
            ("Members", str(g.member_count), True),
            ("Channels", str(len(g.channels)), True),
            ("Roles", str(len(g.roles)), True),
            ("Boost Level", f"Level {g.premium_tier}", True),
            ("Created", g.created_at.strftime("%Y-%m-%d"), True),
        ]
    )
    await interaction.response.send_message(embed=e)


# ── /clear ─────────────────────────────────────
@tree.command(name="clear", description="Bulk delete messages. (Staff)")
@app_commands.describe(amount="1–100 messages to delete.")
async def clear(interaction: discord.Interaction, amount: int):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    if not 1 <= amount <= 100:
        await interaction.response.send_message("❌ Between 1 and 100.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)


# ── /announce ──────────────────────────────────
@tree.command(name="announce", description="Send an announcement embed. (Staff)")
@app_commands.describe(title="Title", message="Body text",
                       channel="Channel to send to")
async def announce(interaction: discord.Interaction, title: str, message: str,
                   channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    target = channel or interaction.channel
    await target.send(embed=make_embed(
        title=f"📢 {title}", description=message,
        color=discord.Color.gold(),
        footer=f"Announced by {interaction.user}"
    ))
    await interaction.response.send_message("✅ Sent!", ephemeral=True)


# ── /lock / /unlock ────────────────────────────
@tree.command(name="lock", description="Lock a channel. (Staff)")
@app_commands.describe(channel="Channel to lock.")
async def lock(interaction: discord.Interaction,
               channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    ow = t.overwrites_for(interaction.guild.default_role)
    ow.send_messages = False
    await t.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message(embed=make_embed(
        title="🔒 Locked", description=f"{t.mention} is now locked.",
        color=discord.Color.red()))

@tree.command(name="unlock", description="Unlock a channel. (Staff)")
@app_commands.describe(channel="Channel to unlock.")
async def unlock(interaction: discord.Interaction,
                 channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    ow = t.overwrites_for(interaction.guild.default_role)
    ow.send_messages = True
    await t.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message(embed=make_embed(
        title="🔓 Unlocked", description=f"{t.mention} is now unlocked.",
        color=discord.Color.green()))


# ── /warn ──────────────────────────────────────
@tree.command(name="warn", description="Warn a user. (Staff)")
@app_commands.describe(member="Member to warn.", reason="Reason.")
async def warn(interaction: discord.Interaction, member: discord.Member,
               reason: str):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    try:
        await member.send(embed=make_embed(
            title="⚠️ Warning",
            description=f"You were warned in **{interaction.guild.name}**.\n**Reason:** {reason}",
            color=discord.Color.yellow()))
    except discord.Forbidden:
        pass
    await log_mod(interaction.guild, "⚠️ Warning", member, reason, interaction.user)
    await interaction.response.send_message(embed=make_embed(
        title="⚠️ Warned",
        description=f"{member.mention} warned.\n**Reason:** {reason}",
        color=discord.Color.yellow()))


# ── /timeout ───────────────────────────────────
@tree.command(name="timeout", description="Timeout a user. (Staff)")
@app_commands.describe(member="Member.", minutes="Duration in minutes.",
                       reason="Reason.")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member,
                      minutes: int, reason: str = "No reason provided"):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    try:
        await member.timeout(datetime.timedelta(minutes=minutes), reason=reason)
        await log_mod(interaction.guild, f"⏱️ Timeout ({minutes}m)",
                      member, reason, interaction.user)
        await interaction.response.send_message(embed=make_embed(
            title="⏱️ Timed Out",
            description=f"{member.mention} timed out for **{minutes}m**.\n**Reason:** {reason}",
            color=discord.Color.orange()))
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)


# ── /kick ──────────────────────────────────────
@tree.command(name="kick", description="Kick a member. (Staff)")
@app_commands.describe(member="Member to kick.", reason="Reason.")
async def kick(interaction: discord.Interaction, member: discord.Member,
               reason: str = "No reason provided"):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    try:
        await member.kick(reason=reason)
        await log_mod(interaction.guild, "👢 Kick", member, reason, interaction.user)
        await interaction.response.send_message(embed=make_embed(
            title="👢 Kicked",
            description=f"{member.mention} was kicked.\n**Reason:** {reason}",
            color=discord.Color.orange()))
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)


# ── /ban ───────────────────────────────────────
@tree.command(name="ban", description="Ban a member. (Admin)")
@app_commands.describe(member="Member to ban.", reason="Reason.")
async def ban(interaction: discord.Interaction, member: discord.Member,
              reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    try:
        await member.ban(reason=reason)
        await log_mod(interaction.guild, "🔨 Ban", member, reason, interaction.user)
        await interaction.response.send_message(embed=make_embed(
            title="🔨 Banned",
            description=f"{member.mention} was banned.\n**Reason:** {reason}",
            color=discord.Color.red()))
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)


# ── /unban ─────────────────────────────────────
@tree.command(name="unban", description="Unban a user by ID. (Admin)")
@app_commands.describe(user_id="User ID to unban.", reason="Reason.")
async def unban(interaction: discord.Interaction, user_id: str,
                reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        await interaction.response.send_message(embed=make_embed(
            title="✅ Unbanned",
            description=f"**{user}** has been unbanned.",
            color=discord.Color.green()))
    except (discord.NotFound, ValueError):
        await interaction.response.send_message(
            "❌ User not found or not banned.", ephemeral=True)


# ── /slowmode ──────────────────────────────────
@tree.command(name="slowmode", description="Set slowmode in a channel. (Staff)")
@app_commands.describe(seconds="Slowmode seconds (0 to disable).",
                       channel="Target channel.")
async def slowmode(interaction: discord.Interaction, seconds: int,
                   channel: discord.TextChannel = None):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    t = channel or interaction.channel
    await t.edit(slowmode_delay=seconds)
    label = f"{seconds}s" if seconds > 0 else "disabled"
    await interaction.response.send_message(embed=make_embed(
        title="🐢 Slowmode",
        description=f"Slowmode in {t.mention} set to **{label}**.",
        color=discord.Color.blurple()))


# ── /role_add / /role_remove ───────────────────
@tree.command(name="role_add", description="Add a role to a user. (Staff)")
@app_commands.describe(member="Member.", role="Role to add.")
async def role_add(interaction: discord.Interaction, member: discord.Member,
                   role: discord.Role):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    await member.add_roles(role)
    await interaction.response.send_message(embed=make_embed(
        title="✅ Role Added",
        description=f"Added {role.mention} to {member.mention}.",
        color=discord.Color.green()))

@tree.command(name="role_remove", description="Remove a role from a user. (Staff)")
@app_commands.describe(member="Member.", role="Role to remove.")
async def role_remove(interaction: discord.Interaction, member: discord.Member,
                      role: discord.Role):
    if not is_staff(interaction):
        await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        return
    await member.remove_roles(role)
    await interaction.response.send_message(embed=make_embed(
        title="✅ Role Removed",
        description=f"Removed {role.mention} from {member.mention}.",
        color=discord.Color.orange()))


# ── /setup_counter ─────────────────────────────
@tree.command(name="setup_counter",
              description="Create live member counter channel. (Admin)")
async def setup_counter(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
    }
    online = sum(1 for m in guild.members
                 if m.status != discord.Status.offline and not m.bot)
    ch = await guild.create_voice_channel(f"🟢 Online: {online}",
                                           overwrites=overwrites)
    config["member_counter_channel_id"] = ch.id
    save_config(config)
    await interaction.response.send_message(embed=make_embed(
        title="📈 Counter Created",
        description=f"{ch.mention} will update every 5 minutes.",
        color=discord.Color.green()), ephemeral=True)


# ── /config_view ───────────────────────────────
@tree.command(name="config_view", description="View bot config. (Admin)")
async def config_view(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    display = "\n".join(f"`{k}`: `{v}`" for k, v in config.items()
                        if k not in ("banned_words",))
    await interaction.response.send_message(embed=make_embed(
        title="⚙️ Config", description=display,
        color=discord.Color.blurple()), ephemeral=True)


# ══════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"[BOT] {bot.user} ready.")
    bot.add_view(TicketPanelView())
    bot.add_view(TicketManageView())
    bot.add_view(VerifyButton())
    try:
        synced = await tree.sync()
        print(f"[SLASH] Synced {len(synced)} commands.")
    except Exception as e:
        print(f"[SLASH] Error: {e}")
    if not update_member_counter.is_running():
        update_member_counter.start()
    if not update_mc_counter.is_running():
        update_mc_counter.start()
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching,
                                  name="the server ⚔️"),
        status=discord.Status.online
    )

@bot.event
async def on_message(message: discord.Message):
    await handle_automod(message)
    await bot.process_commands(message)

@bot.event
async def on_app_command_error(interaction: discord.Interaction,
                                error: app_commands.AppCommandError):
    msg = "An unexpected error occurred."
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ You lack permission for this."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "❌ I'm missing required permissions."
    try:
        await interaction.response.send_message(embed=make_embed(
            title="Error", description=msg,
            color=discord.Color.red()), ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(embed=make_embed(
            title="Error", description=msg,
            color=discord.Color.red()), ephemeral=True)


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════

if __name__ == "__main__":
    if not TOKEN:
        print("[ERROR] No DISCORD_TOKEN in .env")
    else:
        bot.run(TOKEN)
