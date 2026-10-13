# Deployment Plan

## Docker Strategy

- `Dockerfile.build`
  - Portable build environment: AlmaLinux 8 (glibc 2.28) + gcc-toolset-14, CMake, Ninja, Rust
  - Produces every shipped binary, natively per architecture (see `.github/workflows/portable-build.yml`)
- `Dockerfile.runtime`
  - Minimal runtime image based on Ubuntu 22.04
  - Compiles nothing: COPYs `dist/<TARGETARCH>/boat_gateway` built by `Dockerfile.build`
- `Dockerfile.dev`
  - Full development image with compiler toolchain, CMake, Ninja, Python dev stack, `gdb`, `valgrind`
- `docker-compose.yml`
  - Services:
    - `boat-gateway`
    - `boat-agent`
    - `boat-store`
    - optional `timescaledb`

## Multi-Architecture Support

- Build targets:
  - `linux/amd64`
  - `linux/arm64`
- Binaries: built natively per architecture (arm64 on an arm64 runner, no QEMU) in
  `Dockerfile.build`; buildx only assembles the image, with QEMU used for the arm64
  stage's `apt-get` alone
- Release packages (TGZ/DEB/RPM) are produced for both architectures as well
- Distribution baseline: glibc 2.28 / GCC 8 libstdc++ (RHEL 8, Debian 10, Ubuntu 20.04 and newer)
- Registry: `ghcr.io/<owner>/<repo>` lowercased, derived in CI from `github.repository` (`ghcr.io/alextech-stack/boat` for this repo)

## Artifact Versioning

- Semantic versioning: `MAJOR.MINOR.PATCH[-PRERELEASE]+BUILD`
- Version source from Git tags
- `cmake/Version.cmake` extracts version via `git describe`
- Plugin ABI tracked separately with `BOAT_PLUGIN_ABI_VERSION`

## Release Flow

1. Tag release as `v*.*.*`
2. CI builds binaries and packages with CPack (DEB, RPM, TGZ) for amd64 and arm64
3. Publish GitHub Release with packaged artifacts
4. Publish multi-arch Docker images using semver tags
5. Notify downstream automation consumers

