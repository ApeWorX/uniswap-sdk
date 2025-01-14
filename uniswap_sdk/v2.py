from collections import defaultdict
from decimal import Decimal
from functools import cache
from itertools import islice
from typing import Iterator

from ape.contracts import ContractInstance
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS, ManagerAccessMixin, cached_property
from ape_ethereum import multicall
from eth_utils import is_checksum_address, to_int

from .packages import V2, get_contract_instance

try:
    from ape_tokens.managers import ERC20  # type: ignore[import-not-found]
except ImportError:
    ERC20 = None


class Factory(ManagerAccessMixin):
    """
    Singleton class to interact with a deployment of the Uniswap V2 protocol's Factory contract.

    Usage example::

        >>> from uniswap_sdk import v2
        >>> factory = v2.Factory()
        >>> for pair in factory.get_all_pairs():
        ...     print(pair)  # WARNING: Will take 6 mins or more to fetch
        >>> len(list(factory))  # Cached, almost instantaneous
        396757
        >>> from ape_tokens import tokens
        >>> yfi = tokens["YFI"]
        >>> for pair in factory.get_pairs_by_token(yfi):
        ...     print(pair)  # WARNING: Will take 12 mins or more to index
        >>> len(factory["YFI"])  # Already indexed, almost instantaneous
        3
        >>> pair = factory.get_pair(yfi, tokens["USDC"])  # Single contract call
        <uniswap_sdk.v2.Pair address=0xdE37cD310c70e7Fa9d7eD3261515B107D5Fe1F2d>

    """

    def __init__(self) -> None:
        # Memory cache/indexes
        # TODO: Remove once query system is better
        self._cached_pairs: list["Pair"] = []
        self._indexed_pairs: dict[AddressType, list["Pair"]] = defaultdict(list)
        self._last_indexed: int = 0

    # non-cached functions
    @cached_property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V2.UniswapV2Factory, self.provider.chain_id)

    def get_pair(self, tokenA: AddressType, tokenB: AddressType) -> "Pair":
        if (pair_address := self.contract.getPair(tokenA, tokenB)) == ZERO_ADDRESS:
            raise ValueError("No deployed pair")

        return Pair(pair_address)

    def search_for_pairs(self, token: AddressType, *others: AddressType) -> Iterator["Pair"]:
        if len(others) == 0:
            raise ValueError("Must give at least one other token to search for a pair")

        for potential_match in others:
            if (pair_address := self.contract.getPair(token, potential_match)) != ZERO_ADDRESS:
                yield Pair(pair_address)

    def __len__(self) -> int:
        return self.contract.allPairsLength()

    # cached functions
    def get_all_pairs(self) -> Iterator["Pair"]:
        yield from iter(self._cached_pairs)

        # TODO: Reformat to query system when better (using PairCreated)
        if (num_pairs := len(self)) > (last_pair := len(self._cached_pairs)):
            # NOTE: This can be faster than brute force way
            while last_pair < num_pairs:
                call = multicall.Call()
                [
                    call.add(self.contract.allPairs, i)
                    for i in range(
                        last_pair,
                        # TODO: Parametrize multicall increment (per network?)
                        min(last_pair + 4_000, num_pairs),  # NOTE: `range` ignores last value
                    )
                ]

                new_pairs = list(map(Pair, call()))
                yield from iter(new_pairs)
                self._cached_pairs.extend(new_pairs)
                last_pair += len(call.calls)

    def __iter__(self) -> Iterator["Pair"]:
        return self.get_all_pairs()

    def _index_all_pairs(self):
        # TODO: Once query system is used to pull all pairs, we can remove multi-
        #       call since .token0/.token1 addresses will be known in advance
        token0_call = multicall.Call()
        token1_call = multicall.Call()
        matching_pairs = []
        for pair in islice(self, self._last_indexed, None):
            # Add to indexing multicall for later
            # HACK: Just use raw dict instead of `call.add` to avoid `.contract` overhead
            token0_call.calls.append(
                dict(
                    target=pair.address,
                    value=0,
                    allowFailure=True,
                    callData=bytes.fromhex("0dfe1681"),
                )
            )
            token0_call.abis.append(V2.UniswapV2Pair.contract_type.view_methods["token0"])
            token1_call.calls.append(
                dict(
                    target=pair.address,
                    value=0,
                    allowFailure=True,
                    callData=bytes.fromhex("d21220a7"),
                )
            )
            token1_call.abis.append(V2.UniswapV2Pair.contract_type.view_methods["token1"])
            # NOTE: Append pair twice because we want it to match for both token0 and token1
            matching_pairs.append(pair)

            # TODO: Parametrize multicall increment (per network?)
            if len(token0_call.calls) >= 10_000:
                # NOTE: Cache to avoid additional call next time
                for token0_address, token1_address, pair in zip(
                    token0_call(),
                    token1_call(),
                    matching_pairs,
                ):
                    # Update pair cache for token0/token1
                    # TODO: Remove this step once query support is added to fetch
                    pair._token0_address = token0_address
                    pair._token1_address = token1_address
                    # Add pair to cache
                    self._indexed_pairs[token0_address].append(pair)
                    self._indexed_pairs[token1_address].append(pair)

                # NOTE: Reset multicall and matching pairs for next batch
                token0_call = multicall.Call()
                token1_call = multicall.Call()
                matching_pairs = []

        # Execute remaining unindexed batch (batch size smaller than increment)
        # NOTE: If empty, shouldn't do anything
        for token0_address, token1_address, pair in zip(
            token0_call(),
            token1_call(),
            matching_pairs,
        ):
            # Update pair cache for token0/token1
            # TODO: Remove this step once query support is added to fetch
            pair._token0_address = token0_address
            pair._token1_address = token1_address
            # Add pair to cache
            # TODO: Delete everything between here and the islice for loop after
            #       query system support is added for caching all the pairs
            self._indexed_pairs[token0_address].append(pair)
            self._indexed_pairs[token1_address].append(pair)

        self._last_indexed = len(self)  # Skip indexing loop next time from this height

    def get_pairs_by_token(self, token: AddressType) -> Iterator["Pair"]:
        # TODO: Use query manager to search once topic filtering is available
        #       We can move cache/index logic to a query plugin
        # Bring index up to date
        self._index_all_pairs()

        # Yield all from index
        yield from iter(self._indexed_pairs[token.address])

    def __getitem__(self, token: AddressType) -> list["Pair"]:
        return list(self.get_pairs_by_token(token))

    def find_routes(
        self,
        tokenA: AddressType,
        tokenB: AddressType,
        depth: int = 2,
    ) -> Iterator[tuple["Pair", ...]]:
        """
        Find all valid routes (sequence of pairs) that let you swap ``tokenA`` to ``tokenB``
        NOTE: depth >2 takes a long long time, unless the number of pairs is small
        NOTE: Will return deepest routes first, as it performs exhaustive DFS
        """
        if tokenA == tokenB:
            return

        # NOTE: `search_for_pairs` with 2 args should only return 0 or 1 pairs
        if len(pairs := list(self.search_for_pairs(tokenA, tokenB))) == 1:
            yield (pairs[0],)

        # NOTE: `get_pairs_by_token` requires indexing all pairs
        for pair in self.get_pairs_by_token(tokenA):
            # NOTE: This will skip any direct pairs, but that is covered above
            for route in self.find_routes(pair.other(tokenA), tokenB, depth=depth - 1):
                yield (pair, *route)


class Pair(ManagerAccessMixin):
    """
    Represents a UniswapV2Pair contract, which implements swaps between two tokens
    according to the x*y=k constant product market maker function

    Usage example::

        >>> from uniswap_sdk import v2
        >>> pair = v2.Pair(address="0xdE37cD310c70e7Fa9d7eD3261515B107D5Fe1F2d")
        >>> pair["YFI"]  # Get reserves of token in pair (in appropiate decimals)
        Decimal('0.000010265...')
        >>> print(f"Price is {pair.price('YFI'):0,.2f} [YFI/{pair.other('YFI').symbol()}]")
        Price is 2,196.81 [YFI/USDC]

    """

    def __init__(
        self,
        address: AddressType,
        token0_address: AddressType | None = None,
        token1_address: AddressType | None = None,
    ):
        self.address = address
        # Cache if available
        self._token0_address = token0_address
        self._token1_address = token1_address

    def __hash__(self) -> int:
        return to_int(hexstr=self.address)

    def __eq__(self, other) -> bool:
        return isinstance(other, Pair) and self.address == other.address

    def __repr__(self) -> str:
        return f"<{self.__class__.__module__}.{self.__class__.__name__} address={self.address}>"

    @cached_property
    def contract(self) -> ContractInstance:
        # TODO: Make ContractInstance.at cache?
        #       Dunno what causes all the `eth_chainId` requests over and over
        return V2.UniswapV2Pair.at(self.address)

    @cached_property
    def token0(self) -> ContractInstance:
        return self.chain_manager.contracts.instance_at(
            self._token0_address or self.contract.token0(), contract_type=ERC20
        )

    @cached_property
    def token0_symbol(self) -> str:
        return self.token0.symbol()

    @cached_property
    def token0_decimals(self) -> int:
        return self.token0.decimals()

    @cached_property
    def token1(self) -> ContractInstance:
        return self.chain_manager.contracts.instance_at(
            self._token1_address or self.contract.token1(), contract_type=ERC20
        )

    @cached_property
    def token1_symbol(self) -> str:
        return self.token1.symbol()

    @cached_property
    def token1_decimals(self) -> int:
        return self.token1.decimals()

    @cache
    def is_token0(self, token: str) -> bool:
        if is_checksum_address(token):
            return self.token0.address == token
        else:
            return self.token0_symbol == token

    @cache
    def is_token1(self, token: str) -> bool:
        if is_checksum_address(token):
            return self.token1.address == token
        else:
            return self.token1_symbol == token

    def get_reserves(self, block_id: int | str = "latest") -> tuple[int, int, int]:
        if isinstance(block_id, int) and block_id < 0:
            block_id += self.chain_manager.blocks.head.number
        return self.contract.getReserves(block_id=block_id)

    def __getitem__(self, token: ContractInstance | str) -> Decimal:
        if isinstance(token, ContractInstance):
            token = token.address

        if self.is_token0(token):
            return Decimal(self.get_reserves()[0]) / Decimal(10**self.token0_decimals)
        elif self.is_token1(token):
            return Decimal(self.get_reserves()[1]) / Decimal(10**self.token1_decimals)
        else:
            raise ValueError(f"Token {token} not in pair")

    def other(self, token: ContractInstance | str) -> ContractInstance:
        """
        Get the other token in the pair that isn't ``token``.
        """
        if isinstance(token, ContractInstance):
            token = token.address

        if self.is_token0(token):
            return self.token1

        elif self.is_token1(token):
            return self.token0

        raise ValueError(f"Token {token} is not one of the tokens in the pair")

    def price(self, token: ContractInstance | str, block_id: int | str = "latest") -> Decimal:
        """
        Price of ``token`` relative to the other token in the pair.
        """
        token0_reserve, token1_reserve, _ = self.get_reserves(block_id=block_id)
        token0_reserve = Decimal(token0_reserve) / Decimal(10**self.token0_decimals)
        token1_reserve = Decimal(token1_reserve) / Decimal(10**self.token1_decimals)

        if self.is_token0(token.address if isinstance(token, ContractInstance) else token):
            return token1_reserve / token0_reserve
        else:
            return token0_reserve / token1_reserve
