"""Pump.fun Bot Sprint entry: birth-window sniper, small stakes, fast exits.

Protocol in one breath: the harness says hello once (capital, caps, fees),
then streams one batch of market events per slot; for each batch the bot
answers with the orders it wants, and fills come back in later batches.
This bot only buys a coin in its birth window -- the first few slots after
its CreateEvent -- only while the creator has not sold, and only while a
handful of distinct wallets at most have bought in front of it. Every
position is closed by whichever exit rule fires first: take-profit,
stop-loss, the creator selling, a burst of trades while held, or a plain
age limit in slots. One buy per coin, ever, sized well above the 0.01 SOL
round-trip minimum but small against the 10 SOL of paper capital, so the
bot clears the 20-coin activity bar with bounded drawdown.
"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
)

import makefun_bot  # noqa: E402  (after the sys.path line above)

# --- Entry tuning -----------------------------------------------------------
ENTRY_WINDOW_SLOTS = 12          # buy only within this many slots of creation
MAX_DISTINCT_BUYERS = 2          # buy only while few wallets are in already
MIN_REAL_QUOTE = 500_000_000     # 0.5 SOL of real quote reserves, at least
MAX_REAL_QUOTE = 30_000_000_000  # 30 SOL of real quote reserves, at most
STAKE_LAMPORTS = 20_000_000      # 0.02 SOL target size per entry
MIN_BUY_LAMPORTS = 10_000_000    # never send a buy smaller than 0.01 SOL

# --- Exit tuning ------------------------------------------------------------
TAKE_PROFIT_NUM = 5              # sell at +25% over the entry price (x5/4)
TAKE_PROFIT_DEN = 4
STOP_LOSS_NUM = 23               # sell at -8% under the entry price (x23/25)
STOP_LOSS_DEN = 25
MAX_HELD_TRADES = 25             # sell once this many trades print while held
MAX_HOLD_SLOTS = 30              # sell once the position is this old (slots)

SLIPPAGE_BPS = 10_000            # wide open: the replay decides the fill

# Per-coin memory, keyed by mint in first-seen (insertion) order:
#   create_slot -- state.slot on the batch that carried the CreateEvent
#   creator     -- the wallet named by the CreateEvent
#   buyers      -- distinct buying wallets so far, as dict keys (ordered)
#   dev_sold    -- True once a TradeEvent shows the creator selling
#   entered     -- True once we have sent our one allowed buy for this coin
_coins = {}

# Per-position memory for coins we currently hold:
#   entry_slot  -- state.slot on the first batch that showed the holding
#   trades      -- TradeEvents seen on the mint while the holding exists
_holdings = {}


def _note_events(state, batch):
    """Fold this batch's events into the per-coin and per-holding memory."""
    # Holdings first seen on this batch start their exit clocks now.
    for mint in state.positions:
        if mint not in _holdings:
            _holdings[mint] = {"entry_slot": state.slot, "trades": 0}
    # Forget trackers for positions that are gone (sold or closed for us).
    for mint in list(_holdings):
        if mint not in state.positions:
            del _holdings[mint]

    for event in batch["events"]:
        mint = event.get("mint")
        if mint is None:
            continue
        kind = event.get("event")
        if kind == "CreateEvent":
            if mint not in _coins:
                _coins[mint] = {
                    "create_slot": state.slot,
                    "creator": event.get("creator") or event.get("user"),
                    "buyers": {},
                    "dev_sold": False,
                    "entered": False,
                }
        elif kind == "TradeEvent":
            info = _coins.get(mint)
            user = event.get("user")
            if info is not None and user is not None:
                if event.get("is_buy"):
                    info["buyers"][user] = True
                elif info["creator"] is not None and user == info["creator"]:
                    info["dev_sold"] = True
            tracker = _holdings.get(mint)
            if tracker is not None:
                tracker["trades"] += 1
        elif kind == "CompleteEvent":
            _coins.pop(mint, None)


def _pending_mints(state, side):
    """Mints that already have an order of the given side in flight."""
    found = {}
    for pending in state.pending.values():
        if pending.order["side"] == side:
            found[pending.order["mint"]] = True
    return found


def _exit_orders(state, max_orders):
    """Full-position sells for every held coin whose exit rule has fired."""
    orders = []
    sell_pending = _pending_mints(state, "sell")
    for mint, holding in state.positions.items():
        if len(orders) >= max_orders:
            break
        if mint in sell_pending:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.virtual_token_reserves <= 0:
            continue
        if holding.token_amount <= 0 or holding.cost_lamports <= 0:
            continue
        # Compare prices by cross-multiplication, in integers, not floats:
        # price >= entry * 5/4  <=>  vq * tokens * 4 >= vt * cost * 5, and
        # price <= entry * 23/25 <=> vq * tokens * 25 <= vt * cost * 23.
        # Integer arithmetic keeps borderline exits exact and identical on
        # every replay, where a float division could round a price sitting
        # precisely on a threshold the wrong way.
        lhs = curve.virtual_quote_reserves * holding.token_amount
        rhs = curve.virtual_token_reserves * holding.cost_lamports
        info = _coins.get(mint)
        tracker = _holdings.get(mint)
        held_trades = tracker["trades"] if tracker is not None else 0
        entry_slot = tracker["entry_slot"] if tracker is not None else state.slot
        creator_sold = info["dev_sold"] if info is not None else False
        if (
            lhs * TAKE_PROFIT_DEN >= rhs * TAKE_PROFIT_NUM
            or lhs * STOP_LOSS_DEN <= rhs * STOP_LOSS_NUM
            or creator_sold
            or held_trades >= MAX_HELD_TRADES
            or state.slot - entry_slot >= MAX_HOLD_SLOTS
        ):
            orders.append(
                {
                    "side": "sell",
                    "mint": mint,
                    "token_amount": holding.token_amount,
                    "max_slippage_bps": SLIPPAGE_BPS,
                }
            )
    return orders


def _entry_orders(state, orders, max_orders):
    """First-and-only buys for fresh coins that pass every entry filter."""
    caps = state.hello["caps"]
    max_open = caps["max_open_positions"]
    tx_fee = state.tx_fee_lamports()
    committed = state.committed_position_count()
    free_cash = state.free_cash_lamports()
    buy_pending = _pending_mints(state, "buy")

    for mint, info in _coins.items():
        if len(orders) >= max_orders:
            break
        if info["entered"]:
            continue
        if state.slot - info["create_slot"] > ENTRY_WINDOW_SLOTS:
            continue
        if info["dev_sold"]:
            continue
        if len(info["buyers"]) > MAX_DISTINCT_BUYERS:
            continue
        if mint in state.positions or mint in buy_pending:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        if curve.real_quote_reserves < MIN_REAL_QUOTE:
            continue
        if curve.real_quote_reserves > MAX_REAL_QUOTE:
            continue
        if committed >= max_open:
            continue
        amount = min(state.max_buy_lamports(mint), STAKE_LAMPORTS)
        if amount < MIN_BUY_LAMPORTS:
            continue
        if free_cash < amount + tx_fee:
            continue
        orders.append(
            {
                "side": "buy",
                "mint": mint,
                "quote_lamports": amount,
                "max_slippage_bps": SLIPPAGE_BPS,
            }
        )
        info["entered"] = True
        buy_pending[mint] = True
        committed += 1
        free_cash -= amount + tx_fee
    return orders


def on_batch(state, batch):
    _note_events(state, batch)
    max_orders = state.hello["caps"]["max_orders_per_batch"]
    orders = _exit_orders(state, max_orders)
    orders = _entry_orders(state, orders, max_orders)
    return orders


if __name__ == "__main__":
    makefun_bot.run(on_batch)
