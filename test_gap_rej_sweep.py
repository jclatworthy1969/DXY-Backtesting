"""
test_gap_rej_sweep.py
=====================
Sweep minimum gap threshold for GAP_REJ to optimise pair returns.

GAP_REJ fires when:
  - Tokyo session opened with a gap >= min_gap pts
  - Gap target has already been reached (attr_gap_touched = True)
  - 4H BB is flat, prior session range OK, confirmation candle in gap-fill direction
  - TP = gap target +/- near buffer (50 pts, near-edge mode)
  - SL = 1:1 mirror of TP distance

Method:
  Single scan with min_gap = 0, collecting all qualifying GAP_REJ signals with their
  actual gap size stored. For each threshold the simulation then filters to signals
  where gap_pts >= threshold (one signal per session, so no first-qualifying logic needed).
  LONG and SHORT swept together and independently.

Thresholds tested: 25 – 600 pts in 25-pt steps.
Current default:   150 pts.
"""
import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
import dxy_improved_rules as imp
import dxy_clean_rules as r

THRESHOLDS = list(range(25, 625, 25))
MIN_N      = 8      # minimum pair-trade count for meaningful results
PAIRS      = r.PAIRS

# ── Load ────────────────────────────────────────────────────────────────────
df_dxy = imp.load_merged('DXY')
df_dxy = df_dxy.copy().reset_index(drop=True)
pair_dfs = {p: imp.load_merged(p) for p in PAIRS}
news_dates = r.load_news_filter()
months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
print(f"DXY: {len(df_dxy):,} bars  |  {months:.1f} months")
print(f"Pairs: {', '.join(PAIRS)}\n")

# ── BB regime and candle signals ─────────────────────────────────────────────
print("Computing BB regimes and candle signals...")
df_scan = df_dxy.copy().reset_index(drop=True)
df_scan['bb_1h'], _                  = imp.compute_bb_regime(df_scan, 1)
df_scan['bb_4h'], df_scan['bb_4h_flat'] = imp.compute_bb_regime(df_scan, 4)
bull_sig, bear_sig = imp.candle_signals_v2(df_scan)

df_scan['_date'] = df_scan['time'].dt.date
day_grp = df_scan.groupby('_date').agg(day_h=('high', 'max'), day_l=('low', 'min'))

# ── Single full scan — no gap threshold ─────────────────────────────────────
print("Running base scan (min_gap=0, collecting all GAP_REJ signals)...")

london_open_price  = np.nan
prev_session_range = 0.0
attr_gap_pts       = 0.0
attr_gap_target    = np.nan
attr_gap_touched   = False
attr_traded        = False

all_sigs = []

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

    # Tokyo open: reset session and measure gap
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

    # London open: record open price and prior session range
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

    # Gap latch
    if not np.isnan(attr_gap_target):
        if attr_gap_pts < 0 and c >= attr_gap_target:
            attr_gap_touched = True
        elif attr_gap_pts > 0 and c <= attr_gap_target:
            attr_gap_touched = True

    # Session window
    mon_start  = imp.MON_OPEN_HOUR * 60 + imp.MON_OPEN_MINUTE
    attr_start = mon_start if dow == 0 else (imp.ATTR_START_HOUR * 60 + imp.ATTR_START_MIN)
    in_attr_sess = attr_start <= curr_min <= imp.ATTR_WINDOW_END and not in_japan

    bb_4h_flat = int(row['bb_4h_flat'])
    bb_1h      = int(row['bb_1h'])
    bb_4h      = int(row['bb_4h'])

    # Base checks (no gap threshold — record all)
    if not (not attr_traded and in_attr_sess
            and not np.isnan(attr_gap_target)
            and attr_gap_touched                       # GAP_REJ: zone already filled
            and prev_session_range <= imp.ATTR_MAX_PREV_RANGE
            and bb_4h_flat == 1):
        continue

    if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
        continue

    gap_abs = abs(attr_gap_pts)

    # LONG: gap was down, price now below the target, bull signal
    if attr_gap_pts < 0 and c < attr_gap_target and bull_sig.at[i]:
        reward_pts = (attr_gap_target - c) * 10000
        if reward_pts >= imp.ATTR_MIN_REWARD:
            tp_price = attr_gap_target - imp.ATTR_NEAR_BUFFER / 10000
            sl_d = tp_price - c
            if sl_d > 0:
                sl_price = c - sl_d
                outcome, exit_px, exit_bar = r.resolve(df_scan, i, c, tp_price, sl_price, 'long')
                all_sigs.append({
                    'type': 'GAP_REJ_LONG', 'entry_time': str(ts),
                    'entry': round(c, 5), 'tp': round(tp_price, 5),
                    'sl': round(sl_price, 5),
                    'sl_pts': round(sl_d * 10000), 'tp_pts': round(sl_d * 10000),
                    'london_open': round(london_open_price, 5),
                    'pristine': False, 'outcome': outcome,
                    'exit_px': round(exit_px, 5),
                    'exit_time': str(df_scan.at[exit_bar, 'time']),
                    'bias_1h': bb_1h, 'bias_4h': bb_4h,
                    'gap_pts': round(gap_abs),
                })
                attr_traded = True
                continue

    # SHORT: gap was up, price now above the target, bear signal
    if attr_gap_pts > 0 and c > attr_gap_target and bear_sig.at[i]:
        reward_pts = (c - attr_gap_target) * 10000
        if reward_pts >= imp.ATTR_MIN_REWARD:
            tp_price = attr_gap_target + imp.ATTR_NEAR_BUFFER / 10000
            sl_d = c - tp_price
            if sl_d > 0:
                sl_price = c + sl_d
                outcome, exit_px, exit_bar = r.resolve(df_scan, i, c, tp_price, sl_price, 'short')
                all_sigs.append({
                    'type': 'GAP_REJ_SHORT', 'entry_time': str(ts),
                    'entry': round(c, 5), 'tp': round(tp_price, 5),
                    'sl': round(sl_price, 5),
                    'sl_pts': round(sl_d * 10000), 'tp_pts': round(sl_d * 10000),
                    'london_open': round(london_open_price, 5),
                    'pristine': False, 'outcome': outcome,
                    'exit_px': round(exit_px, 5),
                    'exit_time': str(df_scan.at[exit_bar, 'time']),
                    'bias_1h': bb_1h, 'bias_4h': bb_4h,
                    'gap_pts': round(gap_abs),
                })
                attr_traded = True

df_all = pd.DataFrame(all_sigs)
n_long  = (df_all['type'] == 'GAP_REJ_LONG').sum()  if len(df_all) else 0
n_short = (df_all['type'] == 'GAP_REJ_SHORT').sum() if len(df_all) else 0
print(f"Base scan: {n_long} LONG + {n_short} SHORT = {len(df_all)} total GAP_REJ signals")
if len(df_all):
    print(f"Gap size: min={df_all['gap_pts'].min():.0f}  "
          f"median={df_all['gap_pts'].median():.0f}  "
          f"max={df_all['gap_pts'].max():.0f}  pts\n")

if len(df_all) == 0:
    print("No signals found. Exiting.")
    sys.exit(0)


# ── Threshold simulation ─────────────────────────────────────────────────────
def sim_threshold(sig_list):
    """Compute pair returns (fractional R) for a list of signal dicts."""
    if not sig_list:
        return 0, float('nan'), float('nan'), 0, 0, 0
    all_pair_trades = []
    for pair in PAIRS:
        trades = r.apply_to_pair_dxy_exit(sig_list, pair_dfs[pair], pair)
        all_pair_trades.extend(trades)
    valid = [t for t in all_pair_trades if t['outcome'] != 'even']
    n     = len(valid)
    if n == 0:
        return len(sig_list), float('nan'), float('nan'), 0, 0, 0
    net_r  = round(sum(t['r_actual'] for t in valid), 1)
    wins   = sum(1 for t in valid if t['outcome'] == 'win')
    losses = sum(1 for t in valid if t['outcome'] == 'loss')
    wr     = wins / n * 100
    return len(sig_list), net_r, wr, n, wins, losses


# ── Build results tables ─────────────────────────────────────────────────────
rows_all   = []
rows_long  = []
rows_short = []

for t in THRESHOLDS:
    sub_all   = [s for s in all_sigs if s['gap_pts'] >= t]
    sub_long  = [s for s in sub_all  if s['type'] == 'GAP_REJ_LONG']
    sub_short = [s for s in sub_all  if s['type'] == 'GAP_REJ_SHORT']

    ns, nr, wr, n, w, l = sim_threshold(sub_all)
    rows_all.append({'thresh': t, 'sigs': ns, 'n': n, 'w': w, 'l': l, 'wr': wr, 'net': nr})

    ns, nr, wr, n, w, l = sim_threshold(sub_long)
    rows_long.append({'thresh': t, 'sigs': ns, 'n': n, 'w': w, 'l': l, 'wr': wr, 'net': nr})

    ns, nr, wr, n, w, l = sim_threshold(sub_short)
    rows_short.append({'thresh': t, 'sigs': ns, 'n': n, 'w': w, 'l': l, 'wr': wr, 'net': nr})

df_all_r   = pd.DataFrame(rows_all)
df_long_r  = pd.DataFrame(rows_long)
df_short_r = pd.DataFrame(rows_short)


# ── Print tables ─────────────────────────────────────────────────────────────
def print_table(df, title):
    print("=" * 82)
    print(f"  {title}")
    print("=" * 82)
    print(f"  {'MinGap':>7}  {'DXYSig':>7}  {'PairTrd':>8}  {'W':>5}  {'L':>5}  "
          f"{'WR%':>7}  {'NetR':>8}")
    print(f"  {'-'*68}")
    for _, row in df.iterrows():
        wr_s   = f"{row['wr']:.1f}%" if not np.isnan(row['wr']) else "    n/a"
        nr_s   = f"{row['net']:+.1f}R" if not np.isnan(row['net']) else "    n/a"
        flag   = ""
        if row['n'] >= MIN_N and not np.isnan(row['wr']):
            if row['wr'] >= 55:
                flag = " <<"
            if not np.isnan(row['net']) and row['net'] == df[df['n'] >= MIN_N]['net'].max():
                flag = " ** BEST NET R"
        print(f"  {row['thresh']:>7}  {row['sigs']:>7}  {row['n']:>8}  "
              f"{row['w']:>5}  {row['l']:>5}  {wr_s:>7}  {nr_s:>8}{flag}")
    valid = df[df['n'] >= MIN_N].copy()
    if not valid.empty:
        best_wr  = valid.loc[valid['wr'].idxmax()]
        best_net = valid.loc[valid['net'].idxmax()] if not valid['net'].isna().all() else None
        print()
        print(f"  >> Best WR   (N>={MIN_N}): {best_wr['thresh']:.0f} pts  ->  "
              f"{best_wr['wr']:.1f}% WR  |  Net {best_wr['net']:+.1f}R  |  N={best_wr['n']:.0f}")
        if best_net is not None and best_net['thresh'] != best_wr['thresh']:
            print(f"  >> Best NetR (N>={MIN_N}): {best_net['thresh']:.0f} pts  ->  "
                  f"{best_net['wr']:.1f}% WR  |  Net {best_net['net']:+.1f}R  |  N={best_net['n']:.0f}")
    print()


print_table(df_all_r,   "ALL (LONG + SHORT)  — pair returns, fractional R, DXY-exit")
print_table(df_long_r,  "LONG only           — gap down, price pulled back below target")
print_table(df_short_r, "SHORT only          — gap up, price pulled back above target")


# ── Per-pair breakdown at current default (150 pts) and best threshold ───────
def pair_breakdown(sig_list, label):
    print(f"  {label}  (N signals: {len(sig_list)})")
    if not sig_list:
        print("    No signals.\n")
        return
    for pair in PAIRS:
        trades = r.apply_to_pair_dxy_exit(sig_list, pair_dfs[pair], pair)
        valid  = [t for t in trades if t['outcome'] != 'even']
        n = len(valid)
        if n == 0:
            print(f"    {pair:<8}: no pair data")
            continue
        net_r = sum(t['r_actual'] for t in valid)
        wins  = sum(1 for t in valid if t['outcome'] == 'win')
        losses= sum(1 for t in valid if t['outcome'] == 'loss')
        wr    = wins / n * 100 if n > 0 else float('nan')
        print(f"    {pair:<8}: N={n:3d}  W={wins:3d} L={losses:3d}  "
              f"WR={wr:5.1f}%  Net={net_r:+6.1f}R")
    print()

# Find best all-combined threshold by net R
valid_all = df_all_r[df_all_r['n'] >= MIN_N]
best_thresh_net = int(valid_all.loc[valid_all['net'].idxmax(), 'thresh']) if not valid_all.empty else 150
best_thresh_wr  = int(valid_all.loc[valid_all['wr'].idxmax(),  'thresh']) if not valid_all.empty else 150

print("=" * 82)
print("  PER-PAIR BREAKDOWN AT KEY THRESHOLDS")
print("=" * 82)
print()

pair_breakdown([s for s in all_sigs if s['gap_pts'] >= 150],
               f"Current default: 150 pts  (all directions)")
if best_thresh_net != 150:
    pair_breakdown([s for s in all_sigs if s['gap_pts'] >= best_thresh_net],
                   f"Best Net R:      {best_thresh_net} pts  (all directions)")
if best_thresh_wr != 150 and best_thresh_wr != best_thresh_net:
    pair_breakdown([s for s in all_sigs if s['gap_pts'] >= best_thresh_wr],
                   f"Best WR:         {best_thresh_wr} pts  (all directions)")


# ── Focus range ─────────────────────────────────────────────────────────────
print("=" * 82)
print("  FOCUS: 50 – 400 pts (combined, visualised)")
print("=" * 82)
print(f"  {'Gap':>5}   {'Sigs':>5}  {'Pair-N':>7}  {'WR%':>7}  {'NetR':>8}  bar")
print(f"  {'-'*60}")
focus = df_all_r[(df_all_r['thresh'] >= 50) & (df_all_r['thresh'] <= 400)]
for _, row in focus.iterrows():
    wr_s = f"{row['wr']:.1f}%" if not np.isnan(row['wr']) else "  n/a"
    nr   = row['net'] if not np.isnan(row['net']) else 0
    bar  = "#" * max(0, int(nr))
    mark = "<-- current" if row['thresh'] == 150 else ""
    print(f"  {row['thresh']:>5}   {row['sigs']:>5}  {row['n']:>7}  "
          f"{wr_s:>7}  {nr:>+7.1f}R  {bar} {mark}")
print()
