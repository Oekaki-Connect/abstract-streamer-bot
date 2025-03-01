# Abstract XP, Prizes, and Donation Soundboard Bot by Oekaki.io

This bot powers the Abstract streaming chat experience with an XP and leveling system, giveaways, prize lists, donation sound alerts, and more. It listens to chat events, grants XP to users for participating (or donating), and allows admins to manage giveaways and prizes on the fly.

Below is everything you need to know to install, configure, and operate this bot.

Do not use this bot on streams that you do not have permission to or you will be banned!

This bot is provided AS-IS with NO WARRANTY.

---

## 1) Features Overview

- **XP & Leveling**: Users automatically earn XP by chatting or donating, with a formula to progress through levels.
- **Giveaways**: Admins can create giveaways (optionally with whitelists and prize lists), and users can enter via simple commands.
- **Prizes**: Admins can create lists of prizes that winners receive. The bot randomly awards prizes from these lists.
- **Donation Alerts**: Detects pinned donation messages and plays a configurable sound for certain donation thresholds.
- **Promotion Messages** (Optional): Automatically posts promotional or informational messages at intervals.
- **Message Logging**: The bot will record all messages in your stream to the logs folder. Every day has a new log.

---

## 2) Prerequisites

- Python 3.7+ installed on your system
- An Abstract account with valid credentials (`STREAM_API_KEY` and `STREAM_AUTH_KEY`) (see below on how to get).
- A valid Abstract "streamer username" to fetch channel info from the [Abstract Portal](https://portal.abs.xyz).

---

## 3) Setup

### 3.1) Obtain and Set Environment Variables

The bot relies on the following environment variables. In the project root, create a `.env` file with lines like:

```
STREAM_API_KEY=YourStreamApiKey
STREAM_AUTH_KEY=YourStreamAuthKey
APP_WALLET_ADDRESS=0xYourBotWallet
STREAMER_USERNAME=yourAbstractStreamerName
PROMOTIONS_ENABLED=0
PROMOTION_INTERVAL_SECONDS=360
BOT_MESSAGE_RATE_LIMIT=0.01
FINAL_COUNTDOWN_SECONDS=10
```

`STREAMER_USERNAME` is the @ of the stream the bot will use. The `APP_WALLET_ADDRESS` is for the wallet that you are using the api and auth from.


**Key variables**:

- `STREAM_API_KEY` / `STREAM_AUTH_KEY`: Stream Chat API credentials.
- `APP_WALLET_ADDRESS`: Wallet address / user ID for the bot itself.
- `STREAMER_USERNAME`: The Abstract portal username for the streamer channel.
- `PROMOTIONS_ENABLED`: `1` or `0`; controls whether the bot should automatically cycle through `promotions.txt` lines to post in chat.
- `PROMOTION_INTERVAL_SECONDS`: Interval (in seconds) between promotion messages if `PROMOTIONS_ENABLED=1`.
- `BOT_MESSAGE_RATE_LIMIT`: The delay (in seconds) between bot messages to avoid spam. Default is `0.01`. Messages are queued so they should all be sent.
- `FINAL_COUNTDOWN_SECONDS`: Duration for the final countdown period of giveaways (default is `10` seconds). Each second left is posted in chat.

To get the API key and AUTH key:

* Open any Abstract stream in your browser
* Open Developer Tools (F12 or Right Click → Inspect)
* Refresh the page
* Filter for the only WSS request when the page is loaded (`wss://chat.stream-io-api.com/connect`) (in Chrome it's the WS filter)
* Find the request and click the "Payload tab"
* Copy the api_key and authorization fields
* Paste them into the .env file

Important that you do not share the API and AUTH keys with anyone as they would allow you to post on your behalf.

If you get an error with the bot connecting, get a fresh authorization key and add it to your `.env` file as these auth keys expire after a day or so.

---

### 3.2) Create and Activate a Python Virtual Environment

**Step 1:** Create a virtual environment from the project’s root directory (where `requirements.txt` resides):

```bash
python3 -m venv .venv
```

> On some systems, you may need to use `python` instead of `python3`.

**Step 2:** Activate the virtual environment.

- **macOS / Linux**:

  ```bash
  source .venv/bin/activate
  ```

- **Windows (PowerShell)**:

  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```

> If you get an error about scripts being disabled on Windows, run:

```
Set-ExecutionPolicy Unrestricted -Scope Process
```

Then try activating again.

---

### 3.3) Install Dependencies

With the virtual environment active, install the required dependencies:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

- `pip install --upgrade pip` ensures you’re on the latest pip version.
- `pip install -r requirements.txt` installs all dependencies listed in `requirements.txt`.

---

## 4) Running the Bot

Use Terminal or Windows PowerShell.

1. **Make sure your `.env` file is set up** with your credentials.
2. **Activate** your Python virtual environment (see above).
3. **Navigate** to the `src/` folder (cd src).
4. **Run** the bot:

   ```
   python3 bot.py
   ```
   or

   ```
   python bot.py
   ```

Once running, the bot:

- Connects to the specified Stream Chat channel.
- Listens for messages (granting XP, handling donations, etc.).
- Awaits admin and user commands for giveaways, rank checks, etc.

---

## 5) Folder Structure

```
/
 ┣━ .env                  # Environment variables (copy .env.example and rename to this)
 ┣━ requirements.txt
 ┣━ src/
 │   ┣━ bot.py            # Main bot script
 ┣━ logs/                 # Logs for raw messages and structured events
 ┣━ prizelists/           # Prize list .txt files
 ┣━ whitelists/           # Whitelist .txt files
 ┣━ sounds/               # Sounds to play for donations
 ┣━ admins.txt            # One admin handle/wallet per line (you'll need to create this file with initial admin wallets)
 ┣━ blacklist.txt         # One handle/wallet per line to ignore
 ┣━ users.json            # Persistent XP & level data
 ┣━ donations.json        # Track donation totals per user
 ┣━ giveaways.json        # Active and ended giveaways
 ┗━ promotions.txt        # Lines of text for promotional messages (optional)
```

> The bot automatically creates missing folders and JSON files when it first runs, if they do not exist.

---

## 6) Admin Usage & Commands

**Any user/wallet listed in `admins.txt`** can issue these commands in chat. You need to create the `admins.txt` file with the initial admin wallets you want to be able to do admin commands. 
Commands are case-insensitive, but typically typed in all lowercase in chat.

- `!addadmin @SomeHandleOrWallet`  
  Adds a user to the admin list. Use the wallet address or handle as recognized in chat.

- `!removeadmin @SomeHandleOrWallet`  
  Removes a user from the admin list.

- `!blacklist @SomeHandleOrWallet` or `!kill @SomeHandleOrWallet`  
  Adds the user/wallet to the blacklist. Blacklisted accounts are **ignored** entirely.

- `!createprizelist listName, item1, item2, ...`  
  Creates a new prize list file in `prizelists/` with the specified items.  
  **Note**: The list name must be a valid filename (1–15 characters, no special characters).  
  Example:
  ```
  !createprizelist myprizelist, 100 PENGU, Rare Hat, Mystery NFT
  ```

- `!creategiveaway, <giveaway name>, <entry command>, <minutes optional>, <whitelist optional>, <prizelist optional>, <num winners optional>, <min level optional>`  
  Creates a new giveaway. Example:
  ```
  !creategiveaway, Super GA, !foam, 15, whitelisted, foamprizes, 2, 3
  ```
  - **giveaway name**: A descriptive name (e.g. “Super GA”). (The word "giveaway" is block on Abstract chat so don't use that!)
  - **entry command**: Must start with `!` (e.g. `!foam`). **Cannot** be a reserved command or already in use (otherwise it will overwrite an existing giveaway).
  - **minutes** (optional): How many minutes until auto-end. Omit or use `none` for no time limit.
  - **whitelist** (optional): Name of a file in `whitelists/`. If specified, only addresses in that file can enter.
  - **prizelist** (optional): Name of a prize list in `prizelists/`.
  - **num winners** (optional): Defaults to `1` if not specified.
  - **min level** (optional): Users below this level can’t enter. Defaults to `1` (default level).

- `!endgiveaway <entry command>` or `!endgiveaway <entry command> <seconds>`  
  - **Without seconds**: Immediately ends the giveaway and draws winners.
  - **With seconds** (optional): Schedules the giveaway to end after those seconds (extends or shortens the auto-end). If you started a giveaway without an end time in minutes, you can end it with this to have some anticipation.

- `!cancelgiveaway <entry command>`  
  Cancels the giveaway. It ends with **no** winners.

- `!quit`, `!exit`, or `!shutdown`  
  Saves all data and triggers the bot to shut down.

---

## 7) User Usage & Commands

Users simply chat in the channel, and the bot tracks XP. Additionally, the following commands are recognized:

- **Chat messages**: Each normal message grants +1 XP (if not spammed quicker than 1 second).
- **Donations**: If someone “tipped 100 PENGU,” you gain bonus XP = 100. The bot checks that the message is pinned, which ensures it's not simply a chat message (not actual donation).
- `!rank` or `!level`  
  Displays your current rank (based on total XP), your level, and how much XP you have in the current level.

- `!timeleft <entry command>`  
  If a giveaway is active with the given command, shows how long remains.

- `!winners <entry command>`  
  Shows the winners of that giveaway if it has ended.

- **Giveaway Entry**  
  If there is an active giveaway with an entry command like `!foam`, you just type:
  ```
  !foam
  ```
  and the bot adds you to that giveaway (assuming you meet min level & are not blacklisted, etc.).

---

## 8) XP & Leveling Formula

The XP required to advance from level **L** to level **L+1** is:

```
XP for next level = 5 * (L^2) + (50 * L) + 100
```

If you want to you can adjust this formula for your community.

Donated PENGU is directly added to a user's XP!

The bot automatically broadcasts a congrats message whenever someone levels up.

To see your rank or level, use `!rank` or `!level`.

---

## 9) Donations & Sounds

When the chat sees a pinned donation message (e.g. “Tipped 500 PENGU”), the bot grants bonus XP equal to the donated amount. Additionally, donation thresholds trigger sound effects (if configured in `DONATION_SOUNDS` in `bot.py`), playing only the **highest matching threshold** sound.

Example thresholds in `bot.py`:
```python
DONATION_SOUNDS = [
    (100, "../sounds/donation_100.mp3"),
    (500, "../sounds/donation_1000.mp3"),
    (1000, "../sounds/donation_10000.mp3"),
]
```

You can update or remove these entries as needed. Make sure the sound files exist in a `sounds/` folder.

The filenames can be whatever you want them to be.

---

## 10) Giveaways Management

1. **Create**: Use `!creategiveaway, <name>, <entry cmd>, <time>, <whitelist>, <prizelist>, <winners>, <min lvl>`  
2. **Enter**: Users type the `!entrycmd` in chat.  
3. **Auto-End**: The bot can auto-end a giveaway if you specified minutes. It also does timed warnings and a final countdown.  
4. **End Manually**: `!endgiveaway <entry cmd>`  
5. **Cancel**: `!cancelgiveaway <entry cmd>` — ends the giveaway with no winners.  
6. **View Winners**: `!winners <entry cmd>` after a giveaway has ended.

If a prize list is attached, winners get assigned random items from that list (if available). If the list runs out of items, the bot notifies chat.

---

## 11) Prize Lists & Whitelists

### 11.1) Prize Lists

- **Creation**: `!createprizelist listName, item1, item2, ...` You can manually create these .txt files too. It's useful to have multiple admins who can create prizelists with the chat command for you so that they can help setting up giveaways.
- The bot stores these in `prizelists/<listName>.txt`.
- When a user wins a giveaway, the bot picks a random item from the file and removes it so items aren’t reused.

**Important**: The file name must be safe (no special characters, no `..`, etc.).

### 11.2) Whitelists

- Create a text file in `whitelists/` named `<whitelistName>.txt`.
- Add one wallet/handle per line.
- When a giveaway with that whitelist is created, only users in that file can enter.

---

## 12) Promotion Messages

- If `PROMOTIONS_ENABLED=1`, the bot will post lines from `promotions.txt` in intervals defined by `PROMOTION_INTERVAL_SECONDS`.
- Each line in `promotions.txt` is posted in turn.
- **No links** are allowed by the chat system in these promotional messages. Abstract chat will block many words from appearing in chats so ensure that your promotion messages don't have words on their blacklist otherwise they simply will not appear for others. You can have a friend watch your check as you test different promo messages.
- These promo messages are useful to remind your chat of certain things you want them to know about.

---

## 13) Logging & Data Files

- **Logs Folder**: Contains daily logs of raw messages (`YYYY-MM-DD_raw_message.log`) and structured message logs (`YYYY-MM-DD_messages.log`). This is useful for having a recorded chat history of users.
- **Giveaways Log**: `giveaways_log.txt` records creation, ending, and cancellation. This is useful to verify winners of prizes.
- **admins.txt**: Lists admin handles/wallet addresses, one per line.
- **blacklist.txt**: Lists handles/wallets to ignore, one per line. They don't get XP and can't interact with the bot.
- **users.json**: Stores user profiles, XP, and levels.
- **donations.json**: Stores cumulative donation XP by user (how much PENGU they have given you).
- **giveaways.json**: Stores active giveaways and winners.

These files update in real time. **Never** rename them while the bot is running.

---

## 14) Shutting Down

To stop the bot gracefully:

1. In chat, **admin** types `!quit` (or `!exit` / `!shutdown`) is the best way to close the bot so it can save everything properly before exiting.
2. The bot saves all data and logs.
3. The Python process exits.

You can also terminate it with `Ctrl+C` in the console, but that may skip a final data save.

---

## 15) FAQ / Troubleshooting

- **Bot fails to connect**:
  - Double-check `.env` credentials (`STREAM_API_KEY`, `STREAM_AUTH_KEY`).
  - Confirm the `STREAMER_USERNAME` is correct and active in the Oekaki/Abstract Portal.
- **Bot messages have no effect**:
  - Verify the bot’s user ID matches `APP_WALLET_ADDRESS`.
- **Cannot activate venv on Windows**:
  - Run `Set-ExecutionPolicy Unrestricted -Scope Process` in PowerShell, then `.\.venv\Scripts\Activate.ps1`.
- **Sound not playing on donations**:
  - Check that that the proper `.mp3` files exist in a `sounds/` folder.

If you still have issues, run the bot in a terminal to see any error stack traces, and verify your logs in `logs/` for additional clues.

---

**Enjoy using the Oekaki.io XP / Prize Bot for Abstract!** Your community can now engage in leveling up, winning awesome prizes, and hearing fun donation sound alerts. If you have suggestions or encounter problems, feel free to modify `bot.py` or open an issue in your repository.

Love this bot? Send some PENGU to `0x122e25C0758FDd6E42Da45133Be65EbB4F7d2Ef3` !