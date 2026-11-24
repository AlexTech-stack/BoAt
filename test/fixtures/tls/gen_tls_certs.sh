#!/usr/bin/env bash
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0
#
# Generate the throwaway PKI used by TC_Gateway_014 (TLS/mTLS).
#
# Certs are generated on demand rather than committed: they expire (2 days
# here) and a stale committed certificate fails the test with an error that
# has nothing to do with the code under test.
#
#   ./gen_tls_certs.sh
#   BOAT_TLS_CERT=gw.pem BOAT_TLS_KEY=gw.key BOAT_TLS_CLIENT_CA=ca.pem ...
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
    -subj "/CN=boat-test-ca" -keyout ca.key -out ca.pem >/dev/null 2>&1

openssl req -newkey rsa:2048 -nodes -subj "/CN=localhost" \
    -keyout gw.key -out gw.csr >/dev/null 2>&1
cat > gw.ext <<'EOF'
subjectAltName = DNS:localhost, IP:127.0.0.1
extendedKeyUsage = serverAuth
EOF
openssl x509 -req -in gw.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
    -days 2 -extfile gw.ext -out gw.pem >/dev/null 2>&1

openssl req -newkey rsa:2048 -nodes -subj "/CN=boat-test-client" \
    -keyout cl.key -out cl.csr >/dev/null 2>&1
cat > cl.ext <<'EOF'
extendedKeyUsage = clientAuth
EOF
openssl x509 -req -in cl.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
    -days 2 -extfile cl.ext -out cl.pem >/dev/null 2>&1

rm -f gw.csr cl.csr gw.ext cl.ext ca.srl
echo "generated ca.pem/gw.pem/cl.pem (and keys) in ${DIR}"
