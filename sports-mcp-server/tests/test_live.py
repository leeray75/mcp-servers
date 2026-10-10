"""Opt-in smoke checks against the real ESPN API: ``pytest -m live --no-cov``.

Deselected by default (``-m 'not live'`` in pytest's addopts). These only assert shapes that
hold in any season, since the data changes daily.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from sports_mcp.config import Settings
from sports_mcp.espn_client import EspnClient
from sports_mcp.tools import SportsService

pytestmark = pytest.mark.live


@pytest.fixture
async def live() -> AsyncIterator[SportsService]:
    settings = Settings()
    service = SportsService(settings, EspnClient(settings))
    yield service
    await service.client.aclose()


async def test_live_scoreboard_standings_news_teams(live: SportsService) -> None:
    board = await live.get_scoreboard("nfl")
    assert board.data["meta"]["as_of"] and board.data["count"] >= 0
    standings = await live.get_standings("nfl")
    assert len(standings.data["groups"]) == 8
    news = await live.get_news("nfl", limit=3)
    assert news.data["count"] >= 1
    team = await live.find_team("nfl", "bills")
    assert team.data["matches"][0]["abbr"] == "BUF"
    schedule = await live.get_team_schedule("nfl", "BUF")
    assert schedule.data["count"] >= 1


async def test_live_game_detail_for_a_recent_game(live: SportsService) -> None:
    for league in ("nfl", "mlb", "nhl", "nba", "epl"):
        for days in range(0, 8):
            from datetime import UTC, datetime, timedelta

            day = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
            board = await live.get_scoreboard(league, date=day, status="final", limit=1)
            if board.data["games"]:
                game_id = board.data["games"][0]["id"]
                detail = await live.get_game(league, game_id, detail="performers")
                assert detail.data["game"]["status"]["completed"] is True
                return
    pytest.skip("no finished game found in the last week")
