import requests
import datetime
import urllib.parse
from email.mime.text import MIMEText
from ratelimit import limits, sleep_and_retry
import random
import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv; load_dotenv()
import os
from dataclasses import dataclass
import keyring
from typing import Optional

# Configuration
CAMPGROUND_ID = "233359"
CHECK_DATE = "2025-09-26"  # YYYY-MM-DD
EMAIL_ALERTS = True
WATCH_CAMPS = ["sky", "coast"]
DISREGARD_CAMPS = ["boat"]

@dataclass(frozen=True)
class EmailConfig:
    smtp_server: str
    smtp_port: int
    username: str
    password: str
    from_email: str
    to_email: str

import base64, os, json
from email.message import EmailMessage
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

dSCOPES = ["https://www.googleapis.com/auth/gmail.send"]
SERVICE = os.getenv("GMAIL_KEYRING_SERVICE", "gmail-api-token")  # must match your bootstrap script
ACCOUNT = os.getenv("ALERT_FROM", "bae.rich@gmail.com")               # same email you used at bootstrap

def load_creds(service: str = SERVICE, account: str = ACCOUNT, scopes=SCOPES) -> Credentials:
    """Load Gmail OAuth creds JSON from Keychain, refresh if needed, then persist the update."""
    data = keyring.get_password(service, account)
    if not data:
        raise RuntimeError(f"No credentials found in keyring for service={service} account={account}")
    info = json.loads(data)
    creds = Credentials.from_authorized_user_info(info, scopes)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        # Save the refreshed token set back to Keychain
        keyring.set_password(service, account, creds.to_json())
    return creds

def send_email(subject: str, body: str, to_addr: str, from_addr: Optional[str] = None) -> None:
    """Send a simple text email via Gmail API using creds from Keychain."""
    from_addr = from_addr or ACCOUNT
    creds = load_creds()
    service = build("gmail", "v1", credentials=creds)

    msg = EmailMessage()
    msg["To"] = to_addr
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg.add_alternative(body, subtype="html")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print("✅ Email alert sent.")
    except Exception as e:
        print(f"⚠️ Email failed: {e}")
    

@sleep_and_retry
@limits(calls=5, period=60)  # 5 API calls max per 60 seconds
def rate_limited_request(url, headers):
    return requests.get(url, headers=headers)


def fetch_campsite_name(site_id: str) -> str:
    """Lookup the human-friendly name/loop for a given campsite ID."""
    url = f"https://www.recreation.gov/api/campsite/{site_id}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = rate_limited_request(url, headers)
        resp.raise_for_status()
        data = resp.json()
        loop = data.get("loop", "")
        camp_name = data.get("campsite_name") or data.get("name", "")
        if loop and camp_name:
            return f"{loop} {camp_name}".strip()
        return loop or camp_name
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Error fetching name for site {site_id}: {e}")
        return ""

def check_availability(campground_id, check_date):
    date_obj = datetime.datetime.strptime(check_date, "%Y-%m-%d")
    start_date = date_obj.replace(day=1).strftime("%Y-%m-%dT00:00:00.000Z")
    encoded_start_date = urllib.parse.quote(start_date)
    url = f"https://www.recreation.gov/api/camps/availability/campground/{campground_id}/month?start_date={encoded_start_date}"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = rate_limited_request(url, headers)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data: {e}")
        return []

    data = response.json()
    check_datetime_str = f"{check_date}T00:00:00Z"
    available_sites = []

    for site_id, site_data in data.get("campsites", {}).items():
        status = site_data.get("availabilities", {}).get(check_datetime_str)
        if status != "Available":
            continue

        site_name = fetch_campsite_name(site_id)
        name_lower = site_name.lower()

        if any(camp in name_lower for camp in DISREGARD_CAMPS):
            continue  # skip unmonitored sites
        if WATCH_CAMPS and not any(camp in name_lower for camp in WATCH_CAMPS):
            continue

        available_sites.append({
            "site_id": site_id,
            "site_name": site_name,
        })
        
    return available_sites

def main():
    available_sites = check_availability(CAMPGROUND_ID, CHECK_DATE)
    while True:
        PST = ZoneInfo("America/Los_Angeles")
        now = datetime.datetime.now(PST)
        '''
        if 0 <= now.hour < 6:
            print("🌙 Sleeping until 6 AM...")
            # Sleep until 6:00 AM
            next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)
            sleep_seconds = (next_run - now).total_seconds()
            time.sleep(sleep_seconds)
            continue'''
        print(f"🔍 Checking availability for {CHECK_DATE}...")

        if available_sites:
            print(f"🎉 Available sites on {CHECK_DATE}:")
            for site in available_sites:
                print(f"- Site ID: {site['site_id']}, Name: {site['site_name']}")
            if EMAIL_ALERTS:
                body = "<h2>🎉 Available Campsites:</h2><ul>"
                body += "".join([
                    (
                        f"<li><a href='https://www.recreation.gov/camping/campsites/{s['site_id']}' "
                        f"target='_blank'><strong>Site ID:</strong> {s['site_id']} — {s['site_name']}</a></li>"
                    )
                    for s in available_sites
                ])
                body += "</ul>"
                send_email(f"Campground Available on {CHECK_DATE}", body, ACCOUNT, ACCOUNT)
        else:
            print(f"🚫 No available sites on {CHECK_DATE}.")

        wait_minutes = random.randint(5, 10)
        print(f"⏳ Waiting {wait_minutes} minutes before next check...\n")
        time.sleep(wait_minutes * 60)

if __name__ == "__main__":
    main()
