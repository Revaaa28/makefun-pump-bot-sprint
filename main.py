"""Pump Bot Sprint entry bot.

Strategy in plain terms:
  Buy very early, only while a coin still looks clean and uncrowded,
  in one small fixed-size clip, and never more than once per coin.
  Sell the whole position fast on a modest gain, a small loss, a
  creator exit, or simple staleness -- because on fresh pump.fun
  curves the fee drag and rug risk punish anyone who lingers.

The bot is deterministic: no clock, no randomness, no environment
reads.  Every decision is a pure function of the replay stream, the
positions the harness reports, and the trackers built from events.
"""

import os
import sys

# The harness shim lives in vendor/ beside this file.
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

import makefun_bot  # noqa: E402

# --- Constants (lamports unless noted) -------------------------------------

MIN_REAL_QUOTE_LAMPORTS = 500_000_000        # 0.5 SOL of real curve depth
MAX_REAL_QUOTE_LAMPORTS = 30_000_000_000     # 30 SOL of real curve depth
MAX_BUY_LAMPORTS = 20_000_000                # 0.02 SOL clip per entry
MIN_BUY_LAMPORTS = 10_000_000                # 0.01 SOL minimum round trip
SLIPPAGE_BPS = 10_000                        # accept any execution price
MAX_ENTRY_AGE_SLOTS = 12                     # buy only in the first 12 slots
MAX_DISTINCT_BUYERS_AT_ENTRY = 2             # buy only while still uncrowded
MAX_TRADES_WHILE_HELD = 25                   # exit after 25 prints held
MAX_HOLD_SLOTS = 30                          # exit after 30 slots held
DEFAULT_MAX_OPEN_POSITIONS = 5
DEFAULT_MAX_ORDERS_PER_BATCH = 16

# --- Trackers ---------------------------------------------------------------

# mint -> {"create_slot", "creator", "buyers", "creator_sold", "bought"}
_coins = {}
# mint -> {"first_seen_slot", "trades_while_held"}
_holdings = {}


def _field(obj, name, default=None):
    """Read a named field from either a dict or an attribute object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _pending_orders(state):
    """Return a list of in-flight order dicts from state.pending."""
    orders = []
    pending = getattr(state, "pending", None) or {}
    values = pending.values() if isinstance(pending, dict) else pending
    for entry in values:
        order = _field(entry, "order", entry)
        if isinstance(order, dict):
            orders.append(order)
    return orders


def _position(state, mint):
    positions = getattr(state, "positions", None) or {}
    if isinstance(positions, dict):
        return positions.get(mint)
    return getattr(positions, mint, None)


def _curve(state, mint):
    curves = getattr(state, "curves", None) or {}
    if isinstance(curves, dict):
        return curves.get(mint)
    return getattr(curves, mint, None)


def _caps(state):
    hello = getattr(state, "hello", None) or {}
    caps = _field(hello, "caps", {}) or {}
    max_open = _field(caps, "max_open_positions", DEFAULT_MAX_OPEN_POSITIONS)
    max_orders = _field(caps, "max_orders_per_batch", DEFAULT_MAX_ORDERS_PER_BATCH)
    return int(max_open), int(max_orders)


def _ingest_events(state, batch):
    """Update coin and holding trackers from one batch of events.

    Returns the mints in the order they first appeared in this batch,
    which keeps candidate scanning deterministic.
    """
    seen_order = []
    seen_set = set()
    events = []
    if isinstance(batch, dict):
        events = batch.get("events") or []

    for event in events:
        mint = _field(event, "mint")
        if not mint:
            continue
        if mint not in seen_set:
            seen_set.add(mint)
            seen_order.append(mint)

        kind = _field(event, "event")
        user = _field(event, "user")

        if kind == "CreateEvent":
            if mint not in _coins:
                _coins[mint] = {
                    "create_slot": int(state.slot),
                    "creator": _field(event, "creator", user),
                    "buyers": set(),
                    "creator_sold": False,
                    "bought": False,
                }
            continue

        # Any non-create event for an unknown coin still gets a tracker
        # anchored at the current slot, so later rules have state to read.
        if mint not in _coins:
            _coins[mint] = {
                "create_slot": int(state.slot),
                "creator": None,
                "buyers": set(),
                "creator_sold": False,
                "bought": False,
            }
        tracker = _coins[mint]

        if kind == "TradeEvent":
            is_buy = _field(event, "is_buy")
            if is_buy and user:
                tracker["buyers"].add(user)
            creator = tracker.get("creator")
            if creator is not None and user == creator and not is_buy:
                tracker["creator_sold"] = True
            if mint in _holdings:
                _holdings[mint]["trades_while_held"] += 1

    return seen_order


def _refresh_holdings(state):
    """Create holding trackers for live positions; drop stale ones."""
    positions = getattr(state, "positions", None) or {}
    items = positions.items() if isinstance(positions, dict) else []
    live = set()
    for mint, pos in items:
        amount = int(_field(pos, "token_amount", 0) or 0)
        if amount > 0:
            live.add(mint)
            if mint not in _holdings:
                _holdings[mint] = {
                    "first_seen_slot": int(state.slot),
                    "trades_while_held": 0,
                }
            coin = _coins.get(mint)
            if coin is not None:
                coin["bought"] = True
    # Forget trackers for closed positions (no pending sell check here;
    # the sell pass re-checks pending state before ordering).
    for mint in list(_holdings):
        if mint not in live:
            del _holdings[mint]
    return live


def _forget_completed(state, live):
    """Forget trackers for completed curves we no longer hold."""
    curves = getattr(state, "curves", None) or {}
    items = curves.items() if isinstance(curves, dict) else []
    for mint, curve in items:
        if _field(curve, "complete") and mint not in live:
            _coins.pop(mint, None)
            _holdings.pop(mint, None)


def _buy_candidates(state, seen_order, pending, live, max_open, max_orders):
    """Apply the entry rules and return buy orders for this batch."""
    orders = []
    pending_buy_mints = {
        o.get("mint") for o in pending if o.get("side") == "buy"
    }
    free_cash = int(state.free_cash_lamports())
    fee = int(state.tx_fee_lamports())
    committed = int(state.committed_position_count())
    spent_this_batch = 0  # buys already ordered here also claim cash

    for mint in seen_order:
        if len(orders) >= max_orders:
            break
        tracker = _coins.get(mint)
        if tracker is None or tracker["bought"]:
            continue
        if int(state.slot) - int(tracker["create_slot"]) > MAX_ENTRY_AGE_SLOTS:
            continue
        if tracker["creator_sold"]:
            continue
        if len(tracker["buyers"]) > MAX_DISTINCT_BUYERS_AT_ENTRY:
            continue
        if mint in live or mint in pending_buy_mints:
            continue
        curve = _curve(state, mint)
        if curve is None or _field(curve, "complete"):
            continue
        real_quote = int(_field(curve, "real_quote_reserves", 0) or 0)
        if real_quote < MIN_REAL_QUOTE_LAMPORTS:
            continue
        if real_quote > MAX_REAL_QUOTE_LAMPORTS:
            continue
        if committed + len(orders) >= max_open:
            continue
        amount = min(int(state.max_buy_lamports(mint)), MAX_BUY_LAMPORTS)
        if amount < MIN_BUY_LAMPORTS:
            continue
        if amount + fee > free_cash - spent_this_batch:
            continue
        orders.append(
            {
                "side": "buy",
                "mint": mint,
                "quote_lamports": amount,
                "max_slippage_bps": SLIPPAGE_BPS,
            }
        )
        spent_this_batch += amount + fee
        tracker["bought"] = True
    return orders


def _sell_orders(state, pending, live, max_orders):
    """Apply the exit rules and return full-position sell orders.

    Price checks use integer cross-multiplication only -- no floats:
      left = virtual_quote_reserves * token_amount
      base = virtual_token_reserves * cost_lamports
    left / base is the current value relative to entry cost, so
    +25% is left * 4 >= base * 5 and -8% is left * 25 <= base * 23
    (23/25 = 0.92, the fraction of entry value left after an 8% fall).
    """
    orders = []
    pending_sell_mints = {
        o.get("mint") for o in pending if o.get("side") == "sell"
    }
    for mint in sorted(live):
        if len(orders) >= max_orders:
            break
        if mint in pending_sell_mints:
            continue
        pos = _position(state, mint)
        token_amount = int(_field(pos, "token_amount", 0) or 0)
        cost = int(_field(pos, "cost_lamports", 0) or 0)
        if token_amount <= 0 or cost <= 0:
            continue
        curve = _curve(state, mint)
        if curve is None:
            continue
        v_quote = int(_field(curve, "virtual_quote_reserves", 0) or 0)
        v_token = int(_field(curve, "virtual_token_reserves", 0) or 0)

        should_sell = False
        if v_quote > 0 and v_token > 0:
            left = v_quote * token_amount
            base = v_token * cost
            if left * 4 >= base * 5:      # up 25% from entry
                should_sell = True
            elif left * 25 <= base * 23:  # down 8% from entry
                should_sell = True

        tracker = _coins.get(mint)
        if tracker is not None and tracker["creator_sold"]:
            should_sell = True

        holding = _holdings.get(mint)
        if holding is not None:
            if holding["trades_while_held"] >= MAX_TRADES_WHILE_HELD:
                should_sell = True
            if int(state.slot) - int(holding["first_seen_slot"]) >= MAX_HOLD_SLOTS:
                should_sell = True

        if should_sell:
            orders.append(
                {
                    "side": "sell",
                    "mint": mint,
                    "token_amount": token_amount,
                    "max_slippage_bps": SLIPPAGE_BPS,
                }
            )
    return orders


def on_batch(state, batch):
    """Process one slot batch and return this batch's orders."""
    max_open, max_orders = _caps(state)
    seen_order = _ingest_events(state, batch)
    live = _refresh_holdings(state)
    _forget_completed(state, live)
    pending = _pending_orders(state)

    # Exits first: freeing a slot and realising a result matter more
    # than opening a new position with the same order budget.
    sells = _sell_orders(state, pending, live, max_orders)
    remaining = max_orders - len(sells)
    buys = []
    if remaining > 0:
        buys = _buy_candidates(
            state, seen_order, pending, live, max_open, remaining
        )
    return sells + buys


if __name__ == "__main__":
    makefun_bot.run(on_batch)
