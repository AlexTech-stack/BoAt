#!/usr/bin/env bash
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0
#
# Assert that an ELF file is something we can ship as a portable Linux binary:
#
#   1. it is built for the architecture we are about to label it with -- the
#      linux/arm64 image once carried an x86-64 boat_gateway (issue #51) because
#      nothing looked;
#   2. it needs no glibc / libstdc++ symbol version newer than the baseline
#      (AlmaLinux 8: glibc 2.28, libstdc++ from GCC 8), so it loads on every
#      distribution at or above that baseline;
#   3. it links nothing beyond the C/C++ runtime that every such system has.
#
# Usage: check-portable-binary.sh <elf> <x86_64|aarch64> [<elf> <arch> ...]
# Exits non-zero with a reason on the first violation.

set -euo pipefail

MAX_GLIBC=2.28      # AlmaLinux / RHEL 8, Debian 10
MAX_GLIBCXX=3.4.25  # libstdc++ of GCC 8
MAX_CXXABI=1.3.11   # likewise
# libacl (iceoryx) is a coreutils dependency, so present even in minimal images.
ALLOWED_NEEDED='^(libc|libm|libdl|libpthread|librt|libgcc_s|libstdc\+\+|libacl)\.so\.[0-9]+$|^ld-linux-(x86-64|aarch64)\.so\.[0-9]+$'

fail() { echo "check-portable-binary: $*" >&2; exit 1; }

# version_le A B -> true when A <= B in version order.
version_le() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]; }

check_one() {
  local elf=$1 arch=$2 machine want
  [ -f "$elf" ] || fail "$elf: not found"

  case "$arch" in
    x86_64)  want='Advanced Micro Devices X86-64' ;;
    aarch64) want='AArch64' ;;
    *)       fail "unknown architecture '$arch' (expected x86_64 or aarch64)" ;;
  esac
  machine=$(LC_ALL=C readelf -h "$elf" | sed -n 's/^ *Machine: *//p')
  [ "$machine" = "$want" ] || fail "$elf: built for '$machine', expected $arch"

  # Version references (.gnu.version_r) are what the dynamic loader checks.
  local refs ver max
  refs=$(LC_ALL=C readelf -V --wide "$elf" | grep -oE 'Name: (GLIBC|GLIBCXX|CXXABI)_[0-9.]+' | sed 's/^Name: //' | sort -u || true)
  for pair in GLIBC:$MAX_GLIBC GLIBCXX:$MAX_GLIBCXX CXXABI:$MAX_CXXABI; do
    local tag=${pair%%:*} limit=${pair#*:}
    max=$(printf '%s\n' "$refs" | sed -n "s/^${tag}_//p" | sort -V | tail -1)
    [ -z "$max" ] && continue
    version_le "$max" "$limit" \
      || fail "$elf: needs ${tag}_$max, newer than the ${tag}_$limit baseline -- not built in the baseline container?"
  done

  local lib
  while read -r lib; do
    [[ "$lib" =~ $ALLOWED_NEEDED ]] || fail "$elf: links $lib, which is not part of the base C/C++ runtime"
  done < <(LC_ALL=C readelf -d "$elf" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')

  echo "ok: $elf ($arch, glibc <= $(printf '%s\n' "$refs" | sed -n 's/^GLIBC_//p' | sort -V | tail -1))"
}

[ $# -ge 2 ] && [ $(($# % 2)) -eq 0 ] || fail "usage: $0 <elf> <x86_64|aarch64> [<elf> <arch> ...]"
while [ $# -gt 0 ]; do
  check_one "$1" "$2"
  shift 2
done
