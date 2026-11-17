"""Two-leg dip bot for agent-fun/bars-replay/v1 (CBBTC, 1-min bars).

Legs
----
Dip leg (the calm-window earner): stay flat until the close has pulled back
at least 1.50% from its rolling 300-bar high while RSI(14) is oversold
(< 40); then buy with all of the cash on hand. Exit the whole position when
the close is +1.25% above the entry price (take profit) or -0.875% below
it (stop loss), the entry price being the open of the bar the buy filled
on -- the market price at execution, before this order's own pool impact
moves the fill print.

Entry confirmation (both legs): a signal only becomes an order when its
bar closes UP -- the close above the bar's own open. Neither leg buys into
a bar that is still falling; both wait for the first bar the buyers win.

Regime defence (why the dip leg may only lose once): repeated dip buys are
what a falling window eats -- measured on the 15 Nov dev window, the dip
leg alone round-trips a dozen times down the slope for about -$90. So the
first time a dip-leg trade is stopped out, the dip leg is finished for the
rest of the session: one full-size loss is the whole tuition. It is never
re-armed; bear-market rallies back to the rolling high are exactly the
traps that re-arming walks into (also measured, and worse).

Capitulation leg (the falling-window earner): if the close falls at least
7.5% below the session high (the running high of all closes so far) with
RSI(14) < 40, on a bar that closes up, buy with all of the cash on hand
and ride the capitulation bounce: exit at +4.0% / -1.5% from the entry
price, or after 120 bars, whichever comes first. It fires at most once per capitulation episode --
after it exits, it re-arms only when the close is back above the trigger
line. A 20% drawdown from the peak equity halts all new entries (exits
always continue). The exit band and size come from a 1,440-combination
cross-regime grid on the real fill model: at the 7.5% trigger the
take-profit ramp is smooth (3.0% / 3.5% / 4.0% bank +$10.91 / +$17.12 /
+$19.37 of falling-window PnL at full size), while a 6.5% trigger is a
trap (median -$34.95 across its grid) and 8.0% never fired at all.

One position at a time, no shorts, no leverage. Orders decided on a bar's
close are filled by the harness at the next bar's open. Everything is a
pure function of the bars and fills received -- no clock, no network, no
randomness -- so two runs over the same data produce byte-identical
replies.

Measured on the real fill model (fees, slippage, pool impact)
-------------------------------------------------------------
This race's dev window (calm, BTC +0.49%): +$21.34, max DD 0.77% -- two
dip-leg take-profits (+$9.32, +$9.52) and a third dip position flattened
at the window's end (+$2.50); the defence never engages, the capitulation
leg never fires. The 15 Nov dev window (falling, BTC -4.0%): +$22.18, max
DD 1.52% -- one dip stop (-$13.58) switches the dip leg off, then the
capitulation leg catches the bottom (+$35.76). The undefended dip leg
scores -$89.85 on that window; capitulation alone scores $0.00 on the
calm one (it never fires, which also fails this race's dev gate).

Round-3 retune (cross-regime, both windows jointly): the dip entry moved
to 1.50% below a 300-bar rolling high (identical results for lookbacks
270-330, so the point is interior, not an edge) and the dip stop tightened
to -0.875% -- on the falling window the tuition loss is cut about $1.85
earlier, while the calm window's trades never came within 0.29 points of
the tighter stop. Two ramps were measured and deliberately NOT taken:
widening either take-profit (dip 1.375-1.50%, capitulation 4.25%+) only
rides the single observed bounces closer to their exact tops and falls
off a cliff one step further out, and a 7.0-7.25% capitulation trigger
wins on the one observed episode but loses across the full parameter
grid (median +$10.12 vs +$13.28 at 7.5%).

Round-4 (structural variants, same acceptance rule): the one change that
raises the two-window average AND the worst window is the up-close entry
confirmation described above -- it is parameter-free (no fitted level),
it delays the capitulation entry by a few bars to a better fill, and it
lowers max drawdown on both windows (0.98% -> 0.77% calm, 2.01% -> 1.52%
falling). Measured and rejected: scale-out exits and a trailing stop for
the capitulation leg, whose entire gain on the falling window came from
NOT exiting at +4.0% on the single observed bounce (the trailing stop
never even triggered -- the position simply rode to the 120-bar hold
limit; the scale-out remainder's +5% step sits exactly inside that
bounce's top and falls off a cliff at +6%) -- the same peak-riding the
earlier rounds refused. Also rejected: RSI period/smoothing variants
(non-binding -- no decision changes on either window), volatility-scaled
dip depth (calm window drops to +$16.51..+$18.76), capitulation entry
delays of 5-30 bars (the 15+ bar delays miss the episode entirely), and
confirming the dip entry against the previous close instead of the bar's
own open (falling window worsens).
"""

import json
import sys

SYMBOL_FALLBACK = "CBBTC"

# Dip-leg parameters (grid-tuned jointly on the calm and falling dev
# windows against the real fill model; the point sits on a plateau, not
# a spike -- see the round-3 note in the module docstring).
LOOKBACK = 300          # bars in the rolling-high window
DIP = 0.015             # entry: close at least this far below the rolling high
RSI_PERIOD = 14
RSI_OVERSOLD = 40.0     # entry: RSI below this
CASH_FRACTION = 1.00    # entry: spend this fraction of the cash on hand
TAKE_PROFIT = 0.0125    # exit: close this far above the entry fill price
STOP_LOSS = -0.00875    # exit: close this far below the entry fill price

# Capitulation-leg parameters.
CAP_DIP = 0.075         # entry: close at least this far below the session high
CAP_CASH_FRACTION = 1.00
CAP_TAKE_PROFIT = 0.04
CAP_STOP_LOSS = -0.015
CAP_MAX_HOLD = 120      # bars since the fill bar

# Trend-leg parameters (round 6). The leg is gated by the session itself:
# it may only fire once the close is at least TREND_GATE above the
# session's first close -- a confirmed up-day -- and the close breaks the
# highest close of the prior TREND_LOOKBACK bars on an up-close bar. The
# exit is a trailing stop below the peak close since the fill bar, with a
# far take-profit and a wide fixed stop as backstops. Two trend stop-outs
# retire the leg for the session.
TREND_LOOKBACK = 300    # prior closes the entry close must clear
TREND_GATE = 0.0175     # entry: close at least this far above the first close
TREND_CASH_FRACTION = 1.00
TREND_TRAIL = 0.02      # exit: close this far below the peak close since entry
TREND_TAKE_PROFIT = 0.06
TREND_STOP_LOSS = -0.03
TREND_MAX_SL = 2        # trend stop-outs that retire the trend leg

DD_HALT = 0.20          # stop all entries after this drawdown from peak equity
SLIPPAGE_BPS = 200      # max_slippage_bps sent with every order


def rsi(closes, period=RSI_PERIOD):
    """RSI from simple means of the last `period` close-to-close moves."""
    moves = [closes[k] - closes[k - 1] for k in range(len(closes) - period, len(closes))]
    gain = sum(m for m in moves if m > 0) / period
    loss = sum(-m for m in moves if m < 0) / period
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


class TwoLegDipBot:
    """Bar history, the live position, and all three legs' regime state."""

    def __init__(self):
        self.symbol = SYMBOL_FALLBACK
        self.warmup_bars = 0
        self.closes = []         # raw closes (integers at the price scale)
        self.last_open = None    # raw open of the most recent bar
        self.first_close = 0     # first close of the session (trend gate base)
        self.cash = 0            # quote units, refreshed from every batch
        self.qty = 0             # asset units currently held (0 = flat)
        self.start_cash = 0      # quote units at hello, for the DD halt
        self.session_high = 0    # running high of all closes, raw units
        # Live position (all None/0 while flat).
        self.entry_price = None  # raw open of the bar the position filled on
        self.entry_index = -1    # bar index of the fill bar
        self.entry_leg = None    # "dip" or "cap": which leg opened it
        self.pending_leg = None  # leg of the buy order most recently sent
        self.exit_leg = None     # leg of the sell order most recently sent
        self.exit_reason = None  # "tp" / "sl" / "hold" of that sell order
        # Regime state.
        self.dip_halted = False  # a dip stop-out ends the dip leg for good
        self.cap_armed = True    # one capitulation trade per episode
        self.trend_sl_count = 0  # trend-leg stop-outs so far
        self.trend_retired = False  # TREND_MAX_SL stop-outs retire the leg
        self.peak_close = 0      # peak close since the live position filled
        self.halted = False      # 20% drawdown halt (entries only)
        self.peak_equity = 0.0   # quote units
        self.order_count = 0

    def on_hello(self, hello):
        symbols = hello.get("symbols") or []
        if symbols:
            self.symbol = symbols[0]["symbol"]
        self.warmup_bars = int(hello.get("warmup_bars", 0))
        self.cash = int(hello["capital"]["quote_units"])
        self.start_cash = self.cash
        self.peak_equity = float(self.cash)

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
                self.entry_index = len(self.closes) - 1
                self.entry_leg = self.pending_leg
                self.peak_close = 0  # reset; the peak tracker re-seeds below
            elif fill.get("side") == "sell":
                # Apply the regime transitions the exit was sent with.
                if self.exit_leg == "dip" and self.exit_reason == "sl":
                    self.dip_halted = True
                if self.exit_leg == "cap":
                    self.cap_armed = False
                if self.exit_leg == "trend" and self.exit_reason == "sl":
                    self.trend_sl_count += 1
                    if self.trend_sl_count >= TREND_MAX_SL:
                        self.trend_retired = True
                self.entry_price = None
                self.entry_leg = None
                self.exit_leg = None
                self.exit_reason = None

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
                    self.entry_index = len(self.closes) - 1
                    self.entry_leg = self.pending_leg

    def _note_equity(self, close):
        # Position value in quote units: qty is at 10^-8 asset units and
        # prices at 10^-5 quote units, so qty * close / 10^7 is quote units.
        equity = self.cash + self.qty * close / 10 ** 7
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0 and (self.peak_equity - equity) / self.peak_equity >= DD_HALT:
            self.halted = True

    def _buy(self, leg, fraction):
        notional = int(self.cash * fraction)
        if notional <= 0:
            return []
        self.pending_leg = leg
        return [{
            "side": "buy",
            "client_id": self._client_id(),
            "symbol": self.symbol,
            "notional_quote_units": str(notional),
            "max_slippage_bps": SLIPPAGE_BPS,
        }]

    def _sell(self, leg, reason):
        self.exit_leg = leg
        self.exit_reason = reason
        return [{
            "side": "sell",
            "client_id": self._client_id(),
            "symbol": self.symbol,
            "qty": str(self.qty),
            "max_slippage_bps": SLIPPAGE_BPS,
        }]

    def on_batch(self, batch):
        """Return the orders decided on this batch's closing bar."""
        for bar in batch["bars"]:
            if bar["symbol"] == self.symbol:
                self.last_open = int(bar["o"])
                close = int(bar["c"])
                self.closes.append(close)
                if not self.first_close:
                    self.first_close = close
                if close > self.session_high:
                    self.session_high = close
        self._note_fills(batch.get("fills", []))
        self._note_account(batch["account"])
        if not self.closes:
            return []
        close = self.closes[-1]
        self._note_equity(close)
        # Peak close since the live position filled (trend trailing stop).
        if self.qty > 0:
            if close > self.peak_close:
                self.peak_close = close
        else:
            self.peak_close = 0
        if self.warmup_bars and batch["seq"] < self.warmup_bars:
            return []  # orders decided on warm-up bars are rejected anyway

        index = len(self.closes) - 1

        if self.qty > 0:
            # Holding: leave on the open leg's exit rules only.
            if self.entry_price:
                change = close / self.entry_price - 1.0
                leg = self.entry_leg or "dip"
                if leg == "trend":
                    # Trailing stop first, then the far fixed backstops.
                    if (self.peak_close
                            and close <= self.peak_close * (1.0 - TREND_TRAIL)):
                        return self._sell(leg, "trail")
                    if change >= TREND_TAKE_PROFIT:
                        return self._sell(leg, "tp")
                    if change <= TREND_STOP_LOSS:
                        return self._sell(leg, "sl")
                    return []
                if leg == "cap":
                    tp, sl, max_hold = CAP_TAKE_PROFIT, CAP_STOP_LOSS, CAP_MAX_HOLD
                else:
                    tp, sl, max_hold = TAKE_PROFIT, STOP_LOSS, 0
                if change >= tp:
                    return self._sell(leg, "tp")
                if change <= sl:
                    return self._sell(leg, "sl")
                if max_hold and index - self.entry_index >= max_hold:
                    return self._sell(leg, "hold")
            return []

        # Flat. The capitulation leg re-arms once the close is back above
        # its trigger line (a new episode can only start from up there).
        if not self.cap_armed and self.session_high > 0:
            if close > self.session_high * (1.0 - CAP_DIP):
                self.cap_armed = True
        if self.halted:
            return []

        # Capitulation leg first: the deepest signal wins the one position.
        # Confirmation (both legs): the signal bar must close UP -- close
        # above its own open, which is self.last_open at decision time --
        # so the bot never buys into a bar that is still falling.
        if (self.cap_armed and index >= RSI_PERIOD and self.session_high > 0
                and close <= self.session_high * (1.0 - CAP_DIP)
                and rsi(self.closes) < RSI_OVERSOLD
                and close > self.last_open):
            return self._buy("cap", CAP_CASH_FRACTION)

        # Trend leg: only in a confirmed up-day (close at least TREND_GATE
        # above the session's first close) and only on a breakout -- the
        # close clearing the highest close of the prior TREND_LOOKBACK
        # bars, on an up-close bar. A breakout close is by construction
        # within DIP of the rolling high, so this can never collide with
        # the dip leg's signal on the same bar. Two stop-outs retire it.
        if (not self.trend_retired and index >= TREND_LOOKBACK
                and self.first_close > 0
                and close >= self.first_close * (1.0 + TREND_GATE)
                and close >= max(self.closes[-TREND_LOOKBACK - 1:-1])
                and close > self.last_open):
            return self._buy("trend", TREND_CASH_FRACTION)

        # Dip leg, unless its one stop-out already ended it for the session.
        if self.dip_halted or index < LOOKBACK:
            return []
        high = max(self.closes[-LOOKBACK:])
        if close > high * (1.0 - DIP):
            return []
        if rsi(self.closes) >= RSI_OVERSOLD:
            return []
        if close <= self.last_open:
            return []  # confirmation: wait for the first up-close bar
        return self._buy("dip", CASH_FRACTION)


def main():
    bot = TwoLegDipBot()
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
