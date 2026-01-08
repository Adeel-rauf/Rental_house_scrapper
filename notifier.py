import os
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime


SEEN_FILE = "seen_links.json"


def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("links", []))


def save_seen(links: set[str]) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "links": sorted(list(links)),
    }
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def pick_new(rows: list[dict], seen: set[str]) -> list[dict]:
    new_rows = []
    for r in rows:
        link = (r.get("link") or "").strip()
        if link and link not in seen:
            new_rows.append(r)
    return new_rows


def build_email_body(new_rows: list[dict]) -> str:
    lines = []
    lines.append(f"New Zameen rental listings found: {len(new_rows)}")
    lines.append("")

    for i, r in enumerate(new_rows, start=1):
        price = r.get("price_text", "")
        beds = r.get("beds", "")
        baths = r.get("baths", "")
        area = r.get("area", "")
        unit = r.get("area_unit", "")
        address = r.get("address", "")   # ✅ ADD THIS
        link = r.get("link", "")

        # First line: price + address
        if address:
            lines.append(f"{i}) {price} | {address}")
        else:
            lines.append(f"{i}) {price}")

        # Second line: property details
        lines.append(f"   {beds} bed | {baths} bath | {area} {unit}")

        # Third line: link
        lines.append(f"   {link}")
        lines.append("")

    return "\n".join(lines)



def send_email_smtp(
    *,
    subject: str,
    body: str,
    to_email: str,
    from_email: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(body)

    # TLS SMTP (works for Gmail/Outlook etc.)
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
