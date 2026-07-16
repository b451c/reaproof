# ReaProof — one-line commands. The deterministic locale is required for the gates.
ENV := PYTHONPATH=src LC_ALL=en_US.UTF-8 LC_NUMERIC=C TZ=UTC

.PHONY: open close test fast doctor report gates help
help:           ## list commands
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ -/'



test:           ## full suite (REAPER + units)
	@$(ENV) python3 -m pytest tests/ -q

fast:           ## fast suite only (no REAPER) — units, analysis, plugin, coverage
	@$(ENV) python3 -m pytest tests/ -m "not reaper" -q

gates:          ## run with the full enforcement: report + mutation-check + repeat=2
	@$(ENV) python3 -m pytest tests/ -m gate --reaproof-report=.cache/runs/report \
		--mutation-check --reaproof-repeat=2 -q

doctor:         ## environment health (expect all checks ✓)
	@$(ENV) python3 -m reaproof.runner.cli doctor

report:         ## open the latest HTML report
	@open .cache/runs/report/report.html 2>/dev/null || echo "run 'make gates' first"
