"""Render stored signal rows without depending on database JSON codecs."""

import json
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import texts


def features_of(signal):
    value = signal.get("features") or {}
    return json.loads(value) if isinstance(value, str) else value


def format_signal(signal):
    return texts.signal_body(features_of(signal), signal.get("score", 0), signal.get("confidence"))


def signal_keyboard(signal):
    features = features_of(signal)
    mint = (features.get("token") or {}).get("mint") or signal.get("mint") or ""
    buttons = [InlineKeyboardButton(text=texts.WHY_BUTTON, callback_data=f"why:{signal['id']}"),
               InlineKeyboardButton(text=texts.PUMP_BUTTON, url="https://pump.fun/coin/" + quote(str(mint), safe=""))]
    inside = features.get("inside") or []
    if inside:
        buttons.append(InlineKeyboardButton(text=texts.mute_button(inside[0].get("username")),
                                            callback_data=f"mute:{inside[0].get('kol_id', 0)}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])
