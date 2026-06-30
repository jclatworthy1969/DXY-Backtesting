"""
optimize_rev_usdjpy.py
======================
Tests REV_LONG and REV_SHORT using USDJPY as the indicator with
a reworked "impulsive move" trigger framework.

Problem with existing code:
  - abs(dist) <= 250pt filter (price within 250pts of London open) was
    calibrated for DXY; on USDJPY it generates ZERO SHORT signals and
    only 6 LONG signals because USDJPY ranges are ~10x larger.

New framework — "reversal after impulse":
  For REV_LONG:  USDJPY has dropped at least min_impulse pts from
                 London open, price still below London open, bullish candle.
  For REV_SHORT: USDJPY has risen at least min_impulse pts from
                 London open, price still above London open, bearish candle.

Sweep parameters:
  min_impulse   — minimum move from London open before reversal can fire
  max_dist      — max distance from London open AT entry (avoid over-extended moves)
  bb_filter     — BB4 flat regime filter
  tp_mult       — USDJPY indicator TP multiplier
  pair_set      — which pairs to trade

Also checks max_sl_pts to exclude excessively wide structural SLs.

DXY state analysis at optimal signal bars.
"""

import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product

import dxy_improved_rules as imp
import dxy_clean_rules    as r

BASE     = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
MAX_BARS = 500

ALL_PAIRS   = ['EURUSD', 'USDJPY', 'USDCAD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCHF']
PAIR_FACTOR = {'EURUSD':0.01,'GBPUSD':0.01,'AUDUSD':0.01,'NZDUSD':0.01,
               'USDJPY':1.0, 'USDCAD':0.01,'USDCHF':0.01}
PAIR_DIR    = {'EURUSD':-1,'GBPUSD':-1,'AUDUSD':-1,'NZDUSD':-1,
               'USDJPY':+1,'USDCAD':+1,'USDCHF':+1}
FILE_MAP    = {p: BASE / f'FX_{p}, 15_merged.csv' for p in ALL_PAIRS}

PAIR_SETS = {
    'all7':  ALL_PAIRS,
    'same3': ['USDJPY', 'USDCAD', 'USDCHF'],
    'inv4':  ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD'],
    'inv3':  ['EURUSD', 'GBPUSD', 'USDCHF'],
    'jpy_inv4': ['USDJPY', 'EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD'],
}

PIN_WICK_MULT = r.PIN_WICK_MULT
MAX_SL_PTS    = 3000   # structural SL cap for USDJPY


# ══════════════════════════════════════════════════════════════════════════════
# CANDLE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _pin_series(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body    = (c - o).abs()
    bt      = pd.concat([o, c], axis=1).max(axis=1)
    bb_     = pd.concat([o, c], axis=1).min(axis=1)
    hi_wick = h - bt;  lo_wick = bb_ - l
    rng     = (h - l).replace(0, np.nan)
    bull_p  = (lo_wick >= body * PIN_WICK_MULT) & (lo_wick >= hi_wick * 1.5) & rng.notna()
    bear_p  = (hi_wick >= body * PIN_WICK_MULT) & (hi_wick >= lo_wick * 1.5) & rng.notna()
    both    = bull_p & bear_p
    return bull_p & ~(both & (c <= o)), bear_p & ~(both & (c >= o))


def _engulf_3bar(df):
    c, o  = df['close'], df['open']
    body  = (c - o).abs()
    bar2r = (c.shift(2) - o.shift(2)).abs()
    indec = body.shift(1) <= bar2r * 0.5
    bull_e  = ((c > o) & ~(c.shift(1) > o.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))
                & (body >= body.shift(1) * 0.8))
    bear_e  = ((c < o) & ~(c.shift(1) < o.shift(1)) & (c < o.shift(1)) & (o > c.shift(1))
                & (body >= body.shift(1) * 0.8))
    bull_3b = (c.shift(2) < o.shift(2)) & indec & (c > o) & (c > o.shift(2))
    bear_3b = (c.shift(2) > o.shift(2)) & indec & (c < o) & (c < o.shift(2))
    return (bull_e | bull_3b).fillna(False), (bear_e | bear_3b).fillna(False)


# ══════════════════════════════════════════════════════════════════════════════
# INDICATOR EXIT FINDER
# ══════════════════════════════════════════════════════════════════════════════

def _find_exit(df, entry_bar, entry_px, sl_d, direction, rr):
    n = len(df)
    tp_px = (entry_px + sl_d * rr) if direction == 'long' else (entry_px - sl_d * rr)
    sl_px = (entry_px - sl_d)      if direction == 'long' else (entry_px + sl_d)
    for j in range(entry_bar + 1, min(entry_bar + MAX_BARS, n)):
        o_j, h_j, l_j = df.at[j,'open'], df.at[j,'high'], df.at[j,'low']
        if direction == 'long':
            if o_j <= sl_px or l_j <= sl_px: return j, 'loss'
            if o_j >= tp_px or h_j >= tp_px: return j, 'win'
        else:
            if o_j >= sl_px or h_j >= sl_px: return j, 'loss'
            if o_j <= tp_px or l_j <= tp_px: return j, 'win'
    return min(entry_bar + MAX_BARS - 1, n - 1), 'timeout'


# ══════════════════════════════════════════════════════════════════════════════
# PART 1: COLLECT RAW REV CANDIDATES
# Loose thresholds — sweep will tighten them.
# One signal per London session per direction (first valid candle wins).
# ══════════════════════════════════════════════════════════════════════════════

def collect_rev_candidates(df_src, news_dates):
    """
    Scans USDJPY for all reversal candidates.
    For LONG: max_dn tracks maximum downward extension from London open;
              bullish candle while price is below London open.
    For SHORT: max_up tracks maximum upward extension;
               bearish candle while price is above London open.
    No approach/distance filter applied here — full metadata stored for sweep.
    One candidate per direction per London session.
    """
    df = df_src.copy().reset_index(drop=True)
    bull_pin, bear_pin = _pin_series(df)
    bull_eng, bear_eng = _engulf_3bar(df)
    bull_sig = (bull_pin | bull_eng).fillna(False)
    bear_sig = (bear_pin | bear_eng).fillna(False)

    _, bb4_flat = imp.compute_bb_regime(df, 4)
    bb1, _      = imp.compute_bb_regime(df, 1)
    bb4f_arr = bb4_flat.values
    bb1_arr  = bb1.values

    df['_date'] = df['time'].dt.date
    day_grp = df.groupby('_date').agg(day_h=('high','max'), day_l=('low','min'))

    LON_H, LON_M = 7, 0
    MON_H, MON_M = 6, 30
    REV_END  = 18 * 60
    REV_START_MON = MON_H * 60 + MON_M
    REV_START_LON = LON_H * 60

    lon_px   = np.nan
    prev_hi  = prev_lo = np.nan
    max_up   = max_dn  = 0.0
    rev_long_fired  = False
    rev_short_fired = False

    candidates = []

    for i in range(2, len(df)):
        row = df.iloc[i]
        cv, ov = row['close'], row['open']
        hv, lv = row['high'],  row['low']
        ts     = row['time']
        hh, mm = ts.hour, ts.minute
        cm     = hh * 60 + mm
        dow    = ts.dayofweek
        in_jpn = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)
        is_lon = (not in_jpn and hh == LON_H and mm == LON_M and dow != 0)
        is_mon = (not in_jpn and hh == MON_H and mm == MON_M and dow == 0)

        if is_lon or is_mon:
            lon_px = ov
            max_up = max_dn = 0.0
            rev_long_fired = rev_short_fired = False
            today = ts.date()
            prior = [d for d in day_grp.index if d < today]
            if prior:
                pd_ = max(prior)
                prev_hi = float(day_grp.at[pd_, 'day_h'])
                prev_lo = float(day_grp.at[pd_, 'day_l'])
            else:
                prev_hi = prev_lo = np.nan
            continue

        if np.isnan(lon_px) or in_jpn:
            continue

        rev_start = REV_START_MON if dow == 0 else REV_START_LON
        in_rev    = (rev_start <= cm <= REV_END)
        if not in_rev:
            continue

        dist      = (cv - lon_px) * 10000          # positive = above London open
        hi_dist   = (hv - lon_px) * 10000
        lo_dist   = (lv - lon_px) * 10000
        max_up    = max(max_up, hi_dist)
        max_dn    = max(max_dn, -lo_dist)           # max_dn is always positive

        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        # ── REV_LONG: bullish candle, price below London open, after downward impulse ──
        if not rev_long_fired and bull_sig.at[i] and dist < 0:
            if not np.isnan(prev_lo) and not np.isnan(prev_hi):
                sl_p   = imp.get_structural_sl(prev_lo, prev_hi, cv, 'long')
                sl_d   = cv - sl_p
                sl_pts = sl_d * 10000
                if 0 < sl_pts <= MAX_SL_PTS:
                    candidates.append({
                        'type':        'REV_LONG',
                        'direction':   'long',
                        'entry_time':  str(ts),
                        'entry_bar':   i,
                        'entry':       round(cv, 5),
                        'sl_pts':      round(sl_pts),
                        'dist':        round(dist),       # negative = below London
                        'max_dn':      round(max_dn),     # max downward move before this bar
                        'max_up':      round(max_up),     # max upward move before this bar
                        'bb4_flat':    int(bb4f_arr[i]),
                        'bb1_val':     int(bb1_arr[i]),
                        'is_pin':      bool(bull_pin.iat[i]),
                        'is_eng':      bool(bull_eng.iat[i]),
                        'lon_open':    round(lon_px, 5),
                        'prev_lo':     round(prev_lo, 5),
                        'prev_hi':     round(prev_hi, 5),
                        'cm':          cm,
                        'dow':         dow,
                    })
                    rev_long_fired = True

        # ── REV_SHORT: bearish candle, price above London open, after upward impulse ──
        if not rev_short_fired and bear_sig.at[i] and dist > 0:
            if not np.isnan(prev_lo) and not np.isnan(prev_hi):
                sl_p   = imp.get_structural_sl(prev_lo, prev_hi, cv, 'short')
                sl_d   = sl_p - cv
                sl_pts = sl_d * 10000
                if 0 < sl_pts <= MAX_SL_PTS:
                    candidates.append({
                        'type':        'REV_SHORT',
                        'direction':   'short',
                        'entry_time':  str(ts),
                        'entry_bar':   i,
                        'entry':       round(cv, 5),
                        'sl_pts':      round(sl_pts),
                        'dist':        round(dist),       # positive = above London
                        'max_dn':      round(max_dn),
                        'max_up':      round(max_up),     # max upward move before this bar
                        'bb4_flat':    int(bb4f_arr[i]),
                        'bb1_val':     int(bb1_arr[i]),
                        'is_pin':      bool(bear_pin.iat[i]),
                        'is_eng':      bool(bear_eng.iat[i]),
                        'lon_open':    round(lon_px, 5),
                        'prev_lo':     round(prev_lo, 5),
                        'prev_hi':     round(prev_hi, 5),
                        'cm':          cm,
                        'dow':         dow,
                    })
                    rev_short_fired = True

    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# PART 2: PARAMETER SWEEP
# ══════════════════════════════════════════════════════════════════════════════

def _stats(r_vals, months):
    if len(r_vals) < 2:
        return None
    arr  = np.array(r_vals, dtype=float)
    n    = len(arr)
    wins = (arr > 0).sum()
    net  = arr.sum()
    gw   = arr[arr > 0].sum()
    gl   = (-arr[arr < 0]).sum()
    pf   = gw / gl if gl > 0 else 999.0
    aw   = arr[arr > 0].mean() if wins else 0.0
    al   = arr[arr < 0].mean() if (n - wins) else 0.0
    return dict(N=int(n), WR=round(wins/n*100,1), NetR=round(net,2),
                rpm=round(net/months,2), PF=round(pf,2),
                AvgW=round(aw,3), AvgL=round(al,3))


def run_sweep(candidates, sig_type, df_ind, pair_dfs, months):
    """
    Sweep for one signal type. Key parameters:
      min_impulse  — minimum max_dn (LONG) or max_up (SHORT) at entry
      max_abs_dist — max |dist from London open| at entry
      bb_filter    — 'any' or 'flat'
      tp_mult      — USDJPY TP multiplier
      pair_set     — pair set key
    """
    direction = 'long' if 'LONG' in sig_type else 'short'
    cands     = [c for c in candidates if c['type'] == sig_type]

    # Impulse key: how far price moved before the reversal bar
    impulse_key = 'max_dn' if direction == 'long' else 'max_up'
    # Distance sign: for LONG dist < 0 (below London), for SHORT dist > 0 (above London)
    # abs_dist at entry: how far below/above London open at the entry bar
    # For LONG: abs_dist = -dist (so it's positive)
    # For SHORT: abs_dist = dist

    min_impulse_grid = [0, 100, 250, 500, 1000]
    max_dist_grid    = [250, 500, 1000, 2000, 5000]
    bb_filter_grid   = ['any', 'flat']
    tp_mult_grid     = [1.0, 1.5, 2.0, 2.5, 3.0]
    pair_set_grid    = list(PAIR_SETS.keys())

    total = (len(min_impulse_grid) * len(max_dist_grid) * len(bb_filter_grid)
             * len(tp_mult_grid) * len(pair_set_grid))
    print(f"  {sig_type}: {len(cands)} raw candidates, sweeping {total} combinations...")

    results = []
    for min_imp, max_dist, bb_f, tp_m, ps_key in product(
            min_impulse_grid, max_dist_grid, bb_filter_grid,
            tp_mult_grid, pair_set_grid):

        abs_dist_fn = (lambda c: -c['dist']) if direction == 'long' else (lambda c: c['dist'])

        filt = [c for c in cands
                if c[impulse_key] >= min_imp
                and abs_dist_fn(c) <= max_dist
                and (bb_f == 'any' or c['bb4_flat'] == 1)]

        if len(filt) < 2:
            continue

        # resolve exits
        sigs = []
        for c in filt:
            eb, oc = _find_exit(df_ind, c['entry_bar'], c['entry'],
                                c['sl_pts'] / 10000, direction, tp_m)
            sigs.append({**c, 'exit_bar': eb,
                          'exit_time': str(df_ind.at[eb, 'time']),
                          'indicator_outcome': oc, 'tp_mult': tp_m})

        # apply to pair set
        pairs  = PAIR_SETS[ps_key]
        r_vals = []
        for sig in sigs:
            for pair in pairs:
                df_p  = pair_dfs[pair]
                pidx  = pair_dfs[f'_{pair}_idx']
                et, xt = sig['entry_time'], sig['exit_time']
                if et not in pidx or xt not in pidx:
                    continue
                pi, xi = pidx[et], pidx[xt]
                n   = len(df_p)
                pc  = df_p.at[pi, 'close']
                ind_long  = (direction == 'long')
                pair_long = (ind_long and PAIR_DIR[pair] == 1) or \
                            (not ind_long and PAIR_DIR[pair] == -1)
                psl = sig['sl_pts'] / 10000 * PAIR_FACTOR[pair]
                if psl <= 0:
                    continue
                psl_px = pc - psl if pair_long else pc + psl
                rv = None
                for j in range(pi + 1, min(xi + 1, n)):
                    if pair_long and df_p.at[j, 'low']  <= psl_px: rv = -1.0; break
                    if not pair_long and df_p.at[j, 'high'] >= psl_px: rv = -1.0; break
                if rv is None:
                    px  = df_p.at[min(xi, n - 1), 'close']
                    raw = (px - pc) if pair_long else (pc - px)
                    rv  = raw / psl
                r_vals.append(round(rv, 3))

        st = _stats(r_vals, months)
        if st is None:
            continue
        results.append({
            'sig_type': sig_type, 'min_impulse': min_imp,
            'max_dist': max_dist, 'bb_filter': bb_f,
            'tp_mult': tp_m, 'pair_set': ps_key,
            'n_signals': len(filt), **st
        })

    return pd.DataFrame(results).sort_values('NetR', ascending=False).reset_index(drop=True) \
           if results else pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
# PART 3: DXY STATE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyse_dxy_at_signals(sigs, df_dxy):
    df_dxy = df_dxy.copy().reset_index(drop=True)
    df_dxy['time'] = pd.to_datetime(df_dxy['time'], utc=True)
    didx = {str(t): i for i, t in enumerate(df_dxy['time'])}

    _, bb4f_dxy = imp.compute_bb_regime(df_dxy, 4)
    bb1_dxy, _  = imp.compute_bb_regime(df_dxy, 1)
    bull_pin_d, bear_pin_d = _pin_series(df_dxy)
    bull_eng_d, bear_eng_d = _engulf_3bar(df_dxy)
    bull_sig_d = (bull_pin_d | bull_eng_d).fillna(False)
    bear_sig_d = (bear_pin_d | bear_eng_d).fillna(False)

    # precompute DXY London open and running max_up/max_dn per session
    dxy_session = {}   # date -> {lon_px, max_up, max_dn by time}
    dxy_lon_by_date = {}
    lon_px_d = np.nan
    max_up_d = max_dn_d = 0.0

    for i in range(len(df_dxy)):
        ts  = df_dxy.at[i, 'time']
        hh, mm = ts.hour, ts.minute
        dow  = ts.dayofweek
        in_jpn = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)
        is_lon = (not in_jpn and hh == 7 and mm == 0 and dow != 0)
        is_mon = (not in_jpn and hh == 6 and mm == 30 and dow == 0)
        if is_lon or is_mon:
            lon_px_d = df_dxy.at[i, 'open']
            dxy_lon_by_date[ts.date()] = lon_px_d
            max_up_d = max_dn_d = 0.0
        if not np.isnan(lon_px_d) and not in_jpn:
            cv_d = df_dxy.at[i, 'close']
            dist_d = (cv_d - lon_px_d) * 10000
            if dist_d > 0: max_up_d = max(max_up_d, dist_d)
            else:           max_dn_d = max(max_dn_d, -dist_d)

    rows = []
    for sig in sigs:
        et = sig['entry_time']
        if et not in didx:
            continue
        j   = didx[et]
        ts  = df_dxy.at[j, 'time']
        cv_d = df_dxy.at[j, 'close']
        d    = ts.date()

        dxy_lon    = dxy_lon_by_date.get(d, np.nan)
        dxy_dist   = (cv_d - dxy_lon) * 10000 if not np.isnan(dxy_lon) else np.nan
        bb4f_val   = int(bb4f_dxy.at[j]) if j < len(bb4f_dxy) else np.nan
        bb1_val    = int(bb1_dxy.at[j])  if j < len(bb1_dxy)  else np.nan
        dxy_bull   = bool(bull_sig_d.iat[j]) if j < len(bull_sig_d) else False
        dxy_bear   = bool(bear_sig_d.iat[j]) if j < len(bear_sig_d) else False

        is_long = (sig['direction'] == 'long')
        # Does DXY confirm the direction?
        # LONG reversal: DXY also below London open AND bullish candle = DXY confirms
        dxy_confirms = (is_long and not np.isnan(dxy_dist) and dxy_dist < 0 and dxy_bull) or \
                       (not is_long and not np.isnan(dxy_dist) and dxy_dist > 0 and dxy_bear)

        rows.append({
            'entry_time':     et,
            'sig_type':       sig['type'],
            'jpy_dist':       sig.get('dist'),
            'jpy_impulse':    sig.get('max_dn') if is_long else sig.get('max_up'),
            'jpy_sl_pts':     sig.get('sl_pts'),
            'jpy_bb4_flat':   sig.get('bb4_flat'),
            'jpy_indicator_out': sig.get('indicator_outcome',''),
            'dxy_dist_pts':   round(dxy_dist, 1) if not np.isnan(dxy_dist) else np.nan,
            'dxy_bb4_flat':   bb4f_val,
            'dxy_bb1':        bb1_val,
            'dxy_confirms':   dxy_confirms,
            'dxy_same_side':  (not np.isnan(dxy_dist) and
                               ((is_long and dxy_dist < 0) or (not is_long and dxy_dist > 0))),
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# PRINT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def print_top(df_sw, n=15, title=''):
    if title:
        print(f"\n  {title}")
    print(f"  {'min_imp':>7} {'max_dis':>7} {'bb':>5} {'tp_m':>5} {'pairs':>10} "
          f"{'N_sig':>6} {'N_tr':>5} {'WR%':>6} {'NetR':>8} {'R/mo':>6} "
          f"{'PF':>5} {'AvgW':>7} {'AvgL':>7}")
    print(f"  {'-'*95}")
    for _, row in df_sw.head(n).iterrows():
        print(f"  {int(row['min_impulse']):>7} {int(row['max_dist']):>7} "
              f"{row['bb_filter']:>5} {row['tp_mult']:>5.1f} "
              f"{row['pair_set']:>10} {int(row['n_signals']):>6} {int(row['N']):>5} "
              f"{row['WR']:>5.1f}% {row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} "
              f"{row['PF']:>5.2f} {row['AvgW']:>+6.3f}R {row['AvgL']:>+6.3f}R")


def print_best_per_set(df_sw):
    print(f"\n  Best config per pair-set:")
    print(f"  {'pair_set':>10} {'min_imp':>7} {'max_dis':>7} {'bb':>5} {'tp_m':>5} "
          f"{'N_sig':>6} {'N_tr':>5} {'WR%':>6} {'NetR':>8} {'R/mo':>6} {'PF':>5}")
    print(f"  {'-'*85}")
    for ps, grp in df_sw.groupby('pair_set'):
        row = grp.nlargest(1, 'NetR').iloc[0]
        print(f"  {ps:>10} {int(row['min_impulse']):>7} {int(row['max_dist']):>7} "
              f"{row['bb_filter']:>5} {row['tp_mult']:>5.1f} "
              f"{int(row['n_signals']):>6} {int(row['N']):>5} "
              f"{row['WR']:>5.1f}% {row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} {row['PF']:>5.2f}")


def print_rr_table(df_sw, best_row):
    mg = int(best_row['min_impulse']); md = int(best_row['max_dist'])
    bb = best_row['bb_filter'];        ps = best_row['pair_set']
    sub = df_sw[(df_sw['min_impulse']==mg) & (df_sw['max_dist']==md)
                & (df_sw['bb_filter']==bb) & (df_sw['pair_set']==ps)
               ].sort_values('tp_mult')
    print(f"\n  R:R sensitivity (min_imp={mg}, max_dist={md}, bb={bb}, pairs={ps}):")
    print(f"  {'tp_mult':>7} {'N_sig':>6} {'N_tr':>5} {'WR%':>6} "
          f"{'NetR':>8} {'R/mo':>6} {'PF':>5} {'AvgW':>7} {'AvgL':>7}")
    print(f"  {'-'*63}")
    for _, row in sub.iterrows():
        print(f"  {row['tp_mult']:>7.1f} {int(row['n_signals']):>6} {int(row['N']):>5} "
              f"{row['WR']:>5.1f}% {row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} "
              f"{row['PF']:>5.2f} {row['AvgW']:>+6.3f}R {row['AvgL']:>+6.3f}R")


def print_dxy_summary(df_an, sig_type):
    n = len(df_an)
    if n == 0:
        return
    print(f"\n  DXY PATTERN at {sig_type} entries (n={n}):")
    is_long = 'LONG' in sig_type

    same = df_an['dxy_same_side'].sum()
    conf = df_an['dxy_confirms'].sum()
    bb4f = (df_an['dxy_bb4_flat'] == 1).sum()
    side_str = 'below London open' if is_long else 'above London open'

    med_dist = df_an['dxy_dist_pts'].median()
    med_imp  = df_an['jpy_impulse'].median()

    print(f"    USDJPY median impulse at entry: {med_imp:.0f}pts")
    print(f"    DXY {side_str}: {same}/{n} ({same/n*100:.0f}%)  "
          f"median DXY dist: {med_dist:+.0f}pts")
    print(f"    DXY BB4 flat: {bb4f}/{n} ({bb4f/n*100:.0f}%)")
    print(f"    DXY confirms (same side + matching candle): {conf}/{n} ({conf/n*100:.0f}%)")

    print(f"\n    USDJPY outcome split by DXY confirmation:")
    for cv_ in [True, False]:
        sub = df_an[df_an['dxy_confirms'] == cv_]
        if len(sub) == 0: continue
        wins = (sub['jpy_indicator_out'] == 'win').sum()
        print(f"      DXY confirms={str(cv_):>5}: N={len(sub):>3}  "
              f"wins={wins:>2}  WR={wins/len(sub)*100:.0f}%")

    # impulse distribution of winners vs losers
    wins_df   = df_an[df_an['jpy_indicator_out'] == 'win']
    losses_df = df_an[df_an['jpy_indicator_out'] == 'loss']
    if len(wins_df) and len(losses_df):
        print(f"\n    USDJPY impulse — winners: {wins_df['jpy_impulse'].mean():.0f}pts avg  "
              f"losers: {losses_df['jpy_impulse'].mean():.0f}pts avg")
        print(f"    USDJPY dist at entry — winners: {wins_df['jpy_dist'].abs().mean():.0f}pts avg  "
              f"losers: {losses_df['jpy_dist'].abs().mean():.0f}pts avg")


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
        pair_dfs[f'_{pair}_idx'] = {str(t): i for i, t in enumerate(dfp['time'])}

    print(f"  Loaded {len(df_jpy):,} USDJPY bars, {len(df_dxy):,} DXY bars, "
          f"{months:.1f} months")

    # DXY baseline
    df_base = pd.read_csv(BASE / 'combined_trade_log.csv')
    for stype in ['REV_LONG', 'REV_SHORT']:
        sub = df_base[df_base['signal'] == stype]
        n   = len(sub)
        if n == 0:
            print(f"  DXY baseline {stype}: no trades")
            continue
        wins = (sub['r_actual'] > 0).sum()
        print(f"  DXY baseline {stype}: N={n}, WR={wins/n*100:.1f}%, "
              f"NetR={sub['r_actual'].sum():+.1f}R")

    # Part 1: collect raw candidates
    print("\nCollecting raw REV candidates on USDJPY (loose thresholds)...")
    candidates = collect_rev_candidates(df_jpy, news_dates)

    long_c  = [c for c in candidates if c['type'] == 'REV_LONG']
    short_c = [c for c in candidates if c['type'] == 'REV_SHORT']
    print(f"  Total: {len(candidates)}  (LONG={len(long_c)}, SHORT={len(short_c)})")

    for label, cands in [('LONG', long_c), ('SHORT', short_c)]:
        if not cands: continue
        imp_key = 'max_dn' if label == 'LONG' else 'max_up'
        imps  = [c[imp_key] for c in cands]
        dists = [abs(c['dist']) for c in cands]
        sls   = [c['sl_pts'] for c in cands]
        bb4f  = sum(1 for c in cands if c['bb4_flat'] == 1)
        pins  = sum(1 for c in cands if c['is_pin'])
        engs  = sum(1 for c in cands if c['is_eng'])
        print(f"  {label}: impulse {min(imps):.0f}-{max(imps):.0f}pts (mean {np.mean(imps):.0f})  "
              f"dist {min(dists):.0f}-{max(dists):.0f}pts (mean {np.mean(dists):.0f})  "
              f"SL {min(sls):.0f}-{max(sls):.0f}pts  "
              f"bb4flat={bb4f}/{len(cands)}  pin={pins}  eng={engs}")

    # Part 2: sweeps
    best_long = best_short = None

    print()
    print("=" * 100)
    print("  REV_LONG SWEEP (USDJPY indicator)")
    print("=" * 100)
    df_long = run_sweep(candidates, 'REV_LONG', df_jpy, pair_dfs, months)
    if not df_long.empty:
        print_top(df_long, 15, 'Top 15 by Net R:')
        print_best_per_set(df_long)
        valid = df_long[df_long['n_signals'] >= 5]
        best_long = valid.nlargest(1, 'NetR').iloc[0] if len(valid) else df_long.iloc[0]
        print_rr_table(df_long, best_long)
        df_long.to_csv(BASE / 'rev_long_sweep.csv', index=False)
    else:
        print("  No positive results found for REV_LONG")

    print()
    print("=" * 100)
    print("  REV_SHORT SWEEP (USDJPY indicator)")
    print("=" * 100)
    df_short = run_sweep(candidates, 'REV_SHORT', df_jpy, pair_dfs, months)
    if not df_short.empty:
        print_top(df_short, 15, 'Top 15 by Net R:')
        print_best_per_set(df_short)
        valid = df_short[df_short['n_signals'] >= 5]
        best_short = valid.nlargest(1, 'NetR').iloc[0] if len(valid) else df_short.iloc[0]
        print_rr_table(df_short, best_short)
        df_short.to_csv(BASE / 'rev_short_sweep.csv', index=False)
    else:
        print("  No positive results found for REV_SHORT")

    # Part 3: DXY state analysis
    print()
    print("=" * 100)
    print("  DXY STATE ANALYSIS")
    print("=" * 100)

    all_dxy = []
    for sig_type, df_sw, best_row in [
            ('REV_LONG',  df_long,  best_long),
            ('REV_SHORT', df_short, best_short)]:
        if best_row is None:
            continue
        direction = 'long' if 'LONG' in sig_type else 'short'
        imp_key   = 'max_dn' if direction == 'long' else 'max_up'
        abs_dist_fn = (lambda c: -c['dist']) if direction == 'long' else (lambda c: c['dist'])

        opt_cands = [c for c in candidates
                     if c['type'] == sig_type
                     and c[imp_key]    >= int(best_row['min_impulse'])
                     and abs_dist_fn(c) <= int(best_row['max_dist'])
                     and (best_row['bb_filter'] == 'any' or c['bb4_flat'] == 1)]
        for sig in opt_cands:
            eb, oc = _find_exit(df_jpy, sig['entry_bar'], sig['entry'],
                                sig['sl_pts'] / 10000, direction, float(best_row['tp_mult']))
            sig['exit_bar'] = eb
            sig['exit_time'] = str(df_jpy.at[eb, 'time'])
            sig['indicator_outcome'] = oc

        df_an = analyse_dxy_at_signals(opt_cands, df_dxy)
        print_dxy_summary(df_an, sig_type)
        df_an['sig_type'] = sig_type
        all_dxy.append(df_an)

    if all_dxy:
        df_all = pd.concat(all_dxy, ignore_index=True)
        df_all.to_csv(BASE / 'rev_dxy_analysis.csv', index=False)
        print(f"\n  Saved rev_dxy_analysis.csv ({len(df_all)} rows)")

    # Final summary vs DXY baseline
    print()
    print("=" * 100)
    print("  SUMMARY vs DXY BASELINE")
    print("=" * 100)
    print(f"  {'Signal':20} {'DXY NetR':>10} {'JPY NetR':>10} {'Improvement':>12}")
    print(f"  {'-'*55}")
    dxy_rev_long  = df_base[df_base['signal']=='REV_LONG' ]['r_actual'].sum()
    dxy_rev_short = df_base[df_base['signal']=='REV_SHORT']['r_actual'].sum()
    for sig, dxy_r, df_sw in [('REV_LONG', dxy_rev_long, df_long),
                                ('REV_SHORT', dxy_rev_short, df_short)]:
        best_r = df_sw.iloc[0]['NetR'] if not df_sw.empty else 0.0
        delta  = best_r - dxy_r
        print(f"  {sig:<20} {dxy_r:>+9.1f}R {best_r:>+9.1f}R {delta:>+11.1f}R")
