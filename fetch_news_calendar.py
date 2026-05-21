"""
fetch_news_calendar.py
Fetches high-impact economic calendar events from Forex Factory
for USD, EUR, JPY, CAD from 2023-08-01 to yesterday.
Saves to: economic_calendar_high_impact.csv

Columns: iso_date (YYYY-MM-DD), time, currency, event
"""

import requests
import csv
import time
import re
from datetime import date, timedelta
from bs4 import BeautifulSoup

# -- Config ---------------------------------------------------------------
DATE_FROM   = date(2023, 8, 1)
DATE_TO     = date.today() - timedelta(days=1)
OUTPUT_FILE = "economic_calendar_high_impact.csv"
SLEEP_SEC   = 2.0        # polite delay between weekly requests
CURRENCIES  = {'USD', 'EUR', 'JPY', 'CAD'}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# FF day-of-week abbreviations -> offset from Monday (Sunday = -1 = previous day)
_DAY_OFFSET = {'Sun': -1, 'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5}

# -- Helpers --------------------------------------------------------------
def week_str(d: date) -> str:
    """Format date as Forex Factory week param e.g. aug6.2023"""
    return d.strftime('%b').lower() + str(d.day) + '.' + d.strftime('%Y')

def monday_on_or_before(d: date) -> date:
    """Return the Monday of the week containing d."""
    return d - timedelta(days=d.weekday())

def iso_from_ff_date(date_str: str, week_monday: date):
    """
    Convert FF date string e.g. 'ThuAug 10' to ISO 'YYYY-MM-DD'
    using the known Monday of the week being fetched.
    """
    m = re.match(r'([A-Za-z]{3})', date_str.strip())
    if not m:
        return None
    offset = _DAY_OFFSET.get(m.group(1))
    if offset is None:
        return None
    return (week_monday + timedelta(days=offset)).strftime('%Y-%m-%d')

# -- Fetch one week -------------------------------------------------------
def fetch_week(week_date: date) -> list:
    """
    Fetch one week from Forex Factory and return list of high-impact events
    for CURRENCIES. Each event has iso_date, time, currency, event fields.
    """
    url = f'https://www.forexfactory.com/calendar?week={week_str(week_date)}'
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, 'html.parser')
    cal = soup.find('table', class_='calendar__table')
    if not cal:
        return []

    events = []
    current_date_str = None

    for row in cal.find_all('tr'):
        classes = row.get('class', [])

        # Day-breaker row carries the date text
        if 'calendar__row--day-breaker' in classes:
            date_td = row.find('td', class_='calendar__cell')
            if date_td:
                current_date_str = date_td.get_text(strip=True)
            continue

        if 'calendar__row' not in ' '.join(classes):
            continue

        # Currency filter
        cur_td = row.find('td', class_='calendar__currency')
        if not cur_td or cur_td.get_text(strip=True) not in CURRENCIES:
            continue

        # High-impact only (red icon)
        impact_td = row.find('td', class_='calendar__impact')
        if not impact_td:
            continue
        impact_span = impact_td.find('span')
        if not impact_span or 'icon--ff-impact-red' not in impact_span.get('class', []):
            continue

        time_td  = row.find('td', class_='calendar__time')
        event_td = row.find('td', class_='calendar__event')

        iso = iso_from_ff_date(current_date_str or '', week_date) or ''

        events.append({
            'iso_date': iso,
            'time':     time_td.get_text(strip=True)  if time_td  else '',
            'currency': cur_td.get_text(strip=True),
            'event':    event_td.get_text(strip=True) if event_td else '',
        })

    return events

# -- Main -----------------------------------------------------------------
def main():
    all_events = []

    # Build list of Mondays to fetch
    start_monday = monday_on_or_before(DATE_FROM)
    end_monday   = monday_on_or_before(DATE_TO)

    mondays = []
    d = start_monday
    while d <= end_monday:
        mondays.append(d)
        d += timedelta(weeks=1)

    total = len(mondays)
    print(f"Fetching high-impact events: {DATE_FROM} to {DATE_TO}")
    print(f"Currencies: {', '.join(sorted(CURRENCIES))}")
    print(f"Weeks to fetch: {total}\n")

    for i, monday in enumerate(mondays, 1):
        try:
            events = fetch_week(monday)
            all_events.extend(events)
            print(f"  [{i:>3}/{total}] week of {monday}  "
                  f"({len(events)} events, {len(all_events)} total)")
        except Exception as e:
            print(f"  [{i:>3}/{total}] week of {monday}  ERROR: {e}")

        time.sleep(SLEEP_SEC)

    # Save CSV
    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['iso_date', 'time', 'currency', 'event'])
        writer.writeheader()
        writer.writerows(all_events)

    print(f"\nDone. {len(all_events)} events saved to {OUTPUT_FILE}")

    from collections import Counter
    counts = Counter(e['currency'] for e in all_events)
    print("\nEvents by currency:")
    for cur in sorted(CURRENCIES):
        print(f"  {cur}: {counts.get(cur, 0)}")

if __name__ == '__main__':
    main()
