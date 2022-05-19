import pkgutil
from typing import Iterator

from ape import Contract
from ape.contracts import ContractInstance
from ape.types import AddressType
from ape.utils import ManagerAccessMixin
from ethpm_types import PackageManifest

# TODO: Figure out better way to load this using `Project`
_manifest = pkgutil.get_data(__package__, "v2.json")
CONTRACT_TYPES = PackageManifest.parse_raw(_manifest).contract_types  # type: ignore

ADDRESSES = {
    "ethereum": {
        "mainnet": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "rospten": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "rinkeby": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "kovan": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "goerli": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    },
    "bsc": {
        "mainnet": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    },
    "polygon": {
        "mainnet": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "mumbai": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    },
    "fantom": {
        "opera": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "testnet": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    },
}


class Factory(ManagerAccessMixin):
    @property
    def address(self) -> AddressType:
        ecosystem_name = self.provider.network.ecosystem.name
        network_name = self.provider.network.name.replace("-fork", "")

        if ecosystem_name not in ADDRESSES or network_name not in ADDRESSES[ecosystem_name]:
            raise ValueError(f"No Uniswap deployment on '{ecosystem_name}:{network_name}'")

        return AddressType(ADDRESSES[ecosystem_name][network_name])  # type: ignore

    @property
    def contract(self) -> ContractInstance:
        return Contract(
            self.address,
            contract_type=CONTRACT_TYPES["UniswapV2Factory"],  # type: ignore
        )

    def get_pools(self, token: AddressType) -> Iterator["Pool"]:
        # TODO: Use query manager to search once topic filtering is available
        for log in self.contract.PairCreated:
            if token in (log.token0, log.token1):  # `token` is either one in the pool's pair
                yield Pool(log.pair)

    def get_all_pools(self) -> Iterator["Pool"]:
        for address in self.contract.PairCreated.query("pair").pair:
            yield Pool(address)


class Pool(ManagerAccessMixin):
    def __init__(self, address: AddressType):
        self.address = address

    @property
    def contract(self) -> ContractInstance:
        return Contract(
            self.address,
            contract_type=CONTRACT_TYPES["UniswapV2Pair"],  # type: ignore
        )
