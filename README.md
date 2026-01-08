Rental House Scraper & Alert System

A Python automation that monitors rental listings on Zameen, detects new properties, and sends email alerts — fully automated using GitHub Actions.

Features

Dynamic scraping with Playwright

Extracts price, beds, baths, area, full address (with block), and link

Alerts only for new listings (no duplicates)

Email notifications via SMTP

CSV report uploaded as GitHub Actions artifact

Runs 3× daily on schedule

How It Works

Scrapes rental listings for a configured location and price range

Compares results with previously seen listings

Sends email + uploads CSV only if new listings are found

Persists state across runs using a JSON file

Schedule

Runs automatically at:

11:00 AM, 4:00 PM, 8:00 PM (Pakistan Time)

Project Structure
Rental_house_scrapper/
├── z_scrapper.py
├── notifier.py
├── requirements.txt
├── seen_links.json
└── .github/workflows/scrape.yml

Tech Stack

Python · Playwright · BeautifulSoup · GitHub Actions · SMTP · CSV
