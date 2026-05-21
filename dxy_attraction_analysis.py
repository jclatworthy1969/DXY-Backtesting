"""
DXY Zone Strategy — Attraction Opportunity Analysis
====================================================
Answers: how many times does price actually return to the far side of the zone
during the London session, regardless of whether a candle signal fired?

For every day a zone is formed this script asks:
  - Is price on the correct (non-japan-bull) side at any point in the session?
  - Does it eventually reach the far side of the zone (the TP)?
  - What was the distance (pts) from the zone when price was closest before that return?
  - What was the best (closest) entry distance available that day?

This gives the TRUE attraction opportunity universe and lets us find the optimal
ATTR_MIN_PTS / ATTR_MAX_PTS filter without being limited by signal conditions.

Outputs:
  1. Per-day zone return log
  2. Win rate by approach-distance bucket (no-signal-filter version)
  3. Min-distance threshold sweep showing trades vs win rate
  4. Time-of-day breakdown of zone returns
"""

import pandas as pd
import numpy as np

CSV_PATH      = r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting\TVC_DXY, 15.csv"
ZONE_MIN_GAP  = 30
ENTRY_START_H = 7
ENTRY_START_M = 30
ENTRY_END_H   = 19
ENTRY_END_M   = 30
MONDAY_START_H = 6
JAPAN_END_H   = 6
MAX_APPROACH  = 5000   # maximum distance to even consider as "approaching" the zone (pts)

# ---------------------------------------------------------------------------

def session_flags(df):
    ts  = pd.to_datetime(df['time'], utc=True)
    h   = ts.dt.hour
    m   = ts.dt.minute
    dow = ts.dt.dayofweek
    curr_min  = h * 60 + m
    is_mon    = (dow == 0)
    start_min = np.where(is_mon, MONDAY_START_H * 60 + ENTRY_START_M,
                                 ENTRY_START_H  * 60 + ENTRY_START_M)
    end_min   = ENTRY_END_H * 60 + ENTRY_END_M
    in_sess   = (curr_min >= start_min) & (curr_min <= end_min)
    in_japan  = ((h == 23) & (m >= 45)) | ((h >= 0) & (h < JAPAN_END_H))
    is_2345   = (h == 23) & (m == 45)
    return pd.DataFrame({'in_sess': in_sess, 'in_japan': in_japan,
                         'is_2345': is_2345, 'hour': h, 'minute': m}, index=df.index)

def form_zone(df, i):
    if i < 1:
        return None, None, None
    prev_body = abs(df.at[i-1, 'close'] - df.at[i-1, 'open']) * 10000
    prior_close = df.at[i-2, 'close'] if (prev_body < 10 and i >= 2) else df.at[i-1, 'close']
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
        zone_top = max(japan_open, japan_close) + 0.001
        zone_bottom = min(japan_open, japan_close)
    return zone_top, zone_bottom, japan_bull

# ---------------------------------------------------------------------------

def run():
    print("Loading data...")
    df_raw = pd.read_csv(CSV_PATH, low_memory=False)
    df = df_raw[['time', 'open', 'high', 'low', 'close']].copy()
    df = df.sort_values('time').reset_index(drop=True)
    df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)

    sess = session_flags(df)
    df   = pd.concat([df, sess], axis=1)

    print("Scanning zones and sessions...")

    zone_top = zone_bottom = np.nan
    japan_bull = False
    zone_pristine = zone_body_clean = False
    japan_candle_cnt = 0
    n = len(df)

    # For each day we track:
    # current_day_approaches: list of (distance_pts, bar_idx, time) where price
    # was on the correct approach side within MAX_APPROACH pts of the zone
    # We record the BEST (smallest) distance seen before the zone was reached.

    days = []            # one record per zone day
    current_day = None

    for i in range(2, n):
        row = df.iloc[i]
        c, o, h, l = row['close'], row['open'], row['high'], row['low']

        # -- Zone formation --
        if row['is_2345']:
            # Save previous day if it exists
            if current_day is not None:
                days.append(current_day)

            zt, zb, jb = form_zone(df, i)
            if zt is not None:
                zone_top    = zt
                zone_bottom = zb
                japan_bull  = jb
                zone_pristine = zone_body_clean = True
                japan_candle_cnt = 0
                current_day = {
                    'zone_date':    row['time'][:10],
                    'zone_top':     round(zt, 5),
                    'zone_bottom':  round(zb, 5),
                    'zone_size':    round((zt - zb) * 10000, 1),
                    'japan_bull':   jb,
                    # did price reach the far side (TP) at any point in session?
                    'reached_far_side': False,
                    'reached_time':  None,
                    # best (closest) approach distance before reaching far side
                    'best_approach_dist': None,
                    'best_approach_time': None,
                    # first approach time
                    'first_approach_dist': None,
                    'first_approach_time': None,
                    # min distance seen in session (regardless of far side reached)
                    'min_dist_seen': None,
                    'min_dist_time': None,
                    # session bar count
                    'session_bars': 0,
                    # track approach chronology
                    '_approaches': [],   # (bar_idx, dist_pts, time)
                    '_far_side_reached': False,
                }
            else:
                current_day = None
            continue

        if current_day is None or np.isnan(zone_top):
            continue

        # -- Zone state (pristine tracking) --
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

        if not row['in_sess'] or row['in_japan']:
            continue

        current_day['session_bars'] += 1

        # -- Determine approach direction --
        # LONG attraction: japan_bull=False, price approaches zone_top from below
        # SHORT attraction: japan_bull=True,  price approaches zone_bottom from above

        if not current_day['_far_side_reached']:
            if not japan_bull:
                # Long approach: price below zone (close < zone_top)
                dist_to_tp = (zone_top - c) * 10000   # positive = below zone
                if 0 < dist_to_tp <= MAX_APPROACH:
                    current_day['_approaches'].append((i, round(dist_to_tp, 1), row['time']))
                    if current_day['min_dist_seen'] is None or dist_to_tp < current_day['min_dist_seen']:
                        current_day['min_dist_seen'] = round(dist_to_tp, 1)
                        current_day['min_dist_time'] = row['time']
                    if current_day['first_approach_dist'] is None:
                        current_day['first_approach_dist'] = round(dist_to_tp, 1)
                        current_day['first_approach_time'] = row['time']
                # Check if far side reached: price high touches or crosses zone_bottom
                # (i.e., price has passed THROUGH the entire zone to the bottom)
                if h >= zone_top:
                    # price reached zone_top — record best approach before this
                    current_day['_far_side_reached'] = True
                    current_day['reached_far_side']  = True
                    current_day['reached_time']      = row['time']
                    if current_day['_approaches']:
                        best = min(current_day['_approaches'], key=lambda x: x[1])
                        current_day['best_approach_dist'] = best[1]
                        current_day['best_approach_time'] = best[2]
            else:
                # Short approach: price above zone (close > zone_bottom)
                dist_to_tp = (c - zone_bottom) * 10000   # positive = above zone
                if 0 < dist_to_tp <= MAX_APPROACH:
                    current_day['_approaches'].append((i, round(dist_to_tp, 1), row['time']))
                    if current_day['min_dist_seen'] is None or dist_to_tp < current_day['min_dist_seen']:
                        current_day['min_dist_seen'] = round(dist_to_tp, 1)
                        current_day['min_dist_time'] = row['time']
                    if current_day['first_approach_dist'] is None:
                        current_day['first_approach_dist'] = round(dist_to_tp, 1)
                        current_day['first_approach_time'] = row['time']
                # Check if far side reached
                if l <= zone_bottom:
                    current_day['_far_side_reached'] = True
                    current_day['reached_far_side']  = True
                    current_day['reached_time']      = row['time']
                    if current_day['_approaches']:
                        best = min(current_day['_approaches'], key=lambda x: x[1])
                        current_day['best_approach_dist'] = best[1]
                        current_day['best_approach_time'] = best[2]

    if current_day is not None:
        days.append(current_day)

    # Clean up internal tracking fields
    for d in days:
        d.pop('_approaches', None)
        d.pop('_far_side_reached', None)

    ddf = pd.DataFrame(days)
    print(f"Total zone days found: {len(ddf)}")

    # Filter to days that had any session activity
    active = ddf[ddf['session_bars'] > 0].copy()
    returned  = active[active['reached_far_side'] == True]
    no_return = active[active['reached_far_side'] == False]

    # -----------------------------------------------------------------------
    # 1. Summary
    # -----------------------------------------------------------------------
    print("\n" + "="*70)
    print("  ATTRACTION OPPORTUNITY SUMMARY (all London session days)")
    print("="*70)
    print(f"  Total zone days with session activity : {len(active)}")
    print(f"  Days price reached far side of zone  : {len(returned)}  ({len(returned)/len(active)*100:.1f}%)")
    print(f"  Days price did NOT reach far side     : {len(no_return)}  ({len(no_return)/len(active)*100:.1f}%)")
    print(f"\n  Of days that DID return:")
    has_app = returned[returned['best_approach_dist'].notna()]
    no_app  = returned[returned['best_approach_dist'].isna()]
    print(f"    With a measurable approach (price came within {MAX_APPROACH} pts): {len(has_app)}")
    print(f"    Reached zone without coming within {MAX_APPROACH} pts first      : {len(no_app)}")

    # -----------------------------------------------------------------------
    # 2. Per-day log
    # -----------------------------------------------------------------------
    print("\n" + "="*90)
    print("  PER-DAY ZONE RETURN LOG")
    print("="*90)
    print(f"  {'Date':>12}  {'JBull':>5}  {'ZSize':>6}  {'MinDist':>8}  {'BestAppr':>9}  {'Returned':>9}  {'ReachTime':>22}")
    print(f"  {'-'*12}  {'-'*5}  {'-'*6}  {'-'*8}  {'-'*9}  {'-'*9}  {'-'*22}")
    for _, r in active.iterrows():
        ret_str  = 'YES' if r['reached_far_side'] else 'NO'
        min_d    = f"{r['min_dist_seen']:.0f}" if pd.notna(r['min_dist_seen']) else '-'
        best_a   = f"{r['best_approach_dist']:.0f}" if pd.notna(r['best_approach_dist']) else '-'
        reach_t  = str(r['reached_time'])[:22] if r['reached_time'] else '-'
        print(f"  {r['zone_date']:>12}  {str(r['japan_bull']):>5}  {r['zone_size']:>6.0f}  "
              f"{min_d:>8}  {best_a:>9}  {ret_str:>9}  {reach_t:>22}")

    # -----------------------------------------------------------------------
    # 3. Win rate by best approach distance bucket
    #    "Win" = price eventually reached the far side of the zone
    # -----------------------------------------------------------------------
    def bucket_wr(data, col, buckets, label):
        print(f"\n{'='*65}")
        print(f"  ZONE RETURN RATE BY {label}")
        print(f"{'='*65}")
        print(f"  {'Bucket (pts)':>14}  {'Days':>5}  {'Returns':>8}  {'Return%':>8}  {'No-Ret':>7}")
        print(f"  {'-'*14}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*7}")
        for lo, hi in buckets:
            sub = data[(data[col] >= lo) & (data[col] < hi) & data[col].notna()]
            if len(sub) == 0:
                continue
            wins = sub['reached_far_side'].sum()
            wr   = wins / len(sub) * 100
            print(f"  {lo:>6}-{hi:<6}    {len(sub):>5}  {wins:>8}  {wr:>8.1f}%  {len(sub)-wins:>7}")

    # Use best_approach_dist (smallest distance seen BEFORE the return)
    buckets = [(0,100),(100,200),(200,300),(300,500),(500,750),(750,1000),
               (1000,1500),(1500,2000),(2000,3000),(3000,5000)]
    bucket_wr(active[active['best_approach_dist'].notna()],
              'best_approach_dist', buckets, 'BEST APPROACH DISTANCE (before zone reached)')

    # Also do min_dist_seen (includes non-returning days)
    bucket_wr(active[active['min_dist_seen'].notna()],
              'min_dist_seen', buckets, 'MINIMUM DISTANCE SEEN (all days, including non-returns)')

    # -----------------------------------------------------------------------
    # 4. Threshold sweep: if we only took trades where price came within X pts,
    #    what is our universe size and return rate?
    # -----------------------------------------------------------------------
    print(f"\n{'='*65}")
    print(f"  ATTR_MAX_PTS THRESHOLD SWEEP")
    print(f"  (days where price came within X pts of zone at some point)")
    print(f"{'='*65}")
    print(f"  {'Max pts':>8}  {'Days':>5}  {'Returns':>8}  {'Return%':>9}  {'Missed':>7}")
    print(f"  {'-'*8}  {'-'*5}  {'-'*8}  {'-'*9}  {'-'*7}")
    has_min = active[active['min_dist_seen'].notna()]
    for thresh in [100, 200, 300, 400, 500, 600, 750, 1000, 1500, 2000, 3000, 5000]:
        sub  = has_min[has_min['min_dist_seen'] <= thresh]
        if len(sub) == 0:
            continue
        wins = sub['reached_far_side'].sum()
        wr   = wins / len(sub) * 100
        missed = len(returned) - wins
        print(f"  {thresh:>8}  {len(sub):>5}  {wins:>8}  {wr:>9.1f}%  {missed:>7}")

    # -----------------------------------------------------------------------
    # 5. Min-distance filter sweep (ATTR_MIN_PTS equivalent)
    #    Only take trades where price came AT LEAST X pts away (min quality bar)
    # -----------------------------------------------------------------------
    print(f"\n{'='*65}")
    print(f"  ATTR_MIN_PTS THRESHOLD SWEEP")
    print(f"  (only days where closest approach was >= X pts — avoids overextended entries)")
    print(f"{'='*65}")
    print(f"  {'Min pts':>8}  {'Days':>5}  {'Returns':>8}  {'Return%':>9}  {'Excluded':>9}")
    print(f"  {'-'*8}  {'-'*5}  {'-'*8}  {'-'*9}  {'-'*9}")
    for thresh in [0, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000]:
        sub  = has_min[has_min['min_dist_seen'] >= thresh]
        if len(sub) == 0:
            continue
        wins = sub['reached_far_side'].sum()
        wr   = wins / len(sub) * 100
        excluded = len(has_min) - len(sub)
        print(f"  {thresh:>8}  {len(sub):>5}  {wins:>8}  {wr:>9.1f}%  {excluded:>9}")

    # -----------------------------------------------------------------------
    # 6. Combined min+max sweep (optimal window)
    # -----------------------------------------------------------------------
    print(f"\n{'='*75}")
    print(f"  COMBINED MIN/MAX WINDOW SWEEP (best entry distance band)")
    print(f"{'='*75}")
    print(f"  {'Min':>5}  {'Max':>5}  {'Days':>5}  {'Returns':>8}  {'Return%':>9}")
    print(f"  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*8}  {'-'*9}")
    min_vals = [0, 50, 100, 150, 200]
    max_vals = [300, 400, 500, 750, 1000, 1500, 2000]
    best_pct = 0
    best_combo = None
    for mn in min_vals:
        for mx in max_vals:
            if mn >= mx:
                continue
            sub  = has_min[(has_min['min_dist_seen'] >= mn) & (has_min['min_dist_seen'] <= mx)]
            if len(sub) < 3:
                continue
            wins = sub['reached_far_side'].sum()
            wr   = wins / len(sub) * 100
            if wr > best_pct:
                best_pct = wr
                best_combo = (mn, mx, len(sub), wins, wr)
            print(f"  {mn:>5}  {mx:>5}  {len(sub):>5}  {wins:>8}  {wr:>9.1f}%")

    if best_combo:
        print(f"\n  ** Best combo: min={best_combo[0]}, max={best_combo[1]} -> "
              f"{best_combo[2]} days, {best_combo[3]} returns, {best_combo[4]:.1f}% return rate **")

    # -----------------------------------------------------------------------
    # 7. Time-of-day: when do successful zone returns happen?
    # -----------------------------------------------------------------------
    print(f"\n{'='*65}")
    print(f"  TIME OF DAY — WHEN DO ZONE RETURNS COMPLETE?")
    print(f"{'='*65}")
    ret_with_time = returned[returned['reached_time'].notna()].copy()
    ret_with_time['reach_hour'] = ret_with_time['reached_time'].str[11:13].astype(int)
    hour_counts = ret_with_time.groupby('reach_hour').size()
    print(f"  {'Hour (UTC)':>12}  {'Returns':>8}")
    print(f"  {'-'*12}  {'-'*8}")
    for hr, cnt in hour_counts.items():
        bar = '#' * cnt
        print(f"  {hr:>10}:00  {cnt:>8}   {bar}")

    print()

if __name__ == '__main__':
    run()
