import asyncio
import datetime
import os
import time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .analysis import summarise
from .coach import Coach
from .ea_client import EAClient
from .limits import Limiter, human
from .store import Store

CLUB_ID = os.getenv("CLUB_ID", "").strip()
if not CLUB_ID:
    raise RuntimeError(
        "CLUB_ID is not set. Set it to MRFC Academy's EA club ID (see .env.example)."
    )
PLATFORM = os.getenv("PLATFORM", "common-gen5")
TEAM_WINDOW = 24 * 3600          # /coach: once per 24h (team-wide)
PLAYER_WINDOW = 7 * 24 * 3600    # /coachplayer: once per week per player
TZ = ZoneInfo(os.getenv("COACH_TZ", "Asia/Kolkata"))


class CoachCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ea = EAClient(PLATFORM)
        self.coach = Coach()
        self.store = Store()
        self.limits = Limiter(self.store)
        self.ready = asyncio.Event()
        self.auto_channel_id = int(os.getenv("COACH_CHANNEL_ID", "0"))

    async def cog_load(self):
        self._init_task = asyncio.create_task(self._init())

    async def _init(self):
        await self.bot.wait_until_ready()
        await self.store.load(self.bot)
        self.ready.set()
        if self.auto_channel_id:
            self.watch.start()

    def cog_unload(self):
        self.watch.cancel()
        self._init_task.cancel()

    def _memory(self):
        d = self.store.data
        mem = {}
        if d["history"]:
            mem["history_daily_snapshots"] = d["history"][-14:]
        if d["last_debrief"]:
            mem["previous_debrief_with_targets"] = d["last_debrief"]["text"]
            mem["previous_debrief_date"] = d["last_debrief"]["date"]
        return mem

    async def _summary(self, last_n=15):
        matches = await self.ea.all_matches(CLUB_ID)
        members = await self.ea.member_stats(CLUB_ID)
        return summarise(CLUB_ID, matches, members, last_n), matches

    @app_commands.command(name="coach", description="Coach the team from recent EA stats")
    @app_commands.describe(question="Optional: ask the coach something specific",
                           matches="How many recent matches to analyse (default 15)")
    async def coach_cmd(self, interaction: discord.Interaction,
                        question: str | None = None, matches: int = 15):
        await interaction.response.defer(thinking=True)
        await self.ready.wait()
        cached, left = self.limits.get("team", TEAM_WINDOW)
        if cached:
            return await interaction.followup.send(
                f"{cached[:1850]}\n\n*Latest team report (limit: once per 24h). "
                f"New one in {human(left)}.*")
        try:
            summary, _ = await self._summary(max(3, min(matches, 40)))
            summary["coach_memory"] = self._memory()
            text = await self.coach.team_report(summary, question)
        except Exception as e:
            return await interaction.followup.send(f"Couldn't fetch/coach: `{e}`")
        await self.limits.put("team", text)
        await interaction.followup.send(text[:1990])

    @app_commands.command(name="coachplayer", description="Coach one player")
    @app_commands.describe(player="EA gamertag (as shown in the club)")
    async def coach_player(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(thinking=True)
        await self.ready.wait()
        key = "player:" + player.strip().lower()
        cached, left = self.limits.get(key, PLAYER_WINDOW)
        if cached:
            return await interaction.followup.send(
                f"{cached[:1850]}\n\n*Latest report for {player} (limit: once per week "
                f"per player). New one in {human(left)}.*")
        try:
            summary, _ = await self._summary()
            summary["coach_memory"] = self._memory()
            text = await self.coach.player_report(summary, player)
        except Exception as e:
            return await interaction.followup.send(f"Couldn't fetch/coach: `{e}`")
        if not text:
            return await interaction.followup.send(f"No data found for `{player}`.")
        await self.limits.put(key, text)
        await interaction.followup.send(text[:1990])

    @tasks.loop(time=datetime.time(hour=3, minute=0, tzinfo=TZ))
    async def watch(self):
        """Once a day, at 3 AM (IST by default): debrief the last 24h."""
        ch = self.bot.get_channel(self.auto_channel_id)
        if not ch:
            return
        try:
            summary, matches = await self._summary()
        except Exception as e:
            return await ch.send(f"Daily debrief failed: `{e}`")
        cutoff = time.time() - 24 * 3600
        recent = [m for m in matches if m.get("timestamp", 0) >= cutoff]
        if not recent:
            return  # nothing played, stay quiet
        day = summarise(CLUB_ID, recent, {"members": []}, last_n=len(recent))
        day["season_context"] = summary["team_last_matches"]
        day["alltime_member_stats"] = summary["alltime_member_stats"]
        day["coach_memory"] = self._memory()
        text = await self.coach.team_report(
            day, "Daily debrief of the matches played in the last 24 hours.")
        await ch.send(text[:1990])
        today = datetime.datetime.now(TZ).date().isoformat()
        await self.store.add_snapshot({
            "date": today,
            "record": day["team_last_matches"],
            "players": [{k: r[k] for k in (
                "name", "games", "goals", "assists", "pass_pct", "shot_conv_pct",
                "tackle_pct", "dribbles_completed_per_game", "avg_rating")}
                for r in day["players_in_recent_matches"]],
        })
        self.store.data["last_debrief"] = {"date": today, "text": text[:1500]}
        await self.store.save()

    @watch.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(CoachCog(bot))
