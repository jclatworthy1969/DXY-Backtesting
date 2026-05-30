"""
fetch_pair_history.py
=====================
Downloads 15-minute OHLCV data for the 4 untested pairs going back to
August 2023 using the Twelve Data free API.

FREE API KEY
------------
1. Go to https://twelvedata.com  (takes 60 seconds, no credit card)
2. Click "Get your free API key"
3. Paste the key into the API_KEY variable below (or set env var TD_API_KEY)

FREE TIER LIMITS
----------------
  800 API credits / day
  Up to 5,000 bars per request  (~52 days of 15m data)
  This script needs ~96,000 bars per pair = 20 requests per pair
  4 pairs = ~80 requests total — well within the daily limit.

  Between requests the script sleeps 13 seconds (free tier: 8 req/min).
  Total runtime ~20 minutes for all 4 pairs.

OUTPUT
------
  FX_GBPUSD, 15_merged.csv
  FX_AUDUSD, 15_merged.csv
  FX_NZDUSD, 15_merged.csv
  FX_USDCHF, 15_merged.csv

  Each file has columns: time, open, high, low, close
  Format matches the existing merged CSV files exactly.
  Once present, test_expanded_pairs.py will automatically use them.
"""

import os, sys, time, requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from multiprocessing import Pool

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_KEY   = os.environ.get('TD_API_KEY', '52791afa325b42bf9edcb79e942190d8')   # <-- paste key here
BASE      = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

# Pairs to fetch: (twelve_data_symbol, output_filename)
PAIRS = [
    ('GBP/USD', 'FX_GBPUSD, 15_merged.csv'),
    ('AUD/USD', 'FX_AUDUSD, 15_merged.csv'),
    ('NZD/USD', 'FX_NZDUSD, 15_merged.csv'),
    ('USD/CHF', 'FX_USDCHF, 15_merged.csv'),
]

START_DATE  = '2023-08-01'          # match DXY merged file start
END_DATE    = datetime.now(timezone.utc).strftime('%Y-%m-%d')
INTERVAL    = '15min'
BARS_PER_REQ = 4500                 # stay safely under 5000 limit
SLEEP_SEC   = 13                    # free tier: max 8 req/min -> 7.5s min, use 13 for safety


def td_fetch_chunk(symbol, start_dt, end_dt, api_key):
    """Fetch one chunk from Twelve Data. Returns list of bar dicts or raises."""
    url = 'https://api.twelvedata.com/time_series'
    params = {
        'symbol':     symbol,
        'interval':   INTERVAL,
        'start_date': start_dt,
        'end_date':   end_dt,
        'outputsize': BARS_PER_REQ,
        'format':     'JSON',
        'timezone':   'UTC',
        'apikey':     api_key,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get('status') == 'error':
        raise RuntimeError(f"API error: {data.get('message','unknown')}")
    values = data.get('values', [])
    return values   # list of {'datetime','open','high','low','close',...}


def fetch_pair(symbol, out_filename, api_key, start_date, end_date):
    """
    Fetch full history for one symbol by paginating backwards in time.
    Twelve Data returns data in reverse-chronological order (newest first)
    when start+end are specified, so we paginate by moving end_date backwards.
    Returns a DataFrame with columns: time, open, high, low, close
    """
    out_path = BASE / out_filename
    print(f"\n  {symbol}  ->  {out_filename}")

    # Check if file already exists and is complete
    if out_path.exists():
        existing = pd.read_csv(out_path)
        existing['time'] = pd.to_datetime(existing['time'], utc=True)
        if existing['time'].min() <= pd.Timestamp(start_date, tz='UTC'):
            print(f"    Already complete ({len(existing):,} bars). Skipping.")
            return existing

    all_bars = []
    current_end = end_date
    chunk_num   = 0
    start_ts    = pd.Timestamp(start_date, tz='UTC')

    while True:
        chunk_num += 1
        print(f"    Chunk {chunk_num}: fetching up to {current_end}...", end=' ', flush=True)

        try:
            values = td_fetch_chunk(symbol, start_date, current_end, api_key)
        except Exception as e:
            print(f"ERROR: {e}")
            break

        if not values:
            print("empty — done.")
            break

        chunk_bars = []
        for v in values:
            try:
                ts = pd.Timestamp(v['datetime'], tz='UTC')
                chunk_bars.append({
                    'time':  ts,
                    'open':  float(v['open']),
                    'high':  float(v['high']),
                    'low':   float(v['low']),
                    'close': float(v['close']),
                })
            except (KeyError, ValueError):
                continue

        if not chunk_bars:
            print("no parseable bars — done.")
            break

        df_chunk = pd.DataFrame(chunk_bars).sort_values('time')
        all_bars.append(df_chunk)

        oldest_in_chunk = df_chunk['time'].min()
        newest_in_chunk = df_chunk['time'].max()
        print(f"{len(df_chunk):,} bars  ({oldest_in_chunk.date()} to {newest_in_chunk.date()})")

        # Stop if we've reached or passed the start date
        if oldest_in_chunk <= start_ts:
            break

        # Move end date back to just before the oldest bar we received
        current_end = (oldest_in_chunk - pd.Timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')

        # Rate limit — free tier allows ~8 requests/minute
        time.sleep(SLEEP_SEC)

    if not all_bars:
        print(f"    WARNING: No data retrieved for {symbol}")
        return None

    df = pd.concat(all_bars, ignore_index=True)
    df = df.drop_duplicates(subset=['time']).sort_values('time').reset_index(drop=True)

    # Filter to requested window
    df = df[(df['time'] >= start_ts) & (df['time'] <= pd.Timestamp(end_date, tz='UTC'))]

    # Save
    df_out = df.copy()
    df_out['time'] = df_out['time'].dt.strftime('%Y-%m-%d %H:%M:%S+00:00')
    df_out.to_csv(out_path, index=False)
    print(f"    Saved {len(df):,} bars to {out_path.name}")
    return df


def check_api_key(api_key):
    """Quick validation — fetch 1 bar to confirm key is valid."""
    url = 'https://api.twelvedata.com/time_series'
    params = {
        'symbol': 'GBP/USD', 'interval': '15min',
        'outputsize': 1, 'apikey': api_key,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get('status') == 'error':
            return False, data.get('message', 'Unknown error')
        return True, 'OK'
    except Exception as e:
        return False, str(e)


if __name__ == '__main__':

    # ── API key check ──────────────────────────────────────────────────────────
    if API_KEY == 'YOUR_API_KEY_HERE':
        print("=" * 65)
        print("  API KEY NOT SET")
        print()
        print("  1. Go to https://twelvedata.com")
        print("  2. Click 'Get your free API key' (60 seconds, no card)")
        print("  3. Open this script and set:")
        print("       API_KEY = 'your_key_here'")
        print("     OR set the environment variable:")
        print("       $env:TD_API_KEY = 'your_key_here'")
        print("  4. Re-run:  python fetch_pair_history.py")
        print("=" * 65)
        sys.exit(0)

    print("Checking API key...")
    ok, msg = check_api_key(API_KEY)
    if not ok:
        print(f"  API key error: {msg}")
        print("  Check the key at https://app.twelvedata.com/keys")
        sys.exit(1)
    print(f"  API key valid.")

    print(f"\nFetching 15m data from {START_DATE} to {END_DATE}")
    print(f"Sleep between requests: {SLEEP_SEC}s (free tier rate limit)")
    print(f"Estimated time: ~{len(PAIRS) * 20 * SLEEP_SEC // 60} minutes total\n")

    # Fetch pairs sequentially (API rate limit — can't parallelize free tier)
    results = {}
    for symbol, filename in PAIRS:
        df = fetch_pair(symbol, filename, API_KEY, START_DATE, END_DATE)
        results[symbol] = df

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  DOWNLOAD SUMMARY")
    print("=" * 60)
    all_ok = True
    for symbol, filename in PAIRS:
        out_path = BASE / filename
        if out_path.exists():
            df = pd.read_csv(out_path)
            df['time'] = pd.to_datetime(df['time'])
            print(f"  {symbol:<10}  {len(df):>7,} bars  "
                  f"{df['time'].min().date()} to {df['time'].max().date()}  OK")
        else:
            print(f"  {symbol:<10}  MISSING - fetch failed")
            all_ok = False

    print()
    if all_ok:
        print("  All files ready. Run:  python test_expanded_pairs.py")
    else:
        print("  Some files missing — check errors above and re-run.")
    print("=" * 60)
