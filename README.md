# Uniswap SDK

**NOTE: Work In Progress**

## Usage

_NOTE: Only Uniswap V2 supported_

Usage:

```py
>>> from uniswap_sdk import Uniswap
>>> uni = Uniswap()
>>> from ape_tokens import tokens
>>> yfi = tokens["YFI"]
>>> usdc = token["USDC"]
>>> uni.price(yfi, usdc)
Decimal("4200.123")
```

## Dependencies

* [python3](https://www.python.org/downloads) version 3.10 or greater, python3-dev

## Installation

### via `pip`

You can install the latest release via [`pip`](https://pypi.org/project/pip/):

```bash
pip install uniswap_sdk
```

### via `setuptools`

You can clone the repository and use [`setuptools`](https://github.com/pypa/setuptools) for the most up-to-date version:

```bash
git clone https://github.com/ApeWorX/uniswap-sdk.git
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
