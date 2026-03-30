# Project Plan - BoAt Platform

## Vision

Build an open-source, production-grade automotive simulation and testing platform for deterministic, high-throughput validation across software-in-the-loop, hardware-in-the-loop, and CI/CD pipelines.

## Stakeholders

- OEM engineers
- Tier-1 suppliers
- Open-source community contributors
- CI/CD automation consumers

## Milestones

- **M0 Scaffold:** Repository structure, baseline docs, architecture decisions
- **M1 Core Sim Engine:** Tick scheduler, signal router, deterministic execution kernel
- **M2 API Gateway:** gRPC services, control plane, streaming interfaces
- **M3 Plugin SDK:** Stable C ABI, C++ SDK, Python bindings, sample plugins
- **M4 Observability:** Event/trace persistence, metrics, dashboard integration
- **M5 HIL:** HAL layer, virtual and physical interface bridges
- **M6 GA:** Hardening, packaging, documentation completion, public release

## Timeline

The roadmap spans 18 months with 6 milestones, organized in 3-month sprint blocks.

| Period | Milestone | Primary Outcomes |
|---|---|---|
| Months 1-3 | M0 Scaffold | Foundational architecture, plans, and standards |
| Months 4-6 | M1 Core Sim Engine | Deterministic simulation runtime and core modules |
| Months 7-9 | M2 API Gateway | End-to-end control API and streaming |
| Months 10-12 | M3 Plugin SDK | Extensibility model and SDK support |
| Months 13-15 | M4 Observability | Trace, metrics, replay-ready observability |
| Months 16-18 | M5 HIL + M6 GA | Hardware integration, stabilization, release |

## Team Roles

- Project Manager
- Lead Developer
- C++/Python Developer
- Backend Developer
- AI Engineer
- DevOps Engineer
- Test Manager
- Test Engineer
- Requirement Engineer
- UX/UI Designer

## Governance

- Work management via GitHub Issues and Milestones
- RFC process required for breaking changes
- Semantic versioning for all release artifacts and APIs

## Definition of Done

- All acceptance criteria met
- CI pipeline green
- Documentation updated
- Changes peer-reviewed and approved

## Role-Based Task List

| Role | Deliverables | Dependencies |
|---|---|---|
| Project Manager | Projectplan.md, sprint plans, milestone tracking | All roles |
| Requirement Engineer | Technicalplan.md requirements sections, epics.md | Stakeholder interviews |
| Lead Developer | system-architecture.md, module-structure.md | Requirements |
| C++/Python Developer | Core sim engine, plugin SDK, Python bindings | Architecture |
| Backend Developer | gRPC gateway, event store, pipeline | API spec, DB design |
| AI Engineer | LLM-assisted test generation, anomaly detection, llm-cost-control.md | Backend services |
| DevOps Engineer | ci-cd.md, deployment-plan.md, Docker images | Build system |
| Test Manager | test-strategy.md, test-plan.md | Architecture, API |
| Test Engineer | Unit, integration, and HIL test suites | Test strategy |
| UX/UI Designer | ux-concepts.md, user-flows.md, CLI/dashboard wireframes | Epics |

