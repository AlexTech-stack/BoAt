# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Path-confinement tests for the web tools' file endpoints.

These services are unauthenticated. A path parameter must never turn into an
arbitrary read or write, even when the service is deliberately bound to a
routable address.
"""

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (_ROOT / "ui", _ROOT / "tools", _ROOT / "boat-platform" / "sdk" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def _mod(name: str):
    try:
        return importlib.import_module(name)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"cannot import {name}: {exc}")


def _status(exc_info) -> int:
    return exc_info.value.status_code


# ── PDU editor ────────────────────────────────────────────────────────────────

def test_pdu_editor_rejects_absolute_load_outside_config(tmp_path, monkeypatch):
    pdu = _mod("pdu_editor")
    monkeypatch.setattr(pdu, "_CONFIG_DIR", tmp_path / "config")
    (tmp_path / "config").mkdir()

    with pytest.raises(HTTPException) as raised:
        pdu.api_db_load(str(tmp_path / "outside.json"))
    assert _status(raised) == 403


def test_pdu_editor_allows_load_inside_config(tmp_path, monkeypatch):
    pdu = _mod("pdu_editor")
    config = tmp_path / "config"
    config.mkdir()
    db = config / "inside.json"
    db.write_text('{"schema_version": "1.0", "messages": [], "signal_routes": []}')
    monkeypatch.setattr(pdu, "_CONFIG_DIR", config)

    assert pdu.api_db_load(str(db))["schema_version"] == "1.0"


def test_pdu_editor_rejects_save_outside_config(tmp_path, monkeypatch):
    pdu = _mod("pdu_editor")
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr(pdu, "_CONFIG_DIR", config)
    monkeypatch.setattr(pdu, "_current_db", {"schema_version": "1.0", "messages": [], "signal_routes": []})
    monkeypatch.setattr(pdu, "_current_path", None)

    with pytest.raises(HTTPException) as raised:
        pdu.api_db_save({"path": str(tmp_path / "outside.json"), "force": True})
    assert _status(raised) == 403


def test_pdu_editor_allows_save_inside_config(tmp_path, monkeypatch):
    pdu = _mod("pdu_editor")
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr(pdu, "_CONFIG_DIR", config)
    monkeypatch.setattr(pdu, "_current_db", {"schema_version": "1.0", "messages": [], "signal_routes": []})
    monkeypatch.setattr(pdu, "_current_path", None)

    result = pdu.api_db_save({"path": "new.json", "force": True})
    assert result["status"] == "ok"
    assert (config / "new.json").is_file()


# ── Launcher and Commander PDU imports ────────────────────────────────────────

@pytest.mark.parametrize("module_name", ["launcher", "commander"])
def test_pdu_import_rejects_path_outside_config(module_name):
    module = _mod(module_name)

    with pytest.raises(HTTPException) as raised:
        module.api_pdu_import("/etc/passwd")
    assert _status(raised) == 403


def test_launcher_pdu_select_rejects_path_outside_config():
    launcher = _mod("launcher")

    with pytest.raises(HTTPException) as raised:
        launcher.api_pdu_select("../../etc/passwd")
    assert _status(raised) == 403


# ── Trace editor ──────────────────────────────────────────────────────────────

def test_trace_editor_resolves_relative_paths_under_traces(tmp_path, monkeypatch):
    editor = _mod("trace_editor")
    export_dir = tmp_path / "traces"
    monkeypatch.setattr(editor, "_EXPORT_DIR", export_dir)

    assert editor._resolve_path("capture.trace") == (export_dir / "capture.trace").resolve()


def test_trace_editor_allows_tmp_paths(tmp_path, monkeypatch):
    editor = _mod("trace_editor")
    monkeypatch.setattr(editor, "_EXPORT_DIR", tmp_path / "traces")

    assert editor._resolve_path("/tmp/capture.trace") == Path("/tmp/capture.trace").resolve()


@pytest.mark.parametrize("path", ["/etc/passwd", str(Path.home() / "capture.trace")])
def test_trace_editor_rejects_paths_outside_trace_roots(path, tmp_path, monkeypatch):
    editor = _mod("trace_editor")
    monkeypatch.setattr(editor, "_EXPORT_DIR", tmp_path / "traces")

    with pytest.raises(HTTPException) as raised:
        editor._resolve_path(path)
    assert _status(raised) == 403


def test_trace_editor_save_rejects_outside_path(tmp_path, monkeypatch):
    editor = _mod("trace_editor")
    monkeypatch.setattr(editor, "_EXPORT_DIR", tmp_path / "traces")
    monkeypatch.setattr(editor, "_current_path", None)

    with pytest.raises(HTTPException) as raised:
        editor.api_trace_save({"path": str(Path.home() / "capture.trace")})
    assert _status(raised) == 403


def test_trace_editor_rejects_symlink_escape(tmp_path, monkeypatch):
    editor = _mod("trace_editor")
    monkeypatch.setattr(editor, "_EXPORT_DIR", tmp_path / "traces")
    link = tmp_path / "link.trace"
    link.symlink_to("/etc/passwd")

    with pytest.raises(HTTPException) as raised:
        editor._resolve_path(str(link))
    assert _status(raised) == 403


# ── Recorder download ─────────────────────────────────────────────────────────

def test_recorder_download_rejects_sibling_directory_prefix(tmp_path, monkeypatch):
    recorder = _mod("recorder")
    traces = tmp_path / "traces"
    traces.mkdir()
    sibling = tmp_path / "traces_evil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("not a trace")

    monkeypatch.setattr(recorder, "_DEFAULT_OUT_DIR", traces)

    with pytest.raises(HTTPException) as raised:
        recorder.api_download("../traces_evil/secret.txt")
    assert _status(raised) == 403
