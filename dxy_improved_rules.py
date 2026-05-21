"""
dxy_improved_rules.py
=====================
Improved DXY strategy based on Brice Strebler and Ash Mall analysis.

KEY CHANGES vs dxy_clean_rules.py:
  1. SESSION OPENING LEVEL  — London open price (07:00 UTC) replaces static
                               supply/demand zone library as primary reference.
  2. CONFIRMATION CANDLE    — entry fires on engulf / directional pin bar /
                               3-bar pattern AT the opening level, not on the
                               big candle approaching the zone.
  3. BOLLINGER BAND REGIME  — 1H + 4H BB width expanding in trade direction
                               replaces simple EMA20/EMA50 cross.
                               Flat BB (consolidation) to skip REV, allow ATTR.
  4. STRUCTURAL STOP        — prior calendar-day high/low replaces 20-bar
                               swing pivot (~1500–2600 pt stops vs ~100–500).
  5. ATTR HTF GATE          — 4H BB must be FLAT for ATTR entries; strong
                               trending market disqualifies ATTR entirely.
  6. ATTR MOMENTUM CAP      — skip ATTR if prior session range > threshold
                               (prevents trading against huge momentum days).
  7. PIN BAR DIRECTION      — bullish pin bar (long lower wick) only for longs;
                               bearish pin bar (long upper wick) only for shorts.

REVERSAL TRADE:
  London session opens at price X. Price moves away from X by ≥ REV_MIN_MOVE pts.
  Price returns to within REV_PROXIMITY pts of X. Confirmation candle fires.
  BB regime (1H + 4H) must be EXPANDING in trade direction.
  SL = prior session low − buffer (long) or prior session high + buffer (short).
  TP = entry ± SL distance (strict 1:1 R:R).

ATTRACTION TRADE:
  London opens with a GAP ≥ ATTR_MIN_GAP pts from prior bar's close.
  4H BB must be FLAT (regime = 0).  Prior session range ≤ ATTR_MAX_PREV_RANGE.
  Trade taken TOWARD the gap fill (prior session close = TP target).
  Confirmation candle in gap-fill direction.  SL mirrored 1:1.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import dxy_clean_rules as r   # re-use shared helpers

# -- Paths & constants (shared with dxy_clean_rules) ----------------------------
BASE        = r.BASE
PAIRS       = r.PAIRS
PAIR_FACTOR = r.PAIR_FACTOR
PAIR_DIR    = r.PAIR_DIR

# -- Session timing --------------------------------------------------------------
LON_OPEN_HOUR   = 7     # London open  07:00 UTC  (Tue–Fri)
LON_OPEN_MINUTE = 0
MON_OPEN_HOUR   = 6     # Monday open  06:30 UTC
MON_OPEN_MINUTE = 30
ATTR_START_HOUR = 6     # ATTR entry window starts 1hr before London on Tue–Fri (Ash's rule)
ATTR_START_MIN  = 0

# -- REV parameters --------------------------------------------------------------
REV_PROXIMITY  = 150   # pts: max |price − london_open| for entry candle
REV_MIN_MOVE   = 100   # pts: min move away from opening before REV is valid
REV_WINDOW_END = 12 * 60          # 12:00 UTC (minutes since midnight)

# -- ATTR parameters -------------------------------------------------------------
ATTR_MIN_GAP        = 150   # pts: minimum gap at London open
ATTR_MIN_REWARD     = 100   # pts: minimum room to gap-fill target
ATTR_NEAR_BUFFER    = 50    # pts: near-edge TP buffer (when near_edge_tp=True)
ATTR_MAX_PREV_RANGE = 8000  # pts: max prior-session range (momentum cap)
ATTR_WINDOW_END     = 19 * 60 + 30   # 19:30 UTC

# -- Bollinger Band regime -------------------------------------------------------
BB_PERIOD       = 20    # lookback period for BB MA and σ
BB_STD          = 2.0   # standard deviations
BB_SLOPE_BARS   = 3     # HTF bars to measure MA slope
BB_WIDTH_LOOKBACK = 20  # bars for rolling BB-width average (flat detection)

# -- Structural SL ---------------------------------------------------------------
STRUCT_SL_BUFFER = 50    # pts: buffer beyond prior session extreme
MAX_SL_PTS       = 3000  # pts: hard cap on SL distance

# -- Misc ------------------------------------------------------------------------
PIN_WICK_MULT   = r.PIN_WICK_MULT
MAX_LOOKFORWARD = r.MAX_LOOKFORWARD


# ===============================================================================
# DATA LOADER (33-month merged files)
# ===============================================================================
def load_merged(sym: str) -> pd.DataFrame:
    path_map = {
        'DXY':    BASE / 'TVC_DXY, 15_merged.csv',
        'EURUSD': BASE / 'FX_EURUSD, 15_merged.csv',
        'USDJPY': BASE / 'FX_USDJPY, 15_merged.csv',
        'USDCAD': BASE / 'FX_USDCAD, 15_merged.csv',
        'XAUUSD': BASE / 'FX_XAUUSD, 15_merged.csv',
    }
    df = pd.read_csv(path_map[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['time', 'open', 'high', 'low', 'close']].copy()


# ===============================================================================
# BOLLINGER BAND REGIME
# ===============================================================================
def compute_bb_regime(df: pd.DataFrame, tf_hours: int,
                      period: int = BB_PERIOD, std: float = BB_STD,
                      slope_bars: int = BB_SLOPE_BARS,
                      lookback: int = BB_WIDTH_LOOKBACK):
    """
    Resample DXY 15m bars to tf_hours, compute Bollinger Band regime.

    Exactly mirrors the Pine Script v6 logic used in DXYTradeAlert / DXYZoneStrategy:

        expanding = bb_width > bb_width_avg          (width > rolling mean of width)
        slope_up  = ma_now  > ma_3_bars_ago

        bb_regime = +1  if expanding AND slope_up    (trending up   → REV LONG valid)
                  = -1  if expanding AND NOT slope_up (trending down → REV SHORT valid)
                  =  0  if NOT expanding              (flat/consolidating → ATTR allowed)

    Returns
    -------
    dir_series  : int Series  (+1 / -1 / 0) mapped to every 15m bar
    flat_series : int Series  (1 = flat/not-expanding, 0 = expanding)
                  flat_series is simply (dir_series == 0).astype(int)
    """
    idx = pd.to_datetime(df['time'], utc=True)
    dt  = df.set_index(idx)
    htf = (dt[['close']]
           .resample(f'{tf_hours}h')
           .last()
           .dropna())

    htf['ma']    = htf['close'].rolling(period, min_periods=period // 2).mean()
    htf['sigma'] = htf['close'].rolling(period, min_periods=period // 2).std().fillna(0)
    htf['width'] = htf['sigma'] * std * 2
    htf['w_avg'] = htf['width'].rolling(lookback, min_periods=lookback // 2).mean()
    htf['slope'] = htf['ma'] - htf['ma'].shift(slope_bars)

    expanding = htf['width'] > htf['w_avg']

    # Match Pine exactly: expansion required for +1/-1; 0 when flat
    regime = pd.Series(0, index=htf.index, dtype=int)
    regime[ expanding & (htf['slope'] > 0)] =  1
    regime[ expanding & (htf['slope'] < 0)] = -1
    # flat (not expanding) stays 0

    # Flat series is the complement — used as ATTR gate (4H must be flat)
    flat_regime = (regime == 0).astype(int)

    # Map HTF regime back to every 15m bar (step-hold within each HTF period)
    tf_secs = tf_hours * 3600
    def fl(ts): return int(ts.timestamp() // tf_secs) * tf_secs
    dir_map  = {fl(ts): int(v) for ts, v in regime.items()}
    flat_map = {fl(ts): int(v) for ts, v in flat_regime.items()}

    dir_series  = pd.Series([dir_map.get( fl(t), 0) for t in idx], index=df.index)
    flat_series = pd.Series([flat_map.get(fl(t), 0) for t in idx], index=df.index)

    return dir_series, flat_series


# ===============================================================================
# CANDLE SIGNALS  (improved pin-bar detection)
# ===============================================================================
def candle_signals_v2(df: pd.DataFrame):
    """
    Returns (bull_series, bear_series) boolean Series.

    Pin bars are strictly directional (shape, not candle colour):
      Bullish pin — long LOWER wick (price rejected lower prices) to LONG entries
      Bearish pin — long UPPER wick (price rejected higher prices) to SHORT entries

    A bullish pin bar is valid for a long regardless of body colour; likewise
    a bearish pin bar is valid for a short regardless of colour. Direction comes
    from the wick, not the body fill.

    Conditions:
      Bullish pin : lower_wick ≥ PIN_WICK_MULT × body
                    AND lower_wick ≥ 1.5 × upper_wick
      Bearish pin : upper_wick ≥ PIN_WICK_MULT × body
                    AND upper_wick ≥ 1.5 × lower_wick
    """
    c, o, h, l  = df['close'], df['open'], df['high'], df['low']
    body        = (c - o).abs()
    body_top    = pd.concat([o, c], axis=1).max(axis=1)
    body_bottom = pd.concat([o, c], axis=1).min(axis=1)
    hi_wick     = h - body_top
    lo_wick     = body_bottom - l
    rng         = (h - l).replace(0, np.nan)

    # Engulfing candles
    bull_engulf = ((c > o) &
                   ~(c.shift(1) > o.shift(1)) &
                   (c > o.shift(1)) &
                   (o < c.shift(1)) &
                   (body >= body.shift(1) * 0.8))
    bear_engulf = ((c < o) &
                   ~(c.shift(1) < o.shift(1)) &
                   (c < o.shift(1)) &
                   (o > c.shift(1)) &
                   (body >= body.shift(1) * 0.8))

    # Directional pin bars
    bull_pin = ((lo_wick >= body * PIN_WICK_MULT) &
                (lo_wick >= hi_wick * 1.5) &
                rng.notna())   # long lower wick to bullish rejection

    bear_pin = ((hi_wick >= body * PIN_WICK_MULT) &
                (hi_wick >= lo_wick * 1.5) &
                rng.notna())   # long upper wick to bearish rejection

    # 3-bar reversal
    bar2r   = (c.shift(2) - o.shift(2)).abs()
    indecsn = body.shift(1) <= bar2r * 0.5
    bull_3b = (c.shift(2) < o.shift(2)) & indecsn & (c > o) & (c > o.shift(2))
    bear_3b = (c.shift(2) > o.shift(2)) & indecsn & (c < o) & (c < o.shift(2))

    bull = (bull_engulf | bull_pin | bull_3b).fillna(False)
    bear = (bear_engulf | bear_pin | bear_3b).fillna(False)

    # Mutual exclusivity: if both fire on the same bar, body direction is the
    # tiebreaker.  Bullish body (c>o) → keep bull, suppress bear.
    # Bearish body (c<o) → keep bear, suppress bull.
    # Doji (c==o) → suppress both (too ambiguous to act on).
    both = bull & bear
    bull = bull & ~(both & (c <= o))   # bearish/doji body: drop bull
    bear = bear & ~(both & (c >= o))   # bullish/doji body: drop bear

    return bull, bear


# ===============================================================================
# STRUCTURAL STOP LOSS
# ===============================================================================
def get_structural_sl(prev_low: float, prev_high: float,
                      entry: float, direction: str) -> float:
    """
    SL = prior session extreme ± STRUCT_SL_BUFFER, capped at MAX_SL_PTS.
      direction='long'  to prior session low  − buffer (protect against downside)
      direction='short' to prior session high + buffer (protect against upside)
    Falls back to MAX_SL_PTS from entry if prior data is unavailable.
    """
    buf = STRUCT_SL_BUFFER / 10000
    if direction == 'long':
        sl = (entry - MAX_SL_PTS / 10000) if np.isnan(prev_low) else (prev_low - buf)
        sl = max(sl, entry - MAX_SL_PTS / 10000)   # cap
    else:
        sl = (entry + MAX_SL_PTS / 10000) if np.isnan(prev_high) else (prev_high + buf)
        sl = min(sl, entry + MAX_SL_PTS / 10000)   # cap
    return sl


# ===============================================================================
# SIGNAL GENERATOR  (v2 — improved rules)
# ===============================================================================
def generate_signals_v2(df_dxy: pd.DataFrame,
                        near_edge_tp: bool = False,
                        news_dates: dict = None) -> list:
    """
    Improved DXY signal generator.

    news_dates : applied to ATTR and GAP_REJ only — gap-fill trades into a
                 news-driven gap are unreliable.  REV trades are NOT filtered
                 by news because a news-driven move can legitimately validate
                 the reversal setup (price moved away from open → comes back).

    Returns a list of signal dicts compatible with dxy_clean_rules.py pair
    application functions (apply_to_pair_dxy_exit etc.).

    Each signal has the same keys as dxy_clean_rules.generate_dxy_signals:
      type, entry_time, entry, tp, sl, sl_pts, tp_pts, outcome, exit_px,
      exit_time, bias_1h, bias_4h
    (bias_1h / bias_4h now store the BB regime value rather than EMA bias)
    """
    df = df_dxy.copy().reset_index(drop=True)

    # Compute BB regime on 1H and 4H — matches Pine Script exactly:
    #   +1 = width expanding AND MA sloping up   → REV LONG valid
    #   -1 = width expanding AND MA sloping down → REV SHORT valid
    #    0 = width NOT expanding (flat)           → ATTR allowed, REV blocked
    df['bb_1h'], _                = compute_bb_regime(df, 1)
    df['bb_4h'], df['bb_4h_flat'] = compute_bb_regime(df, 4)

    # Candle signals
    bull_sig, bear_sig = candle_signals_v2(df)

    # Pre-compute daily high/low (UTC calendar day) for structural SL
    df['_date'] = df['time'].dt.date
    day_grp = df.groupby('_date').agg(day_h=('high', 'max'), day_l=('low', 'min'))

    # -- Session-level state -------------------------------------------------
    london_open_price  = np.nan   # open of first London bar each day
    prev_session_high  = np.nan   # prior calendar day high
    prev_session_low   = np.nan   # prior calendar day low
    prev_session_range = 0.0      # prior calendar day range (pts)

    # ATTR: gap from prior bar's close to London open price
    attr_gap_pts    = 0.0        # signed: +ve = gap up, -ve = gap down
    attr_gap_target = np.nan     # prior bar's close = gap-fill TP target
    attr_gap_touched = False     # True once price crosses the gap target this session
                                 # (latches — prevents re-entry if price retreats back)

    # Running max distance from London open this session
    max_move_up   = 0.0          # max pts ABOVE london_open_price
    max_move_down = 0.0          # max pts BELOW london_open_price

    attr_traded    = False

    signals = []
    n       = len(df)

    for i in range(2, n):
        row    = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']
        ts       = row['time']
        hour     = ts.hour
        minute   = ts.minute
        curr_min = hour * 60 + minute
        dow      = ts.dayofweek   # Mon=0

        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        is_london_open = (not in_japan
                          and hour == LON_OPEN_HOUR
                          and minute == LON_OPEN_MINUTE
                          and dow != 0)
        is_monday_open = (not in_japan
                          and hour == MON_OPEN_HOUR
                          and minute == MON_OPEN_MINUTE
                          and dow == 0)

        # -- Tokyo open (23:45 UTC): establish ATTR gap for coming London session --
        # Gap = 23:45 open vs 23:15 close (the candle prior to the 23:30 phantom/
        # session-closing candle).  Fallback: use body of the 23:45 candle itself.
        is_tokyo_open = (hour == 23 and minute == 45)
        if is_tokyo_open:
            attr_traded      = False   # reset for new session
            attr_gap_touched = False   # zone is fresh/pristine at Tokyo open
            ref_close   = None
            # i-1 = 23:30 (phantom session-closing candle); i-2 = 23:15 (reference)
            for back, exp_offset in [(2, 30), (1, 15)]:
                if i >= back:
                    cand = df.iloc[i - back]
                    if abs((cand['time'] - (ts - pd.Timedelta(minutes=exp_offset))
                            ).total_seconds()) <= 120:
                        ref_close = cand['close']
                        break
            if ref_close is not None:
                raw_gap = (o - ref_close) * 10000   # 23:45 open vs reference close
                if abs(raw_gap) >= 10:
                    attr_gap_pts    = raw_gap       # +ve = gap up, -ve = gap down
                    attr_gap_target = ref_close     # gap-fill TP = reference close
                else:
                    # Fallback: body of the 23:45 candle
                    body_pts        = (c - o) * 10000   # +ve = bullish, -ve = bearish
                    attr_gap_pts    = body_pts
                    attr_gap_target = o             # return to 23:45 open price
            else:
                attr_gap_pts    = 0.0
                attr_gap_target = np.nan

        # -- Record London open price once per day ---------------------------
        if is_london_open or is_monday_open:
            london_open_price = o   # OPEN of the first London bar
            max_move_up   = 0.0
            max_move_down = 0.0

            # Prior session (prior calendar day) high/low for structural SL
            today_dt   = ts.date()
            prior_days = [d for d in day_grp.index if d < today_dt]
            if prior_days:
                prev_dt           = max(prior_days)
                prev_session_high = float(day_grp.at[prev_dt, 'day_h'])
                prev_session_low  = float(day_grp.at[prev_dt, 'day_l'])
                prev_session_range = (prev_session_high - prev_session_low) * 10000
            else:
                prev_session_high  = np.nan
                prev_session_low   = np.nan
                prev_session_range = 0.0

        if np.isnan(london_open_price):
            continue

        # -- Track max move from London open ---------------------------------
        if not in_japan:
            above = (c - london_open_price) * 10000
            if above > 0:
                max_move_up   = max(max_move_up,    above)
            elif above < 0:
                max_move_down = max(max_move_down, -above)

        # -- Entry windows ---------------------------------------------------
        mon_start    = MON_OPEN_HOUR * 60 + MON_OPEN_MINUTE
        rev_start    = mon_start if dow == 0 else (LON_OPEN_HOUR * 60)
        attr_start   = mon_start if dow == 0 else (ATTR_START_HOUR * 60 + ATTR_START_MIN)
        in_rev_sess  = rev_start  <= curr_min <= REV_WINDOW_END  and not in_japan
        in_attr_sess = attr_start <= curr_min <= ATTR_WINDOW_END and not in_japan

        bb_1h        = int(row['bb_1h'])       # direction: +1 up / -1 down
        bb_4h        = int(row['bb_4h'])       # direction: +1 up / -1 down
        bb_4h_flat   = int(row['bb_4h_flat'])  # 1 = 4H BB flat/consolidating
        dist_from_open  = (c - london_open_price) * 10000   # +ve = above open

        # -- ATTR zone latch: once gap target is crossed, zone is permanently filled --
        # This prevents a re-entry signal if price retreats back below the target after
        # having already filled the gap (zone is no longer "pristine").
        if not np.isnan(attr_gap_target):
            if attr_gap_pts < 0 and c >= attr_gap_target:   # gap-down: price reached target
                attr_gap_touched = True
            elif attr_gap_pts > 0 and c <= attr_gap_target: # gap-up: price reached target
                attr_gap_touched = True

        # ====================================================================
        # REV TRADE
        #   Price moved away from London open then RETURNED to it.
        #   BB midline direction on 1H AND 4H must agree with trade direction.
        #   Confirmation candle at opening level.
        #   Structural SL = prior session extreme.
        # ====================================================================
        if (in_rev_sess
                and not np.isnan(prev_session_high)):

            at_level = abs(dist_from_open) <= REV_PROXIMITY

            # REV LONG: price moved DOWN, returns to open, BB midline sloping UP on 1H+4H
            if (max_move_down >= REV_MIN_MOVE
                    and at_level
                    and bull_sig.at[i]
                    and bb_1h == 1):

                sl_price = get_structural_sl(prev_session_low, prev_session_high, c, 'long')
                sl_d     = c - sl_price
                if sl_d > 0:
                    tp_price = c + sl_d
                    outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'long')
                    signals.append({
                        'type': 'REV_LONG', 'entry_time': str(ts),
                        'entry': round(c, 5), 'tp': round(tp_price, 5),
                        'sl': round(sl_price, 5),
                        'sl_pts': round(sl_d * 10000),
                        'tp_pts': round(sl_d * 10000),
                        'london_open': round(london_open_price, 5),
                        'pristine': False,
                        'outcome': outcome,
                        'exit_px': round(exit_px, 5),
                        'exit_time': str(df.at[exit_bar, 'time']),
                        'bias_1h': bb_1h, 'bias_4h': bb_4h,
                    })
                    continue

            # REV SHORT: price moved UP, returns to open, BB midline sloping DOWN on 1H+4H
            if (max_move_up >= REV_MIN_MOVE
                    and at_level
                    and bear_sig.at[i]
                    and bb_1h == -1):

                sl_price = get_structural_sl(prev_session_low, prev_session_high, c, 'short')
                sl_d     = sl_price - c
                if sl_d > 0:
                    tp_price = c - sl_d
                    outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'short')
                    signals.append({
                        'type': 'REV_SHORT', 'entry_time': str(ts),
                        'entry': round(c, 5), 'tp': round(tp_price, 5),
                        'sl': round(sl_price, 5),
                        'sl_pts': round(sl_d * 10000),
                        'tp_pts': round(sl_d * 10000),
                        'london_open': round(london_open_price, 5),
                        'pristine': False,
                        'outcome': outcome,
                        'exit_px': round(exit_px, 5),
                        'exit_time': str(df.at[exit_bar, 'time']),
                        'bias_1h': bb_1h, 'bias_4h': bb_4h,
                    })
                    continue

        # ====================================================================
        # ATTR TRADE
        #   London opened with a gap ≥ ATTR_MIN_GAP from prior session close.
        #   4H BB must be FLAT (bb_4h_flat == 1) — consolidating market.
        #   Prior session range must be modest (no huge momentum day).
        #   Confirmation candle in gap-fill direction.
        #   SL mirrored 1:1 from entry.  TP = prior session close.
        # ====================================================================
        # Shared pre-checks for both ATTR and GAP_REJ
        _attr_base = (not attr_traded and in_attr_sess
                      and abs(attr_gap_pts) >= ATTR_MIN_GAP
                      and not np.isnan(attr_gap_target)
                      and prev_session_range <= ATTR_MAX_PREV_RANGE
                      and bb_4h_flat == 1)

        if _attr_base:
            # News filter: skip ATTR and GAP_REJ on USD high-impact news days.
            # REV is intentionally exempt — see docstring.
            if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
                continue

            # -- Determine trade type: ATTR (pristine) vs GAP_REJ (zone already filled) --
            is_attr    = not attr_gap_touched
            trade_type = 'ATTR' if is_attr else 'GAP_REJ'

            # LONG: gap down → fill up; price still below target
            if (attr_gap_pts < 0 and c < attr_gap_target and bull_sig.at[i]):
                reward_pts = (attr_gap_target - c) * 10000
                if reward_pts >= ATTR_MIN_REWARD:
                    tp_price = attr_gap_target
                    if near_edge_tp:
                        tp_price = attr_gap_target - ATTR_NEAR_BUFFER / 10000
                    sl_d = tp_price - c
                    if sl_d > 0:
                        sl_price = c - sl_d
                        outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'long')
                        signals.append({
                            'type': f'{trade_type}_LONG', 'entry_time': str(ts),
                            'entry': round(c, 5), 'tp': round(tp_price, 5),
                            'sl': round(sl_price, 5),
                            'sl_pts': round(sl_d * 10000),
                            'tp_pts': round(sl_d * 10000),
                            'london_open': round(london_open_price, 5),
                            'pristine': is_attr,
                            'outcome': outcome,
                            'exit_px': round(exit_px, 5),
                            'exit_time': str(df.at[exit_bar, 'time']),
                            'bias_1h': bb_1h, 'bias_4h': bb_4h,
                        })
                        attr_traded = True
                        continue

            # SHORT: gap up → fill down; price still above target
            if (attr_gap_pts > 0 and c > attr_gap_target and bear_sig.at[i]):
                reward_pts = (c - attr_gap_target) * 10000
                if reward_pts >= ATTR_MIN_REWARD:
                    tp_price = attr_gap_target
                    if near_edge_tp:
                        tp_price = attr_gap_target + ATTR_NEAR_BUFFER / 10000
                    sl_d = c - tp_price
                    if sl_d > 0:
                        sl_price = c + sl_d
                        outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'short')
                        signals.append({
                            'type': f'{trade_type}_SHORT', 'entry_time': str(ts),
                            'entry': round(c, 5), 'tp': round(tp_price, 5),
                            'sl': round(sl_price, 5),
                            'sl_pts': round(sl_d * 10000),
                            'tp_pts': round(sl_d * 10000),
                            'london_open': round(london_open_price, 5),
                            'pristine': is_attr,
                            'outcome': outcome,
                            'exit_px': round(exit_px, 5),
                            'exit_time': str(df.at[exit_bar, 'time']),
                            'bias_1h': bb_1h, 'bias_4h': bb_4h,
                        })
                        attr_traded = True

    return signals


# ===============================================================================
# MAIN  —  run improved backtest and print results
# ===============================================================================
def main():
    print("Loading 33-month merged data...")
    df_dxy   = load_merged('DXY')
    pair_dfs = {p: load_merged(p) for p in PAIRS}
    print(f"  DXY bars : {len(df_dxy):,}  ({df_dxy['time'].min().date()} to {df_dxy['time'].max().date()})")
    print()

    news_dates = r.load_news_filter()

    print("Generating improved signals (near_edge_tp=True)...")
    sigs = generate_signals_v2(df_dxy, near_edge_tp=True, news_dates=news_dates)
    print(f"  Total signals : {len(sigs)}")

    attr_sigs = [s for s in sigs if s['type'].startswith('ATTR')]
    grej_sigs = [s for s in sigs if s['type'].startswith('GAP_REJ')]
    rev_sigs  = [s for s in sigs if s['type'].startswith('REV')]
    print(f"  ATTR (pristine)  : {len(attr_sigs)}")
    print(f"  GAP_REJ (filled) : {len(grej_sigs)}")
    print(f"  REV              : {len(rev_sigs)}")

    months = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
    print(f"  Signals/month : {len(sigs)/months:.1f}  "
          f"(ATTR: {len(attr_sigs)/months:.1f}  GAP_REJ: {len(grej_sigs)/months:.1f}  REV: {len(rev_sigs)/months:.1f})")
    print()

    # DXY signal quality
    print("-- DXY signal outcomes ------------------------------------------")
    for label, subset in [("ALL", sigs), ("ATTR", attr_sigs), ("GAP_REJ", grej_sigs), ("REV", rev_sigs)]:
        s = r.stats(subset, label)
        r.print_stats(s)
    print()

    # Pair results via DXY-exit
    print("-- Pair results (DXY-exit, fractional R) -----------------------")
    all_pair_trades = []
    for pair in PAIRS:
        trades = r.apply_to_pair_dxy_exit(sigs, pair_dfs[pair], pair)
        all_pair_trades.extend(trades)

    r.print_variant_dxy_exit("IMPROVED RULES — DXY EXIT", sigs, all_pair_trades, 0)
    r.profit_estimate_r("Improved Rules", all_pair_trades)


if __name__ == '__main__':
    main()
