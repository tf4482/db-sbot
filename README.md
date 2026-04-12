# 🤖 db-sbot - Discord Channel Monitor

A lightweight, **single-shot** Python script that detects when channels are **created**, **deleted**, or **renamed** on a Discord server and sends notifications via **e-mail** and/or a **Discord webhook**.

Run it on a schedule (e.g. via `cron` or a systemd timer) instead of keeping a long-running process alive.

---

## ✨ Features

- 🆕 Detects **new** channels
- 🗑️ Detects **deleted** channels
- ✏️ Detects **renamed** channels (same ID, different name)
- 👤 Detects posts by a configured username in channels that were previously detected as **new** or **renamed**
- 📧 Sends **e-mail** notifications via Gmail
- 🔔 Sends **Discord webhook** embed notifications (optionally pings a role)
- 📝 Writes events to a **log file**
- ⚙️ Self-bootstrapping - creates a placeholder config on first run
- 💾 Persists state between runs in a local **SQLite** database
- 🐛 **Debug mode** - separate config file and database for testing

---

## 📋 Requirements

- Python **3.12** or newer
- [`uv`](https://docs.astral.sh/uv/) (recommended) **or** `pip`

---

## 🚀 Installation

### Using uv (recommended)

```bash
git clone https://github.com/your-user/db-sbot.git
cd db-sbot
uv sync
```

### Using pip

```bash
git clone https://github.com/your-user/db-sbot.git
cd db-sbot
pip install requests
```

---

## ▶️ Usage

```bash
uv run main.py          # normal mode
uv run main.py --debug  # debug mode  (short: -d)

python main.py
python main.py --debug
```

On the very **first run**, if no config file is found the script will:

1. Create the folder `~/.config/db-sbot/`
2. Write a **placeholder** config file there
3. Print the path and exit

Fill in the config file (see [Configuration](#%EF%B8%8F-configuration) below), then run the script again.

The **second run** (with a valid config) seeds the database with the current channel list and exits - no notifications are sent yet.

From the **third run onwards** the script compares the current channel list with the stored snapshot, sends notifications for any changes, updates the snapshot, and exits.

---

## ⚙️ Configuration

The script looks for the config file in this order:

1. 📁 **Script folder** - `db-sbot-config.json` next to `main.py`
2. 🏠 **User config dir** - `~/.config/db-sbot/db-sbot-config.json`

The file is plain **JSON**. All keys and their defaults:

```jsonc
{
    // 🔑 Required
    "USER_TOKEN": "your-discord-user-token-here",
    "SERVER_ID":  "your-server-id-here",

    // 📋 Detection
    "LOGGING_ENABLED":          true,   // write events to ~/.db-sbot/channel_log.txt
    "DETECT_NEW_CHANNELS":      true,   // notify when a channel is created
    "DETECT_REMOVED_CHANNELS":  true,   // notify when a channel is deleted
    "DETECT_RENAMED_CHANNELS":  true,   // notify when an existing channel is renamed
    "DETECT_USER_POSTS":        false,  // monitor watched channels for posts by WATCH_USERNAME
    "WATCH_USERNAME":           "",     // Discord username to match exactly (case-sensitive)
    "WATCH_POST_MESSAGE_LIMIT": 50,     // messages fetched per watched channel per run (1..100)
    "WATCH_USER_POST_DETECTION_LIMIT": 20, // max matched posts notified per tracked channel in total (>=1)

    // 📧 Gmail notifications
    "EMAIL_ENABLED":      false,
    "GMAIL_ADDRESS":      "your-gmail@gmail.com",
    "GMAIL_APP_PASSWORD": "your-gmail-app-password",  // use an App Password, not your login password
    "EMAIL_RECIPIENTS":   ["recipient@example.com"],

    // 🔔 Discord webhook notifications
    "DISCORD_WEBHOOK_ENABLED": false,
    "DISCORD_WEBHOOK_URL":     "your-discord-webhook-url-here",
    "DISCORD_WEBHOOK_ROLE_ID": ""      // optional: role ID to ping (leave empty to disable)
}
```

### 👤 Watched user post detection

When `DETECT_USER_POSTS` is enabled and `WATCH_USERNAME` is set, the script also checks channels that were previously detected as:

- **new** (`DETECT_NEW_CHANNELS`), or
- **renamed** (`DETECT_RENAMED_CHANNELS`)

If a message by `WATCH_USERNAME` appears in one of those tracked channels, the script sends the same notification outputs (e-mail / webhook) including channel, timestamp, content preview, and a direct message URL.

Notes:

- Username matching is **exact and case-sensitive**.
- `WATCH_POST_MESSAGE_LIMIT` must be between `1` and `100` (Discord API limit).
- `WATCH_USER_POST_DETECTION_LIMIT` limits how many matched posts trigger notifications per tracked channel in total (persisted in SQLite).
- If a tracked channel is detected as **renamed**, both its matched-post counter and message cursor are reset (`count=0`) and rebased to the current latest message ID, so only posts made **after** the rename are considered until the cap is reached or the channel is renamed again.
- The watch state is persisted in the same SQLite database under an internal `watched_channels` table.

### 🐛 Debug mode configuration

When `--debug` / `-d` is passed, the script uses a **separate** config file and database so normal production state is never touched:

| | Normal | Debug |
|---|---|---|
| Config | `db-sbot-config.json` | `db-sbot-config.debug.json` |
| Database | `~/.db-sbot/state.db` | `~/.db-sbot/state.debug.db` |
| Log | `~/.db-sbot/channel_log.txt` | `~/.db-sbot/channel_log.debug.txt` |

If the debug config is missing it is auto-created as a placeholder at `~/.config/db-sbot/db-sbot-config.debug.json`, identical to the normal first-run behaviour.

---

### 🔑 How to get your Discord User Token

> ⚠️ **Never share your user token.** Anyone with it has full access to your Discord account.

1. Open Discord in your **browser** (discord.com)
2. Press **F12** to open DevTools → go to the **Network** tab
3. Press **F5** to reload the page
4. In the filter bar type `science`
5. Click the request → go to **Request Headers**
6. Copy the value of the `Authorization` header - that is your user token

### 🪪 How to get your Discord Server ID

1. Open Discord (browser or desktop app)
2. Go to **Settings → Advanced** and enable **Developer Mode**
3. Right-click the **server icon** in the left sidebar
4. Click **Copy Server ID**

### 🔔 Discord Webhook URL

1. Open the Discord server where you want to receive notifications
2. Go to **Server Settings → Integrations → Webhooks**
3. Click **New Webhook**, choose a channel, and copy the URL
4. Paste it as `DISCORD_WEBHOOK_URL` in your config

### 🏷️ Discord Role ID (optional ping)

1. Enable **Developer Mode** (Settings → Advanced)
2. Right-click the role in **Server Settings → Roles**
3. Click **Copy Role ID**
4. Paste it as `DISCORD_WEBHOOK_ROLE_ID` in your config

Leave the value empty (`""`) to send embed-only notifications without any ping.

### 📧 Gmail App Password

Google requires an **App Password** when using 2-Step Verification (which is recommended).

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Select **Mail** as the app and your device
3. Click **Generate** and copy the 16-character password
4. Paste it as `GMAIL_APP_PASSWORD` in your config

---

## 🕐 Scheduling with cron

To run the script every 5 minutes, add this line to your crontab (`crontab -e`):

```cron
*/5 * * * * /usr/bin/python3 /path/to/db-sbot/main.py >> /home/youruser/.db-sbot/cron.log 2>&1
```

If you're using `uv`:

```cron
*/5 * * * * /home/youruser/.local/bin/uv run --project /path/to/db-sbot main.py >> /home/youruser/.db-sbot/cron.log 2>&1
```

---

## 🔧 Scheduling with systemd (alternative to cron)

systemd timers are the modern alternative to cron. You need **two files**: a `.service` unit that runs the script once and a `.timer` unit that triggers it on a schedule.

### 1 - Create the service unit

```bash
mkdir -p ~/.config/systemd/user
nano ~/.config/systemd/user/db-sbot.service
```

Paste the following (adjust the paths to match your setup):

```ini
[Unit]
Description=db-sbot - Discord channel monitor (single run)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /path/to/db-sbot/main.py
# If you use uv, replace the line above with:
# ExecStart=/home/youruser/.local/bin/uv run --project /path/to/db-sbot main.py
StandardOutput=append:%h/.db-sbot/systemd.log
StandardError=append:%h/.db-sbot/systemd.log
```

### 2 - Create the timer unit

```bash
nano ~/.config/systemd/user/db-sbot.timer
```

```ini
[Unit]
Description=Run db-sbot every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

> 🕐 Change `OnUnitActiveSec` to any interval you like, e.g. `10min`, `1h`, `30s`.

### 3 - Enable and start the timer

```bash
systemctl --user daemon-reload
systemctl --user enable --now db-sbot.timer
```

### 4 - Useful commands

```bash
# Check timer status and next trigger time
systemctl --user list-timers db-sbot.timer

# View logs via journald
journalctl --user -u db-sbot.service

# Or tail the log file directly
tail -f ~/.db-sbot/systemd.log

# Run once immediately (without waiting for the timer)
systemctl --user start db-sbot.service

# Stop / disable the timer
systemctl --user disable --now db-sbot.timer
```

> 💡 **Tip:** If you want the timer to keep running when your user is **not logged in** (e.g. on a headless server), enable lingering:
> ```bash
> loginctl enable-linger $USER
> ```

---

## 📦 Building a standalone binary

Requires the `pyinstaller` dev dependency:

```bash
uv sync --group dev
uv run pyinstaller --onefile --name db-sbot main.py
```

The compiled binary will be at `dist/db-sbot`. Copy it anywhere on your `$PATH` and run it directly:

```bash
cp dist/db-sbot ~/.local/bin/
db-sbot
```

---

## 📁 Runtime file locations

| Path | Purpose |
|------|---------|
| `~/.config/db-sbot/db-sbot-config.json` | Config file (auto-created on first run) |
| `~/.config/db-sbot/db-sbot-config.debug.json` | Debug config file (auto-created on first `--debug` run) |
| `~/.db-sbot/state.db` | SQLite database - stores the last known channel list |
| `~/.db-sbot/state.debug.db` | SQLite database for debug mode |
| `~/.db-sbot/channel_log.txt` | Event log (new / deleted / renamed channels with timestamps) |
| `~/.db-sbot/channel_log.debug.txt` | Event log for debug mode |

---

## 📄 License

See [`LICENSE`](LICENSE).
