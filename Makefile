# Makefile for cofoundaz-api

.PHONY: help install dev-install run test test-cov e2e lint format clean docker-build docker-up docker-down migrate shell \
	prod-up prod-down prod-logs prod-migrate prod-ps prod-config ci-local docker-scan lint-actions \
	quality scan sbom \
	env-generate-key env-encrypt-staging env-encrypt-production env-decrypt-staging \
	env-decrypt-production env-verify env-diff

help:
	@echo "Available commands:"
	@echo "  make install       - Install production dependencies"
	@echo "  make dev-install   - Install all dependencies including dev"
	@echo "  make run           - Run development server"
	@echo "  make test          - Run tests"
	@echo "  make test-cov      - Run tests with coverage"
	@echo "  make lint          - Run linting"
	@echo "  make format        - Format code"
	@echo "  make clean         - Clean up generated files"
	@echo "  make docker-build  - Build Docker image"
	@echo "  make docker-up     - Start Docker containers"
	@echo "  make docker-down   - Stop Docker containers"
	@echo "  make migrate       - Run database migrations"
	@echo "  make shell         - Activate poetry shell"
	@echo ""
	@echo "Production stack (docker-compose.prod.yml; local runs use .env.production):"
	@echo "  make prod-config   - Render and validate the production compose file"
	@echo "  make prod-up       - Start the production stack (migrations run first)"
	@echo "  make prod-ps       - Show production container status + health"
	@echo "  make prod-logs     - Tail production logs"
	@echo "  make prod-migrate  - Run alembic upgrade head in the prod stack"
	@echo "  make prod-down     - Stop the production stack (volumes PRESERVED)"
	@echo ""
	@echo "Environment encryption (scripts/env.sh - see docs/deployment/ENV_ENCRYPTION.md):"
	@echo "  make env-generate-key       - Generate a new AES key"
	@echo "  make env-encrypt-staging    - .env.staging    -> .env.staging.enc    (commit the .enc)"
	@echo "  make env-encrypt-production - .env.production -> .env.production.enc (commit the .enc)"
	@echo "  make env-decrypt-staging    - .env.staging.enc    -> .env.staging"
	@echo "  make env-decrypt-production - .env.production.enc -> .env.production"
	@echo "  make env-verify             - Verify BOTH .enc files decrypt (writes nothing)"
	@echo "  make env-diff               - Which vars differ between envs (values masked)"
	@echo ""
	@echo "CI / security:"
	@echo "  make ci-local      - Run the whole CI pipeline locally"
	@echo "  make lint-actions  - Lint the GitHub Actions workflows (actionlint)"
	@echo "  make quality       - Quality gates: pylint, radon report, hadolint"
	@echo "  make scan          - All security scanners: bandit, semgrep, gitleaks,"
	@echo "                       checkov, trivy fs/config/image"
	@echo "  make sbom          - Generate a CycloneDX SBOM from the built image"
	@echo "  make docker-scan   - Build the image and scan it with Trivy"

install:
	poetry install --only main

dev-install:
	poetry install
	poetry run pre-commit install

run:
	poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	poetry run pytest

test-cov:
	poetry run pytest --cov=app --cov-report=html --cov-report=term

e2e:
	./scripts/e2e_run.sh

lint:
	poetry run black --check app tests
	poetry run isort --check-only app tests
	poetry run ruff check app tests
	poetry run mypy app

format:
	poetry run black app tests
	poetry run isort app tests
	poetry run ruff check --fix app tests

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov/ .mypy_cache/ .ruff_cache/

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

migrate:
	poetry run alembic upgrade head

migrate-create:
	@read -p "Enter migration message: " msg; \
	poetry run alembic revision --autogenerate -m "$$msg"

shell:
	poetry shell


# =============================================================================
# Production stack
# =============================================================================
# NAMING, because it trips people up:
#   On the VPS the environment file is `.env`. That is what CD writes to
#   $$DEPLOY_PATH and what docker-compose.prod.yml reads by default.
#   LOCALLY `.env` is already taken by the DEV stack (docker-compose.yml), so
#   these targets run the prod stack against `.env.production` instead, via
#   STACK_ENV_FILE. Nothing here ever reads or overwrites the dev `.env`.
#
# Get `.env.production` with:  ./scripts/env.sh decrypt production
#
# PROD_COMPOSE is defined once so the -f/--env-file pair can never drift between
# targets -- running one of these against the DEV compose file by accident is
# exactly the kind of mistake that wipes a database.
PROD_ENV_FILE = .env.production
PROD_COMPOSE = STACK_ENV_FILE=$(PROD_ENV_FILE) docker compose -f docker-compose.prod.yml --env-file $(PROD_ENV_FILE)

$(PROD_ENV_FILE):
	@echo "ERROR: $(PROD_ENV_FILE) not found."
	@echo ""
	@echo "  If the encrypted file exists in git:"
	@echo "      ./scripts/env.sh decrypt production"
	@echo ""
	@echo "  If you are setting this up for the first time:"
	@echo "      cp .env.production.example $(PROD_ENV_FILE)"
	@echo "      chmod 600 $(PROD_ENV_FILE)   # then fill in every CHANGE_ME"
	@echo "      ./scripts/env.sh encrypt production"
	@exit 1

prod-config: $(PROD_ENV_FILE)
	$(PROD_COMPOSE) config

prod-up: $(PROD_ENV_FILE)
	$(PROD_COMPOSE) up -d
	@echo "Waiting for readiness (DB + Redis)..."
	@for i in $$(seq 1 40); do \
		if curl -fsS --max-time 5 http://127.0.0.1:$$(grep -E "^API_PORT=" $(PROD_ENV_FILE) | cut -d= -f2 || echo 8000)/api/v1/health/ready >/dev/null 2>&1; then \
			echo "READY:"; curl -s http://127.0.0.1:$$(grep -E "^API_PORT=" $(PROD_ENV_FILE) | cut -d= -f2 || echo 8000)/api/v1/health/ready; echo; exit 0; \
		fi; sleep 3; \
	done; \
	echo "NOT READY after ~120s -- last 60 log lines:"; $(PROD_COMPOSE) logs --tail=60 api; exit 1

prod-ps: $(PROD_ENV_FILE)
	$(PROD_COMPOSE) ps

prod-logs: $(PROD_ENV_FILE)
	$(PROD_COMPOSE) logs -f --tail=100

prod-migrate: $(PROD_ENV_FILE)
	$(PROD_COMPOSE) up --no-build --exit-code-from migrate migrate

# NOTE: no `-v`. Named volumes hold the Postgres data directory; `down -v` on a
# production host is unrecoverable data loss. Remove volumes by hand, on purpose,
# after taking a backup.
prod-down: $(PROD_ENV_FILE)
	$(PROD_COMPOSE) down

# =============================================================================
# CI / security
# =============================================================================

# Mirrors .github/workflows/ci.yml so a red pipeline can be reproduced locally
# instead of debugged by pushing commits. Requires the dev db+redis to be up
# (`make docker-up`) for the test and e2e stages.
ci-local:
	@echo "==> [1/6] lint"
	poetry run black --check app tests
	poetry run isort --check-only app tests
	poetry run ruff check app tests
	poetry run mypy app
	@echo "==> [2/6] lockfile"
	poetry check --lock
	@echo "==> [3/6] unit tests + coverage gate"
	poetry run pytest --cov=app --cov-report=term-missing --cov-fail-under=95
	@echo "==> [4/6] migrations"
	@HEADS=$$(poetry run alembic heads | grep -c '(head)'); \
		echo "alembic heads: $$HEADS"; \
		if [ "$$HEADS" -ne 1 ]; then echo "ERROR: expected exactly 1 head"; exit 1; fi
	poetry run alembic upgrade head
	poetry run alembic check
	@echo "==> [5/6] e2e"
	./scripts/e2e_run.sh
	@echo "==> [6/6] image build + smoke"
	docker build -t cofoundaz-api:ci-local \
		--build-arg GIT_SHA=$$(git rev-parse --short HEAD) \
		--build-arg BUILD_DATE=$$(date -u +%Y-%m-%dT%H:%M:%SZ) .
	docker run --rm -e SECRET_KEY=ci -e DATABASE_URL=postgresql://u:p@h:5432/d \
		-e FIRST_SUPERUSER_EMAIL=a@b.com -e FIRST_SUPERUSER_PASSWORD=x -e LOG_FILE_PATH= \
		cofoundaz-api:ci-local python -c "import app.main; print('image smoke OK')"
	@echo "==> CI-local complete"

lint-actions:
	actionlint

# Build and scan the production image exactly as the CI `build` job does.
# Honours .trivyignore, so a finding here is one that would also fail CI.
docker-scan:
	docker build -t cofoundaz-api:scan \
		--build-arg GIT_SHA=$$(git rev-parse --short HEAD) \
		--build-arg BUILD_DATE=$$(date -u +%Y-%m-%dT%H:%M:%SZ) .
	@command -v trivy >/dev/null 2>&1 || { echo "trivy not installed: brew install trivy"; exit 1; }
	trivy image --severity HIGH,CRITICAL --ignore-unfixed \
		--ignorefile .trivyignore --exit-code 1 cofoundaz-api:scan


# =============================================================================
# Quality & security scanning
# =============================================================================
# These reproduce the CI `quality`, `security`, `trivy-repo` and `build` scan
# steps locally, so a red pipeline can be debugged here instead of by pushing
# commits and waiting.
#
# .worktrees is skipped throughout: it holds gitignored git worktrees for other
# branches, which a CI checkout never contains. Scanning them locally reports
# findings against OTHER branches' files and does not match what CI sees.
TRIVY_SKIP = --skip-dirs .worktrees --skip-dirs htmlcov

quality:
	@echo "==> pylint (gate: --fail-under=9.5; current tree scores 9.94)"
	poetry run pylint app --fail-under=9.5
	@echo "==> complexity & maintainability (REPORT ONLY -- ruff C901 is the gate)"
	poetry run radon cc app -n B -s --total-average
	@poetry run radon mi app -n B; echo "   (no rows above = every module rated A)"
	@echo "==> hadolint (Dockerfile)"
	@command -v hadolint >/dev/null 2>&1 || { echo "hadolint not installed: brew install hadolint"; exit 1; }
	hadolint Dockerfile
	@echo "==> quality OK"

sbom:
	docker build -t cofoundaz-api:sbom \
		--build-arg GIT_SHA=$$(git rev-parse --short HEAD) \
		--build-arg BUILD_DATE=$$(date -u +%Y-%m-%dT%H:%M:%SZ) .
	@command -v trivy >/dev/null 2>&1 || { echo "trivy not installed: brew install trivy"; exit 1; }
	trivy image --format cyclonedx --output sbom.cdx.json --quiet cofoundaz-api:sbom
	@echo "==> wrote sbom.cdx.json ($$(python3 -c "import json;print(len(json.load(open('sbom.cdx.json')).get('components',[])))") components)"

# Mirrors every scanner CI runs. Blocking gates are marked; report-only steps
# print their findings and continue, exactly as in the pipeline.
scan:
	@echo "==> [1/7] bandit -- Python SAST (BLOCKS in CI)"
	poetry run bandit -r app/ --quiet
	@echo "==> [2/7] semgrep -- source patterns (BLOCKS in CI)"
	@command -v semgrep >/dev/null 2>&1 || { echo "semgrep not installed: brew install semgrep"; exit 1; }
	semgrep scan --config p/python --config p/security-audit --config p/owasp-top-ten \
		--config p/jwt --config p/secrets --severity ERROR --error --metrics off --quiet app/
	@echo "==> [3/7] gitleaks -- committed secrets, full history (BLOCKS in CI)"
	docker run --rm -v "$$PWD:/repo" ghcr.io/gitleaks/gitleaks:v8.30.1 \
		detect --source /repo --redact --no-banner
	@echo "==> [4/7] trivy config -- IaC misconfiguration (BLOCKS in CI)"
	@command -v trivy >/dev/null 2>&1 || { echo "trivy not installed: brew install trivy"; exit 1; }
	trivy config --quiet --severity MEDIUM,HIGH,CRITICAL --exit-code 1 $(TRIVY_SKIP) .
	@echo "==> [5/7] checkov -- GitHub Actions workflow policy (BLOCKS in CI)"
	@command -v checkov >/dev/null 2>&1 || { echo "checkov not installed: pipx install checkov"; exit 1; }
	@# Scoped to .github ONLY. Checkov has no docker_compose framework, so pointing
	@# it at the compose files produces literally no results -- see the Checkov
	@# section in docs/deployment/DEPLOYMENT_GUIDE.md.
	checkov --directory .github --framework github_actions --compact --quiet --output cli
	@echo "==> [6/7] trivy fs -- lockfile CVEs + secrets (REPORT ONLY in CI)"
	-trivy fs --scanners vuln,secret --severity HIGH,CRITICAL --quiet $(TRIVY_SKIP) .
	@echo "==> [7/7] trivy image -- image CVEs (BLOCKS in CI)"
	docker build -q -t cofoundaz-api:scan \
		--build-arg GIT_SHA=$$(git rev-parse --short HEAD) . >/dev/null
	trivy image --severity HIGH,CRITICAL --ignore-unfixed \
		--ignorefile .trivyignore --exit-code 1 --quiet cofoundaz-api:scan
	@echo "==> scan complete"


# =============================================================================
# Environment encryption
# =============================================================================
# Thin wrappers over scripts/env.sh so the common operations are discoverable
# from `make help`. The script is the real interface; see
# docs/deployment/ENV_ENCRYPTION.md for the full workflow.
#
# Reminder on filenames: `.env.staging` / `.env.production` are LOCAL plaintext
# working copies (gitignored). `.env.<env>.enc` is ciphertext and IS committed.
# `.env` is what lands on the VPS. `.env.key` holds the AES key and is
# gitignored -- committing it next to the .enc files would defeat the whole
# exercise.

env-generate-key:
	./scripts/env.sh generate-key

env-encrypt-staging:
	./scripts/env.sh encrypt staging

env-encrypt-production:
	./scripts/env.sh encrypt production

env-decrypt-staging:
	./scripts/env.sh decrypt staging

env-decrypt-production:
	./scripts/env.sh decrypt production

# Verifies both environments in one go. Writes no plaintext, so it is safe to
# run anywhere -- including as a pre-push sanity check that the committed
# ciphertext still matches the key you hold.
env-verify:
	./scripts/env.sh verify staging
	./scripts/env.sh verify production

env-diff:
	./scripts/env.sh diff
