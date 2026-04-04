import json
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

with open(Path(__file__).parent / "config.json", encoding="utf-8") as _f:
    _cfg = json.load(_f)

USER_TOKEN         = _cfg["USER_TOKEN"]
SERVER_ID          = _cfg["SERVER_ID"]
GMAIL_ADDRESS      = _cfg["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = _cfg["GMAIL_APP_PASSWORD"]
EMAIL_RECIPIENTS   = _cfg["EMAIL_RECIPIENTS"]

CHECK_INTERVAL = 60
LOG_FILE = "channel_log.txt"

HEADERS = {
    "Authorization": USER_TOKEN,
    "Content-Type": "application/json"
}

def get_server_name():
    """Fetches the name of the server."""
    url = f"https://discord.com/api/v10/guilds/{SERVER_ID}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return response.json()["name"]
    else:
        print(f"Error fetching server name: {response.status_code}")
        return SERVER_ID

def get_channels():
    """Fetches all channels of the server."""
    url = f"https://discord.com/api/v10/guilds/{SERVER_ID}/channels"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return {ch["id"]: ch["name"] for ch in response.json()}
    else:
        print(f"Error: {response.status_code}")
        return None

def send_email(subject: str, body: str, recipients: list[str]):
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

def log_event(event: str, channel_name: str, channel_id: str):
    """Writes a channel event to the log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {event}: #{channel_name} (ID: {channel_id})\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

def main():
    server_name = get_server_name()
    print(f"🔍 Monitoring started on server: {server_name}")

    known_channels = get_channels()

    if known_channels is None:
        print("Could not load channels. Aborting.")
        return

    print(f"✅ {len(known_channels)} channels found. Monitoring...")

    while True:
        time.sleep(CHECK_INTERVAL)

        current_channels = get_channels()

        if current_channels is None:
            continue

        for ch_id, ch_name in current_channels.items():
            if ch_id not in known_channels:
                print(f"🆕 New channel: #{ch_name}")
                log_event("NEW CHANNEL", ch_name, ch_id)
                send_email(
                    subject=f"New Discord channel detected: #{ch_name}",
                    body=f"A new channel was created on the server.\n\nName: #{ch_name}\nID: {ch_id}",
                    recipients=EMAIL_RECIPIENTS,
                )

        for ch_id, ch_name in known_channels.items():
            if ch_id not in current_channels:
                print(f"🗑️ Channel deleted: #{ch_name}")
                log_event("CHANNEL DELETED", ch_name, ch_id)
                send_email(
                    subject=f"Discord channel deleted: #{ch_name}",
                    body=f"A channel was deleted from the server.\n\nName: #{ch_name}\nID: {ch_id}",
                    recipients=EMAIL_RECIPIENTS,
                )

        known_channels = current_channels

if __name__ == "__main__":
    main()
