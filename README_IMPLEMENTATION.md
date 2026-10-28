# AIStockScanner G.11-OPT-14D/14E patch bundle

Prepared from the six source files uploaded for this authorized implementation.

## Included changes
- Remove the duplicate ORM index declaration.
- Add reversible follow-up migration `e6f7a8b9c0d1` after `e5f6a7b8c9d0`.
- Validate async and sync test URL database names with the same helper and require both to target the same database name.
- Add the four missing G.11/option-chain tables to the per-test cleanup list.
- Add regression tests for model index uniqueness, migration operation shape, cleanup coverage, URL validation, and equal-date metadata tie-breaking.
- Extend the test-isolation migration-head assertion to recognize the planned follow-up revision.

## Not performed
- No pytest command was run.
- No database was connected to or modified by this patch preparation.
- No migration was applied.
- No Git operation, staging, commit, or push was performed.

## Apply locally
Copy the included files to their matching paths in the existing project, reviewing the diff first. Do not replace unrelated files. The migration is intentionally not applied; after reviewing and authorizing that separate step, verify the target database again immediately before migration execution.
