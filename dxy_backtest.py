"""
DXY Zone Strategy — Python Backtester
Replicates DXYZoneStrategy.pine logic against historical 15m CSV data.

Usage:
    python dxy_backtest.py

Outputs:
    - Console summary (overall, attraction, reversal)
    - dxy_backtest_trades.csv  (full trade log)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone

# --- PARAMETERS --------------------------------------------------------------
# Aligned to current DXYZoneStrategy.pine defaults.

CSV_PATH = r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting\TVC_DXY, 15.csv"

# Signal Zones
ATTR_ENABLED  = True
ATTR_MIN_PTS  = 0         # 0 = no minimum (backtest: closer = better)
ATTR_MAX_PTS  = 400       # 400 pts = 97.9% zone return rate
ZONE_MIN_GAP  = 30        # minimum pts to use open-gap zone (vs 23:45 body)
REV_ENABLED   = True
REV_MIN_SL    = 3000      # minimum structural SL in pts
REV_MAX_DIST  = 500       # max distance from zone edge for reversal entry

# Reversal candle quality filter (matches Pine Script rev_min_body / rev_min_range)
REV_MIN_BODY  = 200       # minimum signal candle body in pts
REV_MIN_RANGE = 400       # minimum signal candle range (high-low) in pts

# Session Window (UTC)
ENTRY_START_H, ENTRY_START_M = 7, 30
ENTRY_END_H,   ENTRY_END_M   = 19, 30
REV_END_H,     REV_END_M     = 12, 0
MONDAY_START_H               = 6
JAPAN_END_H                  = 6

# Entry Signals
USE_ENGULF    = True
USE_PIN       = True
USE_3BAR      = True
PIN_WICK_MULT = 2.0

# HTF Bias / Divergence (aligned to Pine Script defaults)
DIV_LOOKBACK  = 30        # Pine Script default
REV_MIN_DIV   = 1         # backtest optimal: 54.5% WR PF 1.200 vs 2=50% WR PF 1.000

# ADX Trend Gate (matches Pine Script use_adx_gate / adx_min)
USE_ADX_GATE  = True
ADX_MIN       = 20.0      # 4H ADX must be >= this value

# Trade outcome look-forward limit (bars)
MAX_LOOKFORWARD = 400     # ~100 hours safety cap

# Exit mode:
#   'intrabar'  — realistic: TP/SL hit if intrabar High/Low crosses level (default)
#   'close'     — matches TradingView process_orders_on_close=true: only bar closes checked
EXIT_MODE = 'intrabar'

# --- HELPERS: INDICATORS -----------------------------------------------------

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd_histogram(close, fast=12, slow=26, signal=9):
    m_line = ema(close, fast) - ema(close, slow)
    s_line = ema(m_line, signal)
    return m_line - s_line

def stochastic_k(close, high, low, period=14):
    lo = low.rolling(period).min()
    hi = high.rolling(period).max()
    rng = hi - lo
    return np.where(rng > 0, (close - lo) / rng * 100, 50)

def cci(hlc3, period=20):
    sma = hlc3.rolling(period).mean()
    mad = hlc3.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return np.where(mad > 0, (hlc3 - sma) / (0.015 * mad), 0)

def williams_r(close, high, low, period=14):
    hi = high.rolling(period).max()
    lo = low.rolling(period).min()
    rng = hi - lo
    return np.where(rng > 0, (hi - close) / rng * -100, -50)

def momentum(close, period=10):
    return close - close.shift(period)

def wilder_sum(s, n):
    result = np.zeros(len(s))
    arr = np.nan_to_num(s.values if hasattr(s, 'values') else np.array(s))
    result[n-1] = arr[:n].sum()
    for i in range(n, len(s)):
        result[i] = result[i-1] - result[i-1] / n + arr[i]
    return pd.Series(result, index=s.index if hasattr(s, 'index') else None)

def wilder_mean(s, n):
    result = np.full(len(s), np.nan)
    arr = np.nan_to_num(s.values if hasattr(s, 'values') else np.array(s))
    seed = 2 * n - 1
    if seed >= len(s):
        return pd.Series(result, index=s.index if hasattr(s, 'index') else None)
    result[seed] = arr[n:seed+1].mean()
    for i in range(seed + 1, len(s)):
        result[i] = (result[i-1] * (n-1) + arr[i]) / n
    return pd.Series(result, index=s.index if hasattr(s, 'index') else None)

def calc_adx(df, period=14):
    ph = df['high'].shift(1); pl = df['low'].shift(1); pc = df['close'].shift(1)
    pdm = np.where((df['high']-ph) > (pl-df['low']), np.maximum(df['high']-ph, 0), 0)
    mdm = np.where((pl-df['low']) > (df['high']-ph), np.maximum(pl-df['low'], 0), 0)
    tr  = pd.concat([df['high']-df['low'], (df['high']-pc).abs(), (df['low']-pc).abs()], axis=1).max(axis=1)
    tr_s  = wilder_sum(pd.Series(tr.values,  index=df.index), period)
    pdm_s = wilder_sum(pd.Series(pdm,        index=df.index), period)
    mdm_s = wilder_sum(pd.Series(mdm,        index=df.index), period)
    dip = np.where(tr_s > 0, pdm_s / tr_s * 100, 0)
    dim = np.where(tr_s > 0, mdm_s / tr_s * 100, 0)
    dx  = np.where((dip+dim) > 0, np.abs(dip-dim)/(dip+dim)*100, 0)
    return wilder_mean(pd.Series(dx, index=df.index), period)

def compute_4h_adx(df, period=14):
    """Resample to 4H, compute ADX, map back to 15m bars via floor-to-4H key."""
    df_t = df.set_index(pd.to_datetime(df['time'], utc=True))
    df4  = df_t[['open','high','low','close']].resample('4h').agg(
               {'open':'first','high':'max','low':'min','close':'last'}).dropna()
    df4['adx'] = calc_adx(df4, period)
    def floor4h(ts):
        return int(ts.timestamp() // (4*3600)) * (4*3600)
    adx_map = {floor4h(ts): row['adx'] for ts, row in df4.iterrows()}
    ts_15m  = pd.to_datetime(df['time'], utc=True)
    return pd.Series([adx_map.get(floor4h(t), np.nan) for t in ts_15m], index=df.index)

def compute_indicators(df):
    df = df.copy()
    df['hlc3']    = (df['high'] + df['low'] + df['close']) / 3
    df['rsi']     = rsi(df['close'], 14)
    df['macd_h']  = macd_histogram(df['close'])
    df['stoch_k'] = stochastic_k(df['close'], df['high'], df['low'], 14)
    df['cci_v']   = cci(df['hlc3'], 20)
    df['wpr_v']   = williams_r(df['close'], df['high'], df['low'], 14)
    df['mom_v']   = momentum(df['close'], 10)
    df['adx_4h']  = compute_4h_adx(df, 14)
    return df

def rolling_extremes(series, lb):
    lo = series.rolling(lb).min()
    hi = series.rolling(lb).max()
    return lo, hi

def div_score_bull(df, lb):
    """Bullish divergence: price near lookback low but oscillators NOT at their low."""
    pr_lo, pr_hi = rolling_extremes(df['close'], lb)
    pr_rng = pr_hi - pr_lo
    price_bot = (pr_rng > 0) & (df['close'] <= pr_lo + pr_rng * 0.30)

    scores = np.zeros(len(df))
    for col in ['rsi', 'macd_h', 'stoch_k', 'cci_v', 'wpr_v', 'mom_v']:
        v_lo, v_hi = rolling_extremes(df[col], lb)
        v_rng = v_hi - v_lo
        not_bot = (v_rng > 0) & (df[col] > v_lo + v_rng * 0.30)
        scores += (price_bot & not_bot).astype(int)
    return pd.Series(scores, index=df.index)

def div_score_bear(df, lb):
    """Bearish divergence: price near lookback high but oscillators NOT at their high."""
    pr_lo, pr_hi = rolling_extremes(df['close'], lb)
    pr_rng = pr_hi - pr_lo
    price_top = (pr_rng > 0) & (df['close'] >= pr_hi - pr_rng * 0.30)

    scores = np.zeros(len(df))
    for col in ['rsi', 'macd_h', 'stoch_k', 'cci_v', 'wpr_v', 'mom_v']:
        v_lo, v_hi = rolling_extremes(df[col], lb)
        v_rng = v_hi - v_lo
        not_top = (v_rng > 0) & (df[col] < v_hi - v_rng * 0.30)
        scores += (price_top & not_top).astype(int)
    return pd.Series(scores, index=df.index)

# --- HELPERS: CANDLE PATTERNS -------------------------------------------------

def candle_patterns(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body_sz    = (c - o).abs()
    upper_wick = h - c.clip(lower=o)   # high - max(o, c)
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    cndl_range = h - l
    is_bull    = (c > o).astype(bool)
    is_bear    = (c < o).astype(bool)

    # Engulfing
    bull_engulf = (USE_ENGULF & is_bull & ~is_bull.shift(1).fillna(False).astype(bool) &
                   (c > o.shift(1)) & (o < c.shift(1)) &
                   (body_sz >= body_sz.shift(1) * 0.8))
    bear_engulf = (USE_ENGULF & is_bear & ~is_bear.shift(1).fillna(False).astype(bool) &
                   (c < o.shift(1)) & (o > c.shift(1)) &
                   (body_sz >= body_sz.shift(1) * 0.8))

    # Pin Bar
    bull_pin = (USE_PIN &
                (lower_wick >= body_sz * PIN_WICK_MULT) &
                (upper_wick <= body_sz * 1.5) &
                (cndl_range > 0))
    bear_pin = (USE_PIN &
                (upper_wick >= body_sz * PIN_WICK_MULT) &
                (lower_wick <= body_sz * 1.5) &
                (cndl_range > 0))

    # 3-Bar Reversal
    bar2_range  = (c.shift(2) - o.shift(2)).abs()
    indecision  = (body_sz.shift(1) <= bar2_range * 0.5)
    bull_3bar   = (USE_3BAR & (c.shift(2) < o.shift(2)) & indecision &
                   is_bull & (c > o.shift(2)))
    bear_3bar   = (USE_3BAR & (c.shift(2) > o.shift(2)) & indecision &
                   is_bear & (c < o.shift(2)))

    bull_sig = bull_engulf | bull_pin | bull_3bar
    bear_sig = bear_engulf | bear_pin | bear_3bar
    return bull_sig.fillna(False), bear_sig.fillna(False)

# --- SESSION HELPERS ---------------------------------------------------------

def session_flags(df):
    ts = pd.to_datetime(df['time'], utc=True)
    h  = ts.dt.hour
    m  = ts.dt.minute
    dow = ts.dt.dayofweek   # Monday=0

    curr_min    = h * 60 + m
    is_mon      = (dow == 0)
    start_min   = np.where(is_mon, MONDAY_START_H * 60 + ENTRY_START_M,
                                   ENTRY_START_H  * 60 + ENTRY_START_M)
    end_min     = ENTRY_END_H * 60 + ENTRY_END_M
    rev_end_min = REV_END_H   * 60 + REV_END_M

    in_sess     = (curr_min >= start_min) & (curr_min <= end_min)
    in_rev_sess = (curr_min >= start_min) & (curr_min <= rev_end_min)
    in_japan    = ((h == 23) & (m >= 45)) | ((h >= 0) & (h < JAPAN_END_H))
    is_2345     = (h == 23) & (m == 45)

    return pd.DataFrame({
        'in_sess':     in_sess,
        'in_rev_sess': in_rev_sess,
        'in_japan':    in_japan,
        'is_2345':     is_2345,
    }, index=df.index)

# --- ZONE FORMATION -----------------------------------------------------------

def form_zone(df, i):
    """Replicates Pine Script zone formation at bar i (23:45 bar)."""
    if i < 1:
        return None, None, None

    # Handle line candle (near-zero body on prior bar)
    prev_body = abs(df.at[i-1, 'close'] - df.at[i-1, 'open']) * 10000
    if prev_body < 10 and i >= 2:
        prior_close = df.at[i-2, 'close']
    else:
        prior_close = df.at[i-1, 'close']

    japan_open  = df.at[i, 'open']
    japan_close = df.at[i, 'close']
    gap_size    = abs(prior_close - japan_open) * 10000

    if gap_size >= ZONE_MIN_GAP:
        zone_top    = max(prior_close, japan_open)
        zone_bottom = min(prior_close, japan_open)
        japan_bull  = japan_open > prior_close
    else:
        zone_top    = max(japan_open, japan_close)
        zone_bottom = min(japan_open, japan_close)
        japan_bull  = japan_close > japan_open

    # Guard against flat zone (no meaningful zone)
    if abs(zone_top - zone_bottom) * 10000 < 1:
        zone_top    = max(japan_open, japan_close) + 0.001
        zone_bottom = min(japan_open, japan_close)

    return zone_top, zone_bottom, japan_bull

# --- TRADE OUTCOME SIMULATION ------------------------------------------------

def resolve_trade(df, entry_idx, entry_price, tp, sl, direction):
    """
    Look forward from entry_idx+1 to find if TP or SL is hit first.
    direction: 'long' or 'short'
    EXIT_MODE='intrabar'  → uses bar High/Low  (realistic)
    EXIT_MODE='close'     → uses bar Close only (matches TradingView process_orders_on_close)
    Returns: ('win', exit_price, exit_bar) or ('loss', exit_price, exit_bar) or ('timeout', ...)
    """
    n = len(df)
    for j in range(entry_idx + 1, min(entry_idx + MAX_LOOKFORWARD, n)):
        o_j = df.at[j, 'open']
        c_j = df.at[j, 'close']
        h_j = df.at[j, 'high']  if EXIT_MODE == 'intrabar' else c_j
        l_j = df.at[j, 'low']   if EXIT_MODE == 'intrabar' else c_j

        if direction == 'long':
            if EXIT_MODE == 'intrabar' and o_j <= sl:
                return ('loss', sl, j)
            if h_j >= tp and l_j <= sl:
                if abs(o_j - sl) < abs(tp - o_j):
                    return ('loss', sl, j)
                else:
                    return ('win', tp, j)
            if h_j >= tp:
                return ('win', tp, j)
            if l_j <= sl:
                return ('loss', sl, j)
        else:  # short
            if EXIT_MODE == 'intrabar' and o_j >= sl:
                return ('loss', sl, j)
            if l_j <= tp and h_j >= sl:
                if abs(o_j - sl) < abs(o_j - tp):
                    return ('loss', sl, j)
                else:
                    return ('win', tp, j)
            if l_j <= tp:
                return ('win', tp, j)
            if h_j >= sl:
                return ('loss', sl, j)

    return ('timeout', df.at[min(entry_idx + MAX_LOOKFORWARD - 1, n - 1), 'close'], entry_idx + MAX_LOOKFORWARD)

# --- MAIN SIMULATION ---------------------------------------------------------

def run_backtest():
    print("Loading data…")
    df_raw = pd.read_csv(CSV_PATH, low_memory=False)

    # Keep only OHLC; sort by time
    df = df_raw[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)

    print(f"Bars: {len(df)}  |  {df['time'].iloc[0]} to {df['time'].iloc[-1]}")

    # Compute indicators
    print("Computing indicators…")
    df = compute_indicators(df)

    # Divergence scores
    df['bull_div'] = div_score_bull(df, DIV_LOOKBACK)
    df['bear_div'] = div_score_bear(df, DIV_LOOKBACK)

    # Candle patterns
    df['bull_sig'], df['bear_sig'] = candle_patterns(df)

    # Session flags
    sess = session_flags(df)
    df = pd.concat([df, sess], axis=1)

    # -- Strategy Loop ------------------------------------------------------
    trades = []

    zone_top         = np.nan
    zone_bottom      = np.nan
    japan_bull       = False
    zone_pristine    = False
    zone_body_clean  = False
    japan_candle_cnt = 0
    zone_traded      = False
    in_trade_until   = -1    # bar index: no new entries while i <= this (prevents
                             # entering a new zone while a prior-zone trade is still open)

    print("Running strategy…")
    n = len(df)

    for i in range(2, n):   # start at 2 so we have at least 2 prior bars for 3-bar pattern
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']

        # -- Zone Formation at 23:45 ----------------------------------------
        if row['is_2345']:
            zt, zb, jb = form_zone(df, i)
            if zt is not None:
                zone_top         = zt
                zone_bottom      = zb
                japan_bull       = jb
                zone_pristine    = True
                zone_body_clean  = True
                japan_candle_cnt = 0
                zone_traded      = False
                # Note: in_trade_until is NOT reset here — if a prior-zone trade is
                # still running, we must wait for it to close before entering again.
            continue   # no entry signals on the zone-formation bar itself

        if np.isnan(zone_top):
            continue

        # -- Zone State Monitoring ------------------------------------------
        if row['in_japan']:
            japan_candle_cnt += 1
            if zone_body_clean and japan_candle_cnt > 3:
                if zone_bottom <= c <= zone_top:
                    zone_body_clean = False

        if zone_pristine:
            if japan_bull:
                if c < zone_bottom:
                    zone_pristine = False
            else:
                if c > zone_top:
                    zone_pristine = False

        # -- Skip if zone already traded OR a previous trade is still open --
        if zone_traded or i <= in_trade_until:
            continue

        # -- Distance calculations (in pts = price * 10000) -----------------
        dist_tp_long   = (zone_top    - c) * 10000
        dist_tp_short  = (c - zone_bottom) * 10000
        dist_rev_long  = abs(c - zone_bottom) * 10000
        dist_rev_short = abs(zone_top - c)    * 10000

        # -- ADX gate -------------------------------------------------------
        adx_4h = row.get('adx_4h', np.nan)
        adx_ok = (not USE_ADX_GATE) or (not np.isnan(adx_4h) and adx_4h >= ADX_MIN)

        # -- Candle size (for reversal quality filter) ----------------------
        body_pts  = abs(c - o) * 10000
        range_pts = (h - l)   * 10000
        rev_candle_ok = (body_pts >= REV_MIN_BODY) and (range_pts >= REV_MIN_RANGE)

        # -- Attraction Conditions (ADX gate NOT applied — attraction works in ranging markets)
        if (ATTR_ENABLED and zone_body_clean and zone_pristine and
                row['in_sess'] and not row['in_japan']):

            if (not japan_bull and row['bull_sig'] and
                    ATTR_MIN_PTS <= dist_tp_long <= ATTR_MAX_PTS):
                # Attraction LONG
                tp    = zone_top
                sl_d  = zone_top - c
                sl    = c - sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'long')
                trades.append({
                    'type': 'ATTR_LONG',
                    'entry_time': row['time'],
                    'entry_price': round(c, 5),
                    'tp': round(tp, 5),
                    'sl': round(sl, 5),
                    'sl_pts': round(sl_d * 10000),
                    'zone_top': round(zone_top, 5),
                    'zone_bottom': round(zone_bottom, 5),
                    'outcome': outcome,
                    'exit_price': round(exit_px, 5),
                    'pnl_pts': round((exit_px - c) * 10000 if outcome == 'win' else (c - exit_px) * 10000 * -1),
                })
                zone_traded    = True
                in_trade_until = exit_bar
                continue

            if (japan_bull and row['bear_sig'] and
                    ATTR_MIN_PTS <= dist_tp_short <= ATTR_MAX_PTS):
                # Attraction SHORT
                tp   = zone_bottom
                sl_d = c - zone_bottom
                sl   = c + sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'short')
                trades.append({
                    'type': 'ATTR_SHORT',
                    'entry_time': row['time'],
                    'entry_price': round(c, 5),
                    'tp': round(tp, 5),
                    'sl': round(sl, 5),
                    'sl_pts': round(sl_d * 10000),
                    'zone_top': round(zone_top, 5),
                    'zone_bottom': round(zone_bottom, 5),
                    'outcome': outcome,
                    'exit_price': round(exit_px, 5),
                    'pnl_pts': round((c - exit_px) * 10000 if outcome == 'win' else (exit_px - c) * 10000 * -1),
                })
                zone_traded    = True
                in_trade_until = exit_bar
                continue

        # -- Reversal Conditions --------------------------------------------
        if (REV_ENABLED and not zone_pristine and
                row['in_rev_sess'] and not row['in_japan'] and adx_ok and rev_candle_ok):

            bull_ok = (row['bull_sig'] and row['bull_div'] >= REV_MIN_DIV and
                       dist_rev_long <= REV_MAX_DIST)
            bear_ok = (row['bear_sig'] and row['bear_div'] >= REV_MIN_DIV and
                       dist_rev_short <= REV_MAX_DIST)

            if bull_ok:
                # Reversal LONG — SL at zone_bottom (floor by rev_min_sl)
                min_d = REV_MIN_SL / 10000.0
                sl_d  = max(c - zone_bottom, min_d)
                tp    = c + sl_d
                sl    = c - sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'long')
                trades.append({
                    'type': 'REV_LONG',
                    'entry_time': row['time'],
                    'entry_price': round(c, 5),
                    'tp': round(tp, 5),
                    'sl': round(sl, 5),
                    'sl_pts': round(sl_d * 10000),
                    'zone_top': round(zone_top, 5),
                    'zone_bottom': round(zone_bottom, 5),
                    'outcome': outcome,
                    'exit_price': round(exit_px, 5),
                    'pnl_pts': round((exit_px - c) * 10000 if outcome == 'win' else (c - exit_px) * 10000 * -1),
                })
                zone_traded    = True
                in_trade_until = exit_bar
                continue

            if bear_ok:
                # Reversal SHORT — SL at zone_top (floor by rev_min_sl)
                min_d = REV_MIN_SL / 10000.0
                sl_d  = max(zone_top - c, min_d)
                tp    = c - sl_d
                sl    = c + sl_d
                outcome, exit_px, exit_bar = resolve_trade(df, i, c, tp, sl, 'short')
                trades.append({
                    'type': 'REV_SHORT',
                    'entry_time': row['time'],
                    'entry_price': round(c, 5),
                    'tp': round(tp, 5),
                    'sl': round(sl, 5),
                    'sl_pts': round(sl_d * 10000),
                    'zone_top': round(zone_top, 5),
                    'zone_bottom': round(zone_bottom, 5),
                    'outcome': outcome,
                    'exit_price': round(exit_px, 5),
                    'pnl_pts': round((exit_px - c) * 10000 if outcome == 'win' else (c - exit_px) * 10000 * -1),
                })
                zone_traded    = True
                in_trade_until = exit_bar

    return trades

# --- REPORTING ----------------------------------------------------------------

def report(trades):
    if not trades:
        print("\nNo trades found.")
        return

    tdf = pd.DataFrame(trades)

    # Save full trade log
    out_path = CSV_PATH.replace('.csv', '') + '_BACKTEST_TRADES.csv'
    tdf.to_csv(out_path, index=False)
    print(f"\nFull trade log saved: {out_path}")

    def stats(subset, label):
        if len(subset) == 0:
            print(f"\n{label}: No trades")
            return
        wins    = subset[subset['outcome'] == 'win']
        losses  = subset[subset['outcome'] == 'loss']
        timeouts= subset[subset['outcome'] == 'timeout']
        n       = len(subset)
        w       = len(wins)
        l       = len(losses)
        t       = len(timeouts)
        wr      = w / n * 100 if n > 0 else 0
        gross_w = wins['sl_pts'].sum()   # 1:1 RR → profit = sl_pts per win
        gross_l = losses['sl_pts'].sum()
        pf      = gross_w / gross_l if gross_l > 0 else float('inf')
        net_pts = gross_w - gross_l

        print(f"\n{'-'*50}")
        print(f"  {label}")
        print(f"{'-'*50}")
        print(f"  Total trades : {n}  (Wins: {w}  |  Losses: {l}  |  Timeout: {t})")
        print(f"  Win Rate     : {wr:.1f}%")
        print(f"  Profit Factor: {pf:.3f}")
        print(f"  Net P&L (pts): {net_pts:+.0f}  ({net_pts/100:.2f} DXY pts)")

        if n > 0:
            print(f"\n  Trade breakdown:")
            for typ in subset['type'].unique():
                sub = subset[subset['type'] == typ]
                sw  = len(sub[sub['outcome'] == 'win'])
                swr = sw / len(sub) * 100
                print(f"    {typ:12s}: {len(sub):3d} trades  |  WR {swr:.0f}%")

    all_trades = tdf[tdf['outcome'].isin(['win', 'loss', 'timeout'])]

    # -- Overall ----------------------------------------------------------
    stats(all_trades, "OVERALL")

    # -- Attraction only ---------------------------------------------------
    attr = all_trades[all_trades['type'].str.startswith('ATTR')]
    stats(attr, "ATTRACTION TRADES (ATTR_LONG + ATTR_SHORT)")

    # -- Reversal only -----------------------------------------------------
    rev = all_trades[all_trades['type'].str.startswith('REV')]
    stats(rev, "REVERSAL TRADES (REV_LONG + REV_SHORT)")

    print(f"\n{'-'*50}")
    print(f"  Parameters used:")
    print(f"    attr_min_pts  = {ATTR_MIN_PTS}  |  attr_max_pts  = {ATTR_MAX_PTS}")
    print(f"    rev_min_sl    = {REV_MIN_SL}  |  rev_max_dist  = {REV_MAX_DIST}")
    print(f"    rev_min_body  = {REV_MIN_BODY}  |  rev_min_range = {REV_MIN_RANGE}")
    print(f"    div_lookback  = {DIV_LOOKBACK}   |  rev_min_div   = {REV_MIN_DIV}")
    print(f"    adx_gate      = {USE_ADX_GATE}  |  adx_min       = {ADX_MIN}")
    print(f"    date range    : {all_trades['entry_time'].iloc[0] if len(all_trades) else 'N/A'}")
    print(f"                    to {all_trades['entry_time'].iloc[-1] if len(all_trades) else 'N/A'}")
    print(f"{'-'*50}")

# --- ENTRY POINT -------------------------------------------------------------
if __name__ == '__main__':
    trades = run_backtest()
    report(trades)
