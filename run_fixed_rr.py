"""
run_fixed_rr.py
===============
Compare fixed R:R targets on pairs vs DXY-exit approach.
Uses same GAP_REJ + REV signals. No ATTR.
Tests 1:1, 2:1, 2.5:1, 3:1 and DXY-exit side-by-side.
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules    as r

# ── Load data & signals ────────────────────────────────────────────────────────
print("Loading data and generating signals...")
df_dxy   = imp.load_merged('DXY')
pair_dfs = {p: imp.load_merged(p) for p in r.PAIRS}
news_dates = r.load_news_filter()

all_sigs = imp.generate_signals_v2(df_dxy, near_edge_tp=True, news_dates=news_dates)
sigs = [s for s in all_sigs if not s['type'].startswith('ATTR')]
months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"  {len(sigs)} signals (GAP_REJ + REV)  |  {months:.1f} months")


# ── Fixed R:R pair application ─────────────────────────────────────────────────
def apply_fixed_rr(dxy_signals, df_pair, pair, rr_mult, news_dates=None):
    """
    Enter pair at DXY entry bar, TP = rr_mult × SL distance, SL = 1R distance.
    Exit when pair price hits TP or SL. No DXY-exit dependency.
    """
    F = r.PAIR_FACTOR[pair]
    D = r.PAIR_DIR[pair]
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}

    results = []
    for sig in dxy_signals:
        et = sig['entry_time']
        if et not in pair_idx:
            continue
        if news_dates and r.news_blocks_pair(news_dates, et, pair):
            continue
        pi  = pair_idx[et]
        pc  = df_pair.at[pi, 'close']

        sl_dist = sig['sl_pts'] / 10000 * F
        tp_dist = sl_dist * rr_mult

        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        direction   = 'long' if pair_long else 'short'

        if direction == 'long':
            sl = pc - sl_dist
            tp = pc + tp_dist
        else:
            sl = pc + sl_dist
            tp = pc - tp_dist

        outcome, exit_px, exit_bar = r.resolve(df_pair, pi, pc, tp, sl, direction)

        results.append({
            'dxy_type'  : sig['type'],
            'pair'      : pair,
            'direction' : direction,
            'entry'     : round(pc, 5),
            'tp'        : round(tp, 5),
            'sl'        : round(sl, 5),
            'outcome'   : outcome,
            'exit_px'   : round(exit_px, 5),
            'r_actual'  : rr_mult if outcome == 'win' else (-1.0 if outcome == 'loss' else 0.0),
        })
    return results


# ── Run all variants ───────────────────────────────────────────────────────────
RR_TESTS = [1.0, 1.5, 2.0, 2.5, 3.0]

variants = {}
for rr in RR_TESTS:
    trades = []
    for pair in r.PAIRS:
        trades.extend(apply_fixed_rr(sigs, pair_dfs[pair], pair, rr, news_dates))
    variants[rr] = trades

# DXY-exit variant
dxy_exit_trades = []
for pair in r.PAIRS:
    dxy_exit_trades.extend(
        r.apply_to_pair_dxy_exit(sigs, pair_dfs[pair], pair, news_dates))
variants['DXY-exit'] = dxy_exit_trades


# ── Summary table ──────────────────────────────────────────────────────────────
print()
print("=" * 80)
print("  PORTFOLIO COMPARISON — fixed R:R targets vs DXY-exit")
print(f"  {'Variant':<12} {'N':>4}  {'W':>4} {'L':>4}  {'WR%':>6}  {'PF':>7}  {'NetR':>9}  {'$/trade':>9}")
print("=" * 80)

risk_per_trade = 250  # $100k × 0.25%
for key, trades in variants.items():
    tdf   = pd.DataFrame(trades)
    n     = len(tdf)
    wins  = tdf[tdf['r_actual'] > 0]
    loss  = tdf[tdf['r_actual'] < 0]
    w, l  = len(wins), len(loss)
    wr    = w / (w + l) * 100 if (w + l) > 0 else 0
    gw    = wins['r_actual'].sum()
    gl    = loss['r_actual'].abs().sum()
    pf    = gw / gl if gl > 0 else float('inf')
    net   = round(tdf['r_actual'].sum(), 1)
    usd   = round(net * risk_per_trade, 0)
    label = f"{key}:1" if isinstance(key, float) else key
    pf_s  = f"{pf:.3f}" if pf != float('inf') else "  inf"
    print(f"  {label:<12} {n:>4}  {w:>4} {l:>4}  {wr:>5.1f}%  {pf_s:>7}  {net:>+9.1f}R  ${usd:>+8,.0f}")


# ── Per-pair breakdown at 2.5:1 ────────────────────────────────────────────────
print()
print("=" * 80)
print("  PER-PAIR RESULTS AT 2.5:1 R:R")
print(f"  {'Pair':<10} {'N':>4}  {'W':>4} {'L':>4}  {'WR%':>6}  {'PF':>7}  {'NetR':>9}  {'Profit':>10}")
print("=" * 80)
trades_25 = variants[2.5]
for pair in r.PAIRS:
    pt  = [t for t in trades_25 if t['pair'] == pair]
    tdf = pd.DataFrame(pt)
    if tdf.empty: continue
    n  = len(tdf)
    w  = (tdf['r_actual'] > 0).sum()
    l  = (tdf['r_actual'] < 0).sum()
    wr = w / (w + l) * 100 if (w + l) > 0 else 0
    gw = tdf[tdf['r_actual'] > 0]['r_actual'].sum()
    gl = tdf[tdf['r_actual'] < 0]['r_actual'].abs().sum()
    pf = gw / gl if gl > 0 else float('inf')
    net = round(tdf['r_actual'].sum(), 1)
    usd = round(net * risk_per_trade, 0)
    pf_s = f"{pf:.3f}" if pf != float('inf') else "  inf"
    print(f"  {pair:<10} {n:>4}  {w:>4} {l:>4}  {wr:>5.1f}%  {pf_s:>7}  {net:>+9.1f}R  ${usd:>+9,.0f}")

# Per-pair + type at 2.5:1
print()
print("  TYPE BREAKDOWN AT 2.5:1 R:R")
print(f"  {'Pair+Type':<18} {'N':>4}  {'W':>4} {'L':>4}  {'WR%':>6}  {'NetR':>9}")
print(f"  {'-'*52}")
for pair in r.PAIRS:
    for ttype in ['GAP_REJ', 'REV']:
        pt  = [t for t in trades_25 if t['pair'] == pair and t['dxy_type'].startswith(ttype)]
        if not pt: continue
        tdf = pd.DataFrame(pt)
        n  = len(tdf)
        w  = (tdf['r_actual'] > 0).sum()
        l  = (tdf['r_actual'] < 0).sum()
        wr = w / (w + l) * 100 if (w + l) > 0 else 0
        net = round(tdf['r_actual'].sum(), 1)
        print(f"  {pair+' '+ttype:<18} {n:>4}  {w:>4} {l:>4}  {wr:>5.1f}%  {net:>+9.1f}R")
    print()


# ── Head-to-head: 2.5:1 vs DXY-exit per pair ──────────────────────────────────
print("=" * 80)
print("  2.5:1 FIXED  vs  DXY-EXIT  — per pair head-to-head")
print(f"  {'Pair':<10}  {'2.5:1 WR':>8}  {'2.5:1 NetR':>11}  {'DXY-exit WR':>11}  {'DXY-exit NetR':>13}  {'Delta':>8}")
print("=" * 80)
for pair in r.PAIRS:
    p25  = [t for t in trades_25       if t['pair'] == pair]
    pdx  = [t for t in dxy_exit_trades if t['pair'] == pair]
    if not p25 or not pdx: continue

    def quick(trades):
        tdf = pd.DataFrame(trades)
        w = (tdf['r_actual'] > 0).sum()
        l = (tdf['r_actual'] < 0).sum()
        wr = w / (w + l) * 100 if (w + l) > 0 else 0
        return round(wr, 1), round(tdf['r_actual'].sum(), 1)

    wr25, net25 = quick(p25)
    wrdx, netdx = quick(pdx)
    delta = netdx - net25
    sign  = "+" if delta >= 0 else ""
    print(f"  {pair:<10}  {wr25:>7.1f}%  {net25:>+10.1f}R  {wrdx:>10.1f}%  {netdx:>+12.1f}R  {sign}{delta:>+7.1f}R")

print()
