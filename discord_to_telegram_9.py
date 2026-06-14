import os, re, asyncio, httpx, discord
from dotenv import load_dotenv
load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

TICKET_BOT_NAMES = ["ticket tool","ticketbot","ticket bot","modmail","helper.gg","support bot","ticket manager","tickets","tickets v2","tickets.bot","helper","carl-bot","dyno","mee6","fluent","solayer","lightchain","canton","metacrm","mint official","master of rules"]
TICKET_NAME_PATTERNS = [r'^ticket[-_]',r'^ticket\d+',r'[-_]ticket[-_\d]',r'^modmail[-_]',r'^support[-_]',r'^case[-_]',r'^help[-_]',r'^request[-_]',r'^\d+-\w+',r'^[a-zA-Z][^-]*-[^-]+-\d{3,}$']

pending_channels = {}

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

def extract_ticket_name_from_message(message):
    full_text = message.content or ""
    if message.embeds:
        for e in message.embeds:
            full_text += " " + " ".join(filter(None, [e.description or "", e.title or "", " ".join(f.value for f in e.fields)]))
    m = re.search(r'#\s*([a-zA-Z0-9][a-zA-Z0-9_-]{2,})', full_text)
    if m:
        name = m.group(1).strip().lower()
        if is_ticket_channel(name):
            return name
    m = re.search(r'(?:opened|open).*?ticket[:\s]+([a-zA-Z0-9][a-zA-Z0-9_-]{2,})', full_text, re.IGNORECASE)
    if m:
        name = m.group(1).strip().lower()
        if is_ticket_channel(name):
            return name
    return None

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
            else:
                print(f"[fetch error] {r.status_code} for {user_id}: {r.text}")
    except Exception as e:
        print(f"[fetch error] {e}")
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
        pending_channels[channel_id] = {
            "guild_name": guild.name,
            "channel_name": channel_name,
            "channel_id": channel_id,
            "sent": False
        }

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

      if user_id:
            if channel_id in pending_channels and not pending_channels[channel_id]["sent"]:
                pending_channels[channel_id]["sent"] = True
                pending_channels.pop(channel_id)
                print(f"[ticket] {guild.name} → #{channel_name} | {username or 'Unknown'} ({user_id})")
                await send_to_telegram(build_message(guild.name, channel_name, channel_id, user_id, username))
        else:
            await asyncio.sleep(20)
            if channel_id in pending_channels and not pending_channels[channel_id]["sent"]:
                pending_channels[channel_id]["sent"] = True
                pending_channels.pop(channel_id)
                print(f"[ticket-fallback] {guild.name} → #{channel_name} | Unknown")
                await send_to_telegram(build_message(guild.name, channel_name, channel_id))

    except Exception as e:
        print(f"[ticket error] {guild.name} → #{channel_name} | {e}")
        if channel_id in pending_channels:
            pending_channels.pop(channel_id)
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

@client.event
async def on_message(message):
    if not message.guild: return
    if not message.author.bot: return
    if not any(b in message.author.name.lower() for b in TICKET_BOT_NAMES): return

    channel_id = message.channel.id

    # Case 1: Message inside a ticket channel we're tracking
    if channel_id in pending_channels and not pending_channels[channel_id]["sent"]:
        user_id = extract_user_id(message.content)
        if not user_id and message.embeds:
            for e in message.embeds:
                text = " ".join(filter(None, [e.description or "", e.title or "", " ".join(f.value for f in e.fields)]))
                user_id = extract_user_id(text)
                if user_id: break
        if user_id:
            ticket = pending_channels[channel_id]
            ticket["sent"] = True
            pending_channels.pop(channel_id)
            _, username, _ = await fetch_user_http(user_id)
            if not username:
                username = extract_username_from_channel(ticket["channel_name"])
            print(f"[ticket-msg] {message.guild.name} → #{ticket['channel_name']} | {username} ({user_id})")
            await send_to_telegram(build_message(ticket["guild_name"], ticket["channel_name"], channel_id, user_id, username))
            return

    # Case 2: Confirmation message sent to user
    # e.g. "Ticket Created # ticket-885" or "you created a ticket # 10892-r0bbott"
    ticket_name = extract_ticket_name_from_message(message)
    if not ticket_name: return

    for cid, ticket in list(pending_channels.items()):
        if ticket["channel_name"].lower() == ticket_name and not ticket["sent"]:
            ticket["sent"] = True
            pending_channels.pop(cid)
            user_id = str(client.user.id)
            _, fetched_name, _ = await fetch_user_http(user_id)
            username = fetched_name or extract_username_from_channel(ticket_name) or client.user.name
            print(f"[ticket-confirm] {message.guild.name} → #{ticket_name} | {username} ({user_id})")
            await send_to_telegram(build_message(ticket["guild_name"], ticket["channel_name"], cid, user_id, username))
            return

client.run(DISCORD_USER_TOKEN)
