"""Recovering a structured response that langchain declined to parse.

This is the defect that stopped the analysis feature from ever producing a
report. The model answered correctly every time; langchain's json_schema
method returned parsed=None and, worse, parsing_error=None alongside it, so
the workflow reported "empty structured response" while the complete and
valid JSON sat untouched in raw.content.

The symptom pointed in the wrong direction for a long time — it looks like
a model that returned nothing, which invites theories about token budgets
and thinking traces. Measuring it showed the opposite: the reply used 69
output tokens and no thinking tokens at all.

No API calls here. The responses are the shapes langchain actually returns.
"""

from __future__ import annotations

from app.ai.workflow import AnalysisReport, ChatAnswer, _parse_raw

VALID_REPORT_JSON = """{
  "title": "Apple Inc. analysis",
  "summary": "AAPL holds a composite score of 75.0.",
  "sections": [{"title": "Scoring", "body": "Momentum 100.0, fundamental 50.0.", "citation_keys": []}],
  "limitations": ["Research, not advice."]
}"""


class TestRecoveringTheModelsAnswer:
    def test_plain_json_is_recovered(self):
        report = _parse_raw(VALID_REPORT_JSON, AnalysisReport)
        assert report is not None
        assert report.title == "Apple Inc. analysis"
        assert len(report.sections) == 1

    def test_json_wrapped_in_a_markdown_fence_is_recovered(self):
        """Models add fences even when asked for raw JSON."""
        fenced = f"```json\n{VALID_REPORT_JSON}\n```"
        assert _parse_raw(fenced, AnalysisReport) is not None

    def test_a_bare_fence_without_the_language_tag_is_recovered(self):
        assert _parse_raw(f"```\n{VALID_REPORT_JSON}\n```", AnalysisReport) is not None

    def test_surrounding_whitespace_does_not_defeat_it(self):
        assert _parse_raw(f"\n\n  {VALID_REPORT_JSON}  \n", AnalysisReport) is not None

    def test_the_chat_schema_is_recovered_too(self):
        """run_chat had the identical flaw."""
        answer = _parse_raw('{"answer": "AAPL ranks first on momentum."}', ChatAnswer)
        assert answer is not None
        assert answer.answer == "AAPL ranks first on momentum."


class TestRefusingWhatIsNotAnAnswer:
    def test_an_empty_response_stays_empty(self):
        assert _parse_raw("", AnalysisReport) is None
        assert _parse_raw("   \n  ", AnalysisReport) is None

    def test_prose_is_not_coerced_into_a_report(self):
        assert _parse_raw("I cannot answer that question.", AnalysisReport) is None

    def test_json_that_does_not_match_the_schema_is_rejected(self):
        """Recovering the text must not mean accepting any JSON. A payload
        missing title and summary has to fail, or the grounding checks run
        against a report that was never produced."""
        assert _parse_raw('{"unrelated": "value"}', AnalysisReport) is None

    def test_a_report_with_no_sections_is_rejected(self):
        # AnalysisReport requires exactly one section.
        assert _parse_raw('{"title": "T", "summary": "S", "sections": []}', AnalysisReport) is None

    def test_truncated_json_is_rejected_rather_than_half_read(self):
        assert _parse_raw(VALID_REPORT_JSON[: len(VALID_REPORT_JSON) // 2], AnalysisReport) is None


def test_the_exact_response_that_broke_the_feature():
    """Verbatim from langchain against gemini-3.1-flash-lite: valid JSON in
    raw.content, parsed=None, parsing_error=None."""
    raw_content = (
        '{\n  "title": "Market Ranking Analysis",\n'
        '  "summary": "A brief overview of the top-ranked asset.",\n'
        '  "sections": [\n    {\n      "title": "Top Ranked Asset",\n'
        '      "body": "AAPL is currently ranked at position 1."\n    }\n  ]\n}'
    )
    recovered = _parse_raw(raw_content, AnalysisReport)
    assert recovered is not None, "this is the response the feature used to throw away"
    assert recovered.sections[0].body == "AAPL is currently ranked at position 1."
