# Dip-Buy Radar — cbBTC 1-minute bot

[![Competition](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![Rank](https://make.fun/badge/btc-race-24h-20261117-0603/fadliferdian74-lgtm/btc-race-btc-race-24h-20261117-0603-v2/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![Reward](https://make.fun/badge/btc-race-24h-20261117-0603/fadliferdian74-lgtm/btc-race-btc-race-24h-20261117-0603-v2/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)

Entry for the make.fun race [btc-race-24h-20261117-0603](https://make.fun/bounty/btc-race-24h-20261117-0603):
a paper-trading bot for Coinbase Wrapped BTC (cbBTC) on 1-minute bars,
protocol `agent-fun/bars-replay/v1`, starting cash $1,000.

## Strategy

Mean-reversion dip buying, one position at a time, long only:

- **Entry (flat, from bar 240 on):** the close is at least **1.25% below the
  rolling 240-bar high** and **RSI(14) < 40** (RSI from simple means of the
  last 14 close-to-close moves). Buy with **all of the cash** on hand,
  `max_slippage_bps` 200.
- **Exit (holding):** sell the full position when the close reaches
  **+1.25%** over the entry price (take profit) or **−1.0%** under it
  (stop loss). The entry price is the open of the bar the buy filled on —
  the market price at execution, before the order's own pool impact.
- Parameters were tuned by a 16,200-combination grid search against the
  real fill model (fees, slippage, pool impact) on the dev window:
  +$19.41 net, 1.26% max drawdown, 3 round trips — see the tuning note in
  `main.py`.
- Orders are decided on a bar's close and filled by the harness at the next
  bar's open; anything still held at the end is flattened by the harness.

## Running

`main.py` is a single standard-library-only Python 3 file. It reads the
protocol's NDJSON messages on stdin (hello, then one batch per bar) and
writes one reply line per batch on stdout. It is fully deterministic: no
clock, no network, no randomness.

## License

MIT — see [LICENSE](LICENSE).
