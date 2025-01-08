from collections import defaultdict
from itertools import chain, islice, tee
from typing import Iterator

from ape.contracts import ContractInstance
from ape.types import AddressType
from ape.utils import ZERO_ADDRESS, ManagerAccessMixin, cached_property
from ape_ethereum import multicall

from .packages import V2, get_contract_instance


class Factory(ManagerAccessMixin):
    """
    Singleton class to interact with a deployment of the Uniswap V2 protocol's Factory contract.

    Usage example::

        >>> from uniswap_sdk import v2
        >>> factory = v2.Factory()
        >>> for pair in factory.get_all_pairs():
        ...     print(pair)  # WARNING: Will take 3 mins or more to fetch
        >>> len(list(factory))  # Cached, almost instantaneous
        396757
        >>> from ape_tokens import tokens
        >>> yfi = tokens["YFI"]
        >>> for pair in factory.get_pairs_by_token(yfi):
        ...     print(pair)  # WARNING: Will take 1-2 mins or more to index
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
        self._num_indexed: dict[AddressType, int] = defaultdict(lambda: 0)

    @cached_property
    def contract(self) -> ContractInstance:
        return get_contract_instance(V2.UniswapV2Factory, self.provider.chain_id)

    def get_pair(self, tokenA: AddressType, tokenB: AddressType) -> "Pair":
        if (pair_address := self.contract.getPair(tokenA, tokenB)) == ZERO_ADDRESS:
            raise ValueError("No deployed pair")

        return Pair(pair_address)

    def _filter_matching_pairs(
        self,
        token_to_match: AddressType,
        call: multicall.Call,
        unchecked_pairs: list["Pair"],
    ) -> Iterator["Pair"]:
        # NOTE: This will create an iterator which has each pair represented twice in sequence
        each_pair_twice = chain.from_iterable(zip(*tee(iter(unchecked_pairs), 2)))
        # NOTE: We have 2 calls per pair, we are checking the following
        #       `token0 == token_to_match` OR `token1 == token_to_match`
        for token_address, pair in zip(call(), each_pair_twice):
            if token_address == token_to_match.address:
                yield pair

    def get_pairs_by_token(self, token: AddressType) -> Iterator["Pair"]:
        # Yield all from index first
        yield from iter(self._indexed_pairs[token.address])

        # TODO: Use query manager to search once topic filtering is available
        call = multicall.Call()
        pairs_to_check: list["Pair"] = []
        # NOTE: Skips for loop entirely if index is up to date
        for pair in islice(self.get_all_pairs(), self._num_indexed[token.address], None):
            # Add to indexing multicall for later
            # TODO: I think this doesn't cache when calling `pair.contract`
            call.add(pair.contract.token0)
            call.add(pair.contract.token1)
            pairs_to_check.append(pair)

            if len(call.calls) >= 1_000:  # TODO: Parametrize multicall increment (per network?)
                matching_pairs = list(self._filter_matching_pairs(token, call, pairs_to_check))
                # NOTE: Cache to avoid additional call next time
                self._indexed_pairs[token.address].extend(matching_pairs)
                yield from matching_pairs

                # NOTE: Reset multicall
                call = multicall.Call()
                pairs_to_check = []

        # Execute remaining unchecked pair filter
        # NOTE: If empty, shouldn't yield anything
        matching_pairs = list(self._filter_matching_pairs(token, call, pairs_to_check))
        # NOTE: Cache to avoid additional call next time
        self._indexed_pairs[token.address].extend(matching_pairs)
        yield from matching_pairs

        # NOTE: Set the height of the index to avoid indexing again
        self._num_indexed[token.address] = self.contract.allPairsLength()

    def __getitem__(self, token: AddressType) -> list["Pair"]:
        return list(self.get_pairs_by_token(token))

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

    def __len__(self) -> int:
        return self.contract.allPairsLength()


class Pair(ManagerAccessMixin):
    def __init__(self, address: AddressType):
        self.address = address

    def __repr__(self) -> str:
        return f"<{self.__class__.__module__}.{self.__class__.__name__} address={self.address}>"

    @cached_property
    def contract(self) -> ContractInstance:
        # TODO: Make ContractInstance.at cache?
        #       Dunno what causes all the `eth_chainId` requests over and over
        return V2.UniswapV2Pair.at(self.address)
