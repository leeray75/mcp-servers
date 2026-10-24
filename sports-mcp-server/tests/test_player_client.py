"""URL builders for the player endpoints: strict inputs and the host allow-list (spec 5.1, 7)."""

from __future__ import annotations

import inspect

import httpx
import pytest

from sports_mcp.config import Settings
from sports_mcp.errors import InvalidInputError, UpstreamError
from sports_mcp.espn_client import EspnClient
from sports_mcp.leagues import resolve_league
from tests.conftest import FAKE_AUTH_TOKEN

NFL = resolve_league("nfl")


@pytest.fixture
def client() -> EspnClient:
    settings = Settings(mcp_auth_token=FAKE_AUTH_TOKEN)  # type: ignore[arg-type]
    return EspnClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_search_url_encodes_the_query_and_pins_the_type(client: EspnClient) -> None:
    url = client.player_search_url("Cam Skattebo", 50)
    assert url == (
        "https://site.api.espn.com/apis/common/v3/search?limit=50&query=Cam+Skattebo&type=player"
    )


@pytest.mark.parametrize("bad", ["a/b", "a?b", "a_b", "x" * 61, "", "a\nb", "a;b"])
def test_search_query_is_restricted(client: EspnClient, bad: str) -> None:
    with pytest.raises(InvalidInputError):
        client.player_search_url(bad, 50)


def test_profile_url_is_digits_only(client: EspnClient) -> None:
    assert client.player_profile_url(NFL, "4696981").endswith(
        "/apis/common/v3/sports/football/nfl/athletes/4696981"
    )
    with pytest.raises(UpstreamError):
        client.player_profile_url(NFL, "4696981/../x")


def test_stats_url_maps_season_type_to_seasontype_3_only_for_post(client: EspnClient) -> None:
    regular = client.player_stats_url(NFL, "3912547", "regular")
    assert "seasontype" not in regular and regular.endswith("/athletes/3912547/stats")
    post = client.player_stats_url(NFL, "3912547", "post")
    assert post.endswith("/athletes/3912547/stats?seasontype=3")
    with pytest.raises(InvalidInputError):
        client.player_stats_url(NFL, "3912547", "all")


def test_gamelog_url_takes_no_season_type(client: EspnClient) -> None:
    params = inspect.signature(EspnClient.player_gamelog_url).parameters
    assert "season_type" not in params  # D11: ESPN ignores seasontype on the gamelog
    assert client.player_gamelog_url(NFL, "4696981", None).endswith("/gamelog")
    assert client.player_gamelog_url(NFL, "4696981", 2025).endswith("/gamelog?season=2025")
    with pytest.raises(InvalidInputError):
        client.player_gamelog_url(NFL, "4696981", 1989)


def test_every_player_url_stays_on_the_configured_host(client: EspnClient) -> None:
    urls = [
        client.player_search_url("skattebo", 50),
        client.player_profile_url(NFL, "1"),
        client.player_stats_url(NFL, "1", "post"),
        client.player_gamelog_url(NFL, "1", 2025),
    ]
    assert all(u.startswith("https://site.api.espn.com/") for u in urls)
