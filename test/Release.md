# TestSet: Release

System-level tests of what a release actually ships: the `.deb`, `.rpm` and `.tar.gz`
packages for amd64 and arm64, and the multi-arch runtime container image. They exercise the
**downloaded release artifacts**, not a build tree. Run them against the artifacts attached to
the GitHub release (or a pre-release dry run such as `v0.2.0-rc.1`).

Common precondition: the release's six packages and its image tag are available. The
expected version below is `0.2.0`; substitute the version under test.

Hosts: amd64 cases need any x86-64 Linux host. arm64 cases need an aarch64 host, or the
image under QEMU (`docker run --platform linux/arm64`, slower but valid). The packages are
built on a glibc 2.28 baseline (AlmaLinux 8), so they must also install on older distros.

---

### TC_Release_001_deb_install_and_contents

**TestSets:** [Release]

**Preconditions:**
- Clean Ubuntu 22.04 or Debian 12 amd64 host (VM or container) without BoAt installed

**TestSteps:**
1. `dpkg-deb -f boat-platform_0.2.0_amd64.deb Package Version Architecture Depends`
2. `sudo apt install ./boat-platform_0.2.0_amd64.deb`
3. `dpkg -L boat-platform`
4. `BOAT_CAN_INTERFACES=vcan0 BOAT_NODE_PLUGINS=/usr/lib/boat/plugins/pdu_router.so,/usr/lib/boat/plugins/can_tp.so?{\"iface\":\"vcan0\"} boat_gateway`
5. `sudo apt remove boat-platform`

**Expected:**
- Step 1: `Package: boat-platform`, `Version: 0.2.0`, `Architecture: amd64`,
  `Depends: libc6 (>= 2.28), libstdc++6 (>= 8), libacl1`
- Step 2: installs without pulling in anything unexpected
- Step 3: exactly 15 files -- `/usr/bin/boat_gateway`; `/usr/include/boat/{plugin.h,frame.h}`;
  `/usr/lib/boat/plugins/{pdu_router,can_tp,someip,tcp,probe}.so` plus the
  `can_tp`/`someip`/`tcp`/`probe` `.schema.json` sidecars;
  `/usr/share/doc/boat-platform/{LICENSE,NOTICE,THIRD_PARTY_NOTICES.md}`. No protobuf/Abseil
  headers, static libraries, CMake configs or `/usr/bin/protoc`
- Step 4: both plugins load from `/usr/lib/boat/plugins`; `boat config show` reports
  `"version": "0.2.0"`
- Step 5: removes cleanly

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Release_002_rpm_install

**TestSets:** [Release]

**Preconditions:**
- Clean AlmaLinux 8/9 or Fedora amd64 host

**TestSteps:**
1. `rpm -qip boat-platform-0.2.0-1.x86_64.rpm`
2. `sudo dnf install ./boat-platform-0.2.0-1.x86_64.rpm`
3. `rpm -ql boat-platform`; start the gateway as in TC_Release_001 step 4

**Expected:**
- Step 1: name `boat-platform`, version `0.2.0`, license `Apache-2.0`, arch `x86_64`
- Step 2: installs (v0.1.x's RPM could not be installed at all)
- Step 3: the same 15 files as TC_Release_001; the gateway starts and loads plugins

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Release_003_tarball_on_old_distro

**TestSets:** [Release]

**Preconditions:**
- A host or container on an older glibc, e.g. `almalinux:8` (glibc 2.28) or `debian:11`

**TestSteps:**
1. `tar xzf boat-platform-0.2.0-Linux-x86_64.tar.gz`
2. `ldd boat-platform-0.2.0-Linux-x86_64/bin/boat_gateway`
3. `BOAT_GRPC_PORT=50051 ./boat-platform-0.2.0-Linux-x86_64/bin/boat_gateway`

**Expected:**
- Step 2: no "not found" entries; only system libraries (libc, libstdc++, libm, libgcc_s,
  libacl, and libpthread/libdl/librt on glibc < 2.34)
- Step 3: starts and prints `gRPC server listening on 0.0.0.0:50051` -- no
  `GLIBC_2.xx not found` error

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Release_004_arm64_packages

**TestSets:** [Release]

**Preconditions:**
- An aarch64 Linux host (e.g. Raspberry Pi 4/5 with a 64-bit OS, AWS Graviton)

**TestSteps:**
1. `file` on the `bin/boat_gateway` inside `boat-platform-0.2.0-Linux-aarch64.tar.gz`
2. Install `boat-platform_0.2.0_arm64.deb` (or the `.aarch64.rpm`)
3. Set up vcan0, start the gateway with `pdu_router` and `can_tp`; run
   `boat frame send --bus-type can --can-id 0x123 --iface vcan0 --data AABBCCDD` with
   `candump vcan0` open

**Expected:**
- Step 1: `ELF 64-bit LSB ... ARM aarch64`
- Step 2: `Architecture: arm64`; same file set as TC_Release_001
- Step 3: the frame appears in `candump`; this is the first release that ships arm64

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Release_005_container_image_multiarch

**TestSets:** [Release]

**Preconditions:**
- Docker with buildx/QEMU for the arm64 run on an amd64 host

**TestSteps:**
1. `docker manifest inspect ghcr.io/alextech-stack/boat:v0.2.0`
2. `docker run --rm --platform linux/amd64 --network host -e BOAT_GRPC_PORT=50090 ghcr.io/alextech-stack/boat:v0.2.0`
3. The same with `--platform linux/arm64`
4. `docker run --rm --entrypoint ls ghcr.io/alextech-stack/boat:v0.2.0 /usr/share/doc/boat-platform`
5. Compare the `:latest` digest with `:v0.2.0`; if a `-rc` tag was pushed, compare it too

**Expected:**
- Step 1: manifests for both `linux/amd64` and `linux/arm64`
- Steps 2-3: each prints `gRPC server listening on 0.0.0.0:50090`; the arm64 run does not fail
  with `exec format error` (#51)
- Step 4: `LICENSE NOTICE THIRD_PARTY_NOTICES.md`
- Step 5: `:latest` equals `:v0.2.0`; a pre-release tag did **not** move `:latest`
- Note: the image ships the gateway only, no plugins

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Release_006_out_of_tree_plugin_builds_against_package

**TestSets:** [Release], [Plugins]

**Preconditions:**
- The `.deb` from TC_Release_001 installed; `gcc`/`g++` available

**TestSteps:**
1. Write `p.c`:
   `#include <boat/plugin.h>` / `int main(void){return BOAT_PLUGIN_ABI_VERSION;}`
2. `gcc -c p.c -o p.o` and `g++ -std=c++17 -x c++ -c p.c -o pp.o`
3. Build a minimal v9 plugin `.so` against `/usr/include/boat` (the probe plugin in
   `src/plugins/probe/` is the canonical minimal example) and load it with `BOAT_NODE_PLUGINS`

**Expected:**
- Step 2: both compile. Before v0.2.0 this failed with
  `fatal error: boat/frame.h: No such file or directory` -- the package shipped `plugin.h` but
  not the `frame.h` it includes
- Step 3: the gateway loads the plugin, and `boat plugin list` shows it with `scope node`

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Release_007_version_consistency

**TestSets:** [Release], [CLI]

**Preconditions:**
- The release installed; SDK and CLI installed from the tagged source
  (`pip install ./boat-platform/sdk/python ./boat-platform/cli`)

**TestSteps:**
1. `boat config show | grep '"version"'` against the packaged gateway
2. `pip show boat-py boat-cli | grep Version`
3. Check that the GitHub release title, tag, `CHANGELOG.md` heading and package file names
   agree

**Expected:**
- Everything reads `0.2.0`; `CHANGELOG.md` has a dated `## [0.2.0]` section and an empty
  `## [Unreleased]`

**Verdict:** NOT_TESTED

**Result:**
