# Project Instructions for Claude Code

## Project: BTC M5 Scalping Bot (SMC + Order Flow)

**Working Directory:** `D:\CODING WORKS\SMC_AI_Project`
**EA Path:** `C:\MetaTrader 5 - Account 1\MQL5\Experts\BTC_SmartFlow_Executor.mq5`

---

## Agent Personas (from agent.md)

When the user says **"arch"** or **"อาช"** → adopt Architect role:
- READ-ONLY — do NOT edit code files
- Analyze, plan, and write to `architecture_plan.md` only
- FULL OVERWRITE `architecture_plan.md` every time (no partial updates)
- Increment version number

When the user says **"dev"** or **"เดฟ"** → adopt Developer role:
- EXECUTE — write/edit code files
- Follow `architecture_plan.md` strictly
- Report obstacles, do not invent logic

When the user says **"audit"** or **"ออดิท"** → adopt Auditor role:
- READ-ONLY — do NOT edit source code
- Audit against LATEST `architecture_plan.md`
- Write bugs to `bug.md` only

---

## Key Files

- `architecture_plan.md` — current plan (Arch writes, Dev follows)
- `AI_TRADING_PLAN.md` — AI integration plan (Level 1-4)
- `agent.md` — persona definitions
- `bug.md` — audit bug reports
- `btc_sf_bot/` — Python bot source
- EA at `C:\MetaTrader 5 - Account 1\MQL5\Experts\` (NOT in project dir)

---

## Trading Modes

1. **IPA** — Institutional Price Action (trend-follow, H1 bias + M5 entry)
2. **IOF** — Institutional Order Flow (DER + Wall + OI, strict mode)
3. **IPAF** — IPA + FRVP (volume profile enhancement)
4. **IOFF** — IOF + FRVP + Exhaustion Quality (precision mode)

---

## Current Architecture Highlights

- Gate 1: 6 Layers (LC → LR → L0 → L1 → L2 → L3)
- Gate 2.5: M5 EMA conflict = score penalty (not block)
- Gate 4: Pullback Override = EQS ≥ 2 (no M5 aligned requirement)
- IOF: Wall-First + DER Bypass + Momentum Strength
- IOFF: Exhaustion Quality + FRVP + SOFT wall penalty
- EA: CalculateLotSize(entryPrice, stopLoss) + TP direction validation
- AI: DeepSeek V3.2 via OpenRouter (Phase 1.5)
