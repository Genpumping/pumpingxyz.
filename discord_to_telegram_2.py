import os, re, asyncio, httpx, discord
from dotenv import load_dotenv
load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

TICKET_BOT_NAMES = ["ticket tool","ticketbot","ticket bot","modmail","helper.gg","support bot","ticket manager","tickets","helper","carl-bot","dyno"]
TICKET_NAME_PATTERNS = [r'^ticket[-_]',r'^ticket\d+',r'[-_]ticket[-_\d]',r'^modmail[-_]',r'^support[-_]',r'^case[-_]',r'^help[-_]',r'^request[-_]']

def is_ticket_channel(name):
    return any(re.search(p, name.lower()) for p in TICKET_NAME_PATTERNS)

def is_ticket_bot(author):
    return author.bot and any(b in author.name.lower() for b in TICKET_BOT_NAMES)

def extract_user_id(text):
    m = re.search(r'<@!?(\d{17,19})>', text)
    if m: return m.group(1)
    m = re.search(r'\b(\d{17,19})\b', text)
    return m.group(1) if m else None

def extract_username_from_channel(channel_name):
    parts = re.split(r'[-_]', channel_name)
    text_parts = [p for p in parts[1:] if not p.isdigit() and len(p) > 1]
    return text_parts[-1] if text_parts else None

def get_creator_from_overwrites(channel, guild):
    """
    Ticket bots always give the ticket creator permission overwrites on the channel.
    We scan those overwrites to find non-bot guild members — that's the creator.
    """
    try:
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and not target.bot:
                return str(target.id), target.name
    except Exception as e:
        print(f"  [overwrite error] {e}")
    return None, None

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
    """Scan first 10 messages for user mentions or real user messages."""
    try:
        async for message in channel.history(limit=10, oldest_first=True):
            # Real user sent a message — that's the creator
            if not message.author.bot:
                return str(message.author.id), message.author.name
            # Bot message with a user mention
            found = extract_user_id(message.content)
            if found: return found, None
            for e in message.embeds:
                text = " ".join(filter(None, [
                    e.description, e.title,
                    " ".join(f.value for f in e.fields)
                ]))
                found = extract_user_id(text)
                if found: return found, None
    except Exception as ex:
        print(f"  [history error] {ex}")
    return None, None

async def process_ticket(guild, channel, channel_name, channel_id):
    user_id, username = None, None

    # Method 1: permission overwrites (fastest, works for all ticket bots)
    user_id, username = get_creator_from_overwrites(channel, guild)
    print(f"  [overwrites] user_id={user_id} username={username}")

    # Method 2: scan channel message history
    if not user_id:
        await asyncio.sleep(5)
        user_id, username = await resolve_from_history(channel)
        print(f"  [history] user_id={user_id} username={username}")

    # Method 3: extract from channel name e.g. ticket-2305-ledgers
    if not username:
        username = extract_username_from_channel(channel_name)

    # Method 4: resolve username from guild member cache
    if user_id and not username:
        try:
            m = guild.get_member(int(user_id))
            if m: username = m.name
        except: pass

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
