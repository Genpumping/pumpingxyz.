import asyncio
import os
import re
import html
import logging
import discord
import httpx
from dotenv import load_dotenv

load_dotenv()

DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

TICKET_BOT_NAMES = [
    "ticket tool",
    "ticketbot",
    "ticket bot",
    "modmail",
    "helper.gg",
    "support bot",
    "ticket manager",
    "tickets",
    "tickets v2",
    "tickets.bot",
    "helper",
    "carl-bot",
    "dyno",
    "mee6",
    "fluent",
    "solayer",
    "lightchain",
    "canton",
    "metacrm",
    "mint official",
    "master of rules",
    "extendio",
]

TICKET_NAME_PATTERNS = [
    r"^ticket[-_]",
    r"^ticket\d+",
    r"[-_]ticket[-_\d]",
    r"^modmail[-_]",
    r"^support[-_]",
    r"^case[-_]",
    r"^help[-_]",
    r"^request[-_]",
    r"^\d+-\w+",
    r"^[a-zA-Z][^-]*-[^-]+-\d{3,}$",
]

pending_channels: dict = {}
http_client: httpx.AsyncClient | None = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ticket-watcher")

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True  

def is_ticket_channel(name: str) -> bool:
    if not name:
        return False
    lname = name.lower()
    return any(re.search(p, lname) for p in TICKET_NAME_PATTERNS)

def extract_user_id(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"<@!?(\d{17,19})>", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{17,19})\b", text)
    return m.group(1) if m else None

def extract_username_from_channel(channel_name: str | None) -> str | None:
    if not channel_name:
        return None
    name = channel_name.lower()
    name = re.sub(r"^ticket[-_]", "", name)
    name = re.sub(r"^support[-_]", "", name)
    name = re.sub(r"-\d+$", "", name)
    name = re.sub(r"^\d+[-_]", "", name)
    name = name.strip()
    if re.match(r"^\d+$", name) or name == "":
        return None
    return name

def extract_embed_text(embed) -> str:
    fields_text = " ".join(getattr(f, "value", "") for f in getattr(embed, "fields", []))
    return " ".join(filter(None, [getattr(embed, "description", "") or "", getattr(embed, "title", "") or "", fields_text]))

def extract_ticket_name_from_message(message) -> str | None:
    full_text = message.content or ""
    if getattr(message, "embeds", None):
        for e in message.embeds:
            full_text += " " + extract_embed_text(e)

    m = re.search(r"#\s*([a-zA-Z0-9][a-zA-Z0-9_-]{2,})", full_text)
    if m:
        name = m.group(1).strip().lower()
        if is_ticket_channel(name):
            return name

    m = re.search(r"(?:opened|open).*?ticket[:\s]+([a-zA-Z0-9][a-zA-Z0-9_-]{2,})", full_text, re.IGNORECASE)
    if m:
        name = m.group(1).strip().lower()
        if is_ticket_channel(name):
            return name

    if "we have created a private thread" in full_text.lower():
        return "extendio-thread"

    return None

async def fetch_user_http(user_id: str):
    
    try:
        cu = getattr(client, "user", None)
        if cu is not None and str(user_id) == str(getattr(cu, "id", None)):
            return str(user_id), cu.name, False
    except Exception:
        pass

    try:
        user = await client.fetch_user(int(user_id))
        if user:
            return str(user_id), user.name, getattr(user, "bot", False)
    except Exception as e:
        log.debug(f"[fetch error] {e}")
    return str(user_id), None, None

async def get_creator_from_overwrites(channel):
    member_ids: list = []
    try:
        if hasattr(channel, "overwrites"):
            for target, _ in getattr(channel, "overwrites", {}).items():
                tid = getattr(target, "id", None)
                if not tid:
                    continue
                if isinstance(target, discord.Member):
                    if not getattr(target, "bot", False) and getattr(target, "id", None) != getattr(client.user, "id", None):
                        return str(target.id), target.name
                elif isinstance(target, discord.Role):
                    continue
                else:
                    # Some APIs provide a User-like object or an id-like object
                    if tid != getattr(client.user, "id", None):
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
        topic = getattr(channel, "topic", None) or ""
        if topic:
            found = extract_user_id(topic)
            if found:
                return found
    except Exception:
        pass
    return None

async def resolve_from_history(channel):
    try:
        # small delay to allow plugins/bots to post their join message
        await asyncio.sleep(3)
        async for message in channel.history(limit=15, oldest_first=True):
            # be defensive: message.author or client.user might be None
            if getattr(message, "author", None) is None:
                continue
            if getattr(message.author, "id", None) == getattr(client.user, "id", None):
                continue
            if not getattr(message.author, "bot", False):
                return str(message.author.id), message.author.name
            found = extract_user_id(message.content)
            if found:
                return found, None
            for e in getattr(message, "embeds", []):
                text = extract_embed_text(e)
                found = extract_user_id(text)
                if found:
                    return found, None
    except Exception:
        pass
    return None, None

async def send_to_telegram(text: str):
    global http_client
    # create or re-create client if needed (some httpx versions may not have is_closed)
    try:
        closed = http_client is None or getattr(http_client, "is_closed", False)
    except Exception:
        closed = http_client is None
    if closed:
        http_client = httpx.AsyncClient()

    try:
        r = await http_client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.warning(f"[Telegram error] {r.status_code}: {r.text}")
    except Exception as e:
        log.warning(f"[Telegram error] {e}")

def build_message(guild_name, channel_name, channel_id, user_id=None, username=None):
    # Escape inputs for HTML mode
    g = html.escape(str(guild_name or "Unknown"))
    ch = html.escape(str(channel_name or "Unknown"))
    uid = html.escape(str(user_id)) if user_id else "Unknown"
    usr = html.escape(str(username or "Unknown"))
    profile = f"https://discord.com/users/{html.escape(str(user_id))}" if user_id else "N/A"
    return (
        f"🎫 <b>New Ticket Detected in {g}</b>\n"
        f"Channel: <b>{ch}</b>\n"
        f"ID: <code>{html.escape(str(channel_id))}</code>\n"
        f"Creator ID: <code>{uid}</code>\n"
        f"Username: <b>{usr}</b>\n"
        f"Profile: {profile}"
    )

async def process_ticket(guild, channel, channel_name, channel_id):
    try:
        pending_channels[channel_id] = {
            "guild_name": getattr(guild, "name", "Unknown"),
            "channel_name": channel_name,
            "channel_id": channel_id,
            "sent": False,
        }

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
            ticket_info = pending_channels.get(channel_id)
            if ticket_info and not ticket_info.get("sent"):
                # mark sent and remove
                pending_channels[channel_id]["sent"] = True
                pending_channels.pop(channel_id, None)
                log.info(f"[ticket] {guild.name} → #{channel_name} | {username or 'Unknown'} ({user_id})")
                await send_to_telegram(build_message(guild.name, channel_name, channel_id, user_id, username))
        else:
            # fallback after short delay
            await asyncio.sleep(5)
            ticket_info = pending_channels.get(channel_id)
            if ticket_info and not ticket_info.get("sent"):
                pending_channels[channel_id]["sent"] = True
                pending_channels.pop(channel_id, None)
                log.info(f"[ticket-fallback] {guild.name} → #{channel_name} | Unknown")
                await send_to_telegram(build_message(guild.name, channel_name, channel_id))
    except Exception as e:
        log.exception(f"[ticket error] {getattr(guild, 'name', 'Unknown')} → #{channel_name} | {e}")
        pending_channels.pop(channel_id, None)
        await send_to_telegram(build_message(guild.name if guild else 'Unknown', channel_name, channel_id))

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    log.info(f"✅ Logged in as {client.user} | Watching {len(client.guilds)} servers")

@client.event
async def on_guild_channel_create(channel):
    if not getattr(channel, "guild", None) or not is_ticket_channel(getattr(channel, "name", "")):
        return
    # run but don't block (optional)
    asyncio.create_task(process_ticket(channel.guild, channel, channel.name, channel.id))

@client.event
async def on_thread_create(thread):
    if not getattr(thread, "guild", None) or not is_ticket_channel(getattr(thread, "name", "")):
        return
    asyncio.create_task(process_ticket(thread.guild, thread, thread.name, thread.id))

@client.event
async def on_raw_channel_create(payload):
    log.debug(f"[raw-channel] guild={payload.guild_id} channel={getattr(payload, 'channel_id', 'N/A')}")

@client.event
async def on_raw_thread_create(payload):
    log.debug(f"[raw-thread] guild={payload.guild_id} thread={getattr(payload, 'thread_id', 'N/A')}")

@client.event
async def on_message(message):
    # Only watch messages from bots that are likely ticket bots
    if not getattr(message, "guild", None) or not getattr(message.author, "bot", False):
        return
    if not any(b in (getattr(message.author, "name", "") or "").lower() for b in TICKET_BOT_NAMES):
        return

    channel_id = message.channel.id

    # If we already detected the channel creation, try to extract the user from the bot message
    if channel_id in pending_channels and not pending_channels[channel_id]["sent"]:
        user_id = extract_user_id(message.content)
        if not user_id and getattr(message, "embeds", None):
            for e in message.embeds:
                text = extract_embed_text(e)
                user_id = extract_user_id(text)
                if user_id:
                    break
        if user_id:
            ticket = pending_channels[channel_id]
            ticket["sent"] = True
            pending_channels.pop(channel_id, None)
            _, username, _ = await fetch_user_http(user_id)
            if not username:
                username = extract_username_from_channel(ticket["channel_name"])
            log.info(f"[ticket-msg] {message.guild.name} → #{ticket['channel_name']} | {username} ({user_id})")
            await send_to_telegram(build_message(ticket["guild_name"], ticket["channel_name"], channel_id, user_id, username))
            return

    ticket_name = extract_ticket_name_from_message(message)
    if not ticket_name:
        return

    for cid, ticket in list(pending_channels.items()):
        if ticket["channel_name"].lower() == ticket_name and not ticket["sent"]:
            ticket["sent"] = True
            pending_channels.pop(cid, None)
            user_id = str(getattr(client.user, "id", "Unknown"))
            _, fetched_name, _ = await fetch_user_http(user_id)
            username = (fetched_name or extract_username_from_channel(ticket_name) or getattr(client.user, "name", "Unknown"))
            log.info(f"[ticket-confirm] {message.guild.name} → #{ticket_name} | {username} ({user_id})")
            await send_to_telegram(build_message(ticket["guild_name"], ticket["channel_name"], cid, user_id, username))
            return

if __name__ == "__main__":
    if not DISCORD_USER_TOKEN:
        log.error("DISCORD_USER_TOKEN is not set in the environment.")
    else:
        client.run(DISCORD_USER_TOKEN)
