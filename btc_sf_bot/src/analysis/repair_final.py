import os
import re

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py'

header_fixed = """// IOF Analyzer - Institutional Order Flow v5.0 - Aggressive Mode
// Logic Flow (Section 4.2 of Architecture Plan):
// Gate 1: Market Regime (NOT extreme trending, ADX < 40)
// Gate 2: Delta Absorption (DER > 0.3, Volume > 1.0x average)
// Gate 3: IO Signal (SOFT GATE - OI change > 0.1%, direction opposite to price)
// Gate 4: Order Book Wall (size > session threshold, within 0.5% of price)
// Gate 5: M5 Rejection Candle (wick or close rejection at wall level)

Scoring (Section 4.3 – max 20 points):
  Delta Absorption Quality (max 7):
    + 5  DER > 3.0 (Strong absorption)
    + 4  DER 2.0–3.0 (Moderate)
    + 3  DER 1.5–2.0 (Weak)
    + 2  Volume Surge > 2.0x
    + 1  Volume Surge 1.2–2.0x
  OI & Funding Signal (max 6):
    + 3  OI Divergence > 0.3% (opposite to price)
    + 2  OI Divergence 0.1–0.3%
    + 2  Funding Rate Extreme (> |0.05%|) opposite to price
    + 1  Funding Rate Moderate (0.02–0.05%)
  Wall Quality (max 5):
    + 3  Wall > $1M + Refill confirmed
    + 2  Wall $500K–$1M + Stable
    + 1  Wall $300K–$500K + Stable (ASIA only)
    + 1  Wall stability > 60 seconds
  Confirmation (max 2):
    + 1  Liquidation cascade opposite direction
    + 1  M5 rejection candle at wall level

Score Threshold: >= 9 -> Signal (v5.0 Aggressive)
\"\"\"
"""

# Read current content
with open(file_path, 'r', encoding='utf-8-sig') as f:
    full_content = f.read()

# Find the implementation start
# It should be around the first "from dataclasses"
match = re.search(r"from dataclasses", full_content)
if match:
    implementation = full_content[match.start():]
    new_content = header_fixed + implementation
    
    with open(file_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write(new_content)
    print("Repaired file structure successfully.")
else:
    print("Could not find implementation start.")
