import requests
import datetime
import urllib.parse
import smtplib
from email.mime.text import MIMEText
from ratelimit import limits, sleep_and_retry
import random
import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv; load_dotenv()
import os
from dataclasses import dataclass

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

def load_email_config() -> EmailConfig:
    # Non-sensitive defaults can live in code or a non-secret config file
    smtp_server = os.environ.get("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
    username = os.environ["EMAIL_USERNAME"]
    password = os.environ["EMAIL_PASSWORD"]
    from_email = os.environ["EMAIL_FROM"]
    to_email = os.environ["EMAIL_TO"]

    return EmailConfig(
        smtp_server=smtp_server,
        smtp_port=smtp_port,
        username=username,
        password=password,
        from_email=from_email,
        to_email=to_email,
    )

def send_email(subject, body, config):
    msg = MIMEText(body, "html")
    msg["Subject"] = subject
    msg["From"] = config["from_email"]
    msg["To"] = config["to_email"]

    try:
        with smtplib.SMTP(config["smtp_server"], config["smtp_port"]) as server:
            server.starttls()
            server.login(config["username"], config["password"])
            server.send_message(msg)
        print("✅ Email alert sent.")
    except Exception as e:
        print(f"⚠️ Email failed: {e}")

@sleep_and_retry
@limits(calls=5, period=60)  # 5 API calls max per 60 seconds
def rate_limited_request(url, headers):
    return requests.get(url, headers=headers)

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
        name = site_data.get("site", "").lower()
        if any(camp in name for camp in DISREGARD_CAMPS):
            continue  # skip unmonitored sites
        if status == "Available":
            available_sites.append({
                "site_id": site_id,
                "site_name": site_data.get("site")
            })
        
    return available_sites

def main():
    available_sites = check_availability(CAMPGROUND_ID, CHECK_DATE)
    cfg = load_email_config()
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
                    f"<li><strong>Site ID:</strong> {s['site_id']} — {s['site_name']}</li>"
                    for s in available_sites
                ])
                body += f"</ul><p><a href='https://www.recreation.gov/camping/campsites/{CAMPGROUND_ID}?tab=campsites' target='_blank'>Book Now</a></p>"
                send_email(f"Campground Available on {CHECK_DATE}", body, cfg)
        else:
            print(f"🚫 No available sites on {CHECK_DATE}.")

        wait_minutes = random.randint(5, 10)
        print(f"⏳ Waiting {wait_minutes} minutes before next check...\n")
        time.sleep(wait_minutes * 60)

if __name__ == "__main__":
    main()