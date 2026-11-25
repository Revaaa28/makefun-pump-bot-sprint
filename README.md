[![Pump.fun Bot Sprint](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![Rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/Revaaa28/makefun-pump-bot-sprint/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![Reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/Revaaa28/makefun-pump-bot-sprint/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

# Pump Bot Sprint

A paper-trading bot for a pump.fun bot competition. Entries compete
on a fresh 6-hour pump.fun window that is collected after entries
close, so no entry can be tuned to the actual test data in advance.
Ranking is by net PnL. A prize requires net PnL above zero after
fees, at least 20 coins with a round trip of at least 0.01 SOL each,
and a maximum drawdown of 50%.

## Strategy

### Entry

The bot buys a coin at most once, ever, and only when every one of
these conditions holds at the same time:

- The coin is young: no more than 12 slots have passed since its
  create event.
- The creator has not sold any of their own coin.
- The coin is still uncrowded: no more than 2 distinct buyer wallets
  have bought so far.
- The coin is not already held and no buy for it is already pending.
- The bonding curve has not completed.
- The curve holds between 0.5 SOL and 30 SOL of real quote reserves:
  enough depth that the coin is real, not so much that the early
  window has already passed.
- Fewer than the maximum number of open positions (5) are committed.

Each buy is a single small clip: the smaller of the harness's maximum
buy for that mint and 0.02 SOL, never less than 0.01 SOL, and only
when free cash covers the buy plus the transaction fee. Every order
uses 10,000 bps slippage tolerance, so an order the bot decides to
place is an order that executes at the curve's price rather than
failing on a tolerance check.

### Exit

The bot sells the full position as soon as any one of these fires:

- Price is up 25% from entry.
- Price is down 8% from entry.
- The creator sold any of their own coin.
- 25 trades have printed on the mint while the position was held.
- The position is 30 slots old.

Price comparisons use integer cross-multiplication against the
position's recorded cost (virtual quote reserves times tokens held,
against virtual token reserves times cost, scaled by 5/4 for the
gain trigger and 23/25 for the loss trigger) — no floating-point
division anywhere. A sell is skipped if one is already pending for
that mint. Trackers for a position are forgotten once it closes, and
trackers for a curve are forgotten once it completes.

## Why it is built this way

- **Fee drag.** Every round trip pays transaction fees on top of the
  curve's own spread, so small, short trades with a tight loss
  trigger lose slowly and capped, while the +25% trigger is wide
  enough to clear fees with room to spare. The fixed 0.02 SOL clip
  and the one-buy-per-coin rule also spread entries across many
  coins, which is what the 20-coin round-trip requirement rewards.
- **Rug filter.** A creator selling their own coin in its first
  slots is the clearest early danger signal on pump.fun. It blocks
  new entries outright and, if it happens while a position is held,
  exits that position immediately instead of waiting for the price
  triggers to notice.
- **Determinism.** The bot uses no clock, no randomness, and no
  environment reads. Given the same replay stream it makes exactly
  the same orders, so the submitted code is precisely the behaviour
  that gets scored on the competition window.
