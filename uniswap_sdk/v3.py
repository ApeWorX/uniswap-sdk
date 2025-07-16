import itertools
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Iterator, cast

import networkx as nx  # type: ignore[import-untyped]
from ape.contracts import ContractInstance
from ape.logging import logger
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS, cached_property
from ape_ethereum import multicall
from ape_tokens import TokenInstance
from eth_utils import to_int
from eth_utils.address import to_checksum_address
from pydantic import BaseModel, Field

from .packages import V3, get_contract_instance
from .types import BaseIndex, BaseLiquidity, BasePair, ConvertsToToken, Fee, Route
from .utils import get_token_address, sort_tokens

if TYPE_CHECKING:
    from silverback import SilverbackBot

MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

MIN_TICK = -887272  # price of 2.939e-39
MAX_TICK = 887272  # price of 3.403e38


class Factory(BaseIndex):
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
        tokenA: ConvertsToToken,
        tokenB: ConvertsToToken,
        fee: Fee = Fee.MEDIUM,
    ) -> "Pool | None":
        # NOTE: First make sure this is a supported fee type
        fee = Fee(fee)

        token0: AddressType
        token1: AddressType
        token0, token1 = sort_tokens(
            (
                self.conversion_manager.convert(tokenA, AddressType),
                self.conversion_manager.convert(tokenB, AddressType),
            )
        )

        try:
            return self._indexed_pools[token0][token1][fee.value]["pool"]
        except KeyError:
            pass  # NOTE: Not indexed, go find it

        if (pool_address := self.contract.getPool(token0, token1, fee)) == ZERO_ADDRESS:
            return None  # Doesn't exist

        pool = Pool(address=pool_address, token0=token0, token1=token1, fee=fee)
        self._indexed_pools.add_edge(token0, token1, key=fee.value, pool=pool)
        self._pool_by_address[pool.address] = pool
        return pool

    def get_pools(
        self,
        *tokens: ConvertsToToken,
        fee: Fee | None = None,  # All fee types
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ) -> Iterator["Pool"]:
        # TODO: Why does `ape_tokens` converter not return a checksummed address sometimes?
        converted_tokens = map(to_checksum_address, map(get_token_address, tokens))
        sorted_token_pairs = list(map(sort_tokens, itertools.combinations(converted_tokens, 2)))

        fees_to_index: tuple[Fee, ...]
        if fee is not None:
            fees_to_index = (Fee(fee),)
        else:
            fees_to_index = tuple(v for v in Fee if v is not Fee.MAXIMUM)

        pool_args: list[tuple[AddressType, AddressType, Fee]] = []
        calls = [multicall.Call()]
        for pool_fee in fees_to_index:
            for token0, token1 in sorted_token_pairs:
                # NOTE: we need to order them since `token0` and `token1` is based on ordering
                try:
                    yield self._indexed_pools[token0][token1][pool_fee.value]["pool"]

                except KeyError:
                    # Add to batch
                    pool_args.append((token0, token1, pool_fee))
                    calls[-1].add(self.contract.getPool, token0, token1, pool_fee.value)

                    if len(calls[-1].calls) >= 5_000:
                        calls.append(multicall.Call())

        pool_addresses = itertools.chain(call() for call in calls)
        for pool_address, (token0, token1, fee) in zip(*pool_addresses, pool_args):
            if pool_address != ZERO_ADDRESS:
                pool = Pool(pool_address, token0=token0, token1=token1, fee=fee)
                self._indexed_pools.add_edge(
                    token0,
                    token1,
                    key=fee.value,
                    pool=pool,
                )
                self._pool_by_address[pool.address] = pool
                yield pool

    def index(
        self,
        tokens: Iterable[ConvertsToToken] | None = None,
        min_liquidity: Decimal = Decimal(1),  # 1 token
    ):
        logger.info("Uniswap v3 - indexing")
        num_pools = 0
        if tokens is not None:
            for pool in self.get_pools(*tokens, min_liquidity=min_liquidity):
                yield pool
                num_pools += 1

            logger.success(f"Uniswap v3 - indexed {num_pools} pools")
            return  # NOTE: Shortcut for indexing less

        # NOTE: Uniswap v3 doesn't have a shortcut to iter all pools like v2
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

        logger.success(f"Uniswap v3 - indexed {num_pools} pools")
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

    def get_pools_by_token(self, token: ConvertsToToken) -> Iterator["Pool"]:
        # Yield all from index
        for edge_data in self._indexed_pools[
            self.conversion_manager.convert(token, AddressType)
        ].values():
            yield cast(Pool, edge_data["pool"])

    def __getitem__(self, token: ConvertsToToken) -> list["Pool"]:
        return list(self.get_pools_by_token(token))

    def find_routes(
        self,
        start_token: ConvertsToToken,
        end_token: ConvertsToToken,
        depth: int = 2,
    ) -> Iterator[Route["Pool"]]:
        start = self.conversion_manager.convert(start_token, AddressType)
        end = self.conversion_manager.convert(end_token, AddressType)

        try:
            for edge_paths in nx.all_simple_edge_paths(
                self._indexed_pools, start, end, cutoff=depth
            ):
                yield tuple(self._indexed_pools[u][v][fee]["pool"] for u, v, fee in edge_paths)

        except nx.NodeNotFound as e:
            raise KeyError(f"Cannot solve: {start_token} or {end_token} is not indexed.") from e

    @classmethod
    def encode_route(cls, token: TokenInstance, *route: "Pool") -> tuple[AddressType | Fee, ...]:
        encoded_path = [token.address]

        for pool in route:
            encoded_path.append(pool.fee)
            token = pool.other(token)
            encoded_path.append(token.address)

        return tuple(encoded_path)


class Pool(BasePair):
    def __init__(
        self,
        address: AddressType,
        token0: ConvertsToToken | None = None,
        token1: ConvertsToToken | None = None,
        fee: Fee | int = Fee.MEDIUM,
        tick_spacing: int | None = None,
    ):
        self.address = address
        super().__init__(
            token0=token0 or self.contract.token0(),
            token1=token1 or self.contract.token1(),
        )

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
        token0_price = Decimal(slot0_at_block.sqrtPriceX96) ** 2 / 2**192

        conversion = 10 ** Decimal(self.token1.decimals() - self.token0.decimals())
        if self.is_token0(token):
            return token0_price / conversion

        else:
            return conversion / token0_price

    def depth(self, token: ContractInstance | str, slippage: Decimal) -> Decimal:
        # TODO: This formula is *NOT RIGHT* as it doesn't account for concentrated liquidity
        if not isinstance(slippage, Decimal):
            slippage = Decimal(slippage)

        if not (0 < slippage < 1):
            raise ValueError(f"Slippage out of bounds: {slippage}. Must be a ratio in (0, 1).")

        # NOTE: Slippage is defined as being a nonzero ratio, however formula expects negative
        return (self.liquidity[token] / (1 - self.fee.to_decimal())) * (
            (1 / (1 - slippage).sqrt()) - 1
        )

    def reflexivity(self, token: ContractInstance | str, size: Decimal) -> Decimal:
        # TODO: This formula is *NOT RIGHT* as it doesn't account for concentrated liquidity
        if not isinstance(size, Decimal):
            size = Decimal(size) / 10 ** Decimal(
                self.token0.decimals() if self.is_token0(token) else self.token1.decimals()
            )

        liquidity = self.liquidity[token]

        if not (0 < size < liquidity):
            raise ValueError(f"Size out of bounds: {size}. Must be nonzero and below {liquidity}.")

        return 1 - (liquidity / (liquidity + (1 - self.fee.to_decimal()) * size)) ** 2


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
        # TODO: This formula is not correct for v3 concentrated liquidity
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
        # TODO: This formula is not correct for v3 concentrated liquidity
        if self.pool.is_token0(token):
            return Decimal(self.reserve0) / 10 ** Decimal(self.pool.token1.decimals())

        elif self.pool.is_token1(token):
            return Decimal(self.reserve1) / 10 ** Decimal(self.pool.token1.decimals())

        raise ValueError(f"Token {token} is not one of the tokens in pool")
