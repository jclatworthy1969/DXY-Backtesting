import pickle, json, datetime, os
from collections import Counter

# ── 1. MERGE ALL CHUNKS ──────────────────────────────────────────────────────
chunk_files = [
    'chunk_may2024.pkl','chunk_jun2024a.pkl','chunk_jun2024b.pkl',
    'chunk_jul2024a.pkl','chunk_jul2024b.pkl','chunk_aug2024a.pkl',
    'chunk_aug2024b.pkl','chunk_sep2024a.pkl','chunk_sep2024b.pkl',
    'chunk_oct2024a.pkl','chunk_oct2024b.pkl','chunk_nov2024a.pkl',
    'chunk_nov2024b.pkl','chunk_oct2024c.pkl','chunk_oct2024d.pkl',
    'chunk_oct2024e.pkl','chunk_oct2024f.pkl','chunk_nov2024c.pkl',
    'chunk_nov2024d.pkl','chunk_nov2024e.pkl','chunk_nov2024f.pkl',
    'chunk_dec2024a.pkl','chunk_dec2024_gap.pkl','chunk_dec2024b.pkl',
    'chunk_dec2024.pkl',
]

all_bars = {}
for fname in chunk_files:
    if not os.path.exists(fname):
        print(f"MISSING: {fname}")
        continue
    with open(fname, 'rb') as f:
        bars = pickle.load(f)
    for b in bars:
        all_bars[b['time']] = b

bars = sorted(all_bars.values(), key=lambda x: x['time'])
print(f"Total unique bars: {len(bars)}")
first = datetime.datetime.fromtimestamp(bars[0]['time'], datetime.UTC)
last  = datetime.datetime.fromtimestamp(bars[-1]['time'], datetime.UTC)
print(f"Range: {first} to {last}")

# ── 2. ZONE RECONSTRUCTION ───────────────────────────────────────────────────
zones = []

zone_top = zone_bottom = None
japan_bull = False
pristine = False
body_clean = False
candle_count = 0
zone_date = None

for i, bar in enumerate(bars):
    dt = datetime.datetime.fromtimestamp(bar['time'], datetime.UTC)
    h, m = dt.hour, dt.minute

    if h == 23 and m == 45:
        if zone_top is not None:
            zones.append({
                'zone_date':   str(zone_date),
                'zone_top':    zone_top,
                'zone_bottom': zone_bottom,
                'japan_bull':  japan_bull,
                'pristine':    pristine,
                'body_clean':  body_clean,
            })
        # If the immediately prior candle is a line candle (body < 10 pts),
        # skip it and use the candle before that — handles Monday flat opens.
        if i > 1:
            prior_body = abs(bars[i-1]['close'] - bars[i-1]['open']) * 10000
            prior_is_line = prior_body < 10
            prev_close = bars[i-2]['close'] if prior_is_line else bars[i-1]['close']
        elif i > 0:
            prev_close = bars[i-1]['close']
        else:
            prev_close = bar['open']
        zone_top    = max(prev_close, bar['open'])
        zone_bottom = min(prev_close, bar['open'])
        japan_bull  = bar['open'] > prev_close
        pristine    = True
        body_clean  = True
        candle_count = 0
        zone_date   = (dt + datetime.timedelta(days=1)).date()
        continue

    # Japan session: after 23:45 and before 06:00
    in_japan = (h == 23 and m > 45) or (0 <= h < 6)

    if zone_top is not None and in_japan:
        candle_count += 1

        if body_clean and candle_count > 3:
            close_in_zone = (bar['close'] >= zone_bottom and bar['close'] <= zone_top)
            if close_in_zone:
                body_clean = False

        if pristine:
            if japan_bull:
                if bar['close'] < zone_bottom:
                    pristine = False
            else:
                if bar['close'] > zone_top:
                    pristine = False

if zone_top is not None:
    zones.append({
        'zone_date':   str(zone_date),
        'zone_top':    zone_top,
        'zone_bottom': zone_bottom,
        'japan_bull':  japan_bull,
        'pristine':    pristine,
        'body_clean':  body_clean,
    })

print(f"\nZones reconstructed: {len(zones)}")
zone_map = {z['zone_date']: z for z in zones}

# ── 3. LOAD TRADES ───────────────────────────────────────────────────────────
with open('last50_winning_trades.json') as f:
    trades = json.load(f)
print(f"Trades to match: {len(trades)}")

# ── 4. MATCH TRADES ──────────────────────────────────────────────────────────
ATTR_MIN = 1500
ATTR_MAX = 5000

results = []
for t in trades:
    trade_date = t['date']
    entry_px   = t['entry_px']
    direction  = t['direction']
    entry_h    = t['entry_hour']
    entry_m    = t['entry_min']

    zone = zone_map.get(trade_date)
    if not zone:
        results.append({**t, 'zone_found': False, 'match': 'MANUAL_CHECK'})
        continue

    zt = zone['zone_top']
    zb = zone['zone_bottom']
    jb = zone['japan_bull']
    pr = zone['pristine']
    bc = zone['body_clean']

    # Distance measured to the FAR side of zone (= TP target)
    dist_to_tp_long  = (zt - entry_px) * 10000   # long entry below zone, TP = zone_top
    dist_to_tp_short = (entry_px - zb) * 10000   # short entry above zone, TP = zone_bottom
    # Keep near-edge distances for labels only
    dist_above = (entry_px - zt) * 10000
    dist_below = (zb - entry_px) * 10000

    curr_min = entry_h * 60 + entry_m
    in_sess  = (6*60+45) <= curr_min <= (19*60+30)

    attr_long  = bc and not jb and (dist_to_tp_long  >= ATTR_MIN) and (dist_to_tp_long  <= ATTR_MAX)
    attr_short = bc and jb     and (dist_to_tp_short >= ATTR_MIN) and (dist_to_tp_short <= ATTR_MAX)
    # Reversal: broken zone only, no distance gate, signal-driven direction
    rev_long   = (not pr) and in_sess
    rev_short  = (not pr) and in_sess

    if direction == 'LONG':
        attr_match = attr_long and in_sess
        rev_match  = rev_long
        dist_pts   = round(dist_below)
        dist_label = f"{dist_pts} pts below zone"
    else:
        attr_match = attr_short and in_sess
        rev_match  = rev_short
        dist_pts   = round(dist_above)
        dist_label = f"{dist_pts} pts above zone"

    if attr_match:
        match = 'ATTRACTION'
    elif rev_match:
        match = 'REVERSAL'
    elif not in_sess:
        match = 'OUT_OF_SESSION'
    elif not bc and not pr:
        # Broken zone but reversal distance not met
        if direction == 'LONG' and rev_dist_long < ATTR_MIN:
            match = 'REV_TOO_CLOSE_TO_ZONE_TOP'
        elif direction == 'SHORT' and rev_dist_short < ATTR_MIN:
            match = 'REV_TOO_CLOSE_TO_ZONE_BOTTOM'
        else:
            match = 'DIRECTION_MISMATCH_BROKEN'
    elif not bc:
        match = 'BODY_IN_ZONE_NO_ATTR'
    elif pr and bc and not attr_match:
        if direction == 'LONG' and dist_below < ATTR_MIN:
            match = 'TOO_CLOSE_TO_ZONE'
        elif direction == 'LONG' and dist_below > ATTR_MAX:
            match = 'TOO_FAR_FROM_ZONE'
        elif direction == 'SHORT' and dist_above < ATTR_MIN:
            match = 'TOO_CLOSE_TO_ZONE'
        elif direction == 'SHORT' and dist_above > ATTR_MAX:
            match = 'TOO_FAR_FROM_ZONE'
        else:
            match = 'DIRECTION_MISMATCH'
    else:
        match = 'NO_MATCH'

    results.append({
        **t,
        'zone_found':  True,
        'zone_top':    round(zt, 5),
        'zone_bottom': round(zb, 5),
        'japan_bull':  jb,
        'pristine':    pr,
        'body_clean':  bc,
        'in_sess':     in_sess,
        'dist_pts':    dist_pts,
        'dist_label':  dist_label,
        'attr_long':   attr_long,
        'attr_short':  attr_short,
        'rev_long':    rev_long,
        'rev_short':   rev_short,
        'match':       match,
    })

# ── 5. SUMMARY ───────────────────────────────────────────────────────────────
match_counts = Counter(r['match'] for r in results)
total = len(results)
print("\n=== MATCH SUMMARY ===")
for k, v in sorted(match_counts.items(), key=lambda x: -x[1]):
    print(f"  {k:40s}: {v:3d}  ({100*v/total:.0f}%)")

print("\n=== DETAILED RESULTS ===")
for r in results:
    zf = r.get('zone_found', False)
    if zf:
        zone_str = (f"Z:[{r['zone_bottom']:.3f}-{r['zone_top']:.3f}] "
                    f"{'BULL' if r['japan_bull'] else 'BEAR'} "
                    f"P={int(r['pristine'])} BC={int(r['body_clean'])}")
    else:
        zone_str = "NO_ZONE"
    sess_str = f"sess={int(r.get('in_sess', 0))}"
    print(f"  #{r['trade_num']:3d} {r['date']} {r['direction']:5s} @{r['entry_px']:.3f} | {zone_str:50s} | {r.get('dist_label',''):28s} | {sess_str} -> {r['match']}")

with open('zone_match_results.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
print("\nSaved zone_match_results.json")
