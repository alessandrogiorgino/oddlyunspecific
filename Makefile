.PHONY: help dev up down build logs sh migrate makemigrations superuser enroll \
	test check secrets freeze \
	deploy upgrade link-caddy prod-up prod-down prod-ps prod-logs prod-migrate \
	prod-superuser prod-enroll prod-shell prod-check prod-rotate-journal \
	backup restore clean

# Production stack. No public ports — the VPS shared Caddy fronts it.
PROD = docker compose -f docker-compose.prod.yml

# The VPS-wide Caddy that owns :80/:443 (the detoxy / back_to_me one).
SHARED_CADDY ?= back_to_me_caddy

# How long a reachability probe keeps retrying. `up -d` returns as soon as the
# container exists, but the app still has to run migrations before gunicorn
# listens — probing straight away just reports a connection refused.
WAIT ?= 90

help: ## List commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# --- Development -------------------------------------------------------------

dev: ## Build (if needed) and start the dev stack in the foreground
	docker compose up --build

up: ## Start the dev stack detached
	docker compose up -d

down: ## Stop the dev stack
	docker compose down

build: ## Rebuild images
	docker compose build

logs: ## Tail dev logs
	docker compose logs -f

sh: ## Shell into the dev web container
	docker compose exec web sh

migrate: ## Apply migrations (dev)
	docker compose exec web python manage.py migrate

makemigrations: ## Create migrations (dev) — commit the result
	docker compose exec web python manage.py makemigrations

superuser: ## Create an admin user (dev)
	docker compose exec web python manage.py createsuperuser

enroll: ## Print the TOTP enrolment QR (dev): make enroll USER=alessandro [ARGS=--reset]
	docker compose exec web python manage.py enroll_totp $(USER) $(ARGS)

test: ## Run the test suite (dev)
	docker compose exec web python manage.py test --settings=config.settings_test

check: ## Run Django's deployment checklist against the dev container
	@# A throwaway secret is passed in because settings.py refuses to boot with
	@# DEBUG=0 and the dev placeholder key — which is the behaviour being relied
	@# on here, not worked around.
	docker compose exec -e DJANGO_DEBUG=0 -e DJANGO_ALLOWED_HOSTS=localhost \
		-e DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost \
		-e DJANGO_SECRET_KEY=throwaway-key-for-the-deployment-checklist-only \
		web python manage.py check --deploy

# --- Secrets -----------------------------------------------------------------

secrets: ## Print a fresh DJANGO_SECRET_KEY, JOURNAL_ENCRYPTION_KEYS and DB_PASSWORD
	@python3 -c "import secrets,base64;\
print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64));\
print('JOURNAL_ENCRYPTION_KEYS=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode());\
print('DB_PASSWORD=' + secrets.token_urlsafe(32))"
	@echo ""
	@echo "Back JOURNAL_ENCRYPTION_KEYS up off this machine. Lose it and every"
	@echo "private entry is unrecoverable ciphertext."

freeze: ## Lock requirements.txt to the exact versions currently installed
	docker compose exec web pip freeze > backend/requirements.lock.txt
	@echo "Wrote backend/requirements.lock.txt"

# --- Production (run these on the VPS, in /opt/oddlyunspecific) ---------------

deploy: ## First boot: build, start, then link the shared Caddy to this stack
	@test -f .env || { echo "Missing .env — copy .env.example and fill it in (make secrets)."; exit 1; }
	@echo "--- Building image ---"
	$(PROD) build
	@echo "--- Starting services ---"
	$(PROD) up -d
	@echo "--- Attaching the shared VPS Caddy to this project's network ---"
	@$(MAKE) link-caddy
	@echo "--- Status ---"
	$(PROD) ps
	@echo ""
	@echo "Next: paste ./Caddyfile into the shared Caddyfile and reload it:"
	@echo "  docker exec $(SHARED_CADDY) caddy reload --config /etc/caddy/Caddyfile"
	@echo "Then: make prod-superuser && make prod-enroll USER=<name>"

link-caddy: ## Connect the shared VPS Caddy to this stack's network
	@net=$$(docker inspect -f '{{range $$k,$$v := .NetworkSettings.Networks}}{{$$k}}{{end}}' oddlyunspecific-web-1) || \
		{ echo "oddlyunspecific-web-1 is not running — start the stack first."; exit 1; }; \
	docker network connect $$net $(SHARED_CADDY) 2>/dev/null \
		&& echo "Connected $(SHARED_CADDY) to $$net" \
		|| echo "$(SHARED_CADDY) already on $$net (or not running)"; \
	echo "--- Reachability from the shared Caddy ---"; \
	waited=0; \
	while :; do \
		if docker exec $(SHARED_CADDY) wget -q -O /dev/null \
			--header="Host: localhost" http://oddlyunspecific-web-1:8000/healthz/ 2>/dev/null; then \
			[ $$waited -gt 0 ] && echo; \
			echo "  web OK$$([ $$waited -gt 0 ] && echo " after $${waited}s")"; \
			exit 0; \
		fi; \
		[ $$waited -ge $(WAIT) ] && break; \
		[ $$waited -eq 0 ] && printf "  web not up yet, waiting"; \
		printf "."; \
		sleep 3; waited=$$((waited + 3)); \
	done; \
	echo; echo "  web FAILED after $${waited}s"; \
	echo "    Check 'make prod-logs': no reply at all means the container died in"; \
	echo "    migrations or in 'check --deploy'; a 400 means DJANGO_ALLOWED_HOSTS"; \
	echo "    is missing the domain."; \
	exit 1

upgrade: ## Pull, rebuild and restart the prod stack
	@echo "--- Pulling latest code ---"
	git pull --rebase
	@echo "--- Rebuilding ---"
	$(PROD) build --no-cache web
	@echo "--- Restarting ---"
	$(PROD) up -d --remove-orphans
	@echo "--- Re-attaching the shared VPS Caddy ---"
	@# `up --remove-orphans` can recreate the network and drop the shared Caddy
	@# off it, which shows up as a 502 on every request.
	@$(MAKE) link-caddy
	@echo "--- Status ---"
	$(PROD) ps

prod-up: ## Build and start the prod stack detached (needs .env)
	$(PROD) up --build -d

prod-down: ## Stop the prod stack
	$(PROD) down

prod-ps: ## Prod service status
	$(PROD) ps

prod-logs: ## Tail prod logs
	$(PROD) logs -f

prod-migrate: ## Apply migrations in prod
	$(PROD) exec web python manage.py migrate

prod-superuser: ## Create an admin user in prod
	$(PROD) exec web python manage.py createsuperuser

prod-enroll: ## Print the TOTP enrolment QR: make prod-enroll USER=alessandro [ARGS=--reset]
	$(PROD) exec web python manage.py enroll_totp $(USER) $(ARGS)

prod-shell: ## Django shell in prod
	$(PROD) exec web python manage.py shell

prod-check: ## Run Django's deployment checklist in prod
	$(PROD) exec web python manage.py check --deploy

prod-rotate-journal: ## Re-encrypt every journal entry with the first key in JOURNAL_ENCRYPTION_KEYS
	$(PROD) exec web python manage.py rotate_journal_keys

# --- Backup ------------------------------------------------------------------

backup: ## Dump the prod database to backup-<date>.sql
	@set -a; . ./.env; set +a; \
	$(PROD) exec -T db pg_dump -U $${DB_USER:-oddly} $${DB_NAME:-oddly} \
		> backup-$$(date +%F-%H%M).sql
	@echo "Wrote backup-$$(date +%F-%H%M).sql"
	@echo "Private entries inside it stay encrypted — the dump is useless without"
	@echo "JOURNAL_ENCRYPTION_KEYS, so back that up separately."

restore: ## Restore a dump: make restore FILE=backup-2026-01-01-1200.sql
	@test -n "$(FILE)" || { echo "Usage: make restore FILE=backup-....sql"; exit 1; }
	@set -a; . ./.env; set +a; \
	$(PROD) exec -T db psql -U $${DB_USER:-oddly} -d $${DB_NAME:-oddly} < $(FILE)

# --- Misc --------------------------------------------------------------------

clean: ## Stop the dev stack and remove its volumes (DELETES the dev database)
	docker compose down -v
