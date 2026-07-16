# proxy-tools 0.1.0 license provenance

The `proxy-tools==0.1.0` wheel and source archive omit a license file. Their package metadata says `MIT`, while the matching upstream source repository contains `LICENSE.txt` with BSD terms.

MOKU preserves the upstream license text rather than inventing missing terms.

- PyPI source archive SHA-256: `ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010`
- PyPI `proxy_tools/__init__.py` SHA-256: `d1539d95e1a713c068ca81d42e047b2c76568964cf277596d4e19efb22f476be`
- Matching upstream commit: `db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6`
- Upstream license URL: https://raw.githubusercontent.com/jtushman/proxy_tools/db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6/LICENSE.txt
- Vendored license SHA-256: `a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4`

## Reproducible local wheel

PyPI does not publish a wheel for `proxy-tools==0.1.0`. To avoid executing a legacy source build during normal dependency installation, MOKU vendors a reviewed pure-Python wheel at `third_party/wheels/proxy_tools-0.1.0-py3-none-any.whl`.

- Source archive: `proxy_tools-0.1.0.tar.gz`
- Source archive SHA-256: `ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010`
- Source module SHA-256: `d1539d95e1a713c068ca81d42e047b2c76568964cf277596d4e19efb22f476be`
- Build interpreter: CPython 3.12.10
- Build tools: `setuptools==83.0.0`, `wheel==0.46.3`, `packaging==26.2`
- Build environment: `SOURCE_DATE_EPOCH=1700000000`, `PYTHONHASHSEED=0`, no dependencies, no build isolation
- Wheel SHA-256: `e9c3763d867f00a88c203686480d67950c04f210d8be71c861800bc7e9b53b40`
- Reproducibility check: two independent extracted source trees produced byte-identical wheels
- Wheel contents: `proxy_tools/__init__.py` plus standard `.dist-info` metadata only

The runtime lock requires this exact wheel hash and globally enables `--only-binary=:all:` and `--require-hashes`.

This note records upstream packaging facts and is not legal advice.
