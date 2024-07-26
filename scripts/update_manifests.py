from pathlib import Path

from ape import project
from ethpm_types import ContractType

PACKAGE_FOLDER = Path(__file__).parent.parent / "uniswap_sdk"


def clean_contract_type(contract_type):
    clean_model_dict = contract_type.model_dump(
        exclude={
            "ast",
            "deployment_bytecode",
            "runtime_bytecode",
            "source_id",
            "pcmap",
            "dev_messages",
            "sourcemap",
            "userdoc",
            "devdoc",
            "method_identifiers",
        }
    )
    return ContractType.model_validate(clean_model_dict)


def clean_manifest(manifest, *contract_types_to_keep):
    manifest.contract_types = {
        k: clean_contract_type(v)
        for k, v in manifest.contract_types.items()
        if k in contract_types_to_keep
    }
    return manifest.model_dump_json(
        exclude={
            "meta",
            "dependencies",
            "sources",
            "compilers",
        }
    )


def main():
    manifest_json = clean_manifest(
        project.dependencies["uniswap-v2"]["v1.0.1"].extract_manifest(),
        "UniswapV2Factory",
        "UniswapV2Pair",
    )
    (PACKAGE_FOLDER / "v2-manifest.json").write_text(manifest_json)

    manifest_json = clean_manifest(
        project.dependencies["uniswap-v3"]["v1.0.0"].extract_manifest(),
        "UniswapV3Factory",
        "UniswapV3Pool",
    )
    (PACKAGE_FOLDER / "v3-manifest.json").write_text(manifest_json)

    manifest_json = clean_manifest(
        project.dependencies["permit2"]["main"].extract_manifest(),
        "Permit2",
    )
    (PACKAGE_FOLDER / "permit2-manifest.json").write_text(manifest_json)

    manifest_json = clean_manifest(
        project.dependencies["universal-router"]["v1.6.0"].extract_manifest(),
        "UniversalRouter",
    )
    (PACKAGE_FOLDER / "unirouter-manifest.json").write_text(manifest_json)
