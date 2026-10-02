import type { Reference } from "./types";

export interface Concept {
  id: string;
  title: string;
  body: string[];
  refs: Reference[];
}

const R = (authors: string, year: number, title: string, venue: string, url: string | null = null): Reference => ({
  authors,
  year,
  title,
  venue,
  url,
});

export const CONCEPTS: Concept[] = [
  {
    id: "lookahead",
    title: "Look-ahead bias",
    body: [
      "A backtest must only use information that was available at the moment of each decision. Using today's close to decide a trade executed at today's open, or indicators that peek at future bars, makes almost any rule look brilliant.",
      "In this app every signal is computed at the close of bar t and filled at the open of bar t+1 (or its close). The test suite checks every strategy by cutting the data short and confirming that past signals never change when future bars are added.",
    ],
    refs: [],
  },
  {
    id: "overfitting",
    title: "Overfitting and multiple testing",
    body: [
      "Try enough parameter combinations and one will look great by chance. The best of 100 random strategies will usually show an impressive Sharpe ratio even though none has skill.",
      "The Deflated Sharpe Ratio corrects for the number of trials, and walk-forward testing scores parameters only on data the optimiser never saw. Harvey, Liu & Zhu argue that a newly discovered factor should clear a t-statistic of about 3, not the traditional 2, because so many have been tried.",
      "A robust strategy shows a broad plateau of decent results across nearby parameters, not a single sharp peak.",
    ],
    refs: [
      R("Bailey, D. H. & Lopez de Prado, M.", 2014, "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality", "Journal of Portfolio Management 40(5), 94-107", "https://doi.org/10.3905/jpm.2014.40.5.094"),
      R("Harvey, C. R., Liu, Y. & Zhu, H.", 2016, "... and the Cross-Section of Expected Returns", "Review of Financial Studies 29(1), 5-68", "https://doi.org/10.1093/rfs/hhv059"),
      R("Sullivan, R., Timmermann, A. & White, H.", 1999, "Data-Snooping, Technical Trading Rule Performance, and the Bootstrap", "Journal of Finance 54(5), 1647-1691", "https://doi.org/10.1111/0022-1082.00163"),
    ],
  },
  {
    id: "forward",
    title: "Forward testing: the only truly unseen data",
    body: [
      "However carefully a backtest is built, you chose the strategy after seeing the history it is tested on. A forward test (paper trading) runs the rule on data that did not exist when you chose it, which makes it the cleanest out-of-sample test there is.",
      "Its weakness is time. The uncertainty of an annualised Sharpe ratio estimated from daily returns is roughly 1 / sqrt(years): after one year, a strategy with a true Sharpe ratio of 0.5 can easily show anything from -0.5 to 1.5. Months of good forward results prove little on their own.",
      "The strategy simulator trades each strategy bar by bar with the same engine as the backtester. In the simulated market the future is generated as you go and the shocks you inject are ones no parameter was tuned for; the real-time mode is a genuine forward test on live data.",
    ],
    refs: [
      R("Lo, A. W.", 2002, "The Statistics of Sharpe Ratios", "Financial Analysts Journal 58(4), 36-52", "https://doi.org/10.2469/faj.v58.n4.2453"),
      R("Bailey, D. H., Borwein, J. M., Lopez de Prado, M. & Zhu, Q. J.", 2014, "Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance", "Notices of the AMS 61(5), 458-471", "https://doi.org/10.1090/noti1105"),
    ],
  },
  {
    id: "ai-picks",
    title: "Why an AI model's picks can't be backtested",
    body: [
      "A language model learns from text written up to its training cutoff: news, earnings reports, analysis, and articles looking back at what markets did. A backtest asks it about dates before that cutoff, so it already knows, in some blurred form, what happened next. Asked whether the headline 'Google to announce 2019 Q3 earnings tomorrow' is good news, a model trained on later text can answer from what it remembers about those earnings rather than from the headline. Its 'prediction' is partly a memory.",
      "That leak is inside the model, not in the data you give it. This app's no-look-ahead test cuts the price history at a bar and checks that nothing before the cut changes when later bars are added; that catches a rule that peeks at future prices. It cannot catch a model whose weights were trained on the future: hide every later price and headline, and it still knows. Hiding even the company's name does not settle it. Glasserman and Lin found that GPT-3.5 judged old headlines differently once names were removed, because what it knew about a company coloured its reading, so how much, and in which direction, a backtest is distorted cannot be measured from inside it.",
      "The only fair test is one where the outcome did not exist when the prediction was made: predictions written down in advance, with a timestamp, and scored later. Lopez-Lira and Tang tested ChatGPT only on headlines from after its training cutoff for this reason. The forward journal does the same for this app's rules, and it is why Trades never turns a language model's opinion into a buy or sell signal: there would be no honest way to show it works.",
    ],
    refs: [
      R("Glasserman, P. & Lin, C.", 2023, "Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis", "arXiv:2309.17322", "https://arxiv.org/abs/2309.17322"),
      R("Lopez-Lira, A. & Tang, Y.", 2023, "Can ChatGPT Forecast Stock Price Movements? Return Predictability and Large Language Models", "arXiv:2304.07619", "https://arxiv.org/abs/2304.07619"),
    ],
  },
  {
    id: "regimes",
    title: "Market regimes: why no strategy wins everywhere",
    body: [
      "Markets alternate between trending and range-bound phases, and between calm and turbulent ones. Hamilton's regime-switching model formalises this: prices are driven by a hidden state with its own mean and volatility, which you can only infer from prices, and only with a lag.",
      "Each strategy is a bet on a kind of regime. Trend followers earn in persistent moves (historically including crises) and pay a small premium in choppy markets; mean-reversion rules do the opposite and suffer when dips keep dipping. Cross-sectional momentum is known for sudden crashes when beaten-down stocks rebound sharply after a bear market.",
      "The simulator's per-regime table shows this directly. Judge a strategy by whether it behaved as its logic predicts in each regime, not just by its total return on one path, and remember that the regime mix of the next few years is unknown.",
    ],
    refs: [
      R("Hamilton, J. D.", 1989, "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle", "Econometrica 57(2), 357-384", "https://doi.org/10.2307/1912559"),
      R("Hurst, B., Ooi, Y. H. & Pedersen, L. H.", 2017, "A Century of Evidence on Trend-Following Investing", "Journal of Portfolio Management 44(1), 15-29", "https://doi.org/10.3905/jpm.2017.44.1.015"),
      R("Daniel, K. & Moskowitz, T. J.", 2016, "Momentum Crashes", "Journal of Financial Economics 122(2), 221-247", "https://doi.org/10.1016/j.jfineco.2015.12.002"),
    ],
  },
  {
    id: "neutral",
    title: "Beta, residuals and market-neutral books",
    body: [
      "Most of a stock's daily move is the market carrying it along. Regress a stock's returns on the market's and you split them in two: beta times the market's move, plus a residual that belongs to the stock alone. A long position in one stock is therefore mostly a bet on the market.",
      "Statistical arbitrage and residual momentum work on the residual. Buying a stock and shorting beta times as much of the market leaves only the stock-specific part, so the book barely moves with the index. That is how stat-arb funds can earn in bear markets, and why their risk is different: in August 2007 many funds holding similar trades unwound at once and lost together for days.",
      "Neutral in beta is not neutral in dollars. Hedging a low-beta stock takes little of the market, so a beta-neutral book can be net long or net short in dollar terms, and a beta estimated on the past can be wrong in the future.",
    ],
    refs: [
      R("Avellaneda, M. & Lee, J.-H.", 2010, "Statistical Arbitrage in the US Equities Market", "Quantitative Finance 10(7), 761-782", "https://doi.org/10.1080/14697680903124632"),
      R("Khandani, A. E. & Lo, A. W.", 2011, "What Happened to the Quants in August 2007? Evidence from Factors and Transactions Data", "Journal of Financial Markets 14(1), 1-46", "https://doi.org/10.1016/j.finmar.2010.07.005"),
    ],
  },
  {
    id: "filters",
    title: "Filtering: tracking what you cannot observe",
    body: [
      "Some quantities that matter are never observed: the true hedge ratio between two stocks today, or whether the market is in a calm or a turbulent state. A filter keeps a running estimate and updates it with every bar, moving it more when the new bar is more surprising.",
      "The Kalman filter does this for continuous quantities. Its gain decides how far each forecast error moves the estimate: adapt quickly and it follows real changes but also chases noise; adapt slowly and it is stable but late. A hidden Markov model does the same for discrete states, turning each return into evidence for calm or turbulent and weighing it against how persistent each state has been.",
      "Both strategies here use the filtered estimate, which only looks backwards. A smoothed estimate uses the whole sample, future included, and would be look-ahead bias in a backtest.",
    ],
    refs: [
      R("Kalman, R. E.", 1960, "A New Approach to Linear Filtering and Prediction Problems", "Journal of Basic Engineering 82(1), 35-45", "https://doi.org/10.1115/1.3662552"),
      R("Hamilton, J. D.", 1989, "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle", "Econometrica 57(2), 357-384", "https://doi.org/10.2307/1912559"),
    ],
  },
  {
    id: "ml",
    title: "Machine learning without fooling yourself",
    body: [
      "Flexible models find patterns in noise easily, and markets offer little data: one history, a few thousand days, noisy and changing. A model with many parameters can fit the past perfectly and predict nothing.",
      "Three habits matter most. Train only on data available at the time and retrain as you go (walk-forward). Leave a gap between the last training target and the prediction date, because a 21-day return that ends after today leaks the future into training (purging). And judge the model by its out-of-sample information coefficient, the rank correlation between its predictions and what actually happened, not by how well it fits.",
      "Expect small numbers: an information coefficient of 0.05 is useful across hundreds of stocks. On eight stocks a single month's rank correlation has a standard error of about 0.38, so one good month means little. Shrinkage (ridge regression) keeps the model's weights small and stable, trading a little bias for much less variance.",
    ],
    refs: [
      R("Lopez de Prado, M.", 2018, "Advances in Financial Machine Learning", "Wiley"),
      R("Gu, S., Kelly, B. & Xiu, D.", 2020, "Empirical Asset Pricing via Machine Learning", "Review of Financial Studies 33(5), 2223-2273", "https://doi.org/10.1093/rfs/hhaa009"),
      R("Grinold, R. C. & Kahn, R. N.", 2000, "Active Portfolio Management (2nd ed.)", "McGraw-Hill"),
    ],
  },
  {
    id: "decay",
    title: "Why published edges shrink",
    body: [
      "Once a profitable pattern is published, investors trade on it and it weakens. McLean & Pontiff studied 97 published return predictors and found their returns were about 26% lower out-of-sample and about 58% lower after publication.",
      "Every strategy in this library is published, so expect less than the original papers report, especially after costs. Use them as disciplined, well-understood baselines, not as secret money machines.",
    ],
    refs: [
      R("McLean, R. D. & Pontiff, J.", 2016, "Does Academic Research Destroy Stock Return Predictability?", "Journal of Finance 71(1), 5-32", "https://doi.org/10.1111/jofi.12365"),
    ],
  },
  {
    id: "sharpe",
    title: "The Sharpe ratio and its uncertainty",
    body: [
      "The Sharpe ratio is average excess return divided by volatility, annualised. It is an estimate with a large standard error: roughly sqrt((1 + SR^2/2) / years). With three years of data a Sharpe of 0.5 is statistically indistinguishable from zero.",
      "The Probabilistic Sharpe Ratio shown in backtests turns this into a probability that the true Sharpe exceeds zero, accounting for sample length, skewness and fat tails.",
    ],
    refs: [
      R("Lo, A. W.", 2002, "The Statistics of Sharpe Ratios", "Financial Analysts Journal 58(4), 36-52", "https://doi.org/10.2469/faj.v58.n4.2453"),
      R("Bailey, D. H. & Lopez de Prado, M.", 2012, "The Sharpe Ratio Efficient Frontier", "Journal of Risk 15(2), 3-44"),
    ],
  },
  {
    id: "costs",
    title: "Transaction costs and turnover",
    body: [
      "Every trade pays the bid-ask spread and moves the price against you (slippage), plus any commission. A strategy that trades daily can earn a large gross return and still lose money net of costs.",
      "Use the slippage setting in the Strategy Lab as a stress test: if doubling it wipes out the edge, the edge was never robust. Short-term reversal is the classic example of a strong gross effect that mostly disappears after costs.",
    ],
    refs: [
      R("Barber, B. M. & Odean, T.", 2000, "Trading Is Hazardous to Your Wealth", "Journal of Finance 55(2), 773-806", "https://doi.org/10.1111/0022-1082.00226"),
      R("Avramov, D., Chordia, T. & Goyal, A.", 2006, "Liquidity and Autocorrelations in Individual Stock Returns", "Journal of Finance 61(5), 2365-2394", "https://doi.org/10.1111/j.1540-6261.2006.01060.x"),
    ],
  },
  {
    id: "survivorship",
    title: "Survivorship bias",
    body: [
      "If you backtest on the stocks that exist today, you silently drop every company that went bankrupt or was delisted along the way. The surviving sample looks better than the market investors actually faced.",
      "Professional research uses point-in-time universes that include dead companies. With free data you cannot fully fix this, so treat backtests on today's winners (e.g. the current largest tech stocks) with extra scepticism.",
    ],
    refs: [
      R("Brown, S. J., Goetzmann, W., Ibbotson, R. G. & Ross, S. A.", 1992, "Survivorship Bias in Performance Studies", "Review of Financial Studies 5(4), 553-580", "https://doi.org/10.1093/rfs/5.4.553"),
    ],
  },
  {
    id: "sizing",
    title: "Position sizing, volatility targeting and Kelly",
    body: [
      "How much you bet matters as much as what you bet on. Fixed-fractional sizing risks a set share of equity per trade (e.g. 1% if the stop is hit). Volatility targeting scales exposure inversely to recent volatility, which has historically improved risk-adjusted returns for many strategies.",
      "The Kelly criterion gives the growth-maximising bet size for a known edge, but edges are never known precisely; over-betting is far more damaging than under-betting, which is why practitioners use half-Kelly or less.",
    ],
    refs: [
      R("Kelly, J. L.", 1956, "A New Interpretation of Information Rate", "Bell System Technical Journal 35(4), 917-926", "https://doi.org/10.1002/j.1538-7305.1956.tb03809.x"),
      R("Moreira, A. & Muir, T.", 2017, "Volatility-Managed Portfolios", "Journal of Finance 72(4), 1611-1644", "https://doi.org/10.1111/jofi.12513"),
    ],
  },
  {
    id: "drawdowns",
    title: "Drawdowns and the arithmetic of losses",
    body: [
      "Losses and gains are not symmetric: after a 20% loss you need +25% to get back to even; after 50% you need +100%. Deep drawdowns also cause people to abandon strategies at the worst moment.",
      "Before trusting a backtest, look at its maximum drawdown and its longest time under water, and ask honestly whether you would have kept following it.",
    ],
    refs: [],
  },
  {
    id: "behaviour",
    title: "Behavioural biases",
    body: [
      "The disposition effect is the tendency to sell winners too early and hold losers too long. Overconfidence leads to over-trading, and loss aversion (losses hurt about twice as much as equal gains feel good) makes people hold losing positions hoping to break even.",
      "The simulator's scorecard measures these habits from your own trades, so you can see them rather than just read about them.",
    ],
    refs: [
      R("Shefrin, H. & Statman, M.", 1985, "The Disposition to Sell Winners Too Early and Ride Losers Too Long: Theory and Evidence", "Journal of Finance 40(3), 777-790", "https://doi.org/10.1111/j.1540-6261.1985.tb05002.x"),
      R("Odean, T.", 1998, "Are Investors Reluctant to Realize Their Losses?", "Journal of Finance 53(5), 1775-1798", "https://doi.org/10.1111/0022-1082.00072"),
      R("Barber, B. M. & Odean, T.", 2001, "Boys Will Be Boys: Gender, Overconfidence, and Common Stock Investment", "Quarterly Journal of Economics 116(1), 261-292", "https://doi.org/10.1162/003355301556400"),
      R("Kahneman, D. & Tversky, A.", 1979, "Prospect Theory: An Analysis of Decision under Risk", "Econometrica 47(2), 263-291", "https://doi.org/10.2307/1914185"),
    ],
  },
  {
    id: "process",
    title: "Process versus outcome",
    body: [
      "In a single trade, or a single year, luck can swamp skill. Judging decisions only by their results ('resulting') teaches the wrong lessons: a reckless bet that paid off feels like skill.",
      "Write down why you enter each trade before you know how it ends, and review the reasoning, not just the P&L. The simulator scores process and outcome separately for this reason.",
    ],
    refs: [
      R("Duke, A.", 2018, "Thinking in Bets: Making Smarter Decisions When You Don't Have All the Facts", "Portfolio/Penguin"),
    ],
  },
];
