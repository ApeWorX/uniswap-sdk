from decimal import Decimal
from enum import Enum
from itertools import combinations
from typing import TYPE_CHECKING, Iterable, Iterator, cast

import networkx as nx  # type: ignore[import-untyped]
from ape.contracts import ContractInstance
from ape.logging import logger
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS, ManagerAccessMixin, cached_property
from ape_ethereum import multicall
from ape_tokens import Token, TokenInstance
from eth_utils import to_int
from pydantic import BaseModel, Field

from .packages import V3, get_contract_instance
from .types import BaseIndex, BaseLiquidity, BasePair, Route
from .utils import get_token_address, sort_tokens

if TYPE_CHECKING:
    from silverback import SilverbackBot

MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

MIN_TICK = -887272  # price of 2.939e-39
MAX_TICK = 887272  # price of 3.403e38


class Fee(int, Enum):
    # From Uniswap V3 SDK
    LOWEST = 100  # 1 bip
    LOW_200 = 200
    LOW_300 = 300
    LOW_400 = 400
    LOW = 500  # 0.05%
    MEDIUM = 3000  # 0.3%
    HIGH = 10000  # 1.0%

    @property
    def tick_spacing(self) -> int:
        return {
            Fee.LOWEST: 1,
            Fee.LOW_200: 4,
            Fee.LOW_300: 6,
            Fee.LOW_400: 8,
            Fee.LOW: 10,
            Fee.MEDIUM: 60,
            Fee.HIGH: 200,
        }[self]

    def to_decimal(self) -> Decimal:
        # Convert to ratio in decimal (for fee math)
        return self.value / Decimal(10**6)


class Factory(ManagerAccessMixin, BaseIndex):
    def __init__(self) -> None:
        self._pool_by_address: dict[AddressType, "Pool"] = {}
        # NOTE: V3 allows multiple pools by fee
        self._indexed_pools = nx.MultiGraph()
        self._last_cached_block = 0

    @cached_property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V3.UniswapV3Factory, self.provider.chain_id)

    def __repr__(self) -> str:
        return f"<uniswap_sdk.v3.Factory address={self.contract.address}>"

    def get_pool(
        self,
        tokenA: TokenInstance | AddressType,
        tokenB: TokenInstance | AddressType,
        fee: Fee = Fee.MEDIUM,
    ) -> "Pool | None":
        # NOTE: First make sure this is a supported fee type
        fee = Fee(fee)
        token0, token1 = sort_tokens((tokenA, tokenB))
        u, v = get_token_address(tokenA), get_token_address(tokenB)

        try:
            return self._indexed_pools[u][v][fee.value]["pool"]
        except KeyError:
            pass  # NOTE: Not indexed, go find it

        if (pool_address := self.contract.getPool(token0, token1, fee)) == ZERO_ADDRESS:
            return None

        pool = Pool(address=pool_address, token0=token0, token1=token1, fee=fee)
        self._indexed_pools.add_edge(u, v, key=fee.value, pool=pool)
        self._pool_by_address[pool.address] = pool
        return pool

    def get_pools(
        self,
        *tokens: TokenInstance | AddressType,
        fee: Fee | None = None,  # All fee types
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ) -> Iterator["Pool"]:
        pool_args: list[dict] = []
        call = multicall.Call()

        for fee in (Fee(fee),) if fee else iter(Fee):
            for tokenA, tokenB in combinations(map(get_token_address, tokens), 2):
                # Add to batch
                # NOTE: we need to order them since `token0` and `token1` is based on ordering
                try:
                    yield self._indexed_pools[tokenA][tokenB][fee.value]["pool"]
                    continue  # Skip multicall fetch

                except KeyError:
                    pass

                token0, token1 = sort_tokens((tokenA, tokenB))
                pool_args.append(dict(token0=token0, token1=token1, fee=fee))
                call.add(self.contract.getPool, tokenA, tokenB, fee)

                # If batch is full, execute it
                if len(call.calls) > 5_000:
                    for pool_address, kwargs in zip(call(), pool_args):
                        if pool_address != ZERO_ADDRESS:
                            pool = Pool(pool_address, **kwargs)
                            self._indexed_pools.add_edge(
                                kwargs["token0"],
                                kwargs["token1"],
                                key=kwargs["fee"].value,
                                pool=pool,
                            )
                            self._pool_by_address[pool.address] = pool
                            yield pool

                    # Reset batching variables
                    pool_args = []
                    call = multicall.Call()

        # Do the remaining batch here
        for pool_address, kwargs in zip(call(), pool_args):
            if pool_address != ZERO_ADDRESS:
                pool = Pool(pool_address, **kwargs)
                if pool.liquidity[kwargs["token0"]] < min_liquidity:
                    continue

                self._indexed_pools.add_edge(
                    kwargs["token0"],
                    kwargs["token1"],
                    key=kwargs["fee"].value,
                    pool=pool,
                )
                self._pool_by_address[pool.address] = pool
                yield pool

    def index(
        self,
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ):
        logger.info("Uniswap v3 - indexing")
        num_pools = 0
        if tokens:
            for pool in self.get_pools(*tokens, min_liquidity=min_liquidity):
                yield pool
                num_pools += 1
            logger.success(f"Uniswap v3 - indexed {num_pools} pairs")
            return  # NOTE: Shortcut for indexing less

        # NOTE: Uniswap v3 doesn't have a shortcut to iter all pools
        for log in self.contract.PoolCreated.range(
            self._last_cached_block,
            end_block := self.chain_manager.blocks.head.number,
        ):
            pool = Pool(
                address=log.pool,
                token0=log.token0,
                token1=log.token1,
                fee=log.fee,
                tick_spacing=log.tickSpacing,
            )
            if pool.liquidity[log.token0] > min_liquidity:
                self._indexed_pools.add_edge(log.token0, log.token1, key=log.fee, pool=pool)
                self._pool_by_address[pool.address] = pool

                yield pool
                num_pools += 1

        logger.success(f"Uniswap v3 - indexed {num_pools} pairs")
        self._last_cached_block = end_block

    def install(
        self,
        bot: "SilverbackBot",
        tokens: Iterable[TokenInstance | AddressType] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ):
        from silverback.types import TaskType

        async def index_existing_pools(snapshot):
            for pool in self.index(tokens=tokens):
                pool.liquidity = _ManagedLiquidity(pool)

        # NOTE: Modify name to namespace it from user tasks
        index_existing_pools.__name__ = f"uniswap:v3:{index_existing_pools.__name__}"
        bot.broker_task_decorator(TaskType.STARTUP)(index_existing_pools)

        async def index_new_pool(log):
            if log.token0 in self._indexed_pools or log.token1 in self._indexed_pools:
                pool = Pool(
                    address=log.pool,
                    token0=log.token0,
                    token1=log.token1,
                    fee=log.fee,
                    tick_spacing=log.tickSpacing,
                )
                pool.liquidity = _ManagedLiquidity(pool)
                self._indexed_pools.add_edge(log.token0, log.token1, key=log.fee, pool=pool)
                self._pool_by_address[pool.address] = pool

        # NOTE: Modify name to namespace it from user tasks
        index_new_pool.__name__ = f"uniswap:v3:{index_new_pool.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=self.contract.PoolCreated)(
            index_new_pool
        )

        async def sync_pool_liquidity(log):
            if pool := self._pool_by_address.get(log.contract_address):
                assert isinstance(pool.liquidity, _ManagedLiquidity)
                pool.liquidity.current_tick = log.tick
                # NOTE: `amount*: int256` is negative if flow out
                pool.liquidity.reserve0 += log.amount0
                pool.liquidity.reserve1 += log.amount1

        # NOTE: Modify name to namespace it from user tasks
        sync_pool_liquidity.__name__ = f"uniswap:v3:{sync_pool_liquidity.__name__}"
        bot.broker_task_decorator(TaskType.EVENT_LOG, container=V3.UniswapV3Pool.Swap)(
            sync_pool_liquidity
        )

    def get_pools_by_token(self, token: TokenInstance | AddressType) -> Iterator["Pool"]:
        # Yield all from index
        for data in self._indexed_pools[
            token.address if isinstance(token, Token) else token
        ].values():
            yield cast(Pool, data["pool"])

    def __getitem__(self, token: TokenInstance | AddressType) -> list[BasePair]:
        return list(self.get_pools_by_token(token))

    def find_routes(
        self,
        start_token: TokenInstance | AddressType,
        end_token: TokenInstance | AddressType,
        depth: int = 2,
    ) -> Iterator[Route["Pool"]]:
        start, end = get_token_address(start_token), get_token_address(end_token)

        try:
            for edge_paths in nx.all_simple_edge_paths(
                self._indexed_pools, start, end, cutoff=depth
            ):
                yield tuple(self._indexed_pools[u][v][fee]["pool"] for u, v, fee in edge_paths)

        except nx.NodeNotFound as e:
            raise KeyError(f"Cannot solve: {start_token} or {end_token} is not indexed.") from e


class Pool(ManagerAccessMixin, BasePair):
    def __init__(
        self,
        address: AddressType,
        token0: TokenInstance | AddressType | None = None,
        token1: TokenInstance | AddressType | None = None,
        fee: Fee | int = Fee.MEDIUM,
        tick_spacing: int | None = None,
    ):
        self.address = address
        super().__init__(token0=token0, token1=token1)

        self.fee = Fee(fee)
        self.tick_spacing = tick_spacing or self.fee.tick_spacing

    def __hash__(self) -> int:
        return to_int(hexstr=self.address)

    def __eq__(self, other) -> bool:
        return isinstance(other, Pool) and self.address == other.address

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__module__}.{self.__class__.__name__} "
            f"address={self.address} "
            f"pair='{self.token0.symbol()}/{self.token1.symbol()}' fee={self.fee}>"
        )

    @cached_property
    def contract(self) -> ContractInstance:
        # TODO: Make ContractInstance.at cache?
        #       Dunno what causes all the `eth_chainId` requests over and over
        return V3.UniswapV3Pool.at(self.address)

    @cached_property
    def token0(self) -> TokenInstance:
        return Token.at(self._token0_address or self.contract.token0())

    @cached_property
    def token1(self) -> TokenInstance:
        return Token.at(self._token1_address or self.contract.token1())

    def prev_tick(self, tick: int) -> int:
        compressed = (tick // self.tick_spacing) + 1
        word = self.contract.tickBitmap(compressed >> 8)

        bit_pos = compressed % 256
        if masked := word & ~((1 << bit_pos) - 1):
            # Initialized bit is set
            masked_lsb = (masked & -masked).bit_length - 1
            return compressed + (masked_lsb - bit_pos) * self.tick_spacing
        else:
            return compressed + (255 - bit_pos) * self.tick_spacing

    def next_tick(self, tick: int) -> int:
        compressed = tick // self.tick_spacing
        word = self.contract.tickBitmap(compressed >> 8)

        bit_pos = compressed % 256
        if masked := (word & ((1 << bit_pos) - 1 + (1 << bit_pos))):
            # Initialized bit is set
            masked_msb = masked.bit_length() - 1
            return compressed - (bit_pos - masked_msb) * self.tick_spacing
        else:
            return compressed - bit_pos * self.tick_spacing

    @cached_property
    def liquidity(self) -> "Liquidity":
        return Liquidity(self)

    @property
    def current_slot0(self):
        return self.contract.slot0()

    def price(self, token: ContractInstance | str, block_id: int | str = "latest") -> Decimal:
        """
        Price of ``token`` relative to the other token in the pair.
        """
        slot0_at_block = self.contract.slot0(block_id=block_id)
        token0_price = (Decimal(slot0_at_block.sqrtPriceX96)) ** 2 / 2**192

        conversion = 10 ** Decimal(self.token1.decimals() - self.token0.decimals())
        if self.is_token0(token):
            return token0_price / conversion

        else:
            return conversion / token0_price


class TickReserves(BaseModel):
    gross_liquidity: int = Field(alias="liquidityGross")
    net_liquidity: int = Field(alias="liquidityNet")
    token0_fee_growth: int = Field(alias="feeGrowthOutside0X128")
    token1_fee_growth: int = Field(alias="feeGrowthOutside1X128")
    seconds_per_liquidity_outside: int = Field(alias="secondsPerLiquidityOutsideX128")
    seconds_outside: int = Field(alias="secondsOutside")
    initialized: bool


class Liquidity(BaseLiquidity):
    def __init__(self, pool: "Pool"):
        self.pool = pool

    @property
    def current_tick(self) -> int:
        return self.pool.current_slot0.tick

    def get_reserve(self, tick: int) -> TickReserves:
        assert (tick % self.pool.tick_spacing) == 0
        return TickReserves(**self.pool.contract.ticks(tick).__dict__)

    def __getitem__(self, token: ContractInstance | str) -> Decimal:
        if self.pool.is_token0(token):
            return Decimal(self.pool.token0.balanceOf(self.pool.address)) / 10 ** Decimal(
                self.pool.token0.decimals()
            )

        elif self.pool.is_token1(token):
            return Decimal(self.pool.token1.balanceOf(self.pool.address)) / 10 ** Decimal(
                self.pool.token1.decimals()
            )

        raise ValueError(f"Token {token} is not one of the tokens in pool")


class _ManagedLiquidity(Liquidity):
    def __init__(self, pool: "Pool", reserve0: int | None = None, reserve1: int | None = None):
        super().__init__(pool)
        # NOTE: We must manage these cache values in our bot
        self.reserve0 = reserve0 or self.pool.token0.balanceOf(self.pool.address)
        self.reserve1 = reserve1 or self.pool.token1.balanceOf(self.pool.address)

    @cached_property
    def current_tick(self) -> int:
        return self.pool.current_slot0.tick

    def __getitem__(self, token: ContractInstance | str) -> Decimal:
        if self.pool.is_token0(token):
            return Decimal(self.reserve0) / 10 ** Decimal(self.pool.token1.decimals())

        elif self.pool.is_token1(token):
            return Decimal(self.reserve1) / 10 ** Decimal(self.pool.token1.decimals())

        raise ValueError(f"Token {token} is not one of the tokens in pool")
