"""Живые проверки внешних API. Запуск: pytest -m live (тратит кредиты Frontrun: 2 за поиск)."""

import pytest

from kairos.config import get_settings
from kairos.sources.frontrun import FrontrunClient

pytestmark = pytest.mark.live


async def test_frontrun_points_and_search():
    settings = get_settings()
    if settings.frontrun_api_key is None:
        pytest.skip("FRONTRUN_API_KEY не задан")
    async with FrontrunClient.from_settings(settings) as fr:
        points = await fr.get_points()
        assert points.current_points is not None
        page = await fr.search_tweets("pump.fun", page_size=5)
        assert isinstance(page.tweets, list)
        print(f"\npoints={points.current_points} tweets={len(page.tweets)} next={bool(page.next_cursor)}")
        for t in page.tweets[:3]:
            print(" ", t.created_at, t.screen_name, (t.text or "")[:80].replace("\n", " "))
        assert fr.credits_spent == 2
