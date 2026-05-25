"""
analyse_attr_wave.py
====================
Deep-dive analysis of DXY attraction trade setups.

For every day where a Tokyo-open zone forms and price is >=150 pts away from
the near zone edge at London open (pristine setup), measures the characteristics
of the subsequent wave and asks: what separates setups where price eventually
fills the zone (SUCCESS) from those where it doesn't (FAIL)?

Wave = bars from London open to the maximum excursion point (furthest from zone)
       within the trading session (ends 19:30 UTC).

Outcome = did price reach the zone FAR edge (zone_top for LONG, zone_bot for SHORT)
          at any point between London open and 19:30 UTC?

Metrics per setup:
  WAVE GEOMETRY
    wave_bars           bars from London open to max excursion
    wave_pts            price distance from London open to max excursion (pts)
    pts_per_bar         wave speed  (pts / bar)
    slope               linear regression slope over wave closes (pts/bar)
    r2                  R^2 of regression  (1.0 = perfectly linear, clean impulse)
    avg_body_pts        average candle body during wave (pts)
    avg_range_pts       average candle range during wave (pts)
    body_ratio          avg_body / avg_range  (decisiveness)
    pct_directional     % of wave bars closing in wave direction

  INDICATORS AT MAX EXCURSION (wave peak/trough)
    rsi_at_peak         RSI(14) at max excursion bar
    bb_pctb_at_peak     %B Bollinger position  (<0 = beyond lower band for LONG)
    bb_width_pts        BB bandwidth (upper-lower) in pts at peak
    macd_hist_at_peak   MACD histogram value
    ema20_dist_pts      price distance from EMA20 (pts, signed: below=negative for LONG)
    atr_mult            how many ATR(14)s price is from EMA20 (extension measure)

  DIVERGENCE (peak vs London open bar)
    rsi_divergence      price made new extreme but RSI didn't (True = divergent)
    macd_divergence     price made new extreme but MACD histogram didn't

  SETUP CONTEXT
    gap_pts_at_lon      distance from zone near edge at London open (pts)
    max_excursion_pts   max distance from zone near edge during session (pts)
    zone_width_pts      width of the zone (pts)
    pullback_pts        distance price retraced from peak before session end (pts)
    session_close_pts   distance from zone near edge at 19:30 close (pts)

  OUTCOME
    reached_far_edge    True/False — did price reach zone far edge during session?
    max_progress_pct    how far (%) price got toward far edge from London open close
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats as scipy_stats
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
import dxy_clean_rules as r

BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")

# -- Parameters ----------------------------------------------------------------
MIN_GAP_PTS    = 150     # minimum pristine gap at London open
BB_PERIOD      = 20
BB_STD         = 2.0
RSI_PERIOD     = 14
MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIGNAL    = 9
ATR_PERIOD     = 14
EMA_FAST       = 20
EMA_SLOW       = 50
SESSION_END    = 19 * 60 + 30   # 19:30 UTC in minutes


# -- Technical indicators ------------------------------------------------------
def compute_indicators(df):
    c = df['close'].copy()
    h = df['high'].copy()
    l = df['low'].copy()

    # BB
    bb_mid   = c.ewm(span=BB_PERIOD, adjust=False).mean()
    bb_std   = c.rolling(BB_PERIOD).std()
    bb_upper = bb_mid + BB_STD * bb_std
    bb_lower = bb_mid - BB_STD * bb_std
    bb_width = bb_upper - bb_lower
    bb_pctb  = (c - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - 100 / (1 + rs)

    # MACD
    ema_f    = c.ewm(span=MACD_FAST,   adjust=False).mean()
    ema_s    = c.ewm(span=MACD_SLOW,   adjust=False).mean()
    macd     = ema_f - ema_s
    signal   = macd.ewm(span=MACD_SIGNAL, adjust=False).mean()
    macd_h   = macd - signal

    # ATR
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    # EMAs
    ema20 = c.ewm(span=EMA_FAST, adjust=False).mean()
    ema50 = c.ewm(span=EMA_SLOW, adjust=False).mean()

    return pd.DataFrame({
        'bb_mid': bb_mid, 'bb_upper': bb_upper, 'bb_lower': bb_lower,
        'bb_width': bb_width, 'bb_pctb': bb_pctb,
        'rsi': rsi, 'macd_h': macd_h,
        'atr': atr, 'ema20': ema20, 'ema50': ema50,
    }, index=df.index)


# -- Wave geometry helpers -----------------------------------------------------
def wave_stats(closes, direction):
    """
    Compute geometry of a price wave.
    direction: +1 = wave goes DOWN (LONG setup, price moves further below zone)
               -1 = wave goes UP   (SHORT setup, price moves further above zone)
    """
    n = len(closes)
    if n < 2:
        return dict(wave_bars=0, slope_pts_bar=0, r2=0)

    arr = np.array(closes)
    x   = np.arange(n, dtype=float)
    slope, intercept, rv, pv, se = scipy_stats.linregress(x, arr)
    # slope in price units per bar; convert to pts
    slope_pts = slope * 10000

    # R^2 — 1.0 = perfectly linear (clean impulse), 0 = random
    r2 = rv ** 2

    return dict(
        wave_bars       = n,
        slope_pts_bar   = round(slope_pts, 2),
        r2              = round(r2, 3),
    )


def candle_stats(df_slice, direction):
    """Average candle body / range during the wave. direction: +1=down, -1=up."""
    body  = (df_slice['close'] - df_slice['open']).abs() * 10000
    rng   = (df_slice['high'] - df_slice['low']) * 10000
    # directional: for LONG wave (price going down) we want bear bars
    if direction == 1:   # wave DOWN → directional = close < open
        pct_dir = ((df_slice['close'] < df_slice['open']).sum() / max(len(df_slice), 1))
    else:                # wave UP   → directional = close > open
        pct_dir = ((df_slice['close'] > df_slice['open']).sum() / max(len(df_slice), 1))

    avg_body  = body.mean()
    avg_range = rng.mean()
    return dict(
        avg_body_pts    = round(avg_body, 1),
        avg_range_pts   = round(avg_range, 1),
        body_ratio      = round(avg_body / avg_range if avg_range > 0 else 0, 3),
        pct_directional = round(pct_dir * 100, 1),
    )


# -- Main scanner --------------------------------------------------------------
def scan_setups(df, ind):
    """
    Scan all pristine setups and compute wave characteristics + outcome.
    Returns list of dicts (one per setup).
    """
    zone_top     = np.nan
    zone_bot     = np.nan
    japan_bull   = False
    strict_prist = True

    setups = []
    n = len(df)

    for i in range(2, n):
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']
        ts   = row['time']
        hour = ts.hour
        minute = ts.minute
        curr_min = hour * 60 + minute
        dow  = ts.dayofweek
        in_japan = (hour == 23 and minute >= 45) or (0 <= hour < 6)

        # Zone formation
        if hour == 23 and minute == 45:
            zt, zb, jb = r.form_zone(df, i)
            if zt is not None:
                zone_top, zone_bot = zt, zb
                japan_bull   = jb
                strict_prist = True
            continue

        if np.isnan(zone_top):
            continue

        # Track strict pristine
        if strict_prist and (l <= zone_top) and (h >= zone_bot):
            strict_prist = False

        # London open bar
        is_lon = (not in_japan and hour == 7 and minute == 0 and dow != 0)
        is_mon = (not in_japan and hour == 6 and minute == 30 and dow == 0)

        if not (is_lon or is_mon):
            continue

        # Check pristine condition
        if not japan_bull:
            gap = (zone_bot - c) * 10000      # LONG: price below zone
            direction = 1                      # wave goes DOWN (further from zone)
            near_edge = zone_bot
            far_edge  = zone_top
        else:
            gap = (c - zone_top) * 10000       # SHORT: price above zone
            direction = -1                     # wave goes UP (further from zone)
            near_edge = zone_top
            far_edge  = zone_bot

        if gap < MIN_GAP_PTS:
            continue

        zone_width_pts = (zone_top - zone_bot) * 10000

        # -- Gather bars from London open to session end (19:30) ---------------
        session_bars = []
        j = i
        while j < n:
            rj   = df.iloc[j]
            tsj  = rj['time']
            cmin = tsj.hour * 60 + tsj.minute
            dow_j = tsj.dayofweek
            # Stop at session end or next day
            if tsj.date() != ts.date():
                break
            if cmin > SESSION_END:
                break
            session_bars.append(j)
            j += 1

        if len(session_bars) < 3:
            continue

        closes  = df['close'].iloc[session_bars].values
        highs   = df['high'].iloc[session_bars].values
        lows    = df['low'].iloc[session_bars].values

        # -- Find max excursion (furthest from zone) ---------------------------
        if direction == 1:    # LONG: look for minimum close (furthest below zone)
            excursion_vals = (near_edge - closes) * 10000   # positive = further from zone
        else:                 # SHORT: look for maximum close (furthest above zone)
            excursion_vals = (closes - near_edge) * 10000

        peak_idx  = int(np.argmax(excursion_vals))   # bar index within session_bars
        peak_bar  = session_bars[peak_idx]
        wave_bars = peak_idx + 1                     # bars from London open to peak (inclusive)
        wave_pts  = float(excursion_vals[peak_idx])  # distance from near edge at peak
        # Net wave move AFTER London open (how much further price went)
        wave_extension_pts = float(excursion_vals[peak_idx] - excursion_vals[0])

        # -- Wave geometry (London open bar to peak) ---------------------------
        wave_slice_idx = session_bars[:peak_idx + 1]
        wave_closes    = df['close'].iloc[wave_slice_idx].values
        pts_per_bar    = wave_extension_pts / max(peak_idx, 1)
        wstats         = wave_stats(wave_closes, direction)
        cstats         = candle_stats(df.iloc[wave_slice_idx], direction)

        # -- Indicators at peak bar --------------------------------------------
        rsi_peak     = float(ind.at[peak_bar, 'rsi'])
        bb_pctb_peak = float(ind.at[peak_bar, 'bb_pctb'])
        bb_width_pk  = float(ind.at[peak_bar, 'bb_width']) * 10000
        macd_h_peak  = float(ind.at[peak_bar, 'macd_h']) * 10000
        ema20_pk     = float(ind.at[peak_bar, 'ema20'])
        atr_pk       = float(ind.at[peak_bar, 'atr'])
        peak_close   = float(df.at[peak_bar, 'close'])

        if direction == 1:   # LONG, price below ema20
            ema20_dist_pts = (peak_close - ema20_pk) * 10000  # negative = below ema20
        else:
            ema20_dist_pts = (peak_close - ema20_pk) * 10000  # positive = above ema20

        atr_mult = abs(ema20_dist_pts) / (atr_pk * 10000) if atr_pk > 0 else 0

        # -- Indicators at London open bar -------------------------------------
        rsi_lon    = float(ind.at[i, 'rsi'])
        macd_h_lon = float(ind.at[i, 'macd_h']) * 10000

        # -- Divergence (peak vs London open) ---------------------------------
        # Bullish divergence (LONG): price lower at peak than at London open,
        # but RSI is higher → momentum not confirming new low
        if direction == 1:
            price_new_extreme = excursion_vals[peak_idx] > excursion_vals[0]
            rsi_div  = price_new_extreme and (rsi_peak > rsi_lon)
            macd_div = price_new_extreme and (macd_h_peak > macd_h_lon)
        else:
            price_new_extreme = excursion_vals[peak_idx] > excursion_vals[0]
            rsi_div  = price_new_extreme and (rsi_peak < rsi_lon)
            macd_div = price_new_extreme and (macd_h_peak < macd_h_lon)

        # -- Outcome: did price reach far edge before 19:30? -------------------
        if direction == 1:    # LONG: need any high >= far_edge (zone_top)
            reached = bool(np.any(highs >= far_edge))
            # For LONG we also check progress: max high vs total distance needed
            total_distance = (far_edge - (near_edge - gap / 10000)) * 10000
            max_high = float(np.max(highs))
            # Progress from London open close toward far edge
            dist_needed = (far_edge - c) * 10000
            dist_achieved = max((max_high - c) * 10000, 0)
        else:                 # SHORT: need any low <= far_edge (zone_bot)
            reached = bool(np.any(lows <= far_edge))
            total_distance = ((near_edge + gap / 10000) - far_edge) * 10000
            max_low = float(np.min(lows))
            dist_needed  = (c - far_edge) * 10000
            dist_achieved = max((c - max_low) * 10000, 0)

        max_progress_pct = round(dist_achieved / dist_needed * 100
                                 if dist_needed > 0 else 0, 1)

        # Pullback from peak to session end
        last_close = float(df.at[session_bars[-1], 'close'])
        if direction == 1:
            pullback_pts = (last_close - peak_close) * 10000  # positive = retraced up
        else:
            pullback_pts = (peak_close - last_close) * 10000

        # -- BB regime during wave ---------------------------------------------
        bb_widths_wave = ind['bb_width'].iloc[wave_slice_idx].values * 10000
        bb_expanding   = bool(bb_widths_wave[-1] > bb_widths_wave[0]) if len(bb_widths_wave) > 1 else False
        bb_width_change_pct = ((bb_widths_wave[-1] - bb_widths_wave[0]) /
                               bb_widths_wave[0] * 100) if bb_widths_wave[0] > 0 else 0

        setups.append({
            # Context
            'date'               : str(ts.date()),
            'direction'          : 'LONG' if direction == 1 else 'SHORT',
            'gap_pts_at_lon'     : round(gap, 1),
            'zone_width_pts'     : round(zone_width_pts, 1),
            # Wave geometry
            'wave_bars'          : wave_bars,
            'wave_extension_pts' : round(wave_extension_pts, 1),
            'wave_total_pts'     : round(wave_pts, 1),
            'pts_per_bar'        : round(pts_per_bar, 1),
            'slope_pts_bar'      : wstats['slope_pts_bar'],
            'r2'                 : wstats['r2'],
            'avg_body_pts'       : cstats['avg_body_pts'],
            'avg_range_pts'      : cstats['avg_range_pts'],
            'body_ratio'         : cstats['body_ratio'],
            'pct_directional'    : cstats['pct_directional'],
            # Indicators at peak
            'rsi_at_peak'        : round(rsi_peak, 1),
            'bb_pctb_at_peak'    : round(bb_pctb_peak, 3),
            'bb_width_pts_peak'  : round(bb_width_pk, 1),
            'bb_expanding'       : bb_expanding,
            'bb_width_chg_pct'   : round(bb_width_change_pct, 1),
            'macd_hist_peak'     : round(macd_h_peak, 2),
            'ema20_dist_pts'     : round(ema20_dist_pts, 1),
            'atr_mult'           : round(atr_mult, 2),
            # Divergence
            'rsi_divergence'     : rsi_div,
            'macd_divergence'    : macd_div,
            # Outcome
            'reached_far_edge'   : reached,
            'max_progress_pct'   : max_progress_pct,
            'pullback_pts'       : round(pullback_pts, 1),
        })

    return setups


# -- Reporting -----------------------------------------------------------------
def report(df_setups):
    suc = df_setups[df_setups['reached_far_edge']]
    fal = df_setups[~df_setups['reached_far_edge']]
    n_tot = len(df_setups)
    n_suc = len(suc)
    n_fal = len(fal)

    print()
    print("=" * 78)
    print("  DXY ATTRACTION WAVE ANALYSIS")
    print(f"  {n_tot} pristine setups  |  {n_suc} SUCCESS ({n_suc/n_tot*100:.1f}%)  "
          f"|  {n_fal} FAIL ({n_fal/n_tot*100:.1f}%)")
    print("=" * 78)

    # Split LONG / SHORT
    for direction in ['LONG', 'SHORT', 'ALL']:
        if direction == 'ALL':
            sub = df_setups
            s   = suc
            f   = fal
        else:
            sub = df_setups[df_setups['direction'] == direction]
            s   = sub[sub['reached_far_edge']]
            f   = sub[~sub['reached_far_edge']]
        if len(sub) == 0:
            continue

        print()
        print(f"  -- {direction}  (N={len(sub)}, "
              f"SUCCESS={len(s)} {len(s)/len(sub)*100:.0f}%, "
              f"FAIL={len(f)} {len(f)/len(sub)*100:.0f}%) --")

        metrics = [
            ('gap_pts_at_lon',     'Gap at London open (pts)',           '>=larger = better?'),
            ('zone_width_pts',     'Zone width (pts)',                   ''),
            ('wave_extension_pts', 'Wave extension after L-open (pts)',  '>=larger impulse?'),
            ('wave_bars',          'Wave bars (London open to peak)',     '<=fewer = more impulsive?'),
            ('pts_per_bar',        'Speed: pts per bar',                 '>=faster = better?'),
            ('slope_pts_bar',      'Regression slope (pts/bar)',         '>=steeper = better?'),
            ('r2',                 'Wave linearity R^2',                  '>=cleaner = better?'),
            ('avg_body_pts',       'Avg candle body (pts)',              '>=larger = better?'),
            ('avg_range_pts',      'Avg candle range (pts)',             ''),
            ('body_ratio',         'Body/Range ratio',                   '>=more decisive?'),
            ('pct_directional',    '% directional candles',             '>=more one-sided?'),
            ('rsi_at_peak',        'RSI at wave peak',                   '<=oversold LONG / >=overbought SHORT?'),
            ('bb_pctb_at_peak',    'BB %B at wave peak',                 '<=0 = beyond lower band (LONG)?'),
            ('bb_width_pts_peak',  'BB width at peak (pts)',             ''),
            ('bb_width_chg_pct',   'BB width change during wave (%)',    ''),
            ('macd_hist_peak',     'MACD histogram at peak',             ''),
            ('ema20_dist_pts',     'Distance from EMA20 at peak (pts)',  ''),
            ('atr_mult',           'ATR multiples from EMA20 at peak',   '>=more extended?'),
        ]

        print(f"\n  {'Metric':<38}  {'SUCCESS':>10}  {'FAIL':>10}  {'Diff':>10}  {'p-val':>8}")
        print(f"  {'-'*78}")

        for col, label, note in metrics:
            if col not in sub.columns:
                continue
            sv = s[col].dropna()
            fv = f[col].dropna()
            if len(sv) == 0 or len(fv) == 0:
                continue
            sm = sv.median()
            fm = fv.median()
            diff = sm - fm
            # Mann-Whitney U test for significance
            try:
                _, pval = scipy_stats.mannwhitneyu(sv, fv, alternative='two-sided')
                sig = '***' if pval < 0.01 else ('** ' if pval < 0.05 else ('*  ' if pval < 0.1 else '   '))
                pstr = f"{pval:.3f}{sig}"
            except Exception:
                pstr = "  n/a  "
            print(f"  {label:<38}  {sm:>10.1f}  {fm:>10.1f}  {diff:>+10.1f}  {pstr:>8}")

    # -- Boolean metrics -------------------------------------------------------
    print()
    print("  -- BOOLEAN FACTORS (% of setups where True) --")
    print(f"\n  {'Factor':<30}  {'SUCCESS':>10}  {'FAIL':>10}  {'Diff':>10}")
    print(f"  {'-'*56}")
    bool_cols = ['rsi_divergence', 'macd_divergence', 'bb_expanding']
    for col in bool_cols:
        if col not in df_setups.columns:
            continue
        sp = suc[col].mean() * 100 if len(suc) > 0 else 0
        fp = fal[col].mean() * 100 if len(fal) > 0 else 0
        print(f"  {col:<30}  {sp:>9.1f}%  {fp:>9.1f}%  {sp-fp:>+9.1f}%")

    # -- RSI buckets at peak ---------------------------------------------------
    print()
    print("  -- SUCCESS RATE BY RSI AT PEAK --")
    print(f"  {'RSI bucket':<20}  {'N':>5}  {'Success':>8}  {'Rate':>8}")
    print(f"  {'-'*46}")
    bins = [(0,20,'<20 (extreme OS)'), (20,30,'20-30 (OS)'),
            (30,40,'30-40'), (40,60,'40-60 (neutral)'),
            (60,70,'60-70'), (70,80,'70-80 (OB)'), (80,100,'>80 (extreme OB)')]
    for lo, hi, label in bins:
        mask = (df_setups['rsi_at_peak'] >= lo) & (df_setups['rsi_at_peak'] < hi)
        sub_b = df_setups[mask]
        if len(sub_b) == 0:
            continue
        rate = sub_b['reached_far_edge'].mean() * 100
        print(f"  {label:<20}  {len(sub_b):>5}  {sub_b['reached_far_edge'].sum():>8}  {rate:>7.1f}%")

    # -- BB %B buckets at peak ------------------------------------------------
    print()
    print("  -- SUCCESS RATE BY BB %B AT PEAK --")
    print(f"  {'%B bucket':<25}  {'N':>5}  {'Success':>8}  {'Rate':>8}")
    print(f"  {'-'*50}")
    bb_bins = [(-999,-0.1,'< 0  (beyond band)'), (-0.1,0.2,'0-0.2 (near band)'),
               (0.2,0.5,'0.2-0.5'), (0.5,0.8,'0.5-0.8'), (0.8,1.0,'0.8-1.0'),
               (1.0,999,'> 1.0 (beyond band)')]
    for lo, hi, label in bb_bins:
        mask = (df_setups['bb_pctb_at_peak'] >= lo) & (df_setups['bb_pctb_at_peak'] < hi)
        sub_b = df_setups[mask]
        if len(sub_b) == 0:
            continue
        rate = sub_b['reached_far_edge'].mean() * 100
        print(f"  {label:<25}  {len(sub_b):>5}  {sub_b['reached_far_edge'].sum():>8}  {rate:>7.1f}%")

    # -- Speed buckets ---------------------------------------------------------
    print()
    print("  -- SUCCESS RATE BY WAVE SPEED (pts/bar) --")
    print(f"  {'Speed bucket':<25}  {'N':>5}  {'Success':>8}  {'Rate':>8}")
    print(f"  {'-'*50}")
    speed_bins = [(-999,0,'<=0 (no extension)'), (0,10,'0-10'), (10,25,'10-25'),
                  (25,50,'25-50'), (50,100,'50-100'), (100,999,'>100')]
    for lo, hi, label in speed_bins:
        mask = (df_setups['pts_per_bar'] > lo) & (df_setups['pts_per_bar'] <= hi)
        sub_b = df_setups[mask]
        if len(sub_b) == 0:
            continue
        rate = sub_b['reached_far_edge'].mean() * 100
        print(f"  {label:<25}  {len(sub_b):>5}  {sub_b['reached_far_edge'].sum():>8}  {rate:>7.1f}%")

    # -- ATR multiple buckets --------------------------------------------------
    print()
    print("  -- SUCCESS RATE BY ATR MULTIPLES FROM EMA20 AT PEAK --")
    print(f"  {'ATR mult':<20}  {'N':>5}  {'Success':>8}  {'Rate':>8}")
    print(f"  {'-'*46}")
    atr_bins = [(0,0.5,'0-0.5x ATR'), (0.5,1,'0.5-1x ATR'), (1,1.5,'1-1.5x ATR'),
                (1.5,2,'1.5-2x ATR'), (2,3,'2-3x ATR'), (3,999,'>3x ATR')]
    for lo, hi, label in atr_bins:
        mask = (df_setups['atr_mult'] >= lo) & (df_setups['atr_mult'] < hi)
        sub_b = df_setups[mask]
        if len(sub_b) == 0:
            continue
        rate = sub_b['reached_far_edge'].mean() * 100
        print(f"  {label:<20}  {len(sub_b):>5}  {sub_b['reached_far_edge'].sum():>8}  {rate:>7.1f}%")

    # -- Correlation with success -----------------------------------------------
    print()
    print("  -- CORRELATION WITH SUCCESS (point-biserial, top factors) --")
    numeric_cols = ['gap_pts_at_lon', 'wave_extension_pts', 'wave_bars', 'pts_per_bar',
                    'slope_pts_bar', 'r2', 'avg_body_pts', 'body_ratio', 'pct_directional',
                    'rsi_at_peak', 'bb_pctb_at_peak', 'bb_width_pts_peak',
                    'bb_width_chg_pct', 'macd_hist_peak', 'ema20_dist_pts', 'atr_mult']
    corrs = []
    y = df_setups['reached_far_edge'].astype(int)
    for col in numeric_cols:
        if col not in df_setups.columns:
            continue
        x = df_setups[col].fillna(df_setups[col].median())
        try:
            corr, pv = scipy_stats.pointbiserialr(y, x)
            corrs.append((col, round(corr, 3), round(pv, 4)))
        except Exception:
            pass
    corrs.sort(key=lambda x: abs(x[1]), reverse=True)
    print(f"\n  {'Feature':<30}  {'Correlation':>12}  {'p-value':>10}  {'Direction'}")
    print(f"  {'-'*65}")
    for col, corr, pv in corrs[:15]:
        sig = '***' if pv < 0.01 else ('** ' if pv < 0.05 else ('*  ' if pv < 0.1 else '   '))
        direction_note = 'positive = helps' if corr > 0 else 'negative = helps'
        print(f"  {col:<30}  {corr:>12.3f}  {pv:>10.4f}{sig}  {direction_note}")


# -- Main ----------------------------------------------------------------------
def main():
    print("Loading DXY data and computing indicators...")
    df = pd.read_csv(BASE / 'TVC_DXY, 15_merged.csv')
    df['time'] = pd.to_datetime(df['time'], utc=True)
    df = df.sort_values('time').reset_index(drop=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)

    print(f"  {len(df):,} bars  |  {df['time'].min().date()} to {df['time'].max().date()}")

    print("Computing BB / RSI / MACD / ATR / EMA indicators...")
    ind = compute_indicators(df)

    print("Scanning pristine setups and computing wave characteristics...")
    setups = scan_setups(df, ind)
    df_s   = pd.DataFrame(setups)

    print(f"  Found {len(df_s)} pristine setups (>={MIN_GAP_PTS} pts gap at London open)")

    report(df_s)

    # Save full detail
    out_path = BASE / 'attr_wave_analysis.csv'
    df_s.to_csv(out_path, index=False)
    print(f"\n  Full dataset saved: attr_wave_analysis.csv  ({len(df_s)} rows)")


if __name__ == '__main__':
    main()
