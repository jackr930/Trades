# Forward journal report

Scored from `journal/recommendations.csv` with prices through 2026-10-30.

Outcome of a recommendation: the symbol's return from the next session's open to the close 5 or 21 sessions later, minus SPY's return over the same window. Outcomes are averaged within each day first (symbols move together), and only days at least one horizon apart are used, so every observation is independent. Each experiment is scored on its own. Outcomes are before trading costs and taxes, and SPY's own rows are left out (against itself its excess is always zero).

## Experiment `c19ae262d0b0`

11 sessions recorded (2026-10-05 to 2026-10-30), 88 rows; code version(s) `b49dc0a0f680`, `1f0b53c4f97d`, `d5ff50f30504`.

### 5-session horizon

49 of 88 rows have a finished window.

| Label | Independent days | Mean excess vs SPY | Hit rate vs SPY | t-stat |
| --- | --- | --- | --- | --- |
| Strong buy | 2 | +0.16% | 100% | 1.54 |
| Buy | 2 | +0.57% | 50% | 0.47 |
| Neutral | 2 | +1.84% | 100% | 2.64 |

| Strategy | Bullish: days | Bullish: mean excess | Not bullish: days | Not bullish: mean excess |
| --- | --- | --- | --- | --- |
| bollinger_reversion | 2 | +0.92% | 2 | +0.27% |
| donchian_breakout | 2 | -0.13% | 2 | +1.24% |
| faber_trend | 2 | +0.22% | 2 | +1.84% |
| ma_crossover | 2 | +0.22% | 2 | +1.84% |
| rsi2_reversion | 2 | -0.68% | 2 | +0.65% |
| tsmom | 2 | +0.16% | 2 | +1.21% |
| xs_momentum | 2 | +3.32% | 2 | -0.69% |

> **Too early to conclude anything.** Some rows above rest on fewer than 60 independent observations. With so few, an average can easily be large and still be luck: a t-statistic below about 2 (or 3, given how many numbers this report shows) is no evidence of skill, and even a large one can flip as more days arrive.

### 21-session horizon

0 of 88 rows have a finished window.
