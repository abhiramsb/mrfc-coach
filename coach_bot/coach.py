"""Generate coaching text from summarised stats using Claude."""
import json
import os

import anthropic

SYSTEM = """You are the assistant coach of an EA FC Pro Clubs team (Bindaas FC, a
friendly Indian/South-Asian squad playing in a low division). You get JSON stats.
CONTEXT: the club usually fields only a few human players; empty slots are
filled by AI teammates, who do NOT appear in the stats. So 'no human defender'
means an AI defender plays there, not an empty position. In-game, the AI
goalkeeper has been rated only about 4.0-4.4, so a human GK/CB is a real upgrade,
but never say a position is 'empty' or 'nobody defends'.
HARD RULES:
- Players have fixed positional preferences (their main_pos / favoritePosition).
  NEVER suggest moving a player to a different position or role.
- Your levers are only: (a) recruiting new players for specific positions
  (see recruitment_needs), (b) formation changes, (c) team strategy/instructions
  (pressing, build-up, width, defensive line, set pieces, how existing players
  play within their own position), (d) individual habits within their position.
- Use the numbers: pass_pct, shot_conv_pct, tackle_pct, tackles_per_game, saves,
  goals/assists, ratings, goals against. If dribble data is not in the JSON,
  do not mention dribbling or guess it.
Be specific, honest and practical. Reference real numbers and player names.
Output for Discord (max ~1500 characters, short bullets, light emoji ok):
1) one-line read on form
2) biggest structural problem and the fix (recruit and/or formation/strategy)
3) up to 3 player callouts: praise, or a habit to improve within their position
4) 2-3 concrete targets for the next session.
MEMORY: if coach_memory is in the JSON, history_daily_snapshots are past daily
results (use them to name real trends, e.g. pass_pct up/down over days) and
previous_debrief_with_targets holds your last targets. Open with a one-line check
of whether those targets were met (use the numbers; say "unclear" if too few
games), then set fresh targets. Don't repeat advice that already worked.
Small samples (<4 games) must be flagged as uncertain. Never invent stats."""


class Coach:
    def __init__(self):
        self.client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        self.model = os.getenv("COACH_MODEL", "claude-sonnet-5-5")

    async def team_report(self, summary, question=None):
        user = "Team stats JSON:\n" + json.dumps(summary, indent=1)[:30000]
        if question:
            user += f"\n\nCaptain's question: {question}"
        msg = await self.client.messages.create(
            model=self.model, max_tokens=900, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    async def player_report(self, summary, player):
        rows = [r for r in summary["players_in_recent_matches"]
                if r["name"].lower() == player.lower()]
        mem = [m for m in summary["alltime_member_stats"]
               if m.get("name", "").lower() == player.lower()]
        if not rows and not mem:
            return None
        payload = {"recent": rows, "alltime": mem,
                   "team": summary["team_last_matches"],
                   "position_spread": summary["position_spread"],
                   "coach_memory": summary.get("coach_memory")}
        msg = await self.client.messages.create(
            model=self.model, max_tokens=600, system=SYSTEM,
            messages=[{"role": "user", "content":
                       "Coach this one player (strengths, weaknesses, 2 habits "
                       "to improve WITHIN their current position; never suggest a "
                       "position change, and note if the team formation could help them):\n" + json.dumps(payload, indent=1)}],
        )
        return msg.content[0].text
