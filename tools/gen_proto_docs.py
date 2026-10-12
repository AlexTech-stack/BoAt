#!/usr/bin/env python3
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Regenerate the countable parts of ``boat-platform/docs/api/protobuf-definitions.md``.

The proto file/service/RPC counts and the per-service RPC lists used to be
hand-maintained, and drifted twice in a week (issue #13). This script derives
them from ``boat-platform/proto/boat/v1/*.proto`` instead, rewriting only the
regions of the document delimited by ``<!-- BEGIN/END GENERATED: name -->``
markers. Everything outside a marker is prose and is never touched.

Usage::

    python3 tools/gen_proto_docs.py            # rewrite the doc in place
    python3 tools/gen_proto_docs.py --check    # exit 1 if the doc is stale
    python3 tools/gen_proto_docs.py --init     # insert the markers once

``--init`` is a one-time convenience for adopting the markers: it finds the
existing summary block and each ``### <Service> (`` section's bullet list and
wraps them. Normal runs require the markers to be present, so a newly added
proto service fails the check loudly until its section exists.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROTO_DIR = REPO_ROOT / "boat-platform" / "proto" / "boat" / "v1"
DEFAULT_DOC = REPO_ROOT / "boat-platform" / "docs" / "api" / "protobuf-definitions.md"

SUMMARY_MARKER = "proto-summary"

BEGIN_TEMPLATE = "<!-- BEGIN GENERATED: {name} -->"
END_TEMPLATE = "<!-- END GENERATED: {name} -->"


class GenError(RuntimeError):
    """A marker is missing, stale, or cannot be placed."""


# ── Parsing ──────────────────────────────────────────────────────────────────

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_SERVICE_RE = re.compile(r"\bservice\s+(\w+)\s*\{")
_RPC_RE = re.compile(
    r"\brpc\s+(\w+)\s*\(\s*(stream\s+)?[\w.]+\s*\)\s*returns\s*\(\s*(stream\s+)?[\w.]+\s*\)"
)


@dataclass(frozen=True)
class Rpc:
    name: str
    kind: str | None  # "server streaming", "bidirectional streaming", "client streaming" or None


@dataclass(frozen=True)
class Service:
    name: str
    file: str
    rpcs: tuple[Rpc, ...]


@dataclass(frozen=True)
class ProtoFile:
    name: str
    services: tuple[Service, ...]


def strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _service_bodies(text: str) -> list[tuple[str, str]]:
    """Return (service_name, body) pairs, brace-matched so a `service` field
    inside a message cannot be mistaken for a service declaration."""
    found: list[tuple[str, str]] = []
    pos = 0
    while True:
        match = _SERVICE_RE.search(text, pos)
        if match is None:
            return found
        depth = 0
        in_string: str | None = None
        escaped = False
        index = match.end() - 1  # at the opening brace
        while index < len(text):
            char = text[index]
            if in_string is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == in_string:
                    in_string = None
            elif char == '"':
                in_string = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        if depth != 0:
            raise GenError(f"unbalanced braces in service {match.group(1)}")
        found.append((match.group(1), text[match.end() : index]))
        pos = index + 1


def parse_proto_file(path: Path) -> ProtoFile:
    text = strip_comments(path.read_text(encoding="utf-8"))
    services = []
    for name, body in _service_bodies(text):
        rpcs = []
        for rpc in _RPC_RE.finditer(body):
            client_stream = bool(rpc.group(2))
            server_stream = bool(rpc.group(3))
            if client_stream and server_stream:
                kind = "bidirectional streaming"
            elif server_stream:
                kind = "server streaming"
            elif client_stream:
                kind = "client streaming"
            else:
                kind = None
            rpcs.append(Rpc(rpc.group(1), kind))
        services.append(Service(name, path.name, tuple(rpcs)))
    return ProtoFile(path.name, tuple(services))


def parse_protos(proto_dir: Path) -> list[ProtoFile]:
    files = sorted(proto_dir.glob("*.proto"))
    if not files:
        raise GenError(f"no .proto files under {proto_dir}")
    return [parse_proto_file(path) for path in files]


# ── Rendering ────────────────────────────────────────────────────────────────


def render_summary(files: list[ProtoFile]) -> str:
    services = [service for proto in files for service in proto.services]
    rpc_total = sum(len(service.rpcs) for service in services)
    lines = [
        f"- {len(files)} proto files defining {len(services)} gRPC services, "
        f"{rpc_total} RPCs in total:",
        "",
        "| File | Service | RPCs |",
        "|---|---|---|",
    ]
    for proto in files:
        if proto.services:
            names = ", ".join(service.name for service in proto.services)
            count = sum(len(service.rpcs) for service in proto.services)
            lines.append(f"| `{proto.name}` | {names} | {count} |")
        else:
            lines.append(f"| `{proto.name}` | — | — |")
    return "\n".join(lines)


def render_rpcs(service: Service) -> str:
    lines = []
    for rpc in service.rpcs:
        suffix = f" ({rpc.kind})" if rpc.kind else ""
        lines.append(f"- `{rpc.name}`{suffix}")
    if not lines:
        lines.append("- _(no RPCs declared)_")
    return "\n".join(lines)


# ── Marker handling ──────────────────────────────────────────────────────────


def _marker_name(line: str, template: str) -> str | None:
    prefix = template.split("{name}")[0]
    suffix = template.split("{name}")[1]
    stripped = line.strip()
    if stripped.startswith(prefix) and stripped.endswith(suffix):
        return stripped[len(prefix) : len(stripped) - len(suffix)]
    return None


def find_markers(lines: list[str]) -> dict[str, tuple[int, int]]:
    markers: dict[str, tuple[int, int]] = {}
    for index, line in enumerate(lines):
        name = _marker_name(line, BEGIN_TEMPLATE)
        if name is None:
            continue
        if name in markers:
            raise GenError(f"duplicate BEGIN marker for '{name}'")
        markers[name] = (index, -1)
    for index, line in enumerate(lines):
        name = _marker_name(line, END_TEMPLATE)
        if name is None:
            continue
        if name not in markers:
            raise GenError(f"END marker without BEGIN for '{name}'")
        if markers[name][1] != -1:
            raise GenError(f"duplicate END marker for '{name}'")
        markers[name] = (markers[name][0], index)
    for name, (begin, end) in markers.items():
        if end == -1:
            raise GenError(f"BEGIN marker without END for '{name}'")
        if end <= begin:
            raise GenError(f"END marker before BEGIN for '{name}'")
    return markers


def _replace_block(lines: list[str], begin: int, end: int, content: str) -> list[str]:
    return lines[: begin + 1] + content.splitlines() + lines[end:]


def insert_markers(text: str, files: list[ProtoFile]) -> str:
    """One-time marker adoption: wrap the existing summary block and each
    service section's bullet list. Refuses to guess -- a missing summary line
    or service section is an error, not a silent no-op. A block that already
    has its marker is left alone, so the operation is idempotent."""
    lines = text.splitlines()

    if not any(_marker_name(line, BEGIN_TEMPLATE) == SUMMARY_MARKER for line in lines):
        summary_begin = None
        for index, line in enumerate(lines):
            if re.match(r"^- \d+ proto files defining ", line):
                summary_begin = index
                break
        if summary_begin is None:
            raise GenError("--init: could not find the '- N proto files defining ...' summary line")
        summary_end = summary_begin
        for index in range(summary_begin + 1, len(lines)):
            if lines[index].startswith("|"):
                summary_end = index
            elif lines[index].strip() and summary_end != index - 1:
                break
        lines = (
            lines[:summary_begin]
            + [BEGIN_TEMPLATE.format(name=SUMMARY_MARKER)]
            + lines[summary_begin : summary_end + 1]
            + [END_TEMPLATE.format(name=SUMMARY_MARKER)]
            + lines[summary_end + 1 :]
        )

    for proto in files:
        for service in proto.services:
            name = service.name
            marker_name = f"rpcs-{name}"
            if any(_marker_name(line, BEGIN_TEMPLATE) == marker_name for line in lines):
                continue
            heading = None
            for index, line in enumerate(lines):
                if line.startswith(f"### {name} ("):
                    heading = index
                    break
            if heading is None:
                raise GenError(
                    f"--init: no '### {name} (' section in the document; "
                    "add the section (and its marker) by hand"
                )
            list_begin = None
            for index in range(heading + 1, len(lines)):
                if lines[index].startswith("- "):
                    list_begin = index
                    break
                if lines[index].startswith("#"):
                    break
            if list_begin is None:
                raise GenError(f"--init: no RPC bullet list under '### {name} ('")
            list_end = list_begin
            for index in range(list_begin, len(lines)):
                if lines[index].startswith("- "):
                    list_end = index
                else:
                    break
            lines = (
                lines[:list_begin]
                + [BEGIN_TEMPLATE.format(name=f"rpcs-{name}")]
                + lines[list_begin : list_end + 1]
                + [END_TEMPLATE.format(name=f"rpcs-{name}")]
                + lines[list_end + 1 :]
            )

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def generate(text: str, files: list[ProtoFile], *, init: bool = False) -> str:
    if init:
        find_markers(text.splitlines())  # refuses to double-insert
        text = insert_markers(text, files)

    lines = text.splitlines()
    markers = find_markers(lines)
    services = [service for proto in files for service in proto.services]
    expected = {SUMMARY_MARKER} | {f"rpcs-{service.name}" for service in services}

    missing = sorted(expected - markers.keys())
    if missing:
        raise GenError(
            "missing generated-marker block(s): " + ", ".join(missing) + " (run with --init once, or add by hand)"
        )
    stale = sorted(markers.keys() - expected)
    if stale:
        raise GenError(
            "marker block(s) with no matching service in proto/: " + ", ".join(stale)
        )

    for name, (begin, end) in sorted(markers.items(), key=lambda item: item[1][0], reverse=True):
        if name == SUMMARY_MARKER:
            content = render_summary(files)
        else:
            service_name = name[len("rpcs-") :]
            service = next(service for service in services if service.name == service_name)
            content = render_rpcs(service)
        lines = _replace_block(lines, begin, end, content)

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--proto-dir", type=Path, default=DEFAULT_PROTO_DIR)
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    parser.add_argument("--check", action="store_true", help="exit 1 if the document is stale")
    parser.add_argument("--init", action="store_true", help="insert the generated markers once")
    args = parser.parse_args(argv)

    try:
        files = parse_protos(args.proto_dir)
        original = args.doc.read_text(encoding="utf-8")
        regenerated = generate(original, files, init=args.init)
    except GenError as error:
        print(f"gen_proto_docs: error: {error}", file=sys.stderr)
        return 2

    if args.check:
        if regenerated != original:
            print(
                f"gen_proto_docs: {args.doc} is stale; run tools/gen_proto_docs.py and commit the result",
                file=sys.stderr,
            )
            return 1
        print(f"gen_proto_docs: {args.doc} is up to date")
        return 0

    if regenerated != original:
        args.doc.write_text(regenerated, encoding="utf-8")
        print(f"gen_proto_docs: rewrote {args.doc}")
    else:
        print(f"gen_proto_docs: {args.doc} already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
