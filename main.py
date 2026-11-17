"""Dip-buy mean-reversion bot for agent-fun/bars-replay/v1 (CBBTC, 1-min bars).

Strategy
--------
Stay flat until the price has pulled back at least 1.25% from its rolling
240-bar high while RSI(14) is oversold (< 40); then buy with all of the cash
on hand. While holding, exit the whole position as soon as the close is
+1.25% above the entry price (take profit) or -1.0% below it (stop loss),
where the entry price is the open of the bar the buy filled on -- the market
price at execution, before this order's own pool impact moves the fill.
One position at a time, no shorts, no leverage.

The bot speaks the bars-replay protocol itself: one JSON line in, one JSON
reply line out. Orders decided on a bar's close are filled by the harness at
the next bar's open. Everything is a pure function of the bars and fills
received -- no clock, no network, no randomness -- so two runs over the same
data produce byte-identical replies.

Parameter tuning
----------------
The constants below come from a 16,200-combination grid search run against
the real fill model (fees, slippage and constant-product pool impact) over
this race's dev window, not from the earlier no-impact backtest. The chosen
point scored +$19.41 net with 1.26% max drawdown over 3 round trips, versus
+$13.25 / 0.72% for the first-pass parameters (1.5% dip, 1.5% take profit,
120-bar high, 95% of cash). It sits on a plateau rather than a spike: the
result is unchanged for RSI thresholds 35-50 and stop losses 1.0-1.5%, the
120-bar lookback scores within $0.25 of the 240-bar one, every one-step
neighbour in the grid stays positive, and adding a max-hold exit only hurt
(it force-closed winning positions early), so there is none.
"""

import json
import sys

SYMBOL_FALLBACK = "CBBTC"

# Strategy parameters (tuned, see the tuning note in the module docstring).
LOOKBACK = 240          # bars in the rolling-high window
DIP = 0.0125            # entry: close at least this far below the rolling high
RSI_PERIOD = 14
RSI_OVERSOLD = 40.0     # entry: RSI below this
CASH_FRACTION = 1.00    # entry: spend this fraction of the cash on hand
TAKE_PROFIT = 0.0125    # exit: close this far above the entry fill price
STOP_LOSS = -0.010      # exit: close this far below the entry fill price
SLIPPAGE_BPS = 200      # max_slippage_bps sent with every order


def rsi(closes, period=RSI_PERIOD):
    """RSI from simple means of the last `period` close-to-close moves."""
    moves = [closes[k] - closes[k - 1] for k in range(len(closes) - period, len(closes))]
    gain = sum(m for m in moves if m > 0) / period
    loss = sum(-m for m in moves if m < 0) / period
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


class DipBuyer:
    """Stateful strategy: bar history plus the live position's fill price."""

    def __init__(self):
        self.symbol = SYMBOL_FALLBACK
        self.warmup_bars = 0
        self.closes = []        # raw closes (integers at the symbol's price scale)
        self.last_open = None   # raw open of the most recent bar
        self.cash = 0           # quote units, refreshed from every batch's account
        self.qty = 0            # asset units currently held (0 = flat)
        self.entry_price = None # raw open of the bar the open position filled on
        self.order_count = 0

    def on_hello(self, hello):
        symbols = hello.get("symbols") or []
        if symbols:
            self.symbol = symbols[0]["symbol"]
        self.warmup_bars = int(hello.get("warmup_bars", 0))
        self.cash = int(hello["capital"]["quote_units"])

    def _client_id(self):
        self.order_count += 1
        return "o%d" % self.order_count

    def _note_fills(self, fills):
        for fill in fills:
            if fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy":
                # The fill happened at this batch's bar's open; that open is
                # the entry price (the fill's own print includes our impact).
                self.entry_price = self.last_open
            elif fill.get("side") == "sell":
                self.entry_price = None

    def _note_account(self, account):
        self.cash = int(account["cash_quote_units"])
        self.qty = 0
        for pos in account.get("positions", []):
            if pos["symbol"] == self.symbol:
                self.qty = int(pos["qty"])
                if self.qty > 0 and self.entry_price is None:
                    # The position appeared with this batch, so it filled at
                    # this bar's open even if its fill line was not seen.
                    self.entry_price = self.last_open

    def on_batch(self, batch):
        """Return the orders decided on this batch's closing bar."""
        for bar in batch["bars"]:
            if bar["symbol"] == self.symbol:
                self.last_open = int(bar["o"])
                self.closes.append(int(bar["c"]))
        self._note_fills(batch.get("fills", []))
        self._note_account(batch["account"])
        if not self.closes:
            return []
        if self.warmup_bars and batch["seq"] < self.warmup_bars:
            return []  # orders decided on warm-up bars are rejected anyway

        close = self.closes[-1]

        if self.qty > 0:
            # Holding: leave on the take-profit / stop-loss band only.
            if self.entry_price:
                change = close / self.entry_price - 1.0
                if change >= TAKE_PROFIT or change <= STOP_LOSS:
                    return [{
                        "side": "sell",
                        "client_id": self._client_id(),
                        "symbol": self.symbol,
                        "qty": str(self.qty),
                        "max_slippage_bps": SLIPPAGE_BPS,
                    }]
            return []

        # Flat: wait for a deep enough, oversold enough dip.
        index = len(self.closes) - 1
        if index < LOOKBACK:
            return []
        high = max(self.closes[-LOOKBACK:])
        if close > high * (1.0 - DIP):
            return []
        if rsi(self.closes) >= RSI_OVERSOLD:
            return []
        notional = int(self.cash * CASH_FRACTION)
        if notional <= 0:
            return []
        return [{
            "side": "buy",
            "client_id": self._client_id(),
            "symbol": self.symbol,
            "notional_quote_units": str(notional),
            "max_slippage_bps": SLIPPAGE_BPS,
        }]


def main():
    bot = DipBuyer()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            bot.on_hello(msg)
        elif kind == "batch":
            orders = bot.on_batch(msg)
            reply = {"type": "reply", "seq": msg["seq"], "orders": orders}
            out.write(json.dumps(reply, separators=(",", ":")) + "\n")
            out.flush()
        elif kind == "done":
            break


if __name__ == "__main__":
    main()
