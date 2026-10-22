# Telegram message for the example signal

This is what the bot renders for signal `242` (`$WIFSI`, score 0, confidence `low`).
The text below comes from `kairos.bot.format.format_signal` and `kairos.bot.texts.why_fallback`, run on the exact `features` in [`signal.json`](signal.json). Nothing is edited by hand.

> **Delivery note.** The bot only sends signals with `score >= threshold` (default 60) and `confidence` of `medium` or `high`.
> This signal has score 0 and confidence `low`, so it was never delivered. It is shown because it is a real production signal and the formatter handles it the same way. Production has not yet produced a `medium` or `high` confidence signal (see the [README](README.md)).

## Message as the user sees it

The bot sends HTML parse mode; the bold and monospace parts are rendered by Telegram.

```text
$WIFSI · minute 149
CA: 944fTeZPkMwdUp6yWmuk9Q9uUt44MEuerY3Efjfhpump

Who is already in
@solhousesignals — min 2 · no history yet

Signal confidence
▱▱▱▱▱▱▱▱▱▱ 0/100 · evidence: low

Risks: single shiller · low evidence
Not financial advice
```

Buttons under the message: `Why?` · `pump.fun` · `Mute @solhousesignals`.

## Message source (HTML parse mode)

```html
<b>$WIFSI</b> · minute 149
CA: <code>944fTeZPkMwdUp6yWmuk9Q9uUt44MEuerY3Efjfhpump</code>

<b>Who is already in</b>
@solhousesignals — min 2 · no history yet

<b>Signal confidence</b>
▱▱▱▱▱▱▱▱▱▱ 0/100 · evidence: low

Risks: single shiller · low evidence
Not financial advice
```

Inline keyboard (`callback_data` or URL per button):

| Button | Action |
| --- | --- |
| `Why?` | `why:242` |
| `pump.fun` | `https://pump.fun/coin/944fTeZPkMwdUp6yWmuk9Q9uUt44MEuerY3Efjfhpump` |
| `Mute @solhousesignals` | `mute:6` |

## The "Why?" button

Pressing `Why?` returns an LLM explanation when one is stored in `signal_explanations` for this signal; otherwise a plain summary of the facts built by `texts.why_fallback`.
Explanations are generated on demand, when someone presses `Why?` and `ANTHROPIC_API_KEY` is set, then cached per signal in `signal_explanations`. There is no row for this signal (it was never delivered, so nobody asked), so the reply is the fallback:

```text
Why this signal
Shillers: 1.
Top shiller @solhousesignals: no history yet.
Creator history: 0 final cases, 0 rugs.
Risks: single shiller · low evidence
Not financial advice
```

When an explanation exists, it replaces the body: 2 to 4 short sentences, at most 80 words, written by Claude only from the signal's facts (`kairos/explain.py`), followed by the same disclaimer.
