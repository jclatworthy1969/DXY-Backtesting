"""
dxy_optimize.py
===============
Parameter sweep for the DXY zone strategy using merged (~20-month) price data.

Two independent sweeps:
  1. ATTR sweep  — varies ATTR_MIN_GAP, ATTR_APPROACH_PTS, ATTR_NEAR_BUFFER,
                   ATTR_MIN_REWARD  (REV parameters held at defaults)
  2. REV sweep   — varies REV_MIN_BODY, REV_MIN_RANGE, REV_MAX_DIST
                   (ATTR parameters held at defaults)

For each combination, both exit methods are evaluated:
  • Standard exit : pair exits when its own converted TP/SL is hit
  • DXY exit      : pair exits when DXY hits its TP/SL bar price

Results saved to:
  dxy_optimize_attr.csv
  dxy_optimize_rev.csv
  dxy_optimize_summary.txt
"""

import itertools
import time
import sys
from pathlib import Path
from io import StringIO

import pandas as pd
import numpy as np

BASE = Path(r"C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting")
sys.path.insert(0, str(BASE))
import dxy_clean_rules as R

# ── Point FILE_MAP at merged files ─────────────────────────────────────────────
R.FILE_MAP = {
    'DXY':    BASE / 'TVC_DXY, 15_merged.csv',
    'EURUSD': BASE / 'FX_EURUSD, 15_merged.csv',
    'USDJPY': BASE / 'FX_USDJPY, 15_merged.csv',
    'USDCAD': BASE / 'FX_USDCAD, 15_merged.csv',
    'XAUUSD': BASE / 'FX_XAUUSD, 15_merged.csv',
}

PAIRS_OPT   = ['EURUSD', 'USDJPY', 'USDCAD']   # pairs being optimised
MIN_TRADES  = 15                                  # minimum trades for a valid result

# ── DEFAULT parameters (baseline) ─────────────────────────────────────────────
DEFAULTS = dict(
    ZONE_MIN_GAP      = 30,
    ZONE_MIN_WIDTH    = 150,
    ATTR_MIN_GAP      = 150,
    ATTR_APPROACH_PTS = 150,
    ATTR_NEAR_BUFFER  = 50,
    ATTR_MIN_REWARD   = 150,
    ATTR_WINDOW       = (7*60+30, 19*60+30),
    REV_MIN_BODY      = 200,
    REV_MIN_RANGE     = 400,
    REV_MAX_DIST      = 500,
    REV_WINDOW        = (7*60+30, 12*60+0),
    PIVOT_LOOKBACK    = 20,
    MAX_LOOKFORWARD   = 400,
    EMA_FAST          = 20,
    EMA_SLOW          = 50,
    PIN_WICK_MULT     = 2.0,
)

# ── PARAMETER GRIDS ───────────────────────────────────────────────────────────
ATTR_GRID = dict(
    ATTR_MIN_GAP      = [75, 100, 150, 200, 250],
    ATTR_APPROACH_PTS = [75, 100, 150, 200],
    ATTR_NEAR_BUFFER  = [0, 25, 50, 75, 100],
    ATTR_MIN_REWARD   = [100, 150, 200],
)

REV_GRID = dict(
    REV_MIN_BODY  = [100, 150, 200, 250, 300],
    REV_MIN_RANGE = [200, 300, 400, 500],
    REV_MAX_DIST  = [300, 400, 500, 600],
)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def set_params(params):
    """Override R module constants from a dict."""
    for k, v in params.items():
        setattr(R, k, v)

def reset_params():
    """Restore all defaults."""
    set_params(DEFAULTS)

def pair_metrics(trades, pair):
    """Return (N, WR, PF, NetR) for a pair's trades. Uses r_actual if present."""
    t = [x for x in trades if x['pair'] == pair]
    if len(t) < MIN_TRADES:
        return (len(t), 0.0, 0.0, 0.0)
    df = pd.DataFrame(t)
    if 'r_actual' in df.columns:
        # Fractional R (DXY exit)
        w = (df['r_actual'] > 0).sum()
        l = (df['r_actual'] < 0).sum()
        gw = df.loc[df['r_actual'] > 0, 'r_actual'].sum()
        gl = df.loc[df['r_actual'] < 0, 'r_actual'].abs().sum()
        wr = w / (w + l) * 100 if (w + l) > 0 else 0
        pf = gw / gl if gl > 0 else 0.0
        net = round(df['r_actual'].sum(), 2)
    else:
        w = (df['outcome'] == 'win').sum()
        l = (df['outcome'] == 'loss').sum()
        wr = w / (w + l) * 100 if (w + l) > 0 else 0
        pf = w / l if l > 0 else 0.0
        net = int(w - l)
    return (len(t), round(wr, 1), round(pf, 3), net)

def run_combo(df_dxy, pair_dfs, params_override):
    """Run one parameter combination. Returns (std_trades, dxy_trades, n_sigs)."""
    reset_params()
    set_params(params_override)
    sigs, _ = R.generate_dxy_signals(df_dxy, near_edge_tp=True)
    std_trades, dxy_trades = [], []
    for p in PAIRS_OPT:
        std_trades.extend(R.apply_to_pair(sigs, pair_dfs[p], p))
        dxy_trades.extend(R.apply_to_pair_dxy_exit(sigs, pair_dfs[p], p))
    reset_params()
    return std_trades, dxy_trades, len(sigs)

def grid_combos(grid):
    keys = list(grid.keys())
    for vals in itertools.product(*grid.values()):
        yield dict(zip(keys, vals))

# ── SWEEP ─────────────────────────────────────────────────────────────────────
def run_sweep(df_dxy, pair_dfs, grid, label):
    combos = list(grid_combos(grid))
    total  = len(combos)
    print(f"\n{'='*68}")
    print(f"  {label}  ({total} combinations)")
    print(f"{'='*68}")

    rows = []
    t0   = time.time()
    for idx, combo in enumerate(combos, 1):
        std, dxy, n_sigs = run_combo(df_dxy, pair_dfs, combo)

        row = {**combo, 'n_signals': n_sigs}
        for p in PAIRS_OPT:
            for tag, trades in [('std', std), ('dxy', dxy)]:
                n, wr, pf, net = pair_metrics(trades, p)
                row[f'{p}_{tag}_N']   = n
                row[f'{p}_{tag}_WR']  = wr
                row[f'{p}_{tag}_PF']  = pf
                row[f'{p}_{tag}_Net'] = net
        rows.append(row)

        if idx % 20 == 0 or idx == total:
            elapsed = time.time() - t0
            eta     = elapsed / idx * (total - idx)
            print(f"  {idx:>4}/{total}  elapsed {elapsed:.0f}s  ETA {eta:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    print(f"\n  Sweep complete in {time.time()-t0:.0f}s")
    return df

# ── REPORTING ─────────────────────────────────────────────────────────────────
def top_results(df, pair, exit_tag, metric, n=10, min_trades=MIN_TRADES):
    """Return top N rows for a pair/exit/metric, filtered by trade count."""
    col_n   = f'{pair}_{exit_tag}_N'
    col_met = f'{pair}_{exit_tag}_{metric}'
    sub = df[df[col_n] >= min_trades].copy()
    return sub.nlargest(n, col_met)

def print_top(df, pair, exit_tag, metric, label, n=10):
    t = top_results(df, pair, exit_tag, metric, n)
    if t.empty:
        print(f"  No results with >= {MIN_TRADES} trades")
        return
    param_cols = [c for c in t.columns if c in
                  list(ATTR_GRID.keys()) + list(REV_GRID.keys())]
    metric_cols = [f'{pair}_{exit_tag}_N',
                   f'{pair}_{exit_tag}_WR',
                   f'{pair}_{exit_tag}_PF',
                   f'{pair}_{exit_tag}_Net']
    print(f"\n  {label}  (top {n} by {metric}, {exit_tag} exit)")
    print("  " + "  ".join(f"{c:<22}" for c in param_cols) +
          "  " + "  ".join(f"{c:<14}" for c in metric_cols))
    print("  " + "-" * (len(param_cols)*24 + len(metric_cols)*16))
    for _, row in t.iterrows():
        pstr = "  ".join(f"{row[c]:<22}" for c in param_cols)
        mstr = "  ".join(f"{row[c]:<14}" for c in metric_cols)
        print(f"  {pstr}  {mstr}")

def summarise_sweep(df, label, outfile=None):
    buf = StringIO()

    def p(*args, **kwargs):
        try:
            print(*args, **kwargs)
        except UnicodeEncodeError:
            safe = [str(a).encode('ascii', 'replace').decode('ascii') for a in args]
            print(*safe, **kwargs)
        print(*args, file=buf, **kwargs)

    p(f"\n{'='*68}")
    p(f"  RESULTS: {label}")
    p(f"{'='*68}")

    for pair in PAIRS_OPT:
        p(f"\n  ── {pair} ──────────────────────────────────────────")
        for exit_tag, exit_label in [('std', 'Standard exit'), ('dxy', 'DXY exit')]:
            p(f"\n    [{exit_label}]")
            for metric, met_label in [('PF', 'Profit Factor'), ('Net', 'Net R')]:
                t = top_results(df, pair, exit_tag, metric, n=5)
                if t.empty:
                    continue
                param_cols = [c for c in t.columns if c in
                              list(ATTR_GRID.keys()) + list(REV_GRID.keys())]
                p(f"    Top 5 by {met_label}:")
                hdr = "  ".join(f"{c:<20}" for c in param_cols)
                p(f"      {hdr}   N    WR%    PF     NetR")
                p(f"      {'-'*(len(param_cols)*22 + 28)}")
                for _, row in t.iterrows():
                    pstr = "  ".join(f"{row[c]:<20}" for c in param_cols)
                    n   = row[f'{pair}_{exit_tag}_N']
                    wr  = row[f'{pair}_{exit_tag}_WR']
                    pf  = row[f'{pair}_{exit_tag}_PF']
                    net = row[f'{pair}_{exit_tag}_Net']
                    p(f"      {pstr}   {n:<4} {wr:>5.1f}%  {pf:>6.3f}  {net:>+7}")

    # Best overall (summed Net R across the three pairs)
    p(f"\n  ── COMBINED NET R (sum across {', '.join(PAIRS_OPT)}) ────────────────")
    for exit_tag, exit_label in [('std', 'Standard'), ('dxy', 'DXY exit')]:
        net_cols = [f'{p}_{exit_tag}_Net' for p in PAIRS_OPT]
        n_cols   = [f'{p}_{exit_tag}_N'   for p in PAIRS_OPT]
        valid = df[df[n_cols].min(axis=1) >= MIN_TRADES].copy()
        if valid.empty:
            continue
        valid['_total'] = valid[net_cols].sum(axis=1)
        best = valid.nlargest(5, '_total')
        param_cols = [c for c in best.columns if c in
                      list(ATTR_GRID.keys()) + list(REV_GRID.keys())]
        p(f"\n    [{exit_label}] Top 5 by combined Net R:")
        hdr = "  ".join(f"{c:<20}" for c in param_cols)
        pair_hdr = "  ".join(f"{(pa[:6]+'_Net'):>9}" for pa in PAIRS_OPT)
        p(f"      {hdr}   {pair_hdr}   Total")
        p(f"      {'-'*(len(param_cols)*22 + 50)}")
        for _, row in best.iterrows():
            pstr = "  ".join(f"{row[c]:<20}" for c in param_cols)
            nets = "   ".join(f"{row[c]:>+7}" for c in net_cols)
            p(f"      {pstr}   {nets}   {row['_total']:>+7.1f}")

    if outfile:
        Path(outfile).write_text(buf.getvalue(), encoding='utf-8')
        print(f"\n  Summary saved -> {outfile}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("Loading merged data...")
    df_dxy   = R.load('DXY')
    pair_dfs = {p: R.load(p) for p in PAIRS_OPT}

    # ── Baseline (current defaults) ───────────────────────────────────────────
    print("\nBaseline (current defaults):")
    reset_params()
    sigs0, _ = R.generate_dxy_signals(df_dxy, near_edge_tp=True)
    std0, dxy0 = [], []
    for p in PAIRS_OPT:
        std0.extend(R.apply_to_pair(sigs0, pair_dfs[p], p))
        dxy0.extend(R.apply_to_pair_dxy_exit(sigs0, pair_dfs[p], p))
    print(f"  {'Pair':<8} {'Std PF':>7}  {'Std Net':>8}  {'DXY PF':>7}  {'DXY Net':>8}")
    for p in PAIRS_OPT:
        _, _, spf, snet = pair_metrics(std0, p)
        _, _, dpf, dnet = pair_metrics(dxy0, p)
        print(f"  {p:<8} {spf:>7.3f}  {snet:>+8}R  {dpf:>7.3f}  {dnet:>+8.1f}R")

    # ── ATTR sweep ────────────────────────────────────────────────────────────
    attr_csv = BASE / 'dxy_optimize_attr.csv'
    if attr_csv.exists():
        print("\nLoading existing ATTR results from dxy_optimize_attr.csv ...")
        attr_df = pd.read_csv(attr_csv)
    else:
        attr_df = run_sweep(df_dxy, pair_dfs, ATTR_GRID, "ATTR PARAMETER SWEEP")
        attr_df.to_csv(attr_csv, index=False)
        print(f"\n  Raw results saved -> dxy_optimize_attr.csv")
    summarise_sweep(attr_df, "ATTR SWEEP", BASE / 'dxy_optimize_attr_summary.txt')

    # ── REV sweep ─────────────────────────────────────────────────────────────
    rev_csv = BASE / 'dxy_optimize_rev.csv'
    if rev_csv.exists():
        print("\nLoading existing REV results from dxy_optimize_rev.csv ...")
        rev_df = pd.read_csv(rev_csv)
    else:
        rev_df = run_sweep(df_dxy, pair_dfs, REV_GRID, "REV PARAMETER SWEEP")
        rev_df.to_csv(rev_csv, index=False)
        print(f"\n  Raw results saved -> dxy_optimize_rev.csv")
    summarise_sweep(rev_df, "REV SWEEP", BASE / 'dxy_optimize_rev_summary.txt')

    # ── Combined best ─────────────────────────────────────────────────────────
    # Pick best ATTR params per pair (by PF), then pick best REV params per pair
    print("\n" + "="*68)
    print("  RECOMMENDED PARAMETERS")
    print("="*68)
    for pair in PAIRS_OPT:
        print(f"\n  {pair}:")
        for exit_tag in ['std', 'dxy']:
            # Best ATTR by PF
            a_row = top_results(attr_df, pair, exit_tag, 'PF', n=1)
            r_row = top_results(rev_df,  pair, exit_tag, 'PF', n=1)
            a_params = {k: a_row.iloc[0][k] for k in ATTR_GRID if k in a_row.columns} if not a_row.empty else {}
            r_params = {k: r_row.iloc[0][k] for k in REV_GRID  if k in r_row.columns} if not r_row.empty else {}
            n_a  = a_row.iloc[0][f'{pair}_{exit_tag}_N']   if not a_row.empty else 0
            pf_a = a_row.iloc[0][f'{pair}_{exit_tag}_PF']  if not a_row.empty else 0
            net_a = a_row.iloc[0][f'{pair}_{exit_tag}_Net'] if not a_row.empty else 0
            n_r  = r_row.iloc[0][f'{pair}_{exit_tag}_N']   if not r_row.empty else 0
            pf_r = r_row.iloc[0][f'{pair}_{exit_tag}_PF']  if not r_row.empty else 0
            net_r_v = r_row.iloc[0][f'{pair}_{exit_tag}_Net'] if not r_row.empty else 0
            print(f"    [{exit_tag} exit]")
            print(f"      Best ATTR params: {a_params}  => N={n_a}  PF={pf_a:.3f}  Net={net_a:+}")
            print(f"      Best REV  params: {r_params}  => N={n_r}  PF={pf_r:.3f}  Net={net_r_v:+}")

if __name__ == '__main__':
    main()
