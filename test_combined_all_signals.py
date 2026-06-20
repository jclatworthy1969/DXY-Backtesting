"""
test_combined_all_signals.py
============================
Full combined backtest — all DXY signal types across all 8 pairs.

Signal types:
  GAP_REJ_LONG / GAP_REJ_SHORT   — gap rejection (improved rules, BB 4H flat gate)
  REV_LONG     / REV_SHORT       — London open reversal (BB 1H+4H expanding)
  LON_ATTR_LONG                  — London attraction LONG only (dist >= 1000 pts, pristine zone)
  ATTR_CORE_LONG / ATTR_CORE_SHORT — attraction core (23:45 zone, gap_at_lon < 1500, wave_ext < 1500)

Outputs:
  combined_trade_log.csv  — full trade row per signal × pair, all columns
  combined_summary.csv    — per signal-type × pair statistics

Risk model: 0.25% per trade, $100,000 account
Period: Sep 2023 – May 2026 (~32 months)
"""

import sys, os
sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

import numpy as np
import pandas as pd
from pathlib import Path
from multiprocessing import Pool, cpu_count

import dxy_improved_rules as imp
import dxy_clean_rules    as r

BASE      = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
N_WORKERS = min(10, cpu_count())

ACCOUNT  = 100_000
RISK_PCT = 0.0025

ALL_PAIRS = ['EURUSD', 'USDJPY', 'USDCAD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCHF']

PAIR_FACTOR = {
    'EURUSD': 0.01, 'GBPUSD': 0.01, 'AUDUSD': 0.01, 'NZDUSD': 0.01,
    'USDJPY': 1.0,  'USDCAD': 0.01, 'USDCHF': 0.01,
}
PAIR_DIR = {
    'EURUSD': -1, 'GBPUSD': -1, 'AUDUSD': -1, 'NZDUSD': -1,
    'USDJPY': +1, 'USDCAD': +1, 'USDCHF': +1,
}

FILE_MAP = {p: BASE / f'FX_{p}, 15_merged.csv' for p in ALL_PAIRS}
FILE_MAP['DXY'] = BASE / 'TVC_DXY, 15_merged.csv'

# LON_ATTR: minimum distance (pts) below/above London open for valid setup
LON_ATTR_MIN = 1000

# ATTR_CORE filter thresholds
CORE_GAP_MAX      = 1500
CORE_WAVE_EXT_MAX = 1500

# ATTR_CORE candle / zone params (from dxy_clean_rules / test_attr_core_pairs)
ZONE_MIN_GAP      = 30
ZONE_MIN_WIDTH    = 150
ATTR_MIN_GAP      = 75
ATTR_APPROACH_PTS = 150
ATTR_MIN_REWARD   = 100
ATTR_WINDOW       = (7*60+30, 19*60+30)
PIN_WICK_MULT     = 2.0
MAX_LOOKFORWARD   = 400


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_df(sym):
    path = FILE_MAP[sym]
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['time', 'open', 'high', 'low', 'close']].copy()


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL GENERATORS
# ══════════════════════════════════════════════════════════════════════════════

def scan_gap_rej_rev(df_dxy, news_dates):
    """GAP_REJ and REV signals from improved rules. ATTR/pristine signals excluded."""
    raw = imp.generate_signals_v2(df_dxy, near_edge_tp=True, news_dates=news_dates)
    return [s for s in raw if s['type'].startswith('GAP_REJ') or s['type'].startswith('REV')]


def scan_lon_attr_long(df_dxy, news_dates):
    """LON_ATTR LONG only (SHORT dropped from final strategy)."""
    c_s = df_dxy['close']; o_s = df_dxy['open']
    h_s = df_dxy['high'];  l_s = df_dxy['low']
    body        = (c_s - o_s).abs()
    body_top    = pd.concat([o_s, c_s], axis=1).max(axis=1)
    body_bottom = pd.concat([o_s, c_s], axis=1).min(axis=1)
    hi_wick = h_s - body_top
    lo_wick = body_bottom - l_s
    rng_s   = (h_s - l_s).replace(0, np.nan)
    PMW     = r.PIN_WICK_MULT

    bull_pin = (lo_wick >= body * PMW) & (lo_wick >= hi_wick * 1.5) & rng_s.notna()
    bear_pin = (hi_wick >= body * PMW) & (hi_wick >= lo_wick * 1.5) & rng_s.notna()
    both     = bull_pin & bear_pin
    bull_pin = bull_pin & ~(both & (c_s <= o_s))
    bear_pin = bear_pin & ~(both & (c_s >= o_s))  # noqa: F841

    london_open_price = np.nan
    zone_top = zone_bot = np.nan
    lon_pristine_long = True
    lon_attr_traded   = False
    ENTRY_END         = 18 * 60
    sigs = []

    for i in range(2, len(df_dxy)):
        row = df_dxy.iloc[i]
        cv, ov = row['close'], row['open']
        ts  = row['time']
        hh, mm = ts.hour, ts.minute
        curr_min = hh * 60 + mm
        dow = ts.dayofweek
        in_japan = ((hh == 23) and (mm >= 45)) or (0 <= hh < 6)

        is_lon = (not in_japan and hh == 7 and mm == 0 and dow != 0)
        is_mon = (not in_japan and hh == 6 and mm == 30 and dow == 0)

        if is_lon or is_mon:
            london_open_price = ov
            zone_top          = max(ov, cv)
            zone_bot          = min(ov, cv)
            lon_pristine_long = True
            lon_attr_traded   = False
            continue
        if np.isnan(london_open_price) or in_japan:
            continue
        if not np.isnan(zone_top):
            if ov >= zone_top or cv >= zone_top:
                lon_pristine_long = False

        lon_start = (6*60+30) if dow == 0 else (7*60)
        if not (lon_start < curr_min <= ENTRY_END) or lon_attr_traded:
            continue
        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        dist = (cv - london_open_price) * 10000

        if dist <= -LON_ATTR_MIN and lon_pristine_long and bull_pin.at[i]:
            tp = zone_bot
            if tp > cv:
                sl_d = tp - cv
                sl   = cv - sl_d
                out, exit_px, exit_bar = r.resolve(df_dxy, i, cv, tp, sl, 'long')
                sigs.append({
                    'type':           'LON_ATTR_LONG',
                    'entry_time':     str(ts),
                    'entry':          round(cv, 5),
                    'tp':             round(tp, 5),
                    'sl':             round(sl, 5),
                    'sl_pts':         round(sl_d * 10000),
                    'tp_pts':         round(sl_d * 10000),
                    'london_open':    round(london_open_price, 5),
                    'pristine':       True,
                    'outcome':        out,
                    'exit_px':        round(exit_px, 5),
                    'exit_time':      str(df_dxy.at[exit_bar, 'time']),
                    'bias_1h':        0,
                    'bias_4h':        0,
                    'gap_pts_at_lon': np.nan,
                    'wave_ext_pts':   np.nan,
                })
                lon_attr_traded = True
    return sigs


def _candle_signals_core(df):
    """Pin bar / engulf / 3-bar signals (original zone-based version)."""
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body    = (c - o).abs()
    hi_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lo_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    rng     = h - l
    bull_engulf = ((c > o) & ~(c.shift(1) > o.shift(1)) &
                   (c > o.shift(1)) & (o < c.shift(1)) &
                   (body >= body.shift(1) * 0.8))
    bear_engulf = ((c < o) & ~(c.shift(1) < o.shift(1)) &
                   (c < o.shift(1)) & (o > c.shift(1)) &
                   (body >= body.shift(1) * 0.8))
    bull_pin = (lo_wick >= body * PIN_WICK_MULT) & (hi_wick <= body * 1.5) & (rng > 0)
    bear_pin = (hi_wick >= body * PIN_WICK_MULT) & (lo_wick <= body * 1.5) & (rng > 0)
    bar2r    = (c.shift(2) - o.shift(2)).abs()
    indecsn  = body.shift(1) <= bar2r * 0.5
    bull_3b  = (c.shift(2) < o.shift(2)) & indecsn & (c > o) & (c > o.shift(2))
    bear_3b  = (c.shift(2) > o.shift(2)) & indecsn & (c < o) & (c < o.shift(2))
    bull = (bull_engulf | bull_pin | bull_3b).fillna(False)
    bear = (bear_engulf | bear_pin | bear_3b).fillna(False)
    return bull, bear


def _form_zone(df, i):
    if i < 1:
        return None, None, None
    prev_body = abs(df.at[i-1, 'close'] - df.at[i-1, 'open']) * 10000
    prior_c   = df.at[i-2, 'close'] if (prev_body < 10 and i >= 2) else df.at[i-1, 'close']
    j_o, j_c  = df.at[i, 'open'], df.at[i, 'close']
    gap       = abs(prior_c - j_o) * 10000
    if gap >= ZONE_MIN_GAP:
        zt, zb = max(prior_c, j_o), min(prior_c, j_o)
        bull   = j_o > prior_c
    else:
        zt, zb = max(j_o, j_c), min(j_o, j_c)
        bull   = j_c > j_o
    if abs(zt - zb) * 10000 < 1:
        zt = max(j_o, j_c) + 0.001
        zb = min(j_o, j_c)
    return zt, zb, bull


def _resolve_core(df, entry_idx, entry, tp, sl, direction):
    n = len(df)
    for j in range(entry_idx + 1, min(entry_idx + MAX_LOOKFORWARD, n)):
        h_j, l_j, o_j = df.at[j, 'high'], df.at[j, 'low'], df.at[j, 'open']
        if direction == 'long':
            if o_j <= sl:
                return 'loss', sl, j
            if h_j >= tp and l_j <= sl:
                side = 'win' if abs(o_j - sl) > abs(tp - o_j) else 'loss'
                return side, (tp if side == 'win' else sl), j
            if h_j >= tp:
                return 'win', tp, j
            if l_j <= sl:
                return 'loss', sl, j
        else:
            if o_j >= sl:
                return 'loss', sl, j
            if l_j <= tp and h_j >= sl:
                side = 'win' if abs(o_j - sl) > abs(o_j - tp) else 'loss'
                return side, (tp if side == 'win' else sl), j
            if l_j <= tp:
                return 'win', tp, j
            if h_j >= sl:
                return 'loss', sl, j
    j_last = min(entry_idx + MAX_LOOKFORWARD - 1, n - 1)
    return 'timeout', df.at[j_last, 'close'], j_last


def scan_attr_core(df_dxy, news_dates):
    """
    ATTR_CORE signals — 23:45 zone formation, CORE filter applied.
    gap_pts_at_lon < 1500 pts AND wave_ext < 1500 pts.
    Signals renamed ATTR_CORE_LONG / ATTR_CORE_SHORT.
    """
    df = df_dxy.copy().reset_index(drop=True)
    bull_sig, bear_sig = _candle_signals_core(df)

    zone_top       = np.nan
    zone_bottom    = np.nan
    japan_bull     = False
    attr_pristine  = False
    attr_traded    = False
    in_trade_until = -1
    lon_open_close = np.nan
    gap_pts_at_lon = 0.0

    sigs = []
    n = len(df)

    for i in range(2, n):
        row  = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']
        ts   = row['time']
        hour, minute = ts.hour, ts.minute
        curr_min = hour * 60 + minute
        dow  = ts.dayofweek

        is_2345  = (hour == 23) and (minute == 45)
        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        if is_2345:
            zt, zb, jb = _form_zone(df, i)
            if zt is not None:
                zone_top, zone_bottom = zt, zb
                japan_bull      = jb
                attr_pristine   = False
                attr_traded     = False
                lon_open_close  = np.nan
                gap_pts_at_lon  = 0.0
            continue

        if np.isnan(zone_top):
            continue

        mon_start     = 6 * 60 + 30
        eff_attr_start = mon_start if dow == 0 else ATTR_WINDOW[0]
        london_open_bar = (not in_japan and
                           ((dow != 0 and curr_min == ATTR_WINDOW[0]) or
                            (dow == 0 and curr_min == mon_start)))

        if london_open_bar:
            if not japan_bull:
                gap = (zone_bottom - c) * 10000
                attr_pristine = gap >= ATTR_MIN_GAP
            else:
                gap = (c - zone_top) * 10000
                attr_pristine = gap >= ATTR_MIN_GAP
            if attr_pristine:
                lon_open_close = c
                gap_pts_at_lon = gap

        if i <= in_trade_until:
            continue

        in_attr_sess = eff_attr_start <= curr_min <= ATTR_WINDOW[1] and not in_japan

        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        zone_width_pts = (zone_top - zone_bottom) * 10000

        if i >= 3:
            c_prev3 = df.at[i - 3, 'close']
            approach_pts = ((c - c_prev3) * 10000 if not japan_bull
                            else (c_prev3 - c) * 10000)
        else:
            approach_pts = 0

        impulsive_approach = approach_pts >= ATTR_APPROACH_PTS

        if not (attr_pristine and in_attr_sess and
                zone_width_pts >= ZONE_MIN_WIDTH and not attr_traded):
            continue

        reward_long  = (zone_top    - c) * 10000
        reward_short = (c - zone_bottom) * 10000

        if not np.isnan(lon_open_close):
            wave_ext = ((lon_open_close - c) * 10000 if not japan_bull
                        else (c - lon_open_close) * 10000)
            wave_ext = max(wave_ext, 0.0)
        else:
            wave_ext = 0.0

        core_ok = (gap_pts_at_lon < CORE_GAP_MAX and wave_ext < CORE_WAVE_EXT_MAX)
        if not core_ok:
            continue

        if (not japan_bull and bull_sig.at[i] and impulsive_approach
                and reward_long >= ATTR_MIN_REWARD):
            tp_price = zone_top
            sl_d     = tp_price - c
            sl_price = c - sl_d
            if sl_d > 0:
                out, exit_px, exit_bar = _resolve_core(df, i, c, tp_price, sl_price, 'long')
                sigs.append({
                    'type':           'ATTR_CORE_LONG',
                    'entry_time':     str(ts),
                    'entry':          round(c, 5),
                    'tp':             round(tp_price, 5),
                    'sl':             round(sl_price, 5),
                    'sl_pts':         round(sl_d * 10000),
                    'tp_pts':         round(sl_d * 10000),
                    'london_open':    round(lon_open_close, 5),
                    'pristine':       True,
                    'outcome':        out,
                    'exit_px':        round(exit_px, 5),
                    'exit_time':      str(df.at[exit_bar, 'time']),
                    'bias_1h':        0,
                    'bias_4h':        0,
                    'gap_pts_at_lon': round(gap_pts_at_lon, 1),
                    'wave_ext_pts':   round(wave_ext, 1),
                })
                attr_traded    = True
                in_trade_until = exit_bar
            continue

        if (japan_bull and bear_sig.at[i] and impulsive_approach
                and reward_short >= ATTR_MIN_REWARD):
            tp_price = zone_bottom
            sl_d     = c - tp_price
            sl_price = c + sl_d
            if sl_d > 0:
                out, exit_px, exit_bar = _resolve_core(df, i, c, tp_price, sl_price, 'short')
                sigs.append({
                    'type':           'ATTR_CORE_SHORT',
                    'entry_time':     str(ts),
                    'entry':          round(c, 5),
                    'tp':             round(tp_price, 5),
                    'sl':             round(sl_price, 5),
                    'sl_pts':         round(sl_d * 10000),
                    'tp_pts':         round(sl_d * 10000),
                    'london_open':    round(lon_open_close, 5),
                    'pristine':       True,
                    'outcome':        out,
                    'exit_px':        round(exit_px, 5),
                    'exit_time':      str(df.at[exit_bar, 'time']),
                    'bias_1h':        0,
                    'bias_4h':        0,
                    'gap_pts_at_lon': round(gap_pts_at_lon, 1),
                    'wave_ext_pts':   round(wave_ext, 1),
                })
                attr_traded    = True
                in_trade_until = exit_bar

    return sigs


# ══════════════════════════════════════════════════════════════════════════════
# PAIR APPLICATOR  (runs in worker process)
# ══════════════════════════════════════════════════════════════════════════════

def _apply_pair_worker(args):
    pair, signals, start_ts_str, news_dates = args
    path = FILE_MAP[pair]
    if not Path(path).exists():
        return pair, []

    df_pair = pd.read_csv(path)
    df_pair['time'] = pd.to_datetime(df_pair['time'], utc=True)
    df_pair = df_pair.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df_pair[col] = df_pair[col].astype(float)

    F = PAIR_FACTOR[pair]
    D = PAIR_DIR[pair]
    pair_idx  = {str(t): i for i, t in enumerate(df_pair['time'])}
    start_ts  = pd.Timestamp(start_ts_str, tz='UTC') if start_ts_str else None
    results   = []

    for sig in signals:
        et = sig['entry_time']
        xt = sig.get('exit_time')
        if start_ts and pd.Timestamp(et[:19], tz='UTC') < start_ts:
            continue
        if et not in pair_idx or not xt or xt not in pair_idx:
            continue
        if news_dates and r.news_blocks_pair(news_dates, et, pair):
            continue

        pi, xi      = pair_idx[et], pair_idx[xt]
        pc          = df_pair.at[pi, 'close']
        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        pair_sl_d   = sig['sl_pts'] / 10000 * F

        # Scan forward: exit at pair SL (-1R max loss) or DXY exit bar, whichever first
        pair_sl_px = pc - pair_sl_d if pair_long else pc + pair_sl_d
        r_actual = None
        for j in range(pi + 1, xi + 1):
            l_j = df_pair.at[j, 'low']
            h_j = df_pair.at[j, 'high']
            if pair_long and l_j <= pair_sl_px:
                r_actual = -1.0
                break
            elif not pair_long and h_j >= pair_sl_px:
                r_actual = -1.0
                break

        if r_actual is None:
            px       = df_pair.at[xi, 'close']
            raw_pnl  = (px - pc) if pair_long else (pc - px)
            r_actual = raw_pnl / pair_sl_d if pair_sl_d > 0 else 0.0

        outcome = 'win' if r_actual > 0 else ('loss' if r_actual < 0 else 'even')

        results.append({
            'signal':         sig['type'],
            'entry_time':     et,
            'exit_time':      xt,
            'dxy_outcome':    sig['outcome'],
            'pair':           pair,
            'direction':      'long' if pair_long else 'short',
            'pair_entry':     round(pc, 5),
            'pair_exit':      round(df_pair.at[xi, 'close'], 5),
            'sl_pts_dxy':     sig['sl_pts'],
            'outcome':        outcome,
            'r_actual':       round(r_actual, 3),
            'gap_pts_at_lon': sig.get('gap_pts_at_lon', np.nan),
            'wave_ext_pts':   sig.get('wave_ext_pts', np.nan),
            'dxy_entry':      sig.get('entry', np.nan),
            'dxy_tp':         sig.get('tp', np.nan),
            'dxy_sl':         sig.get('sl', np.nan),
            'london_open':    sig.get('london_open', np.nan),
        })

    return pair, results


# ══════════════════════════════════════════════════════════════════════════════
# STATS
# ══════════════════════════════════════════════════════════════════════════════

def stats(trades):
    if not trades:
        return dict(N=0, W=0, L=0, WR=float('nan'), NetR=0.0, AvgW=0.0, AvgL=0.0, PF=float('inf'))
    df   = pd.DataFrame(trades)
    wins = df[df['r_actual'] > 0]
    loss = df[df['r_actual'] < 0]
    w, l = len(wins), len(loss)
    wr   = w / (w + l) * 100 if (w + l) > 0 else float('nan')
    gw   = wins['r_actual'].sum()
    gl   = loss['r_actual'].abs().sum()
    pf   = gw / gl if gl > 0 else float('inf')
    return dict(N=len(df), W=w, L=l, WR=wr,
                NetR=df['r_actual'].sum(),
                AvgW=gw/w if w else 0.0,
                AvgL=gl/l if l else 0.0,
                PF=pf)


def fmt_wr(v): return f"{v:.1f}%" if not (isinstance(v, float) and np.isnan(v)) else "  n/a"
def fmt_pf(v): return f"{v:.2f}" if v != float('inf') else "  inf"


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print(f"Workers: {cpu_count()} available | {N_WORKERS} used")

    # ── 1. Load DXY + generate all signal types ────────────────────────────────
    print("\nLoading DXY data...")
    df_dxy     = imp.load_merged('DXY').reset_index(drop=True)
    news_dates = r.load_news_filter()
    months     = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
    print(f"  {len(df_dxy):,} bars  |  "
          f"{df_dxy['time'].min().date()} to {df_dxy['time'].max().date()}  ({months:.1f} months)")

    print("\nGenerating signals...")
    sigs_gap_rev  = scan_gap_rej_rev(df_dxy, news_dates)
    sigs_lon_attr = scan_lon_attr_long(df_dxy, news_dates)
    sigs_core     = scan_attr_core(df_dxy, news_dates)
    if sigs_core is None:
        sigs_core = []

    all_sigs = sigs_gap_rev + sigs_lon_attr + (sigs_core or [])

    def ct(prefix): return sum(1 for s in all_sigs if s['type'].startswith(prefix))
    print(f"  GAP_REJ:    {ct('GAP_REJ'):>4} signals")
    print(f"  REV:        {ct('REV'):>4} signals")
    print(f"  LON_ATTR:   {ct('LON_ATTR'):>4} signals")
    print(f"  ATTR_CORE:  {ct('ATTR_CORE'):>4} signals")
    print(f"  TOTAL:      {len(all_sigs):>4} signals")

    # ── 2. Determine data windows ──────────────────────────────────────────────
    print("\nChecking pair data windows...")
    pair_info = {}
    for p in ALL_PAIRS:
        path = FILE_MAP[p]
        if not Path(str(path)).exists():
            pair_info[p] = dict(exists=False, start=None, full=False)
            continue
        df_tmp = pd.read_csv(path, usecols=['time'])
        df_tmp['time'] = pd.to_datetime(df_tmp['time'], utc=True)
        start = df_tmp['time'].min()
        full  = start < pd.Timestamp('2024-01-01', tz='UTC')
        pair_info[p] = dict(exists=True, start=start, full=full)
        tag = 'FULL' if full else 'PARTIAL'
        print(f"  {p:<8}: {tag}  (from {start.date()})")

    available_pairs = [p for p in ALL_PAIRS if pair_info[p]['exists']]
    short_pairs     = [p for p in available_pairs if not pair_info[p]['full']]

    if short_pairs:
        global_start = max(pair_info[p]['start'] for p in short_pairs)
        print(f"\n  Short-window start: {global_start.date()} (limited by {', '.join(short_pairs)})")
        # Filter signals to common window for the short-window run
        short_sigs = [s for s in all_sigs
                      if pd.Timestamp(s['entry_time'][:19], tz='UTC') >= global_start]
        print(f"  Signals in common window: {len(short_sigs)} of {len(all_sigs)}")
    else:
        global_start = None
        short_sigs   = all_sigs
        print("  All 8 pairs have full data.")

    # ── 3. Run pair applicators in parallel ────────────────────────────────────
    print(f"\nSpawning {N_WORKERS} worker processes...")
    jobs = [(p, all_sigs if pair_info[p]['full'] else short_sigs,
             None if pair_info[p]['full'] else str(global_start),
             news_dates)
            for p in available_pairs]

    with Pool(processes=N_WORKERS) as pool:
        raw = pool.map(_apply_pair_worker, jobs)

    all_trades = []
    pair_trades = {}
    for pair, trades in raw:
        pair_trades[pair] = trades
        all_trades.extend(trades)

    print(f"Done. {len(all_trades):>6} total pair trades across {len(available_pairs)} pairs.")

    # ── 4. Print console summary ───────────────────────────────────────────────
    SIGNAL_TYPES = ['GAP_REJ_LONG', 'GAP_REJ_SHORT', 'REV_LONG', 'REV_SHORT',
                    'LON_ATTR_LONG', 'ATTR_CORE_LONG', 'ATTR_CORE_SHORT']

    rpt = ACCOUNT * RISK_PCT
    w_months = months if not short_pairs else (df_dxy['time'].max() - global_start).days / 30.44

    print()
    print("=" * 90)
    print(f"  COMBINED RESULTS — ALL SIGNAL TYPES  ({w_months:.1f}-month common window)")
    print("=" * 90)
    print(f"  {'Signal':<18}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR%':>7}  {'PF':>5}  "
          f"{'NetR':>8}  {'AvgW':>7}  {'AvgL':>7}")
    print(f"  {'-'*82}")
    for sig in SIGNAL_TYPES:
        t = [x for x in all_trades if x['signal'] == sig]
        s = stats(t)
        if s['N'] == 0:
            print(f"  {sig:<18}  {'--':>4}  {'--':>4}  {'--':>4}  {'  n/a':>7}  {'  --':>5}  "
                  f"{'  --':>8}  {'  --':>7}  {'  --':>7}")
        else:
            print(f"  {sig:<18}  {s['N']:>4}  {s['W']:>4}  {s['L']:>4}  "
                  f"{fmt_wr(s['WR']):>7}  {fmt_pf(s['PF']):>5}  "
                  f"{s['NetR']:>+8.1f}R  {s['AvgW']:>+6.2f}R  {s['AvgL']:>-6.2f}R")
    print(f"  {'-'*82}")
    s_all = stats(all_trades)
    print(f"  {'TOTAL':<18}  {s_all['N']:>4}  {s_all['W']:>4}  {s_all['L']:>4}  "
          f"{fmt_wr(s_all['WR']):>7}  {fmt_pf(s_all['PF']):>5}  "
          f"{s_all['NetR']:>+8.1f}R  {s_all['AvgW']:>+6.2f}R  {s_all['AvgL']:>-6.2f}R")

    print()
    print("=" * 90)
    print(f"  BY PAIR — All signals combined")
    print("=" * 90)
    print(f"  {'Pair':<10}  {'Data':<8}  {'N':>4}  {'WR%':>7}  {'PF':>5}  "
          f"{'NetR':>8}  {'R/mo':>8}  {'$P&L':>10}  {'$/mo':>9}")
    print(f"  {'-'*80}")
    pair_rows = []
    for p in available_pairs:
        t = pair_trades[p]
        s = stats(t)
        tag = 'FULL' if pair_info[p]['full'] else 'PARTIAL'
        pm  = s['NetR'] / w_months if w_months else 0.0
        dollar = s['NetR'] * rpt
        dpm    = pm * rpt
        pair_rows.append((p, tag, s, pm, dollar, dpm))
        print(f"  {p:<10}  {tag:<8}  {s['N']:>4}  {fmt_wr(s['WR']):>7}  {fmt_pf(s['PF']):>5}  "
              f"{s['NetR']:>+8.1f}R  {pm:>+7.2f}R/mo  ${dollar:>+9,.0f}  ${dpm:>+8,.0f}/mo")
    print(f"  {'-'*80}")
    print(f"  {'TOTAL':<10}  {'':8}  {s_all['N']:>4}  {fmt_wr(s_all['WR']):>7}  "
          f"{fmt_pf(s_all['PF']):>5}  {s_all['NetR']:>+8.1f}R  "
          f"{s_all['NetR']/w_months:>+7.2f}R/mo  ${s_all['NetR']*rpt:>+9,.0f}")

    print()
    print("=" * 90)
    print(f"  SIGNAL × PAIR BREAKDOWN")
    print("=" * 90)
    print(f"  {'Signal':<18}  {'Pair':<10}  {'N':>4}  {'WR%':>7}  {'NetR':>8}")
    print(f"  {'-'*60}")
    for sig in SIGNAL_TYPES:
        first = True
        for p in available_pairs:
            t = [x for x in pair_trades[p] if x['signal'] == sig]
            if not t:
                continue
            s = stats(t)
            label = sig if first else ''
            print(f"  {label:<18}  {p:<10}  {s['N']:>4}  {fmt_wr(s['WR']):>7}  {s['NetR']:>+8.1f}R")
            first = False
        if not first:
            print(f"  {'-'*60}")

    # ── 5. Save trade log ──────────────────────────────────────────────────────
    log_path = BASE / 'combined_trade_log.csv'
    df_log   = pd.DataFrame(all_trades)
    # Sort by entry_time then pair
    df_log['_ts'] = pd.to_datetime(df_log['entry_time'])
    df_log = df_log.sort_values(['_ts', 'pair']).drop(columns=['_ts'])
    # Reorder columns for readability
    cols = ['signal', 'entry_time', 'exit_time', 'pair', 'direction',
            'pair_entry', 'pair_exit', 'r_actual', 'outcome',
            'sl_pts_dxy', 'dxy_outcome',
            'dxy_entry', 'dxy_tp', 'dxy_sl', 'london_open',
            'gap_pts_at_lon', 'wave_ext_pts']
    df_log = df_log[[c for c in cols if c in df_log.columns]]
    df_log.to_csv(log_path, index=False)
    print(f"\n  Saved: combined_trade_log.csv  ({len(df_log):,} rows)")

    # ── 6. Save summary CSV ────────────────────────────────────────────────────
    summary_rows = []
    for sig in SIGNAL_TYPES:
        for p in available_pairs:
            t = [x for x in pair_trades[p] if x['signal'] == sig]
            s = stats(t)
            summary_rows.append({
                'signal': sig,
                'pair':   p,
                'data':   'FULL' if pair_info[p]['full'] else 'PARTIAL',
                'N':      s['N'], 'W': s['W'], 'L': s['L'],
                'WR_pct': round(s['WR'], 1) if not np.isnan(s['WR']) else None,
                'PF':     round(s['PF'], 3) if s['PF'] != float('inf') else None,
                'NetR':   round(s['NetR'], 2),
                'AvgW':   round(s['AvgW'], 3),
                'AvgL':   round(s['AvgL'], 3),
                'R_per_month': round(s['NetR'] / w_months, 3) if w_months else 0,
            })
    # Portfolio totals per signal
    for sig in SIGNAL_TYPES:
        t = [x for x in all_trades if x['signal'] == sig]
        s = stats(t)
        summary_rows.append({
            'signal': sig, 'pair': 'ALL',
            'data':   'ALL',
            'N': s['N'], 'W': s['W'], 'L': s['L'],
            'WR_pct': round(s['WR'], 1) if not np.isnan(s['WR']) else None,
            'PF':     round(s['PF'], 3) if s['PF'] != float('inf') else None,
            'NetR':   round(s['NetR'], 2),
            'AvgW':   round(s['AvgW'], 3),
            'AvgL':   round(s['AvgL'], 3),
            'R_per_month': round(s['NetR'] / w_months, 3) if w_months else 0,
        })

    sum_path = BASE / 'combined_summary.csv'
    pd.DataFrame(summary_rows).to_csv(sum_path, index=False)
    print(f"  Saved: combined_summary.csv  ({len(summary_rows):,} rows)")
    print()
    print(f"  Total net R (all signals, all pairs): {s_all['NetR']:>+.1f}R")
    print(f"  Est. $ P&L (0.25% risk / $100k):    ${s_all['NetR'] * rpt:>+,.0f}  "
          f"over {w_months:.1f} months")
    print()
