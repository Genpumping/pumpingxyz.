import os, re, asyncio, httpx, discord
from dotenv import load_dotenv
load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

TICKET_BOT_NAMES = ["ticket tool","ticketbot","ticket bot","modmail","helper.gg","support bot","ticket manager","tickets","helper","carl-bot","dyno","mee6"]
TICKET_NAME_PATTERNS = [r'^ticket[-_]',r'^ticket\d+',r'[-_]ticket[-_\d]',r'^modmail[-_]',r'^support[-_]',r'^case[-_]',r'^help[-_]',r'^request[-_]']

def is_ticket_channel(name):
    return any(re.search(p, name.lower()) for p in TICKET_NAME_PATTERNS)

def extract_user_id(text):
    m = re.search(r'<@!?(\d{17,19})>', text)
    if m: return m.group(1)
    m = re.search(r'\b(\d{17,19})\b', text)
    return m.group(1) if m else None

def extract_username_from_channel(channel_name):
    """
    Extracts username from channel names like:
      ticket-2305-slugger2383  → slugger2383
      ticket-2321-h0s7r        → h0s7r
      ticket-reza_abbas        → reza_abbas
      ticket-16712             → None (pure number, no username)
    Strategy: strip the leading 'ticket' and any pure ticket-number segment,
    then join the rest as the username (preserving underscores).
    """
    name = channel_name.lower()
    # Remove leading 'ticket-' or 'ticket_'
    name = re.sub(r'^ticket[-_]', '', name)
    # If what's left starts with a pure number followed by a separator, remove it
    name = re.sub(r'^\d+[-_]', '', name)
    # If the remainder is purely digits, there's no username
    if re.match(r'^\d+$', name) or name == '':
        return None
    return name

def get_creator_from_overwrites(channel):
    try:
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and not target.bot:
                return str(target.id), target.name
    except Exception as e:
        print(f"  [overwrite error] {e}")
    return None, None

async def fetch_username_from_api(user_id):
    try:
        user = await client.fetch_user(int(user_id))
        if user: return user.name
    except Exception as e:
        print(f"  [fetch_user error] {e}")
    return None

async def search_member_by_name(guild, username):
    """Search guild members by username to find their ID."""
    try:
        # Check cache first (exact match)
        for member in guild.members:
            if member.name.lower() == username.lower():
                return str(member.id), member.name
            if member.display_name.lower() == username.lower():
                return str(member.id), member.display_name
        # Search via Discord API
        members = await guild.query_members(query=username, limit=10)
        for member in members:
            if member.name.lower() == username.lower():
                return str(member.id), member.name
            if member.display_name.lower() == username.lower():
                return str(member.id), member.display_name
        # Partial match fallback
        if members:
            return str(members[0].id), members[0].name
    except Exception as e:
        print(f"  [search_member error] {e}")
    return None, username

async def send_to_telegram(text):
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        print("[Telegram] ✅ Sent" if r.status_code == 200 else f"[Telegram error] {r.status_code}: {r.text}")

def build_message(guild_name, channel_name, channel_id, user_id=None, username=None):
    profile = f"https://discord.com/users/{user_id}" if user_id else "N/A"
    return (
        f"🎫 <b>New Ticket Detected in {guild_name}</b>\n"
        f"Channel: <b>{channel_name}</b>\n"
        f"ID: <code>{channel_id}</code>\n"
        f"Creator ID: <code>{user_id or 'Unknown'}</code>\n"
        f"Username: <b>{username or 'Unknown'}</b>\n"
        f"Profile: {profile}"
    )

async def resolve_from_history(channel):
    try:
        async for message in channel.history(limit=10, oldest_first=True):
            if not message.author.bot:
                return str(message.author.id), message.author.name
            found = extract_user_id(message.content)
            if found: return found, None
            for e in message.embeds:
                text = " ".join(filter(None, [e.description, e.title, " ".join(f.value for f in e.fields)]))
                found = extract_user_id(text)
                if found: return found, None
    except Exception as ex:
        print(f"  [history error] {ex}")
    return None, None

async def process_ticket(guild, channel, channel_name, channel_id):
    user_id, username = None, None

    # Method 1: permission overwrites (most reliable)
    user_id, username = get_creator_from_overwrites(channel)

    # Method 2: scan channel history
    if not user_id:
        await asyncio.sleep(5)
        user_id, username = await resolve_from_history(channel)

    # Method 3: extract username from channel name
    if not username:
        username = extract_username_from_channel(channel_name)

    # Method 4: if username found but no ID, search guild members
    if username and not user_id:
        user_id, username = await search_member_by_name(guild, username)

    # Method 5: if ID found but no username, fetch from Discord API
    if user_id and not username:
        username = await fetch_username_from_api(user_id)

    print(f"  → Final: {channel_name} | {username or 'Unknown'} ({user_id or '?'})")
    await send_to_telegram(build_message(guild.name, channel_name, channel_id, user_id, username))

client = discord.Client()

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}\n   Watching ALL {len(client.guilds)} servers")

@client.event
async def on_guild_channel_create(channel):
    if not hasattr(channel, 'guild') or not is_ticket_channel(channel.name): return
    print(f"[new ticket channel] {channel.guild.name} → #{channel.name}")
    await process_ticket(channel.guild, channel, channel.name, channel.id)

@client.event
async def on_thread_create(thread):
    if not thread.guild or not is_ticket_channel(thread.name): return
    print(f"[new ticket thread] {thread.guild.name} → {thread.name}")
    await process_ticket(thread.guild, thread, thread.name, thread.id)

client.run(DISCORD_USER_TOKEN)
