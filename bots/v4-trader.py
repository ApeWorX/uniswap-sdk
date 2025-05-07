import os

from ape import Contract
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS
from ape_tokens import Token
from pydantic import BaseModel
from silverback import SilverbackBot

MAX_BUY = float(os.environ.get("MAX_BUY", "0.01"))
SLIPPAGE = 1.0 + float(os.environ.get("SLIPPAGE", "0.05"))

bot = SilverbackBot()
v4 = Contract("0xE03A1074c86CFeDd5C142C4F04F1a1536e203543")
v4_state_view = Contract("0xE1Dd9c3fA50EDB962E442f60DfBc432e24537E4C")


class Pool(BaseModel):
    currency0: AddressType
    currency1: AddressType
    fee: int
    tickSpacing: int
    hooks: AddressType


@bot.on_startup()
async def load_pools(_):
    # PoolId => Token
    bot.state.pools = {}


@bot.on_(v4.Initialize)
async def buy(log):
    if log.currency0 != ZERO_ADDRESS:
        return

    token = Token.at(log.currency1)
    # NOTE: Track for later
    pool = bot.state.pools[log.id] = Pool(
        currency0=ZERO_ADDRESS,
        currency1=token.address,
        fee=log.fee,
        tickSpacing=log.tickSpacing,
        hooks=log.hooks,
    )

    ratio = token.balanceOf(v4) / token.totalSupply()
    v4.swap(
        pool,
        # NOTE: Max is 1%
        (True, int(ratio * MAX_BUY * bot.balance), int(SLIPPAGE * log.sqrtPriceX96)),
        b"",
        sender=bot.signer,
        nonce=bot.nonce,
    )


@bot.on_(v4.Swap)
async def sell(log):
    if not (pool := bot.state.pool_params.get(log.id)):
        return

    token = Token.at(pool.currency1)
    balance = token.balanceOf(bot.signer)
    ratio = token.balanceOf(v4) / token.totalSupply()
    pool_state = v4_state_view.getSlot0(log.id)
    v4.swap(
        pool,
        # NOTE: Max is all
        (False, int((1 - ratio) * balance), int(SLIPPAGE * pool_state.sqrtPriceX96)),
        b"",
        sender=bot.signer,
        nonce=bot.nonce,
    )

    if token.balanceOf(bot.signer) == 0:
        del bot.state.pool_params[log.id]


@bot.on_shutdown()
async def sell_all():
    for pool_id, pool in bot.state.pools.items():
        pool_state = v4_state_view.getSlot0(pool_id)
        token = Token.at(pool.currency1)
        v4.swap(
            pool,
            # NOTE: Max is all
            (False, token.balanceOf(bot.signer), int(SLIPPAGE * pool_state.sqrtPriceX96)),
            b"",
            nonce=bot.nonce,
            sender=bot.signer,
            confirmations_required=0,
        )
