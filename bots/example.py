from decimal import Decimal
from itertools import pairwise

from ape import chain
from ape_tokens import tokens
from silverback import SilverbackBot

from uniswap_sdk import Uniswap

bot = SilverbackBot()

YFI = tokens["YFI"]
USDC = tokens["USDC"]

uni = Uniswap()
# NOTE: Install `indexer` actions onto `bot` (startup and new pair event)
uni.install(bot)


def calculate_rsi(measurements: list[Decimal]) -> Decimal:
    gains_or_losses = [b - a for a, b in pairwise(measurements)]
    total_measurements = Decimal(len(measurements))
    average_gain = sum([max(gain, Decimal(0)) for gain in gains_or_losses]) / total_measurements
    average_loss = (
        sum([max(abs(loss), Decimal(0)) for loss in gains_or_losses]) / total_measurements
    )
    # RSI normalized to [0.0, 1.0]
    if average_loss == Decimal(0.0):
        return Decimal(1.0)  # Avoid div/0 fault by taking limit
    return 1 - (1 / (1 + average_gain / average_loss))


@bot.on_startup()
async def compute_initial_rsi(_ss):
    # TODO: Load measurements from startup state
    bot.state.measurements = []


@bot.on_(chain.blocks)
async def rsi(blk):
    # TODO: Refactor to use cron?
    # TODO: Use parameter for lookback period vs. constant
    if len(bot.state.measurements) >= 10:
        bot.state.measurements.pop(0)

    bot.state.measurements.append(uni.price(YFI, USDC))

    if len(bot.state.measurements) >= 10:
        rsi = calculate_rsi(bot.state.measurements)
        print(f"RSI: {100 * rsi:0.3f}")
        return rsi
