"""
optimize_attr_core_usdjpy.py
============================
Tests ATTR_CORE_LONG and ATTR_CORE_SHORT using USDJPY as the indicator.

Part 1 — collect raw ATTR_CORE candidates from USDJPY with full metadata
          (loose thresholds so the sweep can explore freely)
Part 2 — sweep filter parameters and TP multiplier independently for LONG/SHORT
Part 3 — analyse DXY state at optimal signal bars to find DXY equivalent trigger

Compares every result to the DXY baseline from combined_trade_log.csv.
"""

import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product

import dxy_improved_rules as imp
import dxy_clean_rules    as r

BASE      = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
MAX_BARS  = 500

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
ZONE_MIN_GAP  = 30


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
    if direction == 'long':
        tp_px = entry_px + sl_d * rr;  sl_px = entry_px - sl_d
    else:
        tp_px = entry_px - sl_d * rr;  sl_px = entry_px + sl_d
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
# PART 1: COLLECT RAW ATTR_CORE CANDIDATES (loose thresholds)
# ══════════════════════════════════════════════════════════════════════════════

def collect_attr_core_candidates(df_src, news_dates):
    """
    Scans df_src for all ATTR_CORE_LONG and ATTR_CORE_SHORT candidates.
    Stores full metadata for sweep filtering. Very loose thresholds here —
    filter parameters are applied in the sweep.
    """
    df = df_src.copy().reset_index(drop=True)
    bull_pin, bear_pin = _pin_series(df)
    bull_eng, bear_eng = _engulf_3bar(df)
    bull_sig = (bull_pin | bull_eng).fillna(False)
    bear_sig = (bear_pin | bear_eng).fillna(False)

    _, bb4_flat = imp.compute_bb_regime(df, 4)
    _, bb1_flat = imp.compute_bb_regime(df, 1)
    bb4f_arr = bb4_flat.values
    bb1f_arr = bb1_flat.values

    LON_H, LON_M = 7, 0
    MON_H, MON_M = 6, 30
    ATTR_WINDOW  = (6*60, 19*60+30)

    # zone state
    ac_zone_top = ac_zone_bot = np.nan
    ac_japan_bull = False
    ac_pristine = ac_traded = False
    ac_in_trade_until = -1
    ac_lon_close = np.nan
    ac_gap_pts   = 0.0
    ac_zone_width = 0.0

    lon_px = np.nan
    candidates = []

    for i in range(2, len(df)):
        row = df.iloc[i]
        cv, ov = row['close'], row['open']
        hv, lv = row['high'], row['low']
        ts     = row['time']
        hh, mm = ts.hour, ts.minute
        cm     = hh * 60 + mm
        dow    = ts.dayofweek
        in_jpn = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)
        is_lon = (not in_jpn and hh == LON_H and mm == LON_M and dow != 0)
        is_mon = (not in_jpn and hh == MON_H and mm == MON_M and dow == 0)
        is_2345 = (hh == 23 and mm == 45)

        # ── Zone formation at 23:45 ────────────────────────────────────────
        if is_2345:
            pb = abs(df.at[i-1,'close'] - df.at[i-1,'open']) * 10000
            pc = df.at[i-2,'close'] if (pb < 10 and i >= 2) else df.at[i-1,'close']
            jo, jc = ov, cv
            gap = abs(pc - jo) * 10000
            if gap >= ZONE_MIN_GAP:
                ac_zone_top   = max(pc, jo)
                ac_zone_bot   = min(pc, jo)
                ac_japan_bull = (jo > pc)
            else:
                ac_zone_top   = max(jo, jc)
                ac_zone_bot   = min(jo, jc)
                ac_japan_bull = (jc > jo)
            if abs(ac_zone_top - ac_zone_bot) * 10000 < 1:
                ac_zone_top = max(jo, jc) + 0.001
            ac_zone_width = (ac_zone_top - ac_zone_bot) * 10000
            ac_pristine = ac_traded = False
            ac_lon_close = np.nan; ac_gap_pts = 0.0
            continue

        # ── London open: assess gap from zone ─────────────────────────────
        if is_lon or is_mon:
            lon_px = ov
            if not np.isnan(ac_zone_top):
                if not ac_japan_bull:
                    # bearish Japan → zone is overhead → need price BELOW zone_bot
                    gap_below = (ac_zone_bot - cv) * 10000
                    ac_pristine = (gap_below > 0)      # any gap, even tiny
                    ac_gap_pts  = gap_below
                else:
                    # bullish Japan → zone is below → need price ABOVE zone_top
                    gap_above = (cv - ac_zone_top) * 10000
                    ac_pristine = (gap_above > 0)
                    ac_gap_pts  = gap_above
                if ac_pristine:
                    ac_lon_close = cv
            continue

        if np.isnan(lon_px) or in_jpn or np.isnan(ac_zone_top):
            continue

        # update pristine: if price touches the zone, mark as not pristine
        if not ac_japan_bull:
            if hv >= ac_zone_bot: ac_pristine = False
        else:
            if lv <= ac_zone_top: ac_pristine = False

        if not ac_pristine or ac_traded or i <= ac_in_trade_until:
            continue

        in_attr = (ATTR_WINDOW[0] <= cm <= ATTR_WINDOW[1])
        if not in_attr: continue

        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        approach_3 = abs((cv - df.at[i-3,'close']) * 10000) if i >= 3 else 0
        dist_from_lon = (cv - lon_px) * 10000

        # ── ATTR_CORE_LONG: Japan bearish, price below zone, bullish candle ─
        if (not ac_japan_bull and bull_sig.at[i]):
            reward_to_top = (ac_zone_top - cv) * 10000
            wave_ext = max((ac_lon_close - cv) * 10000, 0.0) if not np.isnan(ac_lon_close) else 0.0
            if reward_to_top > 0:
                sl_d = reward_to_top / 10000   # natural SL = same dist as TP
                candidates.append({
                    'type':           'ATTR_CORE_LONG',
                    'direction':      'long',
                    'entry_time':     str(ts),
                    'entry_bar':      i,
                    'entry':          round(cv, 5),
                    'sl_pts':         round(sl_d * 10000),
                    'zone_top':       round(ac_zone_top, 5),
                    'zone_bot':       round(ac_zone_bot, 5),
                    'zone_width':     round(ac_zone_width),
                    'gap_at_lon':     round(ac_gap_pts),     # gap from zone at London open
                    'approach_3':     round(approach_3),      # rise in last 3 bars
                    'wave_ext':       round(wave_ext),         # extension below London close
                    'dist_from_lon':  round(dist_from_lon),   # pos=above, neg=below London open
                    'bb4_flat':       int(bb4f_arr[i]),
                    'bb1_flat':       int(bb1f_arr[i]),
                    'is_pin':         bool(bull_pin.iat[i]),
                    'is_eng':         bool(bull_eng.iat[i]),
                    'dow':            dow,
                    'lon_open':       round(lon_px, 5),
                })
                ac_traded = True
                eb, _ = _find_exit(df, i, cv, sl_d, 'long', 1.0)
                ac_in_trade_until = eb

        # ── ATTR_CORE_SHORT: Japan bullish, price above zone, bearish candle ─
        elif (ac_japan_bull and bear_sig.at[i]):
            reward_to_bot = (cv - ac_zone_bot) * 10000
            wave_ext = max((cv - ac_lon_close) * 10000, 0.0) if not np.isnan(ac_lon_close) else 0.0
            if reward_to_bot > 0:
                sl_d = reward_to_bot / 10000
                candidates.append({
                    'type':           'ATTR_CORE_SHORT',
                    'direction':      'short',
                    'entry_time':     str(ts),
                    'entry_bar':      i,
                    'entry':          round(cv, 5),
                    'sl_pts':         round(sl_d * 10000),
                    'zone_top':       round(ac_zone_top, 5),
                    'zone_bot':       round(ac_zone_bot, 5),
                    'zone_width':     round(ac_zone_width),
                    'gap_at_lon':     round(ac_gap_pts),
                    'approach_3':     round(approach_3),
                    'wave_ext':       round(wave_ext),
                    'dist_from_lon':  round(dist_from_lon),
                    'bb4_flat':       int(bb4f_arr[i]),
                    'bb1_flat':       int(bb1f_arr[i]),
                    'is_pin':         bool(bear_pin.iat[i]),
                    'is_eng':         bool(bear_eng.iat[i]),
                    'dow':            dow,
                    'lon_open':       round(lon_px, 5),
                })
                ac_traded = True
                eb, _ = _find_exit(df, i, cv, sl_d, 'short', 1.0)
                ac_in_trade_until = eb

    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# PART 2: PAIR APPLICATION & SWEEP
# ══════════════════════════════════════════════════════════════════════════════

def _apply_to_pairs(sigs, pair_dfs):
    """Apply signals (with pre-resolved exit_bar/exit_time) to pairs. Returns flat list of trade dicts."""
    rows = []
    for sig in sigs:
        pairs = PAIR_SETS.get('all7', ALL_PAIRS)   # always compute all, filter per sweep
        for pair in ALL_PAIRS:
            df_p   = pair_dfs[pair]
            pidx   = pair_dfs[f'_{pair}_idx']
            et, xt = sig['entry_time'], sig.get('exit_time')
            if et not in pidx or not xt or xt not in pidx: continue
            pi, xi = pidx[et], pidx[xt]
            n      = len(df_p)
            pc     = df_p.at[pi, 'close']
            # indicator direction × pair_dir → pair trade direction
            ind_long  = (sig['direction'] == 'long')
            pair_long = (ind_long and PAIR_DIR[pair] == 1) or (not ind_long and PAIR_DIR[pair] == -1)
            pair_sl_d = sig['sl_pts'] / 10000 * PAIR_FACTOR[pair]
            if pair_sl_d <= 0: continue
            pair_sl_px = pc - pair_sl_d if pair_long else pc + pair_sl_d
            r_actual = None
            for j in range(pi + 1, min(xi + 1, n)):
                if pair_long  and df_p.at[j,'low']  <= pair_sl_px: r_actual = -1.0; break
                if not pair_long and df_p.at[j,'high'] >= pair_sl_px: r_actual = -1.0; break
            if r_actual is None:
                px = df_p.at[min(xi, n-1), 'close']
                raw = (px - pc) if pair_long else (pc - px)
                r_actual = raw / pair_sl_d
            rows.append({
                'sig_type':    sig['type'],
                'entry_time':  et,
                'pair':        pair,
                'r_actual':    round(r_actual, 3),
                'gap_at_lon':  sig['gap_at_lon'],
                'approach_3':  sig['approach_3'],
                'wave_ext':    sig['wave_ext'],
                'bb4_flat':    sig['bb4_flat'],
                'tp_mult':     sig.get('tp_mult', 1.0),
                'indicator_outcome': sig.get('indicator_outcome', ''),
            })
    return rows


def _stats(r_vals, months):
    if not r_vals: return None
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
                rpm=round(net/months,2), PF=round(pf,2), AvgW=round(aw,3), AvgL=round(al,3))


def run_sweep(candidates, sig_type, df_ind, pair_dfs, months):
    """
    Sweep filter parameters for one signal type (LONG or SHORT).
    Returns DataFrame of results sorted by NetR.
    """
    # Parameter grid
    min_gap_grid      = [0, 50, 75, 150]        # min gap from zone at London open
    min_approach_grid = [0, 75, 150]             # min approach in 3 bars
    max_wave_grid     = [500, 1500, 5000]        # max wave extension
    bb_filter_grid    = ['any', 'flat']          # BB4 regime
    tp_mult_grid      = [1.0, 1.5, 2.0, 2.5, 3.0]
    pair_set_grid     = list(PAIR_SETS.keys())

    cands = [c for c in candidates if c['type'] == sig_type]
    direction = 'long' if 'LONG' in sig_type else 'short'
    total = (len(min_gap_grid)*len(min_approach_grid)*len(max_wave_grid)
             *len(bb_filter_grid)*len(tp_mult_grid)*len(pair_set_grid))
    print(f"  {sig_type}: {len(cands)} raw candidates, "
          f"sweeping {total} combinations...")

    results = []
    for min_gap, min_app, max_wave, bb_f, tp_m, ps_key in product(
            min_gap_grid, min_approach_grid, max_wave_grid,
            bb_filter_grid, tp_mult_grid, pair_set_grid):

        filt = [c for c in cands
                if c['gap_at_lon']  >= min_gap
                and c['approach_3'] >= min_app
                and c['wave_ext']   <= max_wave
                and (bb_f == 'any' or c['bb4_flat'] == 1)]

        if len(filt) < 2: continue

        # resolve exits at this TP multiplier
        sigs = []
        for c in filt:
            eb, oc = _find_exit(df_ind, c['entry_bar'], c['entry'],
                                c['sl_pts'] / 10000, direction, tp_m)
            sigs.append({**c, 'exit_bar': eb,
                          'exit_time': str(df_ind.at[eb, 'time']),
                          'indicator_outcome': oc,
                          'tp_mult': tp_m})

        # apply to pairs in this set
        pairs = PAIR_SETS[ps_key]
        r_vals = []
        for sig in sigs:
            for pair in pairs:
                df_p  = pair_dfs[pair]
                pidx  = pair_dfs[f'_{pair}_idx']
                et, xt = sig['entry_time'], sig['exit_time']
                if et not in pidx or xt not in pidx: continue
                pi, xi = pidx[et], pidx[xt]
                n   = len(df_p)
                pc  = df_p.at[pi, 'close']
                ind_long  = (direction == 'long')
                pair_long = (ind_long and PAIR_DIR[pair] == 1) or (not ind_long and PAIR_DIR[pair] == -1)
                psl = sig['sl_pts'] / 10000 * PAIR_FACTOR[pair]
                if psl <= 0: continue
                psl_px = pc - psl if pair_long else pc + psl
                rv = None
                for j in range(pi+1, min(xi+1, n)):
                    if pair_long  and df_p.at[j,'low']  <= psl_px: rv = -1.0; break
                    if not pair_long and df_p.at[j,'high'] >= psl_px: rv = -1.0; break
                if rv is None:
                    px = df_p.at[min(xi, n-1), 'close']
                    raw = (px - pc) if pair_long else (pc - px)
                    rv = raw / psl
                r_vals.append(round(rv, 3))

        st = _stats(r_vals, months)
        if st is None: continue
        results.append({
            'sig_type': sig_type, 'min_gap': min_gap,
            'min_approach': min_app, 'max_wave': max_wave,
            'bb_filter': bb_f, 'tp_mult': tp_m, 'pair_set': ps_key,
            'n_signals': len(filt), **st
        })

    df = pd.DataFrame(results).sort_values('NetR', ascending=False).reset_index(drop=True)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# PART 3: DXY STATE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def analyse_dxy_at_signals(sigs, df_dxy):
    """
    For each signal entry time, characterise DXY state:
    - Gap from Japan zone (does DXY have its own ATTR_CORE zone?)
    - DXY price vs London open
    - DXY BB4 flat regime
    - Bearish/bullish candle on DXY at that bar
    """
    df_dxy = df_dxy.copy().reset_index(drop=True)
    df_dxy['time'] = pd.to_datetime(df_dxy['time'], utc=True)
    didx = {str(t): i for i, t in enumerate(df_dxy['time'])}

    # compute DXY ATTR_CORE zones (same logic as USDJPY scanner but on DXY)
    dxy_zone_by_date = {}   # date -> {top, bot, japan_bull, lon_gap}
    dxy_lon_px_by_date = {}

    _, bb4f_dxy = imp.compute_bb_regime(df_dxy, 4)
    bull_pin_d, bear_pin_d = _pin_series(df_dxy)

    ac_zone_top_d = ac_zone_bot_d = np.nan
    ac_japan_bull_d = False
    ac_lon_close_d  = np.nan

    for i in range(2, len(df_dxy)):
        ts  = df_dxy.at[i, 'time']
        ov  = df_dxy.at[i, 'open']
        cv  = df_dxy.at[i, 'close']
        hh, mm = ts.hour, ts.minute
        dow  = ts.dayofweek
        in_jpn = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)
        is_lon = (not in_jpn and hh == 7 and mm == 0 and dow != 0)
        is_mon = (not in_jpn and hh == 6 and mm == 30 and dow == 0)
        is_2345 = (hh == 23 and mm == 45)

        if is_2345:
            pb = abs(df_dxy.at[i-1,'close'] - df_dxy.at[i-1,'open']) * 10000
            pc = df_dxy.at[i-2,'close'] if (pb < 10 and i >= 2) else df_dxy.at[i-1,'close']
            jo, jc = ov, cv
            gap = abs(pc - jo) * 10000
            if gap >= ZONE_MIN_GAP:
                ac_zone_top_d = max(pc, jo); ac_zone_bot_d = min(pc, jo)
                ac_japan_bull_d = (jo > pc)
            else:
                ac_zone_top_d = max(jo, jc); ac_zone_bot_d = min(jo, jc)
                ac_japan_bull_d = (jc > jo)
            dxy_zone_by_date[ts.date()] = {
                'top': ac_zone_top_d, 'bot': ac_zone_bot_d,
                'japan_bull': ac_japan_bull_d
            }
            ac_lon_close_d = np.nan

        if is_lon or is_mon:
            dxy_lon_px_by_date[ts.date()] = ov
            if not np.isnan(ac_zone_top_d):
                if not ac_japan_bull_d:
                    gap_d = (ac_zone_bot_d - cv) * 10000
                else:
                    gap_d = (cv - ac_zone_top_d) * 10000
                zone_d = dxy_zone_by_date.get(ts.date())
                if zone_d: zone_d['lon_gap'] = round(gap_d, 1)
                ac_lon_close_d = cv

    # now read DXY state for each signal entry
    rows = []
    for sig in sigs:
        et = sig['entry_time']
        if et not in didx: continue
        j  = didx[et]
        ts = df_dxy.at[j, 'time']
        cv_d = df_dxy.at[j, 'close']
        d    = ts.date()

        dxy_lon   = dxy_lon_px_by_date.get(d, np.nan)
        dxy_vs_lon = (cv_d - dxy_lon) * 10000 if not np.isnan(dxy_lon) else np.nan
        dxy_zone  = dxy_zone_by_date.get(d)
        bb4f_val  = int(bb4f_dxy.at[j]) if j < len(bb4f_dxy) else np.nan
        is_bull_can = bool(bull_pin_d.iat[j]) if j < len(bull_pin_d) else False
        is_bear_can = bool(bear_pin_d.iat[j]) if j < len(bear_pin_d) else False

        # Does DXY have an equivalent ATTR_CORE zone setup?
        dxy_has_zone  = dxy_zone is not None
        dxy_japan_bull = dxy_zone['japan_bull'] if dxy_has_zone else np.nan
        dxy_lon_gap   = dxy_zone.get('lon_gap', np.nan) if dxy_has_zone else np.nan

        # Same direction? (ATTR_CORE_LONG on USDJPY = USDJPY bullish → DXY also bullish?)
        jpy_is_long = (sig['direction'] == 'long')
        # For LONG (USDJPY rising), DXY should also be rising (Japan bearish zone overhead,
        # price below, DXY approaching from below = same as USDJPY).
        # For SHORT, DXY Japan zone should be bullish with price above.
        dxy_zone_matches = (
            dxy_has_zone and not np.isnan(dxy_japan_bull) and
            (jpy_is_long == (not dxy_japan_bull))   # LONG → DXY also has bearish Japan zone
        ) if dxy_has_zone else False

        rows.append({
            'entry_time':        et,
            'sig_type':          sig['type'],
            'jpy_gap_at_lon':    sig.get('gap_at_lon'),
            'jpy_approach':      sig.get('approach_3'),
            'jpy_wave_ext':      sig.get('wave_ext'),
            'jpy_bb4_flat':      sig.get('bb4_flat'),
            'jpy_indicator_out': sig.get('indicator_outcome', ''),
            'dxy_at_entry':      round(cv_d, 5),
            'dxy_vs_lon_pts':    round(dxy_vs_lon, 1) if not np.isnan(dxy_vs_lon) else np.nan,
            'dxy_bb4_flat':      bb4f_val,
            'dxy_bull_can':      is_bull_can,
            'dxy_bear_can':      is_bear_can,
            'dxy_has_zone':      dxy_has_zone,
            'dxy_japan_bull':    dxy_japan_bull,
            'dxy_lon_gap':       dxy_lon_gap,
            'dxy_zone_matches':  dxy_zone_matches,
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# PRINT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def print_top(df_sweep, n=15, title=''):
    if title: print(f"\n  {title}")
    print(f"  {'min_gap':>7} {'min_app':>7} {'max_wav':>7} {'bb':>5} "
          f"{'tp_m':>5} {'pairs':>10} {'N_sig':>6} {'N_tr':>5} "
          f"{'WR%':>6} {'NetR':>8} {'R/mo':>6} {'PF':>5} {'AvgW':>7} {'AvgL':>7}")
    print(f"  {'-'*97}")
    for _, row in df_sweep.head(n).iterrows():
        print(f"  {int(row['min_gap']):>7} {int(row['min_approach']):>7} "
              f"{int(row['max_wave']):>7} {row['bb_filter']:>5} {row['tp_mult']:>5.1f} "
              f"{row['pair_set']:>10} {int(row['n_signals']):>6} {int(row['N']):>5} "
              f"{row['WR']:>5.1f}% {row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} "
              f"{row['PF']:>5.2f} {row['AvgW']:>+6.3f}R {row['AvgL']:>+6.3f}R")


def print_best_per_set(df_sweep):
    print(f"\n  Best config per pair-set:")
    print(f"  {'pair_set':>11} {'min_gap':>7} {'min_app':>7} {'max_wav':>7} "
          f"{'bb':>5} {'tp_m':>5} {'N_sig':>6} {'N_tr':>5} "
          f"{'WR%':>6} {'NetR':>8} {'R/mo':>6} {'PF':>5}")
    print(f"  {'-'*88}")
    for ps, grp in df_sweep.groupby('pair_set'):
        row = grp.nlargest(1,'NetR').iloc[0]
        print(f"  {ps:>11} {int(row['min_gap']):>7} {int(row['min_approach']):>7} "
              f"{int(row['max_wave']):>7} {row['bb_filter']:>5} {row['tp_mult']:>5.1f} "
              f"{int(row['n_signals']):>6} {int(row['N']):>5} "
              f"{row['WR']:>5.1f}% {row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} {row['PF']:>5.2f}")


def print_rr_sensitivity(df_sweep, best_row):
    mg = int(best_row['min_gap']); ma = int(best_row['min_approach'])
    mw = int(best_row['max_wave']); bb = best_row['bb_filter']
    ps = best_row['pair_set']
    sub = df_sweep[(df_sweep['min_gap']==mg) & (df_sweep['min_approach']==ma)
                   & (df_sweep['max_wave']==mw) & (df_sweep['bb_filter']==bb)
                   & (df_sweep['pair_set']==ps)].sort_values('tp_mult')
    print(f"\n  R:R sensitivity (min_gap={mg}, min_app={ma}, max_wav={mw}, "
          f"bb={bb}, pairs={ps}):")
    print(f"  {'tp_mult':>7} {'N_sig':>6} {'N_tr':>5} {'WR%':>6} "
          f"{'NetR':>8} {'R/mo':>6} {'PF':>5} {'AvgW':>7} {'AvgL':>7}")
    print(f"  {'-'*63}")
    for _, row in sub.iterrows():
        print(f"  {row['tp_mult']:>7.1f} {int(row['n_signals']):>6} {int(row['N']):>5} "
              f"{row['WR']:>5.1f}% {row['NetR']:>+8.1f}R {row['rpm']:>+5.2f} "
              f"{row['PF']:>5.2f} {row['AvgW']:>+6.3f}R {row['AvgL']:>+6.3f}R")


def print_dxy_summary(df_dxy_an, sig_type):
    n = len(df_dxy_an)
    if n == 0: return
    print(f"\n  DXY PATTERN at {sig_type} entries (n={n}):")

    above = (df_dxy_an['dxy_vs_lon_pts'] > 0).sum()
    below = n - above
    med   = df_dxy_an['dxy_vs_lon_pts'].median()
    print(f"    DXY above London open: {above}/{n} ({above/n*100:.0f}%)  "
          f"below: {below}/{n} ({below/n*100:.0f}%)  median={med:+.0f}pts")

    bb_flat = (df_dxy_an['dxy_bb4_flat'] == 1).sum()
    print(f"    DXY BB4 flat: {bb_flat}/{n} ({bb_flat/n*100:.0f}%)")

    zone_match = df_dxy_an['dxy_zone_matches'].sum()
    print(f"    DXY has matching ATTR_CORE zone same dir: {zone_match}/{n} ({zone_match/n*100:.0f}%)")

    # candle match
    if 'LONG' in sig_type:
        can_match = df_dxy_an['dxy_bull_can'].sum()
        print(f"    DXY bullish candle at entry: {can_match}/{n} ({can_match/n*100:.0f}%)")
        # both zone and candle
        both = (df_dxy_an['dxy_zone_matches'] & df_dxy_an['dxy_bull_can']).sum()
    else:
        can_match = df_dxy_an['dxy_bear_can'].sum()
        print(f"    DXY bearish candle at entry: {can_match}/{n} ({can_match/n*100:.0f}%)")
        both = (df_dxy_an['dxy_zone_matches'] & df_dxy_an['dxy_bear_can']).sum()
    print(f"    DXY zone + candle (both conditions): {both}/{n} ({both/n*100:.0f}%)")

    # lon_gap on DXY
    valid_gap = df_dxy_an['dxy_lon_gap'].dropna()
    if len(valid_gap):
        pos_gap = (valid_gap > 10).sum()
        print(f"    DXY had positive London gap (>10pts): {pos_gap}/{len(valid_gap)} "
              f"({pos_gap/len(valid_gap)*100:.0f}% of days with zone data)")

    # outcomes split by DXY zone match
    print(f"\n    USDJPY outcome by DXY zone match:")
    for zm in [True, False]:
        sub = df_dxy_an[df_dxy_an['dxy_zone_matches'] == zm]
        if len(sub) == 0: continue
        wins = (sub['jpy_indicator_out'] == 'win').sum()
        print(f"      DXY match={str(zm):>5}: N={len(sub):>2}  wins={wins:>2}  "
              f"WR={wins/len(sub)*100:.0f}%")


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

    # ── DXY baseline ──────────────────────────────────────────────────────
    df_base = pd.read_csv(BASE / 'combined_trade_log.csv')
    for stype in ['ATTR_CORE_LONG','ATTR_CORE_SHORT']:
        sub = df_base[df_base['signal'] == stype]
        n   = len(sub); wins = (sub['r_actual'] > 0).sum()
        print(f"  DXY baseline {stype}: N={n}, WR={wins/n*100:.1f}%, "
              f"NetR={sub['r_actual'].sum():+.1f}R")

    # ── Part 1: collect raw ATTR_CORE candidates on USDJPY ────────────────
    print("\nCollecting raw ATTR_CORE candidates on USDJPY...")
    candidates = collect_attr_core_candidates(df_jpy, news_dates)

    long_cands  = [c for c in candidates if c['type'] == 'ATTR_CORE_LONG']
    short_cands = [c for c in candidates if c['type'] == 'ATTR_CORE_SHORT']
    print(f"  Total raw: {len(candidates)}  "
          f"(LONG={len(long_cands)}, SHORT={len(short_cands)})")

    for label, cands in [('LONG', long_cands), ('SHORT', short_cands)]:
        if not cands: continue
        gaps  = [c['gap_at_lon'] for c in cands]
        apps  = [c['approach_3'] for c in cands]
        waves = [c['wave_ext']   for c in cands]
        bb4f  = sum(1 for c in cands if c['bb4_flat'] == 1)
        print(f"  {label}: gap_at_lon {min(gaps):.0f}-{max(gaps):.0f}pts (mean {np.mean(gaps):.0f})  "
              f"approach {min(apps):.0f}-{max(apps):.0f}pts (mean {np.mean(apps):.0f})  "
              f"wave {min(waves):.0f}-{max(waves):.0f}pts  bb4flat={bb4f}/{len(cands)}")

    print()
    print("=" * 100)
    print("  ATTR_CORE_LONG SWEEP (USDJPY indicator)")
    print("=" * 100)
    df_long = run_sweep(candidates, 'ATTR_CORE_LONG', df_jpy, pair_dfs, months)
    print_top(df_long, 15, 'Top 15 by Net R:')
    if not df_long.empty:
        print_best_per_set(df_long)
        best_long = df_long[(df_long['n_signals'] >= 5)].nlargest(1,'NetR').iloc[0] \
                    if len(df_long[df_long['n_signals'] >= 5]) else df_long.iloc[0]
        print_rr_sensitivity(df_long, best_long)

    print()
    print("=" * 100)
    print("  ATTR_CORE_SHORT SWEEP (USDJPY indicator)")
    print("=" * 100)
    df_short = run_sweep(candidates, 'ATTR_CORE_SHORT', df_jpy, pair_dfs, months)
    print_top(df_short, 15, 'Top 15 by Net R:')
    if not df_short.empty:
        print_best_per_set(df_short)
        best_short = df_short[(df_short['n_signals'] >= 5)].nlargest(1,'NetR').iloc[0] \
                     if len(df_short[df_short['n_signals'] >= 5]) else df_short.iloc[0]
        print_rr_sensitivity(df_short, best_short)

    # ── Part 3: DXY state analysis ─────────────────────────────────────────
    print()
    print("=" * 100)
    print("  DXY STATE ANALYSIS AT OPTIMAL SIGNAL ENTRIES")
    print("=" * 100)

    all_dxy_rows = []
    for sig_type, df_sw, best_row in [
            ('ATTR_CORE_LONG',  df_long,  best_long  if not df_long.empty  else None),
            ('ATTR_CORE_SHORT', df_short, best_short if not df_short.empty else None)]:
        if best_row is None: continue
        cands_t = [c for c in candidates if c['type'] == sig_type]
        direction = 'long' if 'LONG' in sig_type else 'short'
        opt_sigs = [c for c in cands_t
                    if c['gap_at_lon']  >= int(best_row['min_gap'])
                    and c['approach_3'] >= int(best_row['min_approach'])
                    and c['wave_ext']   <= int(best_row['max_wave'])
                    and (best_row['bb_filter'] == 'any' or c['bb4_flat'] == 1)]
        for sig in opt_sigs:
            eb, oc = _find_exit(df_jpy, sig['entry_bar'], sig['entry'],
                                sig['sl_pts'] / 10000, direction, float(best_row['tp_mult']))
            sig['exit_bar'] = eb
            sig['exit_time'] = str(df_jpy.at[eb, 'time'])
            sig['indicator_outcome'] = oc
        df_dxy_an = analyse_dxy_at_signals(opt_sigs, df_dxy)
        print_dxy_summary(df_dxy_an, sig_type)
        df_dxy_an['sig_type'] = sig_type
        all_dxy_rows.append(df_dxy_an)

    if all_dxy_rows:
        df_all = pd.concat(all_dxy_rows, ignore_index=True)
        df_all.to_csv(BASE / 'attr_core_dxy_analysis.csv', index=False)
        print(f"\n  Saved attr_core_dxy_analysis.csv ({len(df_all)} rows)")

    df_long.to_csv(BASE  / 'attr_core_long_sweep.csv', index=False)
    df_short.to_csv(BASE / 'attr_core_short_sweep.csv', index=False)
    print(f"  Saved attr_core_long_sweep.csv ({len(df_long)} rows), "
          f"attr_core_short_sweep.csv ({len(df_short)} rows)")
