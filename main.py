#!/usr/bin/env python3

import smtplib
import sqlite3
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

from utils_python.config_loader import load_config

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CFG_DEFAULTS = {
    "USER_TOKEN":                "your-discord-user-token-here",
    "SERVER_ID":                 "your-server-id-here",
    "LOGGING_ENABLED":           True,
    "DETECT_NEW_CHANNELS":       True,
    "DETECT_REMOVED_CHANNELS":   True,
    "EMAIL_ENABLED":             False,
    "GMAIL_ADDRESS":             "your-gmail@gmail.com",
    "GMAIL_APP_PASSWORD":        "your-gmail-app-password",
    "EMAIL_RECIPIENTS":          [],
    "DISCORD_WEBHOOK_ENABLED":   False,
    "DISCORD_WEBHOOK_URL":       "your-discord-webhook-url-here",
    "DISCORD_WEBHOOK_ROLE_ID":   "",
}

_cfg = load_config(
    app_name="db-sbot",
    config_filename="db-sbot-config.json",
    defaults=_CFG_DEFAULTS,
    caller_file=__file__,
)

USER_TOKEN               = _cfg["USER_TOKEN"]
SERVER_ID                = _cfg["SERVER_ID"]
LOGGING_ENABLED          = _cfg.get("LOGGING_ENABLED", True)
DETECT_NEW_CHANNELS      = _cfg.get("DETECT_NEW_CHANNELS", True)
DETECT_REMOVED_CHANNELS  = _cfg.get("DETECT_REMOVED_CHANNELS", True)

# Email notification settings
EMAIL_ENABLED      = _cfg.get("EMAIL_ENABLED", False)
GMAIL_ADDRESS      = _cfg.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = _cfg.get("GMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENTS   = _cfg.get("EMAIL_RECIPIENTS", [])

# Discord webhook notification settings
DISCORD_WEBHOOK_ENABLED = _cfg.get("DISCORD_WEBHOOK_ENABLED", False)
DISCORD_WEBHOOK_URL     = _cfg.get("DISCORD_WEBHOOK_URL", "")
DISCORD_WEBHOOK_ROLE_ID = _cfg.get("DISCORD_WEBHOOK_ROLE_ID", "")

# ---------------------------------------------------------------------------
# Persistent state — SQLite in ~/.db-sbot/
# ---------------------------------------------------------------------------

DATA_DIR = Path.home() / ".db-sbot"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH  = DATA_DIR / "state.db"
LOG_FILE = DATA_DIR / "channel_log.txt"

HEADERS = {
    "Authorization": USER_TOKEN,
    "Content-Type": "application/json"
}

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if it doesn't exist yet."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id   TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
        """
    )
    conn.commit()


def load_known_channels(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {id: name} for every channel stored in the DB."""
    rows = conn.execute("SELECT id, name FROM channels").fetchall()
    return {row[0]: row[1] for row in rows}


def save_channels(conn: sqlite3.Connection, channels: dict[str, str]) -> None:
    """Replace the stored channel list with the current snapshot."""
    conn.execute("DELETE FROM channels")
    conn.executemany(
        "INSERT INTO channels (id, name) VALUES (?, ?)",
        channels.items(),
    )
    conn.commit()

# ---------------------------------------------------------------------------
# Discord API
# ---------------------------------------------------------------------------

def get_server_name() -> str:
    """Fetches the name of the server."""
    url = f"https://discord.com/api/v10/guilds/{SERVER_ID}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()["name"]
    print(f"Error fetching server name: {response.status_code}")
    return SERVER_ID


def get_channels() -> dict[str, str] | None:
    """Fetches all channels of the server."""
    url = f"https://discord.com/api/v10/guilds/{SERVER_ID}/channels"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return {ch["id"]: ch["name"] for ch in response.json()}
    print(f"Error: {response.status_code}")
    return None

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str, recipients: list[str]) -> None:
    """Sends an email from the configured Gmail account to one or more recipients."""
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, recipients, msg.as_string())
        print(f"📧 Email sent to: {', '.join(recipients)}")
    except Exception as e:
        print(f"Error sending email: {e}")


def send_discord_webhook(title: str, description: str, color: int, role_id: str = "") -> None:
    """Sends a Discord embed notification via a configured webhook URL.

    If *role_id* is provided the message content will contain a role mention
    so that members with that role receive a ping.
    """
    payload: dict = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ]
    }
    if role_id:
        payload["content"] = f"<@&{role_id}>"
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code in (200, 204):
            print("🔔 Discord webhook notification sent.")
        else:
            print(f"Error sending Discord webhook: {response.status_code} – {response.text}")
    except Exception as e:
        print(f"Error sending Discord webhook: {e}")


def notify(subject: str, body: str, title: str, color: int) -> None:
    """Dispatches notifications to all enabled channels (email, Discord webhook)."""
    if EMAIL_ENABLED and EMAIL_RECIPIENTS:
        send_email(subject=subject, body=body, recipients=EMAIL_RECIPIENTS)
    if DISCORD_WEBHOOK_ENABLED and DISCORD_WEBHOOK_URL:
        send_discord_webhook(title=title, description=body, color=color, role_id=DISCORD_WEBHOOK_ROLE_ID)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_event(event: str, channel_name: str, channel_id: str) -> None:
    """Appends a channel event to the log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {event}: #{channel_name} (ID: {channel_id})\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

# ---------------------------------------------------------------------------
# Main — single-shot execution
# ---------------------------------------------------------------------------

def main() -> None:
    server_name = get_server_name()
    print(f"🔍 Checking server: {server_name}")

    current_channels = get_channels()
    if current_channels is None:
        print("Could not fetch channels. Aborting.")
        raise SystemExit(1)

    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        known_channels = load_known_channels(conn)

        if not known_channels:
            # First run — seed the database and exit
            save_channels(conn, current_channels)
            print(
                f"✅ First run: {len(current_channels)} channels stored in {DB_PATH}.\n"
                "Run the script again (e.g. via cron) to detect changes."
            )
            return

        # Detect new channels
        if DETECT_NEW_CHANNELS:
            for ch_id, ch_name in current_channels.items():
                if ch_id not in known_channels:
                    print(f"🆕 New channel: #{ch_name}")
                    if LOGGING_ENABLED:
                        log_event("NEW CHANNEL", ch_name, ch_id)
                    notify(
                        subject=f"New Discord channel detected: #{ch_name}",
                        body=f"A new channel was created on the server.\n\nName: #{ch_name}\nID: {ch_id}",
                        title=f"🆕 New channel: #{ch_name}",
                        color=0x57F287,  # green
                    )

        # Detect deleted channels
        if DETECT_REMOVED_CHANNELS:
            for ch_id, ch_name in known_channels.items():
                if ch_id not in current_channels:
                    print(f"🗑️ Channel deleted: #{ch_name}")
                    if LOGGING_ENABLED:
                        log_event("CHANNEL DELETED", ch_name, ch_id)
                    notify(
                        subject=f"Discord channel deleted: #{ch_name}",
                        body=f"A channel was deleted from the server.\n\nName: #{ch_name}\nID: {ch_id}",
                        title=f"🗑️ Channel deleted: #{ch_name}",
                        color=0xED4245,  # red
                    )

        # Persist the latest snapshot
        save_channels(conn, current_channels)
        print("✅ Done.")


if __name__ == "__main__":
    main()
