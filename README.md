# Dip-Buy Radar — cbBTC 1-minute bot

[![Competition](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![Rank](https://make.fun/badge/btc-race-24h-20261117-0603/fadliferdian74-lgtm/btc-race-btc-race-24h-20261117-0603-v2/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![Reward](https://make.fun/badge/btc-race-24h-20261117-0603/fadliferdian74-lgtm/btc-race-btc-race-24h-20261117-0603-v2/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)

Entry for the make.fun race [btc-race-24h-20261117-0603](https://make.fun/bounty/btc-race-24h-20261117-0603):
a paper-trading bot for Coinbase Wrapped BTC (cbBTC) on 1-minute bars,
protocol `agent-fun/bars-replay/v1`, starting cash $1,000.

## Strategy

Two mean-reversion legs and one trend leg share one position (long
only, one at a time); which leg is allowed to trade depends on the
regime the market is in:

- **Dip leg (calm windows).** Entry, flat from bar 300 on: the close is at
  least **1.50% below the rolling 300-bar high** and **RSI(14) < 40** (RSI
  from simple means of the last 14 close-to-close moves), and the signal
  bar **closes above its own open** (see Confirmation). Buy with **all of
  the cash** on hand, `max_slippage_bps` 200. Exit when the close reaches
  **+1.25%** over the entry price (take profit) or **−0.875%** under it
  (stop loss); the entry price is the open of the bar the buy filled on —
  the market price at execution, before the order's own pool impact.
  Parameters were tuned by grid search against the real fill model (fees,
  slippage, pool impact), the entry and stop re-tuned jointly on the calm
  and falling dev windows: +$21.34 net, 0.77% max drawdown, 3 round trips
  on the calm window.
- **Confirmation (both legs).** No leg buys into a bar that is still
  falling: a signal becomes an order only when its bar closes up — close
  above the bar's own open. Parameter-free, and it lowers max drawdown on
  both dev windows (0.98% → 0.77% calm, 2.01% → 1.52% falling).
- **Regime defence.** A falling window eats repeated dip buys (the dip leg
  alone scores −$89.85 on the falling 15 Nov dev window, a dozen round
  trips down the slope). So the first dip-leg stop-out ends the dip leg
  for the rest of the session — one full-size loss is the whole tuition,
  and it is never re-armed.
- **Capitulation leg (falling windows).** If the close falls at least
  **7.5% below the session high** with RSI(14) < 40, on a bar that closes
  up, buy with **all of the cash** and exit at **+4.0% / −1.5%** from the
  entry price or after **120 bars**. It fires at most once per capitulation episode, and a 20%
  drawdown from peak equity halts all new entries. (Exit band and size
  set by a 1,440-combination cross-regime grid: the take-profit ramp at
  the 7.5% trigger is a smooth plateau, while a 6.5% trigger is a trap
  and 8.0% never fires on the falling window.)
- **Trend leg (rising windows).** The two legs above only buy weakness,
  so in a rising window they sit in cash after one dip stop. The trend
  leg buys strength, but only in a confirmed up-day: the close must be
  at least **1.75% above the session's first close** and clear the
  highest close of the **prior 300 bars**, on a bar that closes up. Buy
  with **all of the cash**; exit on a **trailing stop 2.0% below the
  peak close** since the fill bar (+6.0% / −3.0% fixed band as
  backstop); two trend stop-outs retire the leg. The session gate is
  the defence — an ungated breakout leg churns all three test windows,
  while the gate keeps this leg silent on the calm window (best close
  +1.56% over its first close) and the falling one. On a synthetic
  uptrend window (the falling window time-reversed, BTC +4.17%) the
  two-leg bot scores −$12.95; with the trend leg it scores **+$6.43**,
  and the two dev-window scores are unchanged to the cent.
- Cross-regime check on the real fill model: calm dev window **+$21.34**
  (dip leg only, the defence never engages); falling dev window
  **+$22.18** (one dip stop, then the capitulation leg catches the bottom)
  where the undefended dip leg loses $89.85 and capitulation alone earns
  $0 on the calm window.
- Orders are decided on a bar's close and filled by the harness at the next
  bar's open; anything still held at the end is flattened by the harness.

## Running

`main.py` is a single standard-library-only Python 3 file. It reads the
protocol's NDJSON messages on stdin (hello, then one batch per bar) and
writes one reply line per batch on stdout. It is fully deterministic: no
clock, no network, no randomness.

## License

MIT — see [LICENSE](LICENSE).
