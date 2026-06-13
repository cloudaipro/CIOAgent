# Alpha Hunter Proposal

### Objective
建立一套可重複執行、可驗證、可持續改善的 NASDAQ 波段選股系統，結合市場環境、板塊輪動、基本面品質、獲利動能與價格動能，產生高品質觀察名單。

---

# 1. Product Vision

## Mission

Alpha Hunter 不預測市場，而是在有利環境中尋找市場正在重新定價的強勢股票。

## Core Principles

### Principle 1 – Market First

Market → Sector → Stock

### Principle 2 – Trade Strength

只交易相對強勢股票。

### Principle 3 – Capital Preservation

保護資本優先於追求獲利。

### Principle 4 – Process Over Prediction

流程優於預測。

### Principle 5 – Earnings Drive Price

股價長期由未來獲利預期驅動。

---

# 2. Target User

## Primary User

- 美股投資人
- NASDAQ 為主
- 波段交易
- 持有週期 2 週至 6 個月

## Out of Scope

- 當沖
- 高頻交易
- 選擇權策略
- 純價值投資

---

# 3. Product Scope

## Included

### Layer 0 – Market Regime

判斷市場燈號：

- GREEN
- YELLOW
- RED

### Layer 1 – Sector Rotation

板塊相對強度排名。

### Layer 2 – Quality Filter

排除低品質股票。

### Layer 2.5 – Earnings Engine

分析未來獲利成長與獲利動能。

### Layer 3 – Momentum Engine

分析價格與趨勢動能。

### Layer 4 – Watchlist Generator

輸出候選名單。

## Excluded

- 自動下單
- AI Agent
- 即時交易
- 選擇權分析
- 財報摘要生成

---

# 4. System Architecture

Market Regime
↓
Sector Ranking
↓
Quality Filter
↓
Earnings Engine
↓
Momentum Engine
↓
Candidate Ranking
↓
Watchlist

---

# 5. Functional Requirements

## FR-001 Market Regime

### Inputs

QQQ 日線資料

### Indicators

- 50MA
- 200MA
- 50MA Slope

### Rules

#### GREEN

- QQQ > 50MA
- 50MA > 200MA
- 50MA 向上

#### YELLOW

- 跌破 50MA
- 連續 3 日確認或跌破超過 1.5%

#### RED

- QQQ < 200MA

### Output

Market Regime Status

---

## FR-002 Sector Ranking

### Sector Universe

- QQQ
- SMH
- IGV
- HACK
- BOTZ

### Metrics

- 3M Return
- 6M Return

### Formula

RS Score = 0.5 × 3M Return + 0.5 × 6M Return

### Output

Sector Ranking

---

## FR-003 Quality Filter

### Minimum Criteria

- Market Cap > 2B USD
- Average Daily Dollar Volume > 50M USD
- Revenue Growth > 15%
- Forward EPS Growth > 15%
- Free Cash Flow > 0

### Result

PASS / FAIL

---

## FR-003A Earnings Engine

### Purpose

尋找市場正在上修未來獲利預期的公司。

### Components

#### A. Forward EPS Growth (40%)

最低要求：

Forward EPS Growth > 15%

#### B. EPS Revision Signal (40%)

Lite Mode：

- Earnings Gap Up > 5%
- 10 個交易日內未回補缺口

Pro Mode：

- Analyst Consensus Revision

#### C. Earnings Surprise (20%)

最近四季 Beat Ratio：

- 4/4 = 100
- 3/4 = 75
- 2/4 = 50
- 1/4 = 25
- 0/4 = 0

### Output

- Earnings Score
- Forward EPS Growth
- EPS Revision Signal
- Earnings Surprise Score

---

## FR-004 Momentum Engine

### Relative Strength

條件：

- 3M Return > QQQ
- 6M Return > QQQ

### Trend Template

- Price > 50MA
- 50MA > 150MA
- 150MA > 200MA

### Output

Momentum Score

---

## FR-005 Candidate Ranking

### Weighting

- Momentum Score = 30%
- Trend Score = 20%
- Earnings Score = 30%
- Revenue Growth = 10%
- Volume Expansion = 10%

### Formula

Final Score =
0.30 × Momentum +
0.20 × Trend +
0.30 × Earnings +
0.10 × Revenue +
0.10 × Volume

---

## FR-006 Watchlist Generator

### Output

Top 20 Candidates

### Fields

- Ticker
- Sector
- Market Regime
- Momentum Score
- Trend Score
- Earnings Score
- Revenue Growth
- Forward EPS Growth
- Earnings Surprise
- Final Score

---

# 6. Risk Management Rules

## Position Risk

單筆風險：

- Account Risk = 1%

## Position Cap

單一持股：

- Max Position Size = 15%

取較小值。

## Theme Exposure

單一主題：

- Max Exposure = 30%

---

# 7. Trading Rules

## Entry

### Breakout

- 平台整理 ≥ 4 週
- 放量突破
- Volume > 1.5 × 20D Average

### Pullback

- 回測 50MA
- 量縮
- 止跌訊號

## Exit

### Risk Management

初始停損：

- 型態低點下方

### Profit Taking

達 2R：

- 賣出 1/3
- Stop 移至成本

### Trend Exit

- 跌破 20MA
- 或跌破 50MA

---

# 8. Weekly Workflow

1. 更新 Market Regime
2. 更新 Sector Ranking
3. 執行 Quality Filter
4. 執行 Earnings Engine
5. 執行 Momentum Engine
6. 更新 Watchlist
7. 標記財報與催化劑

時間目標：

每週 1–2 小時

---

# 9. Post-Trade Review

每筆交易紀錄：

- Entry Reason
- Exit Reason
- Risk (R)
- Profit/Loss
- 是否符合規則
- 情緒狀態

## Metrics

- Win Rate
- Average R
- Profit Factor
- Max Drawdown
- Rule Compliance

---




最終目標：

建立一套能系統化發現市場正在重新定價股票的研究引擎。
