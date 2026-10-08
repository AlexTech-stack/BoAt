# backlog/

Longer-form analyses, gap assessments and incident write-ups — the material that is too
large or too discursive for an issue body.

**This directory is the evidence, not the queue.** Open work is tracked in
[GitHub Issues](https://github.com/AlexTech-stack/BoAt/issues); issues link here for the
detail behind them. If you want to know what is open, read the issues. If you want to know
*why* something is the way it is, or what was already tried, read the file.

Most of what is here is a record of work that is **done**. `nodes_backlog.md` and
`launcher_agent_backlog.md` in particular are mostly dated "Done" entries — development
logs, kept because several of them explain a non-obvious decision or a bug that was hard to
find. Status markers inside the files: ✅ resolved, 🔴 critical, 🟡 important, 🔵 minor,
🟢 cosmetic.

## What each file holds, and what tracks it now

| File | Subject | Open items now tracked as |
|---|---|---|
| `release_readiness_audit.md` | The end-to-end audit taken before dropping the WIP banner: 8 blockers, 11 should-fix items, and the findings N1–N20 added while fixing them. The single best starting point for understanding the project's current state and how it got here. | [#10](https://github.com/AlexTech-stack/BoAt/issues/10), [#13](https://github.com/AlexTech-stack/BoAt/issues/13), [#16](https://github.com/AlexTech-stack/BoAt/issues/16), [#17](https://github.com/AlexTech-stack/BoAt/issues/17), [#18](https://github.com/AlexTech-stack/BoAt/issues/18), [#25](https://github.com/AlexTech-stack/BoAt/issues/25), [#42](https://github.com/AlexTech-stack/BoAt/issues/42), [#43](https://github.com/AlexTech-stack/BoAt/issues/43) |
| `pdu_gap_analysis.md` | AUTOSAR divergences in PDU/COM handling, assessed against the specs. Marks what is deliberately out of scope, and corrects factual errors in its own first draft. | [#35](https://github.com/AlexTech-stack/BoAt/issues/35) (umbrella) |
| `trace_information_value.md` | The theory behind `boat trace score` — what makes a trace informative for reverse engineering, why more entropy is not monotonically better, and the estimator hygiene rules. Design rationale rather than a defect list. | "Open questions" section; nothing filed |
| `can_tp_plugin_backlog.md` | ISO-TP conformance review. 16 items, 11 resolved. | [#38](https://github.com/AlexTech-stack/BoAt/issues/38) |
| `tcp_plugin_backlog.md` | TCP plugin gaps against AUTOSAR and the RFCs, with the deliberate exclusions named (congestion control is excluded by design for lab use). | [#36](https://github.com/AlexTech-stack/BoAt/issues/36), [#37](https://github.com/AlexTech-stack/BoAt/issues/37) |
| `simulation_service_backlog.md` | Why `SimulationService` looks multi-simulation and is not, and how that degrades a long-lived gateway into one that cannot start simulations. | [#31](https://github.com/AlexTech-stack/BoAt/issues/31), [#32](https://github.com/AlexTech-stack/BoAt/issues/32) |
| `frame_service_backlog.md` | `SELF_SENT` semantics and the CAN extended-identifier round-trip limitation. Both found by a real bidirectional client. | [#33](https://github.com/AlexTech-stack/BoAt/issues/33), [#34](https://github.com/AlexTech-stack/BoAt/issues/34) |
| `gateway_backlog.md` | Operational gaps found running against real hardware. Both original items resolved; bus health visibility remains. | [#22](https://github.com/AlexTech-stack/BoAt/issues/22) |
| `python_sdk_backlog.md` | SDK review, 15 items. Six fixed in October 2026 (TLS, `python-can` declaration, `send_tcp`, `grpcio-tools`). | [#41](https://github.com/AlexTech-stack/BoAt/issues/41) |
| `com_signal_backlog.md` | Why the C++ COM signal packer was deleted rather than fixed, and what to do if one is needed again. | [#44](https://github.com/AlexTech-stack/BoAt/issues/44) |
| `test_runner_backlog.md` | `boat test run` development log, including the latency methodology and two real bugs the first end-to-end run found. | — |
| `nodes_backlog.md` | Node scripts and node management in `admin_gui`. Development log. | — |
| `launcher_agent_backlog.md` | Launcher agent and the PySide6 admin client. The longest file here, and almost entirely a development log — but it records several real hardware bugs and the reasoning behind the v1 scope cuts. | [#39](https://github.com/AlexTech-stack/BoAt/issues/39), [#40](https://github.com/AlexTech-stack/BoAt/issues/40) |

## Adding to this directory

A new file is right when the write-up is long enough that an issue body would bury the
argument — a gap analysis against a specification, an incident post-mortem, or a design
rationale someone will want in two years. Open a GitHub issue for the work itself and link
to the file from it.

If you are recording something that was *not obvious* — a wrong hypothesis that cost time, a
symptom whose label turned out to be misleading — write that down too. Several files here
are more useful for the dead ends they record than for the conclusions.
