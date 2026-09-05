# ReconIQ Development Rules

- Use the applicable Superpowers workflow before work begins: brainstorming for new behavior, test-driven development for code changes, systematic debugging for failures, and verification before declaring a slice complete.
- Every implementation stage follows red-green-refactor: write and run a failing behavior test, make the smallest implementation pass it, then run the relevant suite.
- After tests pass, run a Ponytail review. Prefer existing code, the standard library, and the smallest readable solution; do not add speculative abstractions, dependencies, or placeholder infrastructure.
- Ask for explicit approval before every git commit. Do not commit untracked user files incidentally.
- Update `IMPLEMENTATION_LOG.md` after every implementation slice. Keep each entry crisp: status, changes, gate evidence, metrics, decisions beyond the plan, problems and likely cause, resolution, edge cases, and Claude checkpoint.
- Request a Claude review only at the review points required by the implementation plan, and record the outcome before proceeding.
- Use pytest, with tests written before implementation and one test module per pipeline module.
- Keep modules in their planned domain folders; do not introduce a generic utilities folder.
- Use typed Pydantic models at module boundaries. Do not pass bare dictionaries between modules.
- Stages 0-5 are deterministic. Stage 6 receives only unresolved records, uses temperature 0, structured output, and record-pair caching.
- Stage 0 must collapse all injected duplicates with no false collapses. Stage 1 must filter all non-transaction rows. Stage 2 must have 100% design-set precision.
- Stage 3 requires at least 95% precision, 90% recall, and no hard-negative false matches. Stage 4 requires at least 90% precision.
- Stage 5 must correctly net or specifically tag every refund record. Stage 6 must return a reason for every input and never auto-match below 0.5 confidence.
- Stage 7 must assign exactly one exception category to every unresolved record.
- Ground-truth fields remain outside matcher inputs and are used only for scoring.
- Persist at most one holdout run; after it exists, matching-rule or threshold changes make any later holdout execution a new evaluation, never the original untouched holdout.
- The synthetic generator is deterministic by seed and produces exactly 85 design and 35 holdout scenarios. Every case type requires at least 4 design and 2 holdout instances before matching work begins.
