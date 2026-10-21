"""Mint creation and metadata from successful Solana transactions."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from kairos.sources.helius import _account_keys, _signature, _ts, b58decode

TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
METAPLEX = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
METADATA_INITIALIZE = bytes.fromhex("d2e11ea258b84d8d")
LAUNCHPADS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj": "bonk",
}


@dataclass(frozen=True)
class TokenCreation:
    mint: str
    signature: str
    ts: datetime
    creator: str | None
    name: str | None
    symbol: str | None
    uri: str | None
    launchpad: str | None


def _strings(data: bytes, offset: int) -> tuple:
    values = []
    for limit in (200, 50, 500):
        if offset + 4 > len(data):
            return None, None, None
        size = int.from_bytes(data[offset:offset + 4], "little")
        offset += 4
        if size > len(data) - offset:
            return None, None, None
        value = data[offset:offset + size].decode("utf-8", errors="replace")
        # Strip both whitespace and NUL, including interleaved padding.
        start, end = 0, len(value)
        while start < end and (value[start].isspace() or value[start] == "\x00"):
            start += 1
        while end > start and (value[end - 1].isspace() or value[end - 1] == "\x00"):
            end -= 1
        values.append(value[start:end][:limit] or None)
        offset += size
    return tuple(values)


def parse_creation(tx: dict, mint: str) -> TokenCreation | None:
    try:
        meta = tx.get("meta")
        if not isinstance(meta, dict) or meta.get("err") is not None:
            return None
        signature, ts = _signature(tx), _ts(tx, None)
        if not signature or ts is None:
            return None
        keys = _account_keys(tx)
        instructions = list(tx["transaction"]["message"].get("instructions") or [])
        for group in meta.get("innerInstructions") or []:
            if isinstance(group, dict):
                instructions.extend(group.get("instructions") or [])
        def address(value):
            if isinstance(value, int):
                return keys[value] if 0 <= value < len(keys) else None
            return value if isinstance(value, str) else None

        initialized = False
        metadata = None
        programs = set()
        for ins in instructions:
            if not isinstance(ins, dict):
                continue
            pid = address(ins.get("programId", ins.get("programIdIndex")))
            programs.add(pid)
            raw_accounts = ins.get("accounts") or []
            accounts = [address(a) for a in raw_accounts] if isinstance(raw_accounts, list) else []
            if not isinstance(ins.get("data"), str):
                continue
            try:
                data = b58decode(ins["data"])
            except (KeyError, ValueError, TypeError):
                continue
            if pid in TOKEN_PROGRAMS and data[:1] in (b"\x00", b"\x14") and accounts[:1] == [mint]:
                initialized = True
            if metadata is not None:
                continue
            if data.startswith(METADATA_INITIALIZE) and (accounts[:1] == [mint] or accounts[2:3] == [mint]):
                metadata = _strings(data, 8)
            elif pid == METAPLEX:
                if data[:1] in (b"\x21", b"\x10") and accounts[1:2] == [mint]:
                    metadata = _strings(data, 1)
                elif data[:2] == b"\x2a\x00" and accounts[2:3] == [mint]:
                    metadata = _strings(data, 2)
        if not initialized:
            return None
        name, symbol, uri = metadata or (None, None, None)
        launchpad = next((label for pid, label in LAUNCHPADS.items() if pid in programs), None)
        return TokenCreation(mint, signature, ts, keys[0] if keys else None, name, symbol, uri, launchpad)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError, OSError):
        return None


def find_creation(txs: Iterable[dict], mint: str) -> TokenCreation | None:
    for tx in txs:
        creation = parse_creation(tx, mint)
        if creation is not None:
            return creation
    return None
