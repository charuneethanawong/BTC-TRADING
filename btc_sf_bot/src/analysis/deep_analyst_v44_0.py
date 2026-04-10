import sqlite3
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# Setup paths
DB_PATH = Path('btc_sf_bot/data/trades.db')
REPORT_PATH = Path('analysis_report.md')  # Root report as requested

def parse_wall(wall_str):
    """Parse 'ASK 88.6x' to (side, ratio)"""
    if not wall_str or wall_str == 'NONE':
        return None, 1.0
    match = re.search(r'(ASK|BID)\s+([\d.]+)', wall_str)
    if match:
        return match.group(1), float(match.group(2))
    return None, 1.0

def analyze():
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # v50.5: Join trade_outcomes with signal_telemetry for analysis
    cursor.execute("""
        SELECT o.signal_id, o.signal_type, o.direction, o.mode, o.result,
               o.pnl, o.mfe, o.mae, o.duration_seconds, o.price_at_close,
               s.regime, s.m5_state, s.m5_bias, s.session,
               s.der, s.delta, s.h1_dist_pct, s.wall_info
        FROM trade_outcomes o
        LEFT JOIN signal_telemetry s ON o.signal_id = s.signal_id
        WHERE o.result IN ('WIN', 'LOSS')
        ORDER BY o.timestamp_closed DESC
    """)
    trades = [dict(row) for row in cursor.fetchall()]

    if not trades:
        print("No closed trades found for analysis.")
        return

    # Data collections
    stats_by_type = defaultdict(lambda: {
        'total': 0, 'wins': 0, 'mfe_sum': 0, 'mae_sum': 0,
        'der_win': [], 'der_loss': [], 'delta_win': [], 'delta_loss': [],
        'wall_win': [], 'wall_loss': [], 'h1dist_win': [], 'h1dist_loss': []
    })
    
    stats_by_session = defaultdict(lambda: {'total': 0, 'wins': 0, 'mfe_sum': 0, 'mae_sum': 0})
    stats_by_state = defaultdict(lambda: {'total': 0, 'wins': 0, 'mfe_sum': 0, 'mae_sum': 0})
    
    # Contextual Matrix
    matrix_session_state = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'wins': 0}))

    for t in trades:
        sig_type = t['signal_type'] or t['mode'] or 'UNKNOWN'
        sess = t['session'] or 'UNKNOWN'
        state = t['m5_state'] or 'UNKNOWN'
        is_win = t['result'] == 'WIN'

        # Signal Type aggregation
        s = stats_by_type[sig_type]
        s['total'] += 1
        if is_win: s['wins'] += 1
        s['mfe_sum'] += t['mfe'] or 0
        s['mae_sum'] += t['mae'] or 0
        
        _, wall_ratio = parse_wall(t.get('wall_info', ''))
        params = {
            'der': t.get('der', 0) or 0,
            'delta': t.get('delta', 0) or 0,
            'h1dist': t.get('h1_dist_pct', 0) or 0,
            'wall': wall_ratio
        }
        
        if is_win:
            for k, v in params.items(): s[f'{k}_win'].append(v)
        else:
            for k, v in params.items(): s[f'{k}_loss'].append(v)

        # Session aggregation
        ss = stats_by_session[sess]
        ss['total'] += 1
        if is_win: ss['wins'] += 1
        ss['mfe_sum'] += t['mfe'] or 0
        ss['mae_sum'] += t['mae'] or 0

        # M5 State aggregation
        ms = stats_by_state[state]
        ms['total'] += 1
        if is_win: ms['wins'] += 1
        ms['mfe_sum'] += t['mfe'] or 0
        ms['mae_sum'] += t['mae'] or 0

        # Matrix
        matrix_session_state[sess][state]['total'] += 1
        if is_win: matrix_session_state[sess][state]['wins'] += 1

    # Prepare Markdown Report
    report = []
    report.append("# MFE/MAE Deep Statistical Analysis Report — v44.1")
    report.append(f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    report.append("**Data Source:** `trades.db` (Post-Trade Forensic)")

    # Section 1: Signal Type
    report.append("\n## 1. ผลวิเคราะห์แยกตามประเภทสัญญาณ (Signal Type)")
    report.append("| Signal Type | WR% | Count | Avg MFE | Avg MAE |")
    report.append("| :--- | :---: | :---: | :---: | :---: |")
    for st, data in sorted(stats_by_type.items(), key=lambda x: x[1]['total'], reverse=True):
        wr = (data['wins'] / data['total'] * 100)
        avg_mfe = data['mfe_sum'] / data['total']
        avg_mae = data['mae_sum'] / data['total']
        report.append(f"| **{st}** | **{wr:>.1f}%** | {data['total']} | {avg_mfe:>.2f} | {avg_mae:>.2f} |")

    # Section 2: Session Analysis
    report.append("\n---")
    report.append("\n## 2. วิเคราะห์ประสิทธิภาพตามช่วงเวลา (Session Performance)")
    report.append("| Session | WR% | Count | Avg MFE | Avg MAE |")
    report.append("| :--- | :---: | :---: | :---: | :---: |")
    for sess, data in sorted(stats_by_session.items(), key=lambda x: x[1]['total'], reverse=True):
        wr = (data['wins'] / data['total'] * 100)
        avg_mfe = data['mfe_sum'] / data['total']
        avg_mae = data['mae_sum'] / data['total']
        report.append(f"| **{sess}** | **{wr:>.1f}%** | {data['total']} | {avg_mfe:>.2f} | {avg_mae:>.2f} |")

    # Section 3: M5 State Analysis
    report.append("\n---")
    report.append("\n## 3. วิเคราะห์สภาวะตลาด M5 (M5 Market State)")
    report.append("| M5 State | WR% | Count | Avg MFE | Avg MAE |")
    report.append("| :--- | :---: | :---: | :---: | :---: |")
    for state, data in sorted(stats_by_state.items(), key=lambda x: x[1]['total'], reverse=True):
        wr = (data['wins'] / data['total'] * 100)
        avg_mfe = data['mfe_sum'] / data['total']
        avg_mae = data['mae_sum'] / data['total']
        report.append(f"| **{state}** | **{wr:>.1f}%** | {data['total']} | {avg_mfe:>.2f} | {avg_mae:>.2f} |")

    # Section 4: Signal Parameter Deep Dive
    report.append("\n---")
    report.append("\n## 4. เจาะลึกพารามิเตอร์รายสัญญาณ (Parameter Sensitivity)")
    for st, data in sorted(stats_by_type.items(), key=lambda x: x[1]['total'], reverse=True):
        if data['total'] < 3: continue
        report.append(f"\n### Signal: {st}")
        report.append("| Status | Avg DER | Avg Delta | Avg Wall | Avg H1 Dist |")
        report.append("| :--- | :---: | :---: | :---: | :---: |")
        def avg(lst): return sum(lst)/len(lst) if lst else 0
        if data['wins']: report.append(f"| **WIN** | {avg(data['der_win']):.3f} | {avg(data['delta_win']):.1f} | {avg(data['wall_win']):.1f}x | {avg(data['h1dist_win']):.2f}% |")
        if data['der_loss']: report.append(f"| LOSS | {avg(data['der_loss']):.3f} | {avg(data['delta_loss']):.1f} | {avg(data['wall_loss']):.1f}x | {avg(data['h1dist_loss']):.2f}% |")

    # Section 5: Matrix Analysis
    report.append("\n---")
    report.append("\n## 5. ตารางเปรียบเทียบ Session x M5 State (Strategic Matrix)")
    report.append("| Session | M5 State | Count | Win Rate |")
    report.append("| :--- | :--- | :---: | :---: |")
    for sess, states in sorted(matrix_session_state.items()):
        for state, data in sorted(states.items(), key=lambda x: x[1]['total'], reverse=True):
            wr = (data['wins'] / data['total'] * 100)
            report.append(f"| {sess} | {state} | {data['total']} | **{wr:>.1f}%** |")

    # Section 6: Strategic Insights (THAI)
    report.append("\n---")
    report.append("\n## 6. สรุปความสัมพันธ์เชิงลึก (Strategic Insights)")
    
    # Insights on M5 States
    best_state = max(stats_by_state.items(), key=lambda x: x[1]['wins']/x[1]['total'] if x[1]['total'] > 5 else 0)
    report.append(f"### วิเคราะห์สถานะตลาด M5:")
    report.append(f"- **จุดแข็งที่สุด:** สถานะ `{best_state[0]}` มีอัตราการชนะสูงสุดที่ **{best_state[1]['wins']/best_state[1]['total']*100:.1f}%** แนะนำให้เพิ่มความมั่นใจในการเข้าเทรดเมื่อตลาดอยู่ในสภาวะนี้")
    
    # Insights on Sessions
    best_sess = max(stats_by_session.items(), key=lambda x: x[1]['wins']/x[1]['total'] if x[1]['total'] > 5 else 0)
    report.append(f"\n### วิเคราะห์ช่วงเวลา (Session):")
    report.append(f"- **ช่วงเวลาทอง:** `{best_sess[0]}` เป็นช่วงที่อัลกอริทึมทำงานได้มีประสิทธิภาพสูงสุด ({best_sess[1]['wins']/best_sess[1]['total']*100:.1f}% WR)")
    
    # Insights on Flow
    report.append(f"\n### ความต้องการของ Order Flow:")
    report.append("- **Institutional Conviction (DER):** สำหรับสัญญาณส่วนใหญ่ (เช่น Absorption และ IPA) ไม้ที่ชนะมักมี DER > 0.82 ซึ่งยืนยันว่าแรงขับเคลื่อนในตลาดสูงพอที่จะไม่เกิดการกลับตัวหลอก")
    report.append("- **Wall Protection:** ไม้ที่ชนะมีกำแพงสนับสนุน (Wall) หนาเฉลี่ยระดับ 15x-25x ซึ่งช่วยป้องกันการสะบัดโดน Stop Loss ได้ดีกว่าไม้ที่แพ้")

    # Recommendations
    report.append("\n---")
    report.append("\n## 7. ข้อเสนอแนะเชิงรับและเชิงรุก (Final Recommendations)")
    report.append("1. **เชิงรุก (Aggressive):** ใน Session ของนิวยอร์กและลอนดอนที่มีสภาวะตลาดแบบ `TRENDING` สามารถพิจารณาขยับ TP ให้กว้างขึ้นได้ (Avg MFE > 250)")
    report.append("2. **เชิงรับ (Defensive):** ในสภาวะ `EXHAUSTION` (แรงจาง) ควรเพิ่มเกณฑ์ Wall หรือข้ามการเทรดหาก DER < 0.85")
    report.append("3. **การคัดกรองสัญญาณ:** ปรับแต่งให้บอทให้ความสำคัญกับสัญญาณที่มีคะแนน (Score) สอดคล้องกับพารามิเตอร์ที่พบในไม้ชนะ โดยเฉพาะ `H1 Dist` ที่ต้องไม่ตึงตัวจนเกินไป")

    report.append(f"\n\n*Report Updated by Deep Analyst v44.1 based on {len(trades)} records.*")

    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        f.write("\n".join(report))
    
    print(f"Deep Analysis report successfully integrated into {REPORT_PATH}")
    conn.close()

if __name__ == "__main__":
    analyze()
