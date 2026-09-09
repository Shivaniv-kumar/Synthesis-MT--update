.PHONY: help up down build logs shell-api shell-db migrate seed test evals prod-up prod-down clean

COMPOSE      = docker compose
COMPOSE_PROD = docker compose -f docker-compose.yml -f infra/docker-compose.prod.yml

help:
	@echo ""
	@echo "  Meeting Action Tracker — Docker shortcuts"
	@echo ""
	@echo "  Development"
	@echo "    make up          Start full dev stack (hot-reload enabled)"
	@echo "    make down        Stop and remove containers"
	@echo "    make build       Rebuild images (after dependency changes)"
	@echo "    make logs        Tail all service logs"
	@echo "    make logs s=api  Tail a specific service (e.g. api, worker, beat)"
	@echo "    make shell-api   Open a shell inside the api container"
	@echo "    make shell-db    Open a psql prompt in the postgres container"
	@echo "    make migrate     Run alembic upgrade head"
	@echo "    make seed        Create the first admin user (interactive)"
	@echo "    make test        Run backend pytest suite"
	@echo "    make evals       Run the extraction eval suite"
	@echo ""
	@echo "  Production"
	@echo "    make prod-up     Start production stack"
	@echo "    make prod-down   Stop production stack"
	@echo ""
	@echo "  Cleanup"
	@echo "    make clean       Remove all containers, volumes, and images"
	@echo ""

up:
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  Services running:"
	@echo "    API        → http://localhost:8000"
	@echo "    Docs       → http://localhost:8000/docs"
	@echo "    Frontend   → http://localhost:5173"
	@echo "    MinIO      → http://localhost:9001  (minioadmin / minioadmin)"
	@echo "    Postgres   → localhost:5432  (tracker / trackerpass)"
	@echo ""

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build --no-cache

logs:
ifdef s
	$(COMPOSE) logs -f $(s)
else
	$(COMPOSE) logs -f
endif

shell-api:
	$(COMPOSE) exec api /bin/bash

shell-db:
	$(COMPOSE) exec postgres psql -U tracker -d meeting_tracker

migrate:
	$(COMPOSE) exec api alembic upgrade head

seed:
	$(COMPOSE) exec api python -m scripts.seed

test:
	$(COMPOSE) exec api pytest tests/ -v

evals:
	$(COMPOSE) exec api python evals/run.py --suite all

prod-up:
	$(COMPOSE_PROD) up --build -d

prod-down:
	$(COMPOSE_PROD) down

clean:
	$(COMPOSE) down -v --rmi all --remove-orphans
