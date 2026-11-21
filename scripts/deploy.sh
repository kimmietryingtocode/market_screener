#!/usr/bin/env bash
# Deploy the stack to a single VM over SSH.
#
#   scripts/deploy.sh user@host
#
# Copies the repository (excluding git history, data, and local secrets),
# then builds and starts the production Compose stack on the remote host.
#
# Secrets are never copied. The remote /srv/fintech/.env is authored once on
# the VM and left alone by every later deploy, so a redeploy can't overwrite
# production credentials with a developer's local file.
set -euo pipefail

TARGET="${1:-}"
REMOTE_DIR="${REMOTE_DIR:-/srv/fintech}"

if [[ -z "$TARGET" ]]; then
	echo "usage: scripts/deploy.sh user@host" >&2
	exit 64
fi

echo "==> Checking the remote host has Docker and a production .env"
ssh "$TARGET" "command -v docker >/dev/null || { echo 'docker is not installed on the remote host' >&2; exit 1; }
	sudo mkdir -p '$REMOTE_DIR'
	sudo chown \"\$(id -u):\$(id -g)\" '$REMOTE_DIR'
	if [[ ! -f '$REMOTE_DIR/.env' ]]; then
		echo '' >&2
		echo 'No $REMOTE_DIR/.env on the remote host.' >&2
		echo 'Generate one there first:' >&2
		echo '  python3 scripts/generate_env.py   # then set SITE_ADDRESS, ACME_EMAIL, GOOGLE_CLIENT_ID, GOOGLE_API_KEY' >&2
		exit 1
	fi"

echo "==> Syncing source to $TARGET:$REMOTE_DIR"
# --exclude .env is the important one: the remote file is the real one.
rsync -az --delete \
	--exclude '.git' \
	--exclude '.env' \
	--exclude '__pycache__' \
	--exclude '.pytest_cache' \
	--exclude 'node_modules' \
	--exclude 'backtest/build' \
	--exclude 'data/backtest/jobs' \
	./ "$TARGET:$REMOTE_DIR/"

echo "==> Building and starting"
ssh "$TARGET" "cd '$REMOTE_DIR' && \
	DOCKER_PLATFORM=\${DOCKER_PLATFORM:-linux/arm64} \
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"

echo "==> Waiting for readiness"
# /readyz, not /healthz: it confirms the backend can actually reach Postgres,
# so a deploy that starts but cannot see its database fails here rather than
# looking successful.
for attempt in $(seq 1 30); do
	if ssh "$TARGET" "curl -fsS -o /dev/null http://localhost/readyz" 2>/dev/null; then
		echo "==> Ready"
		ssh "$TARGET" "cd '$REMOTE_DIR' && docker compose -f docker-compose.yml -f docker-compose.prod.yml ps"
		exit 0
	fi
	sleep 5
done

echo "==> Did not become ready. Recent logs:" >&2
ssh "$TARGET" "cd '$REMOTE_DIR' && docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 40 backend caddy" >&2
exit 1
