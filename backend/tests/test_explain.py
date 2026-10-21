"""Offline explanation tests; database state is isolated from all other suites."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from kairos.db import migrate
from kairos.explain import (
    AnthropicProvider, ProviderResult, build_facts, build_prompt, check_text,
    estimate_cost_usd, explain_signal, facts_hash, fallback_explanation,
)

DSN = "postgresql://kairos@localhost:5434/kairos_test_explain"
TEXT = "Early mentions and the shillers' past successes support the score. The creator's rug history is the main risk."
NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Live HTTP is forbidden in explanation tests")
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("Live HTTP is forbidden"))


@pytest.fixture
def features():
    return {
        "scorer_version": "s1", "evaluated_at": NOW.isoformat(),
        "outcome_rule_version": "v2", "horizon": "24 hours",
        "token": {"token_id": 1, "mint": "secret-mint", "symbol": "WIF", "name": "Wif",
                  "created_at": NOW.isoformat(), "first_mention_at": NOW.isoformat(),
                  "age_minutes": 14.123, "entry_minute": 3.5},
        "inside": [{"kol_id": 7, "username": "alpha", "followers": 12000,
                    "first_mention_at": NOW.isoformat(), "minute": 3.5,
                    "n_final": 12, "success": 5, "rug": 1, "success_rate": 0.4167,
                    "success_lb": 0.193, "rug_rate": 0.0833, "rug_lb": 0.015}],
        "n_shillers": 3,
        "creator": {"wallet": "secret-wallet", "past_final": 4, "past_rug": 3, "rug_lb": 0.30},
        "similar": [{"token_id": i, "mint": f"secret-similar-{i}", "symbol": f"XYZ{i}",
                     "similarity": 0.729 - i / 100, "jaccard": 0.67,
                     "label": "success", "peak_multiple": 5.1} for i in range(5)],
        "components": {"shillers": 22.4, "breadth": 7.5, "similar": 12.0, "entry": 10.0,
                       "creator_clean": 0.0, "rug_shillers": -1.2, "creator_rug": -15.0,
                       "similar_rug": 0.0},
        "evidence": {"best_shiller_n_final": 30, "shillers_with_10plus": 2, "similar_cases": 4},
        "risks": ["creator_rugged_before", "single_shiller"],
    }


@pytest.fixture
def settings():
    return SimpleNamespace(anthropic_api_key=None, explain_model="claude-sonnet-5-5", explain_daily_limit=200)


@pytest.fixture
async def db_pool():
    # Deliberately override conftest's fixture; never connect to kairos_test.
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await migrate(conn)
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def signal_id(db_pool, features):
    token_id = await db_pool.fetchval(
        "INSERT INTO tokens (mint, symbol, source) VALUES ('test-mint', 'WIF', 'test') RETURNING id"
    )
    return await db_pool.fetchval(
        """INSERT INTO signals (token_id, score, confidence, scorer_version, features, n_shillers)
           VALUES ($1, 65, 'medium', 's1', $2, 3) RETURNING id""",
        token_id, json.dumps(features),
    )


class FakeProvider:
    name = "fake"
    model = "claude-sonnet-5-5"

    def __init__(self, text=TEXT, refused=False, error=None):
        self.calls = []
        self.result = ProviderResult(text, 1000, 100, self.model, refused)
        self.error = error

    async def complete(self, system, prompt):
        self.calls.append((system, prompt))
        if self.error is not None:
            raise self.error
        return self.result


async def test_cache_hit(db_pool, signal_id, settings):
    provider = FakeProvider()
    assert await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW) == TEXT
    # Cached content remains available when the feature is disabled or the budget is exhausted.
    settings.explain_daily_limit = 0
    assert await explain_signal(db_pool, signal_id, settings=settings, now=NOW) == TEXT
    assert len(provider.calls) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM signal_explanations") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls") == 1


async def test_changed_facts(db_pool, signal_id, settings, features):
    provider = FakeProvider()
    await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW)
    features["n_shillers"] = 4
    await db_pool.execute("UPDATE signals SET features = $2 WHERE id = $1", signal_id, json.dumps(features))
    await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW)
    assert len(provider.calls) == 2
    assert await db_pool.fetchval("SELECT count(*) FROM signal_explanations") == 2


async def test_no_key(db_pool, signal_id, settings, monkeypatch, caplog):
    import kairos.explain as module
    monkeypatch.setattr(module, "_disabled_logged", False)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    with caplog.at_level("INFO", logger="kairos.explain"):
        assert await explain_signal(db_pool, signal_id) is None
        assert await explain_signal(db_pool, signal_id) is None
    assert sum("disabled" in record.message for record in caplog.records) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls") == 0


async def test_daily_limit(db_pool, signal_id, settings):
    settings.explain_daily_limit = 1
    await db_pool.execute("INSERT INTO api_calls (ts, provider, endpoint, ok) VALUES ($1, 'llm', 'test', false)", NOW)
    provider = FakeProvider()
    assert await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW) is None
    assert provider.calls == []


async def test_daily_limit_holds_under_parallel_calls(db_pool, features, settings):
    """Слот дневного лимита резервируется до вызова модели: параллельные /why по разным сигналам не превышают лимит,
    а строка-резерв после ответа дописывается результатом (одна строка на вызов, пометка — signal:<id>)."""
    import asyncio
    settings.explain_daily_limit = 2
    ids = []
    for i in range(4):
        token = await db_pool.fetchval("INSERT INTO tokens (mint, source) VALUES ($1, 'test') RETURNING id", f"mint{i}")
        ids.append(await db_pool.fetchval(
            """INSERT INTO signals (token_id, score, confidence, scorer_version, features, n_shillers)
               VALUES ($1, $2, 'medium', 's1', $3, 3) RETURNING id""", token, 60 + i, json.dumps(features)))
    started = asyncio.Event()

    class Slow(FakeProvider):
        async def complete(self, system, prompt):
            started.set()
            await asyncio.sleep(0.2)          # все четыре вызова встречаются внутри провайдера
            return await super().complete(system, prompt)

    provider = Slow()
    results = await asyncio.gather(*(explain_signal(db_pool, sid, provider=provider, settings=settings, now=NOW) for sid in ids))
    assert sorted(r is not None for r in results) == [False, False, True, True]
    assert len(provider.calls) == 2
    rows = await db_pool.fetch("SELECT * FROM api_calls ORDER BY note")
    assert len(rows) == 2 and all(r["ok"] and r["http_status"] == 200 and r["note"].startswith("signal:") for r in rows)
    assert all("reservation" not in r["note"] for r in rows)
    assert await db_pool.fetchval("SELECT count(*) FROM signal_explanations") == 2


async def test_daily_limit_utc_boundaries(db_pool, signal_id, settings):
    settings.explain_daily_limit = 1
    start = NOW.replace(hour=0)
    for ts, name in [(start - timedelta(microseconds=1), "llm"),
                     (start + timedelta(days=1), "llm"), (NOW, "other")]:
        await db_pool.execute("INSERT INTO api_calls (ts, provider, endpoint, ok) VALUES ($1, $2, 'test', true)", ts, name)
    local_now = NOW.astimezone(timezone(timedelta(hours=4)))
    assert await explain_signal(db_pool, signal_id, provider=FakeProvider(), settings=settings, now=local_now) == TEXT


@pytest.mark.parametrize("provider", [FakeProvider("", refused=True), FakeProvider("You should buy now."), FakeProvider(" " * 5), FakeProvider("x" * 701)])
async def test_rejected_result(db_pool, signal_id, settings, provider):
    assert await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW) is None
    assert await db_pool.fetchval("SELECT count(*) FROM signal_explanations") == 0
    assert await db_pool.fetchval("SELECT explanation FROM signals WHERE id = $1", signal_id) is None
    assert await db_pool.fetchval("SELECT count(*) FROM api_calls") == 1


@pytest.mark.parametrize("kind", ["connection", "status", "network"])
async def test_provider_exception(db_pool, signal_id, settings, kind):
    request = httpx.Request("POST", "https://offline.invalid")
    error = {
        "connection": anthropic.APIConnectionError(request=request),
        "status": anthropic.APIStatusError("offline", response=httpx.Response(503, request=request), body=None),
        "network": httpx.ConnectError("offline"),
    }[kind]
    assert await explain_signal(db_pool, signal_id, provider=FakeProvider(error=error), settings=settings, now=NOW) is None
    row = await db_pool.fetchrow("SELECT * FROM api_calls")
    assert row["ok"] is False
    assert row["credits"] == 0
    assert row["http_status"] == (503 if kind == "status" else None)
    assert row["note"] == f"signal:{signal_id}"
    assert await db_pool.fetchval("SELECT count(*) FROM signal_explanations") == 0


async def test_cost_and_signal_update(db_pool, signal_id, settings):
    assert await explain_signal(db_pool, signal_id, provider=FakeProvider(), settings=settings, now=NOW) == TEXT
    row = await db_pool.fetchrow("SELECT * FROM api_calls")
    assert row["provider"] == "llm"
    assert row["endpoint"] == "claude-sonnet-5-5"
    assert row["credits"] == Decimal("0.003")
    assert row["http_status"] == 200 and row["ok"] is True
    assert row["note"] == f"signal:{signal_id}"
    cached = await db_pool.fetchrow("SELECT * FROM signal_explanations")
    assert cached["cost_usd"] == row["credits"]
    assert cached["input_tokens"] == 1000 and cached["output_tokens"] == 100
    assert cached["provider"] == "fake"
    assert await db_pool.fetchval("SELECT explanation FROM signals WHERE id = $1", signal_id) == TEXT


async def test_unknown_signal(db_pool, settings):
    provider = FakeProvider()
    assert await explain_signal(db_pool, 999, provider=provider, settings=settings) is None
    assert not provider.calls


async def test_settings_construct_provider(db_pool, signal_id, settings, monkeypatch):
    settings.anthropic_api_key = SecretStr("offline-key")
    import kairos.explain as module
    fake = FakeProvider()
    def factory(api_key, model):
        assert api_key == "offline-key" and model == settings.explain_model
        return fake
    monkeypatch.setattr(module, "AnthropicProvider", factory)
    assert await explain_signal(db_pool, signal_id, settings=settings, now=NOW) == TEXT
    assert len(fake.calls) == 1


def test_facts_allowlist_and_hash(features):
    facts = build_facts(features)
    dumped = json.dumps(facts)
    for forbidden in ("mint", "wallet", "token_id", "secret-", "evaluated_at", "peak_multiple", "success_lb"):
        assert forbidden not in dumped
    assert facts["age_minutes"] == 14.12
    assert facts["inside"][0]["success_rate"] == 0.42
    assert len(facts["similar"]) == 3
    def reverse(value):
        if isinstance(value, dict):
            return {key: reverse(item) for key, item in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reverse(item) for item in value]
        return value
    assert facts_hash(facts) == facts_hash(build_facts(reverse(features)))
    assert len(facts_hash(facts)) == 64
    assert features["inside"][0]["success_rate"] == 0.4167


def test_prompt_constant(features):
    system, prompt = build_prompt(build_facts(features), Decimal("65"), "medium")
    other, _ = build_prompt({}, 0, "low")
    assert system == other
    assert "Never advise buying, selling or holding" in system
    assert "80 words" in system and "never predict price" in system
    payload = json.loads(prompt)
    assert payload["score"] == 65 and payload["confidence"] == "medium"
    assert payload["facts"] == build_facts(features)


@pytest.mark.parametrize("text", ["", "   ", "x" * 701, "BUY NOW", "Not financial advice.",
    "We recommend this.", "A guaranteed result.", "It will pump.", "It will x10.",
    "Price target: 5.", "You should hold.", "Ape in!", "The price will rise."])
def test_check_text_rejects(text):
    assert not check_text(text)


def test_check_text_accepts():
    assert check_text(TEXT)
    assert check_text("x" * 700)


@pytest.mark.parametrize("model, expected", [("claude-sonnet-5-5", 0.003), ("claude-haiku-5-5", 0.00015), ("unknown", 0.003)])
def test_estimate_cost(model, expected):
    assert estimate_cost_usd(model, 1000, 100) == pytest.approx(expected)


def test_fallback_full(features):
    text = fallback_explanation(features)
    assert "@alpha (5 of 12 past tokens succeeded)" in text
    assert "minute 3.5" in text and "2 more shillers" in text
    assert "creator's earlier tokens" in text and "3 of 4" in text
    assert check_text(text)
    assert text == fallback_explanation(features)


@pytest.mark.parametrize("features", [{}, {"token": None, "inside": None, "creator": None, "evidence": None, "risks": None}])
def test_fallback_minimal(features):
    text = fallback_explanation(features)
    assert "Evidence is thin." in text
    assert check_text(text)


@pytest.mark.parametrize("code, phrase", [
    ("creator_rugged_before", "creator's earlier tokens"),
    ("shiller_rug_history", "shillers have a history"),
    ("single_shiller", "only one shiller"), ("low_evidence", "evidence is thin"),
    ("late_entry", "60 minutes"), ("unknown_creation", "creation time is unknown"),
])
def test_fallback_risk_mapping(code, phrase):
    assert phrase in fallback_explanation({"risks": [code]})


@pytest.mark.parametrize("refused", [False, True])
async def test_anthropic_stub(monkeypatch, refused):
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="refusal" if refused else "end_turn", model="claude-sonnet-5-5",
            content=[SimpleNamespace(type="text", text="First. "), SimpleNamespace(type="other"),
                     SimpleNamespace(type="text", text="Second.")],
            usage=SimpleNamespace(input_tokens=200, output_tokens=30),
        )
    constructor_calls, closed = [], []

    class FakeClient:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)
            self.messages = SimpleNamespace(create=create)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            closed.append(True)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)
    provider = AnthropicProvider("offline-key", "claude-sonnet-5-5")
    result = await provider.complete("system", "prompt")
    assert constructor_calls == [{"api_key": "offline-key", "max_retries": 2, "timeout": 30.0}]
    assert closed == [True]  # клиент закрывается после вызова
    assert result.text == ("" if refused else "First. Second.")
    assert result.refused is refused
    assert (result.input_tokens, result.output_tokens, result.model) == (200, 30, provider.model)
    assert calls == [{
        "model": provider.model, "max_tokens": 600, "system": "system",
        "messages": [{"role": "user", "content": "prompt"}], "output_config": {"effort": "low"},
        "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
        "extra_body": {"fallbacks": "default"},
    }]



async def test_cache_key_includes_score_and_confidence(db_pool, signal_id, settings):
    """Изменился скор или confidence при тех же фактах — объяснение пересчитывается."""
    provider = FakeProvider()
    assert await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW)
    await db_pool.execute("UPDATE signals SET score = score + 5 WHERE id = $1", signal_id)
    assert await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW)
    await db_pool.execute("UPDATE signals SET confidence = 'high' WHERE id = $1", signal_id)
    assert await explain_signal(db_pool, signal_id, provider=provider, settings=settings, now=NOW)
    assert len(provider.calls) == 3


def test_fallback_uses_documented_evidence_keys():
    features = {"inside": [{"username": "alpha", "minute": 3, "n_final": 40, "success": 12}], "n_shillers": 1,
                "evidence": {"best_shiller_n_final": 40, "shillers_with_10plus": 2}, "risks": []}
    assert "Evidence is thin" not in fallback_explanation(features)
    features["evidence"]["best_shiller_n_final"] = 6
    assert "Evidence is thin" in fallback_explanation(features)
