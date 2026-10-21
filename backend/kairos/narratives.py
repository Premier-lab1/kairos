"""Offline, deterministic name/ticker taxonomy. First matching rule wins.

Short words match whole words; only explicit compounds are accepted. National
adjectives (e.g. American) indicate country, never politics on their own.
Names alone cannot establish a token's actual purpose or endorsement.
"""

import argparse
import asyncio
import os
import re
import unicodedata

import asyncpg

NARRATIVE_VERSION = 1
# Priority, vocabulary, compound exceptions and emoji all live here. Bump the
# version whenever these rules change, so the worker revisits old assignments.
RULES = (
    ("ai", "ai agent agents gpt chatgpt llm neural robot robots bot bots artificial intelligence grok claude anthropic", r"aigent\w*|(?:baby)?(?:aiagent|chatgpt|aibot)|\w*gpt", "🤖🧠"),
    ("politics", "trump maga biden election president obama kamala putin zelensky melania tremp macron macrontard", r"(?:baby|dark)?(?:trump|maga)\w*", ""),
    ("celebrity", "elon musk kanye ye taylor swift snoop ronaldo messi celebrity mrbeast ishowspeed", r"(?:baby)?elon\w*|mr\s+beast|bill\s+gates", ""),
    ("dog", "dog dogs doge inu shib shiba puppy puppies pup wif bonk", r"(?:baby|mini|doge)?(?:doge|shib|shiba|dogwifhat)\w*", "🐶🐕"),
    ("cat", "cat cats kitty kitten kittens meow michi popcat", r"(?:baby|pop|nyan|happy|grumpy)cat\w*", "🐱🐈😺"),
    ("frog", "pepe frog frogs toad apu kek", r"(?:baby)?pepe\w*", "🐸"),
    ("anime", "anime naruto sasuke goku dragonball waifu senpai pikachu pokemon luffy totoro akatsuki charmeleon", r"", ""),
    ("gaming", "game games gaming gamer minecraft roblox fortnite mario sonic zelda playstation xbox", r"", "🎮🕹"),
    ("animal", "animal animals bear bull penguin pengu pingu monkey ape gorilla goat sheep horse pony duck goose chicken bird eagle fish shark whale dolphin hamster rabbit bunny capybara hippo moo deng turtle fox wolf lion tiger panda sloth raccoon honeybadger", r"moodeng", "🐻🐧🐒🦍🐐🐰🦊🐼🦈🐳🐢🦆"),
    ("meme", "meme memes wojak chad gigachad based brainrot skibidi troll trollface npc sigma boomer zoomer stonks retard fart poop shit mog chill guy", r"gigachad|chillguy|skibidi\w*", "💩"),
    ("food", "food pizza burger cheese bacon sushi taco tacos banana apple orange potato avocado bread cake cookie milk egg rice coffee chocolate watermelon peanut baguette", r"bananaguy", "🍕🍔🍌🍎🍣🌮🥑🥚☕"),
    ("space", "space moon mars jupiter saturn venus pluto galaxy cosmic astronaut nasa rocket star stars alien ufo spacex starlink", r"", "🚀🌙🌕🛸👽"),
    ("country", "usa ussr america american china chinese japan japanese russia russian ukraine israel iran india indian korea korean france french germany german britain british england english georgia georgian canada canadian brazil brazilian turkey turkish mexico mexican dubai london paris tokyo nyc", r"[\U0001f1e6-\U0001f1ff]{2}", ""),
    ("crypto", "btc bitcoin eth ethereum sol solana defi pump pumpfun blockchain crypto token coin nft hodl satoshi dex web3 zcash fomo", r"pump\.fun|(?:wrapped|baby)(?:btc|eth|sol)|solana\w*", "₿"),
)
_COMPILED = tuple(
    (key, re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, words.split()))
                     + ("|" + extra if extra else "") + r")(?!\w)"), emoji)
    for key, words, extra, emoji in RULES
)


def classify(name: str | None, symbol: str | None) -> str:
    """Return one stable English UI key, without I/O or fuzzy substring matches."""
    text = unicodedata.normalize("NFKC", f"{name or ''} {symbol or ''}").lower().replace("_", " ")
    for key, pattern, emoji in _COMPILED:
        if pattern.search(text) or any(char in text for char in emoji):
            return key
    return "other"


async def classify_pending(pool: asyncpg.Pool, limit: int = 5000) -> int:
    """Classify one bounded batch of mentioned/migrated tokens, safely concurrently."""
    if limit < 1:
        return 0
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch("""
            SELECT t.id, t.name, t.symbol FROM tokens t
            WHERE (t.narrative IS NULL OR t.narrative_version IS NULL OR t.narrative_version < $1)
              -- без имени и тикера классифицировать нечего: ждём метаданные из транзакции создания
              -- (TradeStore.save_creation сбрасывает narrative, когда их записывает)
              AND (t.name IS NOT NULL OR t.symbol IS NOT NULL)
              AND (t.migrated_at IS NOT NULL OR EXISTS
                   (SELECT 1 FROM mentions m WHERE m.token_id = t.id))
            ORDER BY t.id LIMIT $2 FOR UPDATE OF t SKIP LOCKED
        """, NARRATIVE_VERSION, limit)
        await conn.executemany(
            "UPDATE tokens SET narrative=$2, narrative_version=$3 WHERE id=$1",
            [(r["id"], classify(r["name"], r["symbol"]), NARRATIVE_VERSION) for r in rows],
        )
    return len(rows)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Reclassify ALL tokens and print distribution (no external APIs).")
    parser.add_argument("--dsn", help="PostgreSQL DSN; defaults to DATABASE_URL")
    args = parser.parse_args()
    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("provide --dsn or DATABASE_URL")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        last_id = 0
        async with pool.acquire() as conn:
            while rows := await conn.fetch("SELECT id, name, symbol FROM tokens WHERE id > $1 ORDER BY id LIMIT 5000", last_id):
                await conn.executemany("UPDATE tokens SET narrative=$2, narrative_version=$3 WHERE id=$1",
                                      [(r["id"], classify(r["name"], r["symbol"]), NARRATIVE_VERSION) for r in rows])
                last_id = rows[-1]["id"]
            for r in await conn.fetch("SELECT narrative, count(*) n FROM tokens GROUP BY narrative ORDER BY n DESC"):
                print(f"{r['narrative']}: {r['n']}")
            r = await conn.fetchrow("""SELECT count(*) n, count(*) FILTER (WHERE narrative <> 'other') covered
                FROM tokens t WHERE EXISTS (SELECT 1 FROM mentions m WHERE m.token_id=t.id)""")
            print(f"Mentioned coverage: {r['covered']}/{r['n']} ({100*r['covered']/r['n'] if r['n'] else 0:.2f}%)")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
