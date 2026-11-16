from app.ai.workflow import _report_errors


def _facts():
    return {
        "kind": "recommendations",
        "rankings": [
            {"rank": 1, "ticker": "CDNA", "company_name": "CareDx, Inc.", "momentum_score": 86.6667},
            {"rank": 2, "ticker": "DK", "company_name": "Delek US Holdings, Inc.", "momentum_score": 81.6667},
        ],
    }


def _citations():
    return [{"source_key": "ranking:CDNA:2026-09-16"}]


def test_report_validation_accepts_grounded_ordered_output():
    report = {
        "title": "Published ranking",
        "summary": "CDNA ranks 1 with momentum 86.6667, followed by DK at rank 2 with momentum 81.6667.",
        "sections": [{"title": "Evidence", "body": "CDNA then DK.", "citation_keys": ["ranking:CDNA:2026-09-16"]}],
        "limitations": [],
    }

    assert _report_errors(report, _facts(), _citations(), {"kind": "recommendations"}) == []


def test_report_validation_rejects_placeholders_invented_numbers_and_bad_citations():
    report = {
        "title": "Top [Company]",
        "summary": "DK has an illustrative score of 85.2; CDNA follows.",
        "sections": [{"title": "Evidence", "body": "DK then CDNA.", "citation_keys": ["invented:source"]}],
        "limitations": [],
    }

    errors = _report_errors(report, _facts(), _citations(), {"kind": "recommendations"})

    assert any("placeholders" in error for error in errors)
    assert any("85.2" in error for error in errors)
    assert any("unknown citation" in error for error in errors)
    assert any("out of order" in error for error in errors)
