"""
run_no_attr.py
==============
Revised backtest with ATTR removed.
Only GAP_REJ and REV signals are traded.
Uses merged 33-month data + DXY-exit pair application.
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules    as r

# ── Load data ──────────────────────────────────────────────────────────────────
print("Loading merged data...")
df_dxy   = imp.load_merged('DXY')
pair_dfs = {p: imp.load_merged(p) for p in r.PAIRS}
date_range = f"{df_dxy['time'].min().date()} to {df_dxy['time'].max().date()}"
months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"  DXY: {len(df_dxy):,} bars  ({date_range},  {months:.1f} months)")

news_dates = r.load_news_filter()
if news_dates:
    n_usd = sum(1 for s in news_dates.values() if 'USD' in s)
    print(f"  News filter: {len(news_dates)} high-impact days  (USD blocked: {n_usd})")
else:
    print("  News filter: not found")

# ── Generate all signals ───────────────────────────────────────────────────────
print("\nGenerating signals (near_edge_tp=True)...")
all_sigs = imp.generate_signals_v2(df_dxy, near_edge_tp=True, news_dates=news_dates)

attr_sigs   = [s for s in all_sigs if s['type'].startswith('ATTR')]
gap_rej_sigs = [s for s in all_sigs if s['type'].startswith('GAP_REJ')]
rev_sigs    = [s for s in all_sigs if s['type'].startswith('REV')]

print(f"  All signals  : {len(all_sigs)}")
print(f"  ATTR removed : {len(attr_sigs)}")
print(f"  GAP_REJ kept : {len(gap_rej_sigs)}")
print(f"  REV kept     : {len(rev_sigs)}")

# ── Filter: GAP_REJ + REV only ─────────────────────────────────────────────────
sigs = gap_rej_sigs + rev_sigs
sigs.sort(key=lambda s: s['entry_time'])

# ── DXY signal quality ─────────────────────────────────────────────────────────
print()
print("=" * 72)
print("  DXY SIGNAL QUALITY  (GAP_REJ + REV only — ATTR excluded)")
print("=" * 72)
for label, subset in [
    ("GAP_REJ ALL",   gap_rej_sigs),
    ("GAP_REJ LONG",  [s for s in gap_rej_sigs if s['type'].endswith('LONG')]),
    ("GAP_REJ SHORT", [s for s in gap_rej_sigs if s['type'].endswith('SHORT')]),
    ("REV ALL",       rev_sigs),
    ("REV LONG",      [s for s in rev_sigs if s['type'].endswith('LONG')]),
    ("REV SHORT",     [s for s in rev_sigs if s['type'].endswith('SHORT')]),
    ("COMBINED",      sigs),
]:
    s = r.stats(subset, label)
    r.print_stats(s)

# ── Apply to pairs (DXY-exit, fractional R) ────────────────────────────────────
print()
print("=" * 72)
print("  PAIR RESULTS  (DXY-exit, fractional R)")
print("=" * 72)

all_pair_trades = []
for pair in r.PAIRS:
    trades = r.apply_to_pair_dxy_exit(sigs, pair_dfs[pair], pair, news_dates=news_dates)
    all_pair_trades.extend(trades)

# Per-pair table
print(f"\n  {'Pair':<10} {'N':>4}  {'W':>4} {'L':>4}  {'WR%':>6}  {'PF':>7}  {'NetR':>8}  {'AvgW':>7}  {'AvgL':>7}")
print(f"  {'-'*72}")
for pair in r.PAIRS:
    pt = [t for t in all_pair_trades if t['pair'] == pair]
    s  = r.stats_r(pt, pair)
    if s['N'] == 0: continue
    pf = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "  inf"
    print(f"  {pair:<10} {s['N']:>4}  {s['W']:>4} {s['L']:>4}  "
          f"{s['WR%']:>5.1f}%  {pf:>7}  {s['NetR']:>+8.1f}R"
          f"  {s['AvgW']:>+6.2f}R  {s['AvgL']:>-6.2f}R")

# Portfolio + by type
print()
for label, subset in [
    ("PORTFOLIO",  all_pair_trades),
    ("GAP_REJ",    [t for t in all_pair_trades if t['dxy_type'].startswith('GAP_REJ')]),
    ("REV",        [t for t in all_pair_trades if t['dxy_type'].startswith('REV')]),
]:
    r.print_stats_r(r.stats_r(subset, label))

# Per-pair breakdown by trade type
print()
print("=" * 72)
print("  PER-PAIR TYPE BREAKDOWN")
print("=" * 72)
for pair in r.PAIRS:
    pt_all    = [t for t in all_pair_trades if t['pair'] == pair]
    pt_gr     = [t for t in pt_all if t['dxy_type'].startswith('GAP_REJ')]
    pt_rev    = [t for t in pt_all if t['dxy_type'].startswith('REV')]
    if not pt_all: continue
    s_all = r.stats_r(pt_all, f"{pair} TOTAL")
    s_gr  = r.stats_r(pt_gr,  f"{pair} GAP_REJ")
    s_rev = r.stats_r(pt_rev, f"{pair} REV")
    r.print_stats_r(s_all)
    r.print_stats_r(s_gr)
    r.print_stats_r(s_rev)
    print()

# ── Profit estimate ────────────────────────────────────────────────────────────
r.profit_estimate_r("GAP_REJ + REV (ATTR removed)", all_pair_trades)

print()
