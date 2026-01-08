import re
import json
import csv
from urllib.parse import urljoin
import os
from notifier import (
    load_seen,
    save_seen,
    build_email_body,
    send_email_smtp,
)

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.zameen.com"


# -----------------------------
# Helpers
# -----------------------------
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def first_nonempty(*vals):
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and clean(v):
            return clean(v)
        if isinstance(v, (int, float)):
            return str(v)
    return ""

def normalize_link(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("http"):
        return href
    return urljoin(BASE, href)

# import re

PROPERTY_RE = re.compile(
    r"^https://www\.zameen\.com/Property/.+-\d+-\d+-4\.html$"
)

def is_real_listing(url: str) -> bool:
    return bool(PROPERTY_RE.match(url))



# -----------------------------
# Detail page parser (robust)
# -----------------------------
def try_parse_jsonld(soup: BeautifulSoup) -> dict:
    """
    Extracts fields from JSON-LD if present.
    """
    out = {}
    scripts = soup.select('script[type="application/ld+json"]')
    for sc in scripts:
        raw = sc.get_text(strip=True)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue

        items = obj if isinstance(obj, list) else [obj]
        for it in items:
            if not isinstance(it, dict):
                continue

            offers = it.get("offers")
            if isinstance(offers, dict):
                out["price"] = first_nonempty(
                    offers.get("price"),
                    offers.get("priceSpecification", {}).get("price")
                    if isinstance(offers.get("priceSpecification"), dict)
                    else None
                )
                out["priceCurrency"] = first_nonempty(offers.get("priceCurrency"))

            out["beds"] = first_nonempty(it.get("numberOfBedrooms"), it.get("numberOfRooms"))
            out["baths"] = first_nonempty(it.get("numberOfBathroomsTotal"))

            floor = it.get("floorSize")
            if isinstance(floor, dict):
                out["area"] = first_nonempty(floor.get("value"))
                out["areaUnit"] = first_nonempty(floor.get("unitText"))

            out["url"] = first_nonempty(it.get("url"))

    return out

def html_fallback_extract(soup: BeautifulSoup, page_url: str) -> dict:
    """
    Fallback if JSON-LD missing/empty.
    """
    text = clean(soup.get_text(" "))
    out = {"link": page_url}

    # Price (text form)
    m_price = re.search(r"\bPKR\s*[\d,]+(?:\.\d+)?(?:\s*(?:Thousand|Lac|Lakh|Crore))?\b", text, re.I)
    out["price_text"] = m_price.group(0) if m_price else ""

    # Beds / Baths
    m_beds = re.search(r"\b(\d+)\s*(Beds?|Bedrooms?)\b", text, re.I)
    m_baths = re.search(r"\b(\d+)\s*(Baths?|Bathrooms?)\b", text, re.I)
    out["beds"] = m_beds.group(1) if m_beds else ""
    out["baths"] = m_baths.group(1) if m_baths else ""

    # Area
    m_area = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(Sq\.?\s*Yd\.?|Sq\.?\s*Ft\.?|Marla|Kanal)\b", text, re.I)
    if m_area:
        out["area"] = clean(m_area.group(1))
        out["area_unit"] = clean(m_area.group(2))
    else:
        out["area"] = ""
        out["area_unit"] = ""

    return out

def parse_detail_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    j = try_parse_jsonld(soup)

    price = first_nonempty(j.get("price"))
    currency = first_nonempty(j.get("priceCurrency"))
    beds = first_nonempty(j.get("beds"))
    baths = first_nonempty(j.get("baths"))
    area = first_nonempty(j.get("area"))
    area_unit = first_nonempty(j.get("areaUnit"))
    link = first_nonempty(j.get("url"), url)

    # -----------------------------
    # Address: JSON-LD first, then HTML fallback
    # -----------------------------
    address = ""

    # 1) JSON-LD address may be a dict or string depending on schema
    addr_obj = j.get("address")
    if isinstance(addr_obj, dict):
        # Prefer streetAddress (often contains "Federal B Area - Block X, ...")
        address = first_nonempty(
            addr_obj.get("streetAddress"),
            addr_obj.get("name"),
            addr_obj.get("addressLocality"),
        )
    elif isinstance(addr_obj, str):
        address = addr_obj.strip()

    # 2) HTML fallback (BEST: location line under the title, includes Block)
    if not address:
        # This line is usually a clickable <a> under the main title and contains:
        # "Federal B Area - Block 5, Federal B Area, Karachi, Sindh"
        loc_el = soup.select_one('h1 + a, h1 ~ a[href*="/Karachi/"], h1 ~ a[href*="/Federal_B_Area/"]')
        if loc_el:
            address = loc_el.get_text(" ", strip=True)

    # 3) Backup HTML fallbacks (if above selector fails)
    if not address:
        candidates = [
            'a[href*="/Karachi/"]',
            'a[href*="Federal_B_Area"]',
            'span[aria-label="Location"]',
            'div[aria-label="Location"]',
            '[class*="location" i]',
            '[class*="address" i]',
        ]
        for sel in candidates:
            el = soup.select_one(sel)
            if el:
                txt = el.get_text(" ", strip=True)
                # avoid grabbing huge page text; address lines are short
                if txt and len(txt) <= 140 and "PKR" not in txt:
                    address = txt
                    break

    address = address.strip()


    if not (price or beds or baths or area):
        fb = html_fallback_extract(soup, url)
        return {
            "price_text": fb.get("price_text", ""),
            "beds": fb.get("beds", ""),
            "baths": fb.get("baths", ""),
            "area": fb.get("area", ""),
            "area_unit": fb.get("area_unit", ""),
            "address": address,  # ✅ added
            "link": fb.get("link", url),
        }

    # readable price_text
    price_text = ""
    if currency and price:
        price_text = f"{currency} {price}"
    elif price:
        price_text = str(price)

    return {
        "price_text": price_text,
        "beds": str(beds),
        "baths": str(baths),
        "area": str(area),
        "area_unit": str(area_unit),
        "address": address,  # ✅ added
        "link": normalize_link(link),
    }



# -----------------------------
# Results page: collect listing links
# -----------------------------
def collect_listing_links(page) -> list[str]:
    """
    Collects listing detail links from the currently loaded results page.
    Uses DOM evaluation (fast) + filters to "/Property/" links.
    """
    hrefs = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
    )
    links = []
    for h in hrefs:
        u = normalize_link(h)
        if is_real_listing(u):
            links.append(u)


    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for u in links:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


PAGE_RE = re.compile(r"-\d+-\d+\.html")

def build_page_urls(first_page_url: str, max_pages=20):
    """
    Builds page URLs like:
    -12-1.html
    -12-2.html
    -12-3.html
    """
    urls = []
    for page_no in range(1, max_pages + 1):
        url = PAGE_RE.sub(f"-12-{page_no}.html", first_page_url)
        urls.append(url)
    return urls

def go_next_page(page) -> bool:
    """
    Tries to click the next page button.
    Returns True if it navigated, else False.
    """
    candidates = [
        'a[title*="Next" i]',
        'a:has-text("Next")',
        'a[aria-label*="Next" i]'
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                loc.click(timeout=1500)
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                page.wait_for_timeout(1200)
                return True
            except PlaywrightTimeoutError:
                return False
            except Exception:
                return False
    return False


# -----------------------------
# Main end-to-end flow
# -----------------------------
def scrape_zameen(results_url: str, max_pages: int = 2, max_listings: int | None = None) -> list[dict]:
    """
    max_pages: how many results pages to scan (set higher if you want more)
    max_listings: limit number of detail pages to visit (None = no limit)
    """
    rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        # ===============================
# PAGINATION (CORRECT WAY)
# ===============================
        page_urls = build_page_urls(
            results_url,
            max_pages=15   # increase if needed
        )

        all_links = []

        for idx, url in enumerate(page_urls, start=1):
            print(f"Scanning page {idx}: {url}")

            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            links = collect_listing_links(page)

            if not links:
                print("No more listings found, stopping pagination.")
                break

            all_links.extend(links)

        # Deduplicate links
        uniq_links = list(dict.fromkeys(all_links))
        print(f"Total unique listings found: {len(uniq_links)}")

        # # 1) Open results
        # page.goto(results_url, wait_until="domcontentloaded")
        # page.wait_for_timeout(1500)

        # # 2) Collect listing links across pages
        # all_links = []
        # for i in range(max_pages):
        #     links = collect_listing_links(page)
        #     all_links.extend(links)

        #     # stop early if limit reached
        #     if max_listings is not None and len(set(all_links)) >= max_listings:
        #         break

        #     # go next
        #     if i < max_pages - 1:
        #         moved = go_next_page(page)
        #         if not moved:
        #             break

        # # Deduplicate links overall
        # seen = set()
        # uniq_links = []
        # for u in all_links:
        #     if u not in seen:
        #         seen.add(u)
        #         uniq_links.append(u)

        # if max_listings is not None:
        #     uniq_links = uniq_links[:max_listings]

        # print(f"Collected {len(uniq_links)} unique listing links")

        # 3) Visit each detail page and extract fields
        detail_page = context.new_page()
        for idx, link in enumerate(uniq_links, start=1):
            try:
                detail_page.goto(link, wait_until="domcontentloaded", timeout=30000)
                detail_page.wait_for_timeout(900)
                html = detail_page.content()
                data = parse_detail_page(html, link)
                rows.append(data)
                print(f"[{idx}/{len(uniq_links)}] OK: {data.get('price_text','')} | {data.get('beds','')} bed | {data.get('area','')} {data.get('area_unit','')} | {data.get('address','')}") 
            except Exception as e:
                print(f"[{idx}/{len(uniq_links)}] FAIL: {link} ({e})")

        browser.close()

    return rows


if __name__ == "__main__":
    RESULTS_URL = "https://www.zameen.com/Rentals/Karachi_Federal_B._Area-12-1.html?price_max=100000"

    # Tune these:
    MAX_PAGES = 2        # increase to 5/10 for more results
    MAX_LISTINGS = None    # None for unlimited (not recommended initially)

    data = scrape_zameen(RESULTS_URL, max_pages=MAX_PAGES, max_listings=MAX_LISTINGS)

    # Save CSV
    out_csv = "zameen_rentals_federalbarea.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["price_text", "beds", "baths", "area","address", "area_unit", "link"])
        writer.writeheader()
        writer.writerows(data)

    print(f"Saved -> {out_csv} ({len(data)} rows)")
# ===============================
# EMAIL + FIRST-RUN / DELTA LOGIC
# ===============================

    seen = load_seen()

    today_links = {r["link"] for r in data if r.get("link")}
    new_links = today_links - seen

    # FIRST RUN → send ALL listings
    if not seen:
        print("First run detected: sending all listings")
        rows_to_email = data

    # SUBSEQUENT RUNS → send ONLY new listings
    else:
        rows_to_email = [r for r in data if r.get("link") in new_links]

    # Send email only if something to send
    if rows_to_email:
        body = build_email_body(rows_to_email)
        send_email_smtp(
            subject=f"Zameen Rentals Update: {len(rows_to_email)} listing(s)",
            body=body,
            to_email=os.environ["TO_EMAIL"],
            from_email=os.environ["FROM_EMAIL"],
            smtp_host=os.environ["SMTP_HOST"],
            smtp_port=int(os.environ.get("SMTP_PORT", "587")),
            smtp_user=os.environ["SMTP_USER"],
            smtp_password=os.environ["SMTP_PASS"],
        )
        print("Email sent.")
            # Flag for GitHub Actions: upload CSV artifact only when new listings exist
        with open("new_listings.flag", "w", encoding="utf-8") as f:
            f.write(str(len(rows_to_email)))

    else:
        print("No new listings. No email sent.")

    # Update seen store AFTER email logic
    save_seen(seen.union(today_links))
