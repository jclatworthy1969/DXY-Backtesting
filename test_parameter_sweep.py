"""
test_parameter_sweep.py
=======================
Sweep the three key distance / gap parameters to find their optimal values
for maximising pair returns (fractional R, DXY-exit approach).

Parameters swept
----------------
  1. GAP_REJ  attr_min_gap   : 25 – 600 pts (25-pt steps)
       Minimum gap size at Tokyo open required for a GAP_REJ signal to fire.
       Current default: 150 pts.

  2. REV      rev_proximity  : 25 – 500 pts (25-pt steps)
       Maximum distance (pts) between close and London open price at entry.
       Lower = price must be very close to open. Current default: 150 pts.
       Held constant during min_move sweep (at 150 pts).

  3. REV      rev_min_move   : 25 – 600 pts (25-pt steps)
       Minimum pts price must have moved away from London open before a
       reversal is valid. Higher = cleaner reversals.
       Current default: 100 pts.
       Held constant during proximity sweep (at 100 pts).

LON_ATTR (already swept to 1000 pts) is excluded here.

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

GAP_THRESHOLDS   = list(range(25, 625, 25))   # attr_min_gap sweep
PROX_THRESHOLDS  = list(range(25, 525, 25))   # rev_proximity sweep
MOVE_THRESHOLDS  = list(range(25, 625, 25))   # rev_min_move sweep

# Current defaults (held fixed when sweeping the other parameter)
DEFAULT_PROX = 150
DEFAULT_MOVE = 100
DEFAULT_GAP  = 150

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

best_gap  = best_thresh(gap_rows,  'net')
best_prox = best_thresh(prox_rows, 'net')
best_move = best_thresh(move_rows, 'net')

print("=" * 84)
print("  PER-PAIR BREAKDOWN AT KEY THRESHOLDS")
print("=" * 84)
print()

# GAP_REJ
pair_breakdown([s for s in gap_sigs if s['gap_pts'] >= DEFAULT_GAP],
               f"GAP_REJ  — current default {DEFAULT_GAP} pts")
if best_gap and best_gap != DEFAULT_GAP:
    pair_breakdown([s for s in gap_sigs if s['gap_pts'] >= best_gap],
                   f"GAP_REJ  — optimal {best_gap} pts")

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


# ════════════════════════════════════════════════════════════════════════════
# COMBINED: best params together
# ════════════════════════════════════════════════════════════════════════════
print("=" * 84)
print("  COMBINED PORTFOLIO — current defaults vs optimal thresholds")
print("=" * 84)
print()

default_sigs = (
    [s for s in gap_sigs if s['gap_pts'] >= DEFAULT_GAP] +
    [s for s in rev_sigs if s['dist'] <= DEFAULT_PROX and s['max_move'] >= DEFAULT_MOVE]
)
pair_breakdown(default_sigs,
               f"CURRENT DEFAULTS  gap={DEFAULT_GAP}  prox={DEFAULT_PROX}  move={DEFAULT_MOVE}")

bg  = best_gap  or DEFAULT_GAP
bp  = best_prox or DEFAULT_PROX
bm  = best_move or DEFAULT_MOVE
if (bg, bp, bm) != (DEFAULT_GAP, DEFAULT_PROX, DEFAULT_MOVE):
    optimal_sigs = (
        [s for s in gap_sigs if s['gap_pts'] >= bg] +
        [s for s in rev_sigs if s['dist'] <= bp and s['max_move'] >= bm]
    )
    pair_breakdown(optimal_sigs,
                   f"OPTIMAL PARAMS    gap={bg}  prox={bp}  move={bm}")
