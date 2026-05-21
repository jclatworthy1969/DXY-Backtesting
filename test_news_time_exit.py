"""
test_news_time_exit.py
Compare baseline DXY-exit results vs exiting 60 minutes before tier-1 news events.
"""
import pandas as pd
import re
import numpy as np
import dxy_clean_rules as r
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

BASE  = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
PAIRS = r.PAIRS

MONTH_MAP = {
    'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
    'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12
}

TIER1_KW = [
    'ppi',
    'federal funds rate', 'main refinancing rate', 'boj policy rate', 'overnight rate',
    'fomc statement', 'fomc economic projections', 'fomc press conference', 'fomc meeting minutes',
    'ecb press conference', 'monetary policy statement',
    'boj press conference', 'boj outlook report',
    'boc rate statement', 'boc press conference', 'boc monetary policy report',
    'fed chairman powell speaks', 'fed chairman powell testifies',
    'non-farm employment change', 'unemployment rate', 'employment change',
    'adp non-farm', 'unemployment claims',
]

# Known ET release times for events FF sometimes omits time for
DEFAULT_ET = {
    'non-farm employment change':       (8, 30),
    'unemployment rate':                (8, 30),
    'unemployment claims':              (8, 30),
    'core ppi m/m':                     (8, 30),
    'ppi m/m':                          (8, 30),
    'adp non-farm employment change':   (8, 15),
    'adp weekly employment change':     (8, 15),
    'employment change':                (8, 30),  # CAD
}

# Currencies relevant to each pair
RELEVANT_CURS = {
    'EURUSD': ['USD', 'EUR'],
    'USDJPY': ['USD', 'JPY'],
    'USDCAD': ['USD', 'CAD'],
    'XAUUSD': ['USD'],
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_us_dst(iso_date: str) -> bool:
    dt = datetime.strptime(iso_date, '%Y-%m-%d')
    mar = datetime(dt.year, 3, 1)
    dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7) + timedelta(weeks=1)
    nov = datetime(dt.year, 11, 1)
    dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return dst_start.date() <= dt.date() < dst_end.date()


def et_hm_to_utc_mins(h: int, mn: int, iso: str) -> int:
    offset = 4 if is_us_dst(iso) else 5  # hours to add ET -> UTC
    return ((h + offset) * 60 + mn) % 1440


def parse_time_to_utc_mins(t_str) -> int | None:
    """Parse FF time string (already UTC) -> minutes since midnight."""
    if t_str is None or (isinstance(t_str, float) and np.isnan(t_str)):
        return None
    m = re.match(r'(\d+):(\d+)\s*(am|pm)', str(t_str).strip().lower())
    if not m:
        return None
    h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap == 'pm' and h != 12:
        h += 12
    elif ap == 'am' and h == 12:
        h = 0
    return h * 60 + mn  # already UTC (confirmed by ISM/NFP cross-check)


# ── Build news-times dict ─────────────────────────────────────────────────────

def build_news_times() -> dict:
    """
    Returns:
        news_times[iso_date][currency] = sorted list of UTC minutes-of-day
    """
    df = pd.read_csv(BASE / 'economic_calendar_high_impact.csv')
    df['tier1'] = df['event'].apply(
        lambda e: any(k in str(e).lower() for k in TIER1_KW)
    )
    df = df[df['tier1']].copy()

    # Infer ISO dates from legacy 'ThuAug 10' format
    year, prev_month = 2023, None
    iso_list = []
    for _, row in df.iterrows():
        ds = str(row['date']).strip()
        m  = re.match(r'[A-Za-z]{3}([A-Za-z]{3})\s*(\d+)', ds)
        if not m:
            iso_list.append(None)
            continue
        month = MONTH_MAP.get(m.group(1))
        if not month:
            iso_list.append(None)
            continue
        day = int(m.group(2))
        if prev_month and month < prev_month and prev_month >= 11:
            year += 1
        prev_month = month
        iso_list.append(datetime(year, month, day).strftime('%Y-%m-%d'))
    df['iso_date'] = iso_list

    # Assign UTC minutes
    utc_mins_list = []
    for _, row in df.iterrows():
        iso   = row['iso_date']
        event = str(row['event']).lower()
        utc_m = parse_time_to_utc_mins(row['time'])
        if utc_m is None and iso:
            for kw, (eh, em) in DEFAULT_ET.items():
                if kw in event:
                    utc_m = et_hm_to_utc_mins(eh, em, iso)
                    break
        utc_mins_list.append(utc_m)
    df['utc_mins'] = utc_mins_list

    total     = len(df)
    with_time = df['utc_mins'].notna().sum()
    print(f"  Tier-1 events with time (incl. defaults): {with_time}/{total} ({with_time/total*100:.0f}%)")

    nt = defaultdict(lambda: defaultdict(list))
    for _, row in df.iterrows():
        if row['iso_date'] and row['utc_mins'] is not None and not (isinstance(row['utc_mins'], float) and np.isnan(row['utc_mins'])):
            nt[row['iso_date']][row['currency']].append(int(row['utc_mins']))
    for d in nt:
        for c in nt[d]:
            nt[d][c].sort()
    return dict(nt)


# ── DXY-exit with news early-exit ─────────────────────────────────────────────

def apply_dxy_exit_news_exit(dxy_signals, df_pair, pair, news_times,
                              exit_mins_before: int = 60):
    """
    Like apply_to_pair_dxy_exit but exits exit_mins_before minutes before
    the first tier-1 news event relevant to this pair within the trade window.
    """
    F        = r.PAIR_FACTOR[pair]
    D        = r.PAIR_DIR[pair]
    relevant = RELEVANT_CURS.get(pair, ['USD'])
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}

    results = []
    for sig in dxy_signals:
        et = sig['entry_time']
        xt = sig.get('exit_time')
        if et not in pair_idx or not xt or xt not in pair_idx:
            continue

        pi = pair_idx[et]
        xi = pair_idx[xt]
        pc = df_pair.at[pi, 'close']

        is_long_dxy  = 'LONG' in sig['type']
        pair_long    = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        pair_sl_dist = sig['sl_pts'] / 10000 * F

        # Find earliest tier-1 news event for this pair within (entry, dxy_exit]
        earliest_news_dt = None
        for bar_idx in range(pi + 1, xi + 1):
            t_bar = df_pair.at[bar_idx, 'time']
            iso   = t_bar.strftime('%Y-%m-%d')
            bm    = t_bar.hour * 60 + t_bar.minute
            for cur in relevant:
                for nm in news_times.get(iso, {}).get(cur, []):
                    if nm > bm:
                        ndt = t_bar.replace(
                            hour=nm // 60, minute=nm % 60,
                            second=0, microsecond=0
                        )
                        if earliest_news_dt is None or ndt < earliest_news_dt:
                            earliest_news_dt = ndt

        # Choose actual exit bar
        actual_xi = xi
        news_exit = False
        if earliest_news_dt is not None:
            target_t = earliest_news_dt - timedelta(minutes=exit_mins_before)
            for bar_idx in range(xi, pi, -1):
                if df_pair.at[bar_idx, 'time'] <= target_t:
                    actual_xi = bar_idx
                    news_exit = (actual_xi < xi)
                    break

        px       = df_pair.at[actual_xi, 'close']
        raw_pnl  = (px - pc) if pair_long else (pc - px)
        r_actual = raw_pnl / pair_sl_dist if pair_sl_dist > 0 else 0.0
        outcome  = 'win' if r_actual > 0 else ('loss' if r_actual < 0 else 'even')

        results.append({
            'dxy_type'   : sig['type'],
            'entry_time' : et,
            'exit_time'  : str(df_pair.at[actual_xi, 'time']),
            'dxy_outcome': sig['outcome'],
            'pair'       : pair,
            'direction'  : 'long' if pair_long else 'short',
            'entry'      : round(pc, 5),
            'exit_px'    : round(px, 5),
            'sl_pts_dxy' : sig['sl_pts'],
            'outcome'    : outcome,
            'r_actual'   : round(r_actual, 3),
            'news_exit'  : news_exit,
            'bias_1h'    : sig['bias_1h'],
            'bias_4h'    : sig['bias_4h'],
        })
    return results


# ── Data loader ───────────────────────────────────────────────────────────────

def load_merged(sym: str) -> pd.DataFrame:
    path_map = {
        'DXY':    BASE / 'TVC_DXY, 15_merged.csv',
        'EURUSD': BASE / 'FX_EURUSD, 15_merged.csv',
        'USDJPY': BASE / 'FX_USDJPY, 15_merged.csv',
        'USDCAD': BASE / 'FX_USDCAD, 15_merged.csv',
        'XAUUSD': BASE / 'FX_XAUUSD, 15_merged.csv',
    }
    df = pd.read_csv(path_map[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['time', 'open', 'high', 'low', 'close']].copy()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Building tier-1 news-times dictionary...")
    news_times = build_news_times()

    print("Loading 33-month merged data...")
    df_dxy   = load_merged('DXY')
    pair_dfs = {p: load_merged(p) for p in PAIRS}
    print(f"  DXY bars: {len(df_dxy)}")

    print("Generating DXY signals (no day-filter)...")
    sigs, _ = r.generate_dxy_signals(df_dxy, near_edge_tp=True)
    print(f"  Signals: {len(sigs)}")

    # Baseline
    print("Running BASELINE (DXY-exit, no news filter)...")
    pt_base = []
    for pair in PAIRS:
        pt_base.extend(r.apply_to_pair_dxy_exit(sigs, pair_dfs[pair], pair))

    # Time-based exit variants
    results = {}
    for mins in [30, 60, 90]:
        label = f"Exit {mins}min before"
        print(f"Running {label}...")
        pt = []
        ne_count = 0
        for pair in PAIRS:
            trades = apply_dxy_exit_news_exit(
                sigs, pair_dfs[pair], pair, news_times,
                exit_mins_before=mins
            )
            pt.extend(trades)
            ne_count += sum(1 for t in trades if t['news_exit'])
        results[label] = (pt, ne_count)

    # ── Print comparison ──────────────────────────────────────────────────────
    print()
    print("=" * 76)
    print("  PORTFOLIO COMPARISON  (DXY-Exit, near-edge TP, 33-month merged)")
    print("=" * 76)

    def row(label, pt, ne=None):
        s   = r.stats_r(pt, label)
        pf  = f"{s['PF']:.3f}" if s.get('PF', 0) != float('inf') else 'inf'
        ne_str = f"  ({ne} news exits)" if ne is not None else ""
        print(f"  {label:<24}: N={s['N']:3d}  WR={s.get('WR%',0):5.1f}%  "
              f"PF={pf}  NetR={s.get('NetR',0):>+7.1f}R{ne_str}")

    row("Baseline (no filter)", pt_base)
    for label, (pt, ne) in results.items():
        row(label, pt, ne)

    print()
    print("  Per-pair breakdown:")
    headers = ["Baseline"] + list(results.keys())
    print(f"  {'':10}  " + "  ".join(f"{h[:14]:>15}" for h in headers))
    print(f"  {'-'*75}")
    all_pts = [pt_base] + [v[0] for v in results.values()]
    for pair in PAIRS:
        vals = []
        for pt in all_pts:
            s = r.stats_r([t for t in pt if t['pair'] == pair], pair)
            vals.append(f"{s.get('NetR',0):>+7.1f}R (N={s.get('N',0):3d})")
        print(f"  {pair:<10}  " + "  ".join(f"{v:>15}" for v in vals))

    # ── Show what news-exits looked like ─────────────────────────────────────
    pt_60 = results["Exit 60min before"][0]
    news_exits = [t for t in pt_60 if t['news_exit']]
    if news_exits:
        print()
        print(f"  Sample trades that triggered news-exit (60min variant):")
        from collections import Counter
        outcomes = Counter(t['outcome'] for t in news_exits)
        r_vals   = [t['r_actual'] for t in news_exits]
        net      = sum(r_vals)
        wr       = outcomes['win'] / (outcomes['win'] + outcomes['loss']) * 100 if (outcomes['win'] + outcomes['loss']) > 0 else 0
        print(f"  {len(news_exits)} news exits: W={outcomes['win']} L={outcomes['loss']}"
              f"  WR={wr:.0f}%  Net={net:+.1f}R")
        print(f"  (trades that ran to DXY exit but were cut early by news)")


if __name__ == '__main__':
    main()
