"""
test_exit_comparison.py
=======================
Tests the hypothesis: should pair trades exit when the PAIR itself hits its
estimated 1:1 TP/SL, rather than waiting for DXY to hit its own TP/SL?

EXIT_A  (current / DXY-exit):
    Pair closes at its price on the bar where DXY hits its TP or SL.
    P&L is fractional R (pair's actual move / DXY-equivalent SL distance).

EXIT_B  (pair-1:1-exit):
    Pair closes when its own price hits the estimated 1:1 TP or SL level.
    P&L is binary +1R / -1R.

All 8 pairs tested: EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY, XAUUSD
All 4 signal types: REV, GAP_REJ, LON_ATTR, ATTR
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
import dxy_clean_rules    as r
import dxy_improved_rules as imp

# ── Pair configuration (all 8 pairs) ─────────────────────────────────────────
BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

ALL_PAIRS = ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCAD', 'USDCHF', 'USDJPY', 'XAUUSD']

PAIR_DIR_EXT = {
    'EURUSD': -1, 'GBPUSD': -1, 'AUDUSD': -1, 'NZDUSD': -1,
    'USDCAD': +1, 'USDCHF': +1, 'USDJPY': +1, 'XAUUSD': -1,
}
PAIR_FACTOR_EXT = {
    'EURUSD': 0.01, 'GBPUSD': 0.01, 'AUDUSD': 0.01, 'NZDUSD': 0.01,
    'USDCAD': 0.01, 'USDCHF': 0.01, 'USDJPY': 1.0,  'XAUUSD': 100.0,
}

PAIR_FILES = {
    'DXY':    BASE / 'TVC_DXY, 15_merged.csv',
    'EURUSD': BASE / 'FX_EURUSD, 15_merged.csv',
    'GBPUSD': BASE / 'FX_GBPUSD, 15 (1).csv',
    'AUDUSD': BASE / 'FX_AUDUSD, 15 (1).csv',
    'NZDUSD': BASE / 'FX_NZDUSD, 15 (1).csv',
    'USDCAD': BASE / 'FX_USDCAD, 15_merged.csv',
    'USDCHF': BASE / 'FX_USDCHF, 15 (1).csv',
    'USDJPY': BASE / 'FX_USDJPY, 15_merged.csv',
    'XAUUSD': BASE / 'FX_XAUUSD, 15_merged.csv',
}

LON_ATTR_MIN_LONG  = 1000
LON_ATTR_MIN_SHORT = 1000
ENTRY_END_MIN      = 18 * 60


# ── Data loading ──────────────────────────────────────────────────────────────
def load_pair(sym):
    path = PAIR_FILES[sym]
    df = pd.read_csv(path)
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['time', 'open', 'high', 'low', 'close']].copy()


# ── Exit method A: DXY-exit (fractional R) ───────────────────────────────────
def apply_dxy_exit(sigs, df_pair, pair, news_dates=None):
    """Exit pair at DXY's TP/SL bar. Returns fractional R per trade."""
    F = PAIR_FACTOR_EXT[pair]
    D = PAIR_DIR_EXT[pair]
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}

    results = []
    for sig in sigs:
        et, xt = sig['entry_time'], sig.get('exit_time')
        if et not in pair_idx or not xt or xt not in pair_idx:
            continue
        if news_dates and r.news_blocks_pair(news_dates, et, pair):
            continue

        pi, xi = pair_idx[et], pair_idx[xt]
        pc = df_pair.at[pi, 'close']
        px = df_pair.at[xi, 'close']

        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        sl_dist     = sig['sl_pts'] / 10000 * F

        raw_pnl = (px - pc) if pair_long else (pc - px)
        r_val   = raw_pnl / sl_dist if sl_dist > 0 else 0.0
        outcome = 'win' if r_val > 0 else ('loss' if r_val < 0 else 'even')

        results.append({
            'signal': sig['type'], 'pair': pair,
            'entry_time': et, 'exit_time': xt,
            'direction': 'long' if pair_long else 'short',
            'entry': round(pc, 5), 'exit_px': round(px, 5),
            'sl_pts_dxy': sig['sl_pts'],
            'dxy_outcome': sig['outcome'],
            'outcome': outcome, 'r_val': round(r_val, 4),
        })
    return results


# ── Exit method B: pair 1:1 exit (binary ±1R) ────────────────────────────────
def apply_pair_exit(sigs, df_pair, pair, news_dates=None):
    """Exit pair when pair price hits its own estimated 1:1 TP or SL."""
    F = PAIR_FACTOR_EXT[pair]
    D = PAIR_DIR_EXT[pair]
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}

    results = []
    for sig in sigs:
        et = sig['entry_time']
        if et not in pair_idx:
            continue
        if news_dates and r.news_blocks_pair(news_dates, et, pair):
            continue

        pi = pair_idx[et]
        pc = df_pair.at[pi, 'close']

        sl_dist = sig['sl_pts'] / 10000 * F
        tp_dist = sig.get('tp_pts', sig['sl_pts']) / 10000 * F

        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        direction   = 'long' if pair_long else 'short'

        if direction == 'long':
            tp, sl = pc + tp_dist, pc - sl_dist
        else:
            tp, sl = pc - tp_dist, pc + sl_dist

        outcome, exit_px, exit_bar = r.resolve(df_pair, pi, pc, tp, sl, direction)
        r_val = 1.0 if outcome == 'win' else (-1.0 if outcome == 'loss' else 0.0)

        results.append({
            'signal': sig['type'], 'pair': pair,
            'entry_time': et,
            'exit_time': str(df_pair.at[exit_bar, 'time']),
            'direction': direction,
            'entry': round(pc, 5), 'exit_px': round(exit_px, 5),
            'sl_pts_dxy': sig['sl_pts'],
            'dxy_outcome': sig['outcome'],
            'outcome': outcome, 'r_val': r_val,
        })
    return results


# ── LON_ATTR signal scanner (matches current Pine script) ─────────────────────
def scan_lon_attr(df_dxy, news_dates=None):
    c_s, o_s = df_dxy['close'], df_dxy['open']
    h_s, l_s = df_dxy['high'],  df_dxy['low']
    body        = (c_s - o_s).abs()
    body_top    = pd.concat([o_s, c_s], axis=1).max(axis=1)
    body_bottom = pd.concat([o_s, c_s], axis=1).min(axis=1)
    hi_wick     = h_s - body_top
    lo_wick     = body_bottom - l_s
    rng_s       = (h_s - l_s).replace(0, np.nan)
    PMW         = r.PIN_WICK_MULT

    bull_pin = (lo_wick >= body * PMW) & (lo_wick >= hi_wick * 1.5) & rng_s.notna()
    bear_pin = (hi_wick >= body * PMW) & (hi_wick >= lo_wick * 1.5) & rng_s.notna()
    both     = bull_pin & bear_pin
    bull_pin = bull_pin & ~(both & (c_s <= o_s))
    bear_pin = bear_pin & ~(both & (c_s >= o_s))

    london_open_price  = np.nan
    zone_top           = np.nan
    zone_bot           = np.nan
    lon_pristine_long  = True
    lon_pristine_short = True
    lon_attr_traded    = False
    sigs = []

    for i in range(2, len(df_dxy)):
        row = df_dxy.iloc[i]
        cv, ov = row['close'], row['open']
        ts = row['time']
        hour, minute = ts.hour, ts.minute
        curr_min = hour * 60 + minute
        dow = ts.dayofweek
        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        is_lon = (not in_japan and hour == 7 and minute == 0 and dow != 0)
        is_mon = (not in_japan and hour == 6 and minute == 30 and dow == 0)

        if is_lon or is_mon:
            london_open_price  = ov
            zone_top           = max(ov, cv)
            zone_bot           = min(ov, cv)
            lon_pristine_long  = True
            lon_pristine_short = True
            lon_attr_traded    = False
            continue

        if np.isnan(london_open_price) or in_japan:
            continue

        if not np.isnan(zone_top):
            if ov >= zone_top or cv >= zone_top:
                lon_pristine_long = False
            if ov <= zone_bot or cv <= zone_bot:
                lon_pristine_short = False

        lon_start = (6 * 60 + 30) if dow == 0 else (7 * 60)
        if not (lon_start < curr_min <= ENTRY_END_MIN):
            continue
        if lon_attr_traded:
            continue

        ts_str = str(ts)
        if news_dates and r.news_blocks_pair(news_dates, ts_str, 'ALL_USD'):
            continue

        dist = (cv - london_open_price) * 10000

        if dist <= -LON_ATTR_MIN_LONG and lon_pristine_long and bull_pin.at[i]:
            tp = zone_bot
            if tp > cv:
                sl_d = tp - cv
                sl   = cv - sl_d
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'long')
                sigs.append({
                    'type': 'LON_ATTR_LONG', 'entry_time': ts_str,
                    'entry': round(cv, 5), 'tp': round(tp, 5), 'sl': round(sl, 5),
                    'sl_pts': round(sl_d * 10000), 'tp_pts': round(sl_d * 10000),
                    'outcome': out, 'exit_px': round(exit_px, 5),
                    'exit_time': str(df_dxy.at[exit_bar, 'time']),
                    'bias_1h': 0, 'bias_4h': 0,
                })
                lon_attr_traded = True

        elif dist >= LON_ATTR_MIN_SHORT and lon_pristine_short and bear_pin.at[i]:
            tp = zone_bot
            if tp < cv:
                sl_d = cv - tp
                sl   = cv + sl_d
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'short')
                sigs.append({
                    'type': 'LON_ATTR_SHORT', 'entry_time': ts_str,
                    'entry': round(cv, 5), 'tp': round(tp, 5), 'sl': round(sl, 5),
                    'sl_pts': round(sl_d * 10000), 'tp_pts': round(sl_d * 10000),
                    'outcome': out, 'exit_px': round(exit_px, 5),
                    'exit_time': str(df_dxy.at[exit_bar, 'time']),
                    'bias_1h': 0, 'bias_4h': 0,
                })
                lon_attr_traded = True

    return sigs


# ── Stats helpers ─────────────────────────────────────────────────────────────
def compute_stats(trades):
    if not trades:
        return dict(N=0, W=0, L=0, **{'WR%': 0.0}, PF=0.0, NetR=0.0, AvgW=0.0, AvgL=0.0)
    df  = pd.DataFrame(trades)
    n   = len(df)
    w   = (df['r_val'] > 0).sum()
    l   = (df['r_val'] < 0).sum()
    wr  = w / (w + l) * 100 if (w + l) > 0 else 0
    gw  = df[df['r_val'] > 0]['r_val'].sum()
    gl  = df[df['r_val'] < 0]['r_val'].abs().sum()
    pf  = gw / gl if gl > 0 else float('inf')
    net = df['r_val'].sum()
    avg_w = gw / w if w > 0 else 0
    avg_l = gl / l if l > 0 else 0
    return dict(N=n, W=int(w), L=int(l), **{'WR%': round(wr, 1)}, PF=round(pf, 3),
                NetR=round(net, 2), AvgW=round(avg_w, 3), AvgL=round(avg_l, 3))


def pf_str(pf):
    return "  inf" if pf == float('inf') else f"{pf:.3f}"


# ── Print helpers ─────────────────────────────────────────────────────────────
HDR = f"  {'Pair':<8}  {'Exit':<7}  {'N':>4}  {'W':>4} {'L':>4}  " \
      f"{'WR%':>6}  {'PF':>6}  {'NetR':>8}  {'AvgW':>6} {'AvgL':>6}"
SEP = "  " + "-" * 72


def print_row(pair, label, s, highlight=False):
    tag = " **" if highlight else ""
    print(f"  {pair:<8}  {label:<7}  {s['N']:>4}  {s['W']:>4} {s['L']:>4}  "
          f"{s['WR%']:>5.1f}%  {pf_str(s['PF']):>6}  {s['NetR']:>+8.2f}R  "
          f"{s['AvgW']:>+5.3f}R {s['AvgL']:>-5.3f}R{tag}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Loading data...")
    df_dxy   = load_pair('DXY')
    pair_dfs = {}
    for p in ALL_PAIRS:
        try:
            pair_dfs[p] = load_pair(p)
            print(f"  {p}: {len(pair_dfs[p]):,} bars")
        except FileNotFoundError:
            print(f"  {p}: FILE NOT FOUND — skipping")

    available_pairs = [p for p in ALL_PAIRS if p in pair_dfs]

    news_dates = r.load_news_filter()
    print(f"\nNews filter: {len(news_dates)} high-impact dates loaded" if news_dates
          else "\nNews filter: not found — running without")

    print("\nGenerating signals (REV / GAP_REJ / ATTR)...")
    sigs_main = imp.generate_signals_v2(df_dxy, near_edge_tp=True,
                                        news_dates=news_dates)
    print("Generating LON_ATTR signals...")
    sigs_lon  = scan_lon_attr(df_dxy, news_dates=news_dates)
    all_sigs  = sigs_main + sigs_lon

    sig_groups = {
        'REV':      [s for s in all_sigs if s['type'].startswith('REV')],
        'GAP_REJ':  [s for s in all_sigs if s['type'].startswith('GAP_REJ')],
        'LON_ATTR': [s for s in all_sigs if s['type'].startswith('LON_ATTR')],
        'ATTR':     [s for s in all_sigs if s['type'].startswith('ATTR')],
        'ALL':      all_sigs,
    }
    print(f"\nSignal counts:")
    for k, v in sig_groups.items():
        if k != 'ALL':
            print(f"  {k:<10}: {len(v):>4}")
    print(f"  {'TOTAL':<10}: {len(all_sigs):>4}")

    # ── Run both exit methods for every pair ──────────────────────────────────
    results_a = {}   # DXY-exit
    results_b = {}   # Pair 1:1-exit

    for pair in available_pairs:
        results_a[pair] = apply_dxy_exit(all_sigs, pair_dfs[pair], pair, news_dates)
        results_b[pair] = apply_pair_exit(all_sigs, pair_dfs[pair], pair, news_dates)

    # ── Overall comparison: all signals, all pairs ────────────────────────────
    print()
    print("=" * 78)
    print("  OVERALL COMPARISON — All signals, per pair")
    print("  EXIT_A = DXY-exit (current)    EXIT_B = Pair 1:1-exit (test)")
    print("=" * 78)
    print(HDR)
    print(SEP)

    net_a_total, net_b_total = 0.0, 0.0
    for pair in available_pairs:
        sa = compute_stats(results_a[pair])
        sb = compute_stats(results_b[pair])
        better_b = sb['NetR'] > sa['NetR']
        print_row(pair, "EXIT_A", sa, highlight=not better_b)
        print_row(pair, "EXIT_B", sb, highlight=better_b)
        net_a_total += sa['NetR']
        net_b_total += sb['NetR']
        print()

    print(SEP)
    sa_all = compute_stats([t for p in available_pairs for t in results_a[p]])
    sb_all = compute_stats([t for p in available_pairs for t in results_b[p]])
    print_row("PORTFOLIO", "EXIT_A", sa_all)
    print_row("PORTFOLIO", "EXIT_B", sb_all)

    # ── Per signal type, per pair ─────────────────────────────────────────────
    sig_type_list = ['REV', 'GAP_REJ', 'LON_ATTR', 'ATTR']
    for sig_type in sig_type_list:
        print()
        print("=" * 78)
        print(f"  SIGNAL TYPE: {sig_type}")
        print("=" * 78)
        print(HDR)
        print(SEP)

        for pair in available_pairs:
            ra = [t for t in results_a[pair] if t['signal'].startswith(sig_type)]
            rb = [t for t in results_b[pair] if t['signal'].startswith(sig_type)]
            sa = compute_stats(ra)
            sb = compute_stats(rb)
            if sa['N'] == 0 and sb['N'] == 0:
                continue
            better_b = sb['NetR'] > sa['NetR']
            print_row(pair, "EXIT_A", sa, highlight=not better_b)
            print_row(pair, "EXIT_B", sb, highlight=better_b)
            print()

        # Signal-type portfolio row
        ra_all = [t for p in available_pairs for t in results_a[p]
                  if t['signal'].startswith(sig_type)]
        rb_all = [t for p in available_pairs for t in results_b[p]
                  if t['signal'].startswith(sig_type)]
        sa_all = compute_stats(ra_all)
        sb_all = compute_stats(rb_all)
        print(SEP)
        print_row(f"{sig_type} TTL", "EXIT_A", sa_all)
        print_row(f"{sig_type} TTL", "EXIT_B", sb_all)

    # ── Summary verdict ───────────────────────────────────────────────────────
    print()
    print("=" * 78)
    print("  VERDICT SUMMARY  (** marks the better exit per row above)")
    print("=" * 78)
    print(f"  {'Pair':<10}  {'EXIT_A NetR':>12}  {'EXIT_B NetR':>12}  "
          f"{'Delta (B-A)':>12}  {'Winner':<8}")
    print(f"  {'-'*62}")
    for pair in available_pairs:
        sa = compute_stats(results_a[pair])
        sb = compute_stats(results_b[pair])
        delta = sb['NetR'] - sa['NetR']
        winner = "EXIT_B **" if delta > 0 else "EXIT_A **"
        print(f"  {pair:<10}  {sa['NetR']:>+12.2f}R  {sb['NetR']:>+12.2f}R  "
              f"{delta:>+12.2f}R  {winner}")
    print(f"  {'-'*62}")
    total_a = sum(compute_stats(results_a[p])['NetR'] for p in available_pairs)
    total_b = sum(compute_stats(results_b[p])['NetR'] for p in available_pairs)
    delta_t = total_b - total_a
    winner_t = "EXIT_B **" if delta_t > 0 else "EXIT_A **"
    print(f"  {'TOTAL':<10}  {total_a:>+12.2f}R  {total_b:>+12.2f}R  "
          f"{delta_t:>+12.2f}R  {winner_t}")

    # ── Save detailed results ─────────────────────────────────────────────────
    rows_a = [t for p in available_pairs for t in results_a[p]]
    rows_b = [t for p in available_pairs for t in results_b[p]]
    pd.DataFrame(rows_a).to_csv(BASE / 'exit_comparison_dxy_exit.csv',   index=False)
    pd.DataFrame(rows_b).to_csv(BASE / 'exit_comparison_pair_exit.csv',  index=False)
    print(f"\n  Saved: exit_comparison_dxy_exit.csv / exit_comparison_pair_exit.csv")


if __name__ == '__main__':
    main()
