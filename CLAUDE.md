# ReaProof - notes for Claude Code

This is a no-false-results testing platform for REAPER plugins, scripts,
extensions and themes. Full agent instructions live in `AGENTS.md` and the
test-authoring protocol in `docs/AGENT_TEST_AUTHORING.md` - read those first
for any testing task.

Quick facts:
- Zero-code batteries: `reaproof test <subject>` (.clap/.vst3/.vst/.lv2/
  .component/.jsfx/.lua/.eel/.py/reaper_*.dylib/.ReaperThemeZip),
  `reaproof test-repo <dir>` (ReaPack repos incl. .jsfx packages).
  Instrument subjects get a deterministic MIDI note feed; JSFX get a compile
  proof, import/filename installation, serialize + factory-reset checks;
  scripts get the gmem-aware hygiene differ (--expect-gmem to declare).
- Agent authoring: `/reaproof-author <path> [auto|interactive]` (project
  command, ships with this repo) or follow the charter manually.
- Verify like the platform does: every assertion must prove it can fail
  (mutation checks), REAPER-launching tests run twice, honest skips over
  fake green. `reaproof features-report` refuses "covered" without tests.
- Suite: `PYTHONPATH=src LC_ALL=en_US.UTF-8 LC_NUMERIC=C TZ=UTC pytest
  tests/ -m "not reaper and not slow" -q` (fast); full suite needs a
  provisioned/pointed REAPER (`REAPROOF_REAPER_APP`) on macOS.
