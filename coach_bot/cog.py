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


CAPTAIN_ROLE = os.getenv("CAPTAIN_ROLE", "MRFC Captain")
SQUAD_ROLE = os.getenv("SQUAD_ROLE", "MRFC Squad")


class CoachCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ea = EAClient(PLATFORM)
        self.coach = Coach()
        self.store = Store()
        self.limits = Limiter(self.store)
        self.ready = asyncio.Event()
        self.auto_channel_id = int(os.getenv("COACH_CHANNEL_ID", "0"))

    async def _access(self, interaction, allowed_roles):
        """Return (allowed, is_owner). Owner: always allowed, no limits.
        Everyone else needs one of allowed_roles and is subject to limits."""
        if await self.bot.is_owner(interaction.user):
            return True, True
        roles = getattr(interaction.user, "roles", [])
        return any(r.name in allowed_roles for r in roles), False

    async def cog_load(self):
        self._init_task = asyncio.create_task(self._init())

    async def _init(self):
        await self.bot.wait_until_ready()
        await self.store.load(self.bot)
        if os.getenv("CLEAR_CACHE") == "1":  # one-off: reset cooldowns/cached reports
            self.store.data["cache"] = {}
            await self.store.save()
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
        allowed, owner = await self._access(interaction, {CAPTAIN_ROLE})
        if not allowed:
            return await interaction.response.send_message(
                f"Only **{CAPTAIN_ROLE}s** can use this command.", ephemeral=True)
        await interaction.response.defer(thinking=True)
        await self.ready.wait()
        cached, left = (None, 0) if owner else self.limits.get("team", TEAM_WINDOW)
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
        if not owner:
            await self.limits.put("team", text)
        await interaction.followup.send(text[:1990])

    @app_commands.command(name="coachplayer", description="Coach one player")
    @app_commands.describe(player="EA gamertag (as shown in the club)")
    async def coach_player(self, interaction: discord.Interaction, player: str):
        allowed, owner = await self._access(interaction, {SQUAD_ROLE, CAPTAIN_ROLE})
        if not allowed:
            return await interaction.response.send_message(
                f"Only **{SQUAD_ROLE}** members can use this command.", ephemeral=True)
        await interaction.response.defer(thinking=True)
        await self.ready.wait()
        key = "player:" + player.strip().lower()
        cached, left = (None, 0) if owner else self.limits.get(key, PLAYER_WINDOW)
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
        if not owner:
            await self.limits.put(key, text)
        await interaction.followup.send(text[:1990])

    @app_commands.command(name="coachcheck",
                          description="Owner only: check the coach's channel/LLM setup")
    async def coach_check(self, interaction: discord.Interaction):
        if not await self.bot.is_owner(interaction.user):
            return await interaction.response.send_message(
                "Owner only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        lines = [f"LLM: `{self.coach.provider}` / `{self.coach.model}`"]
        for label, var, need in (
            ("Debrief channel (COACH_CHANNEL_ID)", "COACH_CHANNEL_ID",
             ("view_channel", "send_messages")),
            ("Data channel (DATA_CHANNEL_ID)", "DATA_CHANNEL_ID",
             ("view_channel", "send_messages", "attach_files",
              "read_message_history")),
        ):
            raw = os.getenv(var, "")
            if not raw.strip().isdigit() or int(raw) == 0:
                lines.append(f"\u274c **{label}**: not set")
                continue
            try:
                ch = self.bot.get_channel(int(raw)) or await self.bot.fetch_channel(int(raw))
            except Exception as e:
                lines.append(f"\u274c **{label}**: can't reach it ({type(e).__name__}) "
                             f"- wrong ID or bot not in that server/channel")
                continue
            perms = ch.permissions_for(ch.guild.me)
            missing = [n for n in need if not getattr(perms, n)]
            cat = ch.category.name if getattr(ch, "category", None) else "no category"
            status = "\u2705" if not missing else "\u26a0\ufe0f"
            lines.append(f"{status} **{label}**: #{ch.name} ({cat})"
                         + (f" - missing permissions: {', '.join(missing)}" if missing else ""))
        lines.append("Data backup loaded: " + ("yes" if self.store.channel else "no (local file only)"))
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @tasks.loop(time=datetime.time(hour=3, minute=0, tzinfo=TZ))
    async def watch(self):
        """Once a day, at 3 AM (IST by default): debrief the last 24h."""
        ch = self.bot.get_channel(self.auto_channel_id)
        if not ch:
            try:
                ch = await self.bot.fetch_channel(self.auto_channel_id)
            except Exception as e:
                print(f"[debrief] cannot reach COACH_CHANNEL_ID: {e}")
                return
        try:
            summary, matches = await self._summary()
        except Exception as e:
            print(f"[debrief] stats fetch failed: {e}")
            return await ch.send(f"Daily debrief failed: `{e}`")
        cutoff = time.time() - 24 * 3600
        recent = [m for m in matches if m.get("timestamp", 0) >= cutoff]
        if not recent:
            print("[debrief] no matches in last 24h, staying quiet")
            return  # nothing played, stay quiet
        print(f"[debrief] {len(recent)} matches in last 24h, generating")
        try:
            await self._debrief(ch, summary, recent)
        except Exception as e:
            print(f"[debrief] failed: {e!r}")
            await ch.send(f"Daily debrief failed: `{e}`")

    @watch.error
    async def _watch_error(self, error):
        # never let one bad night kill the daily loop
        print(f"[debrief] loop error: {error!r}")
        if not self.watch.is_running():
            self.watch.restart()

    async def _debrief(self, ch, summary, recent):
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
