import json
import logging

from app.core.observability import JsonFormatter, request_id_var


def _record(**kwargs) -> logging.LogRecord:
    defaults = dict(name="app.test", level=logging.INFO, pathname="x.py", lineno=1, msg="hello", args=None, exc_info=None)
    defaults.update(kwargs)
    return logging.LogRecord(**defaults)


def test_formats_a_json_line_with_the_core_fields():
    output = json.loads(JsonFormatter().format(_record()))

    assert output["level"] == "INFO"
    assert output["logger"] == "app.test"
    assert output["message"] == "hello"
    assert "timestamp" in output


def test_includes_the_request_id_when_one_is_set():
    token = request_id_var.set("abc123")
    try:
        output = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)

    assert output["request_id"] == "abc123"


def test_omits_request_id_outside_a_request():
    output = json.loads(JsonFormatter().format(_record()))
    assert "request_id" not in output


def test_forwards_extra_fields_so_they_stay_queryable():
    record = _record()
    record.duration_ms = 12.5
    record.status_code = 200

    output = json.loads(JsonFormatter().format(record))

    assert output["duration_ms"] == 12.5
    assert output["status_code"] == 200


def test_renders_exceptions_into_the_payload_not_as_loose_lines():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(exc_info=sys.exc_info())

    output = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in output["exception"]
    # Must still be one parseable line, not a traceback dumped raw.
    assert json.loads(JsonFormatter().format(record))


def test_message_interpolation_is_applied():
    output = json.loads(JsonFormatter().format(_record(msg="scored %s securities", args=(10,))))
    assert output["message"] == "scored 10 securities"
