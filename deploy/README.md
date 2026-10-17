# Running the paper trader unattended

The paper trader only ever reaches Alpaca's **paper** endpoint; there is no live order path in
this repository. What runs where depends on the spec:

| Spec | Runs on | How |
| --- | --- | --- |
| Daily (e.g. STRAT-000) | GitHub Actions | the Journal workflow's `paper` job, each weekday after 7:00pm ET |
| Intraday (e.g. STRAT-002) | a small Linux machine that stays up | systemd timers start `docker compose` |

Either way `trades paper --submit` refuses to send orders until the spec's latest research run
passed the acceptance checklist, and everything it does lands in `paper/<spec key>/`.

## The machine (intraday specs)

1. **A VM.** 2 vCPU and 2-4 GB is plenty (roughly $6-24 a month). Install Docker and git, and
   clone this repository to `/opt/trades`.
2. **Secrets, outside the repository.** Create `/etc/trades/paper.env` (`chmod 600`):

   ```sh
   APCA_API_KEY_ID=...          # PAPER keys only
   APCA_API_SECRET_KEY=...
   TELEGRAM_BOT_TOKEN=...       # optional: alerts (and set alerts.telegram: true in config/paper.yaml)
   TELEGRAM_CHAT_ID=...
   TRADES_HEARTBEAT_URL=...     # optional but recommended: a dead-man's-switch URL (e.g. healthchecks.io)
   ```

   Never put live keys on this machine, or on any machine that runs Claude Code.
3. **Choose the spec.** Set `paper.spec` in `config/paper.yaml`, then check everything with
   `trades paper status`.
4. **Install the timers.**

   ```sh
   sudo cp deploy/systemd/* /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now trades-paper.timer trades-backstop.timer trades-records.timer
   ```

   * `trades-paper` starts the session at 9:00am ET on weekdays (the runner skips holidays, waits
     for the open and exits after the close; Docker restarts it after a crash).
   * `trades-backstop` checks every 15 minutes: if the runner's heartbeat has stopped while the
     account holds positions, it flattens and turns the kill switch on.
   * `trades-records` commits `paper/` after the close (it needs a deploy key with write access),
     so the record outlives the machine and the daily review can read it.
5. **A dry run first.** Remove `--submit` from the `trader` command in `docker-compose.yml` for a
   few sessions and read `paper/<spec>/reports/`.

## When something goes wrong

| If | What happens by itself | What you do |
| --- | --- | --- |
| The runner crashes at 10:15 with a position open | The position's bracket stop and take-profit stay at Alpaca. Docker restarts the runner, which picks up from `state.json`. | Read the `error` alert and `paper/<spec>/events.jsonl`. |
| The machine dies | The heartbeat pings stop, so your dead-man's-switch service alerts you. Bracket orders still protect the positions until the close. | `trades paper flatten` from anywhere with the paper keys, or use Alpaca's dashboard. |
| The daily loss limit is hit | The risk engine closes positions and accepts no new trades until the next session (weekly: next week). | Nothing, then review. |
| You want everything stopped | | `trades halt` (or commit a file named `HALT`), then `trades paper flatten`. Revoking the API key is the last resort. |

## The routine (the playbook)

* **Pre-market, 15 minutes:** `trades paper status`: the kill switch, the account, positions that
  should not be there, the runner's heartbeat.
* **During the session:** hands off.
* **Post-close, 15 minutes:** `paper/<spec>/reports/<date>.md` and any alerts.
* **Weekly, 1 hour:** `trades track` (signals, fills against the cost model, expectancy, daily P&L
  correlation), then one written decision.
* **Before real money:** `trades gate`, including the four kill switch drills
  (`trades paper drill halt|flatten|daily-loss|revoke-key`) and a test alert
  (`trades paper drill alert`).
