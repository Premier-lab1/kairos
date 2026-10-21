"""Discover KOL wallets and poll their finalized Solana trades within shared budgets."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from kairos.pipeline import jobs
from kairos.pipeline.budget import record_call
from kairos.pipeline.helius_tasks import EXCLUDED_MINTS
from kairos.sources.frontrun import CREDIT_COST, FrontrunError
from kairos.sources.helius import HeliusError, merge_same_signature, parse_transaction

KIND_WALLETS = "frontrun_kol_wallets"
KIND_POLL = "helius_kol_wallet"
FRONTRUN_KINDS = (KIND_WALLETS,)
HELIUS_KINDS = (KIND_POLL,)
WALLETS_COST = CREDIT_COST["associated_wallets"]  # 400


def utc_now():
    return datetime.now(timezone.utc)


def associated_wallets(data):
    """Return deduplicated (chain, address) pairs and rejected leaf/candidate count.

    Containers are traversed recursively; chain/network may be inherited. Address
    syntax must agree with the network. Missing networks can be inferred from
    unambiguous address syntax. EVM networks share the existing EVM namespace.
    """
    found, skipped = set(), 0

    def visit(value, network=None):
        nonlocal skipped
        if isinstance(value, list):
            for item in value:
                visit(item, network)
        elif isinstance(value, dict):
            network = value.get("chain", value.get("network", network))
            address = value.get("address", value.get("wallet"))
            if isinstance(address, str):
                net = network.strip().upper() if isinstance(network, str) else ""
                address = address.strip()
                if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address) and net in ("", "SOL", "SOLANA"):
                    found.add(("SOLANA", address))
                elif re.fullmatch(r"0x[0-9a-fA-F]{40}", address) and net in (
                        "", "EVM", "ETH", "ETHEREUM", "BASE", "BSC", "BNB", "ARBITRUM", "POLYGON", "OPTIMISM", "AVALANCHE"):
                    found.add(("EVM", address.lower()))
                else:
                    skipped += 1
            else:
                children = [v for k, v in value.items() if k not in ("chain", "network") and isinstance(v, (dict, list))]
                if children:
                    for child in children:
                        visit(child, network)
                else:
                    skipped += 1
        else:
            skipped += 1

    visit(data)
    return sorted(found), skipped


async def schedule_kol_wallets(pool, now, settings):
    # Limit BEFORE freshness filtering: never drift from top N into the whole KOL table.
    rows = await pool.fetch("""WITH ranked AS (
        SELECT k.id, k.username, k.wallets_fetched_at
        FROM kols k JOIN mentions m ON m.kol_id = k.id
        WHERE k.username <> 'unknown'
        GROUP BY k.id
        ORDER BY (k.smart_followers_count IS NOT NULL) DESC, count(DISTINCT m.ca) DESC, k.id
        LIMIT $1)
        SELECT * FROM ranked k WHERE (wallets_fetched_at IS NULL OR wallets_fetched_at <= $2::timestamptz - $3::interval)
        AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = $4
            AND j.payload->>'kol_id' = k.id::text AND j.status IN ('pending','running'))""",
        settings.kol_wallets_top_n, now, timedelta(days=settings.kol_wallets_refresh_days), KIND_WALLETS)
    count = 0
    for row in rows:
        count += await jobs.enqueue(pool, KIND_WALLETS, f"{row['id']}:{int(now.timestamp() // 86400)}",
                                    {"kol_id": row["id"], "username": row["username"]}, priority=2, run_after=now)
    return count


async def schedule_kol_wallet_polls(pool, now, settings):
    rows = await pool.fetch("""SELECT w.id, w.address FROM wallets w WHERE w.chain = 'SOLANA'
        AND EXISTS (SELECT 1 FROM kol_wallets kw WHERE kw.wallet_id = w.id)
        AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = $1 AND j.payload->>'wallet_id' = w.id::text
                        AND j.status IN ('pending','running')) ORDER BY w.id""", KIND_POLL)
    interval = int(now.timestamp() // (settings.kol_wallet_poll_minutes * 60))
    count = 0
    for row in rows:
        count += await jobs.enqueue(pool, KIND_POLL, f"{row['id']}:{interval}",
                                    {"wallet_id": row["id"], "address": row["address"]}, priority=4, run_after=now)
    return count


def changed_mints(tx, address):
    amounts = [{}, {}]
    meta = tx.get("meta") or {}
    for target, key in zip(amounts, ("preTokenBalances", "postTokenBalances")):
        for balance in meta.get(key) or []:
            if not isinstance(balance, dict) or balance.get("owner") != address:
                continue
            mint = balance.get("mint")
            if not isinstance(mint, str) or mint in EXCLUDED_MINTS:
                continue
            try:
                target[mint] = target.get(mint, Decimal(0)) + Decimal(str(balance["uiTokenAmount"]["amount"]))
            except (KeyError, TypeError, InvalidOperation):
                continue
    return {mint for mint in amounts[0].keys() | amounts[1].keys()
            if amounts[0].get(mint, 0) != amounts[1].get(mint, 0)}


class KolWalletTasks:
    def __init__(self, pool, client, budget, settings, clock=utc_now):
        self.pool, self.client, self.budget, self.settings, self.clock = pool, client, budget, settings, clock

    async def run_job(self, job):
        try:
            if job.kind == KIND_WALLETS:
                await self._discover(job)
            else:
                await self._poll(job)
        except (FrontrunError, HeliusError) as exc:
            await jobs.fail(self.pool, job.id, f"http {exc.http_status}: {exc.message}", retry_in=timedelta(minutes=10))

    async def _discover(self, job):
        if not await self.budget.can_spend(WALLETS_COST, KIND_WALLETS):
            await jobs.reschedule(self.pool, job.id, self.budget.next_reset(), "daily credit budget exhausted")
            return
        before, ok, status = self.client.credits_spent, False, None
        try:
            data = await self.client.associated_wallets(job.payload["username"])
            ok, status = True, 200
        except FrontrunError as exc:
            status = exc.http_status
            raise
        finally:
            await record_call(self.pool, "frontrun", KIND_WALLETS, self.client.credits_spent - before,
                              ok=ok, http_status=status, ts=self.clock())
        wallets, skipped = associated_wallets(data)
        async with self.pool.acquire() as conn, conn.transaction():
            for chain, address in wallets:
                wallet_id = await conn.fetchval("""INSERT INTO wallets (chain, address) VALUES ($1,$2)
                    ON CONFLICT (chain,address) DO UPDATE SET address = EXCLUDED.address RETURNING id""", chain, address)
                await conn.execute("""INSERT INTO kol_wallets (kol_id,wallet_id,relation,source)
                    VALUES ($1,$2,'associated','frontrun') ON CONFLICT DO NOTHING""", job.payload["kol_id"], wallet_id)
            await conn.execute("UPDATE kols SET wallets_fetched_at = $2 WHERE id = $1", job.payload["kol_id"], self.clock())
        await jobs.complete(self.pool, job.id, {"wallets": len(wallets), "skipped": skipped})

    async def _poll(self, job):
        wallet_id, address = job.payload["wallet_id"], job.payload["address"]
        wallet = await self.pool.fetchrow("""SELECT w.kol_trades_synced_until FROM wallets w
            WHERE w.id=$1 AND w.address=$2 AND w.chain='SOLANA'
            AND EXISTS (SELECT 1 FROM kol_wallets WHERE wallet_id=w.id)""", wallet_id, address)
        if wallet is None:
            await jobs.skip(self.pool, job.id, "not a linked Solana wallet")
            return
        now = self.clock()
        midnight = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        since = wallet["kol_trades_synced_until"] or now - timedelta(hours=24)
        # Preserve a pagination cursor across capped polls, including transactions sharing a second.
        cursor = job.payload.get("cursor")
        if job.payload.get("since"):
            since = datetime.fromisoformat(job.payload["since"])
        pages, trades = 0, 0
        for _ in range(self.settings.kol_wallet_max_pages):
            spent = await self.pool.fetchval("""SELECT coalesce(sum(credits),0) FROM api_calls
                WHERE provider='helius' AND note LIKE 'kol_wallet%' AND ts >= $1 AND ts < $2""",
                midnight, midnight + timedelta(days=1))
            if spent + 10 > self.settings.kol_wallet_daily_credits:
                await jobs.reschedule(self.pool, job.id, midnight + timedelta(days=1), "kol_wallet daily ceiling")
                return
            if not await self.budget.can_spend(10):
                await jobs.reschedule(self.pool, job.id, self.budget.next_reset(), "daily credit budget exhausted")
                return
            before, ok, status = self.client.credits_spent, False, None
            try:
                page = await self.client.get_transactions_for_address(address, since=since, until=now,
                    sort_order="asc", limit=100, pagination_token=cursor)
                ok, status = True, 200
            except HeliusError as exc:
                status = exc.http_status
                raise
            finally:
                await record_call(self.pool, "helius", "getTransactionsForAddress", self.client.credits_spent - before,
                                  ok=ok, http_status=status, note=f"kol_wallet {wallet_id}", ts=self.clock())
            pages += 1
            last = None
            async with self.pool.acquire() as conn, conn.transaction():
                # The required PK identifies a wallet trade. Shared wallets get a stable primary KOL;
                # dashboard queries attribute it to every linked KOL without duplicating the feed.
                kol_id = await conn.fetchval("SELECT min(kol_id) FROM kol_wallets WHERE wallet_id=$1", wallet_id)
                for tx in page.items:
                    if isinstance(tx.get("blockTime"), (int, float)):
                        ts = datetime.fromtimestamp(tx["blockTime"], timezone.utc)
                        last = max(last or ts, ts)
                    for mint in changed_mints(tx, address):
                        records = merge_same_signature(r for r in parse_transaction(tx, mint) if r.wallet == address)
                        for r in records:
                            result = await conn.execute("""INSERT INTO kol_trades
                                (signature,wallet_id,kol_id,token_id,mint,ts,side,sol_amount,token_amount,market_cap_sol,program)
                                VALUES ($1,$2,$3,(SELECT id FROM tokens WHERE chain='SOLANA' AND mint=$4),$4,$5,$6,$7,$8,$9,$10)
                                ON CONFLICT (signature,wallet_id,mint) DO NOTHING""", r.signature, wallet_id, kol_id,
                                mint, r.ts, r.side, r.sol_amount, r.token_amount, r.market_cap_sol, r.program)
                            trades += int(result.rsplit(' ', 1)[-1])
                cursor = page.pagination_token
                synced = last if cursor else now
                await conn.execute("""UPDATE wallets SET kol_trades_synced_until =
                    greatest(kol_trades_synced_until,$2::timestamptz) WHERE id=$1""", wallet_id, synced)
                # Persist before another paid call; retries can resume without skipping a partial second.
                await conn.execute("UPDATE jobs SET payload = payload || $2::jsonb WHERE id=$1", job.id,
                    json.dumps({"cursor": cursor, "since": since.isoformat()}))
            if not cursor:
                break
        if cursor:
            await jobs.reschedule(self.pool, job.id, now + timedelta(minutes=self.settings.kol_wallet_poll_minutes),
                                  "kol_wallet page cap; continue from cursor")
        else:
            await jobs.complete(self.pool, job.id, {"pages": pages, "trades": trades})


class WalletTaskRouter:
    """Keep each provider on its existing single worker and shared budget."""
    def __init__(self, standard, wallets):
        self.standard, self.wallets = standard, wallets

    async def run_job(self, job):
        await (self.wallets if job.kind in (*FRONTRUN_KINDS, *HELIUS_KINDS) else self.standard).run_job(job)
