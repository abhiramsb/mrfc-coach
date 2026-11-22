"""Turn raw EA match + member data into compact stats for coaching."""
from collections import defaultdict


def _i(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# Decoded from EA's unlabeled match_event_aggregate_* fields by comparing with the
# in-game stats screen (8 player-matches, 8/8 exact): code 174 = dribbles COMPLETED.
CODE_DRIBBLES_COMPLETED = "174"


def event_count(p, code):
    total = 0
    for i in range(4):
        for kv in (p.get(f"match_event_aggregate_{i}") or "").split(","):
            if kv.startswith(code + ":"):
                total += _i(kv.split(":")[1])
    return total


def summarise(club_id, matches, members, last_n=15):
    club_id = str(club_id)
    matches = matches[:last_n]
    team = dict(played=0, w=0, d=0, l=0, gf=0, ga=0, clean=0)
    players = defaultdict(lambda: defaultdict(float))
    pos_games = defaultdict(lambda: defaultdict(int))
    opp = dict(shots=0, goals=0, pass_made=0, pass_att=0, tackle_att=0)
    ours = dict(shots=0, pass_att=0)

    for m in matches:
        c = m.get("clubs", {}).get(club_id)
        if not c:
            continue
        gf, ga = _i(c.get("goals")), _i(c.get("goalsAgainst"))
        team["played"] += 1
        team["gf"] += gf
        team["ga"] += ga
        team["clean"] += ga == 0
        team["w"] += gf > ga
        team["d"] += gf == ga
        team["l"] += gf < ga
        for oid, plist in m.get("players", {}).items():
            if oid == club_id:
                continue
            for p in plist.values():
                opp["shots"] += _f(p.get("shots"))
                opp["goals"] += _f(p.get("goals"))
                opp["pass_made"] += _f(p.get("passesmade"))
                opp["pass_att"] += _f(p.get("passattempts"))
                opp["tackle_att"] += _f(p.get("tackleattempts"))
        for p in m.get("players", {}).get(club_id, {}).values():
            players[p.get("playername", "?")]["dribbles_done"] += event_count(
                p, CODE_DRIBBLES_COMPLETED)
            ours["shots"] += _f(p.get("shots"))
            ours["pass_att"] += _f(p.get("passattempts"))
            n = p.get("playername", "?")
            s = players[n]
            s["games"] += 1
            for k in ("goals", "assists", "shots", "passesmade", "passattempts",
                      "tacklesmade", "tackleattempts", "saves", "mom",
                      "goalsconceded", "redcards"):
                s[k] += _f(p.get(k))
            s["rating_sum"] += _f(p.get("rating"))
            pos_games[n][p.get("pos", "?")] += 1

    rows = []
    for n, s in players.items():
        g = s["games"]
        rows.append({
            "name": n,
            "games": int(g),
            "main_pos": max(pos_games[n], key=pos_games[n].get),
            "goals": int(s["goals"]),
            "assists": int(s["assists"]),
            "shots": int(s["shots"]),
            "shot_conv_pct": round(100 * s["goals"] / s["shots"]) if s["shots"] else None,
            "pass_pct": round(100 * s["passesmade"] / s["passattempts"]) if s["passattempts"] else None,
            "tackle_pct": round(100 * s["tacklesmade"] / s["tackleattempts"]) if s["tackleattempts"] else None,
            "tackles_per_game": round(s["tacklesmade"] / g, 1),
            "dribbles_completed_per_game": round(s["dribbles_done"] / g, 1),
            "saves": int(s["saves"]),
            "mom": int(s["mom"]),
            "avg_rating": round(s["rating_sum"] / g, 2),
        })
    rows.sort(key=lambda r: -r["games"])

    pos_count = {}
    for r in rows:
        pos_count[r["main_pos"]] = pos_count.get(r["main_pos"], 0) + 1

    need = {"goalkeeper": 1, "defender": 3, "midfielder": 3, "forward": 2}
    recruitment_needs = {
        k: max(0, v - pos_count.get(k, 0)) for k, v in need.items()
        if pos_count.get(k, 0) < v
    }
    g = team["played"] or 1
    opponent_context = {
        "our_shots_per_game": round(ours["shots"] / g, 1),
        "shots_conceded_per_game": round(opp["shots"] / g, 1),
        "opp_shot_conversion_pct": round(100 * opp["goals"] / opp["shots"]) if opp["shots"] else None,
        "our_pass_attempts_per_game": round(ours["pass_att"] / g),
        "opp_pass_attempts_per_game": round(opp["pass_att"] / g),
        "opp_pass_pct": round(100 * opp["pass_made"] / opp["pass_att"]) if opp["pass_att"] else None,
        "opp_tackle_attempts_per_game": round(opp["tackle_att"] / g),
    }
    return {
        "recruitment_needs": recruitment_needs,
        "opponent_context": opponent_context,
        "team_last_matches": team,
        "goals_for_per_game": round(team["gf"] / team["played"], 2) if team["played"] else None,
        "goals_against_per_game": round(team["ga"] / team["played"], 2) if team["played"] else None,
        "players_in_recent_matches": rows,
        "position_spread": pos_count,
        "alltime_member_stats": members.get("members", []) if isinstance(members, dict) else [],
    }
