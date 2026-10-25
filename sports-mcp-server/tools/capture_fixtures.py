"""Capture trimmed real ESPN responses as test fixtures (tests/fixtures/*.json).

Run from the project root with any Python 3.10+ (stdlib only):

    python tools/capture_fixtures.py            # re-capture everything in PLAN
    python tools/capture_fixtures.py nfl_live   # only fixtures whose name contains the argument

Trimming removes tracking/ad/link/image fields and bulky play-by-play data but keeps ESPN's
real structure. Fixtures are static after capture: tests never touch the network.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

BASE = "https://site.api.espn.com"
SITE = "/apis/site/v2/sports"
V2 = "/apis/v2/sports"
PLAYERS = "/apis/common/v3/sports"
SEARCH = "/apis/common/v3/search"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
UA = "Mozilla/5.0 sports-mcp-fixture-capture"

DROP = {
    "links",
    "link",
    "logos",
    "logo",
    "headshot",
    "headshots",
    "$ref",
    "uid",
    "guid",
    "videos",
    "article",
    "news",
    "tuneInPrompt",
    "geoBroadcasts",
    "highlights",
    "images",
    "jerseyImages",
    "drives",
    "playsMap",
    "atBats",
    "commentary",
    "seasonseries",
    "keyEvents",
    "lastFiveGames",
    "predictor",
    "officials",
    "tracking",
    "franchise",
    "nextEvent",
    "parent",
    "recent",
    "wallclockAvailable",
    "onIce",
    "format",
    "meta",
    "gameInfo_images",
    "alternativeText",
}

# (fixture name, path + query, optional hook)
PLAN: list[tuple[str, str]] = [
    ("nfl_scoreboard_week", f"{SITE}/football/nfl/scoreboard"),
    ("nfl_scoreboard_empty", f"{SITE}/football/nfl/scoreboard?dates=20261006"),
    ("nba_scoreboard_pre", f"{SITE}/basketball/nba/scoreboard"),
    ("nba_scoreboard_final", f"{SITE}/basketball/nba/scoreboard?dates=20260605"),
    ("mlb_scoreboard_pre", f"{SITE}/baseball/mlb/scoreboard"),
    ("mlb_scoreboard_final", f"{SITE}/baseball/mlb/scoreboard?dates=20260930"),
    ("nhl_scoreboard_today", f"{SITE}/hockey/nhl/scoreboard"),
    ("nhl_scoreboard_final", f"{SITE}/hockey/nhl/scoreboard?dates=20261003"),
    ("epl_scoreboard_pre", f"{SITE}/soccer/eng.1/scoreboard"),
    ("epl_scoreboard_final", f"{SITE}/soccer/eng.1/scoreboard?dates=20260920"),
    ("wnba_scoreboard_live", f"{SITE}/basketball/wnba/scoreboard"),
    ("nfl_summary_live", f"{SITE}/football/nfl/summary?event=401872971"),
    ("nfl_summary_final", f"{SITE}/football/nfl/summary?event=401872965"),
    ("nfl_summary_pre", f"{SITE}/football/nfl/summary?event=401872974"),
    ("nba_summary_final", f"{SITE}/basketball/nba/summary?event=401859964"),
    ("nba_summary_pre", f"{SITE}/basketball/nba/summary?event=401914127"),
    ("mlb_summary_final", f"{SITE}/baseball/mlb/summary?event=401907972"),
    ("mlb_summary_pre", f"{SITE}/baseball/mlb/summary?event=401908003"),
    ("nhl_summary_final", f"{SITE}/hockey/nhl/summary?event=401891776"),
    ("wnba_summary_live", f"{SITE}/basketball/wnba/summary?event=401918295"),
    ("epl_summary_final", f"{SITE}/soccer/eng.1/summary?event=401879276"),
    ("epl_summary_pre", f"{SITE}/soccer/eng.1/summary?event=401879268"),
    ("nfl_standings", f"{V2}/football/nfl/standings?level=3"),
    ("nba_standings", f"{V2}/basketball/nba/standings"),
    ("nhl_standings", f"{V2}/hockey/nhl/standings"),
    ("mlb_standings", f"{V2}/baseball/mlb/standings?level=3"),
    ("epl_standings", f"{V2}/soccer/eng.1/standings"),
    ("ncaaf_standings", f"{V2}/football/college-football/standings"),
    ("nfl_teams", f"{SITE}/football/nfl/teams?limit=1000"),
    ("mlb_teams", f"{SITE}/baseball/mlb/teams?limit=1000"),
    ("epl_schedule_ars_fixtures", f"{SITE}/soccer/eng.1/teams/359/schedule?fixture=true"),
    ("epl_teams", f"{SITE}/soccer/eng.1/teams?limit=1000"),
    ("nfl_team_buf", f"{SITE}/football/nfl/teams/2"),
    ("nfl_schedule_buf", f"{SITE}/football/nfl/teams/2/schedule"),
    ("mlb_schedule_atl", f"{SITE}/baseball/mlb/teams/15/schedule"),
    ("epl_schedule_ars", f"{SITE}/soccer/eng.1/teams/359/schedule"),
    ("nfl_news", f"{SITE}/football/nfl/news?limit=5"),
    ("epl_news", f"{SITE}/soccer/eng.1/news?limit=5"),
    # Player endpoints (spec: docs/specs/player-stats). Names are prefixed "player_".
    ("player_search_skattebo", f"{SEARCH}?query=skattebo&limit=5&type=player"),
    ("player_search_skattebu", f"{SEARCH}?query=skattebu&limit=5&type=player"),
    ("player_search_cam_skattebu", f"{SEARCH}?query=Cam+Skattebu&limit=5&type=player"),
    ("player_search_cam_skatteb", f"{SEARCH}?query=Cam+Skatteb&limit=5&type=player"),
    ("player_search_skatteb", f"{SEARCH}?query=skatteb&limit=5&type=player"),
    ("player_search_smith", f"{SEARCH}?query=smith&limit=10&type=player"),
    ("player_gl_nfl_skattebo_2025", f"{PLAYERS}/football/nfl/athletes/4696981/gamelog?season=2025"),
    ("player_gl_nfl_skattebo_2026", f"{PLAYERS}/football/nfl/athletes/4696981/gamelog"),
    ("player_gl_nfl_skattebo_2015", f"{PLAYERS}/football/nfl/athletes/4696981/gamelog?season=2015"),
    ("player_stats_nfl_skattebo", f"{PLAYERS}/football/nfl/athletes/4696981/stats"),
    ("player_gl_nfl_darnold_2025", f"{PLAYERS}/football/nfl/athletes/3912547/gamelog?season=2025"),
    ("player_stats_nfl_darnold", f"{PLAYERS}/football/nfl/athletes/3912547/stats"),
    (
        "player_stats_nfl_darnold_post",
        f"{PLAYERS}/football/nfl/athletes/3912547/stats?seasontype=3",
    ),
    ("player_gl_nfl_maye_2025", f"{PLAYERS}/football/nfl/athletes/4431452/gamelog?season=2025"),
    ("player_gl_nfl_byard_2025", f"{PLAYERS}/football/nfl/athletes/2574056/gamelog?season=2025"),
    ("player_gl_nfl_adams_2025", f"{PLAYERS}/football/nfl/athletes/16800/gamelog?season=2025"),
    ("player_stats_nfl_adams", f"{PLAYERS}/football/nfl/athletes/16800/stats"),
    ("player_gl_nba_lebron_2025", f"{PLAYERS}/basketball/nba/athletes/1966/gamelog?season=2025"),
    ("player_stats_nba_lebron", f"{PLAYERS}/basketball/nba/athletes/1966/stats"),
    ("player_gl_mlb_judge_2025", f"{PLAYERS}/baseball/mlb/athletes/33192/gamelog?season=2025"),
    ("player_profile_nfl_skattebo", f"{PLAYERS}/football/nfl/athletes/4696981"),
    ("player_profile_nba_lebron", f"{PLAYERS}/basketball/nba/athletes/1966"),
    ("player_stats_mlb_ohtani", f"{PLAYERS}/baseball/mlb/athletes/39832/stats"),
    # Team rank and turnovers (spec: docs/specs/team-rank-and-turnovers). Captured 2026-10-24.
    ("ncaaf_scoreboard_ranked", f"{SITE}/football/college-football/scoreboard?dates=20261024"),
    ("ncaaf_summary_ranked", f"{SITE}/football/college-football/summary?event=401858274"),
    ("ncaaf_team_miami", f"{SITE}/football/college-football/teams/2390"),
    ("ncaaf_teams_directory", f"{SITE}/football/college-football/teams?limit=1000"),
]


def trim(value: Any) -> Any:
    if isinstance(value, dict):
        kept = {k: trim(v) for k, v in value.items() if k not in DROP}
        web = value.get("links", {}).get("web") if isinstance(value.get("links"), dict) else None
        if "headline" in value and isinstance(web, dict) and web.get("href"):
            kept["links"] = {
                "web": {"href": web["href"]}
            }  # news articles: the URL is data we serve
        return kept
    if isinstance(value, list):
        return [trim(v) for v in value]
    return value


def reduce_structure(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Name-specific size reductions that keep the real structure."""
    if "plays" in data and isinstance(data["plays"], list):
        # Keep scoring plays only: they are what the server reads from play-by-play.
        data["plays"] = [p for p in data["plays"] if p.get("scoringPlay")]
    if isinstance(data.get("winprobability"), list):
        data["winprobability"] = data["winprobability"][-3:]
    if isinstance(data.get("standings"), dict):
        data.pop("standings")  # summary's embedded standings block (unused)
    for league in data.get("leagues", []) if isinstance(data.get("leagues"), list) else []:
        for item in league.get("calendar", []):
            if isinstance(item, dict):
                item.pop("entries", None)
    if name.endswith("_standings"):
        if name.startswith("ncaaf"):
            data["children"] = data["children"][:2]
        cut_split_records(data)
    if name.endswith("_teams"):
        for sport in data.get("sports", []):
            for lg in sport.get("leagues", []):
                lg["teams"] = lg["teams"][:60]
    for key in ("seasons",):
        if isinstance(data.get(key), list):
            data[key] = data[key][:2]
    if name == "ncaaf_scoreboard_ranked":
        # Keep the file under the 300 KB fixture budget: the scoreboard reader never uses the
        # per-game leaders block (about 1.2 KB per game).
        for event in data.get("events", []):
            for comp in event.get("competitions", []):
                comp.pop("leaders", None)
    return data


def cut_split_records(node: dict[str, Any]) -> None:
    """Standings entries repeat their stats for Home/Away splits; keep the overall block only."""
    entries = (
        node.get("standings", {}).get("entries", [])
        if isinstance(node.get("standings"), dict)
        else []
    )
    for entry in entries:
        stats = entry.get("stats", [])
        for i, stat in enumerate(stats):
            if stat.get("name") == "overall":
                # Keep the overall numbers plus the later record blocks (Home, vs. Conf., ...).
                entry["stats"] = stats[: i + 1] + [s for s in stats[i + 1 :] if "summary" in s]
                break
    for child in node.get("children", []):
        cut_split_records(child)


def fetch(path: str) -> dict[str, Any]:
    request = urllib.request.Request(BASE + path, headers={"User-Agent": UA})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        result: dict[str, Any] = json.loads(response.read())
        return result


def main(argv: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = argv[0] if argv else ""
    for name, path in PLAN:
        if wanted not in name:
            continue
        data = reduce_structure(name, trim(fetch(path)))
        target = OUT / f"{name}.json"
        target.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), "utf-8")
        print(f"{name}: {target.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
