.PHONY: setup test lint format typecheck run infra-up infra-down clean

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

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
