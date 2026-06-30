"""
test_usdjpy_as_indicator.py
===========================
Hypothesis: use USDJPY as the signal indicator instead of DXY.

All 4 signal type groups (7 directional signals total):
  LON_ATTR_LONG          — London Open Attraction (LONG only)
  GAP_REJ_LONG/SHORT     — Zone Rejection
  REV_LONG/SHORT         — Reversal
  ATTR_CORE_LONG/SHORT   — Core Attraction (Pristine Zone Approach)

All 7 pairs:
  USDJPY, USDCAD, USDCHF  — same direction as indicator (PAIR_DIR = +1)
  EURUSD, GBPUSD, AUDUSD, NZDUSD — opposite direction (PAIR_DIR = -1)

TP multiplier on the USDJPY indicator (matches DXYPairLevels v1.1):
  LON_ATTR_LONG : 2.5R  (p75 of backtest winners on USDJPY)
  all others    : 1.0R  (natural 1:1 target)

Exit mechanism:
  Pairs exit when USDJPY (the indicator) hits its TP or SL.
  Pair SL is always hard-capped at -1R.

DXY baseline: loaded from combined_trade_log.csv (all signals, all 7 pairs,
  DXY-driven exit, SL-capped at -1R, 32.4-month period).
"""

import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
from pathlib import Path
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
FILE_MAP['DXY'] = BASE / 'TVC_DXY, 15_merged.csv'

# TP multiplier for USDJPY indicator exit — matches DXYPairLevels v1.1
TP_MULT = {
    'LON_ATTR_LONG':   2.5,   # per Pine Script config
    'GAP_REJ_LONG':    1.0,
    'GAP_REJ_SHORT':   1.0,
    'REV_LONG':        1.0,
    'REV_SHORT':       1.0,
    'ATTR_CORE_LONG':  1.0,
    'ATTR_CORE_SHORT': 1.0,
}

PIN_WICK_MULT   = r.PIN_WICK_MULT
ZONE_MIN_GAP    = 30
ZONE_MIN_WIDTH  = 150
ATTR_MIN_GAP    = 75
ATTR_APPROACH   = 150
ATTR_MIN_REWARD = 100
CORE_GAP_MAX    = 1500
CORE_WAVE_MAX   = 1500


# ══════════════════════════════════════════════════════════════════════════════
# SHARED CANDLE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _pin_series(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body     = (c - o).abs()
    bt       = pd.concat([o, c], axis=1).max(axis=1)
    bb       = pd.concat([o, c], axis=1).min(axis=1)
    hi_wick  = h - bt
    lo_wick  = bb - l
    rng      = (h - l).replace(0, np.nan)
    bull_pin = (lo_wick >= body * PIN_WICK_MULT) & (lo_wick >= hi_wick * 1.5) & rng.notna()
    bear_pin = (hi_wick >= body * PIN_WICK_MULT) & (hi_wick >= lo_wick * 1.5) & rng.notna()
    both     = bull_pin & bear_pin
    bull_pin = bull_pin & ~(both & (c <= o))
    bear_pin = bear_pin & ~(both & (c >= o))
    return bull_pin, bear_pin


def _engulf_3bar(df):
    c, o = df['close'], df['open']
    body  = (c - o).abs()
    bar2r = (c.shift(2) - o.shift(2)).abs()
    indec = body.shift(1) <= bar2r * 0.5
    bull_e = ((c > o) & ~(c.shift(1) > o.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))
               & (body >= body.shift(1) * 0.8))
    bear_e = ((c < o) & ~(c.shift(1) < o.shift(1)) & (c < o.shift(1)) & (o > c.shift(1))
               & (body >= body.shift(1) * 0.8))
    bull_3b = (c.shift(2) < o.shift(2)) & indec & (c > o) & (c > o.shift(2))
    bear_3b = (c.shift(2) > o.shift(2)) & indec & (c < o) & (c < o.shift(2))
    bull = (bull_e | bull_3b).fillna(False)
    bear = (bear_e | bear_3b).fillna(False)
    return bull, bear


# ══════════════════════════════════════════════════════════════════════════════
# INDICATOR EXIT FINDER
# Scans the indicator series (USDJPY) bar-by-bar from entry to find when
# it hits TP (at rr_mult × sl_d) or SL (-1 × sl_d). Returns (exit_bar, outcome).
# ══════════════════════════════════════════════════════════════════════════════

def _find_indicator_exit(df_src, entry_bar, entry_px, sl_d, direction, rr_mult):
    """direction: 'long' or 'short'"""
    n = len(df_src)
    if direction == 'long':
        tp_px = entry_px + sl_d * rr_mult
        sl_px = entry_px - sl_d
    else:
        tp_px = entry_px - sl_d * rr_mult
        sl_px = entry_px + sl_d

    for j in range(entry_bar + 1, min(entry_bar + MAX_BARS, n)):
        o_j = df_src.at[j, 'open']
        h_j = df_src.at[j, 'high']
        l_j = df_src.at[j, 'low']
        if direction == 'long':
            if o_j <= sl_px or l_j <= sl_px:   return j, 'loss'
            if o_j >= tp_px or h_j >= tp_px:   return j, 'win'
        else:
            if o_j >= sl_px or h_j >= sl_px:   return j, 'loss'
            if o_j <= tp_px or l_j <= tp_px:   return j, 'win'

    last = min(entry_bar + MAX_BARS - 1, n - 1)
    return last, 'timeout'


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL SCANNERS  (generic — pass any OHLC DataFrame)
# ══════════════════════════════════════════════════════════════════════════════

def scan_all_signals(df_src, news_dates):
    """
    Runs all 4 signal groups on df_src (DXY or USDJPY OHLC).
    Returns list of signal dicts with entry_time, sl_pts, direction, etc.
    Does NOT set exit_time — that is set by _find_indicator_exit afterward.
    """
    df   = df_src.copy().reset_index(drop=True)
    c_s, o_s = df['close'], df['open']
    h_s, l_s = df['high'],  df['low']

    bull_pin, bear_pin = _pin_series(df)
    bull_e3,  bear_e3  = _engulf_3bar(df)
    bull_sig = (bull_pin | bull_e3).fillna(False)
    bear_sig = (bear_pin | bear_e3).fillna(False)

    bb1, _        = imp.compute_bb_regime(df, 1)
    bb4, bb4_flat = imp.compute_bb_regime(df, 4)
    df['_date']   = df['time'].dt.date
    day_grp = df.groupby('_date').agg(day_h=('high','max'), day_l=('low','min'))

    # ── session/time parameters ─────────────────────────────────────────
    LON_H, LON_M = 7, 0
    MON_H, MON_M = 6, 30
    ATTR_START_H  = 6
    REV_WINDOW_END = 12 * 60
    ATTR_WINDOW   = (6*60, 19*60+30)
    ENTRY_END     = 18 * 60

    # running state
    lon_px   = np.nan
    prev_hi  = prev_lo = np.nan
    prev_rng = 0.0
    max_up   = max_dn  = 0.0

    # LON_ATTR state
    la_zone_top = la_zone_bot = np.nan
    la_pristine = la_traded   = False

    # GAP_REJ / ATTR state
    attr_gap_pts    = 0.0
    attr_gap_target = np.nan
    attr_touched    = False
    attr_traded     = False

    # ATTR_CORE state
    ac_zone_top = ac_zone_bot = np.nan
    ac_japan_bull = False
    ac_pristine   = ac_traded = False
    ac_in_trade_until = -1
    ac_lon_close  = np.nan
    ac_gap_pts    = 0.0

    sigs = []

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
        is_2345 = (hh == 23 and mm == 45)

        # ── ATTR CORE zone at 23:45 ──────────────────────────────────────
        if is_2345:
            pb = abs(df.at[i-1,'close'] - df.at[i-1,'open']) * 10000
            pc = df.at[i-2,'close'] if (pb < 10 and i >= 2) else df.at[i-1,'close']
            jo, jc = ov, cv
            gap = abs(pc - jo) * 10000
            if gap >= ZONE_MIN_GAP:
                ac_zone_top, ac_zone_bot, ac_japan_bull = (
                    max(pc,jo), min(pc,jo), jo > pc)
            else:
                ac_zone_top, ac_zone_bot, ac_japan_bull = (
                    max(jo,jc), min(jo,jc), jc > jo)
            if abs(ac_zone_top - ac_zone_bot) * 10000 < 1:
                ac_zone_top = max(jo,jc) + 0.001
            ac_pristine = ac_traded = False
            ac_lon_close = np.nan; ac_gap_pts = 0.0

        # ── Tokyo gap state ───────────────────────────────────────────────
        if is_2345:
            attr_traded = attr_touched = False
            ref = None
            for back, off in [(2,30),(1,15)]:
                if i >= back:
                    cand = df.iloc[i - back]
                    if abs((cand['time']-(ts-pd.Timedelta(minutes=off))).total_seconds()) <= 120:
                        ref = cand['close']; break
            if ref is not None:
                raw = (ov - ref) * 10000
                if abs(raw) >= 10:
                    attr_gap_pts, attr_gap_target = raw, ref
                else:
                    attr_gap_pts, attr_gap_target = (cv - ov) * 10000, ov
            else:
                attr_gap_pts, attr_gap_target = 0.0, np.nan

        # ── London / Monday open ─────────────────────────────────────────
        if is_lon or is_mon:
            lon_px = ov
            max_up = max_dn = 0.0
            la_zone_top = max(ov, cv); la_zone_bot = min(ov, cv)
            la_pristine = True; la_traded = False
            today = ts.date()
            prior = [d for d in day_grp.index if d < today]
            if prior:
                pd_ = max(prior)
                prev_hi  = float(day_grp.at[pd_,'day_h'])
                prev_lo  = float(day_grp.at[pd_,'day_l'])
                prev_rng = (prev_hi - prev_lo) * 10000
            else:
                prev_hi = prev_lo = np.nan; prev_rng = 0.0

            # ATTR CORE: assess gap at London open
            if not np.isnan(ac_zone_top):
                if not ac_japan_bull:
                    gap = (ac_zone_bot - cv) * 10000
                    ac_pristine = (gap >= ATTR_MIN_GAP)
                else:
                    gap = (cv - ac_zone_top) * 10000
                    ac_pristine = (gap >= ATTR_MIN_GAP)
                if ac_pristine:
                    ac_lon_close = cv; ac_gap_pts = gap
            continue

        if np.isnan(lon_px) or in_jpn:
            continue

        # update pristines
        if not np.isnan(la_zone_top):
            if ov >= la_zone_top or cv >= la_zone_top:
                la_pristine = False
        if not np.isnan(attr_gap_target):
            if attr_gap_pts < 0 and cv >= attr_gap_target: attr_touched = True
            elif attr_gap_pts > 0 and cv <= attr_gap_target: attr_touched = True

        dist = (cv - lon_px) * 10000
        if not in_jpn:
            if dist > 0: max_up = max(max_up, dist)
            else:         max_dn = max(max_dn, -dist)

        # session windows
        mon_s = MON_H*60+MON_M; lon_s = LON_H*60
        rev_start  = mon_s if dow == 0 else lon_s
        attr_start = mon_s if dow == 0 else ATTR_START_H*60
        in_rev  = (rev_start  <= cm <= REV_WINDOW_END and not in_jpn)
        in_attr = (attr_start <= cm <= ATTR_WINDOW[1]  and not in_jpn)
        in_la   = (attr_start <= cm <= ENTRY_END        and not in_jpn)
        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        bv1 = int(bb1.at[i]); bv4 = int(bb4.at[i]); bv4f = int(bb4_flat.at[i])

        # ── LON_ATTR_LONG ─────────────────────────────────────────────────
        if (not la_traded and in_la and dist < 0 and la_pristine
                and bull_pin.at[i] and not np.isnan(la_zone_bot)):
            tp = la_zone_bot
            if tp > cv:
                sl_d = tp - cv
                sigs.append({
                    'type':'LON_ATTR_LONG','entry_time':str(ts),
                    'entry':round(cv,5),'sl_pts':round(sl_d*10000),
                    'direction':'long','entry_bar':i,
                    'lon_open':round(lon_px,5),
                })
                la_traded = True
                continue

        # ── REV LONG ─────────────────────────────────────────────────────
        if (in_rev and max_dn >= 0 and abs(dist) <= 250
                and bull_sig.at[i] and bv1 == 1 and not np.isnan(prev_lo)):
            sl_p = imp.get_structural_sl(prev_lo, prev_hi, cv, 'long')
            sl_d = cv - sl_p
            if 0 < sl_d <= 3000/10000*max(PAIR_FACTOR.values()):
                sl_d_pts = sl_d * 10000
                if sl_d_pts <= 3000:
                    sigs.append({
                        'type':'REV_LONG','entry_time':str(ts),
                        'entry':round(cv,5),'sl_pts':round(sl_d_pts),
                        'direction':'long','entry_bar':i,
                        'lon_open':round(lon_px,5),
                    })
                    continue

        # ── REV SHORT ────────────────────────────────────────────────────
        if (in_rev and max_up >= 0 and abs(dist) <= 250
                and bear_sig.at[i] and bv1 == -1 and not np.isnan(prev_hi)):
            sl_p = imp.get_structural_sl(prev_lo, prev_hi, cv, 'short')
            sl_d = sl_p - cv
            if 0 < sl_d * 10000 <= 3000:
                sigs.append({
                    'type':'REV_SHORT','entry_time':str(ts),
                    'entry':round(cv,5),'sl_pts':round(sl_d*10000),
                    'direction':'short','entry_bar':i,
                    'lon_open':round(lon_px,5),
                })
                continue

        # ── GAP_REJ LONG (zone already touched) ──────────────────────────
        if (not attr_traded and in_attr and attr_gap_pts < 0 and attr_touched
                and not np.isnan(attr_gap_target) and cv < attr_gap_target
                and bull_sig.at[i] and bv4f == 1 and prev_rng <= 8000):
            rwd = (attr_gap_target - cv) * 10000
            if rwd >= ATTR_MIN_REWARD:
                tp_p = attr_gap_target - 50/10000
                sl_d = tp_p - cv
                if sl_d > 0:
                    sigs.append({
                        'type':'GAP_REJ_LONG','entry_time':str(ts),
                        'entry':round(cv,5),'sl_pts':round(sl_d*10000),
                        'direction':'long','entry_bar':i,
                        'lon_open':round(lon_px,5),
                    })
                    attr_traded = True
                    continue

        # ── GAP_REJ SHORT ────────────────────────────────────────────────
        if (not attr_traded and in_attr and attr_gap_pts > 0 and attr_touched
                and not np.isnan(attr_gap_target) and cv > attr_gap_target
                and bear_sig.at[i] and bv4f == 1 and prev_rng <= 8000):
            rwd = (cv - attr_gap_target) * 10000
            if rwd >= ATTR_MIN_REWARD:
                tp_p = attr_gap_target + 50/10000
                sl_d = cv - tp_p
                if sl_d > 0:
                    sigs.append({
                        'type':'GAP_REJ_SHORT','entry_time':str(ts),
                        'entry':round(cv,5),'sl_pts':round(sl_d*10000),
                        'direction':'short','entry_bar':i,
                        'lon_open':round(lon_px,5),
                    })
                    attr_traded = True
                    continue

        # ── ATTR_CORE LONG ───────────────────────────────────────────────
        if i <= ac_in_trade_until or not ac_pristine or not in_attr or ac_traded:
            pass
        elif (not np.isnan(ac_zone_top) and not ac_japan_bull and bull_sig.at[i]
              and (ac_zone_top - cv)*10000 >= ATTR_MIN_REWARD
              and ac_gap_pts < CORE_GAP_MAX):
            approach = (cv - df.at[i-3,'close'])*10000 if i >= 3 else 0
            wave_ext = max((ac_lon_close - cv)*10000, 0.0) if not np.isnan(ac_lon_close) else 0
            if approach >= ATTR_APPROACH and wave_ext < CORE_WAVE_MAX:
                tp_p = ac_zone_top; sl_d = tp_p - cv
                if sl_d > 0:
                    sigs.append({
                        'type':'ATTR_CORE_LONG','entry_time':str(ts),
                        'entry':round(cv,5),'sl_pts':round(sl_d*10000),
                        'direction':'long','entry_bar':i,
                        'lon_open':round(lon_px,5),
                    })
                    ac_traded = True
                    # find approx exit bar for ATTR_CORE to avoid overlap
                    eb, _ = _find_indicator_exit(df, i, cv, sl_d, 'long', 1.0)
                    ac_in_trade_until = eb

        # ── ATTR_CORE SHORT ──────────────────────────────────────────────
        elif (not np.isnan(ac_zone_top) and ac_japan_bull and bear_sig.at[i]
              and (cv - ac_zone_bot)*10000 >= ATTR_MIN_REWARD
              and i > ac_in_trade_until and ac_pristine and not ac_traded
              and ac_gap_pts < CORE_GAP_MAX):
            approach = (df.at[i-3,'close'] - cv)*10000 if i >= 3 else 0
            wave_ext = max((cv - ac_lon_close)*10000, 0.0) if not np.isnan(ac_lon_close) else 0
            if approach >= ATTR_APPROACH and wave_ext < CORE_WAVE_MAX:
                tp_p = ac_zone_bot; sl_d = cv - tp_p
                if sl_d > 0:
                    sigs.append({
                        'type':'ATTR_CORE_SHORT','entry_time':str(ts),
                        'entry':round(cv,5),'sl_pts':round(sl_d*10000),
                        'direction':'short','entry_bar':i,
                        'lon_open':round(lon_px,5),
                    })
                    ac_traded = True
                    eb, _ = _find_indicator_exit(df, i, cv, sl_d, 'short', 1.0)
                    ac_in_trade_until = eb

    return sigs


# ══════════════════════════════════════════════════════════════════════════════
# RESOLVE INDICATOR EXITS  (post-scan: add exit_time / exit_bar to each signal)
# ══════════════════════════════════════════════════════════════════════════════

def resolve_indicator_exits(sigs, df_src):
    """Adds exit_time and indicator_outcome to each signal using TP_MULT."""
    df = df_src.reset_index(drop=True)
    for s in sigs:
        i   = s['entry_bar']
        cv  = s['entry']
        sl_d = s['sl_pts'] / 10000
        rr  = TP_MULT.get(s['type'], 1.0)
        eb, outcome = _find_indicator_exit(df, i, cv, sl_d, s['direction'], rr)
        s['exit_bar']         = eb
        s['exit_time']        = str(df.at[eb, 'time'])
        s['indicator_outcome'] = outcome
    return sigs


# ══════════════════════════════════════════════════════════════════════════════
# PAIR APPLICATOR
# ══════════════════════════════════════════════════════════════════════════════

def _apply_worker(args):
    trade_pair, sigs, news_dates = args
    df_p = pd.read_csv(FILE_MAP[trade_pair])
    df_p['time'] = pd.to_datetime(df_p['time'], utc=True)
    df_p = df_p.sort_values('time').reset_index(drop=True)
    for col in ['open','high','low','close']: df_p[col] = df_p[col].astype(float)
    n  = len(df_p)
    F  = PAIR_FACTOR[trade_pair]
    D  = PAIR_DIR[trade_pair]
    pidx = {str(t): i for i, t in enumerate(df_p['time'])}

    rows = []
    for sig in sigs:
        et = sig['entry_time']
        xt = sig.get('exit_time')
        if et not in pidx or not xt or xt not in pidx: continue
        if news_dates and r.news_blocks_pair(news_dates, et, trade_pair): continue

        pi = pidx[et]; xi = pidx[xt]
        pc = df_p.at[pi, 'close']
        is_long_ind = (sig['direction'] == 'long')
        pair_long   = (is_long_ind and D == 1) or (not is_long_ind and D == -1)
        pair_sl_d   = sig['sl_pts'] / 10000 * F
        if pair_sl_d <= 0: continue
        pair_sl_px  = pc - pair_sl_d if pair_long else pc + pair_sl_d

        r_actual = None
        for j in range(pi + 1, min(xi + 1, n)):
            if pair_long and df_p.at[j,'low']  <= pair_sl_px: r_actual = -1.0; break
            if not pair_long and df_p.at[j,'high'] >= pair_sl_px: r_actual = -1.0; break
        if r_actual is None:
            px = df_p.at[min(xi, n-1), 'close']
            raw = (px - pc) if pair_long else (pc - px)
            r_actual = raw / pair_sl_d

        rows.append({
            'signal':     sig['type'],
            'entry_time': et,
            'exit_time':  xt,
            'pair':       trade_pair,
            'pair_long':  pair_long,
            'r_actual':   round(r_actual, 3),
            'sl_pts':     sig['sl_pts'],
            'indicator_outcome': sig.get('indicator_outcome',''),
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# STATS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def stats(df, months):
    n = len(df)
    if n == 0: return dict(N=0, WR=0, NetR=0, rpm=0, avg_w=0, avg_l=0, pf=0, sigs=0)
    wins  = (df['r_actual'] > 0).sum()
    wr    = wins / n * 100
    net   = df['r_actual'].sum()
    gw    = df.loc[df['r_actual'] > 0, 'r_actual'].sum()
    gl    = df.loc[df['r_actual'] < 0, 'r_actual'].abs().sum()
    pf    = gw / gl if gl > 0 else 999.0
    avg_w = df.loc[df['r_actual'] > 0, 'r_actual'].mean() if wins else 0
    avg_l = df.loc[df['r_actual'] < 0, 'r_actual'].mean() if (n-wins) else 0
    n_sigs = df['entry_time'].nunique()
    return dict(N=n, sigs=n_sigs, WR=round(wr,1), NetR=round(net,1),
                rpm=round(net/months,2), avg_w=round(avg_w,3),
                avg_l=round(avg_l,3), pf=round(pf,2))


HDR = f"  {'':22} {'Sigs':>5} {'N':>5} {'WR%':>6} {'NetR':>9} {'R/mo':>6} {'PF':>5} {'AvgW':>7} {'AvgL':>7}"
SEP = f"  {'-'*78}"

def print_row(label, s):
    if s['N'] == 0:
        print(f"  {label:<22}   --  (no trades)")
        return
    print(f"  {label:<22} {s['sigs']:>5} {s['N']:>5}  {s['WR']:>5.1f}% "
          f"{s['NetR']:>+9.1f}R {s['rpm']:>+5.2f} {str(s['pf']):>5} "
          f"{s['avg_w']:>+6.3f}R {s['avg_l']:>+6.3f}R")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    news_dates = r.load_news_filter()

    # ── Load data ──────────────────────────────────────────────────────────
    print("Loading data...")
    df_dxy = imp.load_merged('DXY').reset_index(drop=True)
    df_dxy['time'] = pd.to_datetime(df_dxy['time'], utc=True)
    months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44

    df_jpy = pd.read_csv(FILE_MAP['USDJPY'])
    df_jpy['time'] = pd.to_datetime(df_jpy['time'], utc=True)
    df_jpy = df_jpy.sort_values('time').reset_index(drop=True)
    for col in ['open','high','low','close']: df_jpy[col] = df_jpy[col].astype(float)
    print(f"  DXY:    {len(df_dxy):,} bars  ({months:.1f} months)")
    print(f"  USDJPY: {len(df_jpy):,} bars")

    # ── DXY baseline (from existing combined trade log) ────────────────────
    print("\nLoading DXY baseline (combined_trade_log.csv)...")
    df_dxy_base = pd.read_csv(BASE / 'combined_trade_log.csv')
    print(f"  {len(df_dxy_base)} trades  |  "
          f"signals: {df_dxy_base['signal'].unique().tolist()}")

    # ── Generate ALL signals on USDJPY ─────────────────────────────────────
    print("\nGenerating all signals on USDJPY data...")
    sigs_jpy = scan_all_signals(df_jpy, news_dates)
    sigs_jpy = resolve_indicator_exits(sigs_jpy, df_jpy)

    from collections import Counter
    cnt = Counter(s['type'] for s in sigs_jpy)
    cnt_dxy = Counter(df_dxy_base['signal'])
    print(f"  USDJPY signals: {len(sigs_jpy)}")
    for sig_type in ['LON_ATTR_LONG','GAP_REJ_LONG','GAP_REJ_SHORT',
                     'REV_LONG','REV_SHORT','ATTR_CORE_LONG','ATTR_CORE_SHORT']:
        tp_m = TP_MULT.get(sig_type, 1.0)
        print(f"    {sig_type:<20}  USDJPY={cnt.get(sig_type,0):>3}  "
              f"(DXY={cnt_dxy.get(sig_type,0):>3})  TP_mult={tp_m}x")

    # ── Indicator outcome comparison ───────────────────────────────────────
    print("\n  Indicator-level outcomes (TP/SL on own data):")
    print(f"  {'Signal':<22} {'USDJPY TP%':>10} {'USDJPY SL%':>10} {'DXY TP%':>10} {'DXY SL%':>10}")
    print(f"  {'-'*65}")
    dxy_oc = df_dxy_base.groupby('signal')['dxy_outcome'].value_counts(normalize=True).unstack(fill_value=0)
    for stype in ['LON_ATTR_LONG','GAP_REJ_LONG','GAP_REJ_SHORT',
                  'REV_LONG','REV_SHORT','ATTR_CORE_LONG','ATTR_CORE_SHORT']:
        sub = [s for s in sigs_jpy if s['type']==stype]
        if not sub: continue
        jtp = sum(1 for s in sub if s['indicator_outcome']=='win')/len(sub)*100
        jsl = sum(1 for s in sub if s['indicator_outcome']=='loss')/len(sub)*100
        dtp = dxy_oc.at[stype,'win']*100 if stype in dxy_oc.index and 'win' in dxy_oc.columns else 0
        dsl = dxy_oc.at[stype,'loss']*100 if stype in dxy_oc.index and 'loss' in dxy_oc.columns else 0
        print(f"  {stype:<22} {jtp:>9.0f}%  {jsl:>9.0f}%  {dtp:>9.0f}%  {dsl:>9.0f}%")

    # ── Apply USDJPY signals to all 7 pairs ───────────────────────────────
    print(f"\nApplying USDJPY signals to all 7 pairs ({N_WORKERS} workers)...")
    jobs = [(p, sigs_jpy, news_dates) for p in ALL_PAIRS]
    with Pool(N_WORKERS) as pool:
        raw = pool.map(_apply_worker, jobs)
    df_jpy_trades = pd.DataFrame([row for pr in raw for row in pr])
    print(f"  {len(df_jpy_trades)} pair-trades generated")

    # ══════════════════════════════════════════════════════════════════════
    # RESULTS TABLES
    # ══════════════════════════════════════════════════════════════════════

    SIG_LABELS = {
        'LON_ATTR_LONG':  'London Attraction L',
        'GAP_REJ_LONG':   'Zone Rejection L',
        'GAP_REJ_SHORT':  'Zone Rejection S',
        'REV_LONG':       'Reversal L',
        'REV_SHORT':      'Reversal S',
        'ATTR_CORE_LONG': 'Core Attraction L',
        'ATTR_CORE_SHORT':'Core Attraction S',
    }

    # ── TABLE 1: DXY baseline — by signal type ────────────────────────────
    print()
    print("=" * 85)
    print("  DXY INDICATOR — all signal types, all 7 pairs")
    print("=" * 85)
    print(HDR); print(SEP)
    for stype, label in SIG_LABELS.items():
        sub = df_dxy_base[df_dxy_base['signal']==stype]
        print_row(label, stats(sub.rename(columns={'r_actual':'r_actual'}), months))
    print(SEP)
    print_row('TOTAL', stats(df_dxy_base.rename(columns={'r_actual':'r_actual'}), months))

    # ── TABLE 2: USDJPY indicator — by signal type ────────────────────────
    print()
    print("=" * 85)
    print("  USDJPY INDICATOR — all signal types, all 7 pairs")
    print(f"  (LON_ATTR_LONG TP={TP_MULT['LON_ATTR_LONG']}x, all others TP=1x)")
    print("=" * 85)
    print(HDR); print(SEP)
    for stype, label in SIG_LABELS.items():
        sub = df_jpy_trades[df_jpy_trades['signal']==stype]
        print_row(label, stats(sub, months))
    print(SEP)
    print_row('TOTAL', stats(df_jpy_trades, months))

    # ── TABLE 3: DXY vs USDJPY per pair (all signals combined) ────────────
    print()
    print("=" * 85)
    print("  DXY vs USDJPY INDICATOR — per pair (all signals combined)")
    print("=" * 85)
    print(f"  {'Pair':<12}  {'--- DXY ---':^38}  {'--- USDJPY ---':^38}")
    print(f"  {'':12}  {'N':>5} {'WR%':>6} {'NetR':>8} {'R/mo':>6}  "
          f"{'N':>5} {'WR%':>6} {'NetR':>8} {'R/mo':>6}  {'NetR delta':>10}")
    print(f"  {'-'*88}")
    for pair in ALL_PAIRS + ['ALL']:
        if pair == 'ALL':
            sd = df_dxy_base
            sj = df_jpy_trades
        else:
            sd = df_dxy_base[df_dxy_base['pair']==pair]
            sj = df_jpy_trades[df_jpy_trades['pair']==pair]
        d = stats(sd, months); j = stats(sj, months)
        delta = j['NetR'] - d['NetR']
        sym = '+' if delta >= 0 else ''
        print(f"  {pair:<12}  {d['N']:>5} {d['WR']:>5.1f}% {d['NetR']:>+8.1f}R {d['rpm']:>+5.2f}  "
              f"{j['N']:>5} {j['WR']:>5.1f}% {j['NetR']:>+8.1f}R {j['rpm']:>+5.2f}  "
              f"{sym}{delta:>+8.1f}R")

    # ── TABLE 4: Head-to-head per signal type per pair ────────────────────
    print()
    print("=" * 85)
    print("  USDJPY vs DXY — net R delta by signal type (USDJPY minus DXY)")
    print("  Positive = USDJPY indicator better for that signal/pair combo")
    print("=" * 85)
    pairs_short = ['EUR','JPY','CAD','GBP','AUD','NZD','CHF','ALL']
    pair_full   = ['EURUSD','USDJPY','USDCAD','GBPUSD','AUDUSD','NZDUSD','USDCHF','ALL']
    header = f"  {'Signal':<22} " + " ".join(f"{p:>6}" for p in pairs_short)
    print(header); print(f"  {'-'*80}")
    for stype, label in SIG_LABELS.items():
        row_vals = []
        for pair, pshort in zip(pair_full, pairs_short):
            if pair == 'ALL':
                dv = df_dxy_base[df_dxy_base['signal']==stype]['r_actual'].sum()
                jv = df_jpy_trades[df_jpy_trades['signal']==stype]['r_actual'].sum()
            else:
                dv = df_dxy_base[(df_dxy_base['signal']==stype)&(df_dxy_base['pair']==pair)]['r_actual'].sum()
                jv = df_jpy_trades[(df_jpy_trades['signal']==stype)&(df_jpy_trades['pair']==pair)]['r_actual'].sum()
            delta = jv - dv
            row_vals.append(f"{delta:>+6.1f}")
        print(f"  {label:<22} " + " ".join(row_vals))
    print(f"  {'-'*80}")
    # total row
    row_vals = []
    for pair, pshort in zip(pair_full, pairs_short):
        if pair == 'ALL':
            dv = df_dxy_base['r_actual'].sum()
            jv = df_jpy_trades['r_actual'].sum()
        else:
            dv = df_dxy_base[df_dxy_base['pair']==pair]['r_actual'].sum()
            jv = df_jpy_trades[df_jpy_trades['pair']==pair]['r_actual'].sum()
        row_vals.append(f"{jv-dv:>+6.1f}")
    print(f"  {'TOTAL':<22} " + " ".join(row_vals))

    # ── Save ──────────────────────────────────────────────────────────────
    df_jpy_trades['indicator'] = 'USDJPY'
    df_dxy_base_out = df_dxy_base[['signal','entry_time','pair','r_actual','dxy_outcome']].copy()
    df_dxy_base_out['indicator'] = 'DXY'
    df_jpy_trades.to_csv(BASE / 'usdjpy_indicator_trades.csv', index=False)
    print(f"\n  Saved usdjpy_indicator_trades.csv ({len(df_jpy_trades)} rows)")
