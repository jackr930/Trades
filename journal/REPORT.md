# Forward journal report

Scored from `journal/recommendations.csv` with prices through 2026-11-04.

Outcome of a recommendation: the symbol's return from the next session's open to the close 5 or 21 sessions later, minus SPY's return over the same window. Outcomes are averaged within each day first (symbols move together), and only days at least one horizon apart are used, so every observation is independent. Each experiment is scored on its own. Outcomes are before trading costs and taxes, and SPY's own rows are left out (against itself its excess is always zero).

## Experiment `c19ae262d0b0`

14 sessions recorded (2026-10-05 to 2026-11-04), 112 rows; code version(s) `b49dc0a0f680`, `1f0b53c4f97d`, `d5ff50f30504`.

### 5-session horizon

63 of 112 rows have a finished window.

| Label | Independent days | Mean excess vs SPY | Hit rate vs SPY | t-stat |
| --- | --- | --- | --- | --- |
| Strong buy | 3 | -0.60% | 67% | -0.79 |
| Buy | 3 | +1.39% | 67% | 1.29 |
| Neutral | 3 | +1.92% | 100% | 4.68 |

| Strategy | Bullish: days | Bullish: mean excess | Not bullish: days | Not bullish: mean excess |
| --- | --- | --- | --- | --- |
| bollinger_reversion | 3 | -0.76% | 3 | +0.37% |
| donchian_breakout | 3 | -0.11% | 3 | +0.25% |
| faber_trend | 3 | -0.27% | 3 | +1.92% |
| ma_crossover | 3 | -0.27% | 3 | +1.92% |
| rsi2_reversion | 2 | -0.68% | 3 | +0.17% |
| tsmom | 3 | -0.32% | 3 | +1.50% |
| xs_momentum | 3 | +2.28% | 3 | -0.85% |

> **Too early to conclude anything.** Some rows above rest on fewer than 60 independent observations. With so few, an average can easily be large and still be luck: a t-statistic below about 2 (or 3, given how many numbers this report shows) is no evidence of skill, and even a large one can flip as more days arrive.

### 21-session horizon

14 of 112 rows have a finished window.

| Label | Independent days | Mean excess vs SPY | Hit rate vs SPY | t-stat |
| --- | --- | --- | --- | --- |
| Strong buy | 1 | +0.21% | 100% | – |
| Buy | 1 | +1.05% | 100% | – |
| Neutral | 1 | +8.16% | 100% | – |

| Strategy | Bullish: days | Bullish: mean excess | Not bullish: days | Not bullish: mean excess |
| --- | --- | --- | --- | --- |
| bollinger_reversion | 1 | +6.40% | 1 | -0.50% |
| donchian_breakout | 1 | -3.60% | 1 | +8.23% |
| faber_trend | 1 | +0.35% | 1 | +8.16% |
| ma_crossover | 1 | +0.35% | 1 | +8.16% |
| rsi2_reversion | 1 | +4.64% | 1 | +0.94% |
| tsmom | 1 | +0.21% | 1 | +4.61% |
| xs_momentum | 1 | +7.42% | 1 | -0.91% |

> **Too early to conclude anything.** Some rows above rest on fewer than 60 independent observations. With so few, an average can easily be large and still be luck: a t-statistic below about 2 (or 3, given how many numbers this report shows) is no evidence of skill, and even a large one can flip as more days arrive.
