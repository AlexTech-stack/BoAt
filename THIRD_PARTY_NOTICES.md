# Third-party notices

BoAt itself is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)).

Nothing third-party is checked into or distributed by this repository. Every
component below is fetched by CMake at build time, pulled in by `cargo`,
installed as a package dependency, or downloaded on demand by a helper script,
and each remains under its own license. Only **direct** dependencies are
listed; their own transitive dependencies are not enumerated here and carry
their own notices. This list is provided for convenience — the authoritative
license text ships with each component.

## C++ build-time dependencies

Declared via CMake `FetchContent` in `boat-platform/CMakeLists.txt` and
`boat-platform/src/tests/CMakeLists.txt`.

| Component | Version | License |
| --- | --- | --- |
| [gRPC](https://github.com/grpc/grpc) | v1.65.0 | Apache-2.0 |
| [Protocol Buffers](https://github.com/protocolbuffers/protobuf) | v27.3 | BSD-3-Clause |
| [spdlog](https://github.com/gabime/spdlog) | v1.14.1 | MIT |
| [iceoryx2](https://github.com/eclipse-iceoryx/iceoryx2) | v0.4.1 | Apache-2.0 OR MIT |
| [Eclipse iceoryx](https://github.com/eclipse-iceoryx/iceoryx) (`iceoryx_hoofs`, pulled in by iceoryx2) | transitive | Apache-2.0 |
| [toml++](https://github.com/marzer/tomlplusplus) | v3.4.0 | MIT |
| [SQLite](https://www.sqlite.org/) (amalgamation) | 3.46.0 | Public domain |
| [Catch2](https://github.com/catchorg/Catch2) (tests only) | v3.6.0 | BSL-1.0 |

Building also requires a Rust toolchain (`cargo`), which downloads the
crates.io dependencies of iceoryx2's Rust core. Those crates are build-time
only and are not enumerated here; they are predominantly MIT and/or Apache-2.0.

**libacl.** `iceoryx_hoofs` needs `sys/acl.h`. If it is not installed
system-wide, `boat-platform/CMakeLists.txt` downloads the Debian/Ubuntu
`libacl1-dev` package into the build tree
([libacl](https://savannah.nongnu.org/projects/acl/), **LGPL-2.1-or-later**).
It is not redistributed by this repository, but binaries linked against it
carry the LGPL's obligations.

## Python dependencies

| Component | Used by | License |
| --- | --- | --- |
| [grpcio / grpcio-tools](https://github.com/grpc/grpc) | `boat-py` | Apache-2.0 |
| [protobuf](https://github.com/protocolbuffers/protobuf) | `boat-py` | BSD-3-Clause |
| [Typer](https://github.com/fastapi/typer) | `boat-cli` | MIT |
| [Rich](https://github.com/Textualize/rich) | `boat-cli` | MIT |
| [python-can](https://github.com/hardbyte/python-can) | `boat-cli`, `boat.trace_replay` | LGPL-3.0 |
| [NumPy](https://numpy.org/) (optional) | `boat-py[analysis]` | BSD-3-Clause |
| [pytest](https://github.com/pytest-dev/pytest) (dev) | tests | MIT |
| [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) (dev) | tests | Apache-2.0 |
| [FastAPI](https://github.com/fastapi/fastapi) | `ui/`, `tools/` | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | `ui/`, `tools/` | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | `ui/`, `tools/` | MIT |
| [Requests](https://github.com/psf/requests) | `ui/`, `admin_gui/` | Apache-2.0 |
| [PyYAML](https://github.com/yaml/pyyaml) | `ui/`, `admin_gui/` | MIT |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | `tools/pdu_editor.py` | MIT |
| [PySide6](https://doc.qt.io/qtforpython/) | `admin_gui/` | LGPL-3.0 (or commercial Qt) |

## External data, fetched on demand

| Source | Fetched by | License |
| --- | --- | --- |
| [opendbc](https://github.com/commaai/opendbc) CAN databases (`opendbc/dbc/*.dbc`) | `tools/dbc/fetch_opendbc.sh` | MIT — Copyright (c) 2020, Comma.ai, Inc. |

BoAt's PDU replay demos need a realistic CAN database. Rather than ship one,
`tools/dbc/fetch_opendbc.sh` downloads comma.ai's DBC collection at run time
into `tools/dbc/opendbc/`; converting one into a BoAt PDU database with
`tools/dbc2boatjson.py` is a separate step the user runs. Both the downloaded
`.dbc` files and any generated `.json` are gitignored, so **no
opendbc content or derivative is tracked or redistributed by this repository**
and MIT's notice requirement does not attach to BoAt. Anyone who redistributes
the fetched files, or a database generated from them, takes on that obligation
themselves. See `tools/dbc/README.md`.

## Copyleft components worth noting

`python-can`, `PySide6`, and `libacl` are LGPL. BoAt uses all three as
unmodified, separately obtained libraries, which keeps this repository's own
Apache-2.0 licensing intact. Redistributors who bundle or modify them must
honour the LGPL's terms for those components.

## Binary distributions

The CPack packages (`cpack -G "TGZ;DEB;RPM"`) and the container image built
from `boat-platform/Dockerfile.runtime` (base: `ubuntu:22.04`) ship a
`boat_gateway` binary that links the C++ dependencies above. Binary
redistribution must therefore carry those components' licenses and notices in
addition to this repository's [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Not third-party

`tools/wireshark/*.lua` are BoAt's own dissector scripts, interpreted by
Wireshark (GPL-2.0) as plugins rather than linked into it; they stay
Apache-2.0. The AUTOSAR specification PDFs referenced by the docs live under
`spec/`, which is gitignored and never distributed with this repository.
