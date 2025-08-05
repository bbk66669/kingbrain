VENV         := .venv
ACT          := . $(VENV)/bin/activate &&
PIP          := $(VENV)/bin/pip
PY           := $(VENV)/bin/python

# 兼容老参数 → 新脚本参数的映射
MODE         ?= func
MAX          ?= 80
MIN          ?= 6
STEP         ?= 40

# 评测/嵌入实验参数
SIG_WEIGHTS  ?= 2,3,5
COMPARE_ANN  ?= 1

# 备份
BACKUP_PREFIX ?= CodeChunk/

.PHONY: init deps scan entries reach graph deadlist split visualize ingest ask eval backup restore check validate lock-hash bot-restart clean all

init:
	python3 -m venv $(VENV)
	$(ACT) $(PIP) install -r requirements.txt
	# spaCy 模型
	$(ACT) $(PY) -m spacy download en_core_web_sm

deps: init
	@true

scan: init
	$(ACT) $(PY) scripts/scan_full.py

entries: scan
	$(ACT) $(PY) scripts/find_entry_candidates.py

reach: entries
	$(ACT) $(PY) scripts/reach_live.py

graph: reach
	$(ACT) $(PY) scripts/gen_graph_active.py

deadlist: reach
	@head -n 10 dead_code.txt | nl || true

split: reach
	$(ACT) $(PY) scripts/split_by_ast.py --min $(MIN) --max-logic-lines $(MAX) --level function

visualize:
	$(ACT) $(PY) scripts/visualize_chunks.py --output $(PWD)/chunks_report.html
	@echo "HTML 报告生成：$(PWD)/chunks_report.html"

ingest: init split
	$(ACT) $(PY) scripts/ensure_weaviate_schema.py
	@if [ "$(COMPARE_ANN)" = "1" ]; then \
		$(ACT) $(PY) scripts/emb_ingest.py --mode $(MODE) --sig-weight-test $(SIG_WEIGHTS) --compare-annotation ; \
	else \
		$(ACT) $(PY) scripts/emb_ingest.py --mode $(MODE) --sig-weight-test $(SIG_WEIGHTS) ; \
	fi

ask:
	$(ACT) $(PY) scripts/ask_code.py "$(q)"

eval:
	$(ACT) $(PY) scripts/evaluate_qa.py --non-interactive
	@echo "评测结果：qa_eval.csv ；检索日志：search_log.csv"

backup:
	$(ACT) $(PY) scripts/backup_restore.py --backup

restore:
	$(ACT) $(PY) scripts/backup_restore.py --restore-prefix "$(prefix)"

check:
	$(ACT) $(PY) scripts/check_deps.py

# ——— 新增：Phase0 校验 & 锁 hash —————————————————————————

validate:
	$(ACT) ../.collab/tools/kb-tools check-lock ../.collab/spec.lock.yaml
	$(ACT) ../.collab/tools/kb-tools sbom-verify --format spdx-json@3.0 --path=../data/sbom/
	$(ACT) ../.collab/tools/kb-tools license-scan --policy=../configs/sbom-policy.yaml
	$(ACT) ../.collab/tools/kb-tools vuln-scan --critical-only
	$(ACT) ../.collab/tools/kb-tools kyverno-verify ../k8s/manifests
	$(ACT) ../.collab/tools/kb-tools lineage-validate ../.collab/schemas/openlineage/spec

lock-hash:
	$(ACT) ../.collab/tools/kb-tools digest-lock ../.collab/spec.lock.yaml

# ——— 其余目标 ————————————————————————————————————————

bot-restart:
	sudo systemctl restart kb-bot

clean:
	rm -rf \
		graphs/* \
		full_files.json \
		live_files.json \
		dead_code.txt \
		chunks.json \
		entry_candidates.txt \
		min_lines_stats.json \
		ingest_stats.json \
		qa_eval.csv \
		search_log.csv \
		chunks_report.html

all: split visualize
