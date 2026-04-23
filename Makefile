.PHONY: setup test lint format typecheck run dashboard \
        infra-up infra-down demo-up demo-down clean \
        docker-build docker-up docker-down api-health

setup:
	poetry install

test:
	poetry run pytest tests/unit/ -v

test-integration:
	poetry run pytest tests/integration/ -v

lint:
	poetry run ruff check . --fix
	poetry run ruff format .

format:
	poetry run ruff format .

typecheck:
	poetry run mypy src/

run:
	# Host-mode Kafka hits the broker via its OUTSIDE advertised listener
	# (localhost:29092). Inside Docker Compose, the container env overrides
	# this to kafka:9092. See docker-compose.yml KAFKA_ADVERTISED_LISTENERS.
	KAFKA_BOOTSTRAP_SERVERS=localhost:29092 \
	poetry run uvicorn src.serving.api:app --host 0.0.0.0 --port 8000 --reload

dashboard:
	poetry run streamlit run src/serving/dashboard.py --server.port 8501

infra-up:
	bash scripts/start_infrastructure.sh

infra-down:
	bash scripts/stop_infrastructure.sh

demo-up:
	docker compose -f demo_app/docker-compose.demo.yml up -d

demo-down:
	docker compose -f demo_app/docker-compose.demo.yml down

# ── Serving (Docker) ────────────────────────────────────────────────────────
docker-build:
	docker compose build opsagent-api

docker-up:
	docker compose up -d opsagent-api opsagent-dashboard

docker-down:
	docker compose rm -sf opsagent-api opsagent-dashboard

api-health:
	@curl -s http://localhost:8000/health | python -m json.tool

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
