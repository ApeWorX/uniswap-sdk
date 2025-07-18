#!/usr/bin/env python
# -*- coding: utf-8 -*-
from setuptools import find_packages, setup  # type: ignore

extras_require = {
    "test": [  # `test` GitHub Action jobs uses this
        "pytest",  # Core testing package
        "pytest-xdist",  # multi-process runner
        "pytest-cov",  # Coverage analyzer plugin
        "hypothesis",  # Strategy-based fuzzer
    ],
    "lint": [
        "black",  # auto-formatter and linter
        "mypy",  # Static type analyzer
        "flake8",  # Style linter
        "isort",  # Import sorting linter
    ],
    "release": [  # `release` GitHub Action job uses this
        "setuptools>=75.6.0",  # Installation tool
        "wheel",  # Packaging tool
        "twine",  # Package upload tool
    ],
    "dev": [
        "commitizen",  # Manage commits and publishing releases
        "pre-commit",  # Ensure that linters are run prior to commiting
        "pytest-watch",  # `ptw` test watcher/runner
        "IPython",  # Console for interacting
        "ipdb",  # Debugger (Must use `export PYTHONBREAKPOINT=ipdb.set_trace`)
    ],
    "bot": [
        "silverback>=0.7.22",  # Need for linting as it is optional
    ],
}

# NOTE: `pip install -e .[dev]` to install package
extras_require["dev"] = (
    extras_require["test"]
    + extras_require["lint"]
    + extras_require["release"]
    + extras_require["bot"]
    + extras_require["dev"]
)

with open("./README.md") as readme:
    long_description = readme.read()


setup(
    name="uniswap-sdk",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    description="""uniswap-sdk: SDK for Uniswap smart contracts""",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="ApeWorX Ltd.",
    author_email="admin@apeworx.io",
    url="https://github.com/ApeWorX/uniswap-sdk",
    include_package_data=True,
    install_requires=[
        "eth-ape>=0.8.33,<1",
        # Graph library for solving (Requires Python 3.10+)
        "networkx>=3.4.2,<4",
        # Required plugin for working with tokens
        "ape-tokens>=0.8.5,<1",
    ],
    entry_points={
        "console_scripts": ["uni=uniswap_sdk._cli:cli"],
    },
    python_requires=">=3.10,<4",
    extras_require=extras_require,
    py_modules=["uniswap_sdk"],
    license="Apache-2.0",
    zip_safe=False,
    keywords="ethereum",
    packages=find_packages(exclude=["tests", "tests.*"]),
    package_data={"uniswap_sdk": ["py.typed", "*.json"]},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Operating System :: MacOS",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
)
