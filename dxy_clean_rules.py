"""
dxy_clean_rules.py
==================
Clean, simplified DXY zone strategy applied to EURUSD, USDJPY, USDCAD, XAUUSD.

ATTRACTION RULES (pristine zone survived Tokyo, London retraces back):
  1. Zone is PRISTINE  ? zone untouched at London open (price still outside zone at 07:30)
                         AND price >= ATTR_MIN_GAP (150 pts) from near zone edge at 07:30
  2. Impulsive approach ? net move toward zone in prior 3 bars >= 150 pts
  3. Entry candle      ? engulf / pin bar / 3-bar reversal pointing toward zone
  4. Zone width        ? (zone_top - zone_bottom) >= 150 pts minimum depth
  5. Min reward        ? at least 150 pts remaining to zone far side from entry close
  6. SL                ? mirrored 1:1 below entry (SL dist = TP dist = remaining to far side)
  7. TP                ? zone far side (fills the imbalance)
  8. Entry window      ? 07:30 to 19:30 UTC

REVERSAL RULES (broken zone, bounce off zone):
  1. Zone is BROKEN    ? at least one bar has touched the zone since formation
  2. Impulsive candle  ? signal bar body >= 200 pts AND range >= 400 pts
  3. Entry candle      ? same signal bar is the entry (close is entry price)
  4. Distance to zone  ? within 500 pts of relevant zone edge
  5. 1H bias           ? 1H EMA20 > EMA50 for long,  < for short
  6. 4H bias           ? 4H EMA20 > EMA50 for long,  < for short
  7. SL                ? most recent swing pivot (20-bar lookback)
  8. TP                ? entry + (entry - SL)  [strict 1:1 R:R]
  9. Entry window      ? 07:30 to 12:00 UTC

PAIRS (DXY signal triggers pair entry):
  EURUSD  ? negative DXY correlation (-1)
  USDJPY  ? positive DXY correlation (+1)
  USDCAD  ? positive DXY correlation (+1)
  XAUUSD  ? negative DXY correlation (-1)

  pair_sl_dist = dxy_sl_pts / PAIR_FACTOR[pair] / 10000
  pair direction: PAIR_DIR * DXY_direction
"""

import re
import pandas as pd
import numpy as np
from pathlib import Path

# --- PATHS --------------------------------------------------------------------
BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
FILE_MAP = {
    'DXY':    BASE / 'TVC_DXY, 15.csv',
    'EURUSD': BASE / 'FX_EURUSD, 15 (1).csv',
    'USDJPY': BASE / 'FX_USDJPY, 15 (1).csv',
    'USDCAD': BASE / 'FX_USDCAD, 15 (1).csv',
    'XAUUSD': BASE / 'FX_XAUUSD, 15 (1).csv',
}

PAIR_FACTOR = {'EURUSD': 0.01, 'USDJPY': 1.0, 'USDCAD': 0.01, 'XAUUSD': 100.0}
PAIR_DIR    = {'EURUSD': -1,   'USDJPY': +1,  'USDCAD': +1,   'XAUUSD': -1}
PAIRS       = list(PAIR_DIR.keys())

# --- PARAMETERS ---------------------------------------------------------------
ZONE_MIN_GAP       = 30    # pts - open-gap zone vs candle body
ZONE_MIN_WIDTH     = 150   # pts - minimum zone width

# ATTRACTION: signal candle fires DURING the approach (before zone is touched).
# Zone must have survived Tokyo with >=ATTR_MIN_GAP pts separation at London open.
# Strict 1:1 R:R: SL distance = TP distance = remaining pts to zone far side.
# SL is NOT the bar extreme; it is mirrored below (long) / above (short) the entry close.
# TP = zone far side (fills the imbalance). Min reward = ATTR_MIN_REWARD pts.
ATTR_MIN_GAP       = 75    # pts - min distance from price to near zone edge at London open
ATTR_APPROACH_PTS  = 150   # pts - net move toward zone in prior 3 bars
ATTR_MIN_REWARD    = 100   # pts - minimum distance from entry close to TP
ATTR_NEAR_BUFFER   = 50    # pts - buffer past near zone edge for option-2 TP
ATTR_WINDOW        = (7*60+30, 19*60+30)   # entry window: 07:30-19:30 UTC (mins)

REV_MIN_BODY       = 200   # pts - reversal candle body
REV_MIN_RANGE      = 300   # pts - reversal candle range
REV_MAX_DIST       = 500   # pts - max distance from zone edge at entry
REV_WINDOW         = (7*60+30, 12*60+0)   # reversal entry window: 07:30-12:00 UTC

PIVOT_LOOKBACK     = 20    # bars for swing pivot SL
MAX_LOOKFORWARD    = 400   # bars ? trade timeout

EMA_FAST, EMA_SLOW = 20, 50  # for 1H and 4H bias
PIN_WICK_MULT      = 2.0

# --- NEWS FILTER --------------------------------------------------------------
# Maps each high-impact currency to the pairs that should be skipped that day.
# USD → skip all pairs (None means skip all); EUR/JPY/CAD → skip specific pair.
NEWS_CURRENCY_PAIRS = {
    'USD': None,          # None  = skip ALL trades on this date
    'EUR': {'EURUSD'},
    'JPY': {'USDJPY'},
    'CAD': {'USDCAD'},
}

def load_news_filter(filepath=None):
    """
    Load high-impact news CSV and return:
        news_dates : dict  { 'YYYY-MM-DD' -> set of currencies with high-impact news }

    Accepts CSVs with either:
      - 'iso_date' column  (new scraper format: '2023-08-10')
      - 'date' column      (old scraper format: 'ThuAug 10')  -- year inferred from sequence
    """
    if filepath is None:
        filepath = BASE / 'economic_calendar_high_impact.csv'
    filepath = Path(filepath)
    if not filepath.exists():
        return {}

    df = pd.read_csv(filepath)
    if df.empty:
        return {}

    news = {}

    # --- New format: iso_date column already present -------------------------
    if 'iso_date' in df.columns:
        for _, row in df.iterrows():
            iso = str(row['iso_date']).strip()
            cur = str(row['currency']).strip()
            if iso and cur in NEWS_CURRENCY_PAIRS:
                news.setdefault(iso, set()).add(cur)
        return news

    # --- Legacy format: 'date' column like 'ThuAug 10' ----------------------
    MONTH_MAP = {
        'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
        'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
    }
    year = 2023
    prev_month = None
    for _, row in df.iterrows():
        date_str = str(row.get('date', '')).strip()
        cur      = str(row.get('currency', '')).strip()
        m = re.match(r'[A-Za-z]{3}([A-Za-z]{3})\s*(\d+)', date_str)
        if not m:
            continue
        month = MONTH_MAP.get(m.group(1))
        if not month:
            continue
        day = int(m.group(2))
        if prev_month is not None and month < prev_month and prev_month >= 11:
            year += 1
        prev_month = month
        try:
            from datetime import datetime
            iso = datetime(year, month, day).strftime('%Y-%m-%d')
        except ValueError:
            continue
        if cur in NEWS_CURRENCY_PAIRS:
            news.setdefault(iso, set()).add(cur)

    return news


def news_blocks_pair(news_dates, entry_time_str, pair):
    """
    Return True if this trade should be skipped due to high-impact news.
      entry_time_str : ISO timestamp string, e.g. '2023-08-10 13:30:00+00:00'
      pair           : 'EURUSD' | 'USDJPY' | 'USDCAD' | 'XAUUSD'
                       or 'ALL_USD' to check only for USD news (used in signal gen)
    Rules:
      USD news  → skip ALL trades / signals on this date
      EUR news  → skip EURUSD only
      JPY news  → skip USDJPY only
      CAD news  → skip USDCAD only
    """
    if not news_dates:
        return False
    iso_date   = str(entry_time_str)[:10]
    currencies = news_dates.get(iso_date)
    if not currencies:
        return False

    # Special sentinel: caller only wants to know about USD
    if pair == 'ALL_USD':
        return 'USD' in currencies

    for cur, blocked_pairs in NEWS_CURRENCY_PAIRS.items():
        if cur not in currencies:
            continue
        if blocked_pairs is None:          # USD → block everything
            return True
        if pair in blocked_pairs:          # pair-specific block
            return True
    return False


# --- DATA HELPERS -------------------------------------------------------------
def load(sym):
    df = pd.read_csv(FILE_MAP[sym])
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open','high','low','close']:
        df[col] = df[col].astype(float)
    return df[['time','open','high','low','close']].copy()

def to_pts(price_dist, sym='DXY'):
    f = PAIR_FACTOR.get(sym, 0.01)
    return abs(price_dist) / f * 10000

# --- ZONE FORMATION -----------------------------------------------------------
def form_zone(df, i):
    if i < 1: return None, None, None
    prev_body = abs(df.at[i-1,'close'] - df.at[i-1,'open']) * 10000
    prior_c   = df.at[i-2,'close'] if (prev_body < 10 and i >= 2) else df.at[i-1,'close']
    j_o, j_c  = df.at[i,'open'], df.at[i,'close']
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

# --- HIGHER-TIMEFRAME EMA BIAS ------------------------------------------------
def compute_htf_bias(df, tf_hours, fast=EMA_FAST, slow=EMA_SLOW):
    """
    Resample to tf_hours, compute EMA fast/slow.
    Return per-15m bias: +1=bullish (fast>slow), -1=bearish, 0=flat.
    """
    idx = pd.to_datetime(df['time'], utc=True)
    dt  = df.set_index(idx)
    htf = dt[['open','high','low','close']].resample(f'{tf_hours}h').agg(
          {'open':'first','high':'max','low':'min','close':'last'}).dropna()
    htf['ef'] = htf['close'].ewm(span=fast, adjust=False).mean()
    htf['es'] = htf['close'].ewm(span=slow, adjust=False).mean()
    htf['bias'] = np.where(htf['ef'] > htf['es'], 1,
                  np.where(htf['ef'] < htf['es'], -1, 0))
    def fl(ts): return int(ts.timestamp() // (tf_hours*3600)) * (tf_hours*3600)
    bmap = {fl(ts): int(row['bias']) for ts, row in htf.iterrows()}
    return pd.Series([bmap.get(fl(t), 0) for t in idx], index=df.index)

# --- CANDLE SIGNAL PATTERNS ---------------------------------------------------
def candle_signals(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body   = (c - o).abs()
    hi_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lo_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    rng    = h - l

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

# --- SWING PIVOT SL -----------------------------------------------------------
def pivot_sl(df, i, direction, lookback=PIVOT_LOOKBACK):
    """Most recent swing pivot for SL. direction='long' -> low pivot, 'short' -> high."""
    start = max(0, i - lookback)
    sub   = df.iloc[start : i+1]
    if direction == 'long':
        return sub['low'].min()
    else:
        return sub['high'].max()

# --- TRADE RESOLVER -----------------------------------------------------------
def resolve(df, entry_idx, entry, tp, sl, direction):
    n = len(df)
    for j in range(entry_idx + 1, min(entry_idx + MAX_LOOKFORWARD, n)):
        h_j = df.at[j,'high']
        l_j = df.at[j,'low']
        o_j = df.at[j,'open']
        if direction == 'long':
            if o_j <= sl: return 'loss', sl, j
            if h_j >= tp and l_j <= sl:
                return ('win' if abs(o_j-sl)>abs(tp-o_j) else 'loss'), (tp if abs(o_j-sl)>abs(tp-o_j) else sl), j
            if h_j >= tp: return 'win', tp, j
            if l_j <= sl: return 'loss', sl, j
        else:
            if o_j >= sl: return 'loss', sl, j
            if l_j <= tp and h_j >= sl:
                return ('win' if abs(o_j-sl)>abs(o_j-tp) else 'loss'), (tp if abs(o_j-sl)>abs(o_j-tp) else sl), j
            if l_j <= tp: return 'win', tp, j
            if h_j >= sl: return 'loss', sl, j
    j_last = min(entry_idx + MAX_LOOKFORWARD - 1, n-1)
    return 'timeout', df.at[j_last,'close'], j_last

# --- DXY SIGNAL GENERATOR -----------------------------------------------------
def generate_dxy_signals(df_dxy, near_edge_tp=False, news_dates=None):
    """
    Runs the clean rules on DXY 15m data.
    Returns (signals list, raw_rev_candidate_count).

    Two flags govern trade type:
      attr_pristine  = True if price is still outside zone at London open (set once at 07:30)
                       Captures: Tokyo session left zone untouched; London is retracing back.
      strict_pristine = False once any bar overlaps zone (wick-based; for reversal trigger)

    ATTRACTION: attr_pristine=True, bull/bear signal candle during approach,
                impulsive 3-bar move toward zone, >=ATTR_MIN_REWARD pts to zone far side.
    REVERSAL  : strict_pristine=False (zone already tested), big reversal candle,
                1H+4H EMA bias aligned, pivot SL, 1:1 R:R.
    """
    df = df_dxy.copy().reset_index(drop=True)
    df['bias_1h'] = compute_htf_bias(df, 1)
    df['bias_4h'] = compute_htf_bias(df, 4)
    bull_sig, bear_sig = candle_signals(df)

    zone_top    = np.nan
    zone_bottom = np.nan
    japan_bull  = False
    # Zone state flags:
    #  strict_pristine: ANY bar overlapping zone breaks it (REV: zone must have been tested)
    #  attr_pristine:   set ONCE at London open — True if price is outside zone at 07:30,
    #                   meaning Tokyo session left the zone intact (common attraction scenario)
    #  london_touched:  any London bar has touched the zone edge
    #  attr_traded:     one attraction trade has already fired this zone (no second ATTR)
    #  zone_traded:     a reversal trade has closed this zone (no further REV)
    #  in_trade_until:  bar index until current open trade resolves (blocks new entries)
    strict_pristine = False
    attr_pristine   = False
    london_touched  = False
    attr_traded     = False   # blocks second attraction on same zone
    zone_traded     = False   # set only by REV; ATTR does NOT set this
    in_trade_until  = -1

    signals = []
    raw_rev_candidates = 0
    n = len(df)

    for i in range(2, n):
        row   = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']
        ts    = row['time']
        hour  = ts.hour
        minute= ts.minute
        curr_min = hour * 60 + minute
        dow   = ts.dayofweek   # Mon=0

        is_2345 = (hour == 23) and (minute == 45)
        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        # -- Zone formation --------------------------------------------------
        if is_2345:
            zt, zb, jb = form_zone(df, i)
            if zt is not None:
                zone_top, zone_bottom = zt, zb
                japan_bull      = jb
                strict_pristine = True
                attr_pristine   = False   # evaluated fresh at London open
                london_touched  = False
                attr_traded     = False
                zone_traded     = False
            continue

        if np.isnan(zone_top):
            continue

        # ----------------------------------------------------------------
        # Update strict_pristine: any bar whose range overlaps zone breaks it.
        # ----------------------------------------------------------------
        if strict_pristine and (l <= zone_top) and (h >= zone_bottom):
            strict_pristine = False

        # ----------------------------------------------------------------
        # Evaluate attr_pristine ONCE at London open each day.
        # Zone survived Tokyo if price is still OUTSIDE the zone at 07:30:
        #   not japan_bull: zone is ABOVE, price must still be below zone_bottom
        #   japan_bull:     zone is BELOW, price must still be above zone_top
        # ----------------------------------------------------------------
        london_open_bar = (not in_japan and
                           ((dow != 0 and curr_min == ATTR_WINDOW[0]) or
                            (dow == 0 and curr_min == 6*60+30)))
        if london_open_bar:
            if not japan_bull:
                # Zone is above price; need >=ATTR_MIN_GAP pts of clear air below zone_bottom
                attr_pristine = (zone_bottom - c) * 10000 >= ATTR_MIN_GAP
            else:
                # Zone is below price; need >=ATTR_MIN_GAP pts of clear air above zone_top
                attr_pristine = (c - zone_top) * 10000 >= ATTR_MIN_GAP
            london_touched = False   # reset daily

        # Step: save pre-touch state, then update london_touched.
        prev_london_touched = london_touched
        if not in_japan and not london_touched:
            if ((not japan_bull) and (h >= zone_bottom)) or (japan_bull and (l <= zone_top)):
                london_touched = True

        # Skip while a trade is still open (in_trade_until).
        # zone_traded and attr_traded are checked per-block below.
        if i <= in_trade_until:
            continue

        body_pts  = abs(c - o) * 10000
        range_pts = (h - l)   * 10000
        zone_width_pts = (zone_top - zone_bottom) * 10000

        # -- Entry window ----------------------------------------------------
        mon_start = 6 * 60 + 30   # Monday 06:30
        eff_attr_start = mon_start if dow == 0 else ATTR_WINDOW[0]
        eff_rev_start  = mon_start if dow == 0 else REV_WINDOW[0]
        in_attr_sess   = eff_attr_start <= curr_min <= ATTR_WINDOW[1] and not in_japan
        in_rev_sess    = eff_rev_start  <= curr_min <= REV_WINDOW[1]  and not in_japan

        # -- USD news day: skip all signals ----------------------------------
        # (pair-specific EUR/JPY/CAD news is filtered at apply_to_pair level)
        if news_dates and news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        # -- Distance to zone ------------------------------------------------
        dist_to_top    = (c - zone_top)    * 10000   # positive = above zone_top
        dist_to_bottom = (zone_bottom - c) * 10000   # positive = below zone_bottom

        # -- Impulsive approach for attraction -------------------------------
        if i >= 3:
            c_prev3 = df.at[i-3, 'close']
            if not japan_bull:
                approach_pts = (c - c_prev3) * 10000   # long: upward toward zone_bottom
            else:
                approach_pts = (c_prev3 - c) * 10000   # short: downward toward zone_top
        else:
            approach_pts = 0

        impulsive_approach = approach_pts >= ATTR_APPROACH_PTS

        # ================================================================
        # ATTRACTION: zone survived Tokyo (attr_pristine=True at London open).
        # London session retraces back toward the zone. Any bar during the
        # approach can fire the entry if:
        #   - bull/bear pattern signal (engulf/pin/3-bar)
        #   - impulsive 3-bar move toward zone (>= ATTR_APPROACH_PTS)
        #   - at least ATTR_MIN_REWARD pts of room to zone far side from close
        #   - zone width >= ZONE_MIN_WIDTH
        # TP = zone far side (fill the imbalance). SL = bar extreme.
        # ================================================================
        # ATTR: fires during approach, BEFORE London touches the zone.
        # not london_touched ensures we're still on the approach side.
        # Once London touches the zone, only REV applies.
        # One per zone (attr_traded), does NOT consume zone for REV.
        if (attr_pristine and not london_touched and in_attr_sess
                and zone_width_pts >= ZONE_MIN_WIDTH and not attr_traded):

            # Remaining room to zone far side
            reward_long  = (zone_top    - c) * 10000   # long:  close below zone_top
            reward_short = (c - zone_bottom) * 10000   # short: close above zone_bottom

            # ATTR LONG: bearish Japan (zone above), bull signal approaching zone from below.
            # Option 1 (near_edge_tp=False): TP = zone_top  (far side, full gap fill)
            # Option 2 (near_edge_tp=True) : TP = zone_bottom + ATTR_NEAR_BUFFER (near edge)
            # SL mirrored 1:1 in both cases: sl_dist = tp_dist.
            if (not japan_bull and bull_sig.at[i] and impulsive_approach
                    and reward_long >= ATTR_MIN_REWARD):
                tp_price = (zone_bottom + ATTR_NEAR_BUFFER / 10000
                            if near_edge_tp else zone_top)
                sl_d     = tp_price - c        # 1:1: SL dist = TP dist
                sl_price = c - sl_d
                if sl_d > 0:
                    outcome, exit_px, exit_bar = resolve(df, i, c, tp_price, sl_price, 'long')
                    signals.append({
                        'type': 'ATTR_LONG', 'entry_time': str(ts),
                        'entry': round(c,5), 'tp': round(tp_price,5), 'sl': round(sl_price,5),
                        'sl_pts': round(sl_d*10000),
                        'tp_pts': round(sl_d*10000),
                        'zone_top': round(zone_top,5),
                        'zone_bottom': round(zone_bottom,5), 'zone_width': round(zone_width_pts),
                        'pristine': True, 'outcome': outcome, 'exit_px': round(exit_px,5),
                        'exit_time': str(df.at[exit_bar,'time']),
                        'bias_1h': int(row['bias_1h']), 'bias_4h': int(row['bias_4h']),
                    })
                    attr_traded = True; in_trade_until = exit_bar
                continue

            # ATTR SHORT: bullish Japan (zone below), bear signal approaching zone from above.
            # Option 1: TP = zone_bottom (far side)
            # Option 2: TP = zone_top - ATTR_NEAR_BUFFER (near edge)
            # SL mirrored 1:1: sl_dist = tp_dist.
            if (japan_bull and bear_sig.at[i] and impulsive_approach
                    and reward_short >= ATTR_MIN_REWARD):
                tp_price = (zone_top - ATTR_NEAR_BUFFER / 10000
                            if near_edge_tp else zone_bottom)
                sl_d     = c - tp_price        # 1:1: SL dist = TP dist
                sl_price = c + sl_d
                if sl_d > 0:
                    outcome, exit_px, exit_bar = resolve(df, i, c, tp_price, sl_price, 'short')
                    signals.append({
                        'type': 'ATTR_SHORT', 'entry_time': str(ts),
                        'entry': round(c,5), 'tp': round(tp_price,5), 'sl': round(sl_price,5),
                        'sl_pts': round(sl_d*10000),
                        'tp_pts': round(sl_d*10000),
                        'zone_top': round(zone_top,5),
                        'zone_bottom': round(zone_bottom,5), 'zone_width': round(zone_width_pts),
                        'pristine': True, 'outcome': outcome, 'exit_px': round(exit_px,5),
                        'exit_time': str(df.at[exit_bar,'time']),
                        'bias_1h': int(row['bias_1h']), 'bias_4h': int(row['bias_4h']),
                    })
                    attr_traded = True; in_trade_until = exit_bar
                continue

        # ================================================================
        # REVERSAL: zone already touched in London BEFORE this bar
        # (prev_london_touched=True). Big reversal candle near zone,
        # 1H+4H EMA bias aligned, pivot SL, 1:1 R:R.
        # ================================================================
        # REV: fires after London has tested the zone (london_touched=True).
        # Replaces 'not strict_pristine' which was always False by London open
        # because Tokyo bars naturally overlap the zone boundary.
        if (london_touched and in_rev_sess and not zone_traded and
                body_pts >= REV_MIN_BODY and range_pts >= REV_MIN_RANGE):

            b1h = int(row['bias_1h'])
            b4h = int(row['bias_4h'])

            # Count raw candidates before bias filter (for reporting)
            raw_cand = ((bull_sig.at[i] and abs(dist_to_bottom) <= REV_MAX_DIST) or
                        (bear_sig.at[i] and abs(dist_to_top)    <= REV_MAX_DIST))
            if raw_cand:
                raw_rev_candidates += 1

            # REV LONG: big bull candle near zone_bottom + 1H+4H bullish bias
            rev_long_ok = (bull_sig.at[i] and
                           abs(dist_to_bottom) <= REV_MAX_DIST and
                           b1h == 1 and b4h == 1)

            # REV SHORT: big bear candle near zone_top + 1H+4H bearish bias
            rev_short_ok = (bear_sig.at[i] and
                            abs(dist_to_top) <= REV_MAX_DIST and
                            b1h == -1 and b4h == -1)

            if rev_long_ok:
                sl_price = pivot_sl(df, i, 'long')
                sl_d     = c - sl_price
                if sl_d > 0:
                    tp_price = c + sl_d
                    outcome, exit_px, exit_bar = resolve(df, i, c, tp_price, sl_price, 'long')
                    signals.append({
                        'type': 'REV_LONG', 'entry_time': str(ts),
                        'entry': round(c,5), 'tp': round(tp_price,5), 'sl': round(sl_price,5),
                        'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                        'zone_top': round(zone_top,5),
                        'zone_bottom': round(zone_bottom,5), 'zone_width': round(zone_width_pts),
                        'pristine': False, 'outcome': outcome, 'exit_px': round(exit_px,5),
                        'exit_time': str(df.at[exit_bar,'time']),
                        'bias_1h': b1h, 'bias_4h': b4h,
                    })
                    zone_traded = True; in_trade_until = exit_bar
                    continue

            if rev_short_ok:
                sl_price = pivot_sl(df, i, 'short')
                sl_d     = sl_price - c
                if sl_d > 0:
                    tp_price = c - sl_d
                    outcome, exit_px, exit_bar = resolve(df, i, c, tp_price, sl_price, 'short')
                    signals.append({
                        'type': 'REV_SHORT', 'entry_time': str(ts),
                        'entry': round(c,5), 'tp': round(tp_price,5), 'sl': round(sl_price,5),
                        'sl_pts': round(sl_d*10000), 'tp_pts': round(sl_d*10000),
                        'zone_top': round(zone_top,5),
                        'zone_bottom': round(zone_bottom,5), 'zone_width': round(zone_width_pts),
                        'pristine': False, 'outcome': outcome, 'exit_px': round(exit_px,5),
                        'exit_time': str(df.at[exit_bar,'time']),
                        'bias_1h': b1h, 'bias_4h': b4h,
                    })
                    zone_traded = True; in_trade_until = exit_bar

    return signals, raw_rev_candidates

# --- PAIR BACKTEST ------------------------------------------------------------
def apply_to_pair(dxy_signals, df_pair, pair, news_dates=None):
    """
    For each DXY signal, find the matching bar on the pair and resolve the trade.
    SL/TP distances are converted from DXY pts to pair price units via PAIR_FACTOR.
    news_dates: optional dict from load_news_filter() — filters EUR/JPY/CAD news days.
    """
    F   = PAIR_FACTOR[pair]
    D   = PAIR_DIR[pair]

    # Index pair by time for fast lookup
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}

    results = []
    for sig in dxy_signals:
        et = sig['entry_time']
        if et not in pair_idx:
            continue
        # Skip pair-specific news days (USD already filtered in generate_dxy_signals)
        if news_dates and news_blocks_pair(news_dates, et, pair):
            continue
        pi = pair_idx[et]
        pr = df_pair.iloc[pi]

        # Convert DXY SL and TP distances to pair price distances
        # For reversals sl_pts==tp_pts (1:1). For attractions tp_pts may differ.
        sl_dist_pair = sig['sl_pts'] / 10000 * F
        tp_dist_pair = sig.get('tp_pts', sig['sl_pts']) / 10000 * F

        # Determine direction on pair
        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        direction   = 'long' if pair_long else 'short'

        pc = pr['close']
        if direction == 'long':
            sl = pc - sl_dist_pair
            tp = pc + tp_dist_pair
        else:
            sl = pc + sl_dist_pair
            tp = pc - tp_dist_pair

        outcome, exit_px, exit_bar = resolve(df_pair, pi, pc, tp, sl, direction)

        results.append({
            'dxy_type'    : sig['type'],
            'entry_time'  : et,
            'dxy_outcome' : sig['outcome'],
            'pair'        : pair,
            'direction'   : direction,
            'entry'       : round(pc, 5),
            'tp'          : round(tp, 5),
            'sl'          : round(sl, 5),
            'sl_pts_dxy'  : sig['sl_pts'],
            'outcome'     : outcome,
            'exit_px'     : round(exit_px, 5),
            'bias_1h'     : sig['bias_1h'],
            'bias_4h'     : sig['bias_4h'],
        })

    return results

# --- PAIR BACKTEST (DXY-EXIT VARIANT) ----------------------------------------
def apply_to_pair_dxy_exit(dxy_signals, df_pair, pair, news_dates=None):
    """
    Exit pair trades when DXY hits its own TP or SL — not when the pair does.
    The pair may have moved only partially toward its target, so outcomes are
    expressed as fractional R (e.g. +0.6R, -0.3R) rather than binary ±1R.

    This captures the case where DXY completes its move but the pair has been
    pushed sideways by pair-specific factors (oil for USDCAD, safe-haven flows
    for USDCHF), exiting before divergence reverses the trade into a full loss.
    news_dates: optional dict from load_news_filter() — filters EUR/JPY/CAD news days.
    """
    F = PAIR_FACTOR[pair]
    D = PAIR_DIR[pair]
    pair_idx = {str(t): i for i, t in enumerate(df_pair['time'])}

    results = []
    for sig in dxy_signals:
        et = sig['entry_time']
        xt = sig.get('exit_time')
        if et not in pair_idx or not xt or xt not in pair_idx:
            continue
        # Skip pair-specific news days (USD already filtered in generate_dxy_signals)
        if news_dates and news_blocks_pair(news_dates, et, pair):
            continue

        pi = pair_idx[et]
        xi = pair_idx[xt]

        pc = df_pair.at[pi, 'close']   # pair price at DXY entry bar
        px = df_pair.at[xi, 'close']   # pair price at DXY exit bar

        is_long_dxy = 'LONG' in sig['type']
        pair_long   = (is_long_dxy and D == 1) or (not is_long_dxy and D == -1)
        pair_sl_dist = sig['sl_pts'] / 10000 * F

        raw_pnl = (px - pc) if pair_long else (pc - px)
        r_actual = raw_pnl / pair_sl_dist if pair_sl_dist > 0 else 0.0
        outcome  = 'win' if r_actual > 0 else ('loss' if r_actual < 0 else 'even')

        results.append({
            'dxy_type'   : sig['type'],
            'entry_time' : et,
            'exit_time'  : xt,
            'dxy_outcome': sig['outcome'],
            'pair'       : pair,
            'direction'  : 'long' if pair_long else 'short',
            'entry'      : round(pc, 5),
            'exit_px'    : round(px, 5),
            'sl_pts_dxy' : sig['sl_pts'],
            'outcome'    : outcome,
            'r_actual'   : round(r_actual, 3),
            'bias_1h'    : sig['bias_1h'],
            'bias_4h'    : sig['bias_4h'],
        })
    return results


# --- REPORTING ----------------------------------------------------------------
def stats(trades, label):
    if not trades:
        return {'label': label, 'N': 0}
    tdf = pd.DataFrame(trades)
    n   = len(tdf)
    w   = (tdf['outcome'] == 'win').sum()
    l   = (tdf['outcome'] == 'loss').sum()
    t   = (tdf['outcome'] == 'timeout').sum()
    wr  = w / (w + l) * 100 if (w + l) > 0 else 0
    pf  = w / l if l > 0 else float('inf')
    net = w - l  # R units at 1:1
    return {'label': label, 'N': n, 'W': w, 'L': l, 'T': t,
            'WR%': round(wr,1), 'PF': round(pf,3), 'NetR': net}

def print_stats(s):
    if s['N'] == 0:
        print(f"  {s['label']:<22}: No trades")
        return
    t_str = f" T:{s['T']}" if s.get('T',0) > 0 else ''
    pf_str = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "inf"
    print(f"  {s['label']:<22}: N={s['N']:3d}  W={s['W']:3d} L={s['L']:3d}{t_str:<6}"
          f"  WR={s['WR%']:5.1f}%  PF={pf_str:<6}  Net={s['NetR']:+d}R")

# --- FRACTIONAL-R STATS (DXY-exit variant) ------------------------------------
def stats_r(trades, label):
    """
    Stats for DXY-exit variant where each trade has an 'r_actual' fractional value.
    PF = sum(positive Rs) / sum(|negative Rs|).  NetR = sum of all Rs.
    """
    if not trades:
        return {'label': label, 'N': 0}
    tdf   = pd.DataFrame(trades)
    n     = len(tdf)
    wins  = tdf[tdf['r_actual'] > 0]
    loss  = tdf[tdf['r_actual'] < 0]
    w, l  = len(wins), len(loss)
    wr    = w / (w + l) * 100 if (w + l) > 0 else 0
    gw    = wins['r_actual'].sum()
    gl    = loss['r_actual'].abs().sum()
    pf    = gw / gl if gl > 0 else float('inf')
    net   = round(tdf['r_actual'].sum(), 2)
    avg_w = round(gw / w, 2) if w > 0 else 0
    avg_l = round(gl / l, 2) if l > 0 else 0
    return {'label': label, 'N': n, 'W': w, 'L': l,
            'WR%': round(wr, 1), 'PF': round(pf, 3), 'NetR': net,
            'AvgW': avg_w, 'AvgL': avg_l}

def print_stats_r(s):
    if s['N'] == 0:
        print(f"  {s['label']:<22}: No trades")
        return
    pf_str = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "inf"
    print(f"  {s['label']:<22}: N={s['N']:3d}  W={s['W']:3d} L={s['L']:3d}"
          f"  WR={s['WR%']:5.1f}%  PF={pf_str:<6}  NetR={s['NetR']:>+7.1f}R"
          f"  (avgW={s['AvgW']:+.2f}R  avgL={s['AvgL']:-.2f}R)")

def print_variant_dxy_exit(label, dxy_signals, all_pair_trades, raw_rev):
    print()
    print("=" * 72)
    print(f"  {label}")
    print("=" * 72)

    print("  -- DXY signal quality (unchanged) --")
    for key, subset in [
        ("ALL",        dxy_signals),
        ("ATTR",       [s for s in dxy_signals if s['type'].startswith('ATTR')]),
        ("GAP_REJ",    [s for s in dxy_signals if s['type'].startswith('GAP_REJ')]),
        ("REV",        [s for s in dxy_signals if s['type'].startswith('REV')]),
    ]:
        print_stats(stats(subset, f"  DXY {key}"))

    print("\n  -- Pair results (fractional R, exiting at DXY TP/SL bar) --")
    print(f"  {'Pair':<10} {'N':>4}  {'W':>4} {'L':>4}  {'WR%':>6}  {'PF':>6}  "
          f"{'NetR':>8}  {'AvgW':>6}  {'AvgL':>6}")
    print(f"  {'-'*68}")
    for pair in PAIRS:
        pt = [t for t in all_pair_trades if t['pair'] == pair]
        s  = stats_r(pt, pair)
        if s['N'] == 0: continue
        pf_str = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "  inf"
        print(f"  {pair:<10} {s['N']:>4}  {s['W']:>4} {s['L']:>4}  "
              f"{s['WR%']:>5.1f}%  {pf_str:>6}  {s['NetR']:>+8.1f}R"
              f"  {s['AvgW']:>+5.2f}R  {s['AvgL']:>-5.2f}R")

    sp  = stats_r(all_pair_trades, "PORTFOLIO")
    sa  = stats_r([t for t in all_pair_trades if t['dxy_type'].startswith('ATTR')],    "ATTR")
    sgr = stats_r([t for t in all_pair_trades if t['dxy_type'].startswith('GAP_REJ')], "GAP_REJ")
    sr  = stats_r([t for t in all_pair_trades if t['dxy_type'].startswith('REV')],     "REV")
    print()
    for s in [sp, sa, sgr, sr]:
        print_stats_r(s)

def profit_estimate_r(label, all_pair_trades, account=100_000, risk_pct=0.0025):
    """Dollar P&L estimate using fractional R: each 1R = account × risk_pct."""
    risk_per_trade = account * risk_pct
    rows = []
    for pair in PAIRS:
        pt = [t for t in all_pair_trades if t['pair'] == pair]
        s  = stats_r(pt, pair)
        if s['N'] == 0: continue
        net_r  = s['NetR']
        dollar = net_r * risk_per_trade
        rows.append((pair, s['N'], s['WR%'], net_r, dollar))

    total_r   = sum(r[3] for r in rows)
    total_usd = total_r * risk_per_trade

    print()
    print("=" * 72)
    print(f"  PROFIT ESTIMATE (DXY EXIT): {label}")
    print(f"  Account: ${account:,.0f}  |  Risk/trade: {risk_pct*100:.2f}%"
          f"  = ${risk_per_trade:,.0f} per trade  (fractional R)")
    print("=" * 72)
    print(f"  {'Pair':<10} {'Trades':>6}  {'WR%':>6}  {'Net R':>8}  {'Profit':>10}")
    print(f"  {'-'*52}")
    for pair, n, wr, nr, d in rows:
        sign = "+" if d >= 0 else ""
        print(f"  {pair:<10} {n:>6}  {wr:>5.1f}%  {nr:>+8.1f}R  {sign}${d:>9,.0f}")
    print(f"  {'-'*52}")
    sign = "+" if total_usd >= 0 else ""
    print(f"  {'TOTAL':<10} {'':>6}  {'':>6}  {total_r:>+8.1f}R  {sign}${total_usd:>9,.0f}")
    ann  = total_usd / 10 * 12
    sign2 = "+" if ann >= 0 else ""
    print(f"\n  Annualised estimate: {sign2}${ann:,.0f}/yr  (~10-month backtest)")
    print(f"  Return on account:   {sign2}{total_usd/account*100:.1f}% over 10 months"
          f"  /  {sign2}{ann/account*100:.1f}% annualised")


# --- HELPERS ------------------------------------------------------------------
def run_variant(df_dxy, pair_dfs, near_edge_tp=False, news_dates=None):
    """Run full backtest for one TP variant. Returns (dxy_signals, all_pair_trades, raw_rev)."""
    dxy_signals, raw_rev = generate_dxy_signals(df_dxy, near_edge_tp=near_edge_tp,
                                                 news_dates=news_dates)
    all_pair_trades = []
    for pair in PAIRS:
        all_pair_trades.extend(apply_to_pair(dxy_signals, pair_dfs[pair], pair,
                                             news_dates=news_dates))
    return dxy_signals, all_pair_trades, raw_rev


def print_variant(label, dxy_signals, all_pair_trades, raw_rev):
    print()
    print("=" * 72)
    print(f"  {label}")
    print("=" * 72)

    # DXY quality
    print("  -- DXY signal quality --")
    for key, subset in [
        ("ALL",        dxy_signals),
        ("ATTR",       [s for s in dxy_signals if 'ATTR' in s['type']]),
        ("ATTR_LONG",  [s for s in dxy_signals if s['type'] == 'ATTR_LONG']),
        ("ATTR_SHORT", [s for s in dxy_signals if s['type'] == 'ATTR_SHORT']),
        ("REV",        [s for s in dxy_signals if 'REV'  in s['type']]),
        ("REV_LONG",   [s for s in dxy_signals if s['type'] == 'REV_LONG']),
        ("REV_SHORT",  [s for s in dxy_signals if s['type'] == 'REV_SHORT']),
    ]:
        print_stats(stats(subset, f"  DXY {key}"))

    n_rev = len([s for s in dxy_signals if 'REV' in s['type']])
    if raw_rev > 0:
        print(f"\n  Bias filter: {n_rev}/{raw_rev} rev candidates kept "
              f"({n_rev/raw_rev*100:.0f}%)")

    # Pair summary
    print("\n  -- Pair results --")
    print(f"  {'Pair':<10} {'N':>4}  {'W':>4} {'L':>4} {'T':>4}  {'WR%':>6}  {'PF':>6}  {'NetR':>6}")
    print(f"  {'-'*60}")
    for pair in PAIRS:
        pt = [t for t in all_pair_trades if t['pair'] == pair]
        s  = stats(pt, pair)
        if s['N'] == 0: continue
        pf_str = f"{s['PF']:.3f}" if s['PF'] != float('inf') else "  inf"
        print(f"  {pair:<10} {s['N']:>4}  {s['W']:>4} {s['L']:>4} {s.get('T',0):>4}  "
              f"{s['WR%']:>5.1f}%  {pf_str:>6}  {s['NetR']:>+6}R")

    # Portfolio
    sp = stats(all_pair_trades, "PORTFOLIO")
    sa = stats([t for t in all_pair_trades if 'ATTR' in t['dxy_type']], "ATTR")
    sr = stats([t for t in all_pair_trades if 'REV'  in t['dxy_type']], "REV")
    print()
    for s in [sp, sa, sr]:
        print_stats(s)


def profit_estimate(label, all_pair_trades, account=100_000, risk_pct=0.0025):
    """Print dollar P&L estimate: each R = account * risk_pct."""
    risk_per_trade = account * risk_pct
    rows = []
    for pair in PAIRS:
        pt = [t for t in all_pair_trades if t['pair'] == pair]
        s  = stats(pt, pair)
        if s['N'] == 0: continue
        net_r  = s['NetR']
        dollar = net_r * risk_per_trade
        rows.append((pair, s['N'], s['WR%'], net_r, dollar))

    total_r   = sum(r[3] for r in rows)
    total_usd = total_r * risk_per_trade

    print()
    print("=" * 72)
    print(f"  PROFIT ESTIMATE: {label}")
    print(f"  Account: ${account:,.0f}  |  Risk/trade: {risk_pct*100:.2f}%"
          f"  = ${risk_per_trade:,.0f} per trade")
    print("=" * 72)
    print(f"  {'Pair':<10} {'Trades':>6}  {'WR%':>6}  {'Net R':>6}  {'Profit':>10}")
    print(f"  {'-'*50}")
    for pair, n, wr, nr, d in rows:
        sign = "+" if d >= 0 else ""
        print(f"  {pair:<10} {n:>6}  {wr:>5.1f}%  {nr:>+6}R  {sign}${d:>9,.0f}")
    print(f"  {'-'*50}")
    sign = "+" if total_usd >= 0 else ""
    print(f"  {'TOTAL':<10} {'':>6}  {'':>6}  {total_r:>+6}R  {sign}${total_usd:>9,.0f}")
    note = "(~10-month backtest period)"
    ann  = total_usd / 10 * 12
    sign2 = "+" if ann >= 0 else ""
    print(f"\n  Annualised estimate: {sign2}${ann:,.0f}/yr  {note}")
    print(f"  Return on account:   {sign2}{total_usd/account*100:.1f}% over 10 months"
          f"  /  {sign2}{ann/account*100:.1f}% annualised")


# --- MAIN ---------------------------------------------------------------------
def main():
    print("Loading DXY and pair data...")
    df_dxy   = load('DXY')
    pair_dfs = {p: load(p) for p in PAIRS}

    # Load news filter (optional — runs without it if CSV not yet available)
    news_dates = load_news_filter()
    if news_dates:
        n_days = len(news_dates)
        n_usd  = sum(1 for s in news_dates.values() if 'USD' in s)
        n_eur  = sum(1 for s in news_dates.values() if 'EUR' in s)
        n_jpy  = sum(1 for s in news_dates.values() if 'JPY' in s)
        n_cad  = sum(1 for s in news_dates.values() if 'CAD' in s)
        print(f"News filter loaded: {n_days} dates with high-impact events")
        print(f"  USD days (all pairs skipped): {n_usd}")
        print(f"  EUR days (EURUSD skipped):    {n_eur}")
        print(f"  JPY days (USDJPY skipped):    {n_jpy}")
        print(f"  CAD days (USDCAD skipped):    {n_cad}")
    else:
        print("News filter: not found — running without news filter")

    print("Running Option 1: TP = zone FAR side (full gap fill)...")
    sigs1, pt1, raw1 = run_variant(df_dxy, pair_dfs, near_edge_tp=False,
                                   news_dates=news_dates)

    print("Running Option 2: TP = zone NEAR edge + 50 pt buffer...")
    sigs2, pt2, raw2 = run_variant(df_dxy, pair_dfs, near_edge_tp=True,
                                   news_dates=news_dates)

    print("Running Option 2 + DXY Exit: exit pair when DXY hits its TP/SL...")
    pt_dxy = []
    for pair in PAIRS:
        pt_dxy.extend(apply_to_pair_dxy_exit(sigs2, pair_dfs[pair], pair,
                                             news_dates=news_dates))

    # -- Results ---------------------------------------------------------------
    print_variant("OPTION 1: ATTR TP = zone far side  (full gap fill, 1:1)", sigs1, pt1, raw1)
    print_variant("OPTION 2: ATTR TP = near edge + 50 pt buffer  (1:1)",     sigs2, pt2, raw2)
    print_variant_dxy_exit(
        "OPTION 3: DXY EXIT — pair exits when DXY hits its TP/SL bar",
        sigs2, pt_dxy, raw2)

    # -- Profit estimates ($100k, 0.25% risk) ----------------------------------
    profit_estimate("Option 1 — TP at zone far side",      pt1)
    profit_estimate("Option 2 — TP at near edge + 50 pts", pt2)
    profit_estimate_r("Option 3 — DXY Exit",               pt_dxy)

    # -- Per-pair comparison table --------------------------------------------
    print()
    print("=" * 72)
    print("  PAIR COMPARISON: Opt2 (pair exit) vs Opt3 (DXY exit)")
    print("=" * 72)
    print(f"  {'Pair':<10}  {'Opt2 PF':>8}  {'Opt2 NetR':>10}  "
          f"{'Opt3 PF':>8}  {'Opt3 NetR':>10}  {'Delta NetR':>11}")
    print(f"  {'-'*64}")
    for pair in PAIRS:
        s2 = stats([t for t in pt2    if t['pair'] == pair], pair)
        s3 = stats_r([t for t in pt_dxy if t['pair'] == pair], pair)
        if s2['N'] == 0 and s3['N'] == 0: continue
        pf2 = f"{s2['PF']:.3f}" if s2.get('PF', 0) != float('inf') else "  inf"
        pf3 = f"{s3['PF']:.3f}" if s3.get('PF', 0) != float('inf') else "  inf"
        nr2 = s2.get('NetR', 0)
        nr3 = s3.get('NetR', 0)
        delta = nr3 - nr2
        sign  = "+" if delta >= 0 else ""
        print(f"  {pair:<10}  {pf2:>8}  {nr2:>+10}R  "
              f"{pf3:>8}  {nr3:>+10.1f}R  {sign}{delta:>+10.1f}R")

    # -- Save Option 2 as baseline ---------------------------------------------
    pd.DataFrame(sigs2).to_csv(BASE / 'dxy_clean_signals.csv', index=False)
    pd.DataFrame(pt2).to_csv(BASE / 'dxy_clean_pair_trades.csv', index=False)
    pd.DataFrame(pt_dxy).to_csv(BASE / 'dxy_clean_pair_trades_dxy_exit.csv', index=False)
    print(f"\n  Saved: dxy_clean_signals.csv / dxy_clean_pair_trades.csv"
          f" / dxy_clean_pair_trades_dxy_exit.csv")
    print()

if __name__ == '__main__':
    main()
