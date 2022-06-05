# Uniswap SDK

**NOTE: Work In Progress**

## Usage

**NOTE: Sketch of potential usage**
```py
from ape_tokens import tokens
from uniswap_sdk import v2 as uni_v2

# Uses v2's TWAP oracle pricing algorithm
price = uni_v2.price(base=tokens["USDC"], quote=tokens["YFI"])
print(f"Buying 100k USDC worth of YFI at {price} USDC/YFI")

# Will automatically discover best possible route for given trade on Uniswap v2
# NOTE: Slippage and timeout are configurable
uni_v2.swap(base=tokens["USDC"], quote=tokens["YFI"], amount_in="100_000 USDC", sender=trader)
```

```py
# Eventual goal?
import uniswap_sdk as uni

# Finds most liquid price across v2 and v3
price = uni.price(base=tokens["USDC"], quote=tokens["YFI"])

# Performs best swap between v2 and v3
uni.swap(base=tokens["USDC"], quote=tokens["YFI"], amount_in="100_000 USDC", sender=trader)
```

## Dependencies

* [python3](https://www.python.org/downloads) version 3.7 or greater, python3-dev

## Installation

### via `pip`

You can install the latest release via [`pip`](https://pypi.org/project/pip/):

```bash
pip install uniswap_sdk
```

### via `setuptools`

You can clone the repository and use [`setuptools`](https://github.com/pypa/setuptools) for the most up-to-date version:

```bash
git clone https://github.com/SilverBackLtd/uniswap-sdk.git
cd uniswap-sdk
python3 setup.py install
```

## Quick Usage

TODO: Describe library overview in code

## Development

This project is in development and should be considered a beta.
Things might not be in their final state and breaking changes may occur.
Comments, questions, criticisms and pull requests are welcomed.

## License

This project is licensed under the [Apache 2.0](LICENSE).
