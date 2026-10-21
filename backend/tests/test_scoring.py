import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import asyncpg
import pytest

from kairos.db import migrate
from kairos.report import wilson_lower_bound
from kairos.scoring import (DAY, History, confidence_for, entry_points, jaccard,
                            risks_for, score_due_tokens, score_token, similarity,
                            upsert_signal, wilson_interval)

T = datetime(2026, 10, 18, tzinfo=timezone.utc)
DSN = "postgresql://kairos@localhost:5434/kairos_test_scoring"


def token(i, created=None, creator=None):
    return dict(id=i, mint=f"mint{i}", symbol=f"T{i}", name=f"Token {i}",
                created_at=created, creator_wallet_id=creator, creator_address="wallet" if creator else None)


def mention(i, k=1, time=T):
    return dict(token_id=i, kol_id=k, tweet_time=time)


def outcome(i, label="success", time=T-DAY*2, reference="first_mention"):
    return dict(token_id=i, reference=reference, reference_time=time, label=label, peak_multiple=3.0)


def fixture_history(n=0, label="success", shillers=1, creator_labels=(), created=T):
    tokens = [token(0, created, 1)]
    mentions = [mention(0, k) for k in range(1, shillers+1)]
    outcomes = []
    for i in range(1, n+1):
        tokens.append(token(i, T-DAY*3))
        mentions.append(mention(i, 1, T-DAY*2))
        outcomes.append(outcome(i, label))
    for i, lab in enumerate(creator_labels, n+1):
        tokens.append(token(i, T-DAY*3, 1))
        outcomes.append(outcome(i, lab, T-DAY*3, "creation"))
    return History(tokens, [dict(id=k, username=f"k{k}", followers_count=100) for k in range(1, shillers+1)], mentions, outcomes)


def score(hist, t=T, tid=0):
    return score_token(hist, tid, t, rule_version="v2", horizon=DAY)


@pytest.mark.parametrize("s,n,low,high", [(0,0,0,1), (0,10,0,.2775401688), (5,10,.2365895936,.7634104064), (10,10,.7224598312,1)])
def test_wilson(s,n,low,high):
    a,b = wilson_interval(s,n)
    assert a == pytest.approx(low)
    assert b == pytest.approx(high)
    assert a == wilson_lower_bound(s,n)


def test_wilson_custom_z():
    assert wilson_interval(5,10,z=1) == pytest.approx((.3492443277,.6507556723))


@pytest.mark.parametrize("a,b,expected", [(set(),set(),0), ({1},{1},1), ({1,2},{2,3},1/3), ({1},{2},0)])
def test_jaccard(a,b,expected):
    assert jaccard(a,b) == expected


def test_similarity():
    assert similarity({1},{1},0,0) == 1
    assert similarity({1},{1},0,60) == .8
    assert similarity({1},{1}) == .9


@pytest.mark.parametrize("minute", [None,0,10,60,180,10000])
def test_entry_zero(minute):
    assert entry_points(minute) == 0


@pytest.mark.parametrize("n,others,expected", [(0,0,"low"),(5,0,"low"),(9,0,"low"),(10,1,"medium"),(30,1,"medium"),(30,2,"high")])
def test_confidence(n,others,expected):
    assert confidence_for(dict(best_shiller_n_final=n,shillers_with_10plus=others)) == expected


def test_cold_start_and_exact_contract():
    r = score(fixture_history(created=None))
    assert r.score == 0 and r.confidence == "low"
    f = r.features
    assert set(f) == {"scorer_version","evaluated_at","outcome_rule_version","horizon","base","token","inside","n_shillers","creator","similar","components","evidence","risks"}
    assert set(f["token"]) == {"token_id","mint","symbol","name","created_at","first_mention_at","age_minutes","entry_minute"}
    assert set(f["inside"][0]) == {"kol_id","username","followers","first_mention_at","minute","n_final","success","rug","success_rate","success_lb","lift","rug_rate","rug_lb","rug_lift","onchain"}
    assert set(f["base"]) == {"success_rate","rug_rate","n_final","default_used"}
    assert set(f["creator"]) == {"wallet","past_final","past_rug"}
    assert set(f["evidence"]) == {"best_shiller_n_final","shillers_with_10plus","similar_cases","shillers_with_onchain","shillers_holding"}
    assert set(f["components"]) == {"shillers","breadth","creator_clean","entry","similar","rug_shillers","creator_rug","similar_rug","kol_holding","kol_fast_dumpers","kol_sold_after_call"}
    assert f["inside"][0]["lift"] == 1
    assert f["inside"][0]["onchain"] is None   # сделок кошельков нет — поведение на цепи неизвестно
    assert f["evidence"]["shillers_with_onchain"] == 0
    assert f["risks"] == ["single_shiller","low_evidence","unknown_creation"]
    json.dumps(f, allow_nan=False)


def test_no_mention_yet():
    assert score(fixture_history(), T-timedelta(seconds=1)) is None
    assert score(History([token(0)])) is None


def test_no_future_outcome_and_own_outcome():
    hist = History([token(0),token(1)], [], [mention(0),mention(1,time=T-DAY)],
                   [outcome(0,time=T-DAY*2), outcome(1,time=T-DAY+timedelta(seconds=1))])
    r = score(hist)
    assert r.features["base"]["n_final"] == 0
    assert r.features["inside"][0]["n_final"] == 0
    assert score(hist,T+timedelta(seconds=1)).features["inside"][0]["n_final"] == 1


def test_future_mention_ignored_and_unique_tokens():
    hist = History([token(0),token(1)], [], [mention(0),mention(0,2,T+timedelta(seconds=1)),
                   mention(1,time=T-DAY*2),mention(1,time=T-DAY*2+timedelta(seconds=1))], [outcome(1)])
    r = score(hist)
    assert r.n_shillers == 1
    assert r.features["inside"][0]["n_final"] == 1


@pytest.mark.parametrize("minutes,count", [(60,1),(60.01,0)])
def test_attribution(minutes,count):
    hist = History([token(0),token(1)], [], [mention(0),mention(1,2,T-DAY*2),
                   mention(1,1,T-DAY*2+timedelta(minutes=minutes))], [outcome(1)])
    assert score(hist).features["inside"][0]["n_final"] == count


def test_base_known_and_default():
    for n in (99,100):
        hist = History([token(i) for i in range(n+1)], [], [mention(0)],
                       [outcome(i,"success" if i <= 20 else "rug") for i in range(1,n+1)])
        b = score(hist).features["base"]
        assert b["success_rate"] == (.05 if n==99 else .2)
        assert b["rug_rate"] == (.3 if n==99 else .8)
        assert b["default_used"] == (n==99)


@pytest.mark.parametrize("n,expected", [(0,0),(4,0),(5,55)])
def test_shiller_points_and_minimum(n,expected):
    assert score(fixture_history(n)).features["components"]["shillers"] == expected


@pytest.mark.parametrize("n,expected", [(1,0),(2,5),(5,20),(7,20)])
def test_breadth(n,expected):
    assert score(fixture_history(shillers=n)).features["components"]["breadth"] == expected


def test_rug_points_and_score_floor():
    r = score(fixture_history(20,"rug",creator_labels=["rug"]))
    assert r.features["components"]["rug_shillers"] == -30
    assert r.score == 0
    assert score(fixture_history()).features["components"]["rug_shillers"] == 0


def test_lift_one_boundaries():
    h = fixture_history(20,"neutral")
    outcomes = list(h.outcomes.values())
    outcomes[0]["label"] = "success"
    for o in outcomes[1:7]:
        o["label"] = "rug"
    h = History(list(h.tokens.values()),list(h.kols.values()),h.mentions,outcomes)
    r = score(h)
    assert r.features["inside"][0]["lift"] == 1
    assert r.features["inside"][0]["rug_lift"] == 1
    assert r.features["components"]["shillers"] == r.features["components"]["rug_shillers"] == 0


@pytest.mark.parametrize("labels,clean,penalty", [([],0,0),(["neutral"]*3,10,0),(["rug","neutral","dead"],0,-15),(["rug","dead"],0,-25),(["rug","rug","neutral","neutral","neutral"],0,-25)])
def test_creator_points(labels,clean,penalty):
    c = score(fixture_history(creator_labels=labels)).features["components"]
    assert c["creator_clean"] == clean and c["creator_rug"] == penalty


def test_creator_future_and_newer_excluded():
    h = History([token(0,T,1),token(1,T+timedelta(seconds=1),1),token(2,T-DAY/2,1)], [], [mention(0)],
                [outcome(1,"rug",T-DAY*2,"creation"), outcome(2,"rug",T-DAY/2,"creation")])
    assert score(h).features["creator"]["past_final"] == 0


def test_maximum_score_is_85_and_zero_components():
    r = score(fixture_history(10,shillers=5,creator_labels=["neutral"]*3))
    assert r.score == 85  # s1 positive weights total 85; the 100 clamp cannot bind.
    assert r.features["components"]["entry"] == r.features["components"]["similar"] == r.features["components"]["similar_rug"] == 0


def test_similar_limit_and_cutoff():
    h = fixture_history(10)
    r = score(h)
    assert len(r.features["similar"]) == 5
    assert all(s["similarity"] >= .2 for s in r.features["similar"])
    assert set(r.features["similar"][0]) == {"token_id","mint","symbol","similarity","jaccard","label","peak_multiple"}


def test_similar_future_shillers_and_24h_window():
    h = History([token(0),token(1)], [], [mention(0,1), mention(1,2,T-DAY*2),
                mention(1,1,T+timedelta(seconds=1))], [outcome(1)])
    assert score(h).features["similar"] == []
    assert h.shillers_at(1,T+DAY,past=True) == {2}


def test_risks():
    risks = risks_for(dict(past_rug=1), [dict(n_final=5,rug_lift=1.5)], "low",61,None)
    assert risks == ["creator_rugged_before","shiller_rug_history","single_shiller","low_evidence","late_entry","unknown_creation"]
    assert risks_for(dict(past_rug=0), [{"n_final":4,"rug_lift":2}]*2,"high",60,T) == []


def test_confidence_uses_best_lift_not_largest_sample():
    h = fixture_history(5)
    for i in range(100,140):
        h.tokens[i] = token(i)
    h = History(list(h.tokens.values()), [], h.mentions + [mention(0,2)] + [mention(i,2,T-DAY*2) for i in range(100,140)],
                list(h.outcomes.values()) + [outcome(i,"neutral") for i in range(100,140)])
    assert score(h).confidence == "low"


@pytest.fixture
async def scoring_db():
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await migrate(conn)
        yield conn
    finally:
        await conn.close()


async def seed(conn, *, age=0):
    k = await conn.fetchval("INSERT INTO kols(username) VALUES ('alpha') RETURNING id")
    tid = await conn.fetchval("INSERT INTO tokens(mint,source,created_at) VALUES ('mint','test',$1) RETURNING id",T-timedelta(hours=age))
    await conn.execute("""INSERT INTO mentions(kol_id,chain,ca,token_id,tweet_id,tweet_time,source,first_seen_at)
                          VALUES ($1,'SOLANA','mint',$2,'tweet',$3,'frontrun_search',$3)""",k,tid,T-timedelta(hours=age))
    return tid,k


async def test_upsert_same_row(scoring_db):
    tid,_ = await seed(scoring_db)
    r = score(fixture_history())
    await upsert_signal(scoring_db,tid,r,T)
    r.score = 42
    await upsert_signal(scoring_db,tid,r,T+timedelta(minutes=1))
    rows = await scoring_db.fetch("SELECT * FROM signals")
    assert len(rows)==1 and rows[0]["score"]==42
    assert rows[0]["created_at"]==T and rows[0]["updated_at"]==T+timedelta(minutes=1)


async def test_due_age_and_new_mentions(scoring_db):
    tid,k = await seed(scoring_db)
    settings = SimpleNamespace(signal_max_age_hours=6,signal_outcome_rule_version="v2")
    assert await score_due_tokens(scoring_db,T,settings)==1
    assert await score_due_tokens(scoring_db,T+timedelta(minutes=1),settings)==0
    await scoring_db.execute("""INSERT INTO mentions(kol_id,chain,ca,token_id,tweet_id,tweet_time,source,first_seen_at)
        VALUES ($1,'SOLANA','mint',$2,'new',$3,'frontrun_search',$3)""",k,tid,T+timedelta(minutes=2))
    assert await score_due_tokens(scoring_db,T+timedelta(minutes=2),settings)==1
    await scoring_db.execute("UPDATE mentions SET first_seen_at=$1",T+timedelta(hours=7))
    assert await score_due_tokens(scoring_db,T+timedelta(hours=7),settings)==0


async def test_failing_token_does_not_stop(scoring_db,monkeypatch):
    import kairos.scoring as module
    await seed(scoring_db)
    original = module.score_token
    def fail(*args,**kwargs):
        raise RuntimeError("isolated failure")
    monkeypatch.setattr(module,"score_token",fail)
    assert await score_due_tokens(scoring_db,T)==0
    monkeypatch.setattr(module,"score_token",original)
    assert await score_due_tokens(scoring_db,T)==1


# ───────────── кошельки шиллеров (kol_trades) ─────────────

M = timedelta(minutes=1)
S = timedelta(seconds=1)


def trade(k, mint, ts, side, wallet=1):
    return dict(kol_id=k, wallet_id=wallet, mint=mint, ts=ts, side=side)


def with_trades(trades, **kw):
    h = fixture_history(**kw)
    return History(list(h.tokens.values()), list(h.kols.values()), h.mentions, list(h.outcomes.values()), trades)


def position(k, i, hold_s, sold_at=T-DAY, wallet=1):
    """Закрытая позиция шиллера k по чужому токену: покупка за hold_s секунд до продажи sold_at."""
    return [trade(k, f"other{i}", sold_at - timedelta(seconds=hold_s), "buy", wallet), trade(k, f"other{i}", sold_at, "sell", wallet)]


def test_onchain_absent_leaves_score_unchanged():
    """Сделки есть только у постороннего шиллера: у шиллера токена onchain = None, компоненты нулевые, скор тот же."""
    plain = score(fixture_history(10, shillers=2))
    r = score(with_trades([trade(9, "mint0", T-M, "buy"), *position(9, 1, 5)], n=10, shillers=2))
    assert all(s["onchain"] is None for s in r.features["inside"])
    assert r.features["components"]["kol_holding"] == r.features["components"]["kol_fast_dumpers"] == r.features["components"]["kol_sold_after_call"] == 0
    assert r.score == plain.score and r.features["risks"] == plain.features["risks"]
    assert r.features["evidence"]["shillers_with_onchain"] == 0


def test_onchain_holding_point_in_time():
    h = with_trades([trade(1, "mint0", T+M, "buy")])
    assert score(h, T+30*S).features["inside"][0]["onchain"] is None            # покупки ещё не было
    r = score(h, T+10*M)
    o = r.features["inside"][0]["onchain"]
    assert o == dict(closed_positions=0, median_hold_s=None, fast_dump_share=None, fast_dumper=False,
                     first_buy_at=(T+M).isoformat(), first_sell_at=None, holding=True,
                     bought_before_call=False, sold_after_call=False)
    assert r.features["components"]["kol_holding"] == 4 and r.score == 4
    assert r.features["evidence"] == dict(best_shiller_n_final=0, shillers_with_10plus=0, similar_cases=0,
                                          shillers_with_onchain=1, shillers_holding=1)
    assert not any(code.startswith("kol_") for code in r.features["risks"])


def test_onchain_sold_after_call_and_bought_before():
    h = with_trades([trade(1, "mint0", T-5*M, "buy"), trade(1, "mint0", T+10*M, "sell")])
    before_sell = score(h, T+5*M).features["inside"][0]["onchain"]
    assert before_sell["holding"] and before_sell["bought_before_call"] and not before_sell["sold_after_call"]
    assert score(h, T+5*M).features["components"]["kol_holding"] == 4
    r = score(h, T+timedelta(hours=1))
    o = r.features["inside"][0]["onchain"]
    assert o["sold_after_call"] and o["bought_before_call"] and not o["holding"]
    assert o["first_sell_at"] == (T+10*M).isoformat() and o["closed_positions"] == 1 and o["median_hold_s"] == 900
    assert o["fast_dump_share"] == 0 and not o["fast_dumper"]                   # одна позиция — мало для «дампера»
    assert r.features["components"]["kol_sold_after_call"] == -10 and r.features["components"]["kol_holding"] == 0
    assert r.score == 0
    assert r.features["risks"][-2:] == ["kol_sold_after_call", "kol_bought_before_call"]


@pytest.mark.parametrize("sell_offset,flag", [(30*M, True), (30*M+S, False), (-S, False)])
def test_sold_after_call_window(sell_offset, flag):
    """Окно — 30 минут от первого упоминания включительно; продажа до поста — не «слив на подписчиков»."""
    h = with_trades([trade(1, "mint0", T+sell_offset, "sell")])
    o = score(h, T+timedelta(hours=1)).features["inside"][0]["onchain"]
    assert o["sold_after_call"] is flag and not o["holding"] and o["first_buy_at"] is None


def test_fast_dumper_threshold_median_and_point_in_time():
    dumper = with_trades([*position(1, 1, 20), *position(1, 2, 30), *position(1, 3, 500)])
    o = score(dumper).features["inside"][0]["onchain"]
    assert o["closed_positions"] == 3 and o["median_hold_s"] == 30 and o["fast_dump_share"] == 0.6667
    assert o["fast_dumper"]
    r = score(dumper)
    assert r.features["components"]["kol_fast_dumpers"] == -6 and "kol_fast_dumper" in r.features["risks"]
    assert r.features["risks"] == ["single_shiller", "low_evidence", "kol_fast_dumper"]
    # Две закрытые позиции — ещё не статистика; медиана ≥ 120 с — не дампер.
    assert not score(with_trades([*position(1, 1, 20), *position(1, 2, 30)])).features["inside"][0]["onchain"]["fast_dumper"]
    slow = score(with_trades([*position(1, 1, 20), *position(1, 2, 120), *position(1, 3, 500)])).features["inside"][0]["onchain"]
    assert slow["median_hold_s"] == 120 and not slow["fast_dumper"] and slow["fast_dump_share"] == 0.3333
    # Третья позиция закрыта после t: на момент t закрытых две.
    late = with_trades([*position(1, 1, 20), *position(1, 2, 30), *position(1, 3, 10, sold_at=T+M)])
    assert score(late).features["inside"][0]["onchain"]["closed_positions"] == 2
    assert score(late, T+M).features["inside"][0]["onchain"]["closed_positions"] == 3


def test_positions_follow_view_semantics():
    """Позиция = кошелёк + mint от первой покупки до первой продажи после неё: продажа до покупки не закрывает,
    вторая продажа не учитывается, кошельки считаются раздельно."""
    h = with_trades([trade(1, "a", T-DAY-10*S, "sell"), trade(1, "a", T-DAY, "buy"),
                     trade(1, "a", T-DAY+40*S, "sell"), trade(1, "a", T-DAY+300*S, "sell"),
                     trade(1, "b", T-DAY, "buy", wallet=1), trade(1, "b", T-DAY+10*S, "sell", wallet=1),
                     trade(1, "b", T-DAY, "buy", wallet=2), trade(1, "b", T-DAY+1000*S, "sell", wallet=2),
                     trade(1, "c", T-DAY, "buy")])                                   # открыта
    o = score(h).features["inside"][0]["onchain"]
    assert o["closed_positions"] == 3 and o["median_hold_s"] == 40 and o["fast_dump_share"] == 0.6667
    assert o["fast_dumper"]


def test_onchain_caps_and_score_bounds():
    holding = [trade(k, "mint0", T-M, "buy") for k in range(1, 6)]
    r = score(with_trades(holding, n=10, shillers=5, creator_labels=["neutral"]*3))
    assert r.features["components"]["kol_holding"] == 8 and r.score == 93
    assert r.features["evidence"]["shillers_holding"] == 5
    dumpers = [t for k in range(1, 4) for i in range(3) for t in position(k, i, 10)]
    r = score(with_trades(dumpers, shillers=3))
    assert r.features["components"]["kol_fast_dumpers"] == -12 and r.score == 0
    assert r.features["risks"].count("kol_fast_dumper") == 1
    # Держит и при этом дампер: оба свидетельства складываются.
    r = score(with_trades([trade(1, "mint0", T-M, "buy"), *[t for i in range(3) for t in position(1, i, 10)]]))
    assert r.features["components"]["kol_holding"] == 4 and r.features["components"]["kol_fast_dumpers"] == -6


def test_holding_follows_latest_trade():
    """holding — последняя сделка по токену к t была покупкой: докупил после продажи — снова держит,
    продал часть и докупил — держит; позиции и медиана при этом по первой покупке/продаже (как во view)."""
    t = T + 10*M
    rebuy = with_trades([trade(1, "mint0", T+M, "buy"), trade(1, "mint0", T+2*M, "sell"), trade(1, "mint0", T+3*M, "buy")])
    o = score(rebuy, t).features["inside"][0]["onchain"]
    assert o["holding"] and o["closed_positions"] == 1 and o["median_hold_s"] == 60
    assert o["first_buy_at"] == (T+M).isoformat() and o["first_sell_at"] == (T+2*M).isoformat()
    assert score(rebuy, T+2*M).features["inside"][0]["onchain"]["holding"] is False      # между продажей и докупкой
    assert score(rebuy, t).features["components"]["kol_holding"] == 4
    partial = with_trades([trade(1, "mint0", T+M, "buy"), trade(1, "mint0", T+2*M, "sell"),
                           trade(1, "mint0", T+3*M, "buy"), trade(1, "mint0", T+4*M, "buy")])
    assert score(partial, t).features["inside"][0]["onchain"]["holding"]
    exited = with_trades([trade(1, "mint0", T+M, "buy"), trade(1, "mint0", T+2*M, "sell"),
                          trade(1, "mint0", T+3*M, "buy"), trade(1, "mint0", T+4*M, "sell")])
    assert not score(exited, t).features["inside"][0]["onchain"]["holding"]
    assert not score(with_trades([trade(1, "mint0", T+M, "sell")]), t).features["inside"][0]["onchain"]["holding"]


async def test_new_kol_trades_make_token_due(scoring_db):
    """Сделки кошельков, записанные после последнего расчёта, требуют пересчёта и без новых упоминаний;
    токен находится и по mint, пока token_id в kol_trades не проставлен."""
    tid, k = await seed(scoring_db)
    wid = await scoring_db.fetchval("INSERT INTO wallets(chain,address) VALUES ('SOLANA','W1') RETURNING id")
    settings = SimpleNamespace(signal_max_age_hours=6, signal_outcome_rule_version="v2")
    assert await score_due_tokens(scoring_db, T, settings) == 1
    assert await score_due_tokens(scoring_db, T+M, settings) == 0
    async def sell(sig, ts, inserted, token_id):
        await scoring_db.execute("""INSERT INTO kol_trades(signature,wallet_id,kol_id,token_id,mint,ts,side,sol_amount,token_amount,program,inserted_at)
            VALUES ($1,$2,$3,$4,'mint',$5,'sell',1,1,'pump',$6)""", sig, wid, k, token_id, ts, inserted)
    await sell("s1", T+10*M, T+70*M, None)                      # сделка в 10:10, в базе — через час (опрос кошельков)
    assert await score_due_tokens(scoring_db, T+71*M, settings) == 1
    features = json.loads(await scoring_db.fetchval("SELECT features FROM signals WHERE token_id=$1", tid))
    assert features["inside"][0]["onchain"]["sold_after_call"] and "kol_sold_after_call" in features["risks"]
    assert await score_due_tokens(scoring_db, T+72*M, settings) == 0
    await sell("s2", T+20*M, T+80*M, tid)                       # по token_id
    assert await score_due_tokens(scoring_db, T+81*M, settings) == 1
    await sell("s3", T+30*M, T+85*M, None)
    # Чужой токен сделки не трогает: токен с другим mint не становится due.
    other = await scoring_db.fetchval("INSERT INTO tokens(mint,source,created_at) VALUES ('other','test',$1) RETURNING id", T)
    await scoring_db.execute("""INSERT INTO mentions(kol_id,chain,ca,token_id,tweet_id,tweet_time,source,first_seen_at)
        VALUES ($1,'SOLANA','other',$2,'tw2',$3,'frontrun_search',$3)""", k, other, T)
    assert await score_due_tokens(scoring_db, T+90*M, settings) == 2       # mint: s3; other: ещё нет сигнала
    await sell("s4", T+40*M, T+95*M, None)
    assert await score_due_tokens(scoring_db, T+96*M, settings) == 1


async def test_load_history_reads_kol_trades(scoring_db):
    tid, k = await seed(scoring_db)
    wid = await scoring_db.fetchval("INSERT INTO wallets(chain,address) VALUES ('SOLANA','W1') RETURNING id")
    await scoring_db.execute("""INSERT INTO kol_trades(signature,wallet_id,kol_id,token_id,mint,ts,side,sol_amount,token_amount,program)
        VALUES ('s1',$1,$2,NULL,'mint',$3,'buy',1,1,'pump')""", wid, k, T-30*M)   # token_id ещё не проставлен — ищем по mint
    settings = SimpleNamespace(signal_max_age_hours=6, signal_outcome_rule_version="v2")
    assert await score_due_tokens(scoring_db, T, settings) == 1
    features = json.loads(await scoring_db.fetchval("SELECT features FROM signals WHERE token_id=$1", tid))
    o = features["inside"][0]["onchain"]
    assert o["holding"] and o["bought_before_call"] and o["first_buy_at"] == (T-30*M).isoformat()
    assert features["components"]["kol_holding"] == 4 and "kol_bought_before_call" in features["risks"]


def test_creator_history_ignored_without_created_at():
    """Без created_at нельзя сказать, какие токены креатора были раньше: история креатора не засчитывается."""
    r = score(fixture_history(creator_labels=["rug", "rug", "rug"], created=None))
    assert r.features["creator"]["past_final"] == 0
    assert r.features["components"]["creator_rug"] == 0
    assert "creator_rugged_before" not in r.features["risks"]
