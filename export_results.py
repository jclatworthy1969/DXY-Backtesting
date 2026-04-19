import json
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, GradientFill
from openpyxl.utils import get_column_letter

with open(r'C:\Users\justi\OneDrive\Documents\Claude\Projects\DXY Backtesting\zone_match_results.json') as f:
    results = json.load(f)

OUT = r'C:\Users\justi\OneDrive\Desktop\Claude is a genius.xlsx'
RISK = 1000

# ── Colours ──────────────────────────────────────────────────────────────────
HDR_BG      = '1F3864'  # dark navy
HDR_FG      = 'FFFFFF'
SUBHDR_BG   = '2E75B6'  # mid blue
SUBHDR_FG   = 'FFFFFF'
ATTR_BG     = 'E2EFDA'  # light green tint
REV_BG      = 'DEEAF1'  # light blue tint
MANUAL_BG   = 'FFF2CC'  # light yellow
OTHER_BG    = 'FCE4D6'  # light orange
ALT_BG      = 'F5F5F5'
WIN_BG      = 'C6EFCE'; WIN_FG  = '276221'
SUMM_BG     = 'D9E1F2'
TITLE_BG    = '1F3864'

def thin_border():
    s = Side(style='thin', color='CCCCCC')
    return Border(left=s, right=s, top=s, bottom=s)

def thick_bottom():
    return Border(
        left=Side(style='thin', color='CCCCCC'),
        right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'),
        bottom=Side(style='medium', color='1F3864')
    )

def apply_hdr(cell, text, bg=HDR_BG, fg=HDR_FG, size=11, bold=True, wrap=False):
    cell.value = text
    cell.font = Font(name='Arial', bold=bold, color=fg, size=size)
    cell.fill = PatternFill('solid', fgColor=bg)
    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=wrap)
    cell.border = thin_border()

def apply_cell(cell, value, fmt=None, bold=False, align='center', bg=None, fg='000000', wrap=False):
    cell.value = value
    cell.font = Font(name='Arial', bold=bold, color=fg, size=10)
    cell.alignment = Alignment(horizontal=align, vertical='center', wrap_text=wrap)
    cell.border = thin_border()
    if bg:
        cell.fill = PatternFill('solid', fgColor=bg)
    if fmt:
        cell.number_format = fmt

wb = Workbook()

# ══════════════════════════════════════════════════════════════════════════════
# SHEET 1 — SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
ws = wb.active
ws.title = 'Summary'
ws.sheet_view.showGridLines = False
ws.column_dimensions['A'].width = 34
ws.column_dimensions['B'].width = 22
ws.column_dimensions['C'].width = 22
ws.column_dimensions['D'].width = 22

# Title
ws.merge_cells('A1:D1')
c = ws['A1']
c.value = 'DXY Backtesting Results — Zone Strategy'
c.font = Font(name='Arial', bold=True, size=16, color=HDR_FG)
c.fill = PatternFill('solid', fgColor=TITLE_BG)
c.alignment = Alignment(horizontal='center', vertical='center')
ws.row_dimensions[1].height = 36

ws.merge_cells('A2:D2')
c = ws['A2']
c.value = 'Last 50 Winning Trades  |  $1,000 risk per trade  |  May – Dec 2024'
c.font = Font(name='Arial', size=11, color='666666', italic=True)
c.alignment = Alignment(horizontal='center', vertical='center')
ws.row_dimensions[2].height = 22

# ── Section: Match Breakdown ─────────────────────────────────────────────────
ws.row_dimensions[3].height = 8
ws.merge_cells('A4:D4')
apply_hdr(ws['A4'], 'TRADE CLASSIFICATION BREAKDOWN', bg=SUBHDR_BG)
ws.row_dimensions[4].height = 22

headers = ['Category', 'Count', '% of 50 Trades', 'Notes']
for col, h in enumerate(headers, 1):
    apply_hdr(ws.cell(5, col), h, bg='2F5496', size=10)
ws.row_dimensions[5].height = 20

from collections import Counter
match_counts = Counter(r['match'] for r in results)
categories = [
    ('ATTRACTION',    ATTR_BG,   'Body-clean zone, signal, 150–500 DXY pts to TP'),
    ('REVERSAL',      REV_BG,    'Broken zone as S/R, any entry signal, in session'),
    ('MANUAL_CHECK',  MANUAL_BG, 'Missing zone data — manual review required'),
    ('TOO_CLOSE_TO_ZONE', OTHER_BG, 'Entry too close to zone far-side TP (<150 pts)'),
    ('BODY_IN_ZONE_NO_ATTR', OTHER_BG, 'Body inside zone — attraction condition failed'),
]
row = 6
for cat, bg, note in categories:
    cnt = match_counts.get(cat, 0)
    apply_cell(ws.cell(row, 1), cat, bold=True, align='left', bg=bg)
    apply_cell(ws.cell(row, 2), cnt, fmt='0', bg=bg)
    apply_cell(ws.cell(row, 3), cnt/50, fmt='0%', bg=bg)
    apply_cell(ws.cell(row, 4), note, align='left', bg=bg, wrap=True)
    ws.row_dimensions[row].height = 20
    row += 1

# ── Section: P&L Summary ─────────────────────────────────────────────────────
ws.row_dimensions[row].height = 8; row += 1
ws.merge_cells(f'A{row}:D{row}')
apply_hdr(ws[f'A{row}'], 'PERFORMANCE — MATCHED TRADES ONLY (ATTRACTION + REVERSAL)', bg=SUBHDR_BG)
ws.row_dimensions[row].height = 22; row += 1

matched = [r for r in results if r['match'] in ('ATTRACTION', 'REVERSAL')]
attr_t  = [r for r in matched if r['match'] == 'ATTRACTION']
rev_t   = [r for r in matched if r['match'] == 'REVERSAL']

def R(t): return t['pip_gain'] / t['sl_gap_pts'] if t['sl_gap_pts'] else 0
def pnl(t): return R(t) * RISK

total_pnl   = sum(pnl(t) for t in matched)
avg_pnl     = total_pnl / len(matched)
avg_pips    = sum(t['pip_gain'] for t in matched) / len(matched)
avg_r       = sum(R(t) for t in matched) / len(matched)
attr_avg_r  = sum(R(t) for t in attr_t) / len(attr_t) if attr_t else 0
rev_avg_r   = sum(R(t) for t in rev_t)  / len(rev_t)  if rev_t  else 0

perf_rows = [
    ('Trades matched (of 50)',       f'{len(matched)} trades',         '',                       ''),
    ('  — Attraction setups',        f'{len(attr_t)} trades',          f'Avg R: {attr_avg_r:.2f}R', ''),
    ('  — Reversal setups',          f'{len(rev_t)} trades',           f'Avg R: {rev_avg_r:.2f}R',  ''),
    ('Win rate',                     '100%',                           '',                       'All trades drawn from confirmed winners'),
    ('Total P&L  @ $1,000/trade',    f'${total_pnl:,.0f}',             '',                       ''),
    ('Avg P&L per trade',            f'${avg_pnl:,.0f}',               '',                       ''),
    ('Avg pip gain',                 f'{avg_pips:.0f} pts',            '≈ {:.0f} DXY pts'.format(avg_pips/10), ''),
    ('Avg R multiple',               f'{avg_r:.2f}R',                  '',                       ''),
    ('Best trade',                   f'${max(pnl(t) for t in matched):,.0f}', f'{max(R(t) for t in matched):.2f}R', ''),
    ('Worst trade',                  f'${min(pnl(t) for t in matched):,.0f}', f'{min(R(t) for t in matched):.2f}R', ''),
]

for label, val, sub, note in perf_rows:
    bg = SUMM_BG if not label.startswith('  ') else ALT_BG
    bold = not label.startswith('  ')
    apply_cell(ws.cell(row, 1), label, align='left', bg=bg, bold=bold)
    apply_cell(ws.cell(row, 2), val,   align='center', bg=bg, bold=bold)
    apply_cell(ws.cell(row, 3), sub,   align='center', bg=bg)
    apply_cell(ws.cell(row, 4), note,  align='left',   bg=bg, wrap=True)
    ws.row_dimensions[row].height = 20
    row += 1

# ── Disclaimer ───────────────────────────────────────────────────────────────
ws.row_dimensions[row].height = 8; row += 1
ws.merge_cells(f'A{row}:D{row}')
c = ws[f'A{row}']
c.value = '⚠  Win rate reflects confirmed winning trades only. Real-world win rate across all signals will be lower. Trade duration not available in current dataset.'
c.font = Font(name='Arial', size=9, color='7F7F7F', italic=True)
c.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
ws.row_dimensions[row].height = 28

# ══════════════════════════════════════════════════════════════════════════════
# SHEET 2 — TRADE LOG (matched only)
# ══════════════════════════════════════════════════════════════════════════════
ws2 = wb.create_sheet('Trade Log')
ws2.sheet_view.showGridLines = False
ws2.freeze_panes = 'A3'

col_widths = [6, 12, 8, 12, 10, 10, 10, 10, 10, 10, 10, 8, 10, 28, 22]
col_headers = [
    '#', 'Date', 'Direction', 'Setup', 'Entry', 'Stop Loss',
    'Exit', 'SL Gap\n(pts)', 'Pip Gain\n(pts)', 'R Multiple',
    '$P&L\n@$1k risk', 'In\nSession', 'Dist Label', 'Zone', 'Notes'
]

for i, w in enumerate(col_widths, 1):
    ws2.column_dimensions[get_column_letter(i)].width = w

ws2.merge_cells('A1:O1')
c = ws2['A1']
c.value = 'DXY Zone Strategy — Matched Trade Log (Attraction + Reversal)'
c.font = Font(name='Arial', bold=True, size=14, color=HDR_FG)
c.fill = PatternFill('solid', fgColor=TITLE_BG)
c.alignment = Alignment(horizontal='center', vertical='center')
ws2.row_dimensions[1].height = 32

for col, h in enumerate(col_headers, 1):
    apply_hdr(ws2.cell(2, col), h, wrap=True)
ws2.row_dimensions[2].height = 32

sorted_matched = sorted(matched, key=lambda x: x['date'])
for i, t in enumerate(sorted_matched, 1):
    row = i + 2
    r_val  = R(t)
    pnl_val = r_val * RISK
    bg = ATTR_BG if t['match'] == 'ATTRACTION' else REV_BG
    if i % 2 == 0:
        # slight shade variation for alternating rows
        pass

    zone_str = f"{t['zone_bottom']:.3f} – {t['zone_top']:.3f}  ({'BULL' if t['japan_bull'] else 'BEAR'})  P={int(t['pristine'])}  BC={int(t['body_clean'])}"
    notes = f"{'Body-clean, ' if t['body_clean'] else 'Zone broken, '}dist: {t.get('dist_label','')}"

    apply_cell(ws2.cell(row, 1),  i,              fmt='0',            bg=bg)
    apply_cell(ws2.cell(row, 2),  t['date'],      align='center',     bg=bg)
    # Direction with colour
    dir_cell = ws2.cell(row, 3)
    dir_cell.value = t['direction']
    dir_cell.font  = Font(name='Arial', bold=True, size=10,
                          color='276221' if t['direction'] == 'LONG' else '9C0006')
    dir_cell.fill  = PatternFill('solid', fgColor='C6EFCE' if t['direction'] == 'LONG' else 'FFC7CE')
    dir_cell.alignment = Alignment(horizontal='center', vertical='center')
    dir_cell.border = thin_border()

    apply_cell(ws2.cell(row, 4),  t['match'],     bold=True, align='center', bg=bg)
    apply_cell(ws2.cell(row, 5),  t['entry_px'],  fmt='#,##0.000', bg=bg)
    apply_cell(ws2.cell(row, 6),  t['sl'],        fmt='#,##0.000', bg=bg)
    apply_cell(ws2.cell(row, 7),  t['exit_px'],   fmt='#,##0.000', bg=bg)
    apply_cell(ws2.cell(row, 8),  t['sl_gap_pts'],fmt='#,##0',     bg=bg)
    apply_cell(ws2.cell(row, 9),  t['pip_gain'],  fmt='#,##0',     bg=bg)
    apply_cell(ws2.cell(row, 10), r_val,          fmt='0.00"R"',   bg=bg)
    # P&L cell — green
    pnl_cell = ws2.cell(row, 11)
    pnl_cell.value = pnl_val
    pnl_cell.font  = Font(name='Arial', bold=True, size=10, color=WIN_FG)
    pnl_cell.fill  = PatternFill('solid', fgColor=WIN_BG)
    pnl_cell.number_format = '"$"#,##0'
    pnl_cell.alignment = Alignment(horizontal='center', vertical='center')
    pnl_cell.border = thin_border()

    apply_cell(ws2.cell(row, 12), 'Yes' if t['in_sess'] else 'No', bg=bg)
    apply_cell(ws2.cell(row, 13), t.get('dist_label',''), align='left', bg=bg)
    apply_cell(ws2.cell(row, 14), zone_str, align='left', bg=bg)
    apply_cell(ws2.cell(row, 15), notes, align='left', bg=bg, wrap=True)
    ws2.row_dimensions[row].height = 18

# Totals row
tot_row = len(sorted_matched) + 3
ws2.merge_cells(f'A{tot_row}:I{tot_row}')
apply_hdr(ws2[f'A{tot_row}'], f'TOTAL  ({len(matched)} trades)', bg=HDR_BG)
apply_hdr(ws2.cell(tot_row, 10), f'{avg_r:.2f}R avg', bg=HDR_BG)
apply_hdr(ws2.cell(tot_row, 11), f'${total_pnl:,.0f}', bg='276221')
for col in range(12, 16):
    apply_hdr(ws2.cell(tot_row, col), '', bg=HDR_BG)
ws2.row_dimensions[tot_row].height = 22

# ══════════════════════════════════════════════════════════════════════════════
# SHEET 3 — FULL RESULTS (all 50)
# ══════════════════════════════════════════════════════════════════════════════
ws3 = wb.create_sheet('All 50 Trades')
ws3.sheet_view.showGridLines = False
ws3.freeze_panes = 'A3'

col_w3 = [6, 12, 8, 16, 10, 10, 10, 10, 28]
col_h3 = ['#', 'Date', 'Direction', 'Classification', 'Entry', 'SL', 'Exit', 'Pip Gain', 'Zone / Notes']
for i, w in enumerate(col_w3, 1):
    ws3.column_dimensions[get_column_letter(i)].width = w

ws3.merge_cells('A1:I1')
c = ws3['A1']
c.value = 'DXY Zone Strategy — All 50 Backtested Trades'
c.font = Font(name='Arial', bold=True, size=14, color=HDR_FG)
c.fill = PatternFill('solid', fgColor=TITLE_BG)
c.alignment = Alignment(horizontal='center', vertical='center')
ws3.row_dimensions[1].height = 32

for col, h in enumerate(col_h3, 1):
    apply_hdr(ws3.cell(2, col), h)
ws3.row_dimensions[2].height = 20

cat_bg = {
    'ATTRACTION':           ATTR_BG,
    'REVERSAL':             REV_BG,
    'MANUAL_CHECK':         MANUAL_BG,
    'TOO_CLOSE_TO_ZONE':    OTHER_BG,
    'BODY_IN_ZONE_NO_ATTR': OTHER_BG,
}

for i, t in enumerate(sorted(results, key=lambda x: x['date']), 1):
    row = i + 2
    bg = cat_bg.get(t['match'], ALT_BG)

    apply_cell(ws3.cell(row, 1), i, fmt='0', bg=bg)
    apply_cell(ws3.cell(row, 2), t['date'], bg=bg)
    dir_cell = ws3.cell(row, 3)
    dir_cell.value = t['direction']
    dir_cell.font  = Font(name='Arial', bold=True, size=10,
                          color='276221' if t['direction']=='LONG' else '9C0006')
    dir_cell.fill  = PatternFill('solid', fgColor='C6EFCE' if t['direction']=='LONG' else 'FFC7CE')
    dir_cell.alignment = Alignment(horizontal='center', vertical='center')
    dir_cell.border = thin_border()

    apply_cell(ws3.cell(row, 4), t['match'], bold=True, bg=bg)
    apply_cell(ws3.cell(row, 5), t['entry_px'], fmt='#,##0.000', bg=bg)
    apply_cell(ws3.cell(row, 6), t.get('sl', ''), fmt='#,##0.000', bg=bg)
    apply_cell(ws3.cell(row, 7), t.get('exit_px', ''), fmt='#,##0.000', bg=bg)
    apply_cell(ws3.cell(row, 8), t.get('pip_gain', ''), fmt='#,##0', bg=bg)

    if t.get('zone_found'):
        note = (f"Zone: {t['zone_bottom']:.3f}–{t['zone_top']:.3f}  "
                f"({'BULL' if t['japan_bull'] else 'BEAR'})  "
                f"P={int(t['pristine'])} BC={int(t['body_clean'])}  |  {t.get('dist_label','')}")
    else:
        note = 'No zone data — manual chart review required'
    apply_cell(ws3.cell(row, 9), note, align='left', bg=bg, wrap=True)
    ws3.row_dimensions[row].height = 18

wb.save(OUT)
print(f'Saved: {OUT}')
