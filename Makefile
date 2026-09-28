.PHONY: help dev up down build logs sh migrate makemigrations superuser enroll \
	test check secrets freeze audit \
	deploy upgrade link-caddy prod-up prod-down prod-ps prod-logs prod-migrate \
	prod-superuser prod-enroll prod-shell prod-check prod-rotate-journal \
	prod-unlock prod-clearsessions prod-stats prod-tail \
	backup restore clean

# bash, not sh: the backup and restore recipes pipe pg_dump through gzip and
# openssl, and dash has no `pipefail` — a failed dump would come out the far
# end as a perfectly valid, perfectly empty archive.
SHELL := /bin/bash

# Production stack. No public ports — the VPS shared Caddy fronts it.
PROD = docker compose -f docker-compose.prod.yml

# Where `make backup` writes. Gitignored.
BACKUP_DIR ?= backups
BACKUP_KEEP_DAYS ?= 14

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

secrets: ## Print a fresh set of secrets for .env
	@python3 -c "import secrets,base64;\
print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64));\
print('JOURNAL_ENCRYPTION_KEYS=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode());\
print('DB_PASSWORD=' + secrets.token_urlsafe(32));\
print('BACKUP_PASSPHRASE=' + secrets.token_urlsafe(32));\
print('DJANGO_ADMIN_PATH=console-' + secrets.token_hex(4))"
	@echo ""
	@echo "Back JOURNAL_ENCRYPTION_KEYS and BACKUP_PASSPHRASE up off this machine."
	@echo "Lose the first and every private entry is unrecoverable ciphertext."
	@echo "Lose the second and every backup is."

freeze: ## Regenerate backend/requirements.lock.txt from requirements.txt
	@# Resolved in a CLEAN container, not in the running one. The dev container
	@# has requirements-dev.txt installed on top, and freezing from it would
	@# quietly put live-reload into the production lock.
	@echo "Resolving requirements.txt in a clean python:3.12-slim ..."
	@{ \
		echo "# GENERATED — do not hand-edit. Run \`make freeze\` after changing"; \
		echo "# requirements.txt. See that file for why this one exists."; \
		echo "#"; \
		echo "# Resolved on Python 3.12 (the image's interpreter — see Dockerfile)"; \
		echo "# on $$(date +%F)."; \
		echo ""; \
		docker run --rm -v "$$PWD/backend:/w:ro" python:3.12-slim sh -c \
			'pip install -q -r /w/requirements.txt >/dev/null 2>&1 && \
			 pip freeze --exclude-editable | grep -viE "^(pip|setuptools|wheel)=="'; \
	} > backend/requirements.lock.txt
	@echo "Wrote backend/requirements.lock.txt — read the diff before committing:"
	@git --no-pager diff --stat backend/requirements.lock.txt || true

audit: ## Check the locked dependencies against the CVE database
	docker compose exec -T web pip-audit --requirement requirements.lock.txt --strict

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

prod-logs: ## Follow prod logs
	$(PROD) logs -f

prod-tail: ## Print the last N prod log lines and exit: make prod-tail [N=200]
	@# `prod-logs` follows, so piping it into grep hangs. This one returns.
	@$(PROD) logs --tail=$(or $(N),100)

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

prod-unlock: ## Clear a django-axes lockout: make prod-unlock IP=1.2.3.4 (or omit IP for all)
	@if [ -n "$(IP)" ]; then \
		$(PROD) exec -T web python -c "from axes.utils import reset; \
print('cleared', reset(ip='$(IP)'), 'attempt record(s) for $(IP)')"; \
	else \
		$(PROD) exec -T web python -c "from axes.utils import reset; \
print('cleared', reset(), 'attempt record(s)')"; \
	fi

prod-clearsessions: ## Delete expired session rows (also runs on every boot)
	$(PROD) exec web python manage.py clearsessions

prod-stats: ## Live CPU / memory per container, against the compose limits
	@docker stats --no-stream \
		$$($(PROD) ps -q) \
		--format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# --- Backup ------------------------------------------------------------------
#
# The dump is sensitive even though the journal inside it is not readable.
# Fernet protects journal.title and journal.body; it does not protect the
# django_otp TOTP device keys, which are stored plaintext, or the Argon2
# password hashes. A leaked dump therefore hands over the second factor — so
# the whole file is encrypted with BACKUP_PASSPHRASE before it touches disk.
#
# Two details that are easy to get wrong and silent when you do:
#   PGPASSWORD      the db container authenticates even over its local socket
#                   (scram-sha-256), so pg_dump prompts and hangs without it.
#   --clean         without it, restoring into a live database fails on every
#                   "relation already exists" — and with psql's default
#                   ON_ERROR_STOP off, it fails one statement at a time while
#                   reporting success.

backup: ## Encrypted pg_dump into ./backups/, pruning old ones
	@set -eo pipefail; \
	set -a; . ./.env; set +a; \
	mkdir -p $(BACKUP_DIR); \
	stamp=$$(date +%F-%H%M); \
	if [ -z "$$BACKUP_PASSPHRASE" ] || [ "$$BACKUP_PASSPHRASE" = "change-me-run-make-secrets" ]; then \
		echo "!!"; \
		echo "!! BACKUP_PASSPHRASE is not set. Writing an UNENCRYPTED dump."; \
		echo "!! It contains TOTP seeds and password hashes in the clear."; \
		echo "!! Run 'make secrets', put BACKUP_PASSPHRASE in .env, re-run this."; \
		echo "!!"; \
		out=$(BACKUP_DIR)/backup-$$stamp.sql.gz; \
	else \
		out=$(BACKUP_DIR)/backup-$$stamp.sql.gz.enc; \
	fi; \
	partial="$$out.part"; \
	trap 'rm -f "$$partial"' EXIT; \
	umask 077; \
	if [ -z "$${out##*.enc}" ]; then \
		$(PROD) exec -T -e PGPASSWORD="$$DB_PASSWORD" db \
			pg_dump --clean --if-exists -U $${DB_USER:-oddly} $${DB_NAME:-oddly} \
			| gzip \
			| openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
				-pass env:BACKUP_PASSPHRASE > "$$partial"; \
	else \
		$(PROD) exec -T -e PGPASSWORD="$$DB_PASSWORD" db \
			pg_dump --clean --if-exists -U $${DB_USER:-oddly} $${DB_NAME:-oddly} \
			| gzip > "$$partial"; \
	fi; \
	size=$$(wc -c < "$$partial"); \
	if [ "$$size" -lt 1024 ]; then \
		echo "Dump is $$size bytes — that is not a database. Check 'make prod-logs'."; \
		exit 1; \
	fi; \
	mv "$$partial" "$$out"; \
	chmod 600 "$$out"; \
	echo "Wrote $$out ($$(du -h "$$out" | cut -f1))"; \
	find $(BACKUP_DIR) -name 'backup-*' -mtime +$(BACKUP_KEEP_DAYS) -delete; \
	echo "Pruned anything older than $(BACKUP_KEEP_DAYS) days."; \
	echo ""; \
	echo "This is still one disk. Copy it somewhere else — see README, Backups."

restore: ## Restore a dump: make restore FILE=backups/backup-....sql.gz.enc
	@test -n "$(FILE)" || { echo "Usage: make restore FILE=backups/backup-....sql.gz[.enc]"; exit 1; }
	@test -f "$(FILE)" || { echo "No such file: $(FILE)"; exit 1; }
	@echo "This writes $(FILE) over the CURRENT database. It is not reversible."
	@read -r -p "Type the database name to confirm: " reply; \
	set -a; . ./.env; set +a; \
	if [ "$$reply" != "$${DB_NAME:-oddly}" ]; then echo "Aborted."; exit 1; fi
	@set -eo pipefail; \
	set -a; . ./.env; set +a; \
	case "$(FILE)" in \
		*.enc) openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
			-pass env:BACKUP_PASSPHRASE -in "$(FILE)" | gunzip ;; \
		*.gz)  gunzip -c "$(FILE)" ;; \
		*)     cat "$(FILE)" ;; \
	esac | $(PROD) exec -T -e PGPASSWORD="$$DB_PASSWORD" db \
		psql -v ON_ERROR_STOP=1 -U $${DB_USER:-oddly} -d $${DB_NAME:-oddly}
	@echo "Restored. Journal entries need the matching JOURNAL_ENCRYPTION_KEYS"
	@echo "to be readable — check /private/ before trusting this."

# --- Misc --------------------------------------------------------------------

clean: ## Stop the dev stack and remove its volumes (DELETES the dev database)
	docker compose down -v
