.PHONY: validate compose-config ingest-prices go-test

validate:
	python3.12 -m compileall -q backend/app worker screener/ingest_prices.py screener/pipeline
	xmllint --noout docs/market-screener-erd.drawio

compose-config:
	POSTGRES_USER=portfolio_user POSTGRES_PASSWORD=local-only-password POSTGRES_DB=portfolio JWT_SECRET=local-only-jwt-secret docker compose config -q

ingest-prices:
	screener/.venv/bin/python screener/ingest_prices.py --period 2y

go-test:
	cd api && go test ./...
