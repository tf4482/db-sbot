#!/usr/bin/env python3

import argparse
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

from utils_python.config_loader import load_config

# ---------------------------------------------------------------------------
# CLI arguments — parsed first so DEBUG is available for everything below
# ---------------------------------------------------------------------------

_parser = argparse.ArgumentParser(description="Discord server channel monitor bot")
_parser.add_argument(
    "-d", "--debug",
    action="store_true",
    help="Run in debug mode (uses a separate config file and database)",
)
_args = _parser.parse_args()
DEBUG: bool = _args.debug

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CFG_DEFAULTS = {
    "USER_TOKEN":                "your-discord-user-token-here",
    "SERVER_ID":                 "your-server-id-here",
    "LOGGING_ENABLED":           True,
    "DETECT_NEW_CHANNELS":       True,
    "DETECT_REMOVED_CHANNELS":   True,
    "DETECT_RENAMED_CHANNELS":   True,
    "DETECT_USER_POSTS":         False,
    "WATCH_USERNAME":            "",
    "WATCH_POST_MESSAGE_LIMIT":  50,
    "EMAIL_ENABLED":             False,
    "GMAIL_ADDRESS":             "your-gmail@gmail.com",
    "GMAIL_APP_PASSWORD":        "your-gmail-app-password",
    "EMAIL_RECIPIENTS":          [],
    "DISCORD_WEBHOOK_ENABLED":   False,
    "DISCORD_WEBHOOK_URL":       "your-discord-webhook-url-here",
    "DISCORD_WEBHOOK_ROLE_ID":   "",
}

_config_filename = "db-sbot-config.debug.json" if DEBUG else "db-sbot-config.json"

_cfg = load_config(
    app_name="db-sbot",
    config_filename=_config_filename,
    defaults=_CFG_DEFAULTS,
    caller_file=__file__,
)

USER_TOKEN               = _cfg["USER_TOKEN"]
SERVER_ID                = _cfg["SERVER_ID"]
LOGGING_ENABLED          = _cfg.get("LOGGING_ENABLED", True)
DETECT_NEW_CHANNELS      = _cfg.get("DETECT_NEW_CHANNELS", True)
DETECT_REMOVED_CHANNELS  = _cfg.get("DETECT_REMOVED_CHANNELS", True)
DETECT_RENAMED_CHANNELS  = _cfg.get("DETECT_RENAMED_CHANNELS", True)
DETECT_USER_POSTS        = _cfg.get("DETECT_USER_POSTS", False)
WATCH_USERNAME           = _cfg.get("WATCH_USERNAME", "").strip()

_watch_limit_raw = _cfg.get("WATCH_POST_MESSAGE_LIMIT", 50)
try:
    WATCH_POST_MESSAGE_LIMIT = max(1, min(100, int(_watch_limit_raw)))
except (TypeError, ValueError):
    WATCH_POST_MESSAGE_LIMIT = 50

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

DB_PATH  = DATA_DIR / ("state.debug.db"        if DEBUG else "state.db")
LOG_FILE = DATA_DIR / ("channel_log.debug.txt" if DEBUG else "channel_log.txt")

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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watched_channels (
            channel_id      TEXT PRIMARY KEY,
            channel_name    TEXT NOT NULL,
            reason          TEXT NOT NULL,
            added_at        TEXT NOT NULL,
            last_message_id TEXT
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


def upsert_watched_channel(
    conn: sqlite3.Connection,
    channel_id: str,
    channel_name: str,
    reason: str,
) -> None:
    """Insert or refresh a watched channel while preserving last_message_id."""
    conn.execute(
        """
        INSERT INTO watched_channels (channel_id, channel_name, reason, added_at, last_message_id)
        VALUES (?, ?, ?, ?, NULL)
        ON CONFLICT(channel_id) DO UPDATE SET
            channel_name = excluded.channel_name,
            reason       = excluded.reason,
            added_at     = excluded.added_at
        """,
        (channel_id, channel_name, reason, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def load_watched_channels(conn: sqlite3.Connection) -> list[tuple[str, str, str, str | None]]:
    """Returns watched channels as (channel_id, channel_name, reason, last_message_id)."""
    rows = conn.execute(
        """
        SELECT channel_id, channel_name, reason, last_message_id
        FROM watched_channels
        """
    ).fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def update_watched_last_message_id(
    conn: sqlite3.Connection,
    channel_id: str,
    last_message_id: str,
) -> None:
    """Stores the newest processed message id for a watched channel."""
    conn.execute(
        """
        UPDATE watched_channels
        SET last_message_id = ?
        WHERE channel_id = ?
        """,
        (last_message_id, channel_id),
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


def get_channel_messages(channel_id: str, after: str | None = None, limit: int = 50) -> list[dict] | None:
    """Fetches recent messages from one channel.

    If *after* is given, only messages newer than that message ID are returned.
    """
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    params: dict[str, str | int] = {"limit": max(1, min(100, limit))}
    if after:
        params["after"] = after

    response = requests.get(url, headers=HEADERS, params=params)
    if response.status_code == 200:
        return response.json()
    print(f"Error fetching messages for channel {channel_id}: {response.status_code}")
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
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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

def log_event(event: str, channel_name: str, channel_id: str, old_name: str = "") -> None:
    """Appends a channel event to the log file.

    For rename events *old_name* contains the previous channel name so the log
    entry shows ``#old_name -> #new_name``.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if old_name:
        line = f"[{timestamp}] {event}: #{old_name} -> #{channel_name} (ID: {channel_id})\n"
    else:
        line = f"[{timestamp}] {event}: #{channel_name} (ID: {channel_id})\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

# ---------------------------------------------------------------------------
# Main — single-shot execution
# ---------------------------------------------------------------------------

def main() -> None:
    if DEBUG:
        print(
            "⚠️  DEBUG MODE\n"
            f"   Config : {_config_filename}\n"
            f"   DB     : {DB_PATH}\n"
            f"   Log    : {LOG_FILE}\n"
        )

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
                    upsert_watched_channel(conn, ch_id, ch_name, reason="new")
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

        # Detect renamed channels (same ID, different name)
        if DETECT_RENAMED_CHANNELS:
            for ch_id, new_name in current_channels.items():
                if ch_id in known_channels:
                    old_name = known_channels[ch_id]
                    if old_name != new_name:
                        print(f"✏️ Channel renamed: #{old_name} -> #{new_name}")
                        if LOGGING_ENABLED:
                            log_event("CHANNEL RENAMED", new_name, ch_id, old_name=old_name)
                        upsert_watched_channel(conn, ch_id, new_name, reason="renamed")
                        notify(
                            subject=f"Discord channel renamed: #{old_name} -> #{new_name}",
                            body=(
                                f"A channel was renamed on the server.\n\n"
                                f"Old name: #{old_name}\n"
                                f"New name: #{new_name}\n"
                                f"ID: {ch_id}"
                            ),
                            title=f"✏️ Channel renamed: #{old_name} → #{new_name}",
                            color=0xFEE75C,  # yellow
                        )

        # Detect posts by watched username in channels previously marked as new/renamed
        if DETECT_USER_POSTS and WATCH_USERNAME:
            watched_channels = load_watched_channels(conn)
            for ch_id, watched_name, reason, last_message_id in watched_channels:
                if ch_id not in current_channels:
                    # Channel no longer exists or is not accessible right now.
                    continue

                ch_name = current_channels.get(ch_id, watched_name)
                messages = get_channel_messages(
                    channel_id=ch_id,
                    after=last_message_id,
                    limit=WATCH_POST_MESSAGE_LIMIT,
                )
                if messages is None or not messages:
                    continue

                sorted_messages = sorted(messages, key=lambda msg: int(msg.get("id", 0)))
                newest_message_id = sorted_messages[-1].get("id")

                for msg in sorted_messages:
                    author = msg.get("author") or {}
                    username = author.get("username", "")
                    if username != WATCH_USERNAME:
                        continue

                    message_id = msg.get("id", "")
                    content = (msg.get("content") or "").strip() or "[no text content]"
                    if len(content) > 300:
                        content = content[:297] + "..."

                    timestamp = msg.get("timestamp", "unknown")
                    message_url = f"https://discord.com/channels/{SERVER_ID}/{ch_id}/{message_id}"

                    print(f"👤 Watched user post: @{WATCH_USERNAME} in #{ch_name}")
                    if LOGGING_ENABLED:
                        log_event(f"USER POST by @{WATCH_USERNAME}", ch_name, ch_id)
                    notify(
                        subject=f"Post by @{WATCH_USERNAME} in #{ch_name}",
                        body=(
                            f"A watched user posted in a tracked channel.\n\n"
                            f"User: @{WATCH_USERNAME}\n"
                            f"Channel: #{ch_name}\n"
                            f"Tracked because: {reason}\n"
                            f"Time: {timestamp}\n"
                            f"Message: {content}\n"
                            f"Link: {message_url}"
                        ),
                        title=f"👤 Post by @{WATCH_USERNAME} in #{ch_name}",
                        color=0x5865F2,  # Discord blurple
                    )

                if newest_message_id:
                    update_watched_last_message_id(conn, ch_id, newest_message_id)

        # Persist the latest snapshot
        save_channels(conn, current_channels)
        print("✅ Done.")


if __name__ == "__main__":
    main()
