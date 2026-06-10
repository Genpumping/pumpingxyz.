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

async def process_ticket(guild, channel_id, channel_name, user_id=None, username=None):
    if user_id and not username:
        try:
            m = guild.get_member(int(user_id))
            if m: username = m.name
        except: pass
    if not username:
        username = extract_username_from_channel(channel_name)
    print(f"  → Sending: {channel_name} | {username or 'Unknown'} ({user_id or '?'})")
    await send_to_telegram(build_message(guild.name, channel_name, channel_id, user_id, username))

client = discord.Client()

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}\n   Watching ALL {len(client.guilds)} servers")

@client.event
async def on_guild_channel_create(channel):
    if not hasattr(channel, 'guild') or not is_ticket_channel(channel.name): return
    print(f"[new ticket channel] {channel.guild.name} → #{channel.name}")
    pending_tickets[channel.id] = {"guild": channel.guild, "channel_name": channel.name, "channel_id": channel.id}
    await asyncio.sleep(10)
    if channel.id in pending_tickets:
        t = pending_tickets.pop(channel.id)
        await process_ticket(t["guild"], t["channel_id"], t["channel_name"])

@client.event
async def on_thread_create(thread):
    if not thread.guild or not is_ticket_channel(thread.name): return
    print(f"[new ticket thread] {thread.guild.name} → {thread.name}")
    pending_tickets[thread.id] = {"guild": thread.guild, "channel_name": thread.name, "channel_id": thread.id}
    await asyncio.sleep(10)
    if thread.id in pending_tickets:
        t = pending_tickets.pop(thread.id)
        await process_ticket(t["guild"], t["channel_id"], t["channel_name"])

@client.event
async def on_message(message):
    if not message.guild or message.channel.id not in pending_tickets or not is_ticket_bot(message.author): return
    t = pending_tickets.pop(message.channel.id)
    user_id = extract_user_id(message.content)
    if not user_id and message.embeds:
        for e in message.embeds:
            text = " ".join(filter(None, [e.description, e.title]))
            user_id = extract_user_id(text)
            if user_id: break
    await process_ticket(t["guild"], t["channel_id"], t["channel_name"], user_id)

client.run(DISCORD_USER_TOKEN)
