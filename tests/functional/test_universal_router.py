import pytest
from hexbytes import HexBytes

from uniswap_sdk.universal_router import Plan


@pytest.mark.parametrize(
    "encoded_plan_commands,encoded_plan_inputs,sdk_plan",
    [
        (
            "0b",
            [
                "000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
                "000000000000000000000000000000000000000000000000016345785d8a0000"
            ],
            Plan().wrap_eth("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", int(10**17)),
        ),
    ],
)
def test_decode_plan(encoded_plan_commands, encoded_plan_inputs, sdk_plan):
    encoded_plan_commands,  encoded_plan_inputs = HexBytes(encoded_plan_commands), list(HexBytes(i) for i in encoded_plan_inputs)
    decoded_plan = Plan.decode(encoded_plan_commands, encoded_plan_inputs)

    assert decoded_plan == sdk_plan
    assert decoded_plan.encoded_commands == encoded_plan_commands
    assert decoded_plan.encode_inputs() == encoded_plan_inputs
