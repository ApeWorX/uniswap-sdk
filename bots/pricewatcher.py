import os

from ape import convert
from ape.types import AddressType
from ape_tokens import Token, tokens
from silverback import SilverbackBot

from uniswap_sdk import Uniswap

bot = SilverbackBot()

QUOTE_TOKEN = Token.at(convert(os.environ.get("QUOTE_TOKEN", "USDC"), AddressType))
TOKENS_TO_WATCH = [
    Token.at(convert(t, AddressType)) for t in os.environ.get("TOKENS_TO_WATCH", "").split(",")
]

if len(TOKENS_TO_WATCH) < 1:
    raise RuntimeError("Needs at least 1 token to work")

uni = Uniswap()

if INTERMEDIATE_TOKENS := os.environ.get("INTERMEDIATE_TOKENS", "").split(","):
    uni.install(bot, tokens=[QUOTE_TOKEN, *TOKENS_TO_WATCH, *INTERMEDIATE_TOKENS])

else:  # Index entire tokenlist for quoting
    uni.install(bot, tokens=tokens)


@bot.cron(os.environ.get("MEASUREMENT_CRON", "* * * * *"))
async def measure(_):
    return {
        f"{base.symbol()}/{QUOTE_TOKEN.symbol()}": uni.price(base, QUOTE_TOKEN)
        for base in TOKENS_TO_WATCH
    }
