# Forward journal report

Scored from `journal/recommendations.csv` with prices through 2026-10-15.

Outcome of a recommendation: the symbol's return from the next session's open to the close 5 or 21 sessions later, minus SPY's return over the same window. Outcomes are averaged within each day first (symbols move together), and only days at least one horizon apart are used, so every observation is independent. Each experiment is scored on its own. Outcomes are before trading costs and taxes, and SPY's own rows are left out (against itself its excess is always zero).

## Experiment `c19ae262d0b0`

7 sessions recorded (2026-10-05 to 2026-10-15), 56 rows; code version(s) `b49dc0a0f680`, `1f0b53c4f97d`.

### 5-session horizon

28 of 56 rows have a finished window.

| Label | Independent days | Mean excess vs SPY | Hit rate vs SPY | t-stat |
| --- | --- | --- | --- | --- |
| Strong buy | 1 | +0.05% | 100% | – |
| Buy | 1 | +1.79% | 100% | – |
| Neutral | 1 | +1.15% | 100% | – |

| Strategy | Bullish: days | Bullish: mean excess | Not bullish: days | Not bullish: mean excess |
| --- | --- | --- | --- | --- |
| bollinger_reversion | 1 | +0.81% | 1 | +0.32% |
| donchian_breakout | 1 | -1.00% | 1 | +2.40% |
| faber_trend | 1 | +0.34% | 1 | +1.15% |
| ma_crossover | 1 | +0.34% | 1 | +1.15% |
| rsi2_reversion | 1 | +0.48% | 1 | +0.45% |
| tsmom | 1 | +0.05% | 1 | +1.47% |
| xs_momentum | 1 | +4.10% | 1 | -1.00% |

> **Too early to conclude anything.** Some rows above rest on fewer than 60 independent observations. With so few, an average can easily be large and still be luck: a t-statistic below about 2 (or 3, given how many numbers this report shows) is no evidence of skill, and even a large one can flip as more days arrive.

### 21-session horizon

0 of 56 rows have a finished window.
