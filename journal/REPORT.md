# Forward journal report

Scored from `journal/recommendations.csv` with prices through 2026-11-12.

Outcome of a recommendation: the symbol's return from the next session's open to the close 5 or 21 sessions later, minus SPY's return over the same window. Outcomes are averaged within each day first (symbols move together), and only days at least one horizon apart are used, so every observation is independent. Each experiment is scored on its own. Outcomes are before trading costs and taxes, and SPY's own rows are left out (against itself its excess is always zero).

## Experiment `c19ae262d0b0`

19 sessions recorded (2026-10-05 to 2026-11-12), 152 rows; code version(s) `b49dc0a0f680`, `1f0b53c4f97d`, `d5ff50f30504`.

### 5-session horizon

105 of 152 rows have a finished window.

| Label | Independent days | Mean excess vs SPY | Hit rate vs SPY | t-stat |
| --- | --- | --- | --- | --- |
| Strong buy | 4 | -0.82% | 50% | -1.42 |
| Buy | 4 | +1.20% | 75% | 1.52 |
| Neutral | 4 | +1.71% | 100% | 3.55 |

| Strategy | Bullish: days | Bullish: mean excess | Not bullish: days | Not bullish: mean excess |
| --- | --- | --- | --- | --- |
| bollinger_reversion | 4 | -1.21% | 4 | +0.39% |
| donchian_breakout | 4 | +0.02% | 4 | -0.26% |
| faber_trend | 4 | +0.16% | 4 | -0.13% |
| ma_crossover | 4 | -0.49% | 4 | +1.71% |
| rsi2_reversion | 3 | -1.00% | 4 | +0.24% |
| tsmom | 4 | -0.52% | 4 | +1.39% |
| xs_momentum | 4 | +1.74% | 4 | -0.94% |

> **Too early to conclude anything.** Some rows above rest on fewer than 60 independent observations. With so few, an average can easily be large and still be luck: a t-statistic below about 2 (or 3, given how many numbers this report shows) is no evidence of skill, and even a large one can flip as more days arrive.

### 21-session horizon

42 of 152 rows have a finished window.

| Label | Independent days | Mean excess vs SPY | Hit rate vs SPY | t-stat |
| --- | --- | --- | --- | --- |
| Strong buy | 1 | +0.21% | 100% | – |
| Buy | 1 | +1.05% | 100% | – |
| Neutral | 1 | +8.61% | 100% | – |

| Strategy | Bullish: days | Bullish: mean excess | Not bullish: days | Not bullish: mean excess |
| --- | --- | --- | --- | --- |
| bollinger_reversion | 1 | +6.62% | 1 | -0.50% |
| donchian_breakout | 1 | -3.60% | 1 | +8.38% |
| faber_trend | 1 | +0.35% | 1 | +8.61% |
| ma_crossover | 1 | +0.35% | 1 | +8.61% |
| rsi2_reversion | 1 | +4.64% | 1 | +1.01% |
| tsmom | 1 | +0.21% | 1 | +4.83% |
| xs_momentum | 1 | +7.42% | 1 | -0.82% |

> **Too early to conclude anything.** Some rows above rest on fewer than 60 independent observations. With so few, an average can easily be large and still be luck: a t-statistic below about 2 (or 3, given how many numbers this report shows) is no evidence of skill, and even a large one can flip as more days arrive.
