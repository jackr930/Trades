/** Privacy and terms: what this app keeps, where it sends things, and what it is not.
 * Drafts for a self-hosted, single-user app; have a lawyer review them before anyone else uses it. */

export function Privacy() {
  return (
    <div className="card prose" style={{ maxWidth: "80ch" }}>
      <h1>Privacy</h1>
      <p className="small muted">
        Draft for a self-hosted app with one user. Review it with a lawyer before letting anyone else sign in.
      </p>
      <h2>What this server keeps</h2>
      <ul>
        <li>
          <b>Settings</b>, including any Alpaca API keys (stored in a file only the server can read, never shown again,
          never in backups) and holdings you import from a broker file.
        </li>
        <li>
          <b>The research log</b>: one line per backtest (strategy, parameters, symbols, Sharpe ratio), so the app can tell
          you how many configurations you have tried.
        </li>
        <li>
          <b>Simulator history</b>: the summary of each practice session you finish.
        </li>
        <li>
          <b>A login cookie</b> when the server has a password: it says you logged in, nothing else. There are no
          analytics, advertising or tracking cookies.
        </li>
      </ul>
      <h2>Where things are sent</h2>
      <ul>
        <li>
          <b>Market data providers</b> (Yahoo Finance, or Alpaca with your key) receive the symbols and dates you ask about.
        </li>
        <li>
          <b>Alpaca&apos;s paper API</b> receives paper orders, only if you run the paper trader with your key.
        </li>
        <li>
          <b>GitHub</b> is asked for the committed track record, only if you set a GitHub address as its source.
        </li>
        <li>Nothing else: your holdings and settings never leave this server except in a backup you download.</li>
      </ul>
      <h2>Your control</h2>
      <p>
        Settings → Your data downloads everything the server keeps about you (without API keys) and deletes all of it on
        request. The forward journal lives in your Git repository, not on this server.
      </p>
    </div>
  );
}

export function Terms() {
  return (
    <div className="card prose" style={{ maxWidth: "80ch" }}>
      <h1>Terms of use</h1>
      <p className="small muted">
        Draft for a self-hosted app with one user. Review it with a lawyer before letting anyone else sign in.
      </p>
      <ul>
        <li>
          <b>Education, not advice.</b> Everything here is generated mechanically from past prices and published research.
          It does not know your goals, income, debts or tax position and is not investment, tax or legal advice.
        </li>
        <li>
          <b>Nothing is proven.</b> Backtests describe the past. Whether any strategy here beats buying and holding an index
          fund is what the forward journal is testing; until its pre-registered rule says otherwise, assume none does.
        </li>
        <li>
          <b>No real orders.</b> The app never places real-money trades. Its paper trader uses Alpaca&apos;s practice
          endpoint only. Anything you do with real money, you do yourself, at your own risk.
        </li>
        <li>
          <b>Market data</b> comes from third parties under their own terms, may be delayed or wrong, and is for your
          personal use.
        </li>
        <li>
          <b>No warranty.</b> The software is provided as is. Estimates of taxes, fees and outcomes are approximations.
        </li>
      </ul>
    </div>
  );
}
