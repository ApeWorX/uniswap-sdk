# Uniswap SDK

Ape-based SDK for working with deployments of Uniswap protocol

## Dependencies

- [python3](https://www.python.org/downloads) version 3.10 or greater, python3-dev

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

### Scripting

The SDK can be used for any scripting task very easily:

```py
>>> from ape_tokens import tokens
>>> from uniswap_sdk import Uniswap
>>> uni = Uniswap(use_v3=False)  # Can skip versions and only index certain tokens
>>> list(uni.index(tokens=tokens))  # Takes time, but makes planning faster (recommended for scripting)
>>> uni.price("UNI", "USDC")  # Get liquidity-weighted prices of entire index in real-time
Decimal("4.75")
>>> usdc = tokens["USDC"]
>>> tx = uni.swap(
...     "UNI",
...     usdc,  # Can use any ContractInstance type
...     amount_in="12 UNI",  # Uses Ape's conversion system
...     slippage=0.3,
...     deadline=timedelta(minutes=2),
...     sender=trader,
... )
```

### Silverback

The SDK has special support for use within [Silverback](https://silverback.apeworx.io) bots,
which takes advantage of real-time processing to drastically reduce the overhead of certain search
and solver functions of the SDK:

```py
from ape_tokens import tokens
from silverback import SilverbackBot
from uniswap_sdk import Uniswap

bot = SilverbackBot()
uni = Uniswap()
uni.install(bot)  # This replaces having to do `uni.index()`

# NOTE: The bot will now process all swaps in the background to keep it's indexes up-to-date!

@bot.cron("* * * * *")
async def weth_price(t):
    # So now when you use top-level functions, it takes advantage of cached data in the index
    return uni.price("WETH", "USDC")  # This executes faster w/ Silverback!
```

### Custom Solver

The SDK comes with a default Solver that should be performant enough for most situations.
However, it is likely that you will want to design a custom solver function or class in order
to obtain better results when performing actions like `uni.swap` which leverage the solver.

You can override the default solver by providing a function or object which matches the following interface:

```py
from uniswap_sdk import Order
Route = tuple[PairType, ...]  # 1 (or more) `PairType`s (e.g. `UniswapV2Pair`, etc.)
Solution = dict[Route, Decimal]  # mapping of Route -> amount to swap via Route
SolverType = Callable[[Order, Iterable[Route]], Solution]
# Given `amount` of `token` and `*routes`, find `solution`
```

This can be a class, allowing more flexibility in how you design your solver:

```py
class Solver:
    def __call__(self, order: Order, routes: Iterable[Route]) -> Solution:
        # This function must match `SolverType` to work

my_solver = Solver(...)

uni = Uniswap(use_solver=my_solver)
uni.solve(...)  # Will now use `my_solver` to find solutions (also `uni.swap`)
```

## Development

This project is in development and should be considered a beta.
Things might not be in their final state and breaking changes may occur.
Comments, questions, criticisms and pull requests are welcomed.

### Support

Support for various Uniswap-related protocols:

- [ ] V1
- [x] V2
- [x] V3
- [ ] V4
- [ ] Permit2
- [x] UniversalRouter

## License

This project is licensed under the [Apache 2.0](LICENSE).
