import os
from decimal import Decimal

from ape.exceptions import ContractLogicError
from ape_tokens import tokens
from silverback import SilverbackBot

from uniswap_sdk import Uniswap

bot = SilverbackBot(signer_required=True)
uni = Uniswap()

TOKENA = tokens[os.environ.get("TOKENA", "USDT")]
TOKENB = tokens[os.environ.get("TOKENB", "USDC")]

if intermediate_tokens := os.environ.get("INTERMEDIATE_TOKENS"):
    uni.install(bot, tokens=[TOKENA, *intermediate_tokens.split(","), TOKENB])

else:
    uni.install(bot, tokens=tokens)

REFERENCE_PRICE = Decimal(os.environ.get("REFERENCE_PRICE", 1))
ARB_THRESHOLD = Decimal(os.environ.get("ARBITRAGE_THRESHOLD", "0.025")) * REFERENCE_PRICE
MAX_SWAP_SIZE_TOKENA = Decimal(os.environ.get("MAX_SWAP_SIZE_TOKENA", "inf"))
MAX_SWAP_SIZE_TOKENB = Decimal(os.environ.get("MAX_SWAP_SIZE_TOKENB", "inf"))

USE_PRIVATE_MEMPOOL = bool(os.environ.get("USE_PRIVATE_MEMPOOL", False))


@bot.on_startup()
async def load_inventory(_ss):
    bot.state.inventory = {
        TOKENA: Decimal(TOKENA.balanceOf(bot.signer)) / Decimal(10 ** TOKENA.decimals()),
        TOKENB: Decimal(TOKENB.balanceOf(bot.signer)) / Decimal(10 ** TOKENB.decimals()),
    }

    return {
        TOKENA.symbol(): bot.state.inventory[TOKENA],
        TOKENB.symbol(): bot.state.inventory[TOKENB],
    }


# NOTE: Monitor inventory so we have it in memory
@bot.on_(TOKENA.Transfer, receiver=bot.signer)
async def add_inventory_tokenA(log):
    bot.state.inventory[TOKENA] += Decimal(log.amount) / Decimal(10 ** TOKENA.decimals())
    return {TOKENA.symbol(): bot.state.inventory[TOKENA]}


@bot.on_(TOKENA.Transfer, sender=bot.signer)
async def rm_inventory_tokenA(log):
    bot.state.inventory[TOKENA] -= Decimal(log.amount) / Decimal(10 ** TOKENA.decimals())
    return {TOKENA.symbol(): bot.state.inventory[TOKENA]}


@bot.on_(TOKENA.Transfer, receiver=bot.signer)
async def add_inventory_tokenB(log):
    bot.state.inventory[TOKENB] += Decimal(log.amount) / Decimal(10 ** TOKENB.decimals())
    return {TOKENB.symbol(): bot.state.inventory[TOKENB]}


@bot.on_(TOKENA.Transfer, sender=bot.signer)
async def rm_inventory_tokenB(log):
    bot.state.inventory[TOKENB] -= Decimal(log.amount) / Decimal(10 ** TOKENB.decimals())
    return {TOKENB.symbol(): bot.state.inventory[TOKENB]}


@bot.cron(os.environ.get("MEASUREMENT_CRON", "*/5 * * * *"))
async def current_price(_):
    return uni.price(TOKENA, TOKENB)


@bot.on_metric("current_price")
async def price_delta(current_price: Decimal):
    # NOTE: Have a more accurate reference model in production use
    return current_price - REFERENCE_PRICE


@bot.on_metric("price_delta", lt=-ARB_THRESHOLD)
async def buy(_):
    # NOTE: Scale buys in production use
    if (sell_amount := min(MAX_SWAP_SIZE_TOKENB, bot.state.inventory[TOKENB])) > 0:
        try:
            uni.swap(
                have=TOKENB,
                want=TOKENA,
                amount_in=sell_amount,
                min_amount_out=sell_amount * REFERENCE_PRICE,
                sender=bot.signer,
                private=USE_PRIVATE_MEMPOOL,
            )

        except ContractLogicError:
            pass


@bot.on_metric("price_delta", gt=ARB_THRESHOLD)
async def sell(_):
    # NOTE: Scale sells in production use
    if (sell_amount := min(MAX_SWAP_SIZE_TOKENA, bot.state.inventory[TOKENA])) > 0:
        try:
            uni.swap(
                have=TOKENA,
                want=TOKENB,
                amount_in=sell_amount,
                min_amount_out=sell_amount * REFERENCE_PRICE,
                sender=bot.signer,
                private=USE_PRIVATE_MEMPOOL,
            )

        except ContractLogicError:
            pass
