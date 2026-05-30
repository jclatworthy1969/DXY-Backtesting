"""
test_lon_attr_pair_returns.py
=============================
LON_ATTR pair returns analysis:
  - DXY signal detection with optimal thresholds:
      LONG  min_dist = 1000 pts,  TP = zone_bot (near edge),  SL = 1:1
      SHORT min_dist = 1000 pts,  TP = zone_bot (far  edge),  SL = 1:1
  - Zone-based pristine definition, pin bar only, one trade per session
  - Pair positions (EURUSD, USDJPY, USDCAD, XAUUSD) opened at DXY signal bar
  - Pairs have NO individual TP/SL — they close when DXY hits its own TP or SL

For each pair the raw pip/point move is reported since the pair P&L is
open-ended (no fixed target). Pip units:
  EURUSD / USDCAD : price x 10000  (5dp pairs, 1 pip = 0.0001)
  USDJPY          : price x 100    (3dp pair,  1 pip = 0.01)
  XAUUSD          : price x 1      (dollars per oz, no pip conversion)
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules as r

# ── Config ───────────────────────────────────────────────────────────────────
MIN_LONG  = 1000   # pts — optimal from distance sweep
MIN_SHORT = 1000   # pts — optimal from distance sweep
ENTRY_END = 18 * 60

PAIRS     = ['EURUSD', 'USDJPY', 'USDCAD', 'XAUUSD']
PAIR_DIR  = r.PAIR_DIR                          # {pair: +1 or -1}
PIP_UNIT  = {'EURUSD': 10000, 'USDJPY': 100, 'USDCAD': 10000, 'XAUUSD': 1}
PIP_LABEL = {'EURUSD': 'pips', 'USDJPY': 'pips', 'USDCAD': 'pips', 'XAUUSD': '$'}

# ── Load data ────────────────────────────────────────────────────────────────
print("Loading data...")
df_dxy = imp.load_merged('DXY').reset_index(drop=True)
news_dates = r.load_news_filter()
months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"DXY: {len(df_dxy):,} bars  |  {months:.1f} months")

pair_dfs = {}
for p in PAIRS:
    pair_dfs[p] = imp.load_merged(p).reset_index(drop=True)
    print(f"{p}: {len(pair_dfs[p]):,} bars")

# Build time -> row-index lookup for each pair (string key for speed)
pair_tidx = {}
for p, pdf in pair_dfs.items():
    pair_tidx[p] = {str(t): i for i, t in enumerate(pdf['time'])}

print()

# ── Pin bar series ────────────────────────────────────────────────────────────
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


# ── DXY scan — identical to test_lon_attr_zone_tp.py ─────────────────────────
def scan_dxy():
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
        if dist <= -MIN_LONG and lon_pristine_long and bull_pin.at[i]:
            tp = zone_bot
            if tp > cv:
                sl = cv - (tp - cv)
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'long')
            else:
                out, exit_px, exit_bar = 'na', cv, i
            sigs.append({
                'dir': 'LONG', 'dxy_dir': +1,
                'date': ts_str[:10], 'entry_time': ts,
                'entry_bar': i, 'exit_bar': exit_bar,
                'dxy_entry': cv, 'dxy_tp': tp, 'dxy_sl': sl if tp > cv else np.nan,
                'dxy_dist': round(-dist),
                'dxy_out': out, 'dxy_exit_px': exit_px,
            })
            lon_attr_traded = True

        # ── SHORT ─────────────────────────────────────────────────────────────
        elif dist >= MIN_SHORT and lon_pristine_short and bear_pin.at[i]:
            tp = zone_bot
            if tp < cv:
                sl = cv + (cv - tp)
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'short')
            else:
                out, exit_px, exit_bar = 'na', cv, i
            sigs.append({
                'dir': 'SHORT', 'dxy_dir': -1,
                'date': ts_str[:10], 'entry_time': ts,
                'entry_bar': i, 'exit_bar': exit_bar,
                'dxy_entry': cv, 'dxy_tp': tp, 'dxy_sl': sl if tp < cv else np.nan,
                'dxy_dist': round(dist),
                'dxy_out': out, 'dxy_exit_px': exit_px,
            })
            lon_attr_traded = True

    return sigs


# ── Pair P&L for a single signal ──────────────────────────────────────────────
def pair_pnl(sig, pair):
    """
    Returns (entry_px, exit_px, raw_move_in_pip_units, direction_label)
    where raw_move > 0 = profitable for this pair.
    Returns None if no matching bar found.
    """
    pdf   = pair_dfs[pair]
    tidx  = pair_tidx[pair]
    unit  = PIP_UNIT[pair]
    pdir  = PAIR_DIR[pair] * sig['dxy_dir']   # +1=LONG on pair, -1=SHORT on pair

    # Entry: pair close at DXY signal bar time
    entry_key = str(df_dxy.at[sig['entry_bar'], 'time'])
    if entry_key in tidx:
        pi_entry = tidx[entry_key]
    else:
        times = pdf['time'].values.astype(str)
        pos = np.searchsorted(times, entry_key, side='right') - 1
        if pos < 0 or pos >= len(pdf):
            return None
        pi_entry = int(pos)

    # Exit: pair close at DXY exit bar time
    exit_key = str(df_dxy.at[sig['exit_bar'], 'time'])
    if exit_key in tidx:
        pi_exit = tidx[exit_key]
    else:
        times = pdf['time'].values.astype(str)
        pos = np.searchsorted(times, exit_key, side='right') - 1
        if pos < 0 or pos >= len(pdf):
            return None
        pi_exit = int(pos)

    pe = pdf.at[pi_entry, 'close']
    px = pdf.at[pi_exit,  'close']

    # Signed move in pip units: positive = trade made money
    raw = (px - pe) * pdir * unit
    dir_lbl = 'LONG' if pdir == +1 else 'SHORT'
    return pe, px, raw, dir_lbl


# ── Run ───────────────────────────────────────────────────────────────────────
print("Scanning DXY for LON_ATTR signals...")
signals = scan_dxy()
print(f"Signals found: {len(signals)} "
      f"(LONG: {sum(1 for s in signals if s['dir']=='LONG')}, "
      f"SHORT: {sum(1 for s in signals if s['dir']=='SHORT')})\n")

# Compute pair P&L for every signal
for sig in signals:
    for pair in PAIRS:
        result = pair_pnl(sig, pair)
        if result:
            sig[f'{pair}_entry'], sig[f'{pair}_exit'], sig[f'{pair}_pips'], sig[f'{pair}_dir'] = result
        else:
            sig[f'{pair}_entry'] = sig[f'{pair}_exit'] = sig[f'{pair}_pips'] = np.nan
            sig[f'{pair}_dir'] = '?'

df = pd.DataFrame(signals)

# ── DXY summary ───────────────────────────────────────────────────────────────
valid  = df[df['dxy_out'] != 'na']
dxy_w  = (valid['dxy_out'] == 'win').sum()
dxy_l  = (valid['dxy_out'] == 'loss').sum()
dxy_wr = dxy_w / (dxy_w + dxy_l) * 100 if (dxy_w + dxy_l) > 0 else 0

print("=" * 78)
print(f"  DXY LON_ATTR  (dist >= {MIN_LONG} pts, TP = zone edge, SL = 1:1)")
print("=" * 78)
print(f"  Signals: {len(valid)}   W: {dxy_w}   L: {dxy_l}   WR: {dxy_wr:.1f}%   Net: {dxy_w-dxy_l:+d}R")

for direction in ['LONG', 'SHORT']:
    sub = valid[valid['dir'] == direction]
    w = (sub['dxy_out'] == 'win').sum(); l = (sub['dxy_out'] == 'loss').sum()
    wr = w / (w + l) * 100 if (w + l) > 0 else 0
    print(f"    {direction:<6}: N={len(sub)}  W={w}  L={l}  WR={wr:.1f}%  Net={w-l:+d}R")
print()


# ── Per-pair summary ──────────────────────────────────────────────────────────
print("=" * 78)
print("  PAIR RETURNS  (DXY-exit driven — no pair-level TP/SL)")
print("  Pair exits at close of bar when DXY hits its TP or SL")
print("=" * 78)
print(f"  {'Pair':<8}  {'N':>4}  {'Dir→DXY':<12}  "
      f"{'AvgWin':>8}  {'AvgLoss':>9}  {'NetTotal':>10}  {'PF':>6}  Unit")
print(f"  {'-'*72}")

for pair in PAIRS:
    unit  = PIP_UNIT[pair]
    label = PIP_LABEL[pair]
    col   = f'{pair}_pips'

    sub = valid.dropna(subset=[col])
    if len(sub) == 0:
        print(f"  {pair:<8}: no data"); continue

    wins   = sub[sub['dxy_out'] == 'win'][col]
    losses = sub[sub['dxy_out'] == 'loss'][col]

    avg_w   = wins.mean()   if len(wins)   > 0 else float('nan')
    avg_l   = losses.mean() if len(losses) > 0 else float('nan')
    net     = sub[col].sum()
    gross_w = wins[wins > 0].sum()
    gross_l = abs(losses[losses < 0].sum())
    pf      = gross_w / gross_l if gross_l > 0 else float('inf')

    # Direction: DXY LONG → pair goes which way?
    long_dir  = 'LONG'  if PAIR_DIR[pair] * (+1) == +1 else 'SHORT'
    short_dir = 'LONG'  if PAIR_DIR[pair] * (-1) == +1 else 'SHORT'
    dir_str   = f"L->{long_dir[:1]} S->{short_dir[:1]}"

    avg_w_s = f"{avg_w:+.1f}"   if not np.isnan(avg_w)  else "   n/a"
    avg_l_s = f"{avg_l:+.1f}"   if not np.isnan(avg_l)  else "    n/a"
    pf_s    = f"{pf:.2f}"       if pf != float('inf')   else "  inf"

    print(f"  {pair:<8}  {len(sub):>4}  {dir_str:<12}  "
          f"{avg_w_s:>8}  {avg_l_s:>9}  {net:>+10.1f}  {pf_s:>6}  {label}")

print()


# ── Per-pair breakdown: LONG signals vs SHORT signals ─────────────────────────
print("=" * 78)
print("  PAIR RETURNS  split by DXY signal direction")
print("=" * 78)
print(f"  {'Pair':<8}  {'DXY Sig':>8}  {'N':>4}  {'W-trades':>9}  {'L-trades':>9}  "
      f"{'Net':>8}  {'PF':>6}")
print(f"  {'-'*68}")

for pair in PAIRS:
    label = PIP_LABEL[pair]
    col   = f'{pair}_pips'
    for dxy_sig in ['LONG', 'SHORT']:
        sub = valid[(valid['dir'] == dxy_sig)].dropna(subset=[col])
        if len(sub) == 0:
            print(f"  {pair:<8}  {dxy_sig:>8}  {'no data':>4}"); continue

        wins   = sub[sub['dxy_out'] == 'win'][col]
        losses = sub[sub['dxy_out'] == 'loss'][col]
        net    = sub[col].sum()
        gross_w = wins[wins > 0].sum()
        gross_l = abs(losses[losses < 0].sum())
        pf      = gross_w / gross_l if gross_l > 0 else float('inf')
        pf_s    = f"{pf:.2f}" if pf != float('inf') else "  inf"

        avg_w_s = f"{wins.mean():+.1f}"   if len(wins) > 0   else "   n/a"
        avg_l_s = f"{losses.mean():+.1f}" if len(losses) > 0 else "    n/a"

        print(f"  {pair:<8}  {dxy_sig:>8}  {len(sub):>4}  "
              f"{avg_w_s:>9}  {avg_l_s:>9}  {net:>+8.1f}  {pf_s:>6}  {label}")
    print()


# ── Per-trade detail table ────────────────────────────────────────────────────
print("=" * 78)
print("  PER-TRADE DETAIL")
print("=" * 78)
hdr_pairs = "  ".join(f"{p:>8}" for p in PAIRS)
print(f"  {'Date':<11} {'Dir':<6} {'Dist':>5} {'DXY':>5}  {hdr_pairs}")
print(f"  {'-'*76}")

for _, row in df.sort_values('date').iterrows():
    if row['dxy_out'] == 'na':
        continue
    dxy_mk = 'W' if row['dxy_out'] == 'win' else 'L'
    pair_cols = []
    for pair in PAIRS:
        v = row[f'{pair}_pips']
        if np.isnan(v):
            pair_cols.append(f"{'---':>8}")
        else:
            sign = '+' if v >= 0 else ''
            pair_cols.append(f"{sign}{v:>7.1f}")
    pairs_str = "  ".join(pair_cols)
    print(f"  {row['date']:<11} {row['dir']:<6} {row['dxy_dist']:>5} "
          f"{'W' if row['dxy_out']=='win' else 'L':>5}  {pairs_str}")

print()
print(f"  Units: EURUSD/USDCAD in pips, USDJPY in pips, XAUUSD in $/oz")
print(f"  Positive = pair trade was profitable, Negative = pair trade lost")
print()


# ── Correlation check: how often does pair move in right direction? ───────────
print("=" * 78)
print("  CORRELATION: % of trades where pair moved profitably")
print("=" * 78)
for pair in PAIRS:
    col = f'{pair}_pips'
    sub = valid.dropna(subset=[col])
    if len(sub) == 0: continue
    pct_pos = (sub[col] > 0).sum() / len(sub) * 100
    avg_mag = sub[col].abs().mean()
    print(f"  {pair:<8}: {pct_pos:5.1f}% trades profitable  |  "
          f"avg move magnitude: {avg_mag:.1f} {PIP_LABEL[pair]}")
print()
