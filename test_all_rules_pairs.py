"""
test_all_rules_pairs.py
=======================
Unified backtest of ALL DXY trading rules applied to pairs.

Signal types (all DXY-signal driven, pairs exit when DXY hits TP or SL):

  ATTR      -- Gap fill toward prior session close (pristine gap, 4H BB flat)
  GAP_REJ   -- Same as ATTR but zone already touched once (re-entry on pullback)
  REV       -- Return to London open after moving away (BB slope gate, structural SL)
  LON_ATTR  -- Attraction from >= 1000pts away (zone-pristine, pin bar, 1:1 SL)
               LONG TP = zone_bot (near edge), SHORT TP = zone_bot (far edge)

Pair exit method: DXY-exit throughout.
  - Pair enters at its close when the DXY signal fires.
  - Pair exits at its close at the bar when DXY hits its own TP or SL.
  - P&L reported as fractional R (actual pair move / DXY-equivalent pair SL distance).

Pairs tested: EURUSD, USDJPY, USDCAD, XAUUSD
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules as r

# ── Config ───────────────────────────────────────────────────────────────────
LON_ATTR_MIN_LONG  = 1000    # pts — optimal from distance sweep
LON_ATTR_MIN_SHORT = 1000    # pts
ENTRY_END          = 18 * 60
ACCOUNT            = 100_000
RISK_PCT           = 0.0025  # 0.25% per trade

PAIRS = r.PAIRS  # ['EURUSD', 'USDJPY', 'USDCAD', 'XAUUSD']


# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading data...")
df_dxy   = imp.load_merged('DXY').reset_index(drop=True)
pair_dfs = {p: imp.load_merged(p).reset_index(drop=True) for p in PAIRS}
news_dates = r.load_news_filter()

months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"DXY: {len(df_dxy):,} bars  |  {df_dxy['time'].min().date()} to "
      f"{df_dxy['time'].max().date()}  ({months:.1f} months)")
for p in PAIRS:
    print(f"  {p}: {len(pair_dfs[p]):,} bars")
print()


# ── LON_ATTR signal scanner ───────────────────────────────────────────────────
# Produces dicts compatible with r.apply_to_pair_dxy_exit
def scan_lon_attr():
    # Pin bar series
    c_s = df_dxy['close']; o_s = df_dxy['open']
    h_s = df_dxy['high'];  l_s = df_dxy['low']
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
                lon_pristine_long  = False
            if ov <= zone_bot or cv <= zone_bot:
                lon_pristine_short = False

        lon_start = (6*60+30) if dow == 0 else (7*60)
        if not (lon_start < curr_min <= ENTRY_END):
            continue
        if lon_attr_traded:
            continue
        ts_str = str(ts)
        if news_dates and r.news_blocks_pair(news_dates, ts_str, 'ALL_USD'):
            continue

        dist = (cv - london_open_price) * 10000

        # ── LONG ─────────────────────────────────────────────────────────────
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
                    'london_open': round(london_open_price, 5),
                    'pristine': True,
                    'outcome': out, 'exit_px': round(exit_px, 5),
                    'exit_time': str(df_dxy.at[exit_bar, 'time']),
                    'bias_1h': 0, 'bias_4h': 0,
                })
                lon_attr_traded = True

        # ── SHORT ─────────────────────────────────────────────────────────────
        elif dist >= LON_ATTR_MIN_SHORT and lon_pristine_short and bear_pin.at[i]:
            tp = zone_bot  # far edge for SHORT
            if tp < cv:
                sl_d = cv - tp
                sl   = cv + sl_d
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'short')
                sigs.append({
                    'type': 'LON_ATTR_SHORT', 'entry_time': ts_str,
                    'entry': round(cv, 5), 'tp': round(tp, 5), 'sl': round(sl, 5),
                    'sl_pts': round(sl_d * 10000), 'tp_pts': round(sl_d * 10000),
                    'london_open': round(london_open_price, 5),
                    'pristine': True,
                    'outcome': out, 'exit_px': round(exit_px, 5),
                    'exit_time': str(df_dxy.at[exit_bar, 'time']),
                    'bias_1h': 0, 'bias_4h': 0,
                })
                lon_attr_traded = True

    return sigs


# ── Run all signal generators ─────────────────────────────────────────────────
print("Generating ATTR / GAP_REJ / REV signals (improved rules)...")
sigs_legacy = imp.generate_signals_v2(df_dxy, near_edge_tp=True, news_dates=news_dates)

print("Generating LON_ATTR signals...")
sigs_lon_attr = scan_lon_attr()

all_sigs = sigs_legacy + sigs_lon_attr

# Signal counts
def count_type(sigs, prefix):
    return len([s for s in sigs if s['type'].startswith(prefix)])

print(f"\nSignal summary:")
print(f"  ATTR      : {count_type(all_sigs,'ATTR'):>4}")
print(f"  GAP_REJ   : {count_type(all_sigs,'GAP_REJ'):>4}")
print(f"  REV       : {count_type(all_sigs,'REV'):>4}")
print(f"  LON_ATTR  : {count_type(all_sigs,'LON_ATTR'):>4}")
print(f"  TOTAL     : {len(all_sigs):>4}")
print()


# ── Apply all signals to pairs via DXY-exit ───────────────────────────────────
print("Applying to pairs (DXY-exit)...")
all_pair_trades = []
for pair in PAIRS:
    trades = r.apply_to_pair_dxy_exit(all_sigs, pair_dfs[pair], pair,
                                       news_dates=news_dates)
    all_pair_trades.extend(trades)
print(f"Total pair-trades: {len(all_pair_trades)}")
print()


# ── Helpers ───────────────────────────────────────────────────────────────────
SIGNAL_GROUPS = [
    ('ALL',      lambda s: True),
    ('ATTR',     lambda s: s['dxy_type'].startswith('ATTR')),
    ('GAP_REJ',  lambda s: s['dxy_type'].startswith('GAP_REJ')),
    ('REV',      lambda s: s['dxy_type'].startswith('REV')),
    ('LON_ATTR', lambda s: s['dxy_type'].startswith('LON_ATTR')),
]

def dxy_stats(sigs, prefix=None):
    sub = [s for s in sigs if (prefix is None or s['type'].startswith(prefix))]
    n = len(sub)
    if n == 0: return n, 0, 0, float('nan'), 0
    w = sum(1 for s in sub if s['outcome'] == 'win')
    l = sum(1 for s in sub if s['outcome'] == 'loss')
    wr = w / (w + l) * 100 if (w + l) > 0 else float('nan')
    return n, w, l, wr, w - l

def pair_stats_r(trades):
    """Fractional-R stats for a list of pair trades."""
    if not trades:
        return 0, 0, 0, float('nan'), float('inf'), 0.0, 0.0, 0.0
    df = pd.DataFrame(trades)
    n     = len(df)
    wins  = df[df['r_actual'] > 0]
    loss  = df[df['r_actual'] < 0]
    w, l  = len(wins), len(loss)
    wr    = w / (w + l) * 100 if (w + l) > 0 else float('nan')
    gw    = wins['r_actual'].sum()
    gl    = loss['r_actual'].abs().sum()
    pf    = gw / gl if gl > 0 else float('inf')
    net   = df['r_actual'].sum()
    avg_w = gw / w if w > 0 else 0.0
    avg_l = gl / l if l > 0 else 0.0
    return n, w, l, wr, pf, net, avg_w, avg_l

def fmt_pf(pf):
    return f"{pf:.2f}" if pf != float('inf') else " inf"

def fmt_wr(wr):
    return f"{wr:.1f}%" if not np.isnan(wr) else "  n/a"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — DXY SIGNAL QUALITY
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("  DXY SIGNAL QUALITY  (source trades on DXY itself)")
print("=" * 78)
print(f"  {'Type':<12} {'N':>5}  {'W':>4}  {'L':>4}  {'WR%':>7}  {'Net':>6}")
print(f"  {'-'*52}")
for prefix, label in [('ATTR','ATTR'), ('GAP_REJ','GAP_REJ'), ('REV','REV'),
                       ('LON_ATTR','LON_ATTR'), (None,'ALL')]:
    n, w, l, wr, net = dxy_stats(all_sigs, prefix)
    if n == 0: continue
    print(f"  {label:<12} {n:>5}  {w:>4}  {l:>4}  {fmt_wr(wr):>7}  {net:>+6}R")
print()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — PER-PAIR RESULTS by signal type
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("  PAIR RESULTS BY SIGNAL TYPE  (fractional R, DXY-exit)")
print("=" * 78)

for sig_label, sig_filter in SIGNAL_GROUPS[1:]:  # skip ALL
    filtered = [t for t in all_pair_trades if sig_filter(t)]
    if not filtered:
        continue
    n, w, l, wr, pf, net, avg_w, avg_l = pair_stats_r(filtered)
    print(f"\n  ── {sig_label}  (total: {n} pair-trades, "
          f"WR={fmt_wr(wr)}, PF={fmt_pf(pf)}, Net={net:+.1f}R) ──")
    print(f"  {'Pair':<10}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR%':>7}  "
          f"{'PF':>6}  {'NetR':>8}  {'AvgW':>7}  {'AvgL':>7}")
    print(f"  {'-'*70}")
    for pair in PAIRS:
        pt = [t for t in filtered if t['pair'] == pair]
        if not pt: continue
        n_p, w_p, l_p, wr_p, pf_p, net_p, aw_p, al_p = pair_stats_r(pt)
        print(f"  {pair:<10}  {n_p:>4}  {w_p:>4}  {l_p:>4}  "
              f"{fmt_wr(wr_p):>7}  {fmt_pf(pf_p):>6}  {net_p:>+8.1f}R  "
              f"{aw_p:>+6.2f}R  {al_p:>-6.2f}R")
print()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PER-PAIR SUMMARY across all signal types
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("  PER-PAIR COMBINED SUMMARY  (all signal types together)")
print("=" * 78)
print(f"  {'Pair':<10}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR%':>7}  "
      f"{'PF':>6}  {'NetR':>8}  {'AvgW':>7}  {'AvgL':>7}")
print(f"  {'-'*70}")
for pair in PAIRS:
    pt = [t for t in all_pair_trades if t['pair'] == pair]
    if not pt: continue
    n_p, w_p, l_p, wr_p, pf_p, net_p, aw_p, al_p = pair_stats_r(pt)
    print(f"  {pair:<10}  {n_p:>4}  {w_p:>4}  {l_p:>4}  "
          f"{fmt_wr(wr_p):>7}  {fmt_pf(pf_p):>6}  {net_p:>+8.1f}R  "
          f"{aw_p:>+6.2f}R  {al_p:>-6.2f}R")

# Portfolio row
n_p, w_p, l_p, wr_p, pf_p, net_p, aw_p, al_p = pair_stats_r(all_pair_trades)
print(f"  {'-'*70}")
print(f"  {'PORTFOLIO':<10}  {n_p:>4}  {w_p:>4}  {l_p:>4}  "
      f"{fmt_wr(wr_p):>7}  {fmt_pf(pf_p):>6}  {net_p:>+8.1f}R  "
      f"{aw_p:>+6.2f}R  {al_p:>-6.2f}R")
print()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — SIGNAL TYPE CONTRIBUTION breakdown per pair
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("  SIGNAL TYPE CONTRIBUTION PER PAIR  (Net R by type)")
print("=" * 78)
# Header
type_labels = ['ATTR', 'GAP_REJ', 'REV', 'LON_ATTR', 'TOTAL']
print(f"  {'Pair':<10}  " + "  ".join(f"{t:>10}" for t in type_labels))
print(f"  {'-'*70}")
for pair in PAIRS:
    row_parts = []
    total_net = 0.0
    for prefix in ['ATTR', 'GAP_REJ', 'REV', 'LON_ATTR']:
        pt = [t for t in all_pair_trades if t['pair'] == pair
              and t['dxy_type'].startswith(prefix)]
        _, _, _, _, _, net_p, _, _ = pair_stats_r(pt)
        total_net += net_p
        row_parts.append(f"{net_p:>+9.1f}R")
    row_parts.append(f"{total_net:>+9.1f}R")
    print(f"  {pair:<10}  " + "  ".join(row_parts))

# Portfolio contribution row
print(f"  {'-'*70}")
port_parts = []
port_total = 0.0
for prefix in ['ATTR', 'GAP_REJ', 'REV', 'LON_ATTR']:
    pt = [t for t in all_pair_trades if t['dxy_type'].startswith(prefix)]
    _, _, _, _, _, net_p, _, _ = pair_stats_r(pt)
    port_total += net_p
    port_parts.append(f"{net_p:>+9.1f}R")
port_parts.append(f"{port_total:>+9.1f}R")
print(f"  {'TOTAL':<10}  " + "  ".join(port_parts))
print()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — DOLLAR PROFIT ESTIMATE
# ═══════════════════════════════════════════════════════════════════════════════
risk_per_trade = ACCOUNT * RISK_PCT

print("=" * 78)
print(f"  DOLLAR PROFIT ESTIMATE")
print(f"  Account: ${ACCOUNT:,.0f}  |  Risk per trade: {RISK_PCT*100:.2f}%  = ${risk_per_trade:,.0f}")
print(f"  Period: {months:.1f} months  |  Each 1R = ${risk_per_trade:,.0f}")
print("=" * 78)
print(f"  {'Pair':<10}  {'Trades':>6}  {'WR%':>7}  {'Net R':>8}  {'$ P&L':>12}")
print(f"  {'-'*56}")
total_net_r = 0.0
for pair in PAIRS:
    pt = [t for t in all_pair_trades if t['pair'] == pair]
    if not pt: continue
    n_p, w_p, l_p, wr_p, pf_p, net_p, aw_p, al_p = pair_stats_r(pt)
    dollar = net_p * risk_per_trade
    total_net_r += net_p
    sign = "+" if dollar >= 0 else ""
    print(f"  {pair:<10}  {n_p:>6}  {fmt_wr(wr_p):>7}  {net_p:>+8.1f}R  "
          f"{sign}${dollar:>10,.0f}")

print(f"  {'-'*56}")
total_dollar = total_net_r * risk_per_trade
sign = "+" if total_dollar >= 0 else ""
print(f"  {'TOTAL':<10}  {'':>6}  {'':>7}  {total_net_r:>+8.1f}R  "
      f"{sign}${total_dollar:>10,.0f}")

ann_r      = total_net_r / months * 12
ann_dollar = ann_r * risk_per_trade
sign2 = "+" if ann_dollar >= 0 else ""
print(f"\n  Annualised ({months:.1f}-month backtest extrapolated to 12 months):")
print(f"    {sign2}{ann_dollar:,.0f} / yr  ({sign2}{ann_dollar/ACCOUNT*100:.1f}% return on account)")
print(f"    {ann_r:+.1f} R/yr")
print()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — SIGNAL FREQUENCY
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("  SIGNAL FREQUENCY")
print("=" * 78)
print(f"  {'Type':<12}  {'Total':>6}  {'Per Month':>10}  {'DXY WR':>8}")
print(f"  {'-'*46}")
for prefix, label in [('ATTR','ATTR'), ('GAP_REJ','GAP_REJ'), ('REV','REV'),
                       ('LON_ATTR','LON_ATTR'), (None,'ALL')]:
    n, w, l, wr, net = dxy_stats(all_sigs, prefix)
    if n == 0: continue
    sep = "-" if label == 'ALL' else ""
    print(f"  {label:<12}  {n:>6}  {n/months:>9.1f}/mo  {fmt_wr(wr):>8}")
print()
