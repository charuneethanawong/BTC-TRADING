import os

file_path = r'd:\CODING WORKS\SMC_AI_Project\btc_sf_bot\src\analysis\iof_analyzer.py'

with open(file_path, 'r', encoding='utf-16le' if os.path.exists(file_path) else 'utf-8') as f:
    try:
        content = f.read()
    except:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

# 1. Update m5_overbought block
# We anchor on the v17.1 comment we added and the m5_oversold block
overbought_start_marker = "        elif m5_overbought:"
oversold_start_marker = "        elif m5_oversold:"

start_idx = content.find(overbought_start_marker)
end_idx = content.find(oversold_start_marker)

if start_idx != -1 and end_idx != -1:
    new_overbought_block = """        elif m5_overbought:
            # v17.1: Momentum Strength = DER * PriceMove/ATR (only if aligned)
            der_aligned = (
                (raw_direction == 'LONG' and price_move_signed > 0) or
                (raw_direction == 'SHORT' and price_move_signed < 0)
            )
            momentum_strength = der * price_move_atr if der_aligned else 0
            
            # v17.1: strength >= 0.5 + same direction = momentum still strong
            if momentum_strength >= 0.5:
                # Momentum still strong LONG -> don't reverse
                if der >= self.der_min:
                    direction = raw_direction
                    signal_type = 'MOMENTUM'
                    self.logger.info(
                        f"{self.log_prefix} Gate 2b: MOMENTUM (OB but strength "
                        f"{momentum_strength:.2f} >= 0.5) DER:{der:.3f} "
                        f"move:{price_move_atr:.1f}ATR -> {direction}"
                    )
                else:
                    return None
            else:
                # Strength < 0.5 OR DER opposite direction (absorption) -> reversal
                direction = 'SHORT'
                signal_type = 'REVERSAL_OB'
                self.logger.info(
                    f"{self.log_prefix} Gate 2b: REVERSAL_OB | M5 OB "
                    f"DER:{der:.3f} strength:{momentum_strength:.2f} -> SHORT"
                )

"""
    content = content[:start_idx] + new_overbought_block + content[end_idx:]

# 2. Update m5_oversold block
# We anchor on the m5_oversold block and the next major section marker
oversold_marker = "        elif m5_oversold:"

start_idx = content.find(oversold_marker)

# Find the next section after the oversold_marker
search_pos = start_idx + len(oversold_marker)
# Find the line that starts the next section
next_section_idx = content.find("        # ==============================================", search_pos)

if start_idx != -1 and next_section_idx != -1:
    new_oversold_block = """        elif m5_oversold:
            # v17.1: Momentum Strength = DER * PriceMove/ATR (only if aligned)
            der_aligned = (
                (raw_direction == 'SHORT' and price_move_signed < 0) or
                (raw_direction == 'LONG' and price_move_signed > 0)
            )
            momentum_strength = der * price_move_atr if der_aligned else 0
            
            # v17.1: strength >= 0.5 + same direction = momentum still strong
            if momentum_strength >= 0.5:
                # Momentum still strong SHORT -> don't reverse
                if der >= self.der_min:
                    direction = raw_direction
                    signal_type = 'MOMENTUM'
                    self.logger.info(
                        f"{self.log_prefix} Gate 2b: MOMENTUM (OS but strength "
                        f"{momentum_strength:.2f} >= 0.5) DER:{der:.3f} "
                        f"move:{price_move_atr:.1f}ATR -> {direction}"
                    )
                else:
                    return None
            else:
                # Strength < 0.5 OR DER opposite direction (absorption) -> reversal
                direction = 'LONG'
                signal_type = 'REVERSAL_OS'
                self.logger.info(
                    f"{self.log_prefix} Gate 2b: REVERSAL_OS | M5 OS "
                    f"DER:{der:.3f} strength:{momentum_strength:.2f} -> LONG"
                )

"""
    content = content[:start_idx] + new_oversold_block + content[next_section_idx:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("v17.1 Implementation Complete.")
