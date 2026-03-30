import os

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py'

# Attempt to read the file in binary and decode with various encodings
encodings = ['utf-8-sig', 'utf-16le', 'utf-8', 'cp1252', 'latin-1']
valid_content = None

with open(file_path, 'rb') as f:
    raw_data = f.read()

for enc in encodings:
    try:
        decoded = raw_data.decode(enc)
        if 'class IOFAnalyzer' in decoded:
            print(f"Salvaged with {enc}")
            # Basic cleanup: remove extra headers if present
            # Find the LAST occurrence of the header part if it was duplicated
            parts = decoded.split('from dataclasses import')
            if len(parts) > 1:
                # Take the last one as it's most likely the repaired one
                valid_content = "from dataclasses import" + parts[-1]
            else:
                valid_content = decoded
            break
    except:
        continue

if valid_content:
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
    # Ensure it's not all one line (fix newlines if they are garbled as \r\n characters in text)
    # If the file came from MT5, it might have \r\n as literal chars in some views or real escapes
    
    with open(file_path, 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write(header_fixed + valid_content)
    print("Restore complete.")
else:
    print("Salvage failed.")
