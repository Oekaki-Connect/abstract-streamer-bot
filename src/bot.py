import asyncio
import json
import os
import sys
import time
import uuid
import urllib.parse
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode
from dotenv import load_dotenv

import asyncio
from playsound import playsound

# For secure random drawing of giveaway winners
import secrets

load_dotenv()

# Use a SystemRandom instance for unpredictable draws
RNG = secrets.SystemRandom()

# --------------------------------------------------
# Environment / Configuration
# --------------------------------------------------
STREAM_API_KEY = os.getenv("STREAM_API_KEY")
STREAM_AUTH_KEY = os.getenv("STREAM_AUTH_KEY")

# The user ID/wallet with which the BOT connects to the chat.
# Must match the user_id in your JWT if using server tokens.
APP_WALLET_ADDRESS = os.getenv("APP_WALLET_ADDRESS")

# The streamer username. We'll fetch their channel info from the portal:
STREAMER_USERNAME = os.getenv("STREAMER_USERNAME")

# We'll fetch these dynamically on startup rather than storing them in .env:
CHANNEL_TYPE = os.getenv("CHANNEL_TYPE", "messaging")
CHANNEL_ID = None  # We'll set this after fetching from the portal
STREAMER_WALLET_ADDRESS = None  # We'll set this after fetching from the portal

# Example rate-limit config (seconds between bot messages):
BOT_MESSAGE_RATE_LIMIT = float(os.getenv("BOT_MESSAGE_RATE_LIMIT", "0.01"))

# Promotions configuration
PROMOTIONS_ENABLED = bool(int(os.getenv("PROMOTIONS_ENABLED", "0")))
PROMOTION_INTERVAL_SECONDS = int(os.getenv("PROMOTION_INTERVAL_SECONDS", "1"))
promotions_list = []

# configurable warning times (in minutes) for giveaways
GIVEAWAY_WARNING_TIMES_MINUTES = [10, 5, 4, 3, 2, 1]
FINAL_COUNTDOWN_SECONDS = int(os.getenv("FINAL_COUNTDOWN_SECONDS", "10"))

# Donation thresholds and corresponding sounds
DONATION_SOUNDS = [
    # (minimum_pengu_for_this_sound, "path/to/sound_file.mp3" or .wav)
    # (100, "../sounds/wow.mp3"),
    # (500, "../sounds/nice.mp3"),
    # (1000, "../sounds/amazing.mp3"),
]

# reserved commands that cannot be used as giveaway entry commands
RESERVED_COMMANDS = {
    "!addadmin",
    "!removeadmin",
    "!creategiveaway",
    "!endgiveaway",
    "!cancelgiveaway",
    "!timeleft",
    "!winners",
    "!rank",
    "!level",
    "!quit",
    "!exit",
    "!shutdown",
    "!createprizelist",
}

# --------------------------------------------------
# Folders
# --------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.resolve()

LOGS_FOLDER = PROJECT_ROOT / "logs"
PRIZELISTS_FOLDER = PROJECT_ROOT / "prizelists"
WHITELISTS_FOLDER = PROJECT_ROOT / "whitelists"

# Ensure these folders exist
LOGS_FOLDER.mkdir(exist_ok=True, parents=True)
PRIZELISTS_FOLDER.mkdir(exist_ok=True, parents=True)
WHITELISTS_FOLDER.mkdir(exist_ok=True, parents=True)

PROMOTIONS_FILE_PATH = PROJECT_ROOT / "promotions.txt"

# --------------------------------------------------
# Paths
# --------------------------------------------------
ADMINS_TXT_PATH = PROJECT_ROOT / "admins.txt"
BLACKLIST_TXT_PATH = PROJECT_ROOT / "blacklist.txt"
USERS_JSON_PATH = PROJECT_ROOT / "users.json"
DONATIONS_JSON_PATH = PROJECT_ROOT / "donations.json"
GIVEAWAYS_JSON_PATH = PROJECT_ROOT / "giveaways.json"
GIVEAWAYS_LOG_PATH = PROJECT_ROOT / "giveaways_log.txt"

def get_messages_log_path() -> Path:
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return LOGS_FOLDER / f"{date_str}_messages.log"

def get_raw_log_path() -> Path:
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return LOGS_FOLDER / f"{date_str}_raw_message.log"

# --------------------------------------------------
# Data Structures
# --------------------------------------------------

admins_set = set()       # for quick membership
blacklist_set = set()    # for ignoring messages entirely
users_db = {}            # { "0xABC": {"wallet":..., "name":..., "xp":..., "level":...}, ... }
donations_db = {}        # { "0xABC": 123, ... }
giveaways_db = {}        # { "!foam": { "uuid":..., "name":..., "entry_name":..., ... }, ... }

message_send_queue = asyncio.Queue()  # for sending chat messages
user_last_msg_ts = {}                 # { wallet: timestamp_of_last_message }
quit_event = asyncio.Event()          # signal a requested shutdown
bot_sent_message_ids = set()          # track messages sent by the bot (no XP for these)

# --------------------------------------------------
# Filename Validation for Prize Lists
# --------------------------------------------------

def is_valid_prizelist_name(name: str) -> bool:
    """
    Returns True if 'name' is acceptable as a Windows-safe filename
    """
    if not name or len(name) > 15:
        return False

    invalid_pattern = r'[<>:"/\\|?*\x00-\x1F]'
    if re.search(invalid_pattern, name):
        return False

    if '..' in name:
        return False

    if name[-1] in ('.', ' '):
        return False

    return True

# ------------------------------------------
# SOUND QUEUE
# ------------------------------------------
sound_queue = asyncio.Queue()

async def audio_player_loop():
    """
    Continuously waits for a sound file path from 'sound_queue',
    then plays it in a blocking manner before reading the next one.
    """
    while True:
        sound_path = await sound_queue.get()
        try:
            # playsound blocks until the sound finishes
            playsound(sound_path)
        except Exception as e:
            print(f"[ERROR] Could not play sound {sound_path}: {e}")
        finally:
            sound_queue.task_done()

# --------------------------------------------------
# File IO
# --------------------------------------------------

def load_admins():
    admins_set.clear()
    if ADMINS_TXT_PATH.is_file():
        lines = ADMINS_TXT_PATH.read_text(encoding="utf-8").strip().split("\n")
        for line in lines:
            line = line.strip()
            if line:
                admins_set.add(line.lower())

def save_admins():
    with ADMINS_TXT_PATH.open("w", encoding="utf-8") as f:
        for admin in sorted(admins_set):
            f.write(admin + "\n")

def load_blacklist():
    blacklist_set.clear()
    if BLACKLIST_TXT_PATH.is_file():
        lines = BLACKLIST_TXT_PATH.read_text(encoding="utf-8").strip().split("\n")
        for line in lines:
            line = line.strip()
            if line:
                blacklist_set.add(line.lower())

def save_blacklist():
    with BLACKLIST_TXT_PATH.open("w", encoding="utf-8") as f:
        for entry in sorted(blacklist_set):
            f.write(entry + "\n")

def load_users():
    global users_db
    if USERS_JSON_PATH.is_file():
        try:
            users_db = json.loads(USERS_JSON_PATH.read_text(encoding="utf-8"))
        except:
            print("[ERROR] Malformed users.json, ignoring.")
            users_db = {}
    else:
        users_db = {}

def save_users():
    USERS_JSON_PATH.write_text(json.dumps(users_db, indent=2), encoding="utf-8")

def load_donations():
    global donations_db
    if DONATIONS_JSON_PATH.is_file():
        try:
            donations_db = json.loads(DONATIONS_JSON_PATH.read_text(encoding="utf-8"))
        except:
            print("[ERROR] Malformed donations.json, ignoring.")
            donations_db = {}
    else:
        donations_db = {}

def save_donations():
    DONATIONS_JSON_PATH.write_text(json.dumps(donations_db, indent=2), encoding="utf-8")

def load_giveaways():
    global giveaways_db
    if GIVEAWAYS_JSON_PATH.is_file():
        try:
            giveaways_db = json.loads(GIVEAWAYS_JSON_PATH.read_text(encoding="utf-8"))
        except:
            print("[ERROR] Malformed giveaways.json, ignoring.")
            giveaways_db = {}
    else:
        giveaways_db = {}

def save_giveaways():
    GIVEAWAYS_JSON_PATH.write_text(json.dumps(giveaways_db, indent=2), encoding="utf-8")

def log_raw(direction: str, message: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp} [{direction}] {message}\n"
    raw_path = get_raw_log_path()
    with raw_path.open("a", encoding="utf-8") as rf:
        rf.write(line)

def log_message_event(event_data):
    msg_path = get_messages_log_path()
    with msg_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event_data, ensure_ascii=False) + "\n")

def log_giveaway_activity(activity: str):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {activity}\n"
    with GIVEAWAYS_LOG_PATH.open("a", encoding="utf-8") as gf:
        gf.write(line)

# --------------------------------------------------
# Promotions Loading
# --------------------------------------------------
def load_promotions():
    global promotions_list, PROMOTIONS_ENABLED

    if not PROMOTIONS_ENABLED:
        print("[DEBUG] Promotions not enabled.")
        return

    if not PROMOTIONS_FILE_PATH.is_file():
        print("[DEBUG] promotions.txt wasn't found, skipping promotions.")
        PROMOTIONS_ENABLED = False
        return

    lines = []
    with PROMOTIONS_FILE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)

    if not lines:
        print("[DEBUG] promotions.txt is empty, skipping promotions.")
        PROMOTIONS_ENABLED = False
        return

    promotions_list = lines
    print(f"[DEBUG] Loaded {len(promotions_list)} promotion(s).")

# --------------------------------------------------
# Fetch Channel & Streamer Info from Portal
# --------------------------------------------------
def fetch_channel_info():
    """
    Fetches the current 'chatChannelId' and 'streamer.walletAddress'
    from https://backend.portal.abs.xyz/api/streamer/<STREAMER_USERNAME>.
    Raises an exception if the request fails.
    """
    url = f"https://backend.portal.abs.xyz/api/streamer/{STREAMER_USERNAME}"
    print(f"[DEBUG] Fetching channel info from: {url}")
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    channel_id = data["chatChannelId"]
    streamer_wallet = data["streamer"]["walletAddress"]
    print(f"[DEBUG] Fetched chatChannelId={channel_id}, streamerWallet={streamer_wallet}")
    return channel_id, streamer_wallet

# --------------------------------------------------
# Initialization
# --------------------------------------------------

def init_data():
    load_admins()
    load_blacklist()
    load_users()
    load_donations()
    load_giveaways()
    load_promotions()
    queue_bot_message("The Oekaki.io XP / Prize bot is starting up!")

# --------------------------------------------------
# Utility
# --------------------------------------------------

def is_admin(user_str: str) -> bool:
    return user_str.lower() in admins_set

def is_blacklisted(user_str: str) -> bool:
    return user_str.lower() in blacklist_set

def try_get_or_init_user(wallet: str, name: str="") -> dict:
    wkey = wallet.lower()
    if wkey not in users_db:
        users_db[wkey] = {
            "wallet": wkey,
            "name": name,
            "xp": 0,
            "level": 1,  # start new users at level 1
        }
    else:
        if name and name != users_db[wkey].get("name"):
            users_db[wkey]["name"] = name
    return users_db[wkey]

def xp_for_next_level(lvl: int) -> int:
    return 5 * (lvl**2) + (50 * lvl) + 100

def total_xp_to_reach_level(lvl: int) -> int:
    total = 0
    for l in range(1, lvl):
        total += xp_for_next_level(l)
    return total

def ensure_user_xp_and_level(user_obj: dict, xp_gained: int):
    old_xp = user_obj["xp"]
    new_xp = old_xp + xp_gained
    user_obj["xp"] = new_xp
    while True:
        needed = xp_for_next_level(user_obj["level"])
        if user_obj["xp"] >= (total_xp_to_reach_level(user_obj["level"]) + needed):
            user_obj["level"] += 1
            queue_bot_message(
                f"Congrats {user_obj['name'] or user_obj['wallet']}! You leveled up to level {user_obj['level']}!"
            )
        else:
            break

def parse_command_args(text: str):
    return text.strip().split()

def queue_bot_message(msg: str):
    message_send_queue.put_nowait(msg)

def get_user_rank(wallet: str) -> int:
    sorted_users = sorted(users_db.values(), key=lambda x: x["xp"], reverse=True)
    for i, u in enumerate(sorted_users):
        if u["wallet"] == wallet.lower():
            return i + 1
    return len(sorted_users)

def get_whitelist_file_path(whitelist_name: str) -> Path:
    return WHITELISTS_FOLDER / f"{whitelist_name}.txt"

def get_prizelist_file_path(prizelist_name: str) -> Path:
    return PRIZELISTS_FOLDER / f"{prizelist_name}.txt"

def pick_random_prize(prizelist_name: str) -> str:
    path = get_prizelist_file_path(prizelist_name)
    if not path.is_file():
        return None
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").split("\n") if ln.strip()]
    if not lines:
        return None
    prize = RNG.choice(lines)
    lines.remove(prize)
    with path.open("w", encoding="utf-8") as f:
        if lines:
            f.write("\n".join(lines) + "\n")
    return prize

# --------------------------------------------------
# Giveaway management
# --------------------------------------------------

def create_new_giveaway(admin_user: str, raw_args: str):
    splitted = [a.strip() for a in raw_args.split(",")]
    if len(splitted) < 2:
        queue_bot_message("GA creation failed: missing at least name and entry command.")
        return

    g_name = splitted[0]
    entry_name = splitted[1]

    minutes = splitted[2] if len(splitted) >= 3 else ""
    whitelist = splitted[3] if len(splitted) >= 4 else ""
    prizelist = splitted[4] if len(splitted) >= 5 else ""
    winners_count_str = splitted[5] if len(splitted) >= 6 else "1"
    min_level_str = splitted[6] if len(splitted) >= 7 else "1"

    if minutes.lower() == "none":
        minutes = ""
    if whitelist.lower() == "none":
        whitelist = ""
    if prizelist.lower() == "none":
        prizelist = ""
    if winners_count_str.lower() == "none":
        winners_count_str = "1"
    if min_level_str.lower() == "none":
        min_level_str = "1"

    if not g_name or not entry_name.startswith("!"):
        queue_bot_message("GA creation failed: missing name or entry command not starting with !.")
        return

    # Check if reserved
    if entry_name.lower() in RESERVED_COMMANDS:
        queue_bot_message(f"Cannot use {entry_name} as a GA command; it is reserved.")
        return

    # Cancel old if same entry_name
    if entry_name in giveaways_db:
        log_giveaway_activity(f"Auto-cancel GA with entry={entry_name} replaced by new one.")
        del giveaways_db[entry_name]

    g_id = str(uuid.uuid4())

    try:
        winners_count = int(winners_count_str)
    except:
        winners_count = 1

    try:
        min_level_required = int(min_level_str) if min_level_str else 1
    except:
        min_level_required = 1

    g_obj = {
        "uuid": g_id,
        "name": g_name,
        "entry_name": entry_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "creator": admin_user,
        "whitelist_name": whitelist,
        "prizelist_name": prizelist,
        "num_winners": winners_count,
        "entries": [],
        "is_active": True,
        "auto_end": None,
        "ended_at": None,
        "winners": [],
        "min_level": min_level_required,
        "warned_for": [],
    }

    if minutes:
        try:
            mm = float(minutes)
            end_ts = time.time() + (mm * 60.0)
            g_obj["auto_end"] = end_ts
            # pre-check for warnings that are greater than total time
            warn_times_in_seconds = [m * 60 for m in GIVEAWAY_WARNING_TIMES_MINUTES]
            total_time = end_ts - time.time()
            for w_sec in warn_times_in_seconds:
                if total_time < w_sec:
                    g_obj["warned_for"].append(w_sec)
        except:
            pass

    giveaways_db[entry_name] = g_obj
    save_giveaways()
    queue_bot_message(
        f"New GA '{g_name}' created with entry '{entry_name}'. (Min level: {min_level_required})"
    )
    log_giveaway_activity(f"Created GA: name='{g_name}', entry='{entry_name}', uuid={g_id}")

def user_enter_giveaway(user_wallet: str, user_name: str, entry_name: str):
    if entry_name not in giveaways_db:
        return
    g = giveaways_db[entry_name]
    if not g["is_active"]:
        return

    wkey = user_wallet.lower()
    user_obj = users_db.get(wkey, {})
    user_level = user_obj.get("level", 1)
    min_lvl = g.get("min_level", 1)
    if user_level < min_lvl:
        return

    # check duplicates
    for e in g["entries"]:
        if e["wallet"] == wkey:
            return

    # whitelist check
    wl = g["whitelist_name"]
    if wl:
        wl_path = get_whitelist_file_path(wl)
        if wl_path.is_file():
            lines = [ln.strip().lower() for ln in wl_path.read_text().split()]
            if wkey not in lines:
                queue_bot_message(f"{user_name or user_wallet} not whitelisted for {g['name']}")
                return

    g["entries"].append({"wallet": wkey, "name": user_name, "ts": time.time()})
    save_giveaways()
    queue_bot_message(f"{user_name or user_wallet} entered GA {g['name']}.")

def end_giveaway(entry_name: str):
    if entry_name not in giveaways_db:
        queue_bot_message(f"No active GA for {entry_name}")
        return
    g = giveaways_db[entry_name]
    if not g["is_active"]:
        queue_bot_message(f"GA {g['name']} not active.")
        return

    num = g["num_winners"]
    pool = g["entries"]
    if len(pool) < num:
        num = len(pool)

    if num > 0 and len(pool) > 0:
        winners = RNG.sample(pool, num)
    else:
        winners = []

    g["winners"] = winners
    g["is_active"] = False
    g["ended_at"] = datetime.now(timezone.utc).isoformat()

    if winners:
        prizelist_name = g.get("prizelist_name", "")
        if prizelist_name:
            for w in winners:
                prize = pick_random_prize(prizelist_name)
                if prize:
                    w["prize"] = prize
                    queue_bot_message(
                        f"{w['name'] or w['wallet']} has won '{prize}' in GA '{g['name']}'!"
                    )
                else:
                    w["prize"] = None
                    queue_bot_message(
                        f"{w['name'] or w['wallet']} won, but no more prizes were available for '{g['name']}'!"
                    )
        lines = []
        for w in winners:
            user_part = f"{w['name'] or w['wallet']}"
            prize_part = f" ({w['prize']})" if w.get("prize") else ""
            lines.append(user_part + prize_part)
        msg = f"GA '{g['name']}' ended! Winners: {', '.join(lines)}"
    else:
        msg = f"'{g['name']}' GA ended! No entries... no winners!"

    queue_bot_message(msg)
    save_giveaways()
    log_giveaway_activity(f"Ended GA entry='{entry_name}', winners={winners}")

def cancel_giveaway(entry_name: str):
    if entry_name not in giveaways_db:
        queue_bot_message(f"No GA found for {entry_name}")
        return
    g = giveaways_db[entry_name]
    if g["is_active"]:
        g["is_active"] = False
        g["ended_at"] = datetime.now(timezone.utc).isoformat()
        g["winners"] = []
    queue_bot_message(f"Canceled GA {g['name']}.")
    log_giveaway_activity(f"Canceled {entry_name} - {g['name']}")
    del giveaways_db[entry_name]
    save_giveaways()

def timeleft_giveaway(entry_name: str):
    if entry_name not in giveaways_db:
        queue_bot_message(f"No active GA for {entry_name}")
        return
    g = giveaways_db[entry_name]

    if not g["is_active"]:
        if not g["ended_at"]:
            queue_bot_message(f"GA {g['name']} ended or was canceled (no end time recorded).")
            return
        ended_time = datetime.fromisoformat(g["ended_at"])
        now_utc = datetime.now(timezone.utc)
        diff = now_utc - ended_time
        secs_ago = diff.total_seconds()
        h = int(secs_ago // 3600)
        m = int((secs_ago % 3600) // 60)
        s = int(secs_ago % 60)
        queue_bot_message(f"GA {g['name']} ended {h}h {m}m {s}s ago.")
        return

    if g["auto_end"] is None:
        queue_bot_message(f"GA {g['name']} has no auto-end time.")
        return

    secs_left = g["auto_end"] - time.time()
    if secs_left <= 0:
        queue_bot_message(f"GA {g['name']} auto-end time passed, but not forcibly ended.")
        return

    h = int(secs_left // 3600)
    m = int((secs_left % 3600) // 60)
    s = int(secs_left % 60)
    queue_bot_message(f"{g['name']} ends in {h}h {m}m {s}s")

def winners_giveaway(entry_name: str):
    # Check if the giveaway command exists
    if entry_name not in giveaways_db:
        queue_bot_message(f"No giveaway found for {entry_name}.")
        return

    g = giveaways_db[entry_name]

    # Check if the giveaway is still active or never properly ended
    if g["is_active"] or not g["ended_at"]:
        queue_bot_message(f"The giveaway '{g['name']}' hasn't ended yet (or was never ended).")
        return

    # If there are no winners, it might be canceled or had zero entries
    if not g["winners"]:
        queue_bot_message(f"'{g['name']}' had no winners or was canceled.")
        return

    # Construct a message listing each winner and their prize
    winners_list = []
    for w in g["winners"]:
        user_str = w["name"] or w["wallet"]
        if w.get("prize"):
            winners_list.append(f"{user_str} ({w['prize']})")
        else:
            winners_list.append(user_str)

    # E.g. "Foam Giveaway winners => Alice (Foam Prize #1), Bob (Foam Prize #2)"
    winners_str = ", ".join(winners_list)
    msg = f"{g['name']} winners => {winners_str}"
    queue_bot_message(msg)


# --------------------------------------------------
# NEW HELPER: set_giveaway_end_in
# --------------------------------------------------

def set_giveaway_end_in(entry_name: str, seconds: int):
    """
    Updates the given giveaway's auto_end to (now + seconds),
    provided it is still active. Resets warnings + final countdown so they
    can be triggered again.
    """
    if entry_name not in giveaways_db:
        queue_bot_message(f"No active GA for {entry_name}")
        return
    g = giveaways_db[entry_name]
    if not g["is_active"]:
        queue_bot_message(f"GA '{g['name']}' is not active, cannot update end time.")
        return

    new_end_time = time.time() + seconds
    g["auto_end"] = new_end_time
    # Reset warnings / final countdown flags so they can be retriggered
    g["warned_for"] = []
    if "in_final_countdown" in g:
        g["in_final_countdown"] = False
    save_giveaways()

    queue_bot_message(f"Updated GA '{g['name']}' to end in {seconds} second(s) from now.")
    log_giveaway_activity(
        f"Updated end time for GA '{entry_name}' => now + {seconds} seconds."
    )

# --------------------------------------------------
# Donation Checking
# --------------------------------------------------

def check_if_donation_message(msg_obj: dict) -> int:
    if msg_obj.get("pinned") is True:  # pinned => typically a donation
        text_lower = msg_obj.get("text", "").lower()
        if text_lower.startswith("tipped ") and " pengu" in text_lower:
            splitted = text_lower.split()
            if len(splitted) >= 3:
                try:
                    donated = int(splitted[1])
                    return max(donated, 0)
                except:
                    pass
    return 0

# --------------------------------------------------
# Background tasks
# --------------------------------------------------

async def record_messages(ws):
    while True:
        raw_data = await ws.recv()
        log_raw("recv", raw_data)

        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            continue

        event_type = data.get("type")
        if event_type == "message.new":
            cid = data.get("cid", "")
            if cid != f"{CHANNEL_TYPE}:{CHANNEL_ID}":
                continue

            msg = data.get("message", {})
            user = msg.get("user", {})
            wallet = user.get("id", "")
            username = user.get("name", "") or wallet
            message_id = msg.get("id", "")

            # Blacklist check
            if is_blacklisted(wallet) or is_blacklisted(f"@{username.lower()}"):
                continue

            event_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message_id": message_id,
                "wallet": wallet,
                "name": username,
                "content": msg.get("text", ""),
            }
            log_message_event(event_data)

            # Skip XP if this is the bot's own message
            if message_id in bot_sent_message_ids:
                continue

            user_obj = try_get_or_init_user(wallet, username)
            donated_pengu = check_if_donation_message(msg)

            now_ts = time.time()
            last_ts = user_last_msg_ts.get(wallet, 0)
            time_since_last = now_ts - last_ts
            user_last_msg_ts[wallet] = now_ts

            xp_gained = 0
            if donated_pengu > 0:
                xp_gained = donated_pengu
                donations_db[wallet.lower()] = donations_db.get(wallet.lower(), 0) + donated_pengu
                save_donations()

                # -------------------------------------------
                # Only play the HIGHEST donation threshold sound
                # -------------------------------------------
                # Sort thresholds in descending order by amount_required
                biggest_threshold_sound = None
                for (amount_required, sound_file) in sorted(DONATION_SOUNDS, key=lambda x: x[0], reverse=True):
                    if donated_pengu >= amount_required:
                        biggest_threshold_sound = sound_file
                        break

                # If we found a matching threshold, queue its sound
                if biggest_threshold_sound:
                    asyncio.create_task(sound_queue.put(biggest_threshold_sound))

            else:
                # 1-second spam filter for normal chat XP
                if time_since_last >= 1.0:
                    xp_gained = 1

            if xp_gained > 0:
                ensure_user_xp_and_level(user_obj, xp_gained)
                save_users()

            txt = msg.get("text", "").strip()
            if is_admin(wallet):
                handle_admin_command(wallet, txt)
                handle_user_command(user_obj, txt)
            else:
                handle_user_command(user_obj, txt)

        else:
            pass


async def wait_for_connection_id(ws):
    while True:
        raw_data = await ws.recv()
        log_raw("recv", raw_data)
        try:
            data = json.loads(raw_data)
        except:
            continue

        if "connection_id" in data:
            return data["connection_id"]

def watch_channel(connection_id):
    """Queries/watches the channel with the current CHANNEL_ID."""
    global CHANNEL_ID  # so we can refresh if needed

    url = f"https://chat.stream-io-api.com/channels/{CHANNEL_TYPE}/{CHANNEL_ID}/query"
    params = {
        "api_key": STREAM_API_KEY,
        "authorization": STREAM_AUTH_KEY,
        "stream-auth-type": "jwt",
        "connection_id": connection_id,
    }
    payload = {
        "watch": True,
        "presence": True,
        "state": True,
    }
    try:
        r = requests.post(url, params=params, json=payload, timeout=10)
        r.raise_for_status()
    except requests.HTTPError as e:
        print("[ERROR] watch_channel =>", e)
        # If it’s a 401, we might want to refresh channel info.
        if e.response.status_code == 401:
            print("[WARN] watch_channel => 401, refreshing channel info.")
            try:
                new_ch, new_wallet = fetch_channel_info()
                # Overwrite global
                CHANNEL_ID = new_ch
                # Possibly also store the new streamer wallet if needed:
                # STREAMER_WALLET_ADDRESS = new_wallet
            except Exception as ex:
                print("[ERROR] watch_channel => Unable to refresh channel info:", ex)
    except Exception as e:
        print("[ERROR] watch_channel =>", e)

async def send_health_check(ws):
    while True:
        await asyncio.sleep(25)
        payload = [{"type":"health.check"}]
        msg_str = json.dumps(payload)
        try:
            await ws.send(msg_str)
            log_raw("send", msg_str)
        except ConnectionClosed:
            break
        except Exception as exc:
            print("[ERROR] Pinger =>", exc)
            break

async def connect_and_watch_once():
    """Connects once to the Stream Chat WS and begins watching channel events."""
    global CHANNEL_ID  # needed in the watch logic

    base = "wss://chat.stream-io-api.com/connect"
    user_details = {
        "user_id": APP_WALLET_ADDRESS.lower(),
        "user_details": {"id": APP_WALLET_ADDRESS.lower()},
        "client_request_id": f"python-record-{APP_WALLET_ADDRESS.lower()}",
    }
    enc_json = urllib.parse.quote(json.dumps(user_details), safe="")
    enc_auth = urllib.parse.quote(STREAM_AUTH_KEY or "", safe="")

    ws_url = (
        f"{base}?json={enc_json}"
        f"&api_key={STREAM_API_KEY}"
        f"&authorization={enc_auth}"
        f"&stream-auth-type=jwt"
        f"&X-Stream-Client=stream-chat-python-client-0.0.1"
    )
    print("[DEBUG] Connecting =>", ws_url)

    async with websockets.connect(ws_url, ping_interval=None, ping_timeout=None, close_timeout=5) as ws:
        pinger_task = asyncio.create_task(send_health_check(ws))
        conn_id = await wait_for_connection_id(ws)
        watch_channel(conn_id)
        await record_messages(ws)

async def connect_and_watch_loop():
    """Keeps attempting to connect to the chat in a loop, with backoff."""
    backoff = 1
    max_backoff = 60
    while not quit_event.is_set():
        try:
            await connect_and_watch_once()
        except (ConnectionClosed, OSError, InvalidStatusCode) as e:
            print(f"[ERROR] Connection error: {e}. Retrying in {backoff} second(s).")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
        except Exception as e:
            print(f"[ERROR] Unexpected error: {e}. Retrying in {backoff} second(s).")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
        else:
            # If connect_and_watch_once() returns cleanly, wait some time then try again
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

async def message_sender_loop():
    """Consumes messages from the bot's queue and POSTs them to the chat channel."""
    global CHANNEL_ID, STREAMER_WALLET_ADDRESS

    while True:
        msg_text = await message_send_queue.get()
        await asyncio.sleep(BOT_MESSAGE_RATE_LIMIT)

        post_url = f"https://chat.stream-io-api.com/channels/{CHANNEL_TYPE}/{CHANNEL_ID}/message"
        post_params = {
            "api_key": STREAM_API_KEY,
            "authorization": STREAM_AUTH_KEY,
            "stream-auth-type": "jwt",
        }
        payload = {"message": {"text": msg_text}}

        try:
            resp = requests.post(post_url, params=post_params, json=payload, timeout=5)
            resp.raise_for_status()

            data = resp.json()
            msg_obj = data.get("message", {})
            if "id" in msg_obj:
                bot_sent_message_ids.add(msg_obj["id"])

            log_raw("send", f"SENT BOT MESSAGE: {msg_text}")
            print("[DEBUG] BOT SENT =>", msg_text)
        except requests.HTTPError as e:
            # If we get a 401, assume the channel might have changed
            if e.response.status_code == 401:
                print("[WARN] message_sender_loop => 401 Unauthorized. Refreshing channel ID and retrying once.")
                try:
                    new_ch, new_wallet = fetch_channel_info()
                    CHANNEL_ID = new_ch
                    STREAMER_WALLET_ADDRESS = new_wallet

                    # Retry once with the new channel ID
                    retry_url = f"https://chat.stream-io-api.com/channels/{CHANNEL_TYPE}/{CHANNEL_ID}/message"
                    retry_resp = requests.post(retry_url, params=post_params, json=payload, timeout=5)
                    retry_resp.raise_for_status()

                    data2 = retry_resp.json()
                    msg_obj2 = data2.get("message", {})
                    if "id" in msg_obj2:
                        bot_sent_message_ids.add(msg_obj2["id"])

                    log_raw("send", f"SENT BOT MESSAGE (retry): {msg_text}")
                    print("[DEBUG] BOT SENT (retry) =>", msg_text)
                except Exception as ex2:
                    print("[ERROR] Retry after 401 also failed =>", ex2)
            else:
                print("[ERROR] message_sender_loop =>", e)
        except Exception as e:
            print("[ERROR] message_sender_loop =>", e)
        finally:
            message_send_queue.task_done()

async def promotion_poster_loop():
    if not PROMOTIONS_ENABLED or not promotions_list:
        return

    i = 0
    total = len(promotions_list)
    while True:
        queue_bot_message(promotions_list[i])
        i = (i + 1) % total
        await asyncio.sleep(PROMOTION_INTERVAL_SECONDS)

# --------------------------------------------------
# Final Countdown Coroutine
# --------------------------------------------------
async def final_countdown_coroutine(entry_name: str):
    g = giveaways_db.get(entry_name)
    if not g or not g["is_active"]:
        return

    g["in_final_countdown"] = True
    save_giveaways()

    for i in range(FINAL_COUNTDOWN_SECONDS, 0, -1):
        if not g["is_active"]:
            return
        queue_bot_message(f"{g['name']} winner(s) picked in {i}..")
        await asyncio.sleep(1)
        if not g["is_active"]:
            return

    end_giveaway(entry_name)

# --------------------------------------------------
# Auto-End Checking + Warnings
# --------------------------------------------------
async def autoend_check_loop():
    warn_times_in_seconds = [m * 60 for m in GIVEAWAY_WARNING_TIMES_MINUTES]
    while True:
        now_ts = time.time()
        to_end = []

        for entry_name, g in list(giveaways_db.items()):
            if g["is_active"] and g.get("auto_end") is not None:
                time_left = g["auto_end"] - now_ts
                if time_left <= 0:
                    if FINAL_COUNTDOWN_SECONDS <= 0 or g.get("in_final_countdown"):
                        to_end.append(entry_name)
                    else:
                        if not g.get("in_final_countdown"):
                            asyncio.create_task(final_countdown_coroutine(entry_name))
                    continue

                if (
                    FINAL_COUNTDOWN_SECONDS > 0
                    and time_left <= FINAL_COUNTDOWN_SECONDS
                    and not g.get("in_final_countdown", False)
                ):
                    asyncio.create_task(final_countdown_coroutine(entry_name))
                    continue

                if "warned_for" not in g:
                    g["warned_for"] = []

                for w_sec in sorted(warn_times_in_seconds, reverse=True):
                    if time_left <= w_sec and w_sec not in g["warned_for"]:
                        mins_left = int(w_sec // 60)
                        queue_bot_message(
                            f"{g['name']} ends in {mins_left} minute{'s' if mins_left!=1 else ''}! "
                            f"Type {g['entry_name']} to enter!"
                        )
                        g["warned_for"].append(w_sec)
                        save_giveaways()

        for entry_name in to_end:
            end_giveaway(entry_name)

        await asyncio.sleep(2)

async def watch_for_quit():
    await quit_event.wait()
    await message_send_queue.join()
    print("[DEBUG] Exiting gracefully now...")
    sys.exit(0)

# --------------------------------------------------
# Command Handlers
# --------------------------------------------------
def handle_admin_command(admin_wallet: str, text: str):
    lower = text.lower()

    if lower.startswith("!addadmin"):
        parts = parse_command_args(text)
        if len(parts) >= 2:
            newadm = parts[1].lower()
            admins_set.add(newadm)
            save_admins()
            queue_bot_message(f"Added admin {newadm}.")
        else:
            queue_bot_message("Usage: !addadmin @someone")

    elif lower.startswith("!removeadmin"):
        parts = parse_command_args(text)
        if len(parts) >= 2:
            oldadm = parts[1].lower()
            if oldadm in admins_set:
                admins_set.remove(oldadm)
                save_admins()
                queue_bot_message(f"Removed admin {oldadm}.")
            else:
                queue_bot_message(f"{oldadm} is not an admin.")
        else:
            queue_bot_message("Usage: !removeadmin @someone")

    elif lower.startswith("!blacklist") or lower.startswith("!kill"):
        parts = parse_command_args(text)
        if len(parts) < 2:
            queue_bot_message("Usage: !blacklist @someone OR !blacklist 0xWallet")
            return
        target = parts[1].lower()
        blacklist_set.add(target)
        save_blacklist()
        queue_bot_message(f"'{target}' has been added to the blacklist and will be ignored.")

    elif lower.startswith("!createprizelist"):
        create_prizelist(admin_wallet, text)

    elif lower.startswith("!creategiveaway"):
        splitted = text.split("!creategiveaway", 1)
        if len(splitted) < 2:
            queue_bot_message("Usage: !creategiveaway, name, !entry, minutes, whitelist, prizelist, winners, minlvl")
            return
        raw_args = splitted[1].strip(" ,")
        create_new_giveaway(admin_wallet, raw_args)

    elif lower.startswith("!endgiveaway"):
        parts = parse_command_args(text)
        if len(parts) < 2:
            queue_bot_message("Usage: !endgiveaway !entry [seconds]")
            return
        if len(parts) == 2:
            # No seconds specified => end now
            end_giveaway(parts[1])
        else:
            # Attempt to parse the number of seconds
            try:
                seconds = int(parts[2])
                set_giveaway_end_in(parts[1], seconds)
            except ValueError:
                # Fallback => end now
                end_giveaway(parts[1])

    elif lower.startswith("!cancelgiveaway"):
        parts = parse_command_args(text)
        if len(parts) >= 2:
            cancel_giveaway(parts[1])
        else:
            queue_bot_message("Usage: !cancelgiveaway !entry")

    elif lower.startswith("!quit") or lower.startswith("!exit") or lower.startswith("!shutdown"):
        queue_bot_message("The Oekaki.io XP / Prize bot is shutting down...")
        save_admins()
        save_users()
        save_donations()
        save_giveaways()
        quit_event.set()

def handle_user_command(user_obj: dict, text: str):
    t = text.strip().lower()
    if t.startswith("!rank") or t.startswith("!level"):
        wallet = user_obj["wallet"]
        xp_total = user_obj["xp"]
        lvl = user_obj["level"]
        name = user_obj["name"] or user_obj["wallet"]
        rank = get_user_rank(wallet)
        xp_needed_for_this_level = xp_for_next_level(lvl)
        xp_to_reach_this_level = total_xp_to_reach_level(lvl)
        xp_in_level = xp_total - xp_to_reach_this_level

        queue_bot_message(
            f"{name}: Rank #{rank}, Level {lvl}, XP: {xp_in_level}/{xp_needed_for_this_level}"
        )

    elif t.startswith("!timeleft"):
        parts = parse_command_args(text)
        if len(parts) >= 2:
            timeleft_giveaway(parts[1])
        else:
            queue_bot_message("Usage: !timeleft !entrycmd")

    elif t.startswith("!winners"):
        parts = parse_command_args(text)
        if len(parts) >= 2:
            winners_giveaway(parts[1])
        else:
            queue_bot_message("Usage: !winners !entrycmd")

    elif t.startswith("!"):
        # Possibly a giveaway entry
        entryname = t.split()[0]
        if entryname in giveaways_db:
            user_enter_giveaway(user_obj["wallet"], user_obj["name"], entryname)
            
    elif t.startswith("!winners"):
        parts = parse_command_args(text)
        # We expect the user to do: "!winners !mygiveaway" so parts would be ["!winners", "!mygiveaway"]
        if len(parts) >= 2:
            winners_giveaway(parts[1])  # pass the entry command, e.g. "!mygiveaway"
        else:
            queue_bot_message("Usage: !winners !giveawaycommand")            

def create_prizelist(admin_user: str, text: str):
    splitted = text.split("!createprizelist", 1)
    if len(splitted) < 2:
        queue_bot_message("Usage: !createprizelist listName, item1, item2, ...")
        return

    raw_args = splitted[1].strip(" ,")
    parts = [p.strip() for p in raw_args.split(",")]
    if not parts:
        queue_bot_message("Usage: !createprizelist listName, item1, item2, ...")
        return

    list_name = parts[0]
    prizes = parts[1:]

    if not list_name:
        queue_bot_message("No prizelist name found. Usage: !createprizelist listName, item1, item2, ...")
        return

    if not is_valid_prizelist_name(list_name):
        queue_bot_message(
            "Invalid prize list name. Must be 1–15 chars, cannot contain Windows‐invalid characters, "
            'cannot contain "..", and cannot end with "." or space.'
        )
        return

    plist_path = get_prizelist_file_path(list_name)
    if plist_path.is_file():
        queue_bot_message(f"Prizelist '{list_name}' already exists! Can't overwrite.")
        return

    cleaned_prizes = [p.strip() for p in prizes if p.strip()]

    if not cleaned_prizes:
        queue_bot_message(f"Creating empty prizelist '{list_name}' (no prizes).")
    else:
        queue_bot_message(f"Creating new prizelist '{list_name}' with {len(cleaned_prizes)} prize(s).")

    with plist_path.open("w", encoding="utf-8") as f:
        if cleaned_prizes:
            f.write("\n".join(cleaned_prizes))
            f.write("\n")

# --------------------------------------------------
# Main Bot Entry
# --------------------------------------------------

async def main_loop():
    tasks = []
    tasks.append(asyncio.create_task(connect_and_watch_loop()))
    tasks.append(asyncio.create_task(message_sender_loop()))
    tasks.append(asyncio.create_task(autoend_check_loop()))
    tasks.append(asyncio.create_task(promotion_poster_loop()))
    tasks.append(asyncio.create_task(watch_for_quit()))
    tasks.append(asyncio.create_task(audio_player_loop()))
    

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in pending:
        t.cancel()

def main():
    global CHANNEL_ID, STREAMER_WALLET_ADDRESS

    # 1) Dynamically fetch the current channel ID + streamer wallet
    CHANNEL_ID, STREAMER_WALLET_ADDRESS = fetch_channel_info()

    # 2) Load all data / start up
    init_data()
    print("[DEBUG] Starting Bot.  RateLimit=", BOT_MESSAGE_RATE_LIMIT)

    # 3) Run the main async loop
    asyncio.run(main_loop())

if __name__ == "__main__":
    main()
