"""
sweep_attr_v2.py
================
ATTR parameter sweep for the current dxy_improved_rules architecture.

Strategy (fast two-stage approach):
  1. Pre-generate an ATTR "universe" with the LOOSEST possible thresholds
     (MIN_GAP=50, MAX_PREV_RANGE=99999) for each NEAR_BUFFER value.
     NEAR_BUFFER must be baked in at generation time because it changes the
     actual TP price and therefore the trade outcome.

  2. For each combo (gap × prev_range × buffer × near_tp), post-hoc filter the
     appropriate pre-generated universe — no re-running the 63k-bar loop.

  Sweep grid:
    ATTR_MIN_GAP        : 100, 125, 150, 175, 200  pts
    ATTR_MAX_PREV_RANGE : 5000, 6000, 7000, 8000, 10000  pts
    ATTR_NEAR_BUFFER    : 25, 50, 75, 100  pts   (near_tp=True only)
    near_edge_tp        : True, False

  Total: (5×5×4) near + (5×5×1) far = 100 + 25 = 125 combos
  Base generation runs: 4 (near, varying buffer) + 1 (far) = 5 runs

Each signal in the universe stores:
  gap_abs      – abs(attr_gap_pts) at Tokyo open (for gap threshold filter)
  prev_range   – prior session range pts (for ATTR_MAX_PREV_RANGE filter)
  outcome/exit – pre-computed for the given buffer/tp_mode

NOTE: ATTR window now starts at 06:00 UTC on Tue-Fri (Ash's rule),
      06:30 UTC on Monday. This matches the updated dxy_improved_rules.py.
"""

import pandas as pd
import numpy as np
import itertools
from pathlib import Path

import dxy_improved_rules as imp
import dxy_clean_rules    as r

BASE  = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
PAIRS = r.PAIRS

# ── Sweep grid ────────────────────────────────────────────────────────────────
GAP_VALUES    = [100, 125, 150, 175, 200]
RANGE_VALUES  = [5000, 6000, 7000, 8000, 10000]
BUFFER_VALUES = [25, 50, 75, 100]   # only used when near_tp=True

MIN_GAP   = min(GAP_VALUES)
MAX_RANGE = max(RANGE_VALUES)


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


# ── Universe generator ────────────────────────────────────────────────────────
def generate_attr_universe(df_dxy, near_edge_tp: bool, near_buffer: int,
                           news_dates=None) -> list:
    """
    Run ATTR detection with loose thresholds (MIN_GAP, MAX_RANGE).
    Records gap_abs and prev_range in each signal for post-hoc filtering.
    Uses 6am UTC ATTR start on Tue-Fri (Ash's rule).
    """
    # Temporarily override module-level params
    imp.ATTR_MIN_GAP        = MIN_GAP
    imp.ATTR_MAX_PREV_RANGE = MAX_RANGE
    imp.ATTR_NEAR_BUFFER    = near_buffer
    imp.ATTR_MIN_REWARD     = 50   # loose

    df = df_dxy.copy().reset_index(drop=True)

    # Compute BB regimes (1H for REV direction, 4H for ATTR flat gate)
    df['bb_1h'], _              = imp.compute_bb_regime(df, 1)
    df['bb_4h'], df['bb_4h_flat'] = imp.compute_bb_regime(df, 4)

    bull_sig, bear_sig = imp.candle_signals_v2(df)

    # State variables
    london_open_price  = np.nan
    max_move_up        = 0.0
    max_move_down      = 0.0
    prev_session_high  = np.nan
    prev_session_low   = np.nan
    prev_session_range = 0.0
    attr_gap_pts       = 0.0
    attr_gap_target    = np.nan
    attr_traded        = False

    # Pre-build day groups for structural SL
    df['date'] = df['time'].dt.date
    day_grp = df.groupby('date').agg(day_h=('high', 'max'), day_l=('low', 'min'))

    signals = []
    n = len(df)

    for i in range(4, n):
        row    = df.iloc[i]
        c, o   = row['close'], row['open']
        ts     = row['time']
        hour   = ts.hour
        minute = ts.minute
        curr_min = hour * 60 + minute
        dow    = ts.dayofweek

        in_japan = ((hour == 23) and (minute >= 45)) or (0 <= hour < 6)

        is_london_open = (not in_japan and hour == imp.LON_OPEN_HOUR
                          and minute == imp.LON_OPEN_MINUTE and dow != 0)
        is_monday_open = (not in_japan and hour == imp.MON_OPEN_HOUR
                          and minute == imp.MON_OPEN_MINUTE and dow == 0)
        is_tokyo_open  = (hour == 23 and minute == 45)

        # Tokyo open: establish gap
        if is_tokyo_open:
            attr_traded = False
            ref_close   = None
            for back, exp_offset in [(2, 30), (1, 15)]:
                if i >= back:
                    cand = df.iloc[i - back]
                    if abs((cand['time'] - (ts - pd.Timedelta(minutes=exp_offset))
                            ).total_seconds()) <= 120:
                        ref_close = cand['close']
                        break
            if ref_close is not None:
                raw_gap = (o - ref_close) * 10000
                if abs(raw_gap) >= 10:
                    attr_gap_pts    = raw_gap
                    attr_gap_target = ref_close
                else:
                    attr_gap_pts    = (c - o) * 10000
                    attr_gap_target = o
            else:
                attr_gap_pts    = 0.0
                attr_gap_target = np.nan

        # London open: update session state
        if is_london_open or is_monday_open:
            london_open_price  = o
            max_move_up        = 0.0
            max_move_down      = 0.0
            today_dt           = ts.date()
            prior_days         = [d for d in day_grp.index if d < today_dt]
            if prior_days:
                prev_dt            = max(prior_days)
                prev_session_high  = float(day_grp.at[prev_dt, 'day_h'])
                prev_session_low   = float(day_grp.at[prev_dt, 'day_l'])
                prev_session_range = (prev_session_high - prev_session_low) * 10000
            else:
                prev_session_high  = np.nan
                prev_session_low   = np.nan
                prev_session_range = 0.0

        if np.isnan(london_open_price):
            continue

        if not in_japan:
            above = (c - london_open_price) * 10000
            if above > 0:
                max_move_up   = max(max_move_up,    above)
            elif above < 0:
                max_move_down = max(max_move_down, -above)

        # ATTR entry window: 06:00 UTC on Tue-Fri, 06:30 on Monday
        mon_start  = imp.MON_OPEN_HOUR * 60 + imp.MON_OPEN_MINUTE
        attr_start = mon_start if dow == 0 else (imp.ATTR_START_HOUR * 60 + imp.ATTR_START_MIN)
        in_attr_sess = attr_start <= curr_min <= imp.ATTR_WINDOW_END and not in_japan

        if news_dates and r.news_blocks_pair(news_dates, str(ts), 'ALL_USD'):
            continue

        bb_4h_flat = int(row['bb_4h_flat'])

        if not (not attr_traded and in_attr_sess
                and abs(attr_gap_pts) >= MIN_GAP
                and not np.isnan(attr_gap_target)
                and prev_session_range <= MAX_RANGE
                and bb_4h_flat == 1):
            continue

        # ATTR LONG
        if (attr_gap_pts < 0 and c < attr_gap_target and bull_sig.at[i]):
            reward_pts_check = (attr_gap_target - c) * 10000
            if reward_pts_check >= 50:
                tp_price = (attr_gap_target - near_buffer / 10000
                            if near_edge_tp else attr_gap_target)
                sl_d = tp_price - c
                if sl_d > 0:
                    sl_price = c - sl_d
                    outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'long')
                    signals.append({
                        'type': 'ATTR_LONG', 'entry_time': str(ts),
                        'entry': round(c, 5), 'tp': round(tp_price, 5),
                        'sl': round(sl_price, 5),
                        'sl_pts': round(sl_d * 10000),
                        'outcome': outcome, 'exit_px': round(exit_px, 5),
                        'exit_time': str(df.at[exit_bar, 'time']),
                        'bias_1h': int(row['bb_1h']), 'bias_4h': int(row['bb_4h']),
                        # metadata for post-hoc filtering
                        'gap_abs':    abs(round(attr_gap_pts, 1)),
                        'prev_range': round(prev_session_range, 0),
                    })
                    attr_traded = True
                    continue

        # ATTR SHORT
        if (attr_gap_pts > 0 and c > attr_gap_target and bear_sig.at[i]):
            reward_pts_check = (c - attr_gap_target) * 10000
            if reward_pts_check >= 50:
                tp_price = (attr_gap_target + near_buffer / 10000
                            if near_edge_tp else attr_gap_target)
                sl_d = c - tp_price
                if sl_d > 0:
                    sl_price = c + sl_d
                    outcome, exit_px, exit_bar = r.resolve(df, i, c, tp_price, sl_price, 'short')
                    signals.append({
                        'type': 'ATTR_SHORT', 'entry_time': str(ts),
                        'entry': round(c, 5), 'tp': round(tp_price, 5),
                        'sl': round(sl_price, 5),
                        'sl_pts': round(sl_d * 10000),
                        'outcome': outcome, 'exit_px': round(exit_px, 5),
                        'exit_time': str(df.at[exit_bar, 'time']),
                        'bias_1h': int(row['bb_1h']), 'bias_4h': int(row['bb_4h']),
                        'gap_abs':    abs(round(attr_gap_pts, 1)),
                        'prev_range': round(prev_session_range, 0),
                    })
                    attr_traded = True

    return signals


# ── Pair-level DXY-exit evaluator ─────────────────────────────────────────────
def eval_combo(universe, pair_dfs, gap, prev_range) -> tuple:
    """Filter pre-generated universe by gap and prev_range thresholds."""
    filtered = [s for s in universe
                if s['gap_abs']    >= gap
                and s['prev_range'] <= prev_range]
    if not filtered:
        return 0, {}, None

    all_trades = []
    pair_nets  = {}
    for pair in PAIRS:
        trades = r.apply_to_pair_dxy_exit(filtered, pair_dfs[pair], pair)
        all_trades.extend(trades)
        s = r.stats_r(trades, pair)
        pair_nets[pair] = round(s.get('NetR', 0), 2)

    port = r.stats_r(all_trades, 'PORT')
    return len(filtered), pair_nets, port


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Loading 33-month merged data...")
    df_dxy   = load_merged('DXY')
    pair_dfs = {p: load_merged(p) for p in PAIRS}
    news_dates = r.load_news_filter()
    months   = (df_dxy['time'].max() - df_dxy['time'].min()).days / 30.44
    print(f"  DXY bars : {len(df_dxy):,}  ({df_dxy['time'].min().date()} to {df_dxy['time'].max().date()})")
    print(f"  Period   : {months:.1f} months")
    print()

    # ── Pre-generate universes ────────────────────────────────────────────────
    universes = {}   # keyed by (near_tp, buffer)

    # near_tp=False: buffer irrelevant (TP goes to full gap target)
    print("Pre-generating universe: near_tp=False ...")
    universes[(False, 0)] = generate_attr_universe(df_dxy, False, 0, news_dates)
    print(f"  {len(universes[(False,0)])} signals")

    # near_tp=True: one run per buffer value
    for buf in BUFFER_VALUES:
        print(f"Pre-generating universe: near_tp=True, buffer={buf}pts ...")
        universes[(True, buf)] = generate_attr_universe(df_dxy, True, buf, news_dates)
        print(f"  {len(universes[(True,buf)])} signals")

    print()

    # ── Sweep ─────────────────────────────────────────────────────────────────
    results = []
    total_combos = len(GAP_VALUES) * len(RANGE_VALUES) * (len(BUFFER_VALUES) + 1)
    done = 0
    print(f"Running {total_combos} combos...")

    # near_tp=False
    for gap, prev_range in itertools.product(GAP_VALUES, RANGE_VALUES):
        univ = universes[(False, 0)]
        n, pair_nets, port = eval_combo(univ, pair_dfs, gap, prev_range)
        done += 1
        results.append(_row(gap, prev_range, 0, False, n, pair_nets, port))

    # near_tp=True
    for gap, prev_range, buf in itertools.product(GAP_VALUES, RANGE_VALUES, BUFFER_VALUES):
        univ = universes[(True, buf)]
        n, pair_nets, port = eval_combo(univ, pair_dfs, gap, prev_range)
        done += 1
        results.append(_row(gap, prev_range, buf, True, n, pair_nets, port))
        if done % 25 == 0:
            best = max(results, key=lambda x: x['total_pair_r'])
            print(f"  [{done:>3}/{total_combos}]  best: "
                  f"gap={best['gap']} range={best['prev_range']} "
                  f"buf={best['buffer']} near={best['near_tp']}  "
                  f"NetR={best['total_pair_r']:+.1f}R  n={best['n_sigs']}")

    # ── Save ──────────────────────────────────────────────────────────────────
    df = pd.DataFrame(results)
    out = BASE / 'sweep_attr_v2_results.csv'
    df.to_csv(out, index=False)
    print(f"\nSaved {len(df)} rows -> {out.name}")

    # ── Print top results ─────────────────────────────────────────────────────
    _print_top(df, months)


def _row(gap, prev_range, buf, near_tp, n, pair_nets, port):
    if port is None:
        return {'gap': gap, 'prev_range': prev_range, 'buffer': buf,
                'near_tp': near_tp, 'n_sigs': 0,
                'wr': 0, 'pf': 0, 'dxy_net_r': 0, 'total_pair_r': -999,
                **{f'net_{p.lower()}': 0 for p in PAIRS}}
    total = sum(pair_nets.values())
    return {
        'gap':          gap,
        'prev_range':   prev_range,
        'buffer':       buf,
        'near_tp':      near_tp,
        'n_sigs':       n,
        'wr':           round(port.get('WR%', 0), 1),
        'pf':           round(port.get('PF',  0), 3),
        'dxy_net_r':    round(port.get('NetR', 0), 2),
        'total_pair_r': round(total, 2),
        **{f'net_{p.lower()}': pair_nets.get(p, 0) for p in PAIRS}
    }


def _print_top(df, months):
    top = df[df['n_sigs'] >= 10].sort_values('total_pair_r', ascending=False).head(30)
    print()
    print("=" * 110)
    print("  TOP 30 ATTR COMBOS  (min 10 signals, sorted by total pair NetR, DXY-exit)")
    print("=" * 110)
    print(f"  {'GAP':>5} {'RANGE':>6} {'BUF':>5} {'TP':>6}  {'N':>4}  "
          f"{'N/mo':>5}  {'WR%':>6}  {'PF':>5}  {'PairNetR':>9}  "
          + "  ".join(f"{p[:6]:>8}" for p in PAIRS))
    print("  " + "-" * 107)
    for _, row in top.iterrows():
        tp_str  = "near" if row['near_tp'] else "full"
        per_mo  = row['n_sigs'] / months
        p_cols  = "  ".join(f"{row[f'net_{p.lower()}']:>+8.1f}" for p in PAIRS)
        print(f"  {int(row['gap']):>5} {int(row['prev_range']):>6} "
              f"{int(row['buffer']):>5} {tp_str:>6}  {int(row['n_sigs']):>4}  "
              f"{per_mo:>5.1f}  {row['wr']:>5.1f}%  {row['pf']:>5.3f}  "
              f"{row['total_pair_r']:>+9.1f}R  {p_cols}")

    print()
    print(f"  Current config: gap=150  prev_range=8000  buffer=50  near_tp=True")
    base = df[(df['gap'] == 150) & (df['prev_range'] == 8000) &
              (df['buffer'] == 50) & (df['near_tp'] == True)]
    if not base.empty:
        row = base.iloc[0]
        print(f"  -> N={int(row['n_sigs'])}  WR={row['wr']}%  "
              f"PairNetR={row['total_pair_r']:+.1f}R  ({row['n_sigs']/months:.1f}/mo)")

    print()
    for tp_mode, label in [(True, "near-edge TP"), (False, "full gap fill")]:
        sub = df[(df['near_tp'] == tp_mode) & (df['n_sigs'] >= 10)]
        if sub.empty:
            continue
        best = sub.loc[sub['total_pair_r'].idxmax()]
        print(f"  Best {label}: gap={int(best['gap'])}  range={int(best['prev_range'])}  "
              f"buf={int(best['buffer'])}  "
              f"-> N={int(best['n_sigs'])} ({best['n_sigs']/months:.1f}/mo)  "
              f"WR={best['wr']}%  PairNetR={best['total_pair_r']:+.1f}R")


if __name__ == '__main__':
    main()
