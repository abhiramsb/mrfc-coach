"""Persistent state (rate-limit cache, daily history, last targets).

If DATA_CHANNEL_ID is set, the state is saved as a coach_state.json attachment in
that private Discord channel (survives host restarts/redeploys) and reloaded on
startup. A local file is always written too as a fallback.
"""
import io
import json
import os

import discord

LOCAL = os.path.join(os.getenv("DATA_DIR", "."), "coach_state.json")
NAME = "coach_state.json"
HISTORY_KEEP = 90


class Store:
    def __init__(self):
        self.data = {"cache": {}, "history": [], "last_debrief": None}
        self.channel = None
        self._last_msg = None

    async def load(self, bot):
        try:
            with open(LOCAL) as f:
                self.data.update(json.load(f))
        except (FileNotFoundError, ValueError):
            pass
        cid = int(os.getenv("DATA_CHANNEL_ID", "0"))
        if not cid:
            return
        try:
            self.channel = bot.get_channel(cid) or await bot.fetch_channel(cid)
            async for msg in self.channel.history(limit=50):
                if msg.author == bot.user and any(a.filename == NAME for a in msg.attachments):
                    att = next(a for a in msg.attachments if a.filename == NAME)
                    self.data.update(json.loads(await att.read()))
                    self._last_msg = msg
                    break
        except Exception as e:  # keep running on local state
            print("Data channel load failed:", e)
            self.channel = None

    async def save(self):
        tmp = LOCAL + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, LOCAL)
        if not self.channel:
            return
        try:
            raw = json.dumps(self.data).encode()
            new = await self.channel.send(
                "state backup", file=discord.File(io.BytesIO(raw), filename=NAME))
            if self._last_msg:
                try:
                    await self._last_msg.delete()
                except discord.HTTPException:
                    pass
            self._last_msg = new
        except Exception as e:
            print("Data channel save failed:", e)

    async def add_snapshot(self, snap):
        h = self.data["history"]
        h[:] = [s for s in h if s.get("date") != snap["date"]] + [snap]
        del h[:-HISTORY_KEEP]
        await self.save()
