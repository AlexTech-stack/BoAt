# Third-party notices

BoAt itself is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)).

No third-party source is checked into this repository. The C++ components
below are fetched by CMake at build time and **statically linked into the
shipped `boat_gateway` binary**, so the CPack packages and the container image
redistribute them in object form. Dependencies that are linked transitively are
listed separately; each component remains under its own license. This list is
provided for convenience — the authoritative license text ships with each
component.

## C++ build-time dependencies

Declared via CMake `FetchContent` in `boat-platform/CMakeLists.txt` and
`boat-platform/src/tests/CMakeLists.txt`.

| Component | Version | License |
| --- | --- | --- |
| [gRPC](https://github.com/grpc/grpc) | v1.75.0 | Apache-2.0 |
| [Protocol Buffers](https://github.com/protocolbuffers/protobuf) | v31.1 | BSD-3-Clause |
| [spdlog](https://github.com/gabime/spdlog) | v1.14.1 | MIT |
| [iceoryx2](https://github.com/eclipse-iceoryx/iceoryx2) | v0.4.1 | Apache-2.0 OR MIT |
| [Eclipse iceoryx](https://github.com/eclipse-iceoryx/iceoryx) (`iceoryx_hoofs`, pulled in by iceoryx2) | transitive | Apache-2.0 |
| [toml++](https://github.com/marzer/tomlplusplus) | v3.4.0 | MIT |
| [SQLite](https://www.sqlite.org/) (amalgamation) | 3.46.0 | Public domain |
| [Catch2](https://github.com/catchorg/Catch2) (tests only) | v3.6.0 | BSL-1.0 |

Building also requires a Rust toolchain (`cargo`), which downloads the
crates.io dependencies of iceoryx2's Rust core. Their compiled code is linked
into the shipped binary; they are predominantly MIT and/or Apache-2.0 (the
exact set is pinned by iceoryx2's `Cargo.lock`).

The following are built as part of gRPC v1.75.0 and linked into
`boat_gateway`:

| Component | License |
| --- | --- |
| [Abseil](https://abseil.io/) | Apache-2.0 |
| [RE2](https://github.com/google/re2) | BSD-3-Clause |
| [c-ares](https://c-ares.org/) | MIT |
| [zlib](https://zlib.net/) | Zlib |
| [BoringSSL](https://boringssl.googlesource.com/boringssl/) | OpenSSL/ISC-style |
| [upb / utf8_range](https://github.com/protocolbuffers/upb) | BSD-3-Clause |

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
`boat_gateway` binary that links the C++ dependencies above. Both carry
`LICENSE`, `NOTICE` and this file under `share/doc/boat-platform/`; binary
redistributors must keep them alongside the binary.

Those binaries are built in `boat-platform/Dockerfile.build` with AlmaLinux 8's
`gcc-toolset-14`, which links the parts of libstdc++ newer than the system's
GCC 8 (`libstdc++_nonshared.a`) and, where referenced, `libatomic.a` statically
into the binary. Both are GCC runtime libraries under **GPL-3.0 with the GCC
Runtime Library Exception**, which permits this without affecting the
licensing of the resulting binary. glibc, the shared libstdc++ and `libacl`
are used from the target system, not shipped.

## Not third-party

`tools/wireshark/*.lua` are BoAt's own dissector scripts, interpreted by
Wireshark (GPL-2.0) as plugins rather than linked into it; they stay
Apache-2.0. The AUTOSAR specification PDFs referenced by the docs live under
`spec/`, which is gitignored and never distributed with this repository.
