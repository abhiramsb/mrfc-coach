"""Generate coaching text from summarised stats using a configurable LLM.

Provider is chosen with LLM_PROVIDER: gemini (default, free tier), groq,
openrouter, or anthropic (paid Claude API). All but anthropic use an
OpenAI-compatible chat endpoint.
"""
import asyncio
import json
import os

import aiohttp

PROVIDERS = {
    # name: (base_url, default_model)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai",
               "gemini-3.5-flash"),
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "openrouter": ("https://openrouter.ai/api/v1",
                   "google/gemma-4-31b-it:free"),
}

SYSTEM = """You are the assistant coach of an EA FC Pro Clubs team (MRFC Academy, a
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
        self.provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        if self.provider == "anthropic":
            import anthropic  # only needed for the paid Claude option
            self.client = anthropic.AsyncAnthropic(
                api_key=os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))
            self.model = (os.getenv("LLM_MODEL") or os.getenv("COACH_MODEL")
                          or "claude-sonnet-5-5")
        elif self.provider in PROVIDERS:
            base, default_model = PROVIDERS[self.provider]
            self.base_url = (os.getenv("LLM_BASE_URL") or base).rstrip("/")
            self.model = os.getenv("LLM_MODEL") or default_model
            self.api_key = os.getenv("LLM_API_KEY", "")
            if not self.api_key:
                raise RuntimeError("LLM_API_KEY is not set")
        else:
            raise RuntimeError(f"Unknown LLM_PROVIDER '{self.provider}'. "
                               f"Use one of: {', '.join(PROVIDERS)}, anthropic")

    async def _chat(self, user, max_tokens):
        if self.provider == "anthropic":
            msg = await self.client.messages.create(
                model=self.model, max_tokens=max_tokens, system=SYSTEM,
                messages=[{"role": "user", "content": user}])
            return msg.content[0].text
        body = {"model": self.model,
                # headroom: some free models spend tokens on hidden reasoning
                "max_tokens": max_tokens * 2,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": user}]}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"
        last = None
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=90)) as http:
            for attempt in range(3):
                async with http.post(url, json=body, headers=headers) as r:
                    if r.status in (429, 500, 502, 503):
                        last = f"{r.status} from {self.provider}"
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    data = await r.json(content_type=None)
                    if r.status != 200:
                        raise RuntimeError(
                            f"{self.provider} error {r.status}: "
                            f"{str(data)[:200]}")
                    text = (data["choices"][0]["message"].get("content") or "").strip()
                    if not text:
                        raise RuntimeError(f"{self.provider} returned empty text")
                    return text
        raise RuntimeError(f"{last} (free-tier limit? try again later)")

    async def team_report(self, summary, question=None):
        user = "Team stats JSON:\n" + json.dumps(summary, indent=1)[:30000]
        if question:
            user += f"\n\nCaptain's question: {question}"
        return await self._chat(user, 900)

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
        return await self._chat(
            "Coach this one player (strengths, weaknesses, 2 habits "
            "to improve WITHIN their current position; never suggest a "
            "position change, and note if the team formation could help them):\n"
            + json.dumps(payload, indent=1), 600)
