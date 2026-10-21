"""All Telegram-facing copy and HTML templates."""

from html import escape

DISCLAIMER = "Not financial advice"
CLOSED_BETA = "Kairos is in closed beta."
START_HINT = "Send /start to subscribe."
INVALID_THRESHOLD = "Use /threshold followed by an integer from 0 to 100."
UNKNOWN_SHILLER = "Unknown shiller."
PAUSED = "Signals paused. Use /unmute to resume."
RESUMED = "Signals resumed."
SIGNAL_MISSING = "This signal is no longer available."
WHY_BUTTON = "Why?"
PUMP_BUTTON = "pump.fun"
UNKNOWN = "unknown"
RISK_PHRASES = {
    "creator_rugged_before": "creator rugged before",
    "shiller_rug_history": "shiller rug history",
    "single_shiller": "single shiller",
    "low_evidence": "low evidence",
    "late_entry": "late entry",
    "unknown_creation": "unknown creation time",
    "kol_fast_dumper": "shiller usually dumps within minutes",
    "kol_sold_after_call": "shiller sold within 30 min of the call",
    "kol_bought_before_call": "shiller bought before the call",
}


def onchain(shiller):
    """Короткая пометка о поведении шиллера на цепи; пусто, если данных кошельков нет."""
    data = shiller.get("onchain")
    if not data:
        return ""
    if data.get("sold_after_call"):
        return "sold after call"
    if data.get("holding"):
        return "holding on-chain"
    if data.get("fast_dumper"):
        hold = data.get("median_hold_s")
        return f"dumps in ~{safe(round(float(hold)))} s" if hold is not None else "fast dumper"
    if data.get("first_sell_at"):
        return "sold"
    if data.get("first_buy_at"):
        return "bought"
    return ""


def safe(value):
    return escape(str(value if value is not None else UNKNOWN))


def closed_beta(chat_id):
    return f"{CLOSED_BETA} Your chat id: {safe(chat_id)} — send it to the team to get access."


def threshold(value):
    return f"Current threshold: {safe(value)}/100."


def welcome(value):
    return ("Kairos sends early Pump.fun signals scored from shiller history.\n"
            + threshold(value) + "\n/start · /threshold [0–100] · /mute [@handle] · "
            "/unmute [@handle] · /status\n" + DISCLAIMER)


def muted(handle, mute=True):
    return f"{'Muted' if mute else 'Unmuted'} @{safe(handle)}"


def mute_button(handle):
    return f"Mute @{handle or UNKNOWN}"


def record(shiller):
    n = shiller.get("n_final") or 0
    success = shiller.get("success") or 0
    return f"{safe(round(success / n * 100))}% win rate ({safe(success)}/{safe(n)})" if n else "no history yet"


def minute(value):
    return safe(round(float(value))) if value is not None else UNKNOWN


def risks(features):
    return " · ".join(safe(RISK_PHRASES.get(code, code)) for code in features.get("risks", []) or [])


def signal_body(features, score, confidence):
    token = features.get("token") or {}
    inside = features.get("inside") or []
    lines = [f"<b>${safe(token.get('symbol'))}</b> · minute {minute(token.get('age_minutes'))}",
             f"CA: <code>{safe(token.get('mint'))}</code>", "", "<b>Who is already in</b>"]
    for item in inside[:5]:
        note = onchain(item)
        lines.append(f"@{safe(item.get('username'))} — min {minute(item.get('minute'))} · {record(item)}"
                     + (f" · {note}" if note else ""))
    if len(inside) > 5:
        lines.append(f"+{len(inside) - 5} more")
    similar = features.get("similar") or []
    if similar:
        lines.extend(["", "<b>Similar past cases</b>"])
        for item in similar[:3]:
            lines.append(f"${safe(item.get('symbol'))} — {safe(item.get('label'))} · "
                         f"{round(float(item.get('similarity') or 0) * 100)}%")
    filled = max(0, min(10, round(float(score or 0) / 10)))
    lines.extend(["", "<b>Signal confidence</b>",
                  f"{'▰' * filled}{'▱' * (10 - filled)} {safe(score or 0)}/100 · evidence: {safe(confidence)}"])
    if risks(features):
        lines.extend(["", "Risks: " + risks(features)])
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def why_fallback(features):
    inside = features.get("inside") or []
    creator = features.get("creator") or {}
    lines = [f"Shillers: {safe(features.get('n_shillers', len(inside)))}."]
    if inside:
        lines.append(f"Top shiller @{safe(inside[0].get('username'))}: {record(inside[0])}.")
    lines.append(f"Creator history: {safe(creator.get('past_final', 0))} final cases, "
                 f"{safe(creator.get('past_rug', 0))} rugs.")
    if risks(features):
        lines.append("Risks: " + risks(features))
    return "\n".join(lines)


def why(body):
    return "<b>Why this signal</b>\n" + body + "\n" + DISCLAIMER


def status(subscriber, handles, count, last):
    state = "paused" if subscriber["paused"] else "subscribed"
    return (f"Status: {state}\n" + threshold(subscriber["threshold"])
            + "\nMuted shillers: " + (", ".join("@" + safe(h) for h in handles) or "none")
            + f"\nSignals delivered: {count}\nLast delivery: {safe(last.isoformat() if last else 'never')}")
