from importlib import resources
from typing import cast

from ape.contracts import ContractContainer, ContractInstance
from ape.managers.project import ProjectManager
from ape.types import AddressType
from evmchains import get_chain_meta

root = resources.files(__package__)

with resources.as_file(root.joinpath("v2-manifest.json")) as manifest_json_file:
    V2 = ProjectManager.from_manifest(manifest_json_file)

with resources.as_file(root.joinpath("v3-manifest.json")) as manifest_json_file:
    V3 = ProjectManager.from_manifest(manifest_json_file)

with resources.as_file(root.joinpath("unirouter-manifest.json")) as manifest_json_file:
    UNI_ROUTER = ProjectManager.from_manifest(manifest_json_file)

with resources.as_file(root.joinpath("permit2-manifest.json")) as manifest_json_file:
    PERMIT2 = ProjectManager.from_manifest(manifest_json_file)


def chain_id(ecosystem: str, network: str) -> int:
    return get_chain_meta(ecosystem, network).chainId


def addr(raw_addr: str) -> AddressType:
    return cast(AddressType, raw_addr)


def get_contract_instance(ct: ContractContainer, chain_id: int) -> ContractInstance:
    assert ct.contract_type.name  # for mypy
    if not (addresses := ADDRESSES_BY_CHAIN_ID.get(ct.contract_type.name)):
        raise ValueError(f"Contract Type `{ct.__class__.__class__}` is not supported.")

    if not (address := addresses.get(chain_id, addresses.get(0))):
        raise ValueError(f"No known address for `{ct.__class__.__name__}` on chain ID: {chain_id}")

    if not (contract := ct.at(address)).is_contract:
        raise ValueError(f"{contract.address} is not a contract on chain ID: {chain_id}")

    return contract


# NOTE: chain_id `0` is wildcard match
ADDRESSES_BY_CHAIN_ID: dict[str, dict[int, AddressType]] = {
    V2.UniswapV2Factory.contract_type.name: {
        chain_id("ethereum", "mainnet"): addr("0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"),
        chain_id("ethereum", "sepolia"): addr("0xB7f907f7A9eBC822a80BD25E224be42Ce0A698A0"),
        chain_id("arbitrum", "mainnet"): addr("0xf1D7CC64Fb4452F05c498126312eBE29f30Fbcf9"),
        chain_id("optimism", "mainnet"): addr("0x0c3c1c532F1e39EdF36BE9Fe0bE1410313E074Bf"),
        chain_id("avalanche", "mainnet"): addr("0x9e5A52f57b3038F1B8EeE45F28b3C1967e22799C"),
        chain_id("polygon", "mainnet"): addr("0x9e5A52f57b3038F1B8EeE45F28b3C1967e22799C"),
        chain_id("bsc", "mainnet"): addr("0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6"),
        chain_id("base", "mainnet"): addr("0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6"),
        chain_id("blast", "mainnet"): addr("0x5C346464d33F90bABaf70dB6388507CC889C1070"),
    },
    # NOTE: UniswapV2Pair addresses should be queried from factory
    V3.UniswapV3Factory.contract_type.name: {
        0: addr("0x1F98431c8aD98523631AE4a59f267346ea31F984"),
        chain_id("ethereum", "sepolia"): addr("0x0227628f3F023bb0B980b67D528571c95c6DaC1c"),
        chain_id("arbitrum", "sepolia"): addr("0x248AB79Bbb9bC29bB72f7Cd42F17e054Fc40188e"),
        chain_id("optimism", "sepolia"): addr("0x8CE191193D15ea94e11d327b4c7ad8bbE520f6aF"),
        chain_id("avalanche", "mainnet"): addr("0x740b1c1de25031C31FF4fC9A62f554A55cdC1baD"),
        chain_id("bsc", "mainnet"): addr("0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7"),
        chain_id("base", "mainnet"): addr("0x33128a8fC17869897dcE68Ed026d694621f6FDfD"),
        chain_id("base", "sepolia"): addr("0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24"),
        chain_id("blast", "mainnet"): addr("0x792edAdE80af5fC680d96a2eD80A44247D2Cf6Fd"),
    },
    # NOTE: UniswapV3Pool addresses should be queried from factory
    # https://github.com/Uniswap/universal-router/tree/main/deploy-addresses
    UNI_ROUTER.UniversalRouter.contract_type.name: {
        0: addr("0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"),
        chain_id("ethereum", "mainnet"): addr("0x66a9893cc07d91d95644aedd05d03f95e1dba8af"),
        chain_id("ethereum", "sepolia"): addr("0x3a9d48ab9751398bbfa63ad67599bb04e4bdf98b"),
        chain_id("arbitrum", "mainnet"): addr("0xa51afafe0263b40edaef0df8781ea9aa03e381a3"),
        chain_id("optimism", "mainnet"): addr("0x851116d9223fabed8e56c0e6b8ad0c31d98b3507"),
        chain_id("optimism", "sepolia"): addr("0x3a9d48ab9751398bbfa63ad67599bb04e4bdf98b"),
        chain_id("polygon", "mainnet"): addr("0x1095692a6237d83c6a72f3f5efedb9a670c49223"),
        chain_id("bsc", "mainnet"): addr("0x1906c1d672b88cd1b9ac7593301ca990f94eae07"),
        chain_id("base", "mainnet"): addr("0x6ff5693b99212da76ad316178a184ab56d299b43"),
        chain_id("base", "sepolia"): addr("0x95273d871c8156636e114b63797d78D7E1720d81"),
        chain_id("blast", "mainnet"): addr("0xeabbcb3e8e415306207ef514f660a3f820025be3"),
        # chain_id("unichain", "mainnet"): addr("0xef740bf23acae26f6492b10de645d6b98dc8eaf3"),
        # chain_id("unichain", "sepolia"): addr("0x986dadb82491834f6d17bd3287eb84be0b4d4cc7"),
    },
    PERMIT2.Permit2.contract_type.name: {
        0: addr("0x000000000022D473030F116dDEE9F6B43aC78BA3"),
    },
}
