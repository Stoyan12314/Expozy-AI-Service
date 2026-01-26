# Sprint S03 — Architecture Design
**Period:** 15.12.2025 to 28.12.2025
**Sprint issue:** #5  
**Epic focus:** Design

## Sprint goal
Create the high-level architecture for the AI page generator service. Produce the first version of the template schema and validate the end-to-end flow on paper.

## Planned backlog items
- Architecture overview (components + responsibilities)
- Sequence diagram / flow
- Template schema v1 + example outputs
- Threat/security overview (validation/sanitization placement)

## Work performed (implementation / actions)
- Designed the system architecture around an async job pipeline:
  - Telegram entrypoint → orchestrator service → DB/job state → queue → worker → AI provider → schema validation → sanitization → bundle render → preview upload → user notification.
- Created the first architecture description with clear boundaries:
  - Orchestrator handles request intake and job lifecycle
  - Worker handles generation + validation + sanitization + bundle creation
  - Preview service/domain separated to reduce blast radius
- Created a first draft of the Template Schema v1 based on EXPOZY runtime conventions:
  - Defined sections/components representation, allowed attributes, and API binding strategy.
- Created an initial threat/security overview:
  - Identified main risks: XSS via generated HTML, prompt injection causing unsafe output, hallucinated endpoints, unsafe attributes.
  - Mapped mitigations to gates: schema validation first, sanitization second, preview isolation and CSP as containment.

## Deliverables completed
- Architecture overview (v1)
- Sequence/flow diagram (v1)
- Template schema v1 + 1–2 example template JSON outputs
- Security/threat overview (v1)

## Definition of Done check
- [ ] Architecture reviewed 
- [x] Schema v1 exists and is specific enough to constrain AI output
- [x] Interfaces defined at high level 

## Review: what changed and why
- Confirmed architecture must treat model output as untrusted input → deterministic validation layers are mandatory.
- Confirmed “render-or-fail” design: invalid schema output must be rejected early, not “best effort rendered”.

## Retrospective
### What went well
- Clear separation of responsibilities reduced complexity.
- Schema-first thinking makes implementation testable.

### What can be improved
- Define concrete API payloads and validation rules more precisely.
- Start a minimal prototype early to prove feasibility.

### Actions for next sprint
- Detailed design of APIs/payloads.
- Prototype pipeline locally (JSON → validate → sanitize → render).

## Evidence to attach (fill later)
- Key file(s): Architecture doc, diagram(s), schema v1
- Issue links: #5
- Commits: (add commit hashes)
