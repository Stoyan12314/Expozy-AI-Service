# Sprint S04 — Detailed Design & Prototyping
**Period:** 29.12.2025 to 11.01.2026
**Sprint issue:** #6  
**Epic focus:** Design + Implementation (prototype)

## Sprint goal
Turn the architecture into design decisions and prove feasibility with small prototypes (schema, prompt, parsing, rendering, sanitization).

## Planned backlog items
- Detailed API design (endpoints + payloads)
- Prompt experiments + structured output strategy
- Minimal prototype: AI → JSON → validate → sanitize → render → preview (local)
- Validation rules documented

## Work performed (implementation / actions)
- Specified the detailed contracts needed for implementation:
  - Job request payload and job status model
  - Worker input (queue message format)
  - Worker output (bundle metadata + preview reference)
- Ran initial prompt experiments:
  - Tested whether candidate models can produce schema-valid JSON reliably
  - Explored “structured output / response schema” approach vs prompt-only JSON
- Built/prototyped a minimal local flow (even if partial):
  - Parse JSON → validate against schema → apply sanitization rules → render to HTML bundle skeleton → preview locally.
- Documented initial validation rules:
  - Allow-lists for tags/attributes
  - Restrictions around script injection and dangerous attributes
  - Handling of Alpine/Tailwind conventions

## Deliverables completed
- Detailed API design notes (v1)
- Prompt/structured output strategy notes (v1)
- Minimal prototype evidence (local feasibility) (v1)
- Validation rules document (v1)

## Definition of Done check
- [ ] Minimal prototype generates and displays simple page locally 
- [ ] Validation + sanitization block unsafe output in test cases 
- [x] Design decisions documented (why schema, why flow)

## Review: what changed and why
- Confirmed that structured outputs substantially reduce failure rate and operational complexity compared to prompt-only JSON.
- Identified that validation rules must be explicit and tested (not “best effort”).

## Retrospective
### What went well
- Prototype work de-risked the design.
- Design artefacts now directly map to implementation tasks.

### What can be improved
- Add unit/schema tests early to avoid regressions.
- Improve traceability: link issues to commits consistently.

### Actions for next sprint
- Start implementation of core pipeline in repo (orchestrator + worker skeleton).
- Add schema validation tests + sanitizer tests.
- Begin CI setup plan (even if CI implemented later).

## Evidence to attach (fill later)
- Key file(s): API design, prompt experiments, validation rules, prototype code
- Issue links: 
- Commits: 
