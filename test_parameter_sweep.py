"""
test_parameter_sweep.py
=======================
Sweep the key distance / gap parameters to find their optimal values
for maximising pair returns (fractional R, DXY-exit approach).

Parameters swept
----------------
  1. GAP_REJ  attr_min_gap        : 25 – 600 pts (25-pt steps)
  2. REV      rev_proximity       : 25 – 500 pts (25-pt steps)
  3. REV      rev_min_move        : 25 – 600 pts (25-pt steps)
  4. LON_ATTR LONG  min_distance  : 200 – 2000 pts (25-pt steps)
  5. LON_ATTR SHORT min_distance  : 200 – 2000 pts (25-pt steps)

  LON_ATTR LONG and SHORT are swept independently so that LONG-only vs
  SHORT-only performance can be compared directly.

Method
------
  Single scan per rule type, collecting all signals with the actual measured
  value stored (gap_pts, dist_from_open, max_move). Each threshold simulation
  then filters the pool and computes pair returns without rescanning.
  Pair returns: fractional R, DXY-exit (pair exits at close when DXY hits TP/SL).
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules as r

PAIRS   = r.PAIRS
MIN_N   = 8    # minimum pair-trade count to report as meaningful

GAP_THRESHOLDS      = list(range(25,  625, 25))   # attr_min_gap sweep
PROX_THRESHOLDS     = list(range(25,  525, 25))   # rev_proximity sweep
MOVE_THRESHOLDS     = list(range(25,  625, 25))   # rev_min_move sweep
LON_ATTR_THRESHOLDS = list(range(200, 2025, 25))  # LON_ATTR distance sweep

# Current defaults (held fixed when sweeping the other parameter)
DEFAULT_PROX     = 150
DEFAULT_MOVE     = 100
DEFAULT_GAP      = 150
DEFAULT_LON_DIST = 1000

# ── Load data ────────────────────────────────────────────────────────────────
print("Loading data...")
df_dxy   = imp.load_merged('DXY')
df_dxy   = df_dxy.copy().reset_index(drop=True)
pair_dfs = {p: imp.load_merged(p) for p in PAIRS}
news_dates = r.load_news_filter()
months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"DXY: {len(df_dxy):,} bars  |  {months:.1f} months  |  pairs: {', '.join(PAIRS)}\n")

# ── Shared pre-computation ───────────────────────────────────────────────────
print("Computing BB regimes and candle signals...")
df_scan = df_dxy.copy().reset_index(drop=True)
df_scan['bb_1h'], _                      = imp.compute_bb_regime(df_scan, 1)
df_scan['bb_4h'], df_scan['bb_4h_flat']  = imp.compute_bb_regime(df_scan, 4)
bull_sig, bear_sig = imp.candle_signals_v2(df_scan)

df_scan['_date'] = df_scan['time'].dt.date
day_grp = df_scan.groupby('_date').agg(day_h=('high','max'), day_l=('low','min'))
print("Done.\n")


# ════════════════════════════════════════════════════════════════════════════
# SCAN 1: GAP_REJ  (attr_gap_touched=True, min_gap=0 to collect all)
# ════════════════════════════════════════════════════════════════════════════
print("Scanning GAP_REJ signals (min_gap=0)...")

london_open_price  = np.nan
prev_session_range = 0.0
attr_gap_pts       = 0.0
attr_gap_target    = np.nan
attr_gap_touched   = False
attr_traded        = False

gap_sigs = []

for i in range(2, len(df_scan)):
    row = df_scan.iloc[i]
    c, o = row['close'], row['open']
    ts = row['time']
    hour, minute = ts.hour, ts.minute
    curr_min = hour * 60 + minute
    dow = ts.dayofweek
    in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

    is_london_open = (not in_japan and hour == imp.LON_OPEN_HOUR
                      and minute == imp.LON_OPEN_MINUTE and dow != 0)
    is_monday_open = (not in_japan and hour == imp.MON_OPEN_HOUR
                      and minute == imp.MON_OPEN_MINUTE and dow == 0)
    is_tokyo_open  = (hour == 23 and minute == 45)

    if is_tokyo_open:
        attr_traded      = False
        attr_gap_touched = False
        ref_close = None
        for back, exp_offset in [(2, 30), (1, 15)]:
            if i >= back:
                cand = df_scan.iloc[i - back]
                if abs((cand['time'] - (ts - pd.Timedelta(minutes=exp_offset))
                        ).total_seconds()) <= 120:
                    ref_close = cand['close']
                    break
        if ref_close is not None:
            raw_gap = (o - ref_close) * 10000
            if abs(raw_gap) >= 10:
                attr_gap_pts    = raw_gap
                attr_gap_target = ref_close
            else:
                attr_gap_pts    = (c - o) * 10000
                attr_gap_target = o
        else:
            attr_gap_pts    = 0.0
            attr_gap_target = np.nan

    if is_london_open or is_monday_open:
        london_open_price = o
        today_dt   = ts.date()
        prior_days = [d for d in day_grp.index if d < today_dt]
        if prior_days:
            prev_dt            = max(prior_days)
            prev_h             = float(day_grp.at[prev_dt, 'day_h'])
            prev_l             = float(day_grp.at[prev_dt, 'day_l'])
            prev_session_range = (prev_h - prev_l) * 10000
        else:
            prev_session_range = 0.0

    if np.isnan(london_open_price):
        continue

    if not np.isnan(attr_gap_target):
        if attr_gap_pts < 0 and c >= attr_gap_target:
            attr_gap_touched = True
        elif attr_gap_pts > 0 and c <= attr_gap_target:
            attr_gap_touched = True

    mon_start    = imp.MON_OPEN_HOUR * 60 + imp.MON_OPEN_MINUTE
    attr_start   = mon_start if dow == 0 else (imp.ATTR_START_HOUR * 60 + imp.ATTR_START_MIN)
    in_attr_sess = attr_start <= curr_min <= imp.ATTR_WINDOW_END and not in_japan

    if not (not attr_traded and in_attr_sess
            and not np.isnan(attr_gap_target)
            and attr_gap_touched
            and prev_session_range <= imp.ATTR_MAX_PREV_RANGE
            and int(row['bb_4h_flat']) == 1):
        continue

    if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
        continue

    gap_abs = abs(attr_gap_pts)
    bb_1h   = int(row['bb_1h'])
    bb_4h   = int(row['bb_4h'])

    if attr_gap_pts < 0 and c < attr_gap_target and bull_sig.at[i]:
        reward_pts = (attr_gap_target - c) * 10000
        if reward_pts >= imp.ATTR_MIN_REWARD:
            tp = attr_gap_target - imp.ATTR_NEAR_BUFFER / 10000
            sl_d = tp - c
            if sl_d > 0:
                out, epx, ebar = r.resolve(df_scan, i, c, tp, c - sl_d, 'long')
                gap_sigs.append({
                    'type': 'GAP_REJ_LONG', 'entry_time': str(ts),
                    'entry': round(c,5), 'tp': round(tp,5), 'sl': round(c-sl_d,5),
                    'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                    'london_open': round(london_open_price,5),
                    'pristine': False, 'outcome': out,
                    'exit_px': round(epx,5), 'exit_time': str(df_scan.at[ebar,'time']),
                    'bias_1h': bb_1h, 'bias_4h': bb_4h,
                    'gap_pts': round(gap_abs),
                })
                attr_traded = True
                continue

    if attr_gap_pts > 0 and c > attr_gap_target and bear_sig.at[i]:
        reward_pts = (c - attr_gap_target) * 10000
        if reward_pts >= imp.ATTR_MIN_REWARD:
            tp = attr_gap_target + imp.ATTR_NEAR_BUFFER / 10000
            sl_d = c - tp
            if sl_d > 0:
                out, epx, ebar = r.resolve(df_scan, i, c, tp, c + sl_d, 'short')
                gap_sigs.append({
                    'type': 'GAP_REJ_SHORT', 'entry_time': str(ts),
                    'entry': round(c,5), 'tp': round(tp,5), 'sl': round(c+sl_d,5),
                    'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                    'london_open': round(london_open_price,5),
                    'pristine': False, 'outcome': out,
                    'exit_px': round(epx,5), 'exit_time': str(df_scan.at[ebar,'time']),
                    'bias_1h': bb_1h, 'bias_4h': bb_4h,
                    'gap_pts': round(gap_abs),
                })
                attr_traded = True

n_gl = sum(1 for s in gap_sigs if s['type']=='GAP_REJ_LONG')
n_gs = sum(1 for s in gap_sigs if s['type']=='GAP_REJ_SHORT')
print(f"GAP_REJ: {n_gl} LONG + {n_gs} SHORT = {len(gap_sigs)} total  "
      f"(gap range: {min(s['gap_pts'] for s in gap_sigs):.0f}–"
      f"{max(s['gap_pts'] for s in gap_sigs):.0f} pts)\n")


# ════════════════════════════════════════════════════════════════════════════
# SCAN 2: REV  (proximity=9999, min_move=0 — collect all, store actuals)
# ════════════════════════════════════════════════════════════════════════════
print("Scanning REV signals (proximity=9999, min_move=0)...")

london_open_price = np.nan
prev_session_high = np.nan
prev_session_low  = np.nan
max_move_up       = 0.0
max_move_down     = 0.0

rev_sigs = []

for i in range(2, len(df_scan)):
    row = df_scan.iloc[i]
    c, o = row['close'], row['open']
    ts = row['time']
    hour, minute = ts.hour, ts.minute
    curr_min = hour * 60 + minute
    dow = ts.dayofweek
    in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

    is_london_open = (not in_japan and hour == imp.LON_OPEN_HOUR
                      and minute == imp.LON_OPEN_MINUTE and dow != 0)
    is_monday_open = (not in_japan and hour == imp.MON_OPEN_HOUR
                      and minute == imp.MON_OPEN_MINUTE and dow == 0)

    if is_london_open or is_monday_open:
        london_open_price = o
        max_move_up   = 0.0
        max_move_down = 0.0
        today_dt   = ts.date()
        prior_days = [d for d in day_grp.index if d < today_dt]
        if prior_days:
            prev_dt           = max(prior_days)
            prev_session_high = float(day_grp.at[prev_dt, 'day_h'])
            prev_session_low  = float(day_grp.at[prev_dt, 'day_l'])
        else:
            prev_session_high = np.nan
            prev_session_low  = np.nan

    if np.isnan(london_open_price):
        continue

    if not in_japan:
        above = (c - london_open_price) * 10000
        if above > 0:
            max_move_up   = max(max_move_up,    above)
        elif above < 0:
            max_move_down = max(max_move_down, -above)

    mon_start   = imp.MON_OPEN_HOUR * 60 + imp.MON_OPEN_MINUTE
    rev_start   = mon_start if dow == 0 else (imp.LON_OPEN_HOUR * 60)
    in_rev_sess = rev_start <= curr_min <= imp.REV_WINDOW_END and not in_japan

    if not (in_rev_sess and not np.isnan(prev_session_high)):
        continue

    bb_1h = int(row['bb_1h'])
    bb_4h = int(row['bb_4h'])
    dist_from_open = abs(c - london_open_price) * 10000

    # REV LONG: price moved DOWN, bull signal, BB 1H up
    if bull_sig.at[i] and bb_1h == 1 and max_move_down > 0:
        sl_price = imp.get_structural_sl(prev_session_low, prev_session_high, c, 'long')
        sl_d = c - sl_price
        if sl_d > 0:
            tp_price = c + sl_d
            out, epx, ebar = r.resolve(df_scan, i, c, tp_price, sl_price, 'long')
            rev_sigs.append({
                'type': 'REV_LONG', 'entry_time': str(ts),
                'entry': round(c,5), 'tp': round(tp_price,5), 'sl': round(sl_price,5),
                'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                'london_open': round(london_open_price,5),
                'pristine': False, 'outcome': out,
                'exit_px': round(epx,5), 'exit_time': str(df_scan.at[ebar,'time']),
                'bias_1h': bb_1h, 'bias_4h': bb_4h,
                'dist': round(dist_from_open),
                'max_move': round(max_move_down),
            })

    # REV SHORT: price moved UP, bear signal, BB 1H down
    if bear_sig.at[i] and bb_1h == -1 and max_move_up > 0:
        sl_price = imp.get_structural_sl(prev_session_low, prev_session_high, c, 'short')
        sl_d = sl_price - c
        if sl_d > 0:
            tp_price = c - sl_d
            out, epx, ebar = r.resolve(df_scan, i, c, tp_price, sl_price, 'short')
            rev_sigs.append({
                'type': 'REV_SHORT', 'entry_time': str(ts),
                'entry': round(c,5), 'tp': round(tp_price,5), 'sl': round(sl_price,5),
                'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                'london_open': round(london_open_price,5),
                'pristine': False, 'outcome': out,
                'exit_px': round(epx,5), 'exit_time': str(df_scan.at[ebar,'time']),
                'bias_1h': bb_1h, 'bias_4h': bb_4h,
                'dist': round(dist_from_open),
                'max_move': round(max_move_up),
            })

n_rl = sum(1 for s in rev_sigs if s['type']=='REV_LONG')
n_rs = sum(1 for s in rev_sigs if s['type']=='REV_SHORT')
print(f"REV: {n_rl} LONG + {n_rs} SHORT = {len(rev_sigs)} total  "
      f"(dist range: {min(s['dist'] for s in rev_sigs):.0f}–"
      f"{max(s['dist'] for s in rev_sigs):.0f} pts  |  "
      f"max_move range: {min(s['max_move'] for s in rev_sigs):.0f}–"
      f"{max(s['max_move'] for s in rev_sigs):.0f} pts)\n")


# ════════════════════════════════════════════════════════════════════════════
# SCAN 3: LON_ATTR  (distance=0 to collect all, store actual dist_from_open)
#   Pin bars only (lower wick >= 2x body for LONG, upper wick >= 2x body for SHORT).
#   Pristine zone: body of London open candle (zone_top / zone_bot).
#   TP = lon_zone_bot for both directions (near edge LONG, far edge SHORT).
#   One signal per session. No BB filter.
# ════════════════════════════════════════════════════════════════════════════
print("Scanning LON_ATTR signals (min_distance=0)...")

# Pin bar vectors (LON_ATTR uses pin bars only — engulfing and 3-bar excluded)
_body_sz    = (df_scan['close'] - df_scan['open']).abs()
_body_top   = df_scan[['open', 'close']].max(axis=1)
_body_bot   = df_scan[['open', 'close']].min(axis=1)
_upper_wick = df_scan['high'] - _body_top
_lower_wick = _body_bot - df_scan['low']
_cndl_range = df_scan['high'] - df_scan['low']
_PIN_MULT   = 2.0
_bull_pin   = (_lower_wick >= _body_sz * _PIN_MULT) & (_lower_wick >= _upper_wick * 1.5) & (_cndl_range > 0)
_bear_pin   = (_upper_wick >= _body_sz * _PIN_MULT) & (_upper_wick >= _lower_wick * 1.5) & (_cndl_range > 0)

LON_ATTR_END = 18 * 60    # 18:00 UTC — entry window closes

london_open_price  = np.nan
lon_zone_top       = np.nan
lon_zone_bot       = np.nan
lon_pristine_long  = True
lon_pristine_short = True
lon_attr_traded    = False

lon_sigs = []

for i in range(2, len(df_scan)):
    row = df_scan.iloc[i]
    c, o = row['close'], row['open']
    ts   = row['time']
    hour, minute = ts.hour, ts.minute
    curr_min = hour * 60 + minute
    dow = ts.dayofweek
    in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

    is_london_open = (not in_japan and hour == imp.LON_OPEN_HOUR
                      and minute == imp.LON_OPEN_MINUTE and dow != 0)
    is_monday_open = (not in_japan and hour == imp.MON_OPEN_HOUR
                      and minute == imp.MON_OPEN_MINUTE and dow == 0)
    is_london_open_bar = is_london_open or is_monday_open

    if is_london_open_bar:
        london_open_price  = o
        lon_zone_top       = max(o, c)
        lon_zone_bot       = min(o, c)
        lon_pristine_long  = True
        lon_pristine_short = True
        lon_attr_traded    = False
        continue   # skip entry on the London open bar itself

    if np.isnan(london_open_price):
        continue

    # Pristine zone violation tracking (Japan session candles excluded)
    if not in_japan and not np.isnan(lon_zone_top):
        if o >= lon_zone_top or c >= lon_zone_top:
            lon_pristine_long  = False
        if o <= lon_zone_bot or c <= lon_zone_bot:
            lon_pristine_short = False

    # Session window: strictly after London open until 18:00 UTC
    lon_start   = (imp.MON_OPEN_HOUR * 60 + imp.MON_OPEN_MINUTE if dow == 0
                   else imp.LON_OPEN_HOUR * 60 + imp.LON_OPEN_MINUTE)
    in_lon_sess = lon_start < curr_min <= LON_ATTR_END and not in_japan

    if not (in_lon_sess and not lon_attr_traded and not np.isnan(lon_zone_top)):
        continue

    bb_1h_i = int(row['bb_1h'])
    bb_4h_i = int(row['bb_4h'])

    # LON_ATTR LONG: price is BELOW London open, zone top pristine, bull pin bar
    dist_below = (london_open_price - c) * 10000   # positive when below open
    if dist_below > 0 and lon_pristine_long and _bull_pin.at[i]:
        sl_d = lon_zone_bot - c          # TP distance (zone_bot above entry)
        if sl_d > 0:
            tp  = lon_zone_bot
            sl  = c - sl_d
            out, epx, ebar = r.resolve(df_scan, i, c, tp, sl, 'long')
            lon_sigs.append({
                'type': 'LON_ATTR_LONG', 'entry_time': str(ts),
                'entry': round(c,5), 'tp': round(tp,5), 'sl': round(sl,5),
                'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                'london_open': round(london_open_price,5),
                'lon_zone_top': round(lon_zone_top,5), 'lon_zone_bot': round(lon_zone_bot,5),
                'pristine': True, 'outcome': out, 'exit_px': round(epx,5),
                'exit_time': str(df_scan.at[ebar,'time']),
                'bias_1h': bb_1h_i, 'bias_4h': bb_4h_i,
                'dist': round(dist_below),
            })
            lon_attr_traded = True
            continue

    # LON_ATTR SHORT: price is ABOVE London open, zone bot pristine, bear pin bar
    dist_above = (c - london_open_price) * 10000   # positive when above open
    if dist_above > 0 and lon_pristine_short and _bear_pin.at[i]:
        sl_d = c - lon_zone_bot          # TP distance (zone_bot below entry)
        if sl_d > 0:
            tp  = lon_zone_bot
            sl  = c + sl_d
            out, epx, ebar = r.resolve(df_scan, i, c, tp, sl, 'short')
            lon_sigs.append({
                'type': 'LON_ATTR_SHORT', 'entry_time': str(ts),
                'entry': round(c,5), 'tp': round(tp,5), 'sl': round(sl,5),
                'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                'london_open': round(london_open_price,5),
                'lon_zone_top': round(lon_zone_top,5), 'lon_zone_bot': round(lon_zone_bot,5),
                'pristine': True, 'outcome': out, 'exit_px': round(epx,5),
                'exit_time': str(df_scan.at[ebar,'time']),
                'bias_1h': bb_1h_i, 'bias_4h': bb_4h_i,
                'dist': round(dist_above),
            })
            lon_attr_traded = True

n_ll = sum(1 for s in lon_sigs if s['type']=='LON_ATTR_LONG')
n_ls = sum(1 for s in lon_sigs if s['type']=='LON_ATTR_SHORT')
if lon_sigs:
    print(f"LON_ATTR: {n_ll} LONG + {n_ls} SHORT = {len(lon_sigs)} total  "
          f"(dist range: {min(s['dist'] for s in lon_sigs):.0f}–"
          f"{max(s['dist'] for s in lon_sigs):.0f} pts)\n")
else:
    print("LON_ATTR: 0 signals found\n")


# ════════════════════════════════════════════════════════════════════════════
# PAIR RETURN HELPER
# ════════════════════════════════════════════════════════════════════════════
def pair_net_r(sig_list):
    """Total fractional pair R for a list of DXY signals."""
    if not sig_list:
        return 0, float('nan'), float('nan'), 0, 0
    total = []
    for pair in PAIRS:
        total.extend(r.apply_to_pair_dxy_exit(sig_list, pair_dfs[pair], pair))
    valid  = [t for t in total if t['outcome'] != 'even']
    n      = len(valid)
    if n == 0:
        return len(sig_list), float('nan'), float('nan'), 0, 0
    net    = round(sum(t['r_actual'] for t in valid), 1)
    wins   = sum(1 for t in valid if t['outcome'] == 'win')
    losses = sum(1 for t in valid if t['outcome'] == 'loss')
    wr     = wins / n * 100
    return len(sig_list), net, wr, n, wins


# ════════════════════════════════════════════════════════════════════════════
# HTF BIAS HELPER
# ════════════════════════════════════════════════════════════════════════════
def htf_breakdown(sig_list, label, direction, show_4h=True, show_1h=True):
    """
    Print pair net R split by 1H and 4H BB regime.
    direction : 'long' or 'short' — determines which bb value is 'with-trend'.
    show_1h/4h: set False when that dimension is already pre-filtered
                (e.g. REV already requires bb_1h direction -> show_1h=False).
    """
    if not sig_list:
        print(f"  {label}: No signals\n")
        return
    with_val = +1 if direction == 'long' else -1
    labels = {
        with_val:   "with-trend     ",
       -with_val:   "counter-trend  ",
        0:          "flat (neutral) ",
    }
    print(f"  {label}  (total DXY signals: {len(sig_list)})")
    print(f"  {'Regime':<24}  {'DXYsig':>7}  {'Pair-N':>7}  {'W':>5}  {'WR%':>7}  {'Net R':>8}")
    print(f"  {'-'*65}")
    if show_1h:
        for bv, bl in labels.items():
            sub = [s for s in sig_list if s['bias_1h'] == bv]
            if not sub:
                continue
            ns, net, wr, n, w = pair_net_r(sub)
            wr_s = f"{wr:.1f}%" if not np.isnan(wr) else "   n/a"
            nr   = net if not np.isnan(net) else 0.0
            print(f"  1H {bl}  {ns:>7}  {n:>7}  {w:>5}  {wr_s:>7}  {nr:>+8.1f}R")
        print()
    if show_4h:
        for bv, bl in labels.items():
            sub = [s for s in sig_list if s['bias_4h'] == bv]
            if not sub:
                continue
            ns, net, wr, n, w = pair_net_r(sub)
            wr_s = f"{wr:.1f}%" if not np.isnan(wr) else "   n/a"
            nr   = net if not np.isnan(net) else 0.0
            print(f"  4H {bl}  {ns:>7}  {n:>7}  {w:>5}  {wr_s:>7}  {nr:>+8.1f}R")
        print()


# ════════════════════════════════════════════════════════════════════════════
# SWEEP TABLE PRINTER
# ════════════════════════════════════════════════════════════════════════════
def sweep_table(rows, title, param_label, default_val):
    df = pd.DataFrame(rows)
    print("=" * 84)
    print(f"  {title}")
    print(f"  Current default: {default_val} pts")
    print("=" * 84)
    print(f"  {param_label:>8}  {'DXYsig':>7}  {'Pair-N':>7}  {'W':>5}  "
          f"{'WR%':>7}  {'Net R':>8}  bar")
    print(f"  {'-'*70}")
    for _, row in df.iterrows():
        wr_s  = f"{row['wr']:.1f}%" if not np.isnan(row['wr']) else "   n/a"
        nr    = row['net'] if not np.isnan(row['net']) else 0
        bar   = "#" * max(0, int(nr))
        flags = []
        if row['thresh'] == default_val:
            flags.append("current")
        if row['n'] >= MIN_N and not np.isnan(row['net']):
            valid_rows = df[df['n'] >= MIN_N]
            if not valid_rows['net'].isna().all() and row['net'] == valid_rows['net'].max():
                flags.append("BEST NET R")
            if not valid_rows['wr'].isna().all() and row['wr'] == valid_rows['wr'].max():
                flags.append("BEST WR")
        flag_s = "  << " + " | ".join(flags) if flags else ""
        print(f"  {row['thresh']:>8}  {row['sigs']:>7}  {row['n']:>7}  "
              f"{row['wins']:>5}  {wr_s:>7}  {nr:>+7.1f}R  {bar}{flag_s}")

    valid = df[df['n'] >= MIN_N]
    if not valid.empty and not valid['net'].isna().all():
        best_net = valid.loc[valid['net'].idxmax()]
        best_wr  = valid.loc[valid['wr'].idxmax()]
        print()
        print(f"  >> Best Net R (N>={MIN_N}): {best_net['thresh']:.0f} pts  ->  "
              f"{best_net['net']:+.1f}R  |  {best_net['wr']:.1f}% WR  |  N={best_net['n']:.0f}")
        if best_wr['thresh'] != best_net['thresh']:
            print(f"  >> Best WR    (N>={MIN_N}): {best_wr['thresh']:.0f} pts  ->  "
                  f"{best_wr['net']:+.1f}R  |  {best_wr['wr']:.1f}% WR  |  N={best_wr['n']:.0f}")
    print()


# ════════════════════════════════════════════════════════════════════════════
# SWEEP 1: GAP_REJ  attr_min_gap
# ════════════════════════════════════════════════════════════════════════════
print("Sweeping GAP_REJ attr_min_gap...")
gap_rows = []
for t in GAP_THRESHOLDS:
    sub = [s for s in gap_sigs if s['gap_pts'] >= t]
    ns, net, wr, n, w = pair_net_r(sub)
    gap_rows.append({'thresh': t, 'sigs': ns, 'net': net, 'wr': wr, 'n': n, 'wins': w})

sweep_table(gap_rows, "GAP_REJ — attr_min_gap sweep (pair returns, DXY-exit)",
            "MinGap", DEFAULT_GAP)

# ════════════════════════════════════════════════════════════════════════════
# SWEEP 2: REV  rev_proximity  (min_move held at DEFAULT_MOVE)
# ════════════════════════════════════════════════════════════════════════════
print("Sweeping REV rev_proximity (min_move fixed at %d pts)..." % DEFAULT_MOVE)
prox_rows = []
for t in PROX_THRESHOLDS:
    sub = [s for s in rev_sigs
           if s['dist'] <= t and s['max_move'] >= DEFAULT_MOVE]
    ns, net, wr, n, w = pair_net_r(sub)
    prox_rows.append({'thresh': t, 'sigs': ns, 'net': net, 'wr': wr, 'n': n, 'wins': w})

sweep_table(prox_rows,
            f"REV — rev_proximity sweep (min_move fixed at {DEFAULT_MOVE} pts, pair returns)",
            "MaxDist", DEFAULT_PROX)

# ════════════════════════════════════════════════════════════════════════════
# SWEEP 3: REV  rev_min_move  (proximity held at DEFAULT_PROX)
# ════════════════════════════════════════════════════════════════════════════
print("Sweeping REV rev_min_move (proximity fixed at %d pts)..." % DEFAULT_PROX)
move_rows = []
for t in MOVE_THRESHOLDS:
    sub = [s for s in rev_sigs
           if s['max_move'] >= t and s['dist'] <= DEFAULT_PROX]
    ns, net, wr, n, w = pair_net_r(sub)
    move_rows.append({'thresh': t, 'sigs': ns, 'net': net, 'wr': wr, 'n': n, 'wins': w})

sweep_table(move_rows,
            f"REV — rev_min_move sweep (proximity fixed at {DEFAULT_PROX} pts, pair returns)",
            "MinMove", DEFAULT_MOVE)


# ════════════════════════════════════════════════════════════════════════════
# SWEEP 4: LON_ATTR LONG  min_distance
# ════════════════════════════════════════════════════════════════════════════
print("Sweeping LON_ATTR LONG min_distance...")
lon_long_rows = []
for t in LON_ATTR_THRESHOLDS:
    sub = [s for s in lon_sigs if s['type'] == 'LON_ATTR_LONG' and s['dist'] >= t]
    ns, net, wr, n, w = pair_net_r(sub)
    lon_long_rows.append({'thresh': t, 'sigs': ns, 'net': net, 'wr': wr, 'n': n, 'wins': w})

sweep_table(lon_long_rows,
            "LON_ATTR LONG — min_distance sweep (pair returns, DXY-exit)",
            "MinDist", DEFAULT_LON_DIST)

# ════════════════════════════════════════════════════════════════════════════
# SWEEP 5: LON_ATTR SHORT  min_distance
# ════════════════════════════════════════════════════════════════════════════
print("Sweeping LON_ATTR SHORT min_distance...")
lon_short_rows = []
for t in LON_ATTR_THRESHOLDS:
    sub = [s for s in lon_sigs if s['type'] == 'LON_ATTR_SHORT' and s['dist'] >= t]
    ns, net, wr, n, w = pair_net_r(sub)
    lon_short_rows.append({'thresh': t, 'sigs': ns, 'net': net, 'wr': wr, 'n': n, 'wins': w})

sweep_table(lon_short_rows,
            "LON_ATTR SHORT — min_distance sweep (pair returns, DXY-exit)",
            "MinDist", DEFAULT_LON_DIST)


# ════════════════════════════════════════════════════════════════════════════
# PER-PAIR BREAKDOWN AT KEY THRESHOLDS
# ════════════════════════════════════════════════════════════════════════════
def pair_breakdown(sig_list, label):
    if not sig_list:
        print(f"  {label}: No signals\n")
        return
    print(f"  {label}  (DXY signals: {len(sig_list)})")
    for pair in PAIRS:
        trades = r.apply_to_pair_dxy_exit(sig_list, pair_dfs[pair], pair)
        valid  = [t for t in trades if t['outcome'] != 'even']
        n = len(valid)
        if n == 0:
            print(f"    {pair:<8}: no pair data")
            continue
        net  = sum(t['r_actual'] for t in valid)
        wins = sum(1 for t in valid if t['outcome'] == 'win')
        loss = sum(1 for t in valid if t['outcome'] == 'loss')
        wr   = wins / n * 100
        print(f"    {pair:<8}: N={n:3d}  W={wins:3d} L={loss:3d}  "
              f"WR={wr:5.1f}%  Net={net:+6.1f}R")
    print()

# Find best thresholds
def best_thresh(rows, key='net'):
    df = pd.DataFrame(rows)
    valid = df[df['n'] >= MIN_N]
    if valid.empty or valid[key].isna().all():
        return None
    return int(valid.loc[valid[key].idxmax(), 'thresh'])

best_gap       = best_thresh(gap_rows,       'net')
best_prox      = best_thresh(prox_rows,      'net')
best_move      = best_thresh(move_rows,      'net')
best_lon_long  = best_thresh(lon_long_rows,  'net')
best_lon_short = best_thresh(lon_short_rows, 'net')

print("=" * 84)
print("  PER-PAIR BREAKDOWN AT KEY THRESHOLDS")
print("=" * 84)
print()

# GAP_REJ — pair breakdown then HTF bias (4H flat already guaranteed by filter; check 1H only)
pair_breakdown([s for s in gap_sigs if s['gap_pts'] >= DEFAULT_GAP],
               f"GAP_REJ  — current default {DEFAULT_GAP} pts")
if best_gap and best_gap != DEFAULT_GAP:
    pair_breakdown([s for s in gap_sigs if s['gap_pts'] >= best_gap],
                   f"GAP_REJ  — optimal {best_gap} pts")

print("=" * 84)
print("  GAP_REJ — HTF BIAS (1H only; 4H flat is a pre-condition so all have bb_4h=0)")
print("  Note: GAP_REJ LONG = bullish signal after gap fills down -> with-trend = 1H up")
print("        GAP_REJ SHORT = bearish signal after gap fills up  -> with-trend = 1H down")
print("=" * 84)
print()
gap_long_sigs  = [s for s in gap_sigs if s['type'] == 'GAP_REJ_LONG'  and s['gap_pts'] >= DEFAULT_GAP]
gap_short_sigs = [s for s in gap_sigs if s['type'] == 'GAP_REJ_SHORT' and s['gap_pts'] >= DEFAULT_GAP]
htf_breakdown(gap_long_sigs,  f"GAP_REJ LONG  — gap >= {DEFAULT_GAP} pts", 'long',  show_4h=False)
htf_breakdown(gap_short_sigs, f"GAP_REJ SHORT — gap >= {DEFAULT_GAP} pts", 'short', show_4h=False)

# REV proximity
pair_breakdown([s for s in rev_sigs if s['dist'] <= DEFAULT_PROX and s['max_move'] >= DEFAULT_MOVE],
               f"REV prox — current default {DEFAULT_PROX} pts (min_move={DEFAULT_MOVE})")
if best_prox and best_prox != DEFAULT_PROX:
    pair_breakdown([s for s in rev_sigs if s['dist'] <= best_prox and s['max_move'] >= DEFAULT_MOVE],
                   f"REV prox — optimal {best_prox} pts (min_move={DEFAULT_MOVE})")

# REV min_move
pair_breakdown([s for s in rev_sigs if s['max_move'] >= DEFAULT_MOVE and s['dist'] <= DEFAULT_PROX],
               f"REV move — current default {DEFAULT_MOVE} pts (prox={DEFAULT_PROX})")
if best_move and best_move != DEFAULT_MOVE:
    pair_breakdown([s for s in rev_sigs if s['max_move'] >= best_move and s['dist'] <= DEFAULT_PROX],
                   f"REV move — optimal {best_move} pts (prox={DEFAULT_PROX})")

print("=" * 84)
print("  REV — HTF BIAS (4H only; 1H direction is a pre-condition so all have matching bb_1h)")
print("  Note: REV already requires bb_1h expanding in trade direction.")
print("        Here we check whether 4H alignment improves results further.")
print("        REV LONG  -> with-trend 4H = bb_4h +1 (4H also up)")
print("        REV SHORT -> with-trend 4H = bb_4h -1 (4H also down)")
print("=" * 84)
print()
rev_long_base  = [s for s in rev_sigs if s['type'] == 'REV_LONG'  and s['dist'] <= DEFAULT_PROX and s['max_move'] >= DEFAULT_MOVE]
rev_short_base = [s for s in rev_sigs if s['type'] == 'REV_SHORT' and s['dist'] <= DEFAULT_PROX and s['max_move'] >= DEFAULT_MOVE]
htf_breakdown(rev_long_base,  f"REV LONG  — prox<={DEFAULT_PROX}, move>={DEFAULT_MOVE}", 'long',  show_1h=False)
htf_breakdown(rev_short_base, f"REV SHORT — prox<={DEFAULT_PROX}, move>={DEFAULT_MOVE}", 'short', show_1h=False)
# Also at optimised params
if best_prox and best_move and (best_prox, best_move) != (DEFAULT_PROX, DEFAULT_MOVE):
    rev_long_opt  = [s for s in rev_sigs if s['type'] == 'REV_LONG'  and s['dist'] <= best_prox and s['max_move'] >= best_move]
    rev_short_opt = [s for s in rev_sigs if s['type'] == 'REV_SHORT' and s['dist'] <= best_prox and s['max_move'] >= best_move]
    htf_breakdown(rev_long_opt,  f"REV LONG  — optimal prox<={best_prox}, move>={best_move}", 'long',  show_1h=False)
    htf_breakdown(rev_short_opt, f"REV SHORT — optimal prox<={best_prox}, move>={best_move}", 'short', show_1h=False)


# ════════════════════════════════════════════════════════════════════════════
# LON_ATTR DIRECTION COMPARISON
# ════════════════════════════════════════════════════════════════════════════
print("=" * 84)
print("  LON_ATTR — LONG vs SHORT DIRECTION COMPARISON")
print("=" * 84)
print()

pair_breakdown([s for s in lon_sigs if s['type'] == 'LON_ATTR_LONG'  and s['dist'] >= DEFAULT_LON_DIST],
               f"LON_ATTR LONG only  — dist >= {DEFAULT_LON_DIST} pts")
pair_breakdown([s for s in lon_sigs if s['type'] == 'LON_ATTR_SHORT' and s['dist'] >= DEFAULT_LON_DIST],
               f"LON_ATTR SHORT only — dist >= {DEFAULT_LON_DIST} pts")
pair_breakdown([s for s in lon_sigs if s['dist'] >= DEFAULT_LON_DIST],
               f"LON_ATTR COMBINED   — dist >= {DEFAULT_LON_DIST} pts")

if best_lon_long and best_lon_long != DEFAULT_LON_DIST:
    pair_breakdown([s for s in lon_sigs if s['type'] == 'LON_ATTR_LONG' and s['dist'] >= best_lon_long],
                   f"LON_ATTR LONG only  — optimal dist >= {best_lon_long} pts")
if best_lon_short and best_lon_short != DEFAULT_LON_DIST:
    pair_breakdown([s for s in lon_sigs if s['type'] == 'LON_ATTR_SHORT' and s['dist'] >= best_lon_short],
                   f"LON_ATTR SHORT only — optimal dist >= {best_lon_short} pts")


# ════════════════════════════════════════════════════════════════════════════
# LON_ATTR HTF BIAS ANALYSIS
#   For LONG: bb_1h == +1 = 1H expanding slope UP   (with-trend for a LONG)
#             bb_1h == -1 = 1H expanding slope DOWN  (counter-trend for a LONG)
#             bb_1h ==  0 = 1H flat                  (neutral)
#   For SHORT: bb_1h == -1 = with-trend, bb_1h == +1 = counter-trend
#   Same logic applied to 4H bb.
# ════════════════════════════════════════════════════════════════════════════
print("=" * 84)
print("  LON_ATTR — HTF BIAS BREAKDOWN (with-trend vs counter-trend)")
print("=" * 84)
print()

lon_long_1000  = [s for s in lon_sigs if s['type'] == 'LON_ATTR_LONG'  and s['dist'] >= DEFAULT_LON_DIST]
lon_short_1000 = [s for s in lon_sigs if s['type'] == 'LON_ATTR_SHORT' and s['dist'] >= DEFAULT_LON_DIST]

htf_breakdown(lon_long_1000,  f"LON_ATTR LONG  — dist >= {DEFAULT_LON_DIST} pts", 'long')
htf_breakdown(lon_short_1000, f"LON_ATTR SHORT — dist >= {DEFAULT_LON_DIST} pts", 'short')


# ════════════════════════════════════════════════════════════════════════════
# COMBINED PORTFOLIO — current defaults vs LON_ATTR long-only variant
# ════════════════════════════════════════════════════════════════════════════
print("=" * 84)
print("  COMBINED PORTFOLIO — current defaults vs optimal thresholds")
print("=" * 84)
print()

# Full portfolio: GAP_REJ + REV + LON_ATTR combined (both directions)
default_sigs = (
    [s for s in gap_sigs if s['gap_pts'] >= DEFAULT_GAP] +
    [s for s in rev_sigs if s['dist'] <= DEFAULT_PROX and s['max_move'] >= DEFAULT_MOVE] +
    [s for s in lon_sigs if s['dist'] >= DEFAULT_LON_DIST]
)
pair_breakdown(default_sigs,
               f"ALL TRADES (both LON_ATTR dirs)  gap={DEFAULT_GAP}  prox={DEFAULT_PROX}  "
               f"move={DEFAULT_MOVE}  lon={DEFAULT_LON_DIST}")

# LON_ATTR LONG-only variant
long_only_sigs = (
    [s for s in gap_sigs if s['gap_pts'] >= DEFAULT_GAP] +
    [s for s in rev_sigs if s['dist'] <= DEFAULT_PROX and s['max_move'] >= DEFAULT_MOVE] +
    [s for s in lon_sigs if s['type'] == 'LON_ATTR_LONG' and s['dist'] >= DEFAULT_LON_DIST]
)
pair_breakdown(long_only_sigs,
               f"LON_ATTR LONG ONLY               gap={DEFAULT_GAP}  prox={DEFAULT_PROX}  "
               f"move={DEFAULT_MOVE}  lon={DEFAULT_LON_DIST}")

# Optimal params (best sweep thresholds for each)
bg  = best_gap       or DEFAULT_GAP
bp  = best_prox      or DEFAULT_PROX
bm  = best_move      or DEFAULT_MOVE
bll = best_lon_long  or DEFAULT_LON_DIST
if (bg, bp, bm, bll) != (DEFAULT_GAP, DEFAULT_PROX, DEFAULT_MOVE, DEFAULT_LON_DIST):
    optimal_long_only = (
        [s for s in gap_sigs if s['gap_pts'] >= bg] +
        [s for s in rev_sigs if s['dist'] <= bp and s['max_move'] >= bm] +
        [s for s in lon_sigs if s['type'] == 'LON_ATTR_LONG' and s['dist'] >= bll]
    )
    pair_breakdown(optimal_long_only,
                   f"OPTIMAL (LON_ATTR LONG only)     gap={bg}  prox={bp}  move={bm}  lon={bll}")
