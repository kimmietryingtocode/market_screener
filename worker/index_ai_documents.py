"""Index published screener material into the local pgvector RAG store."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from app.ai import providers

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "screener"


def _documents() -> list[dict]:
    review_candidates = [p for p in DATA_ROOT.glob("*/top10_review") if (p / "ranking.csv").is_file()]
    review = max(review_candidates, default=None, key=lambda p: p.parent.name)
    rows = []
    documents = []
    if review:
        with (review / "ranking.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            ticker = row["ticker"]
            content = json.dumps(row, sort_keys=True)
            documents.append({"source_key": f"ranking:{ticker}:{review.parent.name}", "source_type": "recommendation", "ticker": ticker, "published_at": review.parent.name[:10], "methodology_version": f"market-screen-{review.parent.name[:10]}", "content": content, "metadata": {"company_name": row.get("company"), "sector": row.get("sector")}})
        for path in sorted(review.glob("*.csv")):
            if path.name == "ranking.csv":
                continue
            text = path.read_text(encoding="utf-8-sig")
            documents.append({"source_key": f"screener:{review.parent.name}:{path.name}", "source_type": "methodology", "ticker": None, "published_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), "methodology_version": review.parent.name, "content": text[:100_000], "metadata": {"path": path.name}})
        for path in sorted(review.glob("factset/*/validation_report.csv")):
            text = path.read_text(encoding="utf-8-sig")
            documents.append({"source_key": f"factset-validation:{path.parent.name}", "source_type": "validation", "ticker": None, "published_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), "methodology_version": review.parent.name, "content": text[:100_000], "metadata": {"path": str(path.relative_to(review))}})
    return documents


def main() -> None:
    embeddings = providers.embeddings()
    embedding_provider = providers.provider()
    embedding_model = providers.embedding_model_name()
    embedding_dimensions = providers.embedding_dimensions()
    documents = _documents()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT s.ticker, e.score_date, e.summary, e.strengths, e.risks, e.model_name FROM explanations e JOIN securities s ON s.id = e.security_id")
            for ticker, score_date, summary, strengths, risks, model_name in cursor.fetchall():
                documents.append({
                    "source_key": f"explanation:{ticker}:{score_date}:{model_name}",
                    "source_type": "explanation",
                    "ticker": ticker,
                    "published_at": score_date,
                    "methodology_version": model_name,
                    "content": json.dumps({"summary": summary, "strengths": strengths, "risks": risks}, default=str, sort_keys=True),
                    "metadata": {"model_name": model_name},
                })
            for document in documents:
                digest = hashlib.sha256(document["content"].encode()).hexdigest()
                cursor.execute("SELECT content_hash, embedding_provider, embedding_model, embedding_dimensions FROM ai_documents WHERE source_key = %s", (document["source_key"],))
                existing = cursor.fetchone()
                if existing and existing == (digest, embedding_provider, embedding_model, embedding_dimensions):
                    continue
                vector = embeddings.embed_query(document["content"])
                if len(vector) != embedding_dimensions:
                    raise ValueError(f"embedding dimension mismatch: expected {embedding_dimensions}, got {len(vector)}")
                cursor.execute("""
                    INSERT INTO ai_documents (source_key, source_type, ticker, published_at, methodology_version, content, content_hash, metadata, embedding, embedding_provider, embedding_model, embedding_dimensions)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s)
                    ON CONFLICT (source_key) DO UPDATE SET source_type = EXCLUDED.source_type, ticker = EXCLUDED.ticker,
                    published_at = EXCLUDED.published_at, methodology_version = EXCLUDED.methodology_version,
                    content = EXCLUDED.content, content_hash = EXCLUDED.content_hash, metadata = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding, embedding_provider = EXCLUDED.embedding_provider,
                    embedding_model = EXCLUDED.embedding_model, embedding_dimensions = EXCLUDED.embedding_dimensions, updated_at = now()
                """, (document["source_key"], document["source_type"], document["ticker"], document["published_at"], document["methodology_version"], document["content"], digest, json.dumps(document["metadata"]), "[" + ",".join(str(float(v)) for v in vector) + "]", embedding_provider, embedding_model, embedding_dimensions))
    print(f"indexed {len(documents)} AI documents")


if __name__ == "__main__":
    main()
