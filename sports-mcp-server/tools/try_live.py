"""Developer helper: call the service layer against live ESPN and print text + sizes.

Usage (WSL): python tools/try_live.py scoreboard nfl [date]
             python tools/try_live.py game nfl 401872965 [summary|performers|boxscore]
             python tools/try_live.py odds|standings|team|schedule|news ...
"""

from __future__ import annotations

import asyncio
import json
import sys

from sports_mcp.config import Settings
from sports_mcp.espn_client import EspnClient
from sports_mcp.tools import SportsService


async def main(argv: list[str]) -> None:
    settings = Settings()
    service = SportsService(settings, EspnClient(settings))
    cmd, args = argv[0], argv[1:]
    if cmd == "scoreboard":
        out = await service.get_scoreboard(args[0], args[1] if len(args) > 1 else "today")
    elif cmd == "game":
        out = await service.get_game(args[0], args[1], args[2] if len(args) > 2 else "summary")
    elif cmd == "odds":
        out = await service.get_odds(
            args[0],
            args[1] if len(args) > 1 and not args[1].isdigit() else "today",
            args[1] if len(args) > 1 and args[1].isdigit() else None,
        )
    elif cmd == "standings":
        out = await service.get_standings(args[0], None, args[1] if len(args) > 1 else None)
    elif cmd == "team":
        out = await service.find_team(args[0], args[1])
    elif cmd == "schedule":
        out = await service.get_team_schedule(args[0], args[1])
    elif cmd == "news":
        out = await service.get_news(args[0])
    else:
        raise SystemExit("unknown command")
    print(out.text)
    print(f"--- text={len(out.text.encode())}B structured={len(json.dumps(out.data).encode())}B")
    await service.client.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
