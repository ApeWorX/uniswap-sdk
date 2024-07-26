import itertools

import pytest
from hexbytes import HexBytes

from uniswap_sdk import universal_router as ur

# Some convienent constants
DEV = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
YFI = "0x0bc529c00C6401aEF6D220BE8C6Ea1667F6Ad93e"
FEE = 10000
AMOUNT = VALUE = 10**18
AMOUNT_MIN = 1234
BIPS = 100
TOKEN_ID = 1234
DEADLINE = 2**42
NONCE = 1
PAYER_IS_USER = False
DATA = b"hentai is art"
ENCODED_PATH = HexBytes(
    # path is: address || ( uint24 || address)+
    f"{WETH.lower().removeprefix('0x')}{FEE:06x}{YFI.lower().removeprefix('0x')}"
)

TEST_CASES = {
    # Format of test cases:
    # <test name>: dict(
    #    command_bytes: HexBytes=...,
    #    command_args: tuple[HexBytes]=...,
    #    plan_steps: tuple[Callable[[Plan], Plan]]=...,
    # )
    # NOTE: *must all follow the same format to load properly*
    ur.WRAP_ETH.__name__: dict(
        command_bytes=HexBytes(ur.WRAP_ETH.type),
        command_args=(
            HexBytes(
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
            ),
        ),
        plan_steps=(lambda plan: plan.wrap_eth(DEV, AMOUNT),),
    ),
    ur.UNWRAP_WETH.__name__: dict(
        command_bytes=HexBytes(ur.UNWRAP_WETH.type),
        command_args=(
            HexBytes(
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
            ),
        ),
        plan_steps=(lambda plan: plan.unwrap_weth(DEV, AMOUNT),),
    ),
    ur.APPROVE_ERC20.__name__: dict(
        command_bytes=HexBytes(ur.APPROVE_ERC20.type),
        command_args=(
            HexBytes(
                "0000000000000000000000000bc529c00c6401aef6d220be8c6ea1667f6ad93e"
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
            ),
        ),
        plan_steps=(lambda plan: plan.approve_erc20(YFI, DEV),),
    ),
    ur.BALANCE_CHECK_ERC20.__name__: dict(
        command_bytes=HexBytes(ur.BALANCE_CHECK_ERC20.type),
        command_args=(
            HexBytes(
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000bc529c00c6401aef6d220be8c6ea1667f6ad93e"
                "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
            ),
        ),
        plan_steps=(lambda plan: plan.balance_check_erc20(DEV, YFI, AMOUNT),),
    ),
    ur.TRANSFER.__name__: dict(
        command_bytes=HexBytes(ur.TRANSFER.type),
        command_args=(
            HexBytes(
                "0000000000000000000000000bc529c00c6401aef6d220be8c6ea1667f6ad93e"
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
            ),
        ),
        plan_steps=(lambda plan: plan.transfer(YFI, DEV, AMOUNT),),
    ),
    ur.SWEEP.__name__: dict(
        command_bytes=HexBytes(ur.SWEEP.type),
        command_args=(
            HexBytes(
                "0000000000000000000000000bc529c00c6401aef6d220be8c6ea1667f6ad93e"
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
            ),
        ),
        plan_steps=(lambda plan: plan.sweep(YFI, DEV, AMOUNT),),
    ),
    ur.PAY_PORTION.__name__: dict(
        command_bytes=HexBytes(ur.PAY_PORTION.type),
        command_args=(
            HexBytes(
                "0000000000000000000000000bc529c00c6401aef6d220be8c6ea1667f6ad93e"
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000000000000000000000000000000000000000064"
            ),
        ),
        plan_steps=(lambda plan: plan.pay_portion(YFI, DEV, BIPS),),
    ),
    ur.V2_SWAP_EXACT_IN.__name__: dict(
        command_bytes=HexBytes(ur.V2_SWAP_EXACT_IN.type),
        command_args=(
            HexBytes(
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
                "00000000000000000000000000000000000000000000000000000000000004d2"
                "00000000000000000000000000000000000000000000000000000000000000a0"
                "0000000000000000000000000000000000000000000000000000000000000000"
                "0000000000000000000000000000000000000000000000000000000000000002"
                "000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
                "0000000000000000000000000bc529c00c6401aef6d220be8c6ea1667f6ad93e"
            ),
        ),
        plan_steps=(
            lambda plan: plan.v2_swap_exact_in(DEV, AMOUNT, AMOUNT_MIN, [WETH, YFI], PAYER_IS_USER),
        ),
    ),
    ur.V2_SWAP_EXACT_OUT.__name__: dict(
        command_bytes=HexBytes(ur.V2_SWAP_EXACT_OUT.type),
        command_args=(
            HexBytes(
                "000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
                "0000000000000000000000000000000000000000000000000de0b6b3a7640000"
                "00000000000000000000000000000000000000000000000000000000000004d2"
                "00000000000000000000000000000000000000000000000000000000000000a0"
                "0000000000000000000000000000000000000000000000000000000000000000"
                "0000000000000000000000000000000000000000000000000000000000000002"
                "000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
                "0000000000000000000000000bc529c00c6401aef6d220be8c6ea1667f6ad93e"
            ),
        ),
        plan_steps=(
            lambda plan: plan.v2_swap_exact_out(
                DEV, AMOUNT, AMOUNT_MIN, [WETH, YFI], PAYER_IS_USER
            ),
        ),
    ),
}


# Multi-step plan test cases:
def case_combinations(tests):
    return {
        f"{caseA}:{caseB}": dict(
            command_bytes=HexBytes(
                tests[caseA].get("command_bytes", HexBytes(b""))  # type: ignore[operator]
                + tests[caseB].get("command_bytes", HexBytes(b""))
            ),
            command_args=(
                *tests[caseA].get("command_args", tuple()),
                *tests[caseB].get("command_args", tuple()),
            ),
            plan_steps=(
                *tests[caseA].get("plan_steps", tuple()),
                *tests[caseB].get("plan_steps", tuple()),
            ),
        )
        for caseA, caseB in itertools.combinations(tests, 2)
    }


def case_products(testsA, testsB):
    return {
        f"{caseA}:{caseB}": dict(
            command_bytes=HexBytes(
                testsA[caseA].get("command_bytes", HexBytes(b""))  # type: ignore[operator]
                + testsB[caseB].get("command_bytes", HexBytes(b""))
            ),
            command_args=(
                *testsA[caseA].get("command_args", tuple()),
                *testsB[caseB].get("command_args", tuple()),
            ),
            plan_steps=(
                *testsA[caseA].get("plan_steps", tuple()),
                *testsB[caseB].get("plan_steps", tuple()),
            ),
        )
        for caseA, caseB in itertools.product(testsA, testsB)
    }


TWO_STEP_TESTS_CASES = case_combinations(TEST_CASES)
THREE_STEP_TEST_CASES = case_products(TEST_CASES, TWO_STEP_TESTS_CASES)
TEST_CASES.update(TWO_STEP_TESTS_CASES)
TEST_CASES.update(THREE_STEP_TEST_CASES)


@pytest.mark.parametrize("command_name", TEST_CASES)
def test_encode_decode_plan(command_name):
    plan = ur.Plan()
    for add_step in TEST_CASES[command_name].get("plan_steps"):
        plan = add_step(plan)

    encoded_command_bytes, encoded_command_args = (
        TEST_CASES[command_name].get("command_bytes"),
        TEST_CASES[command_name].get("command_args"),
    )
    decoded_plan = ur.Plan.decode(encoded_command_bytes, encoded_command_args)

    assert plan == decoded_plan
    assert plan.encoded_commands == decoded_plan.encoded_commands == encoded_command_bytes
    assert plan.encode_args() == decoded_plan.encode_args() == list(encoded_command_args)
