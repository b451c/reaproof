# ReaProof - common tasks. Run `make help` for the list.
.DEFAULT_GOAL := help
PY := PYTHONPATH=src LC_ALL=en_US.UTF-8 LC_NUMERIC=C TZ=UTC python3

help:            ## list commands
	@grep -E '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | sort

doctor:          ## environment health (expect all checks present)
	@$(PY) -m reaproof.runner.cli doctor

test:            ## universal zero-code test of a plugin: make test PLUGIN=/path/to/x.clap
	@$(PY) -m reaproof.runner.cli test "$(PLUGIN)" $(ARGS)

selftest:        ## the platform's own fast suite (no REAPER needed)
	@$(PY) -m pytest tests/ -m "not reaper and not slow" -q

selftest-full:   ## the full self-test suite (launches REAPER; slower)
	@$(PY) -m pytest tests/ -q
