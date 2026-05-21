"""
DXY Approach 1 — Pair-Native Strategy
======================================
Runs the full DXY zone strategy independently on each of the 8 correlated
pairs using each pair's OWN price data:
  • Each pair forms its own 23:45 GMT open zone
  • Japan session monitoring, pristine/body-clean tracking
  • Candle patterns, divergence scorer, ADX gate — all on pair data
  • Same thresholds as the DXY strategy

Distance normalisation:
  All price distances are converted to "DXY-equivalent pts" so that
  ATTR_MAX_PTS=400, zone_min_gap=30, REV_MIN_SL=3000 etc. carry the
  same economic meaning on every pair:
      pair_pts = (price_distance / PAIR_FACTOR) * 10000

Outputs:
  1. Pair-native signals  — each pair acting as its own trigger
  2. DXY-triggered signals — the 17 DXY signals applied to pairs (reference)
  3. Confluence            — same date: DXY fires AND the pair fires in the
                             same implied direction

The confluence win rate tests whether pair-native structure adds
independent confirmation value on top of the DXY trigger.
"""

import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from dxy_backtest import (
    CSV_PATH,
    ATTR_ENABLED, ATTR_MIN_PTS, ATTR_MAX_PTS, ZONE_MIN_GAP,
    REV_ENABLED, REV_MIN_SL, REV_MAX_DIST, REV_MIN_BODY, REV_MIN_RANGE,
    ENTRY_START_H, ENTRY_START_M, ENTRY_END_H, ENTRY_END_M,
    REV_END_H, REV_END_M, MONDAY_START_H, JAPAN_END_H,
    USE_ENGULF, USE_PIN, USE_3BAR, PIN_WICK_MULT,
    DIV_LOOKBACK, REV_MIN_DIV, USE_ADX_GATE, ADX_MIN,
    MAX_LOOKFORWARD, EXIT_MODE,
    compute_indicators, div_score_bull, div_score_bear,
    candle_patterns, session_flags, form_zone, resolve_trade,
)

# ── Pair configuration ───────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(CSV_PATH)

PAIR_FILES = {
    'EURUSD': os.path.join(BASE_DIR, 'FX_EURUSD, 15 (1).csv'),
    'GBPUSD': os.path.join(BASE_DIR, 'FX_GBPUSD, 15 (1).csv'),
    'AUDUSD': os.path.join(BASE_DIR, 'FX_AUDUSD, 15 (1).csv'),
    'NZDUSD': os.path.join(BASE_DIR, 'FX_NZDUSD, 15 (1).csv'),
    'USDCAD': os.path.join(BASE_DIR, 'FX_USDCAD, 15 (1).csv'),
    'USDCHF': os.path.join(BASE_DIR, 'FX_USDCHF, 15 (1).csv'),
    'USDJPY': os.path.join(BASE_DIR, 'FX_USDJPY, 15 (1).csv'),
    'XAUUSD': os.path.join(BASE_DIR, 'FX_XAUUSD, 15 (1).csv'),
}

PAIR_DIRECTION = {
    'EURUSD': -1, 'GBPUSD': -1, 'AUDUSD': -1, 'NZDUSD': -1,
    'USDCAD': +1, 'USDCHF': +1, 'USDJPY': +1, 'XAUUSD': -1,
}

DXY_TICK  = 0.001
PAIR_TICK = {
    'EURUSD': 0.00001, 'GBPUSD': 0.00001, 'AUDUSD': 0.00001, 'NZDUSD': 0.00001,
    'USDCAD': 0.00001, 'USDCHF': 0.00001,
    'USDJPY': 0.001,
    'XAUUSD': 0.01,
}
XAUUSD_MULT = 10
PAIR_FACTOR = {
    p: (PAIR_TICK[p] / DXY_TICK) * (XAUUSD_MULT if p == 'XAUUSD' else 1)
    for p in PAIR_TICK
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_pair(filepath):
    df = pd.read_csv(filepath, low_memory=False)
    df = df[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
    return df


def trade_date(entry_time_str):
    """Extract YYYY-MM-DD zone-day from an ISO timestamp string."""
    return entry_time_str[:10]


# ── Pair-native strategy runner ───────────────────────────────────────────────

def run_pair_native(pair, df_pair, pair_factor):
    """
    Run the full DXY zone strategy on a single pair's own price data.
    All distance thresholds normalised via pair_factor so they match DXY.
    Returns list of trade dicts.
    """
    # Precompute indicators on pair data
    df = compute_indicators(df_pair.copy())
    df['bull_div'] = div_score_bull(df, DIV_LOOKBACK)
    df['bear_div'] = div_score_bear(df, DIV_LOOKBACK)
    df['bull_sig'], df['bear_sig'] = candle_patterns(df)
    sess = session_flags(df)
    df   = pd.concat([df, sess], axis=1)

    # Normalisation: convert price distances to DXY-equivalent pts
    # pair_pts = price_distance / pair_factor * 10000
    F = pair_factor   # shorthand

    zone_top = zone_bottom = np.nan
    japan_bull = False
    zone_pristine = zone_body_clean = False
    japan_candle_cnt = 0
    zone_traded = False
    in_trade_until = -1
    n = len(df)
    trades = []

    for i in range(2, n):
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']

        # ── Zone formation at 23:45 ──────────────────────────────────────
        if row['is_2345']:
            # form_zone uses raw dxy_backtest logic with *10000 internally;
            # replicate here using normalised pts for the pair
            prior_body = abs(df.at[i-1, 'close'] - df.at[i-1, 'open']) / F * 10000
            prior_is_line = prior_body < 10
            prior_close = df.at[i-2, 'close'] if prior_is_line else df.at[i-1, 'close']
            j_open = o
            gap = abs(prior_close - j_open) / F * 10000

            if gap >= ZONE_MIN_GAP:
                zone_top    = max(prior_close, j_open)
                zone_bottom = min(prior_close, j_open)
                japan_bull  = j_open > prior_close
            else:
                zone_top    = max(j_open, c)
                zone_bottom = min(j_open, c)
                japan_bull  = c > j_open

            zone_pristine = True
            zone_body_clean = True
            japan_candle_cnt = 0
            zone_traded = False
            continue

        if np.isnan(zone_top):
            continue

        # ── Zone state maintenance ────────────────────────────────────────
        if row['in_japan']:
            japan_candle_cnt += 1
            if zone_body_clean and japan_candle_cnt > 3:
                if zone_bottom <= c <= zone_top:
                    zone_body_clean = False

        if zone_pristine:
            if japan_bull:
                if c < zone_bottom: zone_pristine = False
            else:
                if c > zone_top:    zone_pristine = False

        if zone_traded or i <= in_trade_until:
            continue

        # ── Normalised distances ─────────────────────────────────────────
        dist_tp_long   = (zone_top    - c) / F * 10000
        dist_tp_short  = (c - zone_bottom) / F * 10000
        dist_rev_long  = abs(c - zone_bottom) / F * 10000
        dist_rev_short = abs(zone_top - c)    / F * 10000

        adx_4h = row.get('adx_4h', np.nan)
        adx_ok = (not USE_ADX_GATE) or (not np.isnan(adx_4h) and adx_4h >= ADX_MIN)

        body_pts  = abs(c - o) / F * 10000
        range_pts = (h - l)   / F * 10000
        rev_candle_ok = (body_pts >= REV_MIN_BODY) and (range_pts >= REV_MIN_RANGE)

        sig = None

        # ── ATTRACTION ────────────────────────────────────────────────────
        if (ATTR_ENABLED and zone_body_clean and zone_pristine and
                row['in_sess'] and not row['in_japan']):

            if (not japan_bull and row['bull_sig'] and
                    ATTR_MIN_PTS <= dist_tp_long <= ATTR_MAX_PTS):
                sl_d = zone_top - c
                tp   = zone_top
                sl   = c - sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'long')
                sig = dict(
                    type='ATTR_LONG', native_direction=+1,
                    entry_time=row['time'], entry_price=round(c, 6),
                    tp=round(tp, 6), sl=round(sl, 6),
                    sl_d=sl_d, sl_pts=round(dist_tp_long),
                    zone_top=round(zone_top, 6), zone_bottom=round(zone_bottom, 6),
                    outcome=outcome, exit_price=round(exit_px, 6),
                    pnl_pts=round((exit_px-c)/F*10000 if outcome=='win'
                                  else (c-exit_px)/F*10000*-1),
                )
                zone_traded = True; in_trade_until = eb

            elif (japan_bull and row['bear_sig'] and
                    ATTR_MIN_PTS <= dist_tp_short <= ATTR_MAX_PTS):
                sl_d = c - zone_bottom
                tp   = zone_bottom
                sl   = c + sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'short')
                sig = dict(
                    type='ATTR_SHORT', native_direction=-1,
                    entry_time=row['time'], entry_price=round(c, 6),
                    tp=round(tp, 6), sl=round(sl, 6),
                    sl_d=sl_d, sl_pts=round(dist_tp_short),
                    zone_top=round(zone_top, 6), zone_bottom=round(zone_bottom, 6),
                    outcome=outcome, exit_price=round(exit_px, 6),
                    pnl_pts=round((c-exit_px)/F*10000 if outcome=='win'
                                  else (exit_px-c)/F*10000*-1),
                )
                zone_traded = True; in_trade_until = eb

        # ── REVERSAL ─────────────────────────────────────────────────────
        if sig is None and (REV_ENABLED and not zone_pristine and
                row['in_rev_sess'] and not row['in_japan'] and adx_ok and rev_candle_ok):

            bull_ok = (row['bull_sig'] and row['bull_div'] >= REV_MIN_DIV
                       and dist_rev_long <= REV_MAX_DIST)
            bear_ok = (row['bear_sig'] and row['bear_div'] >= REV_MIN_DIV
                       and dist_rev_short <= REV_MAX_DIST)

            if bull_ok:
                min_d = REV_MIN_SL / F / 10000
                sl_d  = max(c - zone_bottom, min_d)
                tp    = c + sl_d; sl = c - sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'long')
                sig = dict(
                    type='REV_LONG', native_direction=+1,
                    entry_time=row['time'], entry_price=round(c, 6),
                    tp=round(tp, 6), sl=round(sl, 6),
                    sl_d=sl_d, sl_pts=round(dist_rev_long),
                    zone_top=round(zone_top, 6), zone_bottom=round(zone_bottom, 6),
                    outcome=outcome, exit_price=round(exit_px, 6),
                    pnl_pts=round((exit_px-c)/F*10000 if outcome=='win'
                                  else (c-exit_px)/F*10000*-1),
                )
                zone_traded = True; in_trade_until = eb

            elif bear_ok:
                min_d = REV_MIN_SL / F / 10000
                sl_d  = max(zone_top - c, min_d)
                tp    = c - sl_d; sl = c + sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'short')
                sig = dict(
                    type='REV_SHORT', native_direction=-1,
                    entry_time=row['time'], entry_price=round(c, 6),
                    tp=round(tp, 6), sl=round(sl, 6),
                    sl_d=sl_d, sl_pts=round(dist_rev_short),
                    zone_top=round(zone_top, 6), zone_bottom=round(zone_bottom, 6),
                    outcome=outcome, exit_price=round(exit_px, 6),
                    pnl_pts=round((c-exit_px)/F*10000 if outcome=='win'
                                  else (exit_px-c)/F*10000*-1),
                )
                zone_traded = True; in_trade_until = eb

        if sig:
            sig['pair'] = pair
            trades.append(sig)

    return trades


# ── DXY signal runner ─────────────────────────────────────────────────────────

def run_dxy_signals():
    df_raw = pd.read_csv(CSV_PATH, low_memory=False)
    df = df_raw[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
    df = compute_indicators(df)
    df['bull_div'] = div_score_bull(df, DIV_LOOKBACK)
    df['bear_div'] = div_score_bear(df, DIV_LOOKBACK)
    df['bull_sig'], df['bear_sig'] = candle_patterns(df)
    sess = session_flags(df)
    df   = pd.concat([df, sess], axis=1)

    signals = []
    zone_top = zone_bottom = np.nan
    japan_bull = False
    zone_pristine = zone_body_clean = False
    japan_candle_cnt = 0
    zone_traded = False
    in_trade_until = -1
    n = len(df)

    for i in range(2, n):
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']

        if row['is_2345']:
            zt, zb, jb = form_zone(df, i)
            if zt is not None:
                zone_top = zt; zone_bottom = zb; japan_bull = jb
                zone_pristine = True; zone_body_clean = True
                japan_candle_cnt = 0; zone_traded = False
            continue

        if np.isnan(zone_top): continue

        if row['in_japan']:
            japan_candle_cnt += 1
            if zone_body_clean and japan_candle_cnt > 3:
                if zone_bottom <= c <= zone_top: zone_body_clean = False

        if zone_pristine:
            if japan_bull:
                if c < zone_bottom: zone_pristine = False
            else:
                if c > zone_top:    zone_pristine = False

        if zone_traded or i <= in_trade_until: continue

        dist_tp_long   = (zone_top    - c) * 10000
        dist_tp_short  = (c - zone_bottom) * 10000
        dist_rev_long  = abs(c - zone_bottom) * 10000
        dist_rev_short = abs(zone_top - c)    * 10000
        adx_4h = row.get('adx_4h', np.nan)
        adx_ok = (not USE_ADX_GATE) or (not np.isnan(adx_4h) and adx_4h >= ADX_MIN)
        body_pts  = abs(c - o) * 10000
        range_pts = (h - l)   * 10000
        rev_candle_ok = (body_pts >= REV_MIN_BODY) and (range_pts >= REV_MIN_RANGE)

        sig = None
        if (ATTR_ENABLED and zone_body_clean and zone_pristine and
                row['in_sess'] and not row['in_japan']):
            if (not japan_bull and row['bull_sig'] and
                    ATTR_MIN_PTS <= dist_tp_long <= ATTR_MAX_PTS):
                sl_d = zone_top - c
                tp = zone_top; sl = c - sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'long')
                sig = dict(type='ATTR_LONG', dxy_direction=+1,
                           entry_time=row['time'], outcome=outcome,
                           sl_d=sl_d, sl_pts=round(dist_tp_long))
                zone_traded = True; in_trade_until = eb
            elif (japan_bull and row['bear_sig'] and
                    ATTR_MIN_PTS <= dist_tp_short <= ATTR_MAX_PTS):
                sl_d = c - zone_bottom
                tp = zone_bottom; sl = c + sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'short')
                sig = dict(type='ATTR_SHORT', dxy_direction=-1,
                           entry_time=row['time'], outcome=outcome,
                           sl_d=sl_d, sl_pts=round(dist_tp_short))
                zone_traded = True; in_trade_until = eb

        if sig is None and (REV_ENABLED and not zone_pristine and
                row['in_rev_sess'] and not row['in_japan'] and adx_ok and rev_candle_ok):
            bull_ok = (row['bull_sig'] and row['bull_div'] >= REV_MIN_DIV
                       and dist_rev_long <= REV_MAX_DIST)
            bear_ok = (row['bear_sig'] and row['bear_div'] >= REV_MIN_DIV
                       and dist_rev_short <= REV_MAX_DIST)
            min_d = REV_MIN_SL / 10000.0
            if bull_ok:
                sl_d = max(c - zone_bottom, min_d)
                tp = c + sl_d; sl = c - sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'long')
                sig = dict(type='REV_LONG', dxy_direction=+1,
                           entry_time=row['time'], outcome=outcome,
                           sl_d=sl_d, sl_pts=round(dist_rev_long))
                zone_traded = True; in_trade_until = eb
            elif bear_ok:
                sl_d = max(zone_top - c, min_d)
                tp = c - sl_d; sl = c + sl_d
                outcome, exit_px, eb = resolve_trade(df, i, c, tp, sl, 'short')
                sig = dict(type='REV_SHORT', dxy_direction=-1,
                           entry_time=row['time'], outcome=outcome,
                           sl_d=sl_d, sl_pts=round(dist_rev_short))
                zone_traded = True; in_trade_until = eb

        if sig:
            signals.append(sig)

    return signals


# ── Main ─────────────────────────────────────────────────────────────────────

def run():
    print("Running DXY signal detection...")
    dxy_signals = run_dxy_signals()
    dxy_dates   = {trade_date(s['entry_time']): s for s in dxy_signals}
    print(f"  {len(dxy_signals)} DXY signals on {len(dxy_dates)} dates")

    pair_native = {}
    for pair, fpath in PAIR_FILES.items():
        print(f"Running pair-native strategy on {pair}...")
        df_pair = load_pair(fpath)
        trades  = run_pair_native(pair, df_pair, PAIR_FACTOR[pair])
        pair_native[pair] = trades
        print(f"  {pair}: {len(trades)} native signals")

    return dxy_signals, dxy_dates, pair_native


# ── Reporting ─────────────────────────────────────────────────────────────────

def stats(trades, outcome_key='outcome'):
    n = len(trades)
    if n == 0: return 0, 0.0, 0.0, 0, 0
    w = sum(1 for t in trades if t[outcome_key] == 'win')
    l = sum(1 for t in trades if t[outcome_key] == 'loss')
    wr = w / n * 100
    pf = w / l if l else float('inf')
    return n, wr, pf, w, l


def report(dxy_signals, dxy_dates, pair_native):
    sep  = '=' * 74
    sep2 = '-' * 74

    # ── 1. DXY reference ──────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  1. DXY SIGNALS  (reference)")
    print(sep)
    n, wr, pf, w, l = stats(dxy_signals, 'outcome')
    print(f"  {n} signals  |  WR {wr:.1f}%  |  PF {pf:.3f}  |  W:{w} L:{l}")
    print()
    for typ in ['ATTR_LONG', 'ATTR_SHORT', 'REV_LONG', 'REV_SHORT']:
        sub = [s for s in dxy_signals if s['type'] == typ]
        if not sub: continue
        sn, swr, spf, sw, sl_ = stats(sub, 'outcome')
        print(f"  {typ:<12}: {sn:2d} trades  WR {swr:5.1f}%  PF {spf:.3f}")

    # ── 2. Pair-native results ────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  2. PAIR-NATIVE RESULTS  (each pair acts as its own DXY)")
    print(sep)
    print(f"  {'Pair':<8}  {'N':>3}  {'WR':>7}  {'PF':>8}  "
          f"{'W':>3}  {'L':>3}  {'T':>3}  {'ATTR WR':>8}  {'REV WR':>8}")
    print(f"  {sep2[:70]}")

    for pair in PAIR_FILES:
        trades = pair_native[pair]
        n, wr, pf, w, l = stats(trades)
        t_ = sum(1 for t in trades if t['outcome'] == 'timeout')
        attr_t = [t for t in trades if t['type'].startswith('ATTR')]
        rev_t  = [t for t in trades if t['type'].startswith('REV')]
        a_wr   = f"{sum(1 for t in attr_t if t['outcome']=='win')/len(attr_t)*100:.0f}%" if attr_t else '-'
        r_wr   = f"{sum(1 for t in rev_t  if t['outcome']=='win')/len(rev_t )*100:.0f}%" if rev_t  else '-'
        print(f"  {pair:<8}  {n:>3}  {wr:>6.1f}%  {pf:>8.3f}  "
              f"{w:>3}  {l:>3}  {t_:>3}  {a_wr:>8}  {r_wr:>8}")

    # ── Breakdown: ATTR vs REV per pair ────────────────────────────────
    for sig_group, label in [('ATTR', 'ATTRACTION'), ('REV', 'REVERSAL')]:
        print(f"\n  {label} — pair-native")
        print(f"  {'Pair':<8}  {'N':>3}  {'WR':>7}  {'PF':>8}  "
              f"{'LONG':>6}  {'SHORT':>6}")
        print(f"  {'-'*46}")
        for pair in PAIR_FILES:
            sub = [t for t in pair_native[pair] if t['type'].startswith(sig_group)]
            if not sub:
                print(f"  {pair:<8}  —"); continue
            sn, swr, spf, sw, sl_ = stats(sub)
            long_  = sum(1 for t in sub if 'LONG'  in t['type'])
            short_ = sum(1 for t in sub if 'SHORT' in t['type'])
            print(f"  {pair:<8}  {sn:>3}  {swr:>6.1f}%  {spf:>8.3f}  "
                  f"{long_:>6}  {short_:>6}")

    # ── 3. Confluence analysis ─────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  3. CONFLUENCE  (DXY signal date + pair-native signal same date,")
    print(f"                  aligned direction)")
    print(sep)

    print(f"\n  {'Pair':<8}  {'Conf N':>6}  {'Conf WR':>8}  {'Conf PF':>8}  "
          f"{'Non-Conf N':>10}  {'Non-Conf WR':>11}  {'Lift':>6}")
    print(f"  {sep2[:74]}")

    all_conf     = []
    all_non_conf = []

    for pair in PAIR_FILES:
        pair_dir = PAIR_DIRECTION[pair]
        conf_trades     = []
        non_conf_trades = []

        for trade in pair_native[pair]:
            tdate = trade_date(trade['entry_time'])
            if tdate in dxy_dates:
                dxy_sig = dxy_dates[tdate]
                dxy_dir = dxy_sig['dxy_direction']
                # Confluence if implied pair trade direction matches native signal direction
                # Implied pair direction from DXY = dxy_dir * pair_dir
                # Native signal direction = trade['native_direction']
                implied = dxy_dir * pair_dir
                if implied == trade['native_direction']:
                    conf_trades.append(trade)
                else:
                    non_conf_trades.append(trade)
            else:
                non_conf_trades.append(trade)

        all_conf.extend(conf_trades)
        all_non_conf.extend(non_conf_trades)

        cn, cwr, cpf, cw, cl = stats(conf_trades)
        nn, nwr, npf, nw, nl = stats(non_conf_trades)
        lift = cwr - nwr if cn > 0 and nn > 0 else float('nan')
        cwr_s = f"{cwr:.1f}%" if cn else '-'
        cpf_s = f"{cpf:.3f}"  if cn else '-'
        nwr_s = f"{nwr:.1f}%" if nn else '-'
        lift_s= f"{lift:+.1f}pp" if not np.isnan(lift) else '-'
        print(f"  {pair:<8}  {cn:>6}  {cwr_s:>8}  {cpf_s:>8}  "
              f"{nn:>10}  {nwr_s:>11}  {lift_s:>6}")

    print()
    cn, cwr, cpf, cw, cl = stats(all_conf)
    nn, nwr, npf, nw, nl = stats(all_non_conf)
    lift = cwr - nwr
    print(f"  {'ALL PAIRS':<8}  {cn:>6}  {cwr:.1f}%  {cpf:>8.3f}  "
          f"{nn:>10}  {nwr:.1f}%  {lift:>+.1f}pp")

    # ── 4. Per-date confluence table ──────────────────────────────────────
    print(f"\n{sep}")
    print(f"  4. DATE-BY-DATE  (DXY signal dates — where did pair-native agree?)")
    print(sep)
    print(f"  {'Date':<11}  {'DXY Type':<10}  {'DXY':>4}  "
          f"{'Pairs native signal':>20}  {'Agree':>5}  {'Disagree':>8}  {'No signal':>9}")
    print(f"  {sep2[:74]}")

    for sig in dxy_signals:
        tdate    = trade_date(sig['entry_time'])
        dxy_dir  = sig['dxy_direction']
        dxy_out  = 'W' if sig['outcome'] == 'win' else 'L'
        agree_pairs    = []
        disagree_pairs = []
        nosig_pairs    = []

        for pair in PAIR_FILES:
            pair_dir = PAIR_DIRECTION[pair]
            implied  = dxy_dir * pair_dir
            day_trades = [t for t in pair_native[pair]
                          if trade_date(t['entry_time']) == tdate]
            if not day_trades:
                nosig_pairs.append(pair)
            elif day_trades[0]['native_direction'] == implied:
                agree_pairs.append(pair)
            else:
                disagree_pairs.append(pair)

        agree_str = ','.join(p.replace('USD','') for p in agree_pairs) or '-'
        print(f"  {tdate}  {sig['type']:<10}  DXY:{dxy_out}  "
              f"{agree_str:>20}  {len(agree_pairs):>5}  {len(disagree_pairs):>8}  "
              f"{len(nosig_pairs):>9}")

    # ── 5. Summary comparison ────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  5. SUMMARY COMPARISON")
    print(sep)
    dxy_n, dxy_wr, dxy_pf, dxy_w, dxy_l = stats(dxy_signals, 'outcome')
    all_native = [t for trades in pair_native.values() for t in trades]
    an, awr, apf, aw, al = stats(all_native)
    cn, cwr, cpf, cw, cl = stats(all_conf)
    nn, nwr, npf, nw, nl = stats(all_non_conf)

    print(f"  {'Approach':<35}  {'N':>5}  {'WR':>7}  {'PF':>8}")
    print(f"  {'-'*58}")
    print(f"  {'DXY native signals (source)':<35}  {dxy_n:>5}  {dxy_wr:>6.1f}%  {dxy_pf:>8.3f}")
    print(f"  {'All pair-native signals':<35}  {an:>5}  {awr:>6.1f}%  {apf:>8.3f}")
    print(f"  {'Confluence (DXY + pair agree)':<35}  {cn:>5}  {cwr:>6.1f}%  {cpf:>8.3f}")
    print(f"  {'Non-confluence pair signals':<35}  {nn:>5}  {nwr:>6.1f}%  {npf:>8.3f}")
    print(f"\n  Confluence lift over non-confluence: {cwr-nwr:+.1f}pp")
    print(f"  Confluence lift over DXY standalone: {cwr-dxy_wr:+.1f}pp")

    # ── Save CSVs ─────────────────────────────────────────────────────────
    all_rows = []
    for pair, trades in pair_native.items():
        pair_dir = PAIR_DIRECTION[pair]
        for trade in trades:
            tdate   = trade_date(trade['entry_time'])
            dxy_sig = dxy_dates.get(tdate)
            conf    = False
            if dxy_sig:
                implied = dxy_sig['dxy_direction'] * pair_dir
                conf = (implied == trade['native_direction'])
            all_rows.append({**trade, 'confluence': conf,
                              'dxy_on_date': tdate in dxy_dates})
    if all_rows:
        out = pd.DataFrame(all_rows)
        path = os.path.join(BASE_DIR, 'dxy_pair_native_signals.csv')
        out.to_csv(path, index=False)
        print(f"\n  Full signal log saved: {path}")


if __name__ == '__main__':
    dxy_signals, dxy_dates, pair_native = run()
    report(dxy_signals, dxy_dates, pair_native)
