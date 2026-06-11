import os, re, asyncio, httpx, discord
from dotenv import load_dotenv
load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

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
    Handles all formats:
      ticket-j0h_1-3611  → j0h_1
      ticket-2324-murongg → murongg
      ticket-reza_abbas   → reza_abbas
      ticket-16780        → None
    """
    name = channel_name.lower()
    # Remove leading ticket- or ticket_
    name = re.sub(r'^ticket[-_]', '', name)
    # Remove trailing -number or _number (ticket number at end)
    name = re.sub(r'[-_]\d+$', '', name)
    # Remove leading number- or number_ (ticket number at start)
    name = re.sub(r'^\d+[-_]', '', name)
    # If purely digits or empty, no username
    if re.match(r'^\d+$', name) or name == '':
        return None
    return name

async def fetch_username_http(user_id):
    """Fetch username directly via Discord HTTP API."""
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
                print(f"  [http-fetch] {user_id} → {username}")
                return username
            else:
                print(f"  [http-fetch error] {r.status_code}: {r.text}")
    except Exception as e:
        print(f"  [http-fetch exception] {e}")
    return None

async def resolve_from_history(channel):
    try:
        await asyncio.sleep(6)
        async for message in channel.history(limit=15, oldest_first=True):
            # Skip selfbot's own messages
            if message.author.id == client.user.id:
                continue
            if not message.author.bot:
                return str(message.author.id), message.author.name
            found = extract_user_id(message.content)
            if found: return found, None
            for e in message.embeds:
                text = " ".join(filter(None, [e.description or "", e.title or "", " ".join(f.value for f in e.fields)]))
                found = extract_user_id(text)
                if found: return found, None
    except Exception as ex:
        print(f"  [history error] {ex}")
    return None, None

async def send_to_telegram(text):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
                timeout=10,
            )
            print("[Telegram] ✅ Sent" if r.status_code == 200 else f"[Telegram error] {r.status_code}: {r.text}")
    except Exception as e:
        print(f"  [telegram error] {e}")

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

        # Method 1: permission overwrites (exclude self)
        try:
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Member) and not target.bot and target.id != client.user.id:
                    user_id = str(target.id)
                    username = target.name if target.name else None
                    break
            print(f"  [method1-overwrites] id={user_id} name={username}")
        except Exception as e:
            print(f"  [overwrites error] {e}")

        # Method 2: scan channel history
        if not user_id:
            h_id, h_name = await resolve_from_history(channel)
            print(f"  [method2-history] id={h_id} name={h_name}")
            if h_id:
                user_id = h_id
                username = h_name

        # Method 3: extract from channel name
        if not username:
            username = extract_username_from_channel(channel_name)
            print(f"  [method3-channelname] name={username}")

        # Method 4: fetch username via HTTP API
        if user_id and not username:
            username = await fetch_username_http(user_id)

        print(f"  → Final: {channel_name} | {username or 'Unknown'} ({user_id or '?'})")
        await send_to_telegram(build_message(guild.name, channel_name, channel_id, user_id, username))

    except Exception as e:
        print(f"  [CRASH] {e}")
        await send_to_telegram(build_message(guild.name, channel_name, channel_id))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user} (id={client.user.id})\n   Watching ALL {len(client.guilds)} servers")

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
