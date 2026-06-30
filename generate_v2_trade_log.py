"""
generate_v2_trade_log.py
========================
Generates full trade journal (v2.0 parameters):

DXY indicator:
  LON_ATTR_LONG   pairs: USDJPY/NZDUSD/AUDUSD  TP=2.5x(JPY) / 1.5x(NZD,AUD)
  GAP_REJ_LONG    all 7 pairs                   TP=1.0x, BB4 flat

USDJPY indicator:
  GAP_REJ_SHORT   inv3 pairs (EUR/GBP/CHF)      min_gap=200pts, TP=3.0x
  ATTR_CORE_LONG  all 7 pairs                   any BB, TP=3.0x
  ATTR_CORE_SHORT all 7 pairs                   BB4 flat, TP=2.5x
  REV_LONG        all 7 pairs                   BB4 flat, impulse>=1000, dist<=1000, TP=3.0x
  REV_SHORT       all 7 pairs                   any BB, impulse>=1000, dist<=5000, TP=2.5x
"""

import sys
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
from pathlib import Path
from collections import Counter

import dxy_improved_rules as imp
import dxy_clean_rules as r

BASE         = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
COMBINED_OUT = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\Combined Results\combined_trade_log.csv")
MAX_BARS     = 500

ALL_PAIRS   = ['EURUSD', 'USDJPY', 'USDCAD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCHF']
PAIR_FACTOR = {'EURUSD':0.01,'GBPUSD':0.01,'AUDUSD':0.01,'NZDUSD':0.01,
               'USDJPY':1.0,'USDCAD':0.01,'USDCHF':0.01}
PAIR_DIR    = {'EURUSD':-1,'GBPUSD':-1,'AUDUSD':-1,'NZDUSD':-1,
               'USDJPY':+1,'USDCAD':+1,'USDCHF':+1}
FILE_MAP    = {p: BASE / f'FX_{p}, 15_merged.csv' for p in ALL_PAIRS}
FILE_MAP['DXY'] = BASE / 'TVC_DXY, 15_merged.csv'

LON_ATTR_PAIRS  = ['USDJPY', 'NZDUSD', 'AUDUSD']
LON_ATTR_TP     = {'USDJPY': 2.5, 'NZDUSD': 1.5, 'AUDUSD': 1.5}
GRS_PAIRS       = ['EURUSD', 'GBPUSD', 'USDCHF']
GAP_REJ_L_PAIRS = ['USDJPY', 'NZDUSD']

TP_MULT = {
    'LON_ATTR_LONG':  None,   # per-pair — resolved in applicator
    'GAP_REJ_LONG':   1.0,
    'GAP_REJ_SHORT':  3.0,
    'ATTR_CORE_LONG': 3.0,
    'ATTR_CORE_SHORT':2.5,
    'REV_LONG':       3.0,
    'REV_SHORT':      2.5,
}

GRS_MIN_GAP    = 200
RL_MIN_IMPULSE = 1000
RL_MAX_DIST    = 1000
RS_MIN_IMPULSE = 1000
RS_MAX_DIST    = 5000
MAX_SL_PTS     = 3000

PIN_WICK_MULT   = r.PIN_WICK_MULT
ZONE_MIN_GAP    = 30
ATTR_MIN_GAP    = 75
ATTR_APPROACH   = 150
ATTR_MIN_REWARD = 100
CORE_GAP_MAX    = 1500
CORE_WAVE_MAX   = 1500


# ══════════════════════════════════════════════════════════════════════════════
# CANDLE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _pin_series(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body = (c - o).abs()
    bt   = pd.concat([o, c], axis=1).max(axis=1)
    bb_  = pd.concat([o, c], axis=1).min(axis=1)
    hi_w = h - bt; lo_w = bb_ - l
    rng  = (h - l).replace(0, np.nan)
    bp   = (lo_w >= body * PIN_WICK_MULT) & (lo_w >= hi_w * 1.5) & rng.notna()
    bear = (hi_w >= body * PIN_WICK_MULT) & (hi_w >= lo_w * 1.5) & rng.notna()
    both = bp & bear
    return bp & ~(both & (c <= o)), bear & ~(both & (c >= o))


def _engulf_3bar(df):
    c, o  = df['close'], df['open']
    body  = (c - o).abs()
    bar2r = (c.shift(2) - o.shift(2)).abs()
    indec = body.shift(1) <= bar2r * 0.5
    be = ((c > o) & ~(c.shift(1) > o.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))
          & (body >= body.shift(1) * 0.8))
    bae = ((c < o) & ~(c.shift(1) < o.shift(1)) & (c < o.shift(1)) & (o > c.shift(1))
           & (body >= body.shift(1) * 0.8))
    b3  = (c.shift(2) < o.shift(2)) & indec & (c > o) & (c > o.shift(2))
    ba3 = (c.shift(2) > o.shift(2)) & indec & (c < o) & (c < o.shift(2))
    return (be | b3).fillna(False), (bae | ba3).fillna(False)


# ══════════════════════════════════════════════════════════════════════════════
# EXIT FINDER  (returns exit_bar, outcome, sl_px, tp_px)
# ══════════════════════════════════════════════════════════════════════════════

def _find_exit(df, entry_bar, entry_px, sl_d, direction, rr):
    n = len(df)
    if direction == 'short':
        tp_px = entry_px - sl_d * rr
        sl_px = entry_px + sl_d
    else:
        tp_px = entry_px + sl_d * rr
        sl_px = entry_px - sl_d
    for j in range(entry_bar + 1, min(entry_bar + MAX_BARS, n)):
        o_j, h_j, l_j = df.at[j,'open'], df.at[j,'high'], df.at[j,'low']
        if direction == 'short':
            if o_j >= sl_px or h_j >= sl_px: return j, 'loss', sl_px, tp_px
            if o_j <= tp_px or l_j <= tp_px: return j, 'win',  sl_px, tp_px
        else:
            if o_j <= sl_px or l_j <= sl_px: return j, 'loss', sl_px, tp_px
            if o_j >= tp_px or h_j >= tp_px: return j, 'win',  sl_px, tp_px
    last = min(entry_bar + MAX_BARS - 1, n - 1)
    return last, 'timeout', sl_px, tp_px


# ══════════════════════════════════════════════════════════════════════════════
# DXY SIGNAL SCANNER  (LON_ATTR_LONG + GAP_REJ_LONG)
# ══════════════════════════════════════════════════════════════════════════════

def scan_dxy_signals(df_src, news_dates):
    df = df_src.copy().reset_index(drop=True)
    bull_pin, _   = _pin_series(df)
    bull_e3, _    = _engulf_3bar(df)
    bull_sig      = (bull_pin | bull_e3).fillna(False)
    _, bb4_flat   = imp.compute_bb_regime(df, 4)
    df['_date']   = df['time'].dt.date
    day_grp = df.groupby('_date').agg(day_h=('high','max'), day_l=('low','min'))

    LON_H, LON_M = 7, 0
    MON_H, MON_M = 6, 30
    ATTR_WINDOW  = (6*60, 19*60+30)
    ENTRY_END    = 18*60

    lon_px = np.nan; prev_hi = prev_lo = np.nan; prev_rng = 0.0
    la_zone_top = la_zone_bot = np.nan
    la_pristine = la_traded   = False
    attr_gap_pts = 0.0; attr_gap_target = np.nan
    attr_touched = attr_traded = False
    sigs = []

    for i in range(2, len(df)):
        row  = df.iloc[i]
        cv, ov = row['close'], row['open']
        ts   = row['time']
        hh, mm = ts.hour, ts.minute
        cm   = hh * 60 + mm
        dow  = ts.dayofweek
        in_jpn  = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)
        is_lon  = (not in_jpn and hh == LON_H and mm == LON_M and dow != 0)
        is_mon  = (not in_jpn and hh == MON_H and mm == MON_M and dow == 0)
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
                attr_gap_pts, attr_gap_target = (raw, ref) if abs(raw) >= 10 else ((cv-ov)*10000, ov)
            else:
                attr_gap_pts, attr_gap_target = 0.0, np.nan

        if is_lon or is_mon:
            lon_px = ov
            la_zone_top = max(ov, cv); la_zone_bot = min(ov, cv)
            la_pristine = True; la_traded = False
            today = ts.date()
            prior = [d for d in day_grp.index if d < today]
            if prior:
                pd_ = max(prior)
                prev_hi = float(day_grp.at[pd_,'day_h'])
                prev_lo = float(day_grp.at[pd_,'day_l'])
                prev_rng = (prev_hi - prev_lo) * 10000
            else:
                prev_hi = prev_lo = np.nan; prev_rng = 0.0
            continue

        if np.isnan(lon_px) or in_jpn: continue

        if not np.isnan(la_zone_top):
            if ov >= la_zone_top or cv >= la_zone_top: la_pristine = False
        if not np.isnan(attr_gap_target):
            if attr_gap_pts < 0 and cv >= attr_gap_target: attr_touched = True
            elif attr_gap_pts > 0 and cv <= attr_gap_target: attr_touched = True

        dist = (cv - lon_px) * 10000
        attr_start = MON_H*60+MON_M if dow == 0 else 6*60
        in_attr = (attr_start <= cm <= ATTR_WINDOW[1] and not in_jpn)
        in_la   = (attr_start <= cm <= ENTRY_END and not in_jpn)
        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'): continue

        bv4f = int(bb4_flat.at[i])

        # LON_ATTR_LONG
        if (not la_traded and in_la and dist < 0 and la_pristine
                and bull_pin.at[i] and not np.isnan(la_zone_bot)):
            tp = la_zone_bot
            if tp > cv:
                sl_d = tp - cv
                sigs.append({'type':'LON_ATTR_LONG','indicator':'DXY',
                             'entry_time':str(ts),'entry':round(cv,5),
                             'sl_pts':round(sl_d*10000),'sl_d':sl_d,
                             'direction':'long','entry_bar':i,
                             'lon_open':round(lon_px,5),
                             'gap_pts_at_lon':0.0,'wave_ext_pts':0.0})
                la_traded = True
                continue

        # GAP_REJ_LONG
        if (not attr_traded and in_attr and attr_gap_pts < 0 and attr_touched
                and not np.isnan(attr_gap_target) and cv < attr_gap_target
                and bull_sig.at[i] and bv4f == 1 and prev_rng <= 8000):
            rwd = (attr_gap_target - cv) * 10000
            if rwd >= ATTR_MIN_REWARD:
                tp_p = attr_gap_target - 50/10000
                sl_d = tp_p - cv
                if sl_d > 0:
                    sigs.append({'type':'GAP_REJ_LONG','indicator':'DXY',
                                 'entry_time':str(ts),'entry':round(cv,5),
                                 'sl_pts':round(sl_d*10000),'sl_d':sl_d,
                                 'direction':'long','entry_bar':i,
                                 'lon_open':round(lon_px,5),
                                 'gap_pts_at_lon':round(attr_gap_pts),'wave_ext_pts':0.0})
                    attr_traded = True
    return sigs


# ══════════════════════════════════════════════════════════════════════════════
# USDJPY SIGNAL SCANNER  (GRS + ACL + ACS + RL + RS)
# ══════════════════════════════════════════════════════════════════════════════

def scan_jpy_signals(df_src, news_dates):
    df = df_src.copy().reset_index(drop=True)
    bull_pin, bear_pin = _pin_series(df)
    bull_e3, bear_e3   = _engulf_3bar(df)
    bull_sig = (bull_pin | bull_e3).fillna(False)
    bear_sig = (bear_pin | bear_e3).fillna(False)
    _, bb4_flat = imp.compute_bb_regime(df, 4)
    df['_date'] = df['time'].dt.date
    day_grp = df.groupby('_date').agg(day_h=('high','max'), day_l=('low','min'))

    LON_H, LON_M = 7, 0
    MON_H, MON_M = 6, 30
    ATTR_WINDOW    = (6*60, 19*60+30)
    REV_WINDOW_END = 12*60

    lon_px = np.nan; prev_hi = prev_lo = np.nan; prev_rng = 0.0
    max_up = max_dn = 0.0
    attr_gap_pts = 0.0; attr_gap_target = np.nan
    attr_touched = attr_traded = False
    ac_zone_top = ac_zone_bot = np.nan
    ac_japan_bull = False
    ac_pristine = ac_traded_core = False
    ac_in_trade_until = -1
    ac_lon_close = np.nan; ac_gap_pts = 0.0
    sigs = []

    for i in range(2, len(df)):
        row = df.iloc[i]
        cv, ov = row['close'], row['open']
        ts = row['time']
        hh, mm = ts.hour, ts.minute
        cm = hh * 60 + mm
        dow = ts.dayofweek
        in_jpn  = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)
        is_lon  = (not in_jpn and hh == LON_H and mm == LON_M and dow != 0)
        is_mon  = (not in_jpn and hh == MON_H and mm == MON_M and dow == 0)
        is_2345 = (hh == 23 and mm == 45)

        if is_2345:
            # ATTR_CORE zone
            pb = abs(df.at[i-1,'close'] - df.at[i-1,'open']) * 10000
            pc_ref = df.at[i-2,'close'] if (pb < 10 and i >= 2) else df.at[i-1,'close']
            jo, jc = ov, cv
            gap = abs(pc_ref - jo) * 10000
            if gap >= ZONE_MIN_GAP:
                ac_zone_top, ac_zone_bot, ac_japan_bull = max(pc_ref,jo), min(pc_ref,jo), jo > pc_ref
            else:
                ac_zone_top, ac_zone_bot, ac_japan_bull = max(jo,jc), min(jo,jc), jc > jo
            if abs(ac_zone_top - ac_zone_bot) * 10000 < 1:
                ac_zone_top = max(jo, jc) + 0.001
            ac_pristine = ac_traded_core = False
            ac_lon_close = np.nan; ac_gap_pts = 0.0
            # GAP_REJ gap state
            attr_traded = attr_touched = False
            ref = None
            for back, off in [(2, 30), (1, 15)]:
                if i >= back:
                    cand = df.iloc[i - back]
                    if abs((cand['time'] - (ts - pd.Timedelta(minutes=off))).total_seconds()) <= 120:
                        ref = cand['close']; break
            if ref is not None:
                raw = (ov - ref) * 10000
                attr_gap_pts, attr_gap_target = (raw, ref) if abs(raw) >= 10 else ((cv-ov)*10000, ov)
            else:
                attr_gap_pts, attr_gap_target = 0.0, np.nan

        if is_lon or is_mon:
            lon_px = ov; max_up = max_dn = 0.0
            today = ts.date()
            prior = [d for d in day_grp.index if d < today]
            if prior:
                pd_ = max(prior)
                prev_hi = float(day_grp.at[pd_,'day_h'])
                prev_lo = float(day_grp.at[pd_,'day_l'])
                prev_rng = (prev_hi - prev_lo) * 10000
            else:
                prev_hi = prev_lo = np.nan; prev_rng = 0.0
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

        if np.isnan(lon_px) or in_jpn: continue

        if not np.isnan(attr_gap_target):
            if attr_gap_pts > 0 and cv <= attr_gap_target: attr_touched = True

        dist = (cv - lon_px) * 10000
        if dist > 0: max_up = max(max_up, dist)
        else:        max_dn = max(max_dn, -dist)

        attr_start = MON_H*60+MON_M if dow == 0 else 6*60
        mon_s = MON_H*60+MON_M; lon_s = LON_H*60
        rev_start = mon_s if dow == 0 else lon_s
        in_attr = (attr_start <= cm <= ATTR_WINDOW[1] and not in_jpn)
        in_rev  = (rev_start  <= cm <= REV_WINDOW_END and not in_jpn)

        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'): continue
        bv4f = int(bb4_flat.at[i])

        # GAP_REJ_SHORT (min_gap=200, bear candle, zone touched)
        if (not attr_traded and in_attr and attr_gap_pts >= GRS_MIN_GAP and attr_touched
                and not np.isnan(attr_gap_target) and cv > attr_gap_target
                and bear_sig.at[i]):
            rwd = (cv - attr_gap_target) * 10000
            if rwd >= ATTR_MIN_REWARD:
                tp_p = attr_gap_target + 50/10000
                sl_d = cv - tp_p
                if sl_d > 0:
                    sigs.append({'type':'GAP_REJ_SHORT','indicator':'USDJPY',
                                 'entry_time':str(ts),'entry':round(cv,5),
                                 'sl_pts':round(sl_d*10000),'sl_d':sl_d,
                                 'direction':'short','entry_bar':i,
                                 'lon_open':round(lon_px,5),
                                 'gap_pts_at_lon':round(attr_gap_pts),'wave_ext_pts':0.0})
                    attr_traded = True
                    continue

        # ATTR_CORE_LONG (any BB)
        if (i > ac_in_trade_until and ac_pristine and not ac_traded_core and in_attr
                and not np.isnan(ac_zone_top) and not ac_japan_bull and bull_sig.at[i]
                and (ac_zone_top - cv)*10000 >= ATTR_MIN_REWARD and ac_gap_pts < CORE_GAP_MAX):
            approach = (cv - df.at[i-3,'close'])*10000 if i >= 3 else 0
            wave_ext = max((ac_lon_close - cv)*10000, 0.0) if not np.isnan(ac_lon_close) else 0
            if approach >= ATTR_APPROACH and wave_ext < CORE_WAVE_MAX:
                tp_p = ac_zone_top; sl_d = tp_p - cv
                if sl_d > 0:
                    sigs.append({'type':'ATTR_CORE_LONG','indicator':'USDJPY',
                                 'entry_time':str(ts),'entry':round(cv,5),
                                 'sl_pts':round(sl_d*10000),'sl_d':sl_d,
                                 'direction':'long','entry_bar':i,
                                 'lon_open':round(lon_px,5),
                                 'gap_pts_at_lon':round(ac_gap_pts),'wave_ext_pts':round(wave_ext)})
                    ac_traded_core = True
                    eb, _ = _find_exit(df, i, cv, sl_d, 'long', 1.0)[:2]
                    ac_in_trade_until = eb
                    continue

        # ATTR_CORE_SHORT (BB4 flat required)
        if (i > ac_in_trade_until and ac_pristine and not ac_traded_core and in_attr
                and not np.isnan(ac_zone_top) and ac_japan_bull and bear_sig.at[i]
                and bv4f == 1
                and (cv - ac_zone_bot)*10000 >= ATTR_MIN_REWARD and ac_gap_pts < CORE_GAP_MAX):
            approach = (df.at[i-3,'close'] - cv)*10000 if i >= 3 else 0
            wave_ext = max((cv - ac_lon_close)*10000, 0.0) if not np.isnan(ac_lon_close) else 0
            if approach >= ATTR_APPROACH and wave_ext < CORE_WAVE_MAX:
                tp_p = ac_zone_bot; sl_d = cv - tp_p
                if sl_d > 0:
                    sigs.append({'type':'ATTR_CORE_SHORT','indicator':'USDJPY',
                                 'entry_time':str(ts),'entry':round(cv,5),
                                 'sl_pts':round(sl_d*10000),'sl_d':sl_d,
                                 'direction':'short','entry_bar':i,
                                 'lon_open':round(lon_px,5),
                                 'gap_pts_at_lon':round(ac_gap_pts),'wave_ext_pts':round(wave_ext)})
                    ac_traded_core = True
                    eb, _ = _find_exit(df, i, cv, sl_d, 'short', 1.0)[:2]
                    ac_in_trade_until = eb
                    continue

        # REV_LONG (BB4 flat, impulse>=1000, dist<=1000)
        if (in_rev and max_dn >= RL_MIN_IMPULSE and dist < 0 and abs(dist) <= RL_MAX_DIST
                and bv4f == 1 and bull_sig.at[i] and not np.isnan(prev_lo)):
            sl_p = imp.get_structural_sl(prev_lo, prev_hi, cv, 'long')
            sl_d = cv - sl_p
            sl_pts = sl_d * 10000
            if 0 < sl_pts <= MAX_SL_PTS:
                sigs.append({'type':'REV_LONG','indicator':'USDJPY',
                             'entry_time':str(ts),'entry':round(cv,5),
                             'sl_pts':round(sl_pts),'sl_d':sl_d,
                             'direction':'long','entry_bar':i,
                             'lon_open':round(lon_px,5),
                             'gap_pts_at_lon':0.0,'wave_ext_pts':round(max_dn)})
                continue

        # REV_SHORT (any BB, impulse>=1000, dist<=5000)
        if (in_rev and max_up >= RS_MIN_IMPULSE and dist > 0 and dist <= RS_MAX_DIST
                and bear_sig.at[i] and not np.isnan(prev_hi)):
            sl_p = imp.get_structural_sl(prev_lo, prev_hi, cv, 'short')
            sl_d = sl_p - cv
            sl_pts = sl_d * 10000
            if 0 < sl_pts <= MAX_SL_PTS:
                sigs.append({'type':'REV_SHORT','indicator':'USDJPY',
                             'entry_time':str(ts),'entry':round(cv,5),
                             'sl_pts':round(sl_pts),'sl_d':sl_d,
                             'direction':'short','entry_bar':i,
                             'lon_open':round(lon_px,5),
                             'gap_pts_at_lon':0.0,'wave_ext_pts':round(max_up)})
                continue

    return sigs


# ══════════════════════════════════════════════════════════════════════════════
# PAIR APPLICATOR
# ══════════════════════════════════════════════════════════════════════════════

def _apply_to_pair(trade_pair, sigs, ind_df):
    """Apply signals to one pair. ind_df = indicator OHLC (DXY or USDJPY)."""
    df_p = pd.read_csv(FILE_MAP[trade_pair])
    df_p['time'] = pd.to_datetime(df_p['time'], utc=True)
    df_p = df_p.sort_values('time').reset_index(drop=True)
    for col in ['open','high','low','close']: df_p[col] = df_p[col].astype(float)
    n = len(df_p)
    F = PAIR_FACTOR[trade_pair]
    D = PAIR_DIR[trade_pair]
    pidx = {str(t): i for i, t in enumerate(df_p['time'])}
    rows = []

    for sig in sigs:
        sig_type  = sig['type']
        entry_bar = sig['entry_bar']
        entry_px  = sig['entry']
        sl_d      = sig['sl_d']
        direction = sig['direction']

        # per-pair TP for LON_ATTR_LONG
        if sig_type == 'LON_ATTR_LONG':
            tp_mult = LON_ATTR_TP.get(trade_pair, 1.5)
        else:
            tp_mult = TP_MULT[sig_type]

        exit_bar, ind_outcome, ind_sl, ind_tp = _find_exit(
            ind_df, entry_bar, entry_px, sl_d, direction, tp_mult)
        exit_time = str(ind_df.at[exit_bar, 'time'])

        et = sig['entry_time']
        if et not in pidx or exit_time not in pidx: continue

        pi = pidx[et]; xi = pidx[exit_time]
        pair_long  = (direction == 'long' and D == 1) or (direction == 'short' and D == -1)
        pair_sl_d  = sl_d * F
        if pair_sl_d <= 0: continue
        pc = df_p.at[pi, 'close']
        pair_sl_px = pc - pair_sl_d if pair_long else pc + pair_sl_d

        r_actual = None
        for j in range(pi + 1, min(xi + 1, n)):
            if pair_long and df_p.at[j,'low'] <= pair_sl_px:  r_actual = -1.0; break
            if not pair_long and df_p.at[j,'high'] >= pair_sl_px: r_actual = -1.0; break
        if r_actual is None:
            px = df_p.at[min(xi, n-1), 'close']
            raw = (px - pc) if pair_long else (pc - px)
            r_actual = raw / pair_sl_d

        outcome = 'win' if r_actual > 0 else ('loss' if r_actual < 0 else 'timeout')
        pair_exit_px = pair_sl_px if r_actual == -1.0 else df_p.at[min(xi, n-1), 'close']

        rows.append({
            'signal':         sig_type,
            'entry_time':     et,
            'exit_time':      exit_time,
            'pair':           trade_pair,
            'direction':      'long' if pair_long else 'short',
            'pair_entry':     round(pc, 5),
            'pair_exit':      round(pair_exit_px, 5),
            'r_actual':       round(r_actual, 3),
            'outcome':        outcome,
            'sl_pts_dxy':     sig['sl_pts'],
            'dxy_outcome':    ind_outcome,
            'dxy_entry':      entry_px,
            'dxy_tp':         round(ind_tp, 5),
            'dxy_sl':         round(ind_sl, 5),
            'london_open':    sig['lon_open'],
            'gap_pts_at_lon': sig['gap_pts_at_lon'],
            'wave_ext_pts':   sig['wave_ext_pts'],
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# STATS + PRINT
# ══════════════════════════════════════════════════════════════════════════════

def stats(df, months):
    n = len(df)
    if n == 0: return dict(N=0, sigs=0, WR=0.0, NetR=0.0, rpm=0.0, avg_w=0.0, avg_l=0.0, pf=0.0)
    wins  = (df['r_actual'] > 0).sum()
    net   = df['r_actual'].sum()
    gw    = df.loc[df['r_actual'] > 0, 'r_actual'].sum()
    gl    = df.loc[df['r_actual'] < 0, 'r_actual'].abs().sum()
    pf    = gw / gl if gl > 0 else 999.0
    avg_w = df.loc[df['r_actual'] > 0, 'r_actual'].mean() if wins else 0.0
    avg_l = df.loc[df['r_actual'] < 0, 'r_actual'].mean() if (n - wins) else 0.0
    return dict(N=n, sigs=df['entry_time'].nunique(), WR=round(wins/n*100,1),
                NetR=round(net,1), rpm=round(net/months,2),
                avg_w=round(avg_w,3), avg_l=round(avg_l,3), pf=round(pf,2))


HDR = f"  {'':22} {'Sigs':>5} {'N':>5} {'WR%':>6} {'NetR':>9} {'R/mo':>6} {'PF':>5} {'AvgW':>7} {'AvgL':>7}"
SEP = "  " + "-" * 78

def print_row(label, s):
    if s['N'] == 0:
        print(f"  {label:<22}   --  (no trades)")
        return
    print(f"  {label:<22} {s['sigs']:>5} {s['N']:>5}  {s['WR']:>5.1f}%"
          f" {s['NetR']:>+9.1f}R {s['rpm']:>+5.2f} {str(s['pf']):>5}"
          f" {s['avg_w']:>+6.3f}R {s['avg_l']:>+6.3f}R")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    news_dates = r.load_news_filter()

    print("Loading indicator data...")
    df_dxy = imp.load_merged('DXY').reset_index(drop=True)
    df_dxy['time'] = pd.to_datetime(df_dxy['time'], utc=True)
    df_dxy = df_dxy.sort_values('time').reset_index(drop=True)
    for col in ['open','high','low','close']: df_dxy[col] = df_dxy[col].astype(float)

    df_jpy = pd.read_csv(FILE_MAP['USDJPY'])
    df_jpy['time'] = pd.to_datetime(df_jpy['time'], utc=True)
    df_jpy = df_jpy.sort_values('time').reset_index(drop=True)
    for col in ['open','high','low','close']: df_jpy[col] = df_jpy[col].astype(float)

    months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
    print(f"  DXY:    {len(df_dxy):,} bars  ({months:.1f} months)")
    print(f"  USDJPY: {len(df_jpy):,} bars")

    print("\nScanning DXY signals (LON_ATTR_LONG + GAP_REJ_LONG)...")
    sigs_dxy = scan_dxy_signals(df_dxy, news_dates)
    cnt = Counter(s['type'] for s in sigs_dxy)
    for t, n in cnt.items(): print(f"  {t:<22} {n} raw signals")

    print("\nScanning USDJPY signals (5 types)...")
    sigs_jpy = scan_jpy_signals(df_jpy, news_dates)
    cnt = Counter(s['type'] for s in sigs_jpy)
    for t, n in cnt.items(): print(f"  {t:<22} {n} raw signals")

    print("\nApplying to pairs...")
    all_rows = []
    for pair in ALL_PAIRS:
        # DXY signals
        pair_sigs_dxy = [s for s in sigs_dxy
                         if (s['type'] != 'LON_ATTR_LONG' or pair in LON_ATTR_PAIRS)
                         and (s['type'] != 'GAP_REJ_LONG'  or pair in GAP_REJ_L_PAIRS)]
        rows = _apply_to_pair(pair, pair_sigs_dxy, df_dxy)
        all_rows.extend(rows)
        # USDJPY signals
        pair_sigs_jpy = [s for s in sigs_jpy
                         if s['type'] != 'GAP_REJ_SHORT' or pair in GRS_PAIRS]
        rows = _apply_to_pair(pair, pair_sigs_jpy, df_jpy)
        all_rows.extend(rows)
        print(f"  {pair}: {len(rows)} trades")

    COLS = ['signal','entry_time','exit_time','pair','direction',
            'pair_entry','pair_exit','r_actual','outcome',
            'sl_pts_dxy','dxy_outcome','dxy_entry','dxy_tp','dxy_sl',
            'london_open','gap_pts_at_lon','wave_ext_pts']
    df_out = pd.DataFrame(all_rows, columns=COLS)
    df_out = df_out.sort_values('entry_time').reset_index(drop=True)

    local_out = BASE / 'combined_v2_trade_log.csv'
    df_out.to_csv(local_out, index=False)
    df_out.to_csv(COMBINED_OUT, index=False)
    print(f"\nSaved {len(df_out)} trades to:")
    print(f"  {local_out}")
    print(f"  {COMBINED_OUT}")

    # ═══════════════════════════════════════════
    # ANALYSIS
    # ═══════════════════════════════════════════
    SIG_LABELS = {
        'LON_ATTR_LONG':  'LON ATTR LONG  (DXY)',
        'GAP_REJ_LONG':   'GAP REJ LONG   (DXY)',
        'GAP_REJ_SHORT':  'GAP REJ SHORT  (JPY)',
        'ATTR_CORE_LONG': 'ATTR CORE LONG (JPY)',
        'ATTR_CORE_SHORT':'ATTR CORE SHORT(JPY)',
        'REV_LONG':       'REV LONG       (JPY)',
        'REV_SHORT':      'REV SHORT      (JPY)',
    }

    print()
    print("=" * 85)
    print(f"  V2.0 RESULTS — ALL SIGNALS  ({months:.1f} months)")
    print("=" * 85)
    print(HDR); print(SEP)
    for stype, label in SIG_LABELS.items():
        print_row(label, stats(df_out[df_out['signal']==stype], months))
    print(SEP)
    print_row('TOTAL', stats(df_out, months))

    print()
    print("=" * 85)
    print("  BY PAIR  (all signals)")
    print("=" * 85)
    print(HDR); print(SEP)
    for pair in ALL_PAIRS:
        print_row(pair, stats(df_out[df_out['pair']==pair], months))
    print(SEP)
    print_row('ALL PAIRS', stats(df_out, months))

    print()
    print("=" * 85)
    print("  BY SIGNAL x PAIR")
    print("=" * 85)
    for stype, label in SIG_LABELS.items():
        sub = df_out[df_out['signal']==stype]
        if len(sub) == 0: continue
        print(f"\n  [{label}]")
        print(HDR); print(SEP)
        for pair in ALL_PAIRS:
            ps = sub[sub['pair']==pair]
            if len(ps) == 0: continue
            print_row(pair, stats(ps, months))
        print(SEP)
        print_row('SUBTOTAL', stats(sub, months))

    # Monthly R
    print()
    print("=" * 85)
    print("  MONTHLY R  (all signals, all pairs)")
    print("=" * 85)
    df_out2 = df_out.copy()
    df_out2['month'] = pd.to_datetime(df_out2['entry_time']).dt.to_period('M')
    mon = df_out2.groupby('month').apply(
        lambda x: pd.Series({
            'Trades': len(x),
            'NetR':   round(x['r_actual'].sum(), 2),
            'WR_pct': round((x['r_actual'] > 0).mean() * 100, 1),
            'AvgR':   round(x['r_actual'].mean(), 3),
        })
    )
    print(f"  {'Month':<10} {'Trades':>7} {'NetR':>9} {'AvgR':>8} {'WR%':>7}")
    print("  " + "-" * 48)
    for period, row in mon.iterrows():
        print(f"  {str(period):<10} {int(row['Trades']):>7} {row['NetR']:>+9.2f}R"
              f" {row['AvgR']:>+8.3f}R {row['WR_pct']:>6.1f}%")
    print("  " + "-" * 48)
    print(f"  {'TOTAL':<10} {int(mon['Trades'].sum()):>7} {mon['NetR'].sum():>+9.2f}R"
          f" {df_out['r_actual'].mean():>+8.3f}R"
          f" {(df_out['r_actual']>0).mean()*100:>6.1f}%")
