# Uniswap SDK

**NOTE: Work In Progress**

## Usage

Uniswap V2 Usage:

```py
>>> from uniswap_sdk import v2
>>> factory = v2.Factory()
>>> for pair in factory.get_all_pairs():
...     print(pair)  # WARNING: Will take 6 mins or more to fetch
>>> len(list(factory))  # Cached, almost instantaneous afterwards
396757
>>> from ape_tokens import tokens
>>> yfi = tokens["YFI"]
>>> for pair in factory.get_pairs_by_token(yfi):
...     print(pair)  # WARNING: Will take 12 mins or more to index
>>> len(factory["YFI"])  # Already indexed, almost instantaneous
73
>>> pair = factory.get_pair(yfi, tokens["USDC"])  # Single contract call
<uniswap_sdk.v2.Pair address=0xdE37cD310c70e7Fa9d7eD3261515B107D5Fe1F2d>
>>> for route in factory.find_routes(yfi, usdc, depth=3):
...     # WARNING: For tokens with lots of pairs, exploring at depth of 3
...     #          or more will take a long time -- use the default of 2
...     # Routes can be used for path planning now!
```

## Dependencies

* [python3](https://www.python.org/downloads) version 3.9 or greater, python3-dev

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
