"""
optimize_gap_rej_short.py
=========================
Part 1: Sweep trigger parameters and R:R for GAP_REJ_SHORT on USDJPY indicator.
Part 2: For each optimal-config signal, analyse what DXY is doing at that bar
        to find an equivalent DXY-based trigger.

Approach:
  - First pass: collect ALL raw GAP_REJ_SHORT candidates from USDJPY data with
    full metadata (gap_pts, reward_pts, bb4_flat_regime, candle type, etc.)
  - Second pass: sweep filter thresholds over that metadata (fast, no re-scan)
  - For each (filter combo × TP multiplier × pair set) measure stats
  - Third pass: for signals passing the best config, load DXY state at that bar
"""

import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product
from multiprocessing import Pool, cpu_count

import dxy_improved_rules as imp
import dxy_clean_rules    as r

BASE      = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
N_WORKERS = min(8, cpu_count())
MAX_BARS  = 500

ALL_PAIRS   = ['EURUSD', 'USDJPY', 'USDCAD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCHF']
PAIR_FACTOR = {'EURUSD':0.01,'GBPUSD':0.01,'AUDUSD':0.01,'NZDUSD':0.01,
               'USDJPY':1.0, 'USDCAD':0.01,'USDCHF':0.01}
PAIR_DIR    = {'EURUSD':-1,'GBPUSD':-1,'AUDUSD':-1,'NZDUSD':-1,
               'USDJPY':+1,'USDCAD':+1,'USDCHF':+1}
FILE_MAP    = {p: BASE / f'FX_{p}, 15_merged.csv' for p in ALL_PAIRS}

PAIR_SETS = {
    'gbp_chf':  ['GBPUSD', 'USDCHF'],
    'inv3':     ['EURUSD', 'GBPUSD', 'USDCHF'],
    'inv4':     ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD'],
    'all7':     ALL_PAIRS,
}

PIN_WICK_MULT = r.PIN_WICK_MULT
ATTR_MIN_REWARD_DEFAULT = 100


# ══════════════════════════════════════════════════════════════════════════════
# CANDLE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _pin_series(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body    = (c - o).abs()
    bt      = pd.concat([o, c], axis=1).max(axis=1)
    bb_     = pd.concat([o, c], axis=1).min(axis=1)
    hi_wick = h - bt
    lo_wick = bb_ - l
    rng     = (h - l).replace(0, np.nan)
    bull_p  = (lo_wick >= body * PIN_WICK_MULT) & (lo_wick >= hi_wick * 1.5) & rng.notna()
    bear_p  = (hi_wick >= body * PIN_WICK_MULT) & (hi_wick >= lo_wick * 1.5) & rng.notna()
    both    = bull_p & bear_p
    return bull_p & ~(both & (c <= o)), bear_p & ~(both & (c >= o))


def _engulf_3bar(df):
    c, o   = df['close'], df['open']
    body   = (c - o).abs()
    bar2r  = (c.shift(2) - o.shift(2)).abs()
    indec  = body.shift(1) <= bar2r * 0.5
    bear_e = ((c < o) & ~(c.shift(1) < o.shift(1)) & (c < o.shift(1)) & (o > c.shift(1))
               & (body >= body.shift(1) * 0.8))
    bear_3b = (c.shift(2) > o.shift(2)) & indec & (c < o) & (c < o.shift(2))
    return (bear_e | bear_3b).fillna(False)


# ══════════════════════════════════════════════════════════════════════════════
# INDICATOR EXIT FINDER
# ══════════════════════════════════════════════════════════════════════════════

def _find_exit(df, entry_bar, entry_px, sl_d, direction, rr):
    n = len(df)
    tp_px = entry_px - sl_d * rr if direction == 'short' else entry_px + sl_d * rr
    sl_px = entry_px + sl_d       if direction == 'short' else entry_px - sl_d
    for j in range(entry_bar + 1, min(entry_bar + MAX_BARS, n)):
        o_j, h_j, l_j = df.at[j,'open'], df.at[j,'high'], df.at[j,'low']
        if direction == 'short':
            if o_j >= sl_px or h_j >= sl_px: return j, 'loss'
            if o_j <= tp_px or l_j <= tp_px: return j, 'win'
        else:
            if o_j <= sl_px or l_j <= sl_px: return j, 'loss'
            if o_j >= tp_px or h_j >= tp_px: return j, 'win'
    return min(entry_bar + MAX_BARS - 1, n - 1), 'timeout'


# ══════════════════════════════════════════════════════════════════════════════
# PART 1: COLLECT RAW GAP_REJ_SHORT CANDIDATES (no filtering except basic state)
# ══════════════════════════════════════════════════════════════════════════════

def collect_raw_candidates(df_src, news_dates):
    """
    Scan USDJPY (or any OHLC df) for ALL potential GAP_REJ_SHORT triggers,
    storing full metadata. Filtering happens in the sweep below.
    """
    df = df_src.copy().reset_index(drop=True)
    c_s, o_s = df['close'], df['open']
    _, bear_pin = _pin_series(df)
    bear_eng    = _engulf_3bar(df)
    bear_sig    = bear_pin | bear_eng

    _, bb4_flat = imp.compute_bb_regime(df, 4)
    _, bb1_flat = imp.compute_bb_regime(df, 1)
    bb4_arr  = bb4_flat.values
    bb1_arr  = bb1_flat.values
    bs_arr   = bear_sig.values

    df['_date'] = df['time'].dt.date
    day_grp = df.groupby('_date').agg(day_h=('high','max'), day_l=('low','min'))

    LON_H, LON_M = 7, 0
    MON_H, MON_M = 6, 30
    ATTR_WINDOW  = (6*60, 19*60+30)
    ENTRY_END    = 18*60

    lon_px   = np.nan
    prev_rng = 0.0
    prev_hi = prev_lo = np.nan

    # gap state
    attr_gap_pts    = 0.0
    attr_gap_target = np.nan
    attr_touched    = False
    attr_traded     = False

    candidates = []

    for i in range(2, len(df)):
        row = df.iloc[i]
        cv, ov = row['close'], row['open']
        ts     = row['time']
        hh, mm = ts.hour, ts.minute
        cm     = hh * 60 + mm
        dow    = ts.dayofweek
        in_jpn = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)
        is_lon = (not in_jpn and hh == LON_H and mm == LON_M and dow != 0)
        is_mon = (not in_jpn and hh == MON_H and mm == MON_M and dow == 0)
        is_2345 = (hh == 23 and mm == 45)

        if is_2345:
            attr_traded = attr_touched = False
            ref = None
            for back, off in [(2, 30), (1, 15)]:
                if i >= back:
                    cand = df.iloc[i - back]
                    if abs((cand['time'] - (ts - pd.Timedelta(minutes=off))).total_seconds()) <= 120:
                        ref = cand['close']; break
            if ref is not None:
                raw = (ov - ref) * 10000
                if abs(raw) >= 5:       # very loose raw threshold - filter later
                    attr_gap_pts, attr_gap_target = raw, ref
                else:
                    attr_gap_pts, attr_gap_target = (cv - ov) * 10000, ov
            else:
                attr_gap_pts, attr_gap_target = 0.0, np.nan

        if is_lon or is_mon:
            lon_px = ov
            today  = ts.date()
            prior  = [d for d in day_grp.index if d < today]
            if prior:
                pd_ = max(prior)
                prev_hi  = float(day_grp.at[pd_, 'day_h'])
                prev_lo  = float(day_grp.at[pd_, 'day_l'])
                prev_rng = (prev_hi - prev_lo) * 10000
            else:
                prev_hi = prev_lo = np.nan; prev_rng = 0.0
            continue

        if np.isnan(lon_px) or in_jpn:
            continue

        # update gap touch state
        if not np.isnan(attr_gap_target):
            if attr_gap_pts > 0 and cv <= attr_gap_target:
                attr_touched = True

        in_attr = (ATTR_WINDOW[0] <= cm <= ATTR_WINDOW[1] and not in_jpn)
        if not in_attr: continue

        if (not attr_traded and attr_gap_pts > 0 and attr_touched
                and not np.isnan(attr_gap_target) and cv > attr_gap_target
                and bs_arr[i]):
            rwd = (cv - attr_gap_target) * 10000
            tp_p = attr_gap_target + 50 / 10000
            sl_d = cv - tp_p
            if sl_d > 0 and rwd > 0:
                is_pin = bool(bear_pin.iat[i])
                is_eng = bool(bear_eng.iat[i])
                # news filter
                if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
                    continue
                candidates.append({
                    'entry_time':    str(ts),
                    'entry_bar':     i,
                    'entry':         round(cv, 5),
                    'sl_pts':        round(sl_d * 10000),
                    'gap_pts':       round(attr_gap_pts),
                    'reward_pts':    round(rwd),
                    'gap_target':    round(attr_gap_target, 5),
                    'bb4_flat':      int(bb4_arr[i]),
                    'bb1_flat':      int(bb1_arr[i]),
                    'prev_rng':      round(prev_rng),
                    'is_pin':        is_pin,
                    'is_eng':        is_eng,
                    'lon_open':      round(lon_px, 5),
                    'dow':           dow,
                })
                attr_traded = True

    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# PART 2: SWEEP FILTERS × TP MULTIPLIERS × PAIR SETS
# ══════════════════════════════════════════════════════════════════════════════

def _apply_pair_for_sweep(pair, sigs_with_exits, df_p):
    """Apply a list of signals (with pre-computed exit_bar) to one pair."""
    n  = len(df_p)
    F  = PAIR_FACTOR[pair]
    D  = PAIR_DIR[pair]
    pidx = {str(t): i for i, t in enumerate(df_p['time'])}
    rows = []
    for sig in sigs_with_exits:
        et = sig['entry_time']
        xt = sig.get('exit_time')
        if et not in pidx or not xt or xt not in pidx: continue
        pi = pidx[et]; xi = pidx[xt]
        pc = df_p.at[pi, 'close']
        # indicator SHORT -> pair direction depends on PAIR_DIR
        pair_long  = (D == -1)   # SHORT indicator → inverse pairs go LONG
        pair_sl_d  = sig['sl_pts'] / 10000 * F
        if pair_sl_d <= 0: continue
        pair_sl_px = pc - pair_sl_d if pair_long else pc + pair_sl_d
        r_actual   = None
        for j in range(pi + 1, min(xi + 1, n)):
            if pair_long  and df_p.at[j, 'low']  <= pair_sl_px: r_actual = -1.0; break
            if not pair_long and df_p.at[j, 'high'] >= pair_sl_px: r_actual = -1.0; break
        if r_actual is None:
            px = df_p.at[min(xi, n - 1), 'close']
            raw = (px - pc) if pair_long else (pc - px)
            r_actual = raw / pair_sl_d
        rows.append({'entry_time': et, 'pair': pair, 'r_actual': round(r_actual, 3)})
    return rows


def _stats(r_vals, months):
    if not r_vals: return None
    n    = len(r_vals)
    arr  = np.array(r_vals)
    wins = (arr > 0).sum()
    net  = arr.sum()
    gw   = arr[arr > 0].sum()
    gl   = (-arr[arr < 0]).sum()
    pf   = gw / gl if gl > 0 else 999.0
    aw   = arr[arr > 0].mean() if wins else 0.0
    al   = arr[arr < 0].mean() if (n - wins) else 0.0
    return dict(N=n, sigs=n, WR=round(wins/n*100,1), NetR=round(net,2),
                rpm=round(net/months,2), PF=round(pf,2), AvgW=round(aw,3), AvgL=round(al,3))


def run_sweep(candidates, df_jpy, pair_dfs, months):
    """
    Sweep parameters, apply to pairs, return top configs.
    candidates: list of raw candidate dicts from collect_raw_candidates
    """
    # Parameter grid
    min_gap_grid    = [0, 10, 20, 50, 100, 200]
    min_reward_grid = [50, 100, 200]
    bb_filter_grid  = ['any', 'flat']       # 'flat' = bb4_flat==1
    tp_mult_grid    = [1.0, 1.5, 2.0, 2.5, 3.0]
    pair_set_grid   = list(PAIR_SETS.keys())

    total = (len(min_gap_grid) * len(min_reward_grid) * len(bb_filter_grid)
             * len(tp_mult_grid) * len(pair_set_grid))
    print(f"  Sweeping {total} combinations over {len(candidates)} raw candidates...")

    results = []
    n_done  = 0

    for min_gap, min_rwd, bb_f, tp_m, ps_key in product(
            min_gap_grid, min_reward_grid, bb_filter_grid, tp_mult_grid, pair_set_grid):

        # 1. Filter candidates
        filt = [c for c in candidates
                if c['gap_pts']    >= min_gap
                and c['reward_pts'] >= min_rwd
                and (bb_f == 'any' or c['bb4_flat'] == 1)]

        if len(filt) < 2:
            n_done += 1
            continue

        # 2. Resolve indicator exits (USDJPY hits TP or SL)
        sigs = []
        for c in filt:
            eb, oc = _find_exit(df_jpy, c['entry_bar'], c['entry'],
                                c['sl_pts'] / 10000, 'short', tp_m)
            sigs.append({**c, 'exit_bar': eb,
                          'exit_time': str(df_jpy.at[eb, 'time']),
                          'indicator_outcome': oc})

        # 3. Apply to pair set
        pairs = PAIR_SETS[ps_key]
        r_vals = []
        for pair in pairs:
            df_p = pair_dfs[pair]
            rows = _apply_pair_for_sweep(pair, sigs, df_p)
            r_vals.extend(r['r_actual'] for r in rows)

        st = _stats(r_vals, months)
        if st is None:
            n_done += 1
            continue

        results.append({
            'min_gap':    min_gap,
            'min_reward': min_rwd,
            'bb_filter':  bb_f,
            'tp_mult':    tp_m,
            'pair_set':   ps_key,
            'n_signals':  len(filt),
            **st
        })
        n_done += 1

    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════════════
# PART 3: DXY STATE ANALYSIS at optimal signal times
# ══════════════════════════════════════════════════════════════════════════════

def analyse_dxy_at_signals(sigs, df_dxy):
    """
    For each signal entry time, look up DXY state:
      - Was DXY also gapping up overnight? (positive gap at 23:45)
      - DXY BB regime (flat/trend)
      - DXY price vs DXY London open
      - DXY distance from 23:45 open
    """
    df_dxy = df_dxy.copy().reset_index(drop=True)
    df_dxy['time'] = pd.to_datetime(df_dxy['time'], utc=True)
    didx = {str(t): i for i, t in enumerate(df_dxy['time'])}

    # Compute DXY overnight gap at 23:45 each day
    dxy_gap_by_date = {}
    dxy_lon_by_date = {}
    bb4_dxy, bb4f_dxy = imp.compute_bb_regime(df_dxy, 4)

    lon_px = np.nan
    for i in range(1, len(df_dxy)):
        ts  = df_dxy.at[i, 'time']
        hh, mm = ts.hour, ts.minute
        dow  = ts.dayofweek
        in_jpn = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)

        if hh == 23 and mm == 45:
            ov = df_dxy.at[i, 'open']
            # find close ~30 min before
            for back, off in [(2, 30), (1, 15)]:
                if i >= back:
                    cand = df_dxy.iloc[i - back]
                    if abs((cand['time'] - (ts - pd.Timedelta(minutes=off))).total_seconds()) <= 120:
                        ref = cand['close']
                        raw = (ov - ref) * 10000
                        dxy_gap_by_date[ts.date()] = raw
                        break

        if ((not in_jpn and hh == 7 and mm == 0 and dow != 0)
                or (not in_jpn and hh == 6 and mm == 30 and dow == 0)):
            lon_px = df_dxy.at[i, 'open']
            dxy_lon_by_date[ts.date()] = lon_px

    rows = []
    for sig in sigs:
        et = sig['entry_time']
        if et not in didx: continue
        j  = didx[et]
        ts = df_dxy.at[j, 'time']
        cv = df_dxy.at[j, 'close']
        d  = ts.date()

        dxy_gap   = dxy_gap_by_date.get(d, np.nan)
        dxy_lon   = dxy_lon_by_date.get(d, np.nan)
        dxy_vs_lon = (cv - dxy_lon) * 10000 if not np.isnan(dxy_lon) else np.nan
        bb4f_val  = int(bb4f_dxy.at[j]) if j < len(bb4f_dxy) else np.nan

        rows.append({
            'entry_time':    et,
            'jpy_gap_pts':   sig.get('gap_pts'),
            'jpy_rew_pts':   sig.get('reward_pts'),
            'jpy_entry':     sig.get('entry'),
            'jpy_lon_open':  sig.get('lon_open'),
            'jpy_vs_lon':    round((sig.get('entry', np.nan) - sig.get('lon_open', np.nan)) * 10000, 1)
                              if not np.isnan(sig.get('lon_open', np.nan)) else np.nan,
            'dxy_at_entry':  round(cv, 5),
            'dxy_gap_pts':   round(dxy_gap, 1) if not np.isnan(dxy_gap) else np.nan,
            'dxy_vs_lon_pts':round(dxy_vs_lon, 1) if not np.isnan(dxy_vs_lon) else np.nan,
            'dxy_bb4_flat':  bb4f_val,
            'dxy_gap_dir':   ('up' if dxy_gap > 10 else ('down' if dxy_gap < -10 else 'flat'))
                              if not np.isnan(dxy_gap) else 'unknown',
            'jpy_indicator_outcome': sig.get('indicator_outcome',''),
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    news_dates = r.load_news_filter()

    print("Loading data...")
    df_jpy = pd.read_csv(FILE_MAP['USDJPY'])
    df_jpy['time'] = pd.to_datetime(df_jpy['time'], utc=True)
    df_jpy = df_jpy.sort_values('time').reset_index(drop=True)
    for col in ['open','high','low','close']: df_jpy[col] = df_jpy[col].astype(float)

    df_dxy = imp.load_merged('DXY').reset_index(drop=True)
    df_dxy['time'] = pd.to_datetime(df_dxy['time'], utc=True)
    months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44

    pair_dfs = {}
    for pair in ALL_PAIRS:
        dfp = pd.read_csv(FILE_MAP[pair])
        dfp['time'] = pd.to_datetime(dfp['time'], utc=True)
        dfp = dfp.sort_values('time').reset_index(drop=True)
        for col in ['open','high','low','close']: dfp[col] = dfp[col].astype(float)
        pair_dfs[pair] = dfp

    print(f"  Loaded {len(df_jpy):,} USDJPY bars, {len(df_dxy):,} DXY bars, {months:.1f} months")

    # ── Part 1: collect raw candidates ────────────────────────────────────
    print("\nCollecting raw GAP_REJ_SHORT candidates on USDJPY...")
    candidates = collect_raw_candidates(df_jpy, news_dates)
    print(f"  Raw candidates (minimal filter): {len(candidates)}")
    if candidates:
        gaps  = [c['gap_pts'] for c in candidates]
        rwds  = [c['reward_pts'] for c in candidates]
        print(f"  Gap range: {min(gaps):.0f} - {max(gaps):.0f} pts  "
              f"(mean {np.mean(gaps):.0f})")
        print(f"  Reward range: {min(rwds):.0f} - {max(rwds):.0f} pts  "
              f"(mean {np.mean(rwds):.0f})")
        bb4f_counts = pd.Series([c['bb4_flat'] for c in candidates]).value_counts()
        print(f"  BB4-flat=1: {bb4f_counts.get(1,0)}  BB4-flat=0: {bb4f_counts.get(0,0)}")

    # ── Part 2: parameter sweep ─────────────────────────────────────────────
    print("\nRunning parameter sweep...")
    df_sweep = run_sweep(candidates, df_jpy, pair_dfs, months)
    df_sweep = df_sweep.sort_values('NetR', ascending=False).reset_index(drop=True)

    print(f"\n  Top 20 configurations by Net R:")
    print(f"  {'min_gap':>7} {'min_rwd':>7} {'bb':>6} {'tp_m':>5} {'pairs':>9} "
          f"{'N_sig':>6} {'N_tr':>5} {'WR%':>6} {'NetR':>8} {'R/mo':>6} {'PF':>5}")
    print(f"  {'-'*82}")
    for _, row in df_sweep.head(20).iterrows():
        print(f"  {int(row['min_gap']):>7} {int(row['min_reward']):>7} "
              f"{row['bb_filter']:>6} {row['tp_mult']:>5.1f} {row['pair_set']:>9} "
              f"{int(row['n_signals']):>6} {int(row['N']):>5}  {row['WR']:>5.1f}% "
              f"{row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} {row['PF']:>5.2f}")

    # ── Additional view: best per pair-set ─────────────────────────────────
    print("\n  Best config per pair-set (by Net R):")
    print(f"  {'pair_set':>10} {'min_gap':>7} {'min_rwd':>7} {'bb':>6} {'tp_m':>5} "
          f"{'N_sig':>6} {'N_tr':>5} {'WR%':>6} {'NetR':>8} {'R/mo':>6} {'PF':>5}")
    print(f"  {'-'*82}")
    for ps, grp in df_sweep.groupby('pair_set'):
        row = grp.nlargest(1, 'NetR').iloc[0]
        print(f"  {ps:>10} {int(row['min_gap']):>7} {int(row['min_reward']):>7} "
              f"{row['bb_filter']:>6} {row['tp_mult']:>5.1f} "
              f"{int(row['n_signals']):>6} {int(row['N']):>5}  {row['WR']:>5.1f}% "
              f"{row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} {row['PF']:>5.2f}")

    # ── R:R sensitivity for the best pair-set ──────────────────────────────
    best_ps  = df_sweep.nlargest(1,'NetR')['pair_set'].iloc[0]
    best_gap = int(df_sweep.nlargest(1,'NetR')['min_gap'].iloc[0])
    best_rwd = int(df_sweep.nlargest(1,'NetR')['min_reward'].iloc[0])
    best_bb  = df_sweep.nlargest(1,'NetR')['bb_filter'].iloc[0]

    print(f"\n  R:R sensitivity for best filter config "
          f"(min_gap={best_gap}, min_rwd={best_rwd}, bb={best_bb}, pairs={best_ps}):")
    sub = df_sweep[(df_sweep['min_gap']==best_gap) & (df_sweep['min_reward']==best_rwd)
                   & (df_sweep['bb_filter']==best_bb) & (df_sweep['pair_set']==best_ps)]
    sub = sub.sort_values('tp_mult')
    print(f"  {'tp_mult':>7} {'N_sig':>6} {'N_tr':>5} {'WR%':>6} {'NetR':>8} "
          f"{'R/mo':>6} {'PF':>5} {'AvgW':>7} {'AvgL':>7}")
    print(f"  {'-'*62}")
    for _, row in sub.iterrows():
        print(f"  {row['tp_mult']:>7.1f} {int(row['n_signals']):>6} {int(row['N']):>5} "
              f"{row['WR']:>5.1f}% {row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} "
              f"{row['PF']:>5.2f} {row['AvgW']:>+6.3f}R {row['AvgL']:>+6.3f}R")

    # Save sweep
    df_sweep.to_csv(BASE / 'gap_rej_short_sweep.csv', index=False)
    print(f"\n  Saved gap_rej_short_sweep.csv ({len(df_sweep)} rows)")

    # ── Part 3: DXY state analysis at best-config signal times ────────────
    # Use best overall config to generate the signal list
    print(f"\n{'='*80}")
    print("  PART 3: DXY STATE ANALYSIS AT USDJPY GAP_REJ_SHORT ENTRIES")
    print(f"{'='*80}")

    # Identify optimal config (best NetR with reasonable N_signals >= 5)
    good = df_sweep[df_sweep['n_signals'] >= 5].nlargest(1, 'NetR')
    if good.empty:
        good = df_sweep.nlargest(1, 'NetR')
    best_row = good.iloc[0]
    opt_gap  = int(best_row['min_gap'])
    opt_rwd  = int(best_row['min_reward'])
    opt_bb   = best_row['bb_filter']
    opt_tp   = float(best_row['tp_mult'])
    opt_ps   = best_row['pair_set']

    print(f"\n  Optimal config: min_gap={opt_gap}, min_rwd={opt_rwd}, "
          f"bb={opt_bb}, tp_mult={opt_tp}, pairs={opt_ps}")
    print(f"  Performance: {int(best_row['n_signals'])} signals, {int(best_row['N'])} trades, "
          f"{best_row['WR']:.1f}% WR, {best_row['NetR']:+.1f}R total")

    # Rebuild the optimal signal list
    opt_sigs = [c for c in candidates
                if c['gap_pts']    >= opt_gap
                and c['reward_pts'] >= opt_rwd
                and (opt_bb == 'any' or c['bb4_flat'] == 1)]
    for sig in opt_sigs:
        eb, oc = _find_exit(df_jpy, sig['entry_bar'], sig['entry'],
                            sig['sl_pts'] / 10000, 'short', opt_tp)
        sig['exit_bar']          = eb
        sig['exit_time']         = str(df_jpy.at[eb, 'time'])
        sig['indicator_outcome'] = oc

    # Analyse DXY at those times
    df_dxy_analysis = analyse_dxy_at_signals(opt_sigs, df_dxy)

    print(f"\n  DXY state at the {len(df_dxy_analysis)} optimal USDJPY GAP_REJ_SHORT entries:")
    print()
    print(f"  {'Entry':>22}  {'JPY gap':>8} {'JPY rwd':>8} {'DXY gap':>8} "
          f"{'DXY vs Lon':>11} {'DXY BB4f':>9} {'JPY result':>11}")
    print(f"  {'-'*88}")
    for _, row in df_dxy_analysis.iterrows():
        jpy_res = row.get('jpy_indicator_outcome','')
        print(f"  {str(row['entry_time'])[:19]:>22}  "
              f"{row['jpy_gap_pts']:>7.0f}p "
              f"{row['jpy_rew_pts']:>7.0f}p "
              f"{row['dxy_gap_pts']:>7.1f}p "
              f"{row['dxy_vs_lon_pts']:>10.1f}p "
              f"{int(row['dxy_bb4_flat']) if pd.notna(row['dxy_bb4_flat']) else '-':>9} "
              f"{jpy_res:>11}")

    # ── Summary of DXY patterns ─────────────────────────────────────────────
    print()
    print("  DXY PATTERN SUMMARY across all optimal signals:")
    print()
    n_tot = len(df_dxy_analysis)
    # Gap direction
    gd = df_dxy_analysis['dxy_gap_dir'].value_counts()
    for direction in ['up','down','flat','unknown']:
        cnt = gd.get(direction, 0)
        print(f"    DXY gapped {direction:>5}: {cnt:>3} / {n_tot} "
              f"({cnt/n_tot*100:.0f}%)")
    print()

    # DXY BB4 flat
    bb_cnt = df_dxy_analysis['dxy_bb4_flat'].value_counts()
    for bb_val in [1, 0]:
        cnt = bb_cnt.get(bb_val, 0)
        label = 'flat' if bb_val == 1 else 'trending'
        print(f"    DXY BB4 {label:>9}: {cnt:>3} / {n_tot} "
              f"({cnt/n_tot*100:.0f}%)")
    print()

    # DXY vs London open
    above = (df_dxy_analysis['dxy_vs_lon_pts'] > 0).sum()
    below = (df_dxy_analysis['dxy_vs_lon_pts'] <= 0).sum()
    med_dist = df_dxy_analysis['dxy_vs_lon_pts'].median()
    print(f"    DXY above London open: {above} / {n_tot} ({above/n_tot*100:.0f}%)")
    print(f"    DXY below London open: {below} / {n_tot} ({below/n_tot*100:.0f}%)")
    print(f"    DXY vs London median: {med_dist:+.1f} pts")
    print()

    # DXY gap magnitude
    valid_gaps = df_dxy_analysis['dxy_gap_pts'].dropna()
    if len(valid_gaps):
        pos_gaps = valid_gaps[valid_gaps > 10]
        print(f"    DXY positive gap (>10pts): {len(pos_gaps)} / {n_tot} "
              f"({len(pos_gaps)/n_tot*100:.0f}%)")
        if len(pos_gaps):
            print(f"      Mean DXY gap: {pos_gaps.mean():.1f} pts  "
                  f"range {pos_gaps.min():.0f} - {pos_gaps.max():.0f} pts")

    # Win/loss by DXY gap direction
    print()
    print("  USDJPY outcome by DXY gap direction:")
    df_dxy_analysis['result'] = df_dxy_analysis['jpy_indicator_outcome']
    for gdir in ['up','down','flat']:
        sub = df_dxy_analysis[df_dxy_analysis['dxy_gap_dir'] == gdir]
        if len(sub) == 0: continue
        wins = (sub['result'] == 'win').sum()
        print(f"    DXY gap {gdir:>5}: N={len(sub):>2}  wins={wins:>2}  "
              f"WR={wins/len(sub)*100:.0f}%")

    # Save analysis
    df_dxy_analysis.to_csv(BASE / 'gap_rej_short_dxy_analysis.csv', index=False)
    print(f"\n  Saved gap_rej_short_dxy_analysis.csv ({len(df_dxy_analysis)} rows)")
    print()

    # ── Candidate breakdown table for manual inspection ────────────────────
    print("  ALL raw candidates (sorted by entry time):")
    print(f"  {'Entry':>22} {'JPY_gap':>8} {'JPY_rwd':>8} {'BB4f':>5} "
          f"{'Pin':>5} {'Eng':>5}")
    print(f"  {'-'*60}")
    for c in sorted(candidates, key=lambda x: x['entry_time']):
        print(f"  {str(c['entry_time'])[:19]:>22}  "
              f"{c['gap_pts']:>7.0f}p {c['reward_pts']:>7.0f}p "
              f"{c['bb4_flat']:>5} {str(c['is_pin']):>5} {str(c['is_eng']):>5}")
