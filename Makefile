.PHONY: install dev test ui run up down train evaluate card desktop-linux
install:        ; pip install -r requirements.txt
dev:            ; pip install -r requirements-dev.txt
test:           ; pytest --cov=waferguard
ui:             ; cd frontend && npm install --no-audit --no-fund && npx vite build
run:            ; ./scripts/run_local.sh
up:             ; docker compose up -d
down:           ; docker compose down
train:          ; python -m waferguard.training.train --config deploy/configs/train.yaml
evaluate:       ; python scripts/evaluate.py --model-dir models/wafer-ensemble --data-root dataset --split test
card:           ; python scripts/make_model_card.py
desktop-linux:  ; ./scripts/build_desktop_linux.sh
