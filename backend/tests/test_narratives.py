import pytest

from kairos.narratives import NARRATIVE_VERSION, classify, classify_pending


@pytest.mark.parametrize("name,symbol,expected", [
    ("AI Agent", "AIA", "ai"), ("aigent", "AIGENT", "ai"),
    ("Neural Network", "NN", "ai"), ("ChatGPT", "GPT", "ai"),
    ("Robot 🤖", "RBT", "ai"), ("Trading Bot", "BOT", "ai"),
    ("BabyDoge", "BDOGE", "dog"), ("dogwifhat", "WIF", "dog"),
    ("Shiba Inu", "SHIB", "dog"), ("Good Puppy", "PUP", "dog"),
    ("Woof 🐶", "WOOF", "dog"), ("Bonk", "BONK", "dog"),
    ("Popcat", "POP", "cat"), ("Michi", "MICHI", "cat"),
    ("GrumpyCat", "GC", "cat"), ("Kitten 🐱", "KIT", "cat"),
    ("Pepe", "PEPE", "frog"), ("BabyPepe", "BPEPE", "frog"),
    ("Toad", "TOAD", "frog"), ("Green 🐸", "GREEN", "frog"),
    ("Moo Deng", "MOODENG", "animal"), ("Pudgy Penguin", "PENGU", "animal"),
    ("Capybara", "CAPY", "animal"), ("Shark", "SHARK", "animal"),
    ("DarkMAGA", "DMAGA", "politics"), ("President Trump", "TRUMP", "politics"),
    ("Biden Coin", "JOE", "politics"), ("Elon Musk", "ELON", "celebrity"),
    ("Taylor Swift", "SWIFT", "celebrity"), ("Lionel Messi", "MESSI", "celebrity"),
    ("Gigachad", "GIGA", "meme"), ("Wojak", "WOJAK", "meme"),
    ("ChillGuy", "CHILL", "meme"), ("Skibidi Toilet", "SKIBIDI", "meme"),
    ("Naruto", "NARUTO", "anime"), ("Goku", "GOKU", "anime"),
    ("Minecraft", "CRAFT", "gaming"), ("Roblox", "RBLX", "gaming"),
    ("Bitcoin", "BTC", "crypto"), ("Pump.fun", "PUMP", "crypto"),
    ("Solana", "SOL", "crypto"), ("Pizza", "PIZZA", "food"),
    ("Banana", "BANANA", "food"), ("Coffee", "COFFEE", "food"),
    ("Mars", "MARS", "space"), ("Cosmic Rocket", "SPACE", "space"),
    ("Japan Coin", "JP", "country"), ("Tokyo", "TOKYO", "country"),
    ("Flag 🇺🇸", "FLAG", "country"),
    ("Strategic American Protocol", "SAP", "country"),
    ("Chair", "CHAIR", "other"), ("Retail", "RETAIL", "other"),
    ("Scattered", "SCAT", "other"), ("Education", "EDU", "other"),
    ("Bottom", "BOTTOM", "other"), ("Melon", "MELON", "other"),
    ("Musketeer", "MSKT", "other"), ("Solitude", "SOLD", "other"),
    ("Pumpkin", "PKIN", "other"), ("Catering", "CATER", "other"),
    (None, None, "other"), ("", "$AI", "ai"),
    ("ＡＩ", "", "ai"), ("AI_DOG", "", "ai"),
    ("Trump Doge", "", "politics"), ("AI Pepe", "", "ai"),
    ("Dunald Tremp", "TREMP", "politics"), ("Grok", "GROK", "ai"),
    ("Mr Beast | Official Team", "MRBEAST", "celebrity"),
    ("IShowSpeed", "SPEED", "celebrity"), ("SpaceX", "SPACEX", "space"),
    ("The Honeybadger", "HONEY", "animal"),
])
def test_classify(name, symbol, expected):
    assert classify(name, symbol) == expected


async def add_token(pool, mint, name="Pepe", **kwargs):
    return await pool.fetchval("""INSERT INTO tokens
        (mint,name,source,narrative,narrative_version,migrated_at)
        VALUES ($1,$2,'test',$3,$4,CASE WHEN $5 THEN now() END) RETURNING id""",
        mint, name, kwargs.get("narrative"), kwargs.get("version"), kwargs.get("migrated", False))


async def mention(pool, token_id):
    kol = await pool.fetchval("INSERT INTO kols(username) VALUES('narrative_test') RETURNING id")
    await pool.execute("""INSERT INTO mentions(kol_id,chain,ca,token_id,tweet_id,tweet_time,source)
        VALUES($1,'SOLANA',$2,$3,$2,now(),'test')""", kol, str(token_id), token_id)


async def test_pending_eligibility_limit_and_idempotence(db_pool):
    ignored = await add_token(db_pool, "ignored")
    called = await add_token(db_pool, "called")
    await mention(db_pool, called)
    migrated = await add_token(db_pool, "migrated", "Dog", migrated=True)
    current = await add_token(db_pool, "current", migrated=True, narrative="manual", version=NARRATIVE_VERSION)
    assert await classify_pending(db_pool, limit=0) == 0
    assert await classify_pending(db_pool, limit=1) == 1
    assert await classify_pending(db_pool) == 1
    assert await classify_pending(db_pool) == 0
    rows = {r["id"]: r for r in await db_pool.fetch("SELECT * FROM tokens")}
    assert rows[ignored]["narrative"] is None
    assert rows[called]["narrative"] == "frog"
    assert rows[migrated]["narrative"] == "dog"
    assert rows[current]["narrative"] == "manual"
    assert rows[called]["narrative_version"] == NARRATIVE_VERSION


async def test_pending_version_upgrade_and_null_version(db_pool):
    old = await add_token(db_pool, "old", "Robot", migrated=True, narrative="other", version=NARRATIVE_VERSION - 1)
    null_version = await add_token(db_pool, "null", "Cat", migrated=True, narrative="other")
    future = await add_token(db_pool, "future", migrated=True, narrative="future", version=NARRATIVE_VERSION + 1)
    null_label = await add_token(db_pool, "null_label", "Dog", migrated=True, version=NARRATIVE_VERSION)
    assert await classify_pending(db_pool) == 3
    rows = {r["id"]: r["narrative"] for r in await db_pool.fetch("SELECT id,narrative FROM tokens")}
    assert rows == {old: "ai", null_version: "cat", future: "future", null_label: "dog"}
