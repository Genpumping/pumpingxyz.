import os, re, asyncio, httpx, discord
from dotenv import load_dotenv
load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

TICKET_NAME_PATTERNS = [r'^ticket[-_]',r'^ticket\d+',r'[-_]ticket[-_\d]',r'^modmail[-_]',r'^support[-_]',r'^case[-_]',r'^help[-_]',r'^request[-_]',r'^\d+-\w+',r'^[a-zA-Z][^-]*-[^-]+-\d{3,}$']

def is_ticket_channel(name):
    return any(re.search(p, name.lower()) for p in TICKET_NAME_PATTERNS)

def extract_user_id(text):
    m = re.search(r'<@!?(\d{17,19})>', text)
    if m: return m.group(1)
    m = re.search(r'\b(\d{17,19})\b', text)
    return m.group(1) if m else None

def extract_username_from_channel(channel_name):
    name = channel_name.lower()
    name = re.sub(r'^ticket[-_]', '', name)
    name = re.sub(r'^support[-_]', '', name)
    name = re.sub(r'-\d+$', '', name)
    name = re.sub(r'^\d+[-_]', '', name)
    if re.match(r'^\d+$', name) or name == '':
        return None
    return name

async def fetch_user_http(user_id):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"https://discord.com/api/v10/users/{user_id}",
                headers={"Authorization": DISCORD_USER_TOKEN},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                username = data.get("global_name") or data.get("username")
                is_bot = data.get("bot", False)
                return str(user_id), username, is_bot
    except Exception:
        pass
    return str(user_id), None, None

async def get_creator_from_overwrites(channel):
    member_ids = []
    try:
        for target, overwrite in channel.overwrites.items():
            tid = getattr(target, 'id', None)
            if not tid: continue
            if isinstance(target, discord.Member):
                if not target.bot and target.id != client.user.id:
                    return str(target.id), target.name
            elif isinstance(target, discord.Role):
                continue
            else:
                if tid != client.user.id:
                    member_ids.append(tid)
    except Exception:
        pass
    for uid in member_ids:
        uid_str, username, is_bot = await fetch_user_http(uid)
        if not is_bot and username:
            return uid_str, username
    return None, None

def get_user_id_from_topic(channel):
    try:
        topic = getattr(channel, 'topic', None) or ''
        if topic:
            found = extract_user_id(topic)
            if found: return found
    except Exception:
        pass
    return None

async def resolve_from_history(channel):
    try:
        await asyncio.sleep(6)
        async for message in channel.history(limit=15, oldest_first=True):
            if message.author.id == client.user.id: continue
            if not message.author.bot:
                return str(message.author.id), message.author.name
            found = extract_user_id(message.content)
            if found: return found, None
            for e in message.embeds:
                text = " ".join(filter(None, [e.description or "", e.title or "", " ".join(f.value for f in e.fields)]))
                found = extract_user_id(text)
                if found: return found, None
    except Exception:
        pass
    return None, None

async def send_to_telegram(text):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
                timeout=10,
            )
            if r.status_code != 200:
                print(f"[Telegram error] {r.status_code}: {r.text}")
    except Exception as e:
        print(f"[Telegram error] {e}")

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

async def process_ticket(guild, channel, channel_name, channel_id):
    try:
        user_id, username = None, None

        user_id, username = await get_creator_from_overwrites(channel)

        if not user_id:
            user_id = get_user_id_from_topic(channel)

        if not user_id:
            h_id, h_name = await resolve_from_history(channel)
            if h_id:
                user_id = h_id
                username = h_name

        if not username:
            username = extract_username_from_channel(channel_name)

        if user_id and not username:
            _, username, _ = await fetch_user_http(user_id)

        status = f"{username or 'Unknown'} ({user_id or '?'})"
        print(f"[ticket] {guild.name} → #{channel_name} | {status}")
        await send_to_telegram(build_message(guild.name, channel_name, channel_id, user_id, username))

    except Exception as e:
        print(f"[ticket error] {guild.name} → #{channel_name} | {e}")
        await send_to_telegram(build_message(guild.name, channel_name, channel_id))

client = discord.Client()

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user} | Watching {len(client.guilds)} servers")

@client.event
async def on_guild_channel_create(channel):
    if not hasattr(channel, 'guild') or not is_ticket_channel(channel.name): return
    await process_ticket(channel.guild, channel, channel.name, channel.id)

@client.event
async def on_thread_create(thread):
    if not thread.guild or not is_ticket_channel(thread.name): return
    await process_ticket(thread.guild, thread, thread.name, thread.id)

client.run(DISCORD_USER_TOKEN)
