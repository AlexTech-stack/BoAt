# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Tests for client-side TLS configuration.

These cover the resolution and fail-loud rules. The handshake itself was
verified end-to-end against a real gateway started with BOAT_TLS_CERT /
BOAT_TLS_KEY (and separately with BOAT_TLS_CLIENT_CA for mTLS) using certs
from a throwaway CA -- see the PR notes. That needs a built gateway binary
and so is not reproduced here.
"""

import grpc
import pytest

from boat.client import BoAtClient, TlsConfig, TlsConfigError, make_channel

_TLS_ENV = ("BOAT_TLS", "BOAT_TLS_CA", "BOAT_TLS_CLIENT_CERT",
            "BOAT_TLS_CLIENT_KEY", "BOAT_TLS_SERVER_NAME")


@pytest.fixture(autouse=True)
def _clean_tls_env(monkeypatch):
    """TLS must not leak between tests, or one stray var turns the rest secure."""
    for name in _TLS_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pem(tmp_path):
    """A file that is non-empty, which is all TlsConfig checks before gRPC."""
    def _make(name: str) -> str:
        p = tmp_path / name
        p.write_bytes(b"-----BEGIN CERTIFICATE-----\nnot-a-real-cert\n"
                      b"-----END CERTIFICATE-----\n")
        return str(p)
    return _make


class TestFromEnv:
    def test_no_env_means_no_tls(self) -> None:
        """Insecure stays the default -- vcan on localhost needs no certs."""
        assert TlsConfig.from_env() is None

    def test_ca_enables_tls(self, monkeypatch, pem) -> None:
        ca = pem("ca.crt")
        monkeypatch.setenv("BOAT_TLS_CA", ca)
        cfg = TlsConfig.from_env()
        assert cfg is not None and cfg.root_ca == ca

    def test_explicit_flag_enables_tls_with_default_roots(self, monkeypatch) -> None:
        monkeypatch.setenv("BOAT_TLS", "1")
        cfg = TlsConfig.from_env()
        assert cfg is not None and cfg.root_ca is None

    @pytest.mark.parametrize("value,enabled", [
        ("1", True), ("true", True), ("TRUE", True),
        ("0", False), ("yes", False), ("", False), ("ture", False),
    ])
    def test_only_exact_opt_in_values_count(self, monkeypatch, value, enabled) -> None:
        """A typo must not silently leave the channel in the clear, and must
        not silently turn TLS on either -- same reasoning as BOAT_TIME_SOURCE
        accepting only the exact string "virtual"."""
        monkeypatch.setenv("BOAT_TLS", value)
        assert (TlsConfig.from_env() is not None) is enabled

    def test_client_cert_and_key_together(self, monkeypatch, pem) -> None:
        monkeypatch.setenv("BOAT_TLS_CA", pem("ca.crt"))
        monkeypatch.setenv("BOAT_TLS_CLIENT_CERT", pem("client.crt"))
        monkeypatch.setenv("BOAT_TLS_CLIENT_KEY", pem("client.key"))
        cfg = TlsConfig.from_env()
        assert cfg.client_cert and cfg.client_key

    @pytest.mark.parametrize("present", ["BOAT_TLS_CLIENT_CERT", "BOAT_TLS_CLIENT_KEY"])
    def test_half_set_client_identity_is_refused(self, monkeypatch, pem, present) -> None:
        """Mirrors the gateway refusing to start with only one of CERT/KEY."""
        monkeypatch.setenv(present, pem("half.pem"))
        with pytest.raises(TlsConfigError, match="must be set together"):
            TlsConfig.from_env()

    def test_server_name_override_is_carried(self, monkeypatch, pem) -> None:
        monkeypatch.setenv("BOAT_TLS_CA", pem("ca.crt"))
        monkeypatch.setenv("BOAT_TLS_SERVER_NAME", "gateway.bench")
        cfg = TlsConfig.from_env()
        assert cfg.channel_options() == [
            ("grpc.ssl_target_name_override", "gateway.bench")
        ]

    def test_no_override_means_no_options(self, monkeypatch, pem) -> None:
        monkeypatch.setenv("BOAT_TLS_CA", pem("ca.crt"))
        assert TlsConfig.from_env().channel_options() == []


class TestFailLoud:
    """A client that downgrades to plaintext when its certs are unusable sends
    traffic in the clear that the operator believed was encrypted. Every one
    of these must raise rather than fall back."""

    def test_unreadable_ca_raises(self) -> None:
        with pytest.raises(TlsConfigError, match="cannot read"):
            TlsConfig(root_ca="/nonexistent/ca.crt").credentials()

    def test_empty_pem_raises(self, tmp_path) -> None:
        empty = tmp_path / "empty.crt"
        empty.write_text("   \n")
        with pytest.raises(TlsConfigError, match="is empty"):
            TlsConfig(root_ca=str(empty)).credentials()

    def test_error_names_the_env_var(self, tmp_path) -> None:
        """The message has to say which variable to go and fix."""
        with pytest.raises(TlsConfigError, match="BOAT_TLS_CLIENT_KEY"):
            TlsConfig(client_key="/nonexistent/k.pem").credentials()


class TestChannelSelection:
    def test_insecure_without_tls(self) -> None:
        ch = make_channel("localhost:50051")
        assert isinstance(ch, grpc.Channel)
        ch.close()

    def test_explicit_config_overrides_env(self, monkeypatch, pem) -> None:
        """Passing a TlsConfig wins over the environment."""
        monkeypatch.setenv("BOAT_TLS_CA", pem("env-ca.crt"))
        explicit = TlsConfig(root_ca=pem("explicit-ca.crt"))
        client = BoAtClient(address="localhost:50051", tls=explicit)
        assert client.tls is explicit
        client.close()

    def test_client_picks_up_env(self, monkeypatch, pem) -> None:
        monkeypatch.setenv("BOAT_TLS_CA", pem("ca.crt"))
        client = BoAtClient(address="localhost:50051")
        assert client.tls is not None
        client.close()

    def test_client_is_insecure_by_default(self) -> None:
        client = BoAtClient(address="localhost:50051")
        assert client.tls is None
        client.close()
