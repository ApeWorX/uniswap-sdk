import os

from ape_tokens import tokens
from silverback import SilverbackBot

from uniswap_sdk import Uniswap

bot = SilverbackBot()
uni = Uniswap()
uni.install(bot, tokens=os.environ.get("INTERMEDIATE_TOKENS", "").split(",") or tokens)

QUOTE_TOKEN = os.environ.get("QUOTE_TOKEN", "WETH")
BASE_TOKEN = os.environ.get("BASE_TOKEN", "USDC")


@bot.cron(os.environ.get("MEASUREMENT_CRON", "* * * * *"))
async def current_price(t):
    return uni.price(QUOTE_TOKEN, BASE_TOKEN)
