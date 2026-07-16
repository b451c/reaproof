---
description: Author a mutation-verified test suite for a REAPER subject from its source code (ReaProof U7)
---

You are executing the ReaProof Agent Test Authoring protocol.

Subject and mode: $ARGUMENTS
(first token = path to the subject or its source dir; optional second token
`auto` or `interactive`, default `interactive`).

1. Read `docs/AGENT_TEST_AUTHORING.md` COMPLETELY and follow it as a binding
   charter — including the §1 doctrine it imports.
2. Scaffold the manifest: `reaproof author <subject> --mode <mode>`.
3. Read the subject's source completely; fill the feature inventory.
4. In `interactive` mode, stop at every ⏸ checkpoint and ask the user with
   your question tool. In `auto` mode never ask: decide conservatively and
   record every decision as `"intent": "assumed"` with the assumption text.
5. Run the zero-code battery first (`reaproof test <subject>`), then write
   specs (each test file carries `COVERS = [...]`), mutation-verify each,
   run everything twice.
6. Finish with `reaproof features-report <manifest> --tests <dir>` green and
   a REPORT.md (feature table, assumptions section, honest untestable tail).
