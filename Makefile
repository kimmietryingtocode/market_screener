.PHONY: validate compose-config ingest-prices

validate:
	python3.12 -m compileall -q backend/app worker screener/ingest_prices.py screener/pipeline
	xmllint --noout docs/market-screener-erd.drawio

compose-config:
	POSTGRES_USER=demo_user POSTGRES_PASSWORD=demo_value POSTGRES_DB=portfolio JWT_SECRET=demo_value docker compose config -q

ingest-prices:
	screener/.venv/bin/python screener/ingest_prices.py --period 2y
