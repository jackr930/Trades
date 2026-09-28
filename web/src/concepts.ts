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
