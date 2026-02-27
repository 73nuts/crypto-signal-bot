.PHONY: setup test test-unit lint format docker-up docker-down db-init status logs

setup:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	@test -f .env.telegram || (cp .env.telegram.example .env.telegram && echo "Created .env.telegram from .env.telegram.example")
	pip install -e ".[dev]"

test:
	pytest tests/

test-unit:
	pytest tests/ --ignore=tests/integration --ignore=tests/system

lint:
	ruff check .

format:
	ruff format .

docker-up:
	docker-compose up -d mysql redis
	@echo "Waiting for MySQL to be healthy..."
	@until docker-compose ps mysql | grep -q "healthy"; do sleep 2; done
	docker-compose up -d swing scanner tg-bot tg-payment

docker-down:
	docker-compose down

db-init:
	$(eval MYSQL_ROOT_PASSWORD := $(shell grep MYSQL_ROOT_PASSWORD .env | cut -d= -f2))
	docker exec -i $$(docker-compose ps -q mysql) mysql -uroot -p$(MYSQL_ROOT_PASSWORD) crypto_signals < scripts/init.sql

status:
	python -m src.strategies.swing.scheduler --status

logs:
	docker-compose logs -f $(SERVICE)
