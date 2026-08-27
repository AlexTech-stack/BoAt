#!/usr/bin/env bash
#
# Download comma.ai's opendbc CAN database files into tools/dbc/opendbc/.
#
# This script only downloads. Converting a .dbc into BoAt's PDU-database JSON
# format is a separate, manual step -- see tools/dbc/README.md.
#
# BoAt does not redistribute any opendbc content. Everything fetched here is
# MIT-licensed, Copyright (c) 2020 Comma.ai, Inc., and is gitignored.
#
# Usage:
#   tools/dbc/fetch_opendbc.sh                 # fetch from master
#   tools/dbc/fetch_opendbc.sh --ref 279d834f  # pin to a commit/tag/branch

set -euo pipefail

REF="${OPENDBC_REF:-master}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)     REF="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *)         echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HERE/opendbc"

command -v curl >/dev/null || { echo "error: curl is required" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Fetching opendbc @ ${REF} from github.com/commaai/opendbc ..."
curl -fsSL "https://codeload.github.com/commaai/opendbc/tar.gz/${REF}" -o "$TMP/opendbc.tar.gz" \
  || { echo "error: download failed -- check the ref '${REF}' and your network" >&2; exit 1; }

mkdir -p "$DEST"
# Extract only opendbc/dbc/*.dbc. --no-wildcards-match-slash keeps '*' from
# crossing directory boundaries, which would also pull in dbc/generator/ --
# those are templates, not usable databases, and _community.dbc exists in both.
tar -xzf "$TMP/opendbc.tar.gz" -C "$TMP" \
  --wildcards --no-wildcards-match-slash '*/opendbc/dbc/*.dbc'
src_dir="$(dirname "$(find "$TMP" -type f -path '*/opendbc/dbc/*.dbc' -print -quit)")"
[[ -n "$src_dir" && -d "$src_dir" ]] || { echo "error: no .dbc files in archive" >&2; exit 1; }
rm -f "$DEST"/*.dbc
cp "$src_dir"/*.dbc "$DEST/"

COUNT=$(find "$DEST" -maxdepth 1 -name '*.dbc' | wc -l)

# Record what was fetched, so a database generated later can be traced back.
cat > "$DEST/FETCHED" <<EOF
source: https://github.com/commaai/opendbc/tree/${REF}/opendbc/dbc
ref:    ${REF}
date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)
files:  ${COUNT} .dbc
license: MIT, Copyright (c) 2020 Comma.ai, Inc.
EOF

echo "Fetched ${COUNT} .dbc files into tools/dbc/opendbc/ (not tracked by git)."
echo "To convert one into a BoAt PDU database, see tools/dbc/README.md."
