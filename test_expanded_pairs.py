"""
test_expanded_pairs.py  (v2 — multiprocessing, 10 workers)
===========================================================
Run improved-rules backtest on all 8 DXY-correlated pairs.

CONFIRMED (32-month data):  EURUSD  USDJPY  USDCAD  XAUUSD
NEW / PARTIAL DATA:         GBPUSD  AUDUSD  NZDUSD  USDCHF

Data strategy
-------------
For each new pair this script looks for a full merged file first:
    FX_GBPUSD, 15_merged.csv   (export from TradingView Aug 2023+)
If not found it falls back to the (1).csv file (Jun 2025 only).
When any pair uses the short window, all confirmed pairs are ALSO
re-run against that same window so comparisons remain fair.

To get the full 32-month CSVs from TradingView
-----------------------------------------------
1. Open TradingView Desktop (or web).
2. Load the pair on a 15-minute chart (e.g. FX:GBPUSD).
3. Scroll back until the chart shows August 2023.
4. Right-click anywhere on the chart -> "Export chart data..."
5. Save the file — rename it to:
       FX_GBPUSD, 15_merged.csv
   (or AUDUSD / NZDUSD / USDCHF as appropriate)
6. Drop the file into the project folder and re-run this script.

Parallelism
-----------
Signal generation (DXY only, sequential) then pair application
is farmed out across up to N_WORKERS processes via multiprocessing.
"""

import sys, os
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
from pathlib import Path
from multiprocessing import Pool, cpu_count
import dxy_improved_rules as imp
import dxy_clean_rules    as r

BASE       = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
N_WORKERS  = min(10, cpu_count())

# ── Pair config ────────────────────────────────────────────────────────────────
CONFIRMED_PAIRS = ['EURUSD', 'USDJPY', 'USDCAD', 'XAUUSD']
NEW_PAIRS       = ['GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCHF']
ALL_PAIRS       = CONFIRMED_PAIRS + NEW_PAIRS

PAIR_FACTOR = {
    'EURUSD': 0.01,  'GBPUSD': 0.01,  'AUDUSD': 0.01,  'NZDUSD': 0.01,
    'USDJPY': 1.0,   'USDCAD': 0.01,  'USDCHF': 0.01,  'XAUUSD': 100.0,
}
PAIR_DIR = {
    'EURUSD': -1,  'GBPUSD': -1,  'AUDUSD': -1,  'NZDUSD': -1,
    'USDJPY': +1,  'USDCAD': +1,  'USDCHF': +1,  'XAUUSD': -1,
}

# File map — merged (full period) preferred, (1).csv fallback for new pairs
FILE_MAP = {
    'EURUSD': BASE / 'FX_EURUSD, 15_merged.csv',
    'USDJPY': BASE / 'FX_USDJPY, 15_merged.csv',
    'USDCAD': BASE / 'FX_USDCAD, 15_merged.csv',
    'XAUUSD': BASE / 'FX_XAUUSD, 15_merged.csv',
    'GBPUSD': (BASE / 'FX_GBPUSD, 15_merged.csv'
               if (BASE / 'FX_GBPUSD, 15_merged.csv').exists()
               else BASE / 'FX_GBPUSD, 15 (1).csv'),
    'AUDUSD': (BASE / 'FX_AUDUSD, 15_merged.csv'
               if (BASE / 'FX_AUDUSD, 15_merged.csv').exists()
               else BASE / 'FX_AUDUSD, 15 (1).csv'),
    'NZDUSD': (BASE / 'FX_NZDUSD, 15_merged.csv'
               if (BASE / 'FX_NZDUSD, 15_merged.csv').exists()
               else BASE / 'FX_NZDUSD, 15 (1).csv'),
    'USDCHF': (BASE / 'FX_USDCHF, 15_merged.csv'
               if (BASE / 'FX_USDCHF, 15_merged.csv').exists()
               else BASE / 'FX_USDCHF, 15 (1).csv'),
}

ACCOUNT      = 100_000
RISK_PCT     = 0.0025
LON_ATTR_MIN = 1000   # pts


# ── Data loading ───────────────────────────────────────────────────────────────
def load_pair(sym):
    df = pd.read_csv(FILE_MAP[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['time', 'open', 'high', 'low', 'close']].copy()


# ── LON_ATTR scanner ───────────────────────────────────────────────────────────
def scan_lon_attr(df_dxy, news_dates):
    c_s = df_dxy['close']; o_s = df_dxy['open']
    h_s = df_dxy['high'];  l_s = df_dxy['low']
    body        = (c_s - o_s).abs()
    body_top    = pd.concat([o_s, c_s], axis=1).max(axis=1)
    body_bottom = pd.concat([o_s, c_s], axis=1).min(axis=1)
    hi_wick = h_s - body_top
    lo_wick = body_bottom - l_s
    rng_s   = (h_s - l_s).replace(0, np.nan)
    PMW     = r.PIN_WICK_MULT

    bull_pin = (lo_wick >= body * PMW) & (lo_wick >= hi_wick * 1.5) & rng_s.notna()
    bear_pin = (hi_wick >= body * PMW) & (hi_wick >= lo_wick * 1.5) & rng_s.notna()
    both     = bull_pin & bear_pin
    bull_pin = bull_pin & ~(both & (c_s <= o_s))
    bear_pin = bear_pin & ~(both & (c_s >= o_s))

    london_open_price = np.nan
    zone_top = zone_bot = np.nan
    lon_pristine_long = lon_pristine_short = True
    lon_attr_traded = False
    ENTRY_END = 18 * 60
    sigs = []

    for i in range(2, len(df_dxy)):
        row = df_dxy.iloc[i]
        cv, ov = row['close'], row['open']
        ts = row['time']
        hh, mm = ts.hour, ts.minute
        curr_min = hh * 60 + mm
        dow = ts.dayofweek
        in_japan = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)

        is_lon = (not in_japan and hh == 7 and mm == 0 and dow != 0)
        is_mon = (not in_japan and hh == 6 and mm == 30 and dow == 0)

        if is_lon or is_mon:
            london_open_price = ov
            zone_top = max(ov, cv)
            zone_bot = min(ov, cv)
            lon_pristine_long = lon_pristine_short = True
            lon_attr_traded = False
            continue
        if np.isnan(london_open_price) or in_japan:
            continue
        if not np.isnan(zone_top):
            if ov >= zone_top or cv >= zone_top:
                lon_pristine_long  = False
            if ov <= zone_bot or cv <= zone_bot:
                lon_pristine_short = False

        lon_start = (6*60+30) if dow == 0 else (7*60)
        if not (lon_start < curr_min <= ENTRY_END) or lon_attr_traded:
            continue
        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        dist = (cv - london_open_price) * 10000

        if dist <= -LON_ATTR_MIN and lon_pristine_long and bull_pin.at[i]:
            tp = zone_bot
            if tp > cv:
                sl_d = tp - cv
                sl   = cv - sl_d
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'long')
                sigs.append({'type': 'LON_ATTR_LONG', 'entry_time': str(ts),
                    'entry': round(cv,5), 'tp': round(tp,5), 'sl': round(sl,5),
                    'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                    'london_open': round(london_open_price,5), 'pristine': True,
                    'outcome': out, 'exit_px': round(exit_px,5),
                    'exit_time': str(df_dxy.at[exit_bar,'time']),
                    'bias_1h': 0, 'bias_4h': 0})
                lon_attr_traded = True

        elif dist >= LON_ATTR_MIN and lon_pristine_short and bear_pin.at[i]:
            tp = zone_bot
            if tp < cv:
                sl_d = cv - tp
                sl   = cv + sl_d
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'short')
                sigs.append({'type': 'LON_ATTR_SHORT', 'entry_time': str(ts),
                    'entry': round(cv,5), 'tp': round(tp,5), 'sl': round(sl,5),
                    'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                    'london_open': round(london_open_price,5), 'pristine': True,
                    'outcome': out, 'exit_px': round(exit_px,5),
                    'exit_time': str(df_dxy.at[exit_bar,'time']),
                    'bias_1h': 0, 'bias_4h': 0})
                lon_attr_traded = True
    return sigs


# ── Pair application (runs in worker process) ──────────────────────────────────
def _apply_pair_worker(args):
    """Top-level function (picklable) — called by Pool."""
    pair, signals, start_ts_str, news_dates = args
    df_pair = load_pair(pair)
    F = PAIR_FACTOR[pair]
    D = PAIR_DIR[pair]
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}
    start_ts = pd.Timestamp(start_ts_str, tz='UTC') if start_ts_str else None
    results = []
    for sig in signals:
        et = sig['entry_time']
        xt = sig.get('exit_time')
        if start_ts and pd.Timestamp(et[:19], tz='UTC') < start_ts:
            continue
        if et not in pair_idx or not xt or xt not in pair_idx:
            continue
        if news_dates and r.news_blocks_pair(news_dates, et, pair):
            continue
        pi, xi = pair_idx[et], pair_idx[xt]
        pc = df_pair.at[pi, 'close']
        px = df_pair.at[xi, 'close']
        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        pair_sl_d   = sig['sl_pts'] / 10000 * F
        raw_pnl     = (px - pc) if pair_long else (pc - px)
        r_actual    = raw_pnl / pair_sl_d if pair_sl_d > 0 else 0.0
        outcome     = 'win' if r_actual > 0 else ('loss' if r_actual < 0 else 'even')
        results.append({
            'dxy_type':    sig['type'],
            'entry_time':  et,
            'exit_time':   xt,
            'dxy_outcome': sig['outcome'],
            'pair':        pair,
            'direction':   'long' if pair_long else 'short',
            'entry':       round(pc, 5),
            'exit_px':     round(px, 5),
            'sl_pts_dxy':  sig['sl_pts'],
            'outcome':     outcome,
            'r_actual':    round(r_actual, 3),
        })
    return pair, results


# ── Stats helpers ──────────────────────────────────────────────────────────────
def pair_stats(trades):
    if not trades:
        return 0, 0, 0, float('nan'), float('inf'), 0.0, 0.0, 0.0
    df = pd.DataFrame(trades)
    wins = df[df['r_actual'] > 0];  loss = df[df['r_actual'] < 0]
    w, l = len(wins), len(loss)
    wr   = w / (w + l) * 100 if (w + l) > 0 else float('nan')
    gw, gl = wins['r_actual'].sum(), loss['r_actual'].abs().sum()
    pf   = gw / gl if gl > 0 else float('inf')
    return len(df), w, l, wr, pf, df['r_actual'].sum(), \
           gw/w if w else 0.0, gl/l if l else 0.0

def fmt_pf(v): return f"{v:.2f}" if v != float('inf') else "  inf"
def fmt_wr(v): return f"{v:.1f}%" if not (isinstance(v, float) and np.isnan(v)) else "  n/a"


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':

    print(f"Workers available: {cpu_count()}  |  Using: {N_WORKERS}")

    # ── 1. Load DXY + generate signals (sequential — single data source) ────────
    print("Loading DXY data and generating signals...")
    df_dxy     = imp.load_merged('DXY').reset_index(drop=True)
    news_dates = r.load_news_filter()

    all_sigs = imp.generate_signals_v2(df_dxy, near_edge_tp=True, news_dates=news_dates)
    all_sigs += scan_lon_attr(df_dxy, news_dates)

    def ct(prefix): return sum(1 for s in all_sigs if s['type'].startswith(prefix))
    print(f"Signals — GAP_REJ:{ct('GAP_REJ')}  REV:{ct('REV')}  LON_ATTR:{ct('LON_ATTR')}  TOTAL:{len(all_sigs)}")

    # ── 2. Determine windows ───────────────────────────────────────────────────
    # Check which new pairs have full merged data available
    pair_has_full = {}
    for p in ALL_PAIRS:
        df_tmp = load_pair(p)
        start  = df_tmp['time'].min()
        full   = start < pd.Timestamp('2024-01-01', tz='UTC')
        pair_has_full[p] = full

    # Short window = latest start among pairs without full data
    short_pairs = [p for p in NEW_PAIRS if not pair_has_full[p]]
    if short_pairs:
        short_starts = []
        for p in short_pairs:
            df_tmp = load_pair(p)
            short_starts.append(df_tmp['time'].min())
        NEW_START_TS     = max(short_starts)
        NEW_START_STR    = str(NEW_START_TS)
        months_short     = (df_dxy['time'].max() - NEW_START_TS).days / 30.44
        using_short_win  = True
    else:
        NEW_START_STR    = None
        months_short     = None
        using_short_win  = False

    months_full = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44

    print(f"\nFull window  : {df_dxy['time'].min().date()} to {df_dxy['time'].max().date()} ({months_full:.1f} months)")
    if using_short_win:
        print(f"Short window : {NEW_START_TS.date()} to {df_dxy['time'].max().date()} ({months_short:.1f} months)")
        print(f"Pairs needing full export: {', '.join(short_pairs)}")
    else:
        print("All 8 pairs have full 32-month data — running full comparison.")
    print()

    # Filter signals to short window for the limited-data run
    short_sigs = [s for s in all_sigs
                  if not using_short_win or
                  pd.Timestamp(s['entry_time'][:19], tz='UTC') >= NEW_START_TS] \
                  if using_short_win else all_sigs

    # ── 3. Build worker job list ───────────────────────────────────────────────
    # Each job: (pair, signals_list, start_ts_str_or_None, news_dates)
    # Full-data confirmed pairs run against all_sigs (no start filter).
    # Short-window pairs (and confirmed for comparison) run against short_sigs.

    jobs_full  = [(p, all_sigs,   None,          news_dates) for p in CONFIRMED_PAIRS]
    if using_short_win:
        jobs_short = [(p, short_sigs, NEW_START_STR, news_dates) for p in ALL_PAIRS]
    else:
        jobs_short = [(p, all_sigs,   None,          news_dates) for p in ALL_PAIRS]

    # ── 4. Run in parallel ─────────────────────────────────────────────────────
    print(f"Spawning {N_WORKERS} worker processes...")
    all_jobs = jobs_full + jobs_short   # confirmed pairs appear twice (once each window)

    with Pool(processes=N_WORKERS) as pool:
        raw_results = pool.map(_apply_pair_worker, all_jobs)

    # Separate full vs short results
    n_full = len(jobs_full)
    full_results  = {pair: trades for pair, trades in raw_results[:n_full]}
    short_results = {pair: trades for pair, trades in raw_results[n_full:]}

    print("Done.\n")

    # ── 5. Print results ───────────────────────────────────────────────────────
    HDR = f"  {'Pair':<10}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR%':>7}  {'PF':>6}  {'NetR':>8}  {'AvgW':>7}  {'AvgL':>7}"
    SEP = f"  {'-'*72}"

    # ── Section 1: confirmed pairs, full 32m ───────────────────────────────────
    print("=" * 80)
    print(f"  SECTION 1 -- CONFIRMED PAIRS  (full {months_full:.1f}-month period)")
    print("=" * 80)
    print(HDR); print(SEP)
    all_full_trades = []
    for p in CONFIRMED_PAIRS:
        t = full_results[p]
        all_full_trades.extend(t)
        n,w,l,wr,pf,net,aw,al = pair_stats(t)
        print(f"  {p:<10}  {n:>4}  {w:>4}  {l:>4}  {fmt_wr(wr):>7}  {fmt_pf(pf):>6}  {net:>+8.1f}R  {aw:>+6.2f}R  {al:>-6.2f}R")
    n,w,l,wr,pf,net,aw,al = pair_stats(all_full_trades)
    print(SEP)
    print(f"  {'PORTFOLIO':<10}  {n:>4}  {w:>4}  {l:>4}  {fmt_wr(wr):>7}  {fmt_pf(pf):>6}  {net:>+8.1f}R  {aw:>+6.2f}R  {al:>-6.2f}R")
    print()

    # ── Section 2: all 8 pairs, common window ─────────────────────────────────
    win_label = (f"{NEW_START_TS.date()} to {df_dxy['time'].max().date()} ({months_short:.1f} months)"
                 if using_short_win else f"full {months_full:.1f}-month period")
    print("=" * 80)
    print(f"  SECTION 2 -- ALL 8 PAIRS  ({win_label})")
    if using_short_win:
        print(f"  Confirmed pairs re-run on same window for fair comparison")
    print("=" * 80)
    w_months = months_short if using_short_win else months_full
    print(f"  {'Pair':<10}  {'Data':<8}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR%':>7}  {'PF':>6}  {'NetR':>8}  {'R/mo':>8}")
    print(f"  {'-'*76}")
    rows = []
    all_short_trades = []
    for p in ALL_PAIRS:
        t = short_results[p]
        all_short_trades.extend(t)
        n,w,l,wr,pf,net,aw,al = pair_stats(t)
        data_tag = 'FULL' if pair_has_full[p] else 'PARTIAL'
        rpm = net / w_months if w_months else 0
        rows.append((p, data_tag, n, w, l, wr, pf, net, rpm))
        print(f"  {p:<10}  {data_tag:<8}  {n:>4}  {w:>4}  {l:>4}  {fmt_wr(wr):>7}  "
              f"{fmt_pf(pf):>6}  {net:>+8.1f}R  {rpm:>+7.2f}R/mo")
    n,w,l,wr,pf,net,aw,al = pair_stats(all_short_trades)
    print(f"  {'-'*76}")
    print(f"  {'ALL 8':<10}  {'':8}  {n:>4}  {w:>4}  {l:>4}  {fmt_wr(wr):>7}  {fmt_pf(pf):>6}  {net:>+8.1f}R")
    print()

    # ── Section 3: signal type breakdown, new pairs only ──────────────────────
    if using_short_win:
        print("=" * 80)
        print(f"  SECTION 3 -- SIGNAL TYPE BREAKDOWN (new pairs, short window)")
        print("=" * 80)
        for prefix in ['GAP_REJ', 'REV', 'LON_ATTR']:
            filtered = {p: [t for t in short_results[p]
                            if t['dxy_type'].startswith(prefix)] for p in NEW_PAIRS}
            if not any(filtered.values()):
                continue
            print(f"\n  -- {prefix} --")
            print(f"  {'Pair':<10}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR%':>7}  {'PF':>6}  {'NetR':>8}")
            print(f"  {'-'*54}")
            for p in NEW_PAIRS:
                t = filtered[p]
                if not t: continue
                n,w,l,wr,pf,net,aw,al = pair_stats(t)
                print(f"  {p:<10}  {n:>4}  {w:>4}  {l:>4}  {fmt_wr(wr):>7}  {fmt_pf(pf):>6}  {net:>+8.1f}R")
        print()

    # ── Section 4: ranking ─────────────────────────────────────────────────────
    print("=" * 80)
    print(f"  SECTION 4 -- RANKING by Net R/month ({win_label})")
    print("=" * 80)
    sorted_rows = sorted(rows, key=lambda x: x[8], reverse=True)
    print(f"  {'Rank':<5}  {'Pair':<10}  {'Data':<8}  {'N':>4}  {'WR%':>7}  {'PF':>6}  {'NetR':>8}  {'R/mo':>8}  Verdict")
    print(f"  {'-'*82}")
    for rank, (p, data_tag, n, w, l, wr, pf, net, rpm) in enumerate(sorted_rows, 1):
        if   n < 5:                         verdict = "INSUFFICIENT DATA"
        elif wr >= 55 and net > 0:          verdict = "STRONG  -- ADD"
        elif wr >= 45 and net > 0:          verdict = "POSITIVE -- CONSIDER"
        elif net > 0:                       verdict = "MARGINAL -- MONITOR"
        else:                               verdict = "NEGATIVE -- SKIP"
        flag = " *" if data_tag == 'PARTIAL' else "  "
        print(f"  {rank:<5}  {p:<10}  {data_tag:<8}  {n:>4}  {fmt_wr(wr):>7}  "
              f"{fmt_pf(pf):>6}  {net:>+8.1f}R  {rpm:>+7.2f}R/mo  {verdict}{flag}")
    if using_short_win:
        print("  * PARTIAL = only Jun 2025 onwards — export full history to confirm")
    print()

    # ── Section 5: dollar estimate ─────────────────────────────────────────────
    rpt = ACCOUNT * RISK_PCT
    print("=" * 80)
    print(f"  SECTION 5 -- DOLLAR ESTIMATE  (0.25% risk | ${rpt:,.0f}/trade | {w_months:.1f} months)")
    print("=" * 80)
    print(f"  {'Pair':<10}  {'N':>4}  {'WR%':>7}  {'NetR':>8}  {'$P&L':>12}  {'$/month':>10}")
    print(f"  {'-'*62}")
    total_net = sum(row[7] for row in sorted_rows)
    for p, dt, n, w, l, wr, pf, net, rpm in sorted_rows:
        dollar = net * rpt
        dpm    = rpm * rpt
        print(f"  {p:<10}  {n:>4}  {fmt_wr(wr):>7}  {net:>+8.1f}R  ${dollar:>+10,.0f}  ${dpm:>+8,.0f}/mo")
    print(f"  {'-'*62}")
    print(f"  {'ALL 8':<10}  {'':>4}  {'':>7}  {total_net:>+8.1f}R  ${total_net*rpt:>+10,.0f}")
    print()

    if using_short_win:
        print("=" * 80)
        print("  ACTION REQUIRED: Export full 15m history (Aug 2023+) from TradingView for:")
        for p in short_pairs:
            print(f"    {p}  ->  save as: FX_{p}, 15_merged.csv")
        print("  Steps: TradingView chart -> right-click -> Export chart data")
        print("  Then re-run this script for full 32-month validation.")
        print("=" * 80)
