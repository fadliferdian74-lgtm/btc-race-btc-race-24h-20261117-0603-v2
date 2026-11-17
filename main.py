"""Dip-buy mean-reversion bot for agent-fun/bars-replay/v1 (CBBTC, 1-min bars).

Strategy
--------
Stay flat until the price has pulled back at least 1.5% from its rolling
120-bar high while RSI(14) is oversold (< 40); then buy with 95% of the cash
on hand. While holding, exit the whole position as soon as the close is
+1.5% above the fill price (take profit) or -1.0% below it (stop loss).
One position at a time, no shorts, no leverage.

The bot speaks the bars-replay protocol itself: one JSON line in, one JSON
reply line out. Orders decided on a bar's close are filled by the harness at
the next bar's open. Everything is a pure function of the bars and fills
received -- no clock, no network, no randomness -- so two runs over the same
data produce byte-identical replies.
"""

import json
import sys

SYMBOL_FALLBACK = "CBBTC"

# Strategy parameters.
LOOKBACK = 120          # bars in the rolling-high window
DIP = 0.015             # entry: close at least this far below the rolling high
RSI_PERIOD = 14
RSI_OVERSOLD = 40.0     # entry: RSI below this
CASH_FRACTION = 0.95    # entry: spend this fraction of the cash on hand
TAKE_PROFIT = 0.015     # exit: close this far above the entry fill price
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
        self.price_scale = 5
        self.qty_scale = 8
        self.quote_scale = 6
        self.warmup_bars = 0
        self.closes = []        # raw closes (integers at the symbol's price scale)
        self.cash = 0           # quote units, refreshed from every batch's account
        self.qty = 0            # asset units currently held (0 = flat)
        self.entry_price = None # raw price of the open position's buy fill
        self.order_count = 0

    def on_hello(self, hello):
        symbols = hello.get("symbols") or []
        if symbols:
            self.symbol = symbols[0]["symbol"]
            self.price_scale = int(symbols[0]["price_scale"])
            self.qty_scale = int(symbols[0]["qty_scale"])
        self.quote_scale = int(hello["capital"]["quote_scale"])
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
                self.entry_price = int(fill["price"])
            elif fill.get("side") == "sell":
                self.entry_price = None

    def _note_account(self, account):
        self.cash = int(account["cash_quote_units"])
        self.qty = 0
        for pos in account.get("positions", []):
            if pos["symbol"] == self.symbol:
                self.qty = int(pos["qty"])
                if self.qty > 0 and self.entry_price is None:
                    # Fall back to the position's cost basis (fees included)
                    # so the exit band still has an entry price to measure
                    # against: cost = qty * price * 10^quote / 10^(qty+price).
                    cost = int(pos["cost_quote_units"])
                    span = self.qty_scale + self.price_scale - self.quote_scale
                    self.entry_price = cost * 10 ** span // self.qty

    def on_batch(self, batch):
        """Return the orders decided on this batch's closing bar."""
        self._note_fills(batch.get("fills", []))
        self._note_account(batch["account"])
        for bar in batch["bars"]:
            if bar["symbol"] == self.symbol:
                self.closes.append(int(bar["c"]))
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
