SEED_DIR ?= seed
CASE_ID ?= CEDX-0000

PYTHON := python3
PYTHONPATH := src:lib

.PHONY: demo verify trace eval replay probe-approval probe-agent-failure probe-budget \
        probe-append-only probe-idempotency probe-crash clean

demo:
	PYTHONPATH=$(PYTHONPATH) REPLAY_LLM=true SEED_DIR=$(SEED_DIR) CASE_ID=$(CASE_ID) \
		$(PYTHON) scripts/run_pipeline.py

verify:
	$(PYTHON) verify_audit.py --audit out/audit.json --transcripts transcripts --schema audit.schema.json

trace:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/trace.py --id $(ID)

eval:
	PYTHONPATH=$(PYTHONPATH) REPLAY_LLM=true $(PYTHON) scripts/eval_harness.py 2>&1; \
		echo "---"; \
		PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests/ -v --tb=short 2>&1 | tail -40

replay:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/replay.py --id $(ID)

probe-approval:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/probe_approval.py

probe-agent-failure:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/probe_agent_failure.py

probe-budget:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/probe_budget.py

probe-append-only:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/probe_append_only.py

probe-idempotency:
	PYTHONPATH=$(PYTHONPATH) REPLAY_LLM=true SEED_DIR=$(SEED_DIR) $(PYTHON) scripts/probe_idempotency.py

probe-crash:
	PYTHONPATH=$(PYTHONPATH) REPLAY_LLM=true SEED_DIR=$(SEED_DIR) $(PYTHON) scripts/probe_crash.py

clean:
	rm -rf out transcripts
