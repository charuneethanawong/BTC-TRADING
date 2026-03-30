You are an expert BTC scalping bot analyst. Analyze deeply:
1. Overall Performance (WR, PnL, profit factor)
2. BE Analysis (BE count vs real TP, is BE trigger too early?)
3. Mode Performance (IPA/IOF/IPAF/IOFF)
4. Signal Type (MOMENTUM/ABSORPTION/REVERSAL/MEAN_REVERT)
5. Session (ASIA/LONDON/NY)
6. AI Accuracy (aligned vs conflict WR)
7. Loss Patterns
8. SL/TP Efficiency
9. Give 3-5 actionable recommendations
Be data-driven. Brutally honest.

Trade Log Analysis (BTC M5 Scalping Bot):
Total: 163 trades | 113W 50L | WR: 69.3% | PnL: $101.10
Profit Factor: 1.65 | Avg Win: $2.27 | Avg Loss: $-3.11

BE Analysis:
  BE exits (WIN <$1): 78/113 wins (69%)
  Real TP wins (>=$1): 35/113 (31%)
  Avg BE PnL: $0.39
  Avg Real TP PnL: $6.46

Per Mode:
  IOF_FRVP: 38W 17L WR:69% PnL:$-15.68
  IPA_FRVP: 47W 23L WR:67% PnL:$53.08
  IPA: 18W 8L WR:69% PnL:$55.40
  IOF: 10W 2L WR:83% PnL:$8.30

Per Signal Type:
  MOMENTUM: 38W 15L WR:72% PnL:$4.00
  IPA_FRVP: 47W 23L WR:67% PnL:$53.08
  IPA: 18W 8L WR:69% PnL:$55.40
  ABSORPTION: 10W 2L WR:83% PnL:$-4.40
  REVERSAL_OS: 0W 2L WR:0% PnL:$-6.98

AI Alignment:
  Aligned+WIN: 57 | Aligned+LOSS: 34 | Aligned WR: 63%
  Conflict+WIN: 30 | Conflict+LOSS: 6 | Conflict WR: 83%

Per Session:
  NY: 22W 12L WR:65% PnL:$-27.76
  ASIA: 56W 28L WR:67% PnL:$87.05
  LONDON: 35W 10L WR:78% PnL:$41.81

Last 5 LOSS trades:
  IPAF_SHORT_183924 | IPA_FRVP | PnL:$-5.98 | AI:BEARISH vs Bot:SHORT | exit:SL
  IPAF_SHORT_184332 | IPA_FRVP | PnL:$-5.87 | AI:BEARISH vs Bot:SHORT | exit:SL
  IOFF_MOMENTUM_SHORT_185618 | MOMENTUM | PnL:$-3.39 | AI:BEARISH vs Bot:SHORT | exit:SL
  IOFF_MOMENTUM_SHORT_185822 | MOMENTUM | PnL:$0.00 | AI:BEARISH vs Bot:SHORT | exit:STALE_CLEANUP
  IPAF_SHORT_193043 | IPA_FRVP | PnL:$0.00 | AI:BEARISH vs Bot:SHORT | exit:STALE_CLEANUP

Last 5 BE trades (WIN <$1):
  IOFF_MOMENTUM_LONG_180632 | PnL:$0.28 | SL_dist:$360 | TP_dist:$649
  IOFF_MOMENTUM_LONG_182022 | PnL:$0.26 | SL_dist:$342 | TP_dist:$615
  IOFF_MOMENTUM_LONG_193701 | PnL:$0.51 | SL_dist:$296 | TP_dist:$533
  IOF_ABSORPTION_LONG_204745 | PnL:$0.59 | SL_dist:$284 | TP_dist:$512
  IOFF_ABSORPTION_LONG_204745 | PnL:$0.59 | SL_dist:$284 | TP_dist:$512
