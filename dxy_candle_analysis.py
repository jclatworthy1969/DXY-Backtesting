"""
DXY Zone Strategy — Signal Candle Size Analysis
Answers: is there an optimal candle body/range size that filters profitable reversal trades?

Captures at each reversal entry:
  - sig_body_pts   : abs(close - open) * 10000  (body size in pts)
  - sig_range_pts  : (high - low) * 10000        (full candle range in pts)
  - atr_pts        : 14-period ATR in pts
  - body_atr_pct   : sig_body_pts / atr_pts * 100  (body as % of ATR — normalised impulsiveness)

Then:
  1. Prints raw reversal trade log with candle metrics
  2. Buckets by body size and range size
  3. Sweeps minimum body threshold to find optimal filter
"""

import pandas as pd
import numpy as np

CSV_PATH = r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting\TVC_DXY, 15.csv"

ATTR_ENABLED  = True
ATTR_MIN_PTS  = 200
ATTR_MAX_PTS  = 2000
ZONE_MIN_GAP  = 30
REV_ENABLED   = True
REV_MIN_SL    = 3000
REV_MAX_DIST  = 500
ENTRY_START_H, ENTRY_START_M = 7, 30
ENTRY_END_H,   ENTRY_END_M   = 19, 30
REV_END_H,     REV_END_M     = 12, 0
MONDAY_START_H               = 6
JAPAN_END_H                  = 6
USE_ENGULF    = True
USE_PIN       = True
USE_3BAR      = True
PIN_WICK_MULT = 2.0
DIV_LOOKBACK  = 15
REV_MIN_DIV   = 1
MAX_LOOKFORWARD = 400
EXIT_MODE = 'intrabar'

# ---------------------------------------------------------------------------

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
    lo  = low.rolling(period).min()
    hi  = high.rolling(period).max()
    rng = hi - lo
    return np.where(rng > 0, (close - lo) / rng * 100, 50)

def cci(hlc3, period=20):
    sma = hlc3.rolling(period).mean()
    mad = hlc3.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return np.where(mad > 0, (hlc3 - sma) / (0.015 * mad), 0)

def williams_r(close, high, low, period=14):
    hi  = high.rolling(period).max()
    lo  = low.rolling(period).min()
    rng = hi - lo
    return np.where(rng > 0, (hi - close) / rng * -100, -50)

def momentum(close, period=10):
    return close - close.shift(period)

def atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()

def compute_indicators(df):
    df = df.copy()
    df['hlc3']    = (df['high'] + df['low'] + df['close']) / 3
    df['rsi']     = rsi(df['close'], 14)
    df['macd_h']  = macd_histogram(df['close'])
    df['stoch_k'] = stochastic_k(df['close'], df['high'], df['low'], 14)
    df['cci_v']   = cci(df['hlc3'], 20)
    df['wpr_v']   = williams_r(df['close'], df['high'], df['low'], 14)
    df['mom_v']   = momentum(df['close'], 10)
    df['atr14']   = atr(df['high'], df['low'], df['close'], 14)
    return df

def rolling_extremes(series, lb):
    return series.rolling(lb).min(), series.rolling(lb).max()

def div_score_bull(df, lb):
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

def candle_patterns(df):
    c, o, h, l = df['close'], df['open'], df['high'], df['low']
    body_sz    = (c - o).abs()
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    cndl_range = h - l
    is_bull    = (c > o).astype(bool)
    is_bear    = (c < o).astype(bool)

    bull_engulf = (USE_ENGULF & is_bull & ~is_bull.shift(1).fillna(False).astype(bool) &
                   (c > o.shift(1)) & (o < c.shift(1)) &
                   (body_sz >= body_sz.shift(1) * 0.8))
    bear_engulf = (USE_ENGULF & is_bear & ~is_bear.shift(1).fillna(False).astype(bool) &
                   (c < o.shift(1)) & (o > c.shift(1)) &
                   (body_sz >= body_sz.shift(1) * 0.8))

    bull_pin = (USE_PIN &
                (lower_wick >= body_sz * PIN_WICK_MULT) &
                (upper_wick <= body_sz * 1.5) &
                (cndl_range > 0))
    bear_pin = (USE_PIN &
                (upper_wick >= body_sz * PIN_WICK_MULT) &
                (lower_wick <= body_sz * 1.5) &
                (cndl_range > 0))

    bar2_range = (c.shift(2) - o.shift(2)).abs()
    indecision = (body_sz.shift(1) <= bar2_range * 0.5)
    bull_3bar  = (USE_3BAR & (c.shift(2) < o.shift(2)) & indecision &
                  is_bull & (c > o.shift(2)))
    bear_3bar  = (USE_3BAR & (c.shift(2) > o.shift(2)) & indecision &
                  is_bear & (c < o.shift(2)))

    bull_sig = bull_engulf | bull_pin | bull_3bar
    bear_sig = bear_engulf | bear_pin | bear_3bar
    return bull_sig.fillna(False), bear_sig.fillna(False)

def session_flags(df):
    ts  = pd.to_datetime(df['time'], utc=True)
    h   = ts.dt.hour
    m   = ts.dt.minute
    dow = ts.dt.dayofweek
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
    return pd.DataFrame({'in_sess': in_sess, 'in_rev_sess': in_rev_sess,
                         'in_japan': in_japan, 'is_2345': is_2345}, index=df.index)

def form_zone(df, i):
    if i < 1:
        return None, None, None
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
    if abs(zone_top - zone_bottom) * 10000 < 1:
        zone_top    = max(japan_open, japan_close) + 0.001
        zone_bottom = min(japan_open, japan_close)
    return zone_top, zone_bottom, japan_bull

def resolve_trade(df, entry_idx, entry_price, tp, sl, direction):
    n = len(df)
    for j in range(entry_idx + 1, min(entry_idx + MAX_LOOKFORWARD, n)):
        c_j = df.at[j, 'close']
        h_j = df.at[j, 'high']  if EXIT_MODE == 'intrabar' else c_j
        l_j = df.at[j, 'low']   if EXIT_MODE == 'intrabar' else c_j
        o_j = df.at[j, 'open']
        if direction == 'long':
            if EXIT_MODE == 'intrabar' and o_j <= sl:
                return ('loss', sl, j)
            if h_j >= tp and l_j <= sl:
                return ('win', tp, j) if abs(o_j - sl) >= abs(tp - o_j) else ('loss', sl, j)
            if h_j >= tp:
                return ('win', tp, j)
            if l_j <= sl:
                return ('loss', sl, j)
        else:
            if EXIT_MODE == 'intrabar' and o_j >= sl:
                return ('loss', sl, j)
            if l_j <= tp and h_j >= sl:
                return ('win', tp, j) if abs(o_j - sl) >= abs(o_j - tp) else ('loss', sl, j)
            if l_j <= tp:
                return ('win', tp, j)
            if h_j >= sl:
                return ('loss', sl, j)
    return ('timeout', df.at[min(entry_idx + MAX_LOOKFORWARD - 1, n - 1), 'close'], entry_idx + MAX_LOOKFORWARD)

# ---------------------------------------------------------------------------

def run():
    print("Loading data...")
    df_raw = pd.read_csv(CSV_PATH, low_memory=False)
    df = df_raw[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)

    print("Computing indicators...")
    df = compute_indicators(df)
    df['bull_div'] = div_score_bull(df, DIV_LOOKBACK)
    df['bear_div'] = div_score_bear(df, DIV_LOOKBACK)
    df['bull_sig'], df['bear_sig'] = candle_patterns(df)
    sess = session_flags(df)
    df = pd.concat([df, sess], axis=1)

    trades = []
    zone_top = zone_bottom = np.nan
    japan_bull = zone_pristine = zone_body_clean = zone_traded = False
    japan_candle_cnt = 0
    n = len(df)

    print("Running strategy...")
    for i in range(2, n):
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']

        if row['is_2345']:
            zt, zb, jb = form_zone(df, i)
            if zt is not None:
                zone_top = zt; zone_bottom = zb; japan_bull = jb
                zone_pristine = zone_body_clean = True
                japan_candle_cnt = 0; zone_traded = False
            continue

        if np.isnan(zone_top):
            continue

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

        if zone_traded:
            continue

        dist_tp_long   = (zone_top    - c) * 10000
        dist_tp_short  = (c - zone_bottom) * 10000
        dist_rev_long  = abs(c - zone_bottom) * 10000
        dist_rev_short = abs(zone_top - c)    * 10000

        # Candle metrics for the signal bar
        sig_body_pts  = abs(c - o) * 10000
        sig_range_pts = (h - l) * 10000
        atr_pts       = row['atr14'] * 10000
        body_atr_pct  = (sig_body_pts / atr_pts * 100) if atr_pts > 0 else 0
        range_atr_pct = (sig_range_pts / atr_pts * 100) if atr_pts > 0 else 0

        # Attraction
        if (ATTR_ENABLED and zone_body_clean and zone_pristine and
                row['in_sess'] and not row['in_japan']):
            if (not japan_bull and row['bull_sig'] and
                    ATTR_MIN_PTS <= dist_tp_long <= ATTR_MAX_PTS):
                tp = zone_top; sl_d = zone_top - c; sl = c - sl_d
                outcome, exit_px, _ = resolve_trade(df, i, c, tp, sl, 'long')
                trades.append({'type': 'ATTR_LONG', 'entry_time': row['time'],
                    'entry_price': round(c,5), 'sl_pts': round(sl_d*10000),
                    'outcome': outcome,
                    'sig_body_pts': round(sig_body_pts,1), 'sig_range_pts': round(sig_range_pts,1),
                    'atr_pts': round(atr_pts,1), 'body_atr_pct': round(body_atr_pct,1),
                    'range_atr_pct': round(range_atr_pct,1),
                    'pnl_pts': round((exit_px-c)*10000 if outcome=='win' else (c-exit_px)*10000*-1)})
                zone_traded = True; continue
            if (japan_bull and row['bear_sig'] and
                    ATTR_MIN_PTS <= dist_tp_short <= ATTR_MAX_PTS):
                tp = zone_bottom; sl_d = c - zone_bottom; sl = c + sl_d
                outcome, exit_px, _ = resolve_trade(df, i, c, tp, sl, 'short')
                trades.append({'type': 'ATTR_SHORT', 'entry_time': row['time'],
                    'entry_price': round(c,5), 'sl_pts': round(sl_d*10000),
                    'outcome': outcome,
                    'sig_body_pts': round(sig_body_pts,1), 'sig_range_pts': round(sig_range_pts,1),
                    'atr_pts': round(atr_pts,1), 'body_atr_pct': round(body_atr_pct,1),
                    'range_atr_pct': round(range_atr_pct,1),
                    'pnl_pts': round((c-exit_px)*10000 if outcome=='win' else (exit_px-c)*10000*-1)})
                zone_traded = True; continue

        # Reversal
        if (REV_ENABLED and not zone_pristine and
                row['in_rev_sess'] and not row['in_japan']):
            bull_ok = (row['bull_sig'] and row['bull_div'] >= REV_MIN_DIV and dist_rev_long <= REV_MAX_DIST)
            bear_ok = (row['bear_sig'] and row['bear_div'] >= REV_MIN_DIV and dist_rev_short <= REV_MAX_DIST)

            if bull_ok:
                min_d = REV_MIN_SL / 10000.0
                sl_d  = max(c - zone_bottom, min_d)
                tp = c + sl_d; sl = c - sl_d
                outcome, exit_px, _ = resolve_trade(df, i, c, tp, sl, 'long')
                trades.append({'type': 'REV_LONG', 'entry_time': row['time'],
                    'entry_price': round(c,5), 'sl_pts': round(sl_d*10000),
                    'outcome': outcome,
                    'sig_body_pts': round(sig_body_pts,1), 'sig_range_pts': round(sig_range_pts,1),
                    'atr_pts': round(atr_pts,1), 'body_atr_pct': round(body_atr_pct,1),
                    'range_atr_pct': round(range_atr_pct,1),
                    'pnl_pts': round((exit_px-c)*10000 if outcome=='win' else (c-exit_px)*10000*-1)})
                zone_traded = True; continue

            if bear_ok:
                min_d = REV_MIN_SL / 10000.0
                sl_d  = max(zone_top - c, min_d)
                tp = c - sl_d; sl = c + sl_d
                outcome, exit_px, _ = resolve_trade(df, i, c, tp, sl, 'short')
                trades.append({'type': 'REV_SHORT', 'entry_time': row['time'],
                    'entry_price': round(c,5), 'sl_pts': round(sl_d*10000),
                    'outcome': outcome,
                    'sig_body_pts': round(sig_body_pts,1), 'sig_range_pts': round(sig_range_pts,1),
                    'atr_pts': round(atr_pts,1), 'body_atr_pct': round(body_atr_pct,1),
                    'range_atr_pct': round(range_atr_pct,1),
                    'pnl_pts': round((exit_px-c)*10000 if outcome=='win' else (exit_px-c)*10000*-1)})
                zone_traded = True

    tdf = pd.DataFrame(trades)
    rev = tdf[tdf['type'].str.startswith('REV') & tdf['outcome'].isin(['win','loss'])].copy()

    # ---------------------------------------------------------------------------
    # 1. Raw reversal trade log
    # ---------------------------------------------------------------------------
    print("\n" + "="*80)
    print("  REVERSAL TRADES — SIGNAL CANDLE METRICS")
    print("="*80)
    print(f"  {'Date':22s}  {'Type':10s}  {'Body':>7}  {'Range':>7}  {'ATR':>7}  {'Body%ATR':>9}  {'Result':>6}")
    print(f"  {'-'*22}  {'-'*10}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*9}  {'-'*6}")
    for _, r in rev.iterrows():
        mark = 'WIN' if r['outcome'] == 'win' else 'LOSS'
        print(f"  {str(r['entry_time'])[:22]:22s}  {r['type']:10s}  "
              f"{r['sig_body_pts']:>7.1f}  {r['sig_range_pts']:>7.1f}  "
              f"{r['atr_pts']:>7.1f}  {r['body_atr_pct']:>9.1f}  {mark:>6}")

    # ---------------------------------------------------------------------------
    # 2. Body size bucket breakdown
    # ---------------------------------------------------------------------------
    def bucket_analysis(rev, col, buckets, label):
        print(f"\n{'='*70}")
        print(f"  REVERSAL PF/WR BY {label} (pts)")
        print(f"{'='*70}")
        print(f"  {'Bucket':>16}  {'Trades':>6}  {'Wins':>5}  {'WR%':>6}  {'PF':>6}  {'Net pts':>8}")
        print(f"  {'-'*16}  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*8}")
        for lo, hi in buckets:
            sub = rev[(rev[col] >= lo) & (rev[col] < hi)]
            if len(sub) == 0:
                continue
            wins   = (sub['outcome'] == 'win').sum()
            losses = (sub['outcome'] == 'loss').sum()
            wr     = wins / len(sub) * 100
            gw = sub[sub['outcome']=='win']['sl_pts'].sum()
            gl = sub[sub['outcome']=='loss']['sl_pts'].sum()
            pf = gw / gl if gl > 0 else float('inf')
            net = gw - gl
            label_str = f"{lo}-{hi}"
            print(f"  {label_str:>16}  {len(sub):>6}  {wins:>5}  {wr:>6.1f}  {pf:>6.3f}  {net:>8.0f}")

    body_buckets  = [(0,50),(50,100),(100,150),(150,200),(200,300),(300,500),(500,9999)]
    range_buckets = [(0,100),(100,150),(150,200),(200,300),(300,400),(400,600),(600,9999)]

    bucket_analysis(rev, 'sig_body_pts',  body_buckets,  'SIGNAL CANDLE BODY SIZE')
    bucket_analysis(rev, 'sig_range_pts', range_buckets, 'SIGNAL CANDLE RANGE SIZE')

    # ---------------------------------------------------------------------------
    # 3. Minimum body threshold sweep
    # ---------------------------------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  MINIMUM BODY SIZE THRESHOLD SWEEP (reversal trades only)")
    print(f"{'='*70}")
    print(f"  {'Min Body':>9}  {'Trades':>6}  {'WR%':>6}  {'PF':>6}  {'Net pts':>8}  {'Removed':>8}")
    print(f"  {'-'*9}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*8}")
    total_rev = len(rev)
    for thresh in [0, 25, 50, 75, 100, 125, 150, 175, 200, 250, 300]:
        sub = rev[rev['sig_body_pts'] >= thresh]
        if len(sub) == 0:
            continue
        wins   = (sub['outcome'] == 'win').sum()
        losses = (sub['outcome'] == 'loss').sum()
        wr     = wins / len(sub) * 100
        gw = sub[sub['outcome']=='win']['sl_pts'].sum()
        gl = sub[sub['outcome']=='loss']['sl_pts'].sum()
        pf = gw / gl if gl > 0 else float('inf')
        net = gw - gl
        removed = total_rev - len(sub)
        print(f"  {thresh:>9}  {len(sub):>6}  {wr:>6.1f}  {pf:>6.3f}  {net:>8.0f}  {removed:>8}")

    # ---------------------------------------------------------------------------
    # 4. Minimum range threshold sweep
    # ---------------------------------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  MINIMUM RANGE SIZE THRESHOLD SWEEP (reversal trades only)")
    print(f"{'='*70}")
    print(f"  {'Min Range':>9}  {'Trades':>6}  {'WR%':>6}  {'PF':>6}  {'Net pts':>8}  {'Removed':>8}")
    print(f"  {'-'*9}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*8}")
    for thresh in [0, 50, 100, 125, 150, 175, 200, 250, 300, 400]:
        sub = rev[rev['sig_range_pts'] >= thresh]
        if len(sub) == 0:
            continue
        wins   = (sub['outcome'] == 'win').sum()
        losses = (sub['outcome'] == 'loss').sum()
        wr     = wins / len(sub) * 100
        gw = sub[sub['outcome']=='win']['sl_pts'].sum()
        gl = sub[sub['outcome']=='loss']['sl_pts'].sum()
        pf = gw / gl if gl > 0 else float('inf')
        net = gw - gl
        removed = total_rev - len(sub)
        print(f"  {thresh:>9}  {len(sub):>6}  {wr:>6.1f}  {pf:>6.3f}  {net:>8.0f}  {removed:>8}")

    # ---------------------------------------------------------------------------
    # 5. Body as % of ATR sweep (normalised for current volatility)
    # ---------------------------------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  MINIMUM BODY/ATR % THRESHOLD SWEEP (normalised impulsiveness)")
    print(f"{'='*70}")
    print(f"  {'Min Body%ATR':>12}  {'Trades':>6}  {'WR%':>6}  {'PF':>6}  {'Net pts':>8}  {'Removed':>8}")
    print(f"  {'-'*12}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*8}")
    for thresh in [0, 10, 20, 30, 40, 50, 60, 75, 100]:
        sub = rev[rev['body_atr_pct'] >= thresh]
        if len(sub) == 0:
            continue
        wins   = (sub['outcome'] == 'win').sum()
        losses = (sub['outcome'] == 'loss').sum()
        wr     = wins / len(sub) * 100
        gw = sub[sub['outcome']=='win']['sl_pts'].sum()
        gl = sub[sub['outcome']=='loss']['sl_pts'].sum()
        pf = gw / gl if gl > 0 else float('inf')
        net = gw - gl
        removed = total_rev - len(sub)
        print(f"  {thresh:>12}  {len(sub):>6}  {wr:>6.1f}  {pf:>6.3f}  {net:>8.0f}  {removed:>8}")

    print()

if __name__ == '__main__':
    run()
