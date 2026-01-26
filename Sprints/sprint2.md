# Sprint S02 — Research & Analysis
**Period:** 1.12.2025 to 14.12.2025
**Sprint issue:** #4  
**Epic focus:** Advice + Analysis

## Sprint goal
Literature review on AI generation, market research on existing tools, document research findings, define research variables and evaluation criteria.

## Planned backlog items
- Collect and summarize sources on LLM-based UI/code generation
- Compare platforms/providers (cost, reliability, latency, governance)
- Define evaluation criteria and variables 
- Draft a recommendation for platform + model

## Work performed (implementation / actions)
- Conducted a literature review around:
  - NL→UI/code generation benchmarks and findings (execution-based evaluation focus)
  - Hybrid architectures: LLM generation + deterministic validation/sanitization
  - Structured outputs / JSON Schema compliance and constrained decoding concepts
  - Hallucination mitigation approaches (RAG, verification, evaluators)
  - Cloud platform constraints: cost runway, credits, EU region availability, compliance posture
- Drafted a criteria-based evaluation approach aligned with a multi-phase selection framework (requirements → filtering → evaluation → decision).
- Documented measurable criteria that matter for EXPOZY:
  - Task-specific front-end generation quality (proxy via JSON deployability)
  - Structured output reliability (schema-valid JSON)
  - Cost and startup runway
  - Hallucination risk and mitigation plan
  - Security + GDPR constraints
  - Latency + context window
  - Few-shot / instruction-following reliability
- Produced an initial recommendation direction (platform/provider) based on the startup constraint and governance requirements.

## Deliverables completed
- Literature review summary 
- Evaluation criteria documented 
- Draft platform/model recommendation section

## Definition of Done check
- [x] 8–12 credible sources collected and summarized (in research references/notes)
- [x] Recommendation section drafted (provider/model and why)
- [x] Evaluation criteria documented clearly for later filtering/evaluation
- [x] Backlog updated with research-driven decisions/risks (at least initial mapping)

## Review: what changed and why
- Shifted from “pick best model overall” to “pick best model for EXPOZY pipeline constraints”.
- Confirmed that structured outputs / schema enforcement is a non-negotiable gate in the pipeline.

## Retrospective
### What went well
- Clear criteria reduced ambiguity and future rework.
- Research directly supported architectural decisions (schema + validation layers).

### What can be improved
- Reduce reliance on community post channels like reddit , prefer primary docs/papers where possible.
- Start capturing experiment logs earlier.

### Actions for next sprint
- Convert research into architecture artefacts: C4 diagrams, sequence diagram, schema v1.
- Formalize risk mitigations (validation, sanitization, preview isolation).

## Evidence to attach (fill later)
- Key file(s): 
- Issue links: #4
- Commits: (add commit hashes)
