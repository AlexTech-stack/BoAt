# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``tools/gen_proto_docs.py``.

The generator exists because hand-maintained proto/service/RPC counts in
``docs/api/protobuf-definitions.md`` drifted twice in a week (issue #13).
These tests pin the parsing (streaming kinds, comments, brace matching), the
generated output, marker adoption, and the failure modes -- a service with no
marker and a marker with no service must both fail loudly rather than silently
produce a wrong document.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

from gen_proto_docs import (  # noqa: E402
    GenError,
    generate,
    insert_markers,
    main,
    parse_protos,
    render_rpcs,
    render_summary,
)

ALPHA_PROTO = """\
syntax = "proto3";
package boat.v1;

// service CommentedOutService { rpc Nope(A) returns (B); }

message Alpha {
  string service = 1;  // a field named "service" must not confuse the parser
}

/* rpc AlsoCommented(A) returns (B); */
service AlphaService {
  rpc Plain(Alpha) returns (Alpha);
  rpc Server(Alpha) returns (stream Alpha);
  rpc Both(stream Alpha) returns (stream Alpha);
  rpc Client(stream Alpha) returns (Alpha);
}
"""

BETA_PROTO = """\
syntax = "proto3";
package boat.v1;
message Beta {}
"""

DOC_WITHOUT_MARKERS = """\
# Protobuf Definitions

- 99 proto files defining 99 gRPC services, 99 RPCs in total:

| File | Service | RPCs |
|---|---|---|
| `stale.proto` | StaleService | 99 |

## Service-to-Method Map

### AlphaService (`alpha.proto`)

- `Wrong`

### Beta-only prose stays here.
"""


@pytest.fixture()
def proto_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "proto"
    directory.mkdir()
    (directory / "alpha.proto").write_text(ALPHA_PROTO, encoding="utf-8")
    (directory / "beta.proto").write_text(BETA_PROTO, encoding="utf-8")
    return directory


def test_parses_services_rpcs_and_streaming_kinds(proto_dir: Path) -> None:
    files = parse_protos(proto_dir)
    assert [f.name for f in files] == ["alpha.proto", "beta.proto"]
    alpha = files[0].services[0]
    assert alpha.name == "AlphaService"
    assert [(r.name, r.kind) for r in alpha.rpcs] == [
        ("Plain", None),
        ("Server", "server streaming"),
        ("Both", "bidirectional streaming"),
        ("Client", "client streaming"),
    ]
    # The commented-out service/rpc and the message field named `service` are ignored.
    assert files[1].services == ()


def test_summary_counts_and_marks_service_less_files(proto_dir: Path) -> None:
    summary = render_summary(parse_protos(proto_dir))
    assert summary.splitlines()[0] == "- 2 proto files defining 1 gRPC services, 4 RPCs in total:"
    assert "| `alpha.proto` | AlphaService | 4 |" in summary
    assert "| `beta.proto` | — | — |" in summary


def test_rpcs_render_annotates_streaming(proto_dir: Path) -> None:
    service = parse_protos(proto_dir)[0].services[0]
    assert render_rpcs(service).splitlines() == [
        "- `Plain`",
        "- `Server` (server streaming)",
        "- `Both` (bidirectional streaming)",
        "- `Client` (client streaming)",
    ]


def test_init_inserts_markers_and_generation_rewrites_content(proto_dir: Path) -> None:
    generated = generate(DOC_WITHOUT_MARKERS, parse_protos(proto_dir), init=True)
    # Summary block replaced.
    assert "- 2 proto files defining 1 gRPC services, 4 RPCs in total:" in generated
    assert "StaleService" not in generated
    # Marker blocks present and, in the docs file convention, closed.
    assert generated.count("<!-- BEGIN GENERATED:") == 2
    assert generated.count("<!-- END GENERATED:") == 2
    # Prose outside the markers is untouched.
    assert "### Beta-only prose stays here." in generated
    # A second init is a no-op, and a plain run of the generated doc is stable.
    assert generate(generated, parse_protos(proto_dir), init=True) == generated
    assert generate(generated, parse_protos(proto_dir)) == generated


def test_missing_marker_for_service_fails(proto_dir: Path) -> None:
    doc = """\
<!-- BEGIN GENERATED: proto-summary -->
stale
<!-- END GENERATED: proto-summary -->
"""
    with pytest.raises(GenError, match="rpcs-AlphaService"):
        generate(doc, parse_protos(proto_dir))


def test_stale_marker_fails(proto_dir: Path) -> None:
    doc = (
        "<!-- BEGIN GENERATED: proto-summary -->\nsummary\n<!-- END GENERATED: proto-summary -->\n"
        "<!-- BEGIN GENERATED: rpcs-AlphaService -->\n- `Plain`\n<!-- END GENERATED: rpcs-AlphaService -->\n"
        "<!-- BEGIN GENERATED: rpcs-GhostService -->\n- `Boo`\n<!-- END GENERATED: rpcs-GhostService -->\n"
    )
    with pytest.raises(GenError, match="GhostService"):
        generate(doc, parse_protos(proto_dir))


def test_init_requires_a_section_for_every_service(proto_dir: Path) -> None:
    doc = (
        "- 1 proto files defining 1 gRPC services, 1 RPCs in total:\n\n"
        "| File | Service | RPCs |\n|---|---|---|\n"
    )
    with pytest.raises(GenError, match="AlphaService"):
        insert_markers(doc, parse_protos(proto_dir))


def test_check_mode_reports_staleness(proto_dir: Path, tmp_path: Path, capsys) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(DOC_WITHOUT_MARKERS, encoding="utf-8")
    assert main(["--proto-dir", str(proto_dir), "--doc", str(doc), "--init"]) == 0
    assert main(["--proto-dir", str(proto_dir), "--doc", str(doc), "--check"]) == 0

    doc.write_text(doc.read_text(encoding="utf-8").replace("- 2 proto", "- 3 proto"), encoding="utf-8")
    assert main(["--proto-dir", str(proto_dir), "--doc", str(doc), "--check"]) == 1
