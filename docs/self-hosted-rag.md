# RAG analysis providers

The analysis workflow is provider-selectable. Gemini is the default alpha
provider for published recommendation and stock evidence; Ollama remains the
private/self-hosted provider for portfolio and backtest data.

## Gemini alpha

Copy `.env.example` to `.env`, add `GOOGLE_API_KEY`, and start the stack:

```bash
cp .env.example .env
docker compose up --build
docker compose run --rm analysis-worker python /workspace/worker/index_ai_documents.py
```

Gemini uses `gemini-3.8-flash` and `gemini-embedding-001` by default. Override
`GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL`, or `GEMINI_EMBEDDING_DIMENSIONS` in
`.env` when needed. The free/public-only policy rejects private backtest
reports before model invocation. Gemini API keys and prompts must never be
committed or printed in logs.

## Ollama private mode

Set `AI_PROVIDER=ollama` and start the optional Ollama profile:

```bash
AI_PROVIDER=ollama docker compose --profile ollama up --build
docker compose run --rm analysis-worker python /workspace/worker/index_ai_documents.py
```

Ollama runs locally with `OLLAMA_NO_CLOUD=1`; private backtest facts stay in the
local database and are not sent to a hosted model. Re-run the indexer whenever
the provider or embedding model changes so retrieval never mixes vector spaces.

## Self-hosted stock analysis

The AI analysis feature is optional. Existing rankings, portfolios, and
backtests continue to work if Ollama is stopped.

The local stack uses PostgreSQL with pgvector, Ollama, LangChain, and
LangGraph. Ollama runs in local-only mode; prompts and generated reports stay
inside the configured deployment.

## Local setup

1. Start the database, API, Redis, and Ollama:

   ```bash
   docker compose up --build postgres migrate redis backend ollama analysis-worker
   ```

2. Pull the models once:

   ```bash
   docker compose exec ollama ollama pull qwen3:4b
   docker compose exec ollama ollama pull nomic-embed-text:v1.5
   ```

3. Index the published screener material:

   ```bash
   docker compose run --rm \
     -e DATABASE_URL=postgres://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB \
     -e OLLAMA_BASE_URL=http://ollama:11434 \
     analysis-worker python -m worker.index_ai_documents
   ```

For native Ollama on macOS, set `OLLAMA_BASE_URL=http://host.docker.internal:11434`
for the analysis worker. Docker Desktop on macOS does not pass the host GPU
through to containers; native Ollama is preferred for acceleration.

The worker defaults to a 4,096-token context, bounded report/chat output, one
model job at a time, and a hard five-minute child-process deadline. Give the
Docker VM at least 8 GB of memory for `qwen3:4b`; if the Ollama log reports a
killed or unexpectedly stopped model runner, increase VM memory or use native
Ollama, then restart `ollama` and `analysis-worker`. Override
`AI_CONTEXT_TOKENS`, `AI_MODEL_TIMEOUT_SECONDS`, or `AI_JOB_TIMEOUT_SECONDS`
only after measuring the real model on the target host.

The frontend exposes “Explain rankings”, “Explain” beside each ranked stock,
and “Analyze run” for completed backtests. Reports are queued, saved per
account, and can be reopened after a restart. Follow-up questions remain
attached to the report snapshot.

## Deployment notes

Run Ollama on a private host or private network and allow the API/worker to
reach port 11434. Do not expose Ollama directly to the browser. Use a GPU host
when response latency or concurrent users requires it; the initial
configuration deliberately runs one analysis job at a time.

Change `AI_MODEL`, `AI_EMBEDDING_MODEL`, and `OLLAMA_BASE_URL` through the
deployment environment. Re-run the indexer when published ranking artifacts or
validation reports change. The indexer hashes source content, so unchanged
documents are skipped.
