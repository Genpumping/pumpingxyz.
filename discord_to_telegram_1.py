import os, re, asyncio, httpx, discord
from dotenv import load_dotenv
load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

TICKET_BOT_NAMES = ["ticket tool","ticketbot","ticket bot","modmail","helper.gg","support bot","ticket manager","tickets","helper","carl-bot","dyno"]
TICKET_NAME_PATTERNS = [r'^ticket[-_]',r'^ticket\d+',r'[-_]ticket[-_\d]',r'^modmail[-_]',r'^support[-_]',r'^case[-_]',r'^help[-_]',r'^request[-_]']

pending_tickets = {}

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

async def send_to_telegram(text):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}, timeout=10)
        print("[Telegram] ✅ Sent" if r.status_code == 200 else f"[Telegram error] {r.status_code}: {r.text}")

def build_message(guild_name, channel_name, channel_id, user_id=None, username=None):
    profile = f"https://discord.com/users/{user_id}" if user_id else "N/A"
    return (f"🎫 <b>New Ticket Detected in {guild_name}</b>\nChannel: <b>{channel_name}</b>\nID: <code>{channel_id}</code>\nCreator ID: <code>{user_id or 'Unknown'}</code>\nUsername: <b>{username or 'Unknown'}</b>\nProfile: {profile}")

async def resolve(guild, user_id):
    if not user_id: return None
    try:
        m = guild.get_member(int(user_id))
        if m: return m.name
    except: pass
    return None

async def process_ticket(guild, channel_id, channel_name, user_id=None, username=None):
    # Try resolving username from guild members
    if user_id and not username:
        username = await resolve(guild, user_id)
    # Try extracting from channel name e.g. ticket-2305-ledgers
    if not username:
        username = extract_username_from_channel(channel_name)
    print(f"  → Sending: {channel_name} | {username or 'Unknown'} ({user_id or '?'})")
    await send_to_telegram(build_message(guild.name, channel_name, channel_id, user_id, username))

async def scan_channel_history(channel, guild):
    """Scan the first few messages in a new ticket channel to find creator."""
    await asyncio.sleep(5)  # wait for bot to post welcome message
    try:
        user_id = None
        username = None
        async for message in channel.history(limit=10, oldest_first=True):
            # Check message content for mentions
            found = extract_user_id(message.content)
            if found:
                user_id = found
                break
            # Check embeds
            for e in message.embeds:
                text = " ".join(filter(None, [e.description, e.title, " ".join(f.value for f in e.fields)]))
                found = extract_user_id(text)
                if found:
                    user_id = found
                    break
            if user_id:
                break
            # If message is from a real user (not a bot), that's the creator
            if not message.author.bot:
                user_id = str(message.author.id)
                username = message.author.name
                break
        return user_id, username
    except Exception as e:
        print(f"  [scan error] {e}")
        return None, None

client = discord.Client()

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}\n   Watching ALL {len(client.guilds)} servers")

@client.event
async def on_guild_channel_create(channel):
    if not hasattr(channel, 'guild') or not is_ticket_channel(channel.name): return
    guild = channel.guild
    print(f"[new ticket channel] {guild.name} → #{channel.name}")

    # Scan channel history to find creator
    user_id, username = await scan_channel_history(channel, guild)

    # Mark as handled so on_message doesn't double-send
    pending_tickets[channel.id] = {"resolved": True}
    await process_ticket(guild, channel.id, channel.name, user_id, username)

@client.event
async def on_thread_create(thread):
    if not thread.guild or not is_ticket_channel(thread.name): return
    print(f"[new ticket thread] {thread.guild.name} → {thread.name}")
    user_id, username = await scan_channel_history(thread, thread.guild)
    pending_tickets[thread.id] = {"resolved": True}
    await process_ticket(thread.guild, thread.id, thread.name, user_id, username)

@client.event
async def on_message(message):
    if not message.guild or message.channel.id not in pending_tickets: return
    if pending_tickets[message.channel.id].get("resolved"): return
    if not is_ticket_bot(message.author): return
    t = pending_tickets.pop(message.channel.id)
    user_id = extract_user_id(message.content)
    if not user_id and message.embeds:
        for e in message.embeds:
            text = " ".join(filter(None, [e.description, e.title]))
            user_id = extract_user_id(text)
            if user_id: break
    await process_ticket(message.guild, message.channel.id, message.channel.name, user_id)

client.run(DISCORD_USER_TOKEN)
