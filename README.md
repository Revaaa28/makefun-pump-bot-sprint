[![Pump.fun Bot Sprint](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![Rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/Revaaa28/makefun-pump-bot-sprint/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![Reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/Revaaa28/makefun-pump-bot-sprint/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

# Pump Bot Sprint

A paper-trading bot for make.fun's **Pump.fun Bot Sprint: Make Bot And Win**
competition. The bot trades pump.fun bonding curves on a fresh live 6-hour
window collected after entries close, ranked by net PnL. Paper trading only —
no real funds ever move. A prize requires net PnL above 0 SOL after fees,
at least 20 coins each with a completed round trip of at least 0.01 SOL,
and a maximum drawdown of 50% or less.

## Strategy

Early-entry sniping with a rug filter — buy at a coin's birth, leave fast:

- **Entry:** only at a coin's birth — within 12 slots of its create event,
  once real reserves reach 0.3 SOL (so the 1% size cap allows a real
  position), at most 3 distinct buyers so far, and never after the creator
  has sold. One buy per coin, min(cap, 0.1 SOL), which keeps every stake
  comfortably above the 0.01 SOL round-trip minimum.
- **Exits:** take-profit +25%, stop-loss −8%, a hard 50-slot / 40-trade
  max hold, and an immediate exit the moment the creator sells (the
  classic rug tell).
- **Slippage wide open on both sides:** at a coin's birth the price moves
  inside the 2-slot latency, and a tight slippage only converts fills into
  paid-for rejections; in a dump, a rejected sell costs more than a bad
  fill.
- **Activity by design:** new coins launch constantly on pump.fun, so the
  birth-only filter still produces many independent round trips — the
  shape is built to clear the 20-coin minimum bar while keeping each
  position small (0.1 SOL against 10 SOL of paper capital, at most 5 open
  positions) so drawdown stays bounded.

The final score comes from a fresh live 6-hour window that does not exist
yet, so the strategy is deliberately simple and robust rather than tuned
to the dev window's particular coins.

## Layout

- `main.py` — the bot (Python 3.13, standard library only).
- `vendor/makefun_bot.py` — the kit's `agent-fun/replay/v2` Python shim,
  vendored as the SDK instructs.

## Run it

```sh
PYTHONHASHSEED=0 python3 -P -s main.py
```

The bot speaks `agent-fun/replay/v2` on stdin/stdout: it reads `hello`,
one `batch` per slot, and `done`, and writes exactly one reply per
batch. It is fully deterministic — no clock, no environment reads, no
unseeded randomness — so the grader's two executions agree byte for
byte.

## Grade it (dev window)

From the unpacked kit directory:

```sh
node cli/makefun.mjs grade-local --kit . --bot /path/to/this/repo --driver process
```

On the kit's dev window the bot graded at a net PnL of −0.6129 SOL, with the guardrails met: 332 qualifying coins and a maximum drawdown of 6.5%.

## License

MIT. See [LICENSE](LICENSE).
