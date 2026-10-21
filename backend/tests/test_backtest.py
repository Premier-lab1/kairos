from datetime import timedelta

import pytest

import kairos.backtest as module
from kairos.scoring import DAY, History
from test_scoring import T, mention, outcome, token, scoring_db, seed


def synthetic(n=40):
    tokens, mentions, outcomes = [], [], []
    for i in range(n):
        tm = T + i*DAY*2
        tokens.append(token(i,tm))
        mentions.append(dict(**mention(i,1 if i%2==0 else 2,tm),
                             source="frontrun_search",first_seen_at=tm+timedelta(seconds=i)))
        outcomes.append(outcome(i,"success" if i%2==0 else "rug",tm))
    return History(tokens, [], mentions, outcomes)


async def run_history(monkeypatch, hist, **kwargs):
    async def load(*args,**kw):
        return hist
    monkeypatch.setattr(module,"load_history",load)
    return await module.run_backtest(None,**kwargs)


async def test_walk_forward_precision_and_warmup(monkeypatch):
    r = await run_history(monkeypatch,synthetic())
    assert r.n == 20 and r.warmup_n==20
    assert min(s["token_id"] for s in r.scores)==20
    assert r.base["precision"] == .5
    bucket = r.thresholds[0]
    assert bucket["n"]==10 and bucket["precision"]==1
    assert bucket["lift"]==2 and bucket["rug_share"]==0
    assert r.top_ten_percent["n"]==2 and r.top_ten_percent["precision"]==1
    assert sum(v["n"] for v in r.histogram)==20
    assert sum(v["n"] for v in r.deciles)==20
    assert sum(r.confidence_counts.values())==20
    assert r.component_means["success"]["shillers"] > r.component_means["non_success"]["shillers"]
    assert r.collection_lag==dict(n=40,median_seconds=19.5,p90_seconds=35.1)
    assert r.suggested_threshold is None
    assert r.warnings


async def test_empty_markdown(monkeypatch):
    r = await run_history(monkeypatch,History())
    text = module.format_markdown(r,dict(dataset="empty",dump_time=T.isoformat()))
    assert r.n==0 and r.base["precision"] is None
    assert "пусто" in text and "not enough data" in text
    assert "|" in text and "tweet_time" in text and "delay" in text


async def test_excluded_counts(monkeypatch):
    h = History([token(0),token(1),token(2)], [], [mention(0),mention(1),mention(2)],
                [outcome(0),outcome(1,"pending"),outcome(2,"no_data")])
    r = await run_history(monkeypatch,h,warmup_share=0)
    assert r.n==1
    assert r.excluded["pending"]==r.excluded["no_data"]==1
    assert r.excluded["share"]==pytest.approx(2/3)


async def test_entry_buckets(monkeypatch):
    minutes=[0,10,10.1,60,60.1,180,180.1,None]
    h = History([token(i,T-timedelta(minutes=m) if m is not None else None) for i,m in enumerate(minutes)],
                [],[mention(i) for i in range(8)],[outcome(i,time=T) for i in range(8)])
    r = await run_history(monkeypatch,h,warmup_share=0)
    assert [b["n"] for b in r.entry_buckets]==[2,2,2,1,1]
    assert all(s["score"]==0 for s in r.scores)  # current labels not known at t


@pytest.mark.parametrize("share", [-.1,1.1])
async def test_invalid_share(share):
    with pytest.raises(ValueError):
        await module.run_backtest(None,warmup_share=share)


async def test_zero_base(monkeypatch):
    h = History([token(0)],[],[mention(0)],[outcome(0,"rug")])
    r = await run_history(monkeypatch,h,warmup_share=0,thresholds=(0,))
    assert r.base["precision"]==0 and r.thresholds[0]["lift"] is None


async def test_suggested_threshold(monkeypatch):
    r = await run_history(monkeypatch,synthetic(300),warmup_share=.5,thresholds=(0,40,50))
    # 50% base cannot be tripled, even perfect precision is insufficient.
    assert r.suggested_threshold is None
    h = synthetic(400)
    outcomes = list(h.outcomes.values())
    for o in outcomes:
        if o["token_id"]%4!=0:
            o["label"]="rug"
    mentions = [dict(m,kol_id=1 if m["token_id"]%4==0 else 2) for m in h.mentions]
    h = History(list(h.tokens.values()),[],mentions,outcomes)
    r = await run_history(monkeypatch,h,thresholds=(0,30,40))
    assert r.suggested_threshold==30


async def test_real_db_backtest(scoring_db):
    tid,_ = await seed(scoring_db)
    await scoring_db.execute("""INSERT INTO token_outcomes
        (token_id,horizon,rule_version,reference,reference_time,label)
        VALUES ($1,interval '24 hours','v2','first_mention',$2,'neutral')""",tid,T)
    r = await module.run_backtest(scoring_db,warmup_share=0)
    assert r.n==1 and r.scores[0]["score"]==0


async def test_thresholds_count_only_medium_or_high_confidence(monkeypatch):
    """В пороги идут только сигналы, которые ушли бы в бота: confidence medium или high."""
    h = synthetic()
    late = T + 80*DAY  # позже всех: в тестовой части; шиллер 3 без истории → confidence low
    tokens = list(h.tokens.values()) + [token(100, late)]
    mentions = h.mentions + [dict(**mention(100, 3, late), source="frontrun_search", first_seen_at=late)]
    outcomes = list(h.outcomes.values()) + [outcome(100, "success", late)]
    r = await run_history(monkeypatch, History(tokens, [], mentions, outcomes), thresholds=(0,))
    low = sum(1 for s in r.scores if s["confidence"] == "low")
    assert low >= 1
    assert r.thresholds[0]["n"] == r.n - low
