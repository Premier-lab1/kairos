"""Point-in-time s1 scoring. No network calls in the pure scoring core."""
from __future__ import annotations

import argparse
import asyncio
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import heapq
import logging
import math
from statistics import median
import sys

import asyncpg

from kairos.config import get_settings
from kairos.report import wilson_lower_bound

SCORER_VERSION = "s1"
FINAL = frozenset(("success", "rug", "dead", "neutral"))
DAY = timedelta(hours=24)
log = logging.getLogger(__name__)

# Поведение шиллера на цепи (kol_trades; docs/signals.md, раздел «Кошельки шиллеров»). Позиция = кошелёк + mint
# от первой покупки до первой продажи после неё — как во view kol_positions, но на момент t.
FAST_DUMP_S = 60                   # позиция короче минуты — «слив»
FAST_DUMPER_MIN_CLOSED = 3         # с этого числа закрытых позиций медиана что-то значит
FAST_DUMPER_MEDIAN_S = 120         # медиана удержания ниже — шиллер-«дампер»
SOLD_AFTER_CALL = timedelta(minutes=30)
KOL_HOLDING_POINTS, KOL_HOLDING_CAP = 4, 8
KOL_FAST_DUMPER_POINTS, KOL_FAST_DUMPER_CAP = -6, -12
KOL_SOLD_AFTER_CALL_POINTS = -10


def wilson_interval(successes, n, z=1.96):
    if not n:
        return (0.0, 1.0)
    p, z2 = successes / n, z * z
    center = p + z2 / (2 * n)
    radius = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    low = wilson_lower_bound(successes, n) if z == 1.96 else max(0.0, (center - radius) / (1 + z2 / n))
    return low, min(1.0, (center + radius) / (1 + z2 / n))


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a or b else 0.0


def similarity(a, b, minute_a=None, minute_b=None):
    minute_sim = 0.5 if minute_a is None or minute_b is None else 1 - min(abs(minute_a - minute_b) / 60, 1)
    return 0.8 * jaccard(a, b) + 0.2 * minute_sim


def entry_points(minute):
    """Entry is exposed for calibration, but has zero weight in s1."""
    return 0.0


def confidence_for(evidence):
    n = evidence["best_shiller_n_final"]
    if n >= 30 and evidence["shillers_with_10plus"] >= 2:
        return "high"
    return "medium" if n >= 10 else "low"


def risks_for(creator, inside, confidence, entry_minute, created_at):
    risks = []
    if creator["past_rug"]:
        risks.append("creator_rugged_before")
    if any(s["n_final"] >= 5 and s["rug_lift"] >= 1.5 for s in inside):
        risks.append("shiller_rug_history")
    if len(inside) == 1:
        risks.append("single_shiller")
    if confidence == "low":
        risks.append("low_evidence")
    if entry_minute is not None and entry_minute > 60:
        risks.append("late_entry")
    if created_at is None:
        risks.append("unknown_creation")
    # Поведение на цепи: только у шиллеров с данными кошельков (onchain не None).
    onchain = [s["onchain"] for s in inside if s.get("onchain")]
    if any(o["fast_dumper"] for o in onchain):
        risks.append("kol_fast_dumper")
    if any(o["sold_after_call"] for o in onchain):
        risks.append("kol_sold_after_call")
    if any(o["bought_before_call"] for o in onchain):
        risks.append("kol_bought_before_call")
    return risks


def kol_onchain_points(inside):
    """Баллы за поведение шиллеров на цепи; без данных кошельков — нули (скор не меняется)."""
    onchain = [s["onchain"] for s in inside if s.get("onchain")]
    holding = sum(o["holding"] for o in onchain)
    dumpers = sum(o["fast_dumper"] for o in onchain)
    return dict(kol_holding=float(min(KOL_HOLDING_POINTS * holding, KOL_HOLDING_CAP)),
                kol_fast_dumpers=float(max(KOL_FAST_DUMPER_POINTS * dumpers, KOL_FAST_DUMPER_CAP)),
                kol_sold_after_call=float(KOL_SOLD_AFTER_CALL_POINTS) if any(o["sold_after_call"] for o in onchain) else 0.0)


class _Counts:
    def __init__(self, events):
        events.sort(key=lambda e: (e[0], e[1]))
        self.times, self.s, self.r = [], [0], [0]
        for time, _, label in events:
            self.times.append(time)
            self.s.append(self.s[-1] + (label == "success"))
            self.r.append(self.r[-1] + (label == "rug"))

    def at(self, t):
        n = bisect_right(self.times, t)
        return n, self.s[n], self.r[n]


class History:
    """Immutable-by-convention snapshot; constructor accepts small lists of dicts."""
    def __init__(self, tokens=(), kols=(), mentions=(), outcomes=(), kol_trades=(), *, rule_version="v2", horizon=DAY):
        self.rule_version, self.horizon = rule_version, horizon
        self._index_trades(kol_trades)
        self.tokens = {r["id"]: dict(r) for r in tokens}
        self.kols = {r["id"]: dict(r) for r in kols}
        self.mentions = [dict(r) for r in mentions if r.get("token_id") is not None]
        self.by_token = defaultdict(list)
        self.by_creator = defaultdict(list)
        self.kol_tokens = defaultdict(list)
        self.first = {}
        self.kol_first = defaultdict(dict)
        for m in self.mentions:
            self.by_token[m["token_id"]].append((m["tweet_time"], m["kol_id"]))
        self.shiller_sets, self.mention_times = {}, {}
        for token_id, rows in self.by_token.items():
            rows.sort()
            self.first[token_id] = rows[0][0]
            self.mention_times[token_id] = [r[0] for r in rows]
            self.shiller_sets[token_id] = {k for tm, k in rows if tm <= rows[0][0] + DAY}
            for tm, k in rows:
                self.kol_first[k].setdefault(token_id, tm)
        for k, token_times in self.kol_first.items():
            self.kol_tokens[k] = sorted((tm, tid) for tid, tm in token_times.items())
        self.kol_times = {k: [tm for tm, _ in rows] for k, rows in self.kol_tokens.items()}
        for token in self.tokens.values():
            if token.get("creator_wallet_id") is not None and token.get("created_at") is not None:
                self.by_creator[token["creator_wallet_id"]].append((token["created_at"], token["id"]))
        for rows in self.by_creator.values():
            rows.sort()
        self.creator_times = {k: [tm for tm, _ in rows] for k, rows in self.by_creator.items()}
        self.entry_minutes = {tid: (first - self.tokens[tid]["created_at"]).total_seconds()/60
                              if self.tokens[tid].get("created_at") else None for tid, first in self.first.items()}
        self.outcomes = {}
        for row in outcomes:
            r = dict(row)
            if r.get("rule_version", rule_version) == rule_version and r.get("horizon", horizon) == horizon:
                self.outcomes[r["token_id"], r["reference"]] = r
        self.final = {tid: o for (tid, ref), o in self.outcomes.items() if ref == "first_mention" and o["label"] in FINAL}
        self.base_counts = _Counts([(o["reference_time"] + horizon, tid, o["label"]) for tid, o in self.final.items()])
        self.kol_counts = {}
        for k, rows in self.kol_tokens.items():
            events = []
            for tm, tid in rows:
                o = self.final.get(tid)
                if o and tm <= self.first[tid] + timedelta(minutes=60):
                    events.append((max(tm, o["reference_time"] + horizon), tid, o["label"]))
            self.kol_counts[k] = _Counts(events)

    def shillers_at(self, token_id, t, *, past=False):
        rows = self.by_token.get(token_id, [])
        if not rows:
            return set()
        cutoff = min(t, rows[0][0] + DAY) if past else t
        if past and cutoff >= rows[0][0] + DAY:
            return self.shiller_sets[token_id]
        return {k for _, k in rows[:bisect_right(self.mention_times[token_id], cutoff)]}

    # ───────────── сделки кошельков шиллеров (kol_trades) ─────────────

    def _index_trades(self, kol_trades):
        """Сделки по шиллерам: все времена сделок (есть ли данные на момент t), сделки по (шиллер, mint)
        и позиции (кошелёк + mint: первая покупка → первая продажа после неё), закрытые — по времени продажи."""
        self.kol_trade_times = defaultdict(list)
        self.kol_mint_trades = defaultdict(list)
        buys, sells = {}, defaultdict(list)
        for r in kol_trades:
            k, ts, side = r["kol_id"], r["ts"], r["side"]
            self.kol_trade_times[k].append(ts)
            self.kol_mint_trades[k, r["mint"]].append((ts, side))
            key = (k, r["wallet_id"], r["mint"])
            if side == "buy":
                buys[key] = min(buys[key], ts) if key in buys else ts
            elif side == "sell":
                sells[key].append(ts)
        for times in self.kol_trade_times.values():
            times.sort()
        for trades in self.kol_mint_trades.values():
            trades.sort()
        closed = defaultdict(list)
        for key, first_buy in buys.items():
            first_sell = min((ts for ts in sells.get(key, ()) if ts >= first_buy), default=None)
            if first_sell is not None:
                closed[key[0]].append((first_sell, (first_sell - first_buy).total_seconds()))
        self.kol_closed_sells, self.kol_closed_holds = {}, {}
        for k, rows in closed.items():
            rows.sort()
            self.kol_closed_sells[k] = [sell for sell, _ in rows]
            self.kol_closed_holds[k] = [hold for _, hold in rows]

    def kol_onchain(self, k, mint, call, t):
        """Поведение шиллера ``k`` на цепи на момент ``t`` (только сделки с ts ≤ t): статистика удержания по всем
        его позициям и что он делал с токеном ``mint`` относительно своего первого упоминания ``call``.
        None — сделок до ``t`` у шиллера нет (кошельки не найдены или ещё не опрошены): скор не меняется."""
        if not bisect_right(self.kol_trade_times.get(k, []), t):
            return None
        n = bisect_right(self.kol_closed_sells.get(k, []), t)
        holds = self.kol_closed_holds[k][:n] if n else []
        median_hold = median(holds) if holds else None
        trades = self.kol_mint_trades.get((k, mint), [])
        trades = trades[:bisect_right(trades, (t, "\x7f"))]
        first_buy = next((ts for ts, side in trades if side == "buy"), None)
        first_sell = None if first_buy is None else next((ts for ts, side in trades if side == "sell" and ts >= first_buy), None)
        sold_after_call = any(side == "sell" and call <= ts <= call + SOLD_AFTER_CALL for ts, side in trades)
        return dict(closed_positions=n, median_hold_s=median_hold,
                    fast_dump_share=sum(h < FAST_DUMP_S for h in holds) / n if n else None,
                    fast_dumper=n >= FAST_DUMPER_MIN_CLOSED and median_hold < FAST_DUMPER_MEDIAN_S,
                    first_buy_at=first_buy, first_sell_at=first_sell,
                    # Держит — если его последняя сделка по токену к t была покупкой (докупил после продажи — снова
                    # держит; продал часть и докупил — держит). Позиции и медиана при этом как во view kol_positions.
                    holding=bool(trades) and trades[-1][1] == "buy",
                    bought_before_call=first_buy is not None and first_buy < call,
                    sold_after_call=sold_after_call)


async def load_history(db, rule_version, horizon=DAY):
    """Снимок истории. С пулом все запросы идут в одной транзакции REPEATABLE READ на одном соединении:
    иначе сборщик может вставить токен и его упоминание между запросами, и упоминание сошлётся на токен,
    которого нет в снимке."""
    if isinstance(db, asyncpg.Pool):
        async with db.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                return await load_history(conn, rule_version, horizon)
    tokens = await db.fetch("""SELECT t.id, t.mint, t.symbol, t.name, t.created_at,
        t.creator_wallet_id, w.address AS creator_address FROM tokens t
        LEFT JOIN wallets w ON w.id=t.creator_wallet_id""")
    kols = await db.fetch("SELECT id, username, followers_count FROM kols")
    mentions = await db.fetch("""SELECT kol_id, token_id, tweet_time, first_seen_at, source
        FROM mentions WHERE token_id IS NOT NULL""")
    outcomes = await db.fetch("""SELECT token_id, reference, reference_time, label, peak_multiple
        FROM token_outcomes WHERE rule_version=$1 AND horizon=$2
        AND reference IN ('first_mention', 'creation')""", rule_version, horizon)
    # Сделки кошельков шиллеров одним запросом; токен сопоставляется по mint (token_id в kol_trades бывает NULL).
    kol_trades = await db.fetch("SELECT kol_id, wallet_id, mint, ts, side FROM kol_trades")
    return History(tokens, kols, mentions, outcomes, kol_trades, rule_version=rule_version, horizon=horizon)


@dataclass
class ScoreResult:
    score: float
    confidence: str
    features: dict
    n_shillers: int
    first_mention_at: datetime


def _json_values(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: _json_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_values(v) for v in value]
    return value


def score_token(hist, token_id, t, *, rule_version, horizon):
    if (rule_version, horizon) != (hist.rule_version, hist.horizon):
        raise ValueError("History rule version/horizon mismatch")
    first = hist.first.get(token_id)
    if first is None or first > t:
        return None
    token = hist.tokens[token_id]
    created = token.get("created_at")
    entry = (first - created).total_seconds() / 60 if created else None
    shillers = hist.shillers_at(token_id, t)
    n, successes, rugs = hist.base_counts.at(t)
    own = hist.final.get(token_id)
    own_usable = own is not None and own["reference_time"] + horizon <= t
    if own_usable:
        n, successes, rugs = n - 1, successes - (own["label"] == "success"), rugs - (own["label"] == "rug")
    b, br = (successes / n, rugs / n) if n >= 100 else (0.05, 0.3)
    inside = []
    candidates = set()
    for k in sorted(shillers):
        kn, ks, kr = hist.kol_counts[k].at(t)
        if own_usable and hist.kol_first[k][token_id] <= first + timedelta(minutes=60):
            kn, ks, kr = kn - 1, ks - (own["label"] == "success"), kr - (own["label"] == "rug")
        # A zero empirical base implies zero corresponding counts; define lift as 1.
        lift = ((ks + 10 * b) / (kn + 10) / b) if b else 1.0
        rug_lift = ((kr + 10 * br) / (kn + 10) / br) if br else 1.0
        kol = hist.kols.get(k, {})
        tm = hist.kol_first[k][token_id]
        inside.append(dict(kol_id=k, username=kol.get("username"), followers=kol.get("followers_count"),
                           first_mention_at=tm, minute=(tm - (created or first)).total_seconds() / 60,
                           n_final=kn, success=ks, rug=kr, success_rate=ks / kn if kn else 0.0,
                           success_lb=wilson_lower_bound(ks, kn), lift=lift,
                           rug_rate=kr / kn if kn else 0.0, rug_lb=wilson_lower_bound(kr, kn), rug_lift=rug_lift,
                           onchain=hist.kol_onchain(k, token["mint"], tm, t)))
        rows = hist.kol_tokens[k]
        candidates.update(tid for _, tid in rows[:bisect_right(hist.kol_times[k], t)])
    inside.sort(key=lambda s: (-s["lift"], s["kol_id"]))
    creator = dict(wallet=token.get("creator_address"), past_final=0, past_rug=0)
    wallet_id = token.get("creator_wallet_id")
    # Без created_at порядок токенов креатора неизвестен: историю креатора не засчитываем.
    if wallet_id is not None and created is not None:
        rows = hist.by_creator.get(wallet_id, [])
        end = bisect_left(hist.creator_times.get(wallet_id, []), created)
        for _, tid in rows[:end]:
            o = hist.outcomes.get((tid, "creation"))
            if tid != token_id and o and o["label"] in FINAL and o["reference_time"] + horizon <= t:
                creator["past_final"] += 1
                creator["past_rug"] += o["label"] == "rug"
    similar_heap = []
    for tid in candidates - {token_id}:
        o = hist.final.get(tid)
        if not o or o["reference_time"] + horizon > t:
            continue
        other_entry = hist.entry_minutes[tid]
        others = hist.shillers_at(tid, t, past=True)
        intersection = len(shillers & others)
        if not intersection:
            continue
        jac = intersection / (len(shillers) + len(others) - intersection)
        minute_sim = .5 if entry is None or other_entry is None else 1 - min(abs(entry-other_entry)/60, 1)
        sim = .8 * jac + .2 * minute_sim
        if jac > 0 and sim >= 0.2:
            item = (sim, -tid, jac)
            if len(similar_heap) < 5:
                heapq.heappush(similar_heap, item)
            elif item > similar_heap[0]:
                heapq.heapreplace(similar_heap, item)
    similar = []
    for sim, negative_tid, jac in sorted(similar_heap, reverse=True):
        tid = -negative_tid
        other, o = hist.tokens[tid], hist.final[tid]
        similar.append(dict(token_id=tid, mint=other["mint"], symbol=other.get("symbol"),
                            similarity=sim, jaccard=jac, label=o["label"],
                            peak_multiple=float(o["peak_multiple"]) if o.get("peak_multiple") is not None else None))
    eligible = [s for s in inside if s["n_final"] >= 5]
    best = eligible[0] if eligible else None
    evidence = dict(best_shiller_n_final=best["n_final"] if best else 0,
                    shillers_with_10plus=sum(s["n_final"] >= 10 for s in inside), similar_cases=len(similar),
                    shillers_with_onchain=sum(s["onchain"] is not None for s in inside),
                    shillers_holding=sum(bool(s["onchain"] and s["onchain"]["holding"]) for s in inside))
    confidence = confidence_for(evidence)
    pf, pr = creator["past_final"], creator["past_rug"]
    components = dict(shillers=55 * min(max(((best["lift"] if best else 1) - 1) / 4, 0), 1),
                      breadth=20 * min(len(shillers) - 1, 4) / 4,
                      creator_clean=10.0 if pf >= 3 and pr == 0 else 0.0,
                      entry=entry_points(entry), similar=0.0,
                      rug_shillers=-30 * min(max(max((s["rug_lift"] for s in eligible), default=1) - 1, 0), 1),
                      creator_rug=(-25.0 if pr >= 2 or (pr and pr / pf >= 0.5) else -15.0 if pr else 0.0), similar_rug=0.0,
                      **kol_onchain_points(inside))
    features = dict(scorer_version=SCORER_VERSION, evaluated_at=t, outcome_rule_version=rule_version,
                    horizon=f"{horizon.total_seconds() / 3600:g} hours",
                    base=dict(success_rate=b, rug_rate=br, n_final=n, default_used=n < 100),
                    token=dict(token_id=token_id, mint=token["mint"], symbol=token.get("symbol"), name=token.get("name"),
                               created_at=created, first_mention_at=first, age_minutes=(t-first).total_seconds()/60, entry_minute=entry),
                    inside=inside, n_shillers=len(shillers), creator=creator, similar=similar,
                    components=components, evidence=evidence,
                    risks=risks_for(creator, inside, confidence, entry, created))
    return ScoreResult(round(min(max(sum(components.values()), 0), 100), 1), confidence,
                       _json_values(features), len(shillers), first)


async def upsert_signal(conn, token_id, result, now):
    await conn.execute("""INSERT INTO signals
        (token_id, scorer_version, score, confidence, features, n_shillers, first_mention_at, created_at, updated_at)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$8)
        ON CONFLICT (token_id, scorer_version) DO UPDATE SET score=EXCLUDED.score,
        confidence=EXCLUDED.confidence, features=EXCLUDED.features, n_shillers=EXCLUDED.n_shillers,
        first_mention_at=EXCLUDED.first_mention_at, updated_at=EXCLUDED.updated_at""",
        token_id, SCORER_VERSION, result.score, result.confidence, json.dumps(result.features),
        result.n_shillers, result.first_mention_at, now)


async def score_due_tokens(pool, now=None, settings=None):
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    # Пересчёт нужен, если сигнала ещё нет, появились упоминания после прошлого расчёта или в базу попали
    # сделки кошельков шиллеров по этому токену (kol_trades.inserted_at — время записи, а не сделки: кошельки
    # опрашиваются раз в час; токен ищется по token_id или по mint, пока token_id не проставлен).
    due = await pool.fetch("""SELECT m.token_id FROM mentions m
        JOIN tokens t ON t.id=m.token_id
        LEFT JOIN signals s ON s.token_id=m.token_id AND s.scorer_version=$1
        WHERE m.token_id IS NOT NULL AND m.tweet_time <= $2
        GROUP BY m.token_id, t.mint, s.id, s.updated_at
        HAVING min(m.tweet_time) >= $3 AND (s.id IS NULL OR max(m.first_seen_at) > s.updated_at
            OR EXISTS (SELECT 1 FROM kol_trades kt WHERE (kt.token_id=m.token_id OR kt.mint=t.mint)
                       AND kt.inserted_at > s.updated_at))
        ORDER BY m.token_id""", SCORER_VERSION, now, now-timedelta(hours=settings.signal_max_age_hours))
    if not due:
        return 0
    hist = await load_history(pool, settings.signal_outcome_rule_version)
    count = 0
    for row in due:
        try:
            result = score_token(hist, row["token_id"], now, rule_version=settings.signal_outcome_rule_version, horizon=DAY)
            if result is not None:
                await upsert_signal(pool, row["token_id"], result, now)
                count += 1
        except Exception:
            log.exception("Scoring token %s failed", row["token_id"])
    return count


async def _main(args):
    pool = await asyncpg.create_pool(args.dsn or get_settings().database_url)
    try:
        print(f"Scored: {await score_due_tokens(pool)}")
        for r in await pool.fetch("""SELECT t.symbol,s.score,s.confidence,s.n_shillers FROM signals s
            JOIN tokens t ON t.id=s.token_id WHERE scorer_version=$1 ORDER BY score DESC,token_id LIMIT 10""", SCORER_VERSION):
            print(r["symbol"], r["score"], r["confidence"], r["n_shillers"])
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn")
    parser.add_argument("--once", action="store_true", help="Run one pass (also the default)")
    asyncio.run(_main(parser.parse_args()))
