from app.api.v1.workflows import _chat_message_usage_totals
from app.services.chat import _extract_tokens_used


def test_extract_tokens_used_prefers_explicit_total():
    assert _extract_tokens_used({"tokens_used": 42, "input_tokens": 10, "output_tokens": 20}) == 42


def test_extract_tokens_used_falls_back_to_input_plus_output():
    assert _extract_tokens_used({"input_tokens": 120, "output_tokens": 30}) == 150


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *args, **kwargs):
        return _FakeQuery(self._rows)


def test_chat_message_usage_totals_reads_historical_context_fields():
    db = _FakeDB([
        (None, {"input_tokens": 100, "output_tokens": 50, "cost_usd": "0.12"}),
        (25, {"cost": 0.08}),
        (None, {"tokens_used": 10}),
    ])

    total_tokens, total_cost = _chat_message_usage_totals(db, "tenant-id")

    assert total_tokens == 185
    assert total_cost == 0.2
