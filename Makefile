.PHONY: dev test lint format doctor docker-up docker-down clean

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest

lint:
	ruff check .

format:
	ruff format .
	ruff check --fix .

doctor:
	python scripts/doctor.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down

clean:
	python -c "from pathlib import Path; import shutil; p=Path('generated'); [shutil.rmtree(x) for x in p.iterdir() if x.is_dir()]"
