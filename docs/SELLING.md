# Selling: tools, not signals

The harness in this repository (specs, honest backtests, the acceptance checklist, paper
tracking, the gate) is worth more than any single strategy, and it is the part worth selling. A
product that helps people *test* their ideas against realistic costs is impersonal software. A
product that tells people what to buy, or promises they will make money, is regulated advice,
banned by most payment processors, and the fastest way to lose a business.

*Informational only. Not legal, tax or financial advice. Confirm regulatory questions with a
securities attorney and tax questions with a CPA. The policy quotes below come from the
playbook (October 2026); read each platform's current text before you launch.*

## What to sell, ranked

| Rank | Product | Early revenue (estimate) | Legal risk | Watch out for |
| --- | --- | --- | --- | --- |
| 1 | **Backtesting, journaling and risk SaaS** (no buy or sell advice) | low hundreds to low thousands a month after 6 to 12 months with an audience | low, if impersonal | Stripe restricts "investment and brokerage services": describe it accurately as analytics software |
| 2 | **Python template packs** (the harness, without your live strategies) | tens to low thousands per launch | low | Lemon Squeezy bans get-rich-quick products; Paddle bans "trading signals and strategies" |
| 3 | **TradingView invite-only indicators** | small and steady, with a following | low to moderate | a Premium account; TradingView's vendor rules |
| 4 | **Education** (a course, YouTube, a newsletter) | depends on the audience | moderate (earnings claims, testimonials) | no get-rich-quick framing |
| 5 | **NinjaTrader add-ons** | niche | moderate | vendor terms |
| 6 | **Signals, or Collective2 track records** | unlikely early | **high** (adviser registration, the CFTC, state rules) | do not, without counsel |

**The recommended path:** open-source a slim version of the harness on GitHub as the funnel, sell
a pro template pack, then a hosted analysis and journaling service. Publish one or two free
TradingView indicators before any invite-only script.

## Platform rules (as the playbook quotes them)

| Platform | The rule that matters |
| --- | --- |
| Stripe | prohibits "'Get rich quick' schemes... sites that promise fast and easy money" and funded prop trading; restricts investment and brokerage services |
| Paddle | prohibits "Investment or financial advice, including trading signals and strategies" |
| Lemon Squeezy | allows software; prohibits get-rich-quick schemes and crypto products |
| TradingView | only invite-only and Paid Space scripts may be sold; vendors who mislead traders "can be permanently banned" |
| Anthropic's Usage Policy | Claude-produced financial recommendations shown to users need a qualified human in the loop and an AI disclosure |

## Marketing that stays legal

* **FTC**: the Endorsement Guides (16 CFR 255) and the rule on reviews and testimonials (16 CFR
  465). No fake reviews, disclose connections, never present atypical results as typical, and make
  no earnings claims for a software tool.
* **SEC and CFTC**: stay impersonal (the publisher's exclusion, *Lowe v. SEC*: the same content
  for everyone, no one-on-one advice), label hypothetical performance, and never "AI-wash" (claim
  AI does more than it does).

**Say this, not that:**

| Say | Not |
| --- | --- |
| "Test your strategy against realistic costs before you risk money." | "Find strategies that make money." |
| "See how much of your backtest survives slippage." | "Our AI finds winning trades." |
| "A checklist that rejects most strategies, on purpose." | "Proven, profitable algorithms." |

## The pre-launch checklist

- [ ] No promise or hint of profit, income or "passive income"
- [ ] Every backtest labelled "Hypothetical backtest. Not actual trading results."
- [ ] Every figure states its period and the costs assumed, and that past results do not predict
      future results
- [ ] No profit testimonials; product testimonials only, with connections disclosed
- [ ] No claim that AI "predicts", "guarantees" or "finds winning trades"; say exactly what AI does
- [ ] The same output for every user given the same inputs; no one-on-one advice
- [ ] "Educational and informational only, not investment advice" on the site, at checkout and in
      the app
- [ ] A refund policy and terms, published
- [ ] An AI disclosure and a human review path if Claude produces any user-facing financial
      recommendation
- [ ] The payment processor's category describes the product accurately

**A sample disclaimer:**

> This software is for educational and informational purposes only. It is not investment, legal,
> or tax advice, and it does not recommend any security or trade. Backtest results are
> hypothetical, were prepared with the benefit of hindsight, and do not reflect actual trading.
> Trading involves substantial risk of loss. Past performance does not guarantee future results.
> Some features use AI, and AI outputs can be wrong; review everything yourself.

## What this repository already does

* **It is impersonal.** Every screen gives the same output for the same inputs and settings;
  nothing is tailored to a person's finances beyond the account profile they type in.
* **It labels itself.** The app shows "Educational software. Not investment advice." on every
  screen, the Live Desk says "Not advice" in its own words, and synthetic data is labelled.
* **It does not use an AI model at run time.** Recommendations come from published,
  deterministic rules whose reasoning is shown. If you add Claude-written insights to a product,
  the Usage Policy's human-in-the-loop and disclosure requirements apply: design for them first.
* **It never places real-money orders.** The paper trader reaches only Alpaca's paper endpoint.

What a product built from it would still need: hypothetical-performance labels on every chart
and export, terms and a refund policy, an accurate processor category, and (for any hosted
service) no stored broker keys at first: CSV uploads, then read-only keys with encryption and a
security review.

## Go to market

* **Channels**: the open-source funnel on GitHub, building in public on YouTube ("I replicated the
  ORB paper; here is what costs did"), X, TradingView community scripts, Reddit only where
  self-promotion is allowed, a Product Hunt launch, a free newsletter.
* **Prices to compare with**: vectorbt PRO from $25 a month or $500 lifetime; Massive $29 to $199 a
  month. Start around $15 to $30 a month for a service, or $49 to $149 once for templates
  (estimates).
* **Realistic revenue**: low hundreds of dollars a month within six months is a good outcome (an
  estimate, not a sourced figure).

Before launch, answer the playbook's three questions in writing: does any sentence imply a user
will make money (read it as the FTC would)? If a customer asks "should I buy NVDA tomorrow", what
do the product and its support say? Does the product's category match the payment processor's
current policy text?
