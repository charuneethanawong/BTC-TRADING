import os

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py'

# The content I want to ensure is there, fixed.
# I'll read the current content (handling the garbled mess)
encodings = ['utf-8-sig', 'utf-16le', 'utf-8', 'cp1252']
content = None
for enc in encodings:
    try:
        with open(file_path, 'r', encoding=enc) as f:
            content = f.read()
            if 'class IOFAnalyzer' in content:
                break
    except:
        continue

if not content:
    print("Could not read content.")
    exit(1)

# Fix the internal newlines if they are missing (common if mixed up)
# If it's all one line, we need to be careful.
# But looking at the tool output, it seems it HAS newlines but the tool showed it as line 1? 
# Actually, the tool output showed "1: ..." followed by many lines. 
# This happens if there's a weird character at the start.

# Let's just normalize the content and fix the OS block.
import re

# Fix OS block to use momentum_strength
os_pattern = r"elif m5_oversold:.*?(self\.logger\.info\(.*?Gate 2b: REVERSAL_OS.*? \))"
os_replacement = """elif m5_oversold:
            # v17.1: Momentum Strength = DER * PriceMove/ATR (only if aligned)
            der_aligned = (
                (raw_direction == 'SHORT' and price_move_signed < 0) or
                (raw_direction == 'LONG' and price_move_signed > 0)
            )
            momentum_strength = der * price_move_atr if der_aligned else 0
            
            # v17.1: strength >= 0.5 + same direction = momentum still strong
            if momentum_strength >= 0.5:
                # Momentum still strong -> don't reverse
                if der >= self.der_min:
                    direction = 'SHORT'
                    signal_type = 'MOMENTUM'
                    self.logger.info(
                        f"{self.log_prefix} Gate 2b: MOMENTUM (OS but DER {der:.3f} SHORT strong) -> SHORT"
                    )
                else:
                    return None
            else:
                # DER < 0.5 OR DER opposite direction -> reversal
                direction = 'LONG'
                signal_type = 'REVERSAL_OS'
                self.logger.info(
                    f"{self.log_prefix} Gate 2b: REVERSAL_OS | M5 OS DER:{der:.3f} raw_dir:{raw_direction} -> LONG"
                )"""

# Also fix the header if it's garbled
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

# Replace the garbled start (everything before 'from dataclasses')
content = re.sub(r"^.*?from dataclasses", "from dataclasses", content, flags=re.DOTALL)
content = header_fixed + content

# Replace the OS block
# Note: This regex might be tricky if formatting is slightly different.
# Let's use a more robust replacement.
target_os = """        elif m5_oversold:
            # v17.1: Check DER strength - DER >= 0.5 + same direction as impulse = momentum still strong
            if der >= 0.5 and raw_direction == 'SHORT':
                # DER strong SHORT + M5 OS -> momentum still strong -> don't reverse
                if der >= self.der_min:
                    direction = 'SHORT'
                    signal_type = 'MOMENTUM'
                    self.logger.info(
                        f"{self.log_prefix} Gate 2b: MOMENTUM (OS but DER {der:.3f} SHORT strong) -> SHORT"
                    )
                else:
                    return None
            else:
                # DER < 0.5 OR DER opposite direction -> reversal
                direction = 'LONG'
                signal_type = 'REVERSAL_OS'
                self.logger.info(
                    f"{self.log_prefix} Gate 2b: REVERSAL_OS | M5 OS DER:{der:.3f} raw_dir:{raw_direction} -> LONG"
                )"""

if target_os in content:
    content = content.replace(target_os, os_replacement)
    print("Successfully replaced OS block.")
else:
    print("Could not find OS block to replace. Attempting regex...")
    content = re.sub(os_pattern, os_replacement, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
    f.write(content)

print("Final cleanup and v17.1 refinement complete.")
