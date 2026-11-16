from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.repositories import recommendations_repository, scores_repository
from app.services import backtest_service
from app.ai import providers

logger = logging.getLogger(__name__)


class AnalysisState(TypedDict, total=False):
    context: dict[str, Any]
    facts: dict[str, Any]
    citations: list[dict[str, Any]]
    result: dict[str, Any]
    error: str


class ReportSection(BaseModel):
    title: str
    body: str = Field(description="Compact analysis containing every required ticker in ranking order")
    citation_keys: list[str] = Field(default_factory=list)


class AnalysisReport(BaseModel):
    title: str
    summary: str
    sections: list[ReportSection] = Field(min_length=1, max_length=1, description="Exactly one compact analysis section")
    limitations: list[str] = Field(default_factory=list)


class ChatAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=600, description="Final answer only, at most three sentences, with no reasoning or instructions")


_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")
_PLACEHOLDERS = ("[ticker]", "[company", "placeholder", "illustrative", "example value")


def _plain(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def load_facts(connection: Any, context: dict[str, Any]) -> dict[str, Any]:
    kind = context["kind"]
    if kind == "recommendations":
        rows = recommendations_repository.top_ranked(limit=10)
        return {"kind": kind, "rankings": [{"rank": rank, **_plain(row._asdict())} for rank, row in enumerate(rows, 1)]}
    if kind == "stock":
        ticker = context["ticker"].upper()
        rows = recommendations_repository.top_ranked(limit=100)
        recommendation = next(((rank, row) for rank, row in enumerate(rows, 1) if row.ticker == ticker), None)
        score = None
        fundamentals = None
        explanation = None
        # latest_run, not a bare score date. Two methodology versions can be
        # scored on the same day — running a new one beside the old one to
        # compare them is normal — and resolving on the date alone merges
        # them, so the same ticker appears twice with scores that were never
        # comparable. top_ranked requires both halves for that reason.
        latest = scores_repository.latest_run(connection)
        if latest:
            score_date, methodology_version = latest
            score_rows = scores_repository.top_ranked(
                connection,
                score_date=score_date,
                methodology_version=methodology_version,
                limit=100,
            )
            score = next((row for row in score_rows if row.ticker == ticker), None)
        with connection.cursor() as cursor:
            cursor.execute("SELECT s.company_name, s.sector, s.industry, f.period_end, f.filing_date, f.revenue, f.revenue_growth, f.earnings_growth, f.operating_margin, f.free_cash_flow, f.debt_to_equity, f.data_source FROM securities s LEFT JOIN LATERAL (SELECT * FROM fundamental_snapshots WHERE security_id = s.id ORDER BY filing_date DESC LIMIT 1) f ON TRUE WHERE s.ticker = %s", (ticker,))
            row = cursor.fetchone()
            if row:
                fundamentals = {"company_name": row[0], "sector": row[1], "industry": row[2], "period_end": _plain(row[3]), "filing_date": _plain(row[4]), "revenue": _plain(row[5]), "revenue_growth": _plain(row[6]), "earnings_growth": _plain(row[7]), "operating_margin": _plain(row[8]), "free_cash_flow": _plain(row[9]), "debt_to_equity": _plain(row[10]), "data_source": row[11]}
                cursor.execute("SELECT summary, strengths, risks, generated_at, model_name FROM explanations WHERE security_id = (SELECT id FROM securities WHERE ticker = %s) ORDER BY score_date DESC LIMIT 1", (ticker,))
                explanation_row = cursor.fetchone()
                if explanation_row:
                    explanation = {"summary": explanation_row[0], "strengths": explanation_row[1], "risks": explanation_row[2], "generated_at": _plain(explanation_row[3]), "model_name": explanation_row[4]}
        recommendation_fact = {"rank": recommendation[0], **_plain(recommendation[1]._asdict())} if recommendation else None
        return {"kind": kind, "ticker": ticker, "recommendation": recommendation_fact, "score": _plain(score._asdict()) if score else None, "fundamentals": fundamentals, "explanation": explanation}
    detail = backtest_service.get_backtest_detail(connection, context["backtest_id"], context["user_id"])
    return {"kind": kind, "backtest": _plain(detail.backtest._asdict()), "research_outcome": detail.research_outcome, "validation_messages": detail.validation_messages, "metrics": _plain(detail.metrics._asdict()) if detail.metrics else None, "equity_curve": [_plain(point._asdict()) for point in detail.equity_curve]}


def retrieve(connection: Any, context: dict[str, Any], facts: dict[str, Any]) -> list[dict[str, Any]]:
    providers.ensure_allowed(context)
    query = json.dumps(facts, sort_keys=True)
    ticker = context.get("ticker")
    embedding = None
    try:
        embedding = providers.embeddings().embed_query(query)
    except Exception:
        if providers.provider() == "gemini":
            raise
    with connection.cursor() as cursor:
        if embedding:
            where = "WHERE embedding IS NOT NULL AND embedding_provider = %s AND embedding_model = %s"
            params: list[Any] = [providers.provider(), providers.embedding_model_name()]
            vector = "[" + ",".join(str(float(v)) for v in embedding) + "]"
            if ticker:
                where += " AND (ticker = %s OR ticker IS NULL)"
                params.append(ticker)
            params.extend([vector, 3])
            cursor.execute(f"SELECT id, source_key, content, metadata FROM ai_documents {where} ORDER BY embedding <=> %s::vector LIMIT %s", tuple(params))
        else:
            like = f"%{ticker or context['kind']}%"
            cursor.execute("SELECT id, source_key, content, metadata FROM ai_documents WHERE (content ILIKE %s OR source_type = %s) AND embedding_provider = %s AND embedding_model = %s ORDER BY updated_at DESC LIMIT 3", (like, context["kind"], providers.provider(), providers.embedding_model_name()))
        return [{"document_id": row[0], "source_key": row[1], "excerpt": row[2][:300], "metadata": row[3] or {}} for row in cursor.fetchall()]


def _numbers(value: Any) -> set[str]:
    text = json.dumps(value, default=str, sort_keys=True) if not isinstance(value, str) else value
    numbers = set()
    for token in _NUMBER.findall(text):
        try:
            number = Decimal(token.rstrip("%").replace(",", ""))
        except InvalidOperation:
            continue
        numbers.add(str(number.normalize()))
    return numbers


def _report_errors(payload: dict, facts: dict, citations: list[dict], context: dict) -> list[str]:
    errors = []
    sections = payload.get("sections") or []
    text = "\n".join([str(payload.get("title", "")), str(payload.get("summary", "")), *(str(section.get("title", "")) + "\n" + str(section.get("body", "")) for section in sections)])
    lowered = text.lower()
    if not payload.get("title", "").strip() or not payload.get("summary", "").strip() or not sections:
        errors.append("title, summary, and at least one section are required")
    if any(marker in lowered for marker in _PLACEHOLDERS):
        errors.append("placeholders or illustrative values are forbidden")

    valid_citations = {item["source_key"] for item in citations}
    used_citations = [key for section in sections for key in section.get("citation_keys", [])]
    invalid_citations = sorted(set(used_citations) - valid_citations)
    if invalid_citations:
        errors.append("unknown citation keys: " + ", ".join(invalid_citations))
    if citations and not used_citations:
        errors.append("at least one retrieved evidence citation is required")

    unknown_numbers = sorted(_numbers(text) - _numbers(facts))
    if unknown_numbers:
        errors.append("numbers absent from authoritative facts: " + ", ".join(unknown_numbers))

    if context["kind"] == "recommendations":
        tickers = [str(row["ticker"]).upper() for row in facts.get("rankings", [])]
        positions = [text.upper().find(ticker) for ticker in tickers]
        missing = [ticker for ticker, position in zip(tickers, positions) if position < 0]
        if missing:
            errors.append("missing ranked tickers: " + ", ".join(missing))
        elif positions != sorted(positions):
            errors.append("ranked tickers are out of order")
    elif context["kind"] == "stock" and context.get("ticker") and context["ticker"].upper() not in text.upper():
        errors.append("stock report must name ticker " + context["ticker"].upper())
    return errors


def _chat_model(*, num_predict: int):
    return providers.chat_model(num_predict=num_predict)


def _structured_attempt(model: Any, prompt: str) -> tuple[dict | None, str, str | None]:
    response = model.with_structured_output(AnalysisReport, method="json_schema", include_raw=True).invoke(prompt, stream=False)
    parsed = response.get("parsed")
    raw = response.get("raw")
    raw_content = str(getattr(raw, "content", "") or "")
    payload = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
    return payload, raw_content, str(response.get("parsing_error")) if response.get("parsing_error") else None


def _model_result(facts: dict, citations: list[dict], context: dict) -> dict:
    providers.ensure_allowed(context)

    prompt = """You analyze stock research data. Use only FACTS and EVIDENCE below. Never invent prices, returns, dates, filings, or causes. Copy company names, tickers, ranks, and numeric values exactly from FACTS; never use placeholders or illustrative values. If a fact is missing, say "not available". Preserve every published ranking in order. For incomplete backtests, explain the recorded status without performance analysis. Explain that this is research, not personalized financial advice. Use one compact section and keep the entire response under 400 tokens. Return only JSON matching JSON_SCHEMA. Every citation_keys value must be one of the evidence source_key values; cite retrieved evidence when it is available.

FACTS:
{facts}
EVIDENCE:
{evidence}
REQUEST TYPE: {kind}
JSON_SCHEMA:
{schema}
""".format(facts=json.dumps(facts, default=str), evidence=json.dumps(citations, default=str), kind=context["kind"], schema=json.dumps(AnalysisReport.model_json_schema()))
    model = _chat_model(num_predict=512)
    payload, raw, parsing_error = _structured_attempt(model, prompt)
    errors = [parsing_error] if parsing_error else (_report_errors(payload, facts, citations, context) if payload else ["empty structured response"])
    if errors:
        logger.warning("AI report first pass failed validation: %s", "; ".join(errors))
        correction = """Correct PREVIOUS_OUTPUT using only FACTS. Return only JSON matching JSON_SCHEMA, with one compact section under 400 tokens. Copy numeric values exactly. Preserve all ranked tickers in order. citation_keys may only use ALLOWED_CITATION_KEYS.

FACTS:
{facts}
ALLOWED_CITATION_KEYS:
{citation_keys}
PREVIOUS_OUTPUT:
{previous}
VALIDATION_ERRORS:
{errors}
JSON_SCHEMA:
{schema}
""".format(
            facts=json.dumps(facts, default=str),
            citation_keys=json.dumps([item["source_key"] for item in citations]),
            previous=raw[:2000],
            errors="\n- ".join(errors),
            schema=json.dumps(AnalysisReport.model_json_schema()),
        )
        payload, raw, parsing_error = _structured_attempt(model, correction)
        errors = [parsing_error] if parsing_error else (_report_errors(payload, facts, citations, context) if payload else ["empty structured response"])
    if errors:
        raise ValueError("model output failed validation after one correction: " + "; ".join(errors))
    return payload


def run_analysis(connection: Any, context: dict[str, Any]) -> tuple[dict, dict, list[dict]]:
    from langgraph.graph import END, START, StateGraph

    def facts_node(state: AnalysisState):
        providers.ensure_allowed(state["context"])
        return {"facts": load_facts(connection, state["context"])}

    def retrieve_node(state: AnalysisState):
        return {"citations": retrieve(connection, state["context"], state["facts"])}

    def generate_node(state: AnalysisState):
        return {"result": _model_result(state["facts"], state["citations"], state["context"])}

    def validate_node(state: AnalysisState):
        result = state["result"]
        errors = _report_errors(result, state["facts"], state.get("citations", []), state["context"])
        if errors:
            raise ValueError("validated report became invalid: " + "; ".join(errors))
        return {"result": result}

    graph = StateGraph(AnalysisState)
    graph.add_node("facts", facts_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("validate", validate_node)
    graph.add_edge(START, "facts")
    graph.add_edge("facts", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "validate")
    graph.add_edge("validate", END)
    state = graph.compile().invoke({"context": context})
    return state["facts"], state["result"], state.get("citations", [])


def run_chat(context: dict[str, Any]) -> str:
    providers.ensure_allowed(context)
    facts = context.get("facts") or {}
    history = [item for item in context.get("history") or [] if 0 < len(str(item.get("content", "")).strip()) <= 1000]
    prompt = "Answer only from REPORT_FACTS and CONVERSATION. If the facts do not answer the question, say so. Copy numeric values exactly. Give only the final answer in at most three sentences. Do not give personalized trade advice.\n\nREPORT_FACTS:\n" + json.dumps(facts, default=str) + "\n\nCONVERSATION:\n" + json.dumps(history, default=str)
    model = _chat_model(num_predict=160)
    response = model.with_structured_output(ChatAnswer, method="json_schema", include_raw=True).invoke(prompt, stream=False)
    parsed = response.get("parsed")
    answer = str(parsed.answer if parsed else "").strip()
    errors = [] if answer else [str(response.get("parsing_error") or "empty answer")]
    unknown_numbers = sorted(_numbers(answer) - _numbers(facts))
    if unknown_numbers:
        errors.append("numbers absent from authoritative facts: " + ", ".join(unknown_numbers))
    if errors:
        response = model.with_structured_output(ChatAnswer, method="json_schema", include_raw=True).invoke(prompt + "\n\nCorrect these validation errors: " + "; ".join(errors), stream=False)
        parsed = response.get("parsed")
        answer = str(parsed.answer if parsed else "").strip()
        errors = [] if answer else [str(response.get("parsing_error") or "empty answer")]
        unknown_numbers = sorted(_numbers(answer) - _numbers(facts))
        if unknown_numbers:
            errors.append("numbers absent from authoritative facts: " + ", ".join(unknown_numbers))
    if errors:
        raise ValueError("chat output failed validation after one correction: " + "; ".join(errors))
    return answer
