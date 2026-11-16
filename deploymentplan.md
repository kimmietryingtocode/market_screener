# Free-first deployment plan

This is the deployment design for a small invited alpha group (approximately
5–10 users). The first hosted release is intentionally a single-server
deployment. It keeps financial calculations in the existing screener, quant
service, and C++ engine; AI only explains authoritative stored facts.

## Hosting

- Use one Oracle Cloud Always Free ARM VM (`VM.Standard.A1.Flex`, 2 OCPUs,
  12 GB RAM) in the home region.
- Run Ubuntu, Docker Compose, Caddy, FastAPI, the quant service, PostgreSQL 16
  with pgvector, Redis, workers, the C++ backtest engine, and Ollama on the VM.
- Use a 50 GB boot volume and a separate 100 GB data volume mounted at
  `/srv/fintech` for Postgres, screener outputs, backtest artifacts, model data,
  TLS state, and backups.
- Use a free DuckDNS hostname and let Caddy obtain and renew HTTPS certificates.
- Publish only ports 80 and 443. Restrict SSH to the operator IP. Do not expose
  Postgres, Redis, Ollama, quant, or worker endpoints publicly.
- Use private OCI Object Storage for encrypted backups. Keep the decryption key
  outside the VM and outside the bucket.
- Use GitHub Container Registry for immutable application images and build ARM64
  images in CI.

The target is $0/month while the account remains inside free allowances. Free
capacity, quotas, VM availability, and performance are not guaranteed. Do not
automatically upgrade to paid resources.

## Application layout

Production Compose should include:

- Caddy serving the buildless frontend and proxying `/api` to FastAPI.
- FastAPI, the quant service, backtest worker, ingestion workers, and AI worker.
- PostgreSQL 16 with pgvector as the durable database and job queue.
- Redis only for cache/rate-limit support.
- Ollama behind a private Docker profile for private backtest analysis.
- Gemini as the optional public-data alpha provider through `GOOGLE_API_KEY`.

All services use restart policies, health checks, bounded resources, and log
rotation. Heavy AI, indexing, ingestion, and C++ work should be serialized or
bounded so the small VM does not thrash or run out of memory.

## AI policy

Gemini is enabled for published recommendation and stock explanations by
default:

```env
AI_PROVIDER=gemini
AI_GEMINI_DATA_POLICY=public_only
GOOGLE_API_KEY=...
GEMINI_MODEL=gemini-3.8-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_DIMENSIONS=768
```

Private portfolios, holdings, equity curves, and backtests must use:

```env
AI_PROVIDER=ollama
AI_GEMINI_DATA_POLICY=public_only
```

The API rejects a private backtest request before it is queued when Gemini is
selected with `public_only`. No private backtest facts are sent to Google.

The shared index contains only published screener explanations, normalized
FactSet outputs, validation notes, and ranking methodology. Raw uploads,
credentials, source code, audit reports, and private backtest data are excluded.
Documents retain source identifiers, ticker, publication date, methodology
version, content hashes, and embedding provider/model metadata. Re-index after
changing embedding providers or models.

## Reliability and limits

- One active AI request per user and one model generation at a time.
- Queue capacity: 10 deployed AI jobs; processing deadline: 600 seconds.
- Recover only jobs whose lease has expired; use claim fencing and heartbeats.
- Preserve atomic result/citation/job completion writes.
- Suggested group target: 30 reports, 50 follow-ups, and 10 backtests per day.
- Keep full equity curves in PostgreSQL/UI; send only bounded summaries to the
  model.
- Retain reports and conversations until the user deletes them. Remove temporary
  inputs immediately; prune redundant raw backtest artifacts after 30 days.

## Backups and operations

- Nightly encrypted `pg_dump`, artifact manifests, and changed screener outputs.
- Keep seven daily and four weekly backups within the 8 GB free backup budget.
- Test a restore into a separate database monthly.
- Target RPO: 24 hours. Target restore effort: 2 hours after capacity is available.
- Monitor API readiness, worker heartbeats, queue age, backup failures, disk usage,
  memory, validation failures, and data-feed failures.
- Patch weekly, review quotas and restore status monthly, and never generate fake
  workload to avoid provider idle policies.

## Release sequence

1. Confirm Always Free ARM capacity before depending on the VM.
2. Create the VM, data volume, firewall rules, DuckDNS record, and Caddy.
3. Build and push immutable ARM64 images.
4. Back up and migrate existing database/artifacts with append-only migrations.
5. Pull Gemini/Ollama models and run the provider-aware indexer.
6. Run real acceptance tests for recommendations, stock, chat, and private Ollama
   backtest analysis.
7. Invite users only after ownership, queue, restart, backup, and outage gates pass.

Keep the previous application image available for rollback. Pause claims, drain
jobs, back up, apply migrations, and restart during releases. Ordinary rankings,
portfolios, and backtests must remain usable when Gemini, Ollama, or the AI worker
is unavailable.
