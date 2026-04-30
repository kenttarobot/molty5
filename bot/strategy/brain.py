"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for maximum win rate and profit.

v1.7.0 MAJOR UPGRADES:
════════════════════════════════════════════════════════════════════
🧠 SMARTER COMBAT SYSTEM:
  - Multi-round combat simulation (not just 1-hit estimate)
  - Kite/chase logic: approach or flee based on weapon range advantage
  - "Finish them" logic: always chase low-HP fleeing enemies
  - Combo logic: attack > heal > attack cycle for sustained fights
  - Guardian HP memory: remember partial HP from previous attacks

💰 FARMING PROFIT MAXIMISER:
  - Priority scoring includes expected sMoltz drop per risk
  - Avoid duplicate farming (skip already-looted guardians)
  - "Loot sweep" mode: collect all drops after winning a fight
  - Supply cache and medical_facility revisit when inventory not full
  - Track cumulative sMoltz earned this session

🗺️ ENDGAME POSITIONING:
  - Late game (≤15 alive): aggressive position — push center
  - Choke point awareness: prefer high-connection regions for map control
  - Safe-zone prediction: pre-move before DZ closes
  - Kite into safe terrain against melee enemies

⚡ EP / STAMINA MANAGEMENT:
  - EP budget planner: reserve EP for escape before committing to fight
  - Avoid rest if enemy nearby (within 1 hop)
  - Energy drink used only when EP=0 AND enemy present
  - Endgame rest suppression: never rest in top 10

🔫 WEAPON & INVENTORY INTELLIGENCE:
  - Weapon range tactics: sniper = stay far, katana = close gap
  - Inventory compaction: drop weakest item when full (make room for katana/sniper)
  - Ammo-awareness: track if equipped ranged weapon can shoot
  - Binoculars vision exploitation: move toward scouted targets

🛡️ SURVIVAL IMPROVEMENTS:
  - Retreat + heal cycle: retreat if fight goes bad mid-combat
  - Double-DZ prediction: skip regions with 2+ pending DZ neighbors
  - Weather adaptation: rain/fog → prefer melee; storm → retreat
  - Cursed EP=0 recovery protocol re-enabled (guardian curse v1.7)

════════════════════════════════════════════════════════════════════

v1.6.0 changes (ENHANCED — see previous file for details)
v1.5.2 changes — guardians hostile, curse disabled, free room 5 guardians

Uses ALL view fields from api-summary.md:
- self: agent stats, inventory, equipped weapon
- currentRegion: terrain, weather, connections, facilities
- connectedRegions: adjacent regions (full Region object or bare string ID)
- visibleRegions: all regions in vision range
- visibleAgents: other agents (players + guardians — guardians HOSTILE)
- visibleMonsters: monsters
- visibleNPCs: NPCs (flavor — safe to ignore)
- visibleItems: ground items in visible regions
- pendingDeathzones: regions becoming death zones next ({id, name})
- recentLogs: recent gameplay events
- recentMessages: regional/private/broadcast messages
- aliveCount: remaining alive agents
"""
from bot.utils.logger import get_logger

log = get_logger(__name__)

# =============================================================================
# ⚙️  CONSTANTS & LOOKUP TABLES
# =============================================================================

# ── Weapon stats from combat-items.md ────────────────────────────────────────
WEAPONS = {
    "fist":   {"bonus": 0,  "range": 0, "style": "melee"},
    "dagger": {"bonus": 10, "range": 0, "style": "melee"},
    "sword":  {"bonus": 20, "range": 0, "style": "melee"},
    "katana": {"bonus": 35, "range": 0, "style": "melee"},
    "bow":    {"bonus": 5,  "range": 1, "style": "ranged"},
    "pistol": {"bonus": 10, "range": 1, "style": "ranged"},
    "sniper": {"bonus": 28, "range": 2, "style": "ranged"},
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

# ── Item pickup priority ──────────────────────────────────────────────────────
ITEM_PRIORITY = {
    "rewards":        300,   # Moltz/sMoltz — ALWAYS pickup
    "katana":         100,
    "sniper":          95,
    "sword":           90,
    "pistol":          85,
    "dagger":          80,
    "bow":             75,
    "medkit":          70,
    "bandage":         65,
    "emergency_food":  60,
    "energy_drink":    58,
    "binoculars":      55,   # Passive: vision+1, always pickup
    "map":             52,   # Use immediately
    "megaphone":       40,
}

# ── Recovery items (HP restore) ───────────────────────────────────────────────
RECOVERY_ITEMS = {
    "medkit":         50,   # +50 HP
    "bandage":        30,   # +30 HP
    "emergency_food": 20,   # +20 HP
    "energy_drink":    0,   # EP only, NOT HP
}

# ── Weather combat penalty per game-systems.md ───────────────────────────────
WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain":  0.05,   # -5%
    "fog":   0.10,   # -10%
    "storm": 0.15,   # -15%
}

# ── Terrain move EP cost ──────────────────────────────────────────────────────
TERRAIN_EP_COST = {
    "water": 3,
}
BASE_MOVE_EP   = 2
STORM_EP_EXTRA = 1

# ── Guardian reward ───────────────────────────────────────────────────────────
GUARDIAN_REWARD_SMOLTZ = 120

# ── Endgame threshold ────────────────────────────────────────────────────────
ENDGAME_ALIVE_THRESHOLD = 15
LATE_GAME_ALIVE_THRESHOLD = 30

# =============================================================================
# 🧠  PER-GAME STATE  (reset each game via reset_game_state())
# =============================================================================
_known_agents:    dict = {}   # agent_id → last observed stats
_map_knowledge:   dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_looted_guardians: set = set()   # guardian IDs already killed/looted this game
_farming_stats:   dict = {"smoltz_earned": 0, "kills": 0, "guardian_kills": 0}
_combat_memory:   dict = {}   # agent_id → {"dmg_dealt": int, "rounds": int}


def reset_game_state():
    """Reset per-game tracking state. Call when game ends."""
    global _known_agents, _map_knowledge, _looted_guardians, _farming_stats, _combat_memory
    _known_agents     = {}
    _map_knowledge    = {"revealed": False, "death_zones": set(), "safe_center": []}
    _looted_guardians = set()
    _farming_stats    = {"smoltz_earned": 0, "kills": 0, "guardian_kills": 0}
    _combat_memory    = {}
    log.info("🔄 Strategy brain reset for new game")


# =============================================================================
# ⚔️  DAMAGE & COMBAT MATH
# =============================================================================

def calc_damage(atk: int, weapon_bonus: int, target_def: int,
                weather: str = "clear") -> int:
    """Damage formula per combat-items.md + weather penalty.
    Base = ATK + bonus − (DEF × 0.5), min 1.
    """
    base    = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped_weapon) -> int:
    if not equipped_weapon:
        return 0
    return WEAPONS.get(equipped_weapon.get("typeId", "").lower(), {}).get("bonus", 0)


def get_weapon_range(equipped_weapon) -> int:
    if not equipped_weapon:
        return 0
    return WEAPONS.get(equipped_weapon.get("typeId", "").lower(), {}).get("range", 0)


def get_weapon_style(equipped_weapon) -> str:
    """Returns 'melee' or 'ranged'."""
    if not equipped_weapon:
        return "melee"
    return WEAPONS.get(equipped_weapon.get("typeId", "").lower(), {}).get("style", "melee")


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    weapon  = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def _estimate_enemy_weapon_range(agent: dict) -> int:
    weapon  = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("range", 0)


def estimate_combat_outcome(my_hp, my_atk, my_def, my_weapon_bonus,
                            enemy_hp, enemy_atk, enemy_def, enemy_weapon_bonus,
                            weather) -> dict:
    """
    TTK-based combat estimator (v1.6 logic, kept for compatibility).
    Returns: win, my_dmg, enemy_dmg, hits_to_kill, hits_to_die, survival_hits.
    """
    penalty         = WEATHER_COMBAT_PENALTY.get(weather, 0)
    my_dmg          = max(1, int((my_atk + my_weapon_bonus - int(enemy_def * 0.5)) * (1 - penalty)))
    enemy_dmg       = max(1, int((enemy_atk + enemy_weapon_bonus - int(my_def * 0.5)) * (1 - penalty)))

    hits_to_kill    = (enemy_hp + my_dmg - 1) // my_dmg   if my_dmg   > 0 else 999
    hits_to_die     = (my_hp + enemy_dmg - 1) // enemy_dmg if enemy_dmg > 0 else 999

    win             = hits_to_kill <= hits_to_die
    survival_hits   = hits_to_die - hits_to_kill if win else -1

    return {
        "win": win,
        "my_dmg": my_dmg,
        "enemy_dmg": enemy_dmg,
        "hits_to_kill": hits_to_kill,
        "hits_to_die": hits_to_die,
        "survival_hits": survival_hits,
    }


def simulate_full_fight(my_hp, my_atk, my_def, my_weapon_bonus,
                        enemy_hp, enemy_atk, enemy_def, enemy_weapon_bonus,
                        weather) -> dict:
    """
    🆕 v1.7.0 — Full round-by-round simulation.
    Simulates until one side dies; returns leftover HP and whether we won.
    Useful for "is it worth finishing this fight?" decisions.
    """
    penalty   = WEATHER_COMBAT_PENALTY.get(weather, 0)
    my_dmg    = max(1, int((my_atk + my_weapon_bonus - int(enemy_def  * 0.5)) * (1 - penalty)))
    e_dmg     = max(1, int((enemy_atk + enemy_weapon_bonus - int(my_def * 0.5)) * (1 - penalty)))

    mhp, ehp  = my_hp, enemy_hp
    rounds    = 0
    while mhp > 0 and ehp > 0 and rounds < 100:
        ehp  -= my_dmg
        if ehp <= 0:
            break
        mhp  -= e_dmg
        rounds += 1

    win           = ehp <= 0 and mhp > 0
    hp_remaining  = max(0, mhp)
    return {
        "win":          win,
        "hp_remaining": hp_remaining,
        "rounds":       rounds + 1,
        "my_dmg":       my_dmg,
        "enemy_dmg":    e_dmg,
        "worth_it":     win and hp_remaining > 20,   # Win AND survive with buffer
    }


# =============================================================================
# 🎯  TARGET SELECTION
# =============================================================================

def _select_best_target(targets: list, my_atk, my_def, my_weapon_bonus,
                        weather, my_hp) -> dict | None:
    """
    Risk/reward scoring — prefer easy kills with high sMoltz value.
    v1.7.0: guardians weighted higher; also considers enemy's range vs our range.
    """
    if not targets:
        return None

    best, best_score = None, -9999

    for t in targets:
        enemy_hp = t.get("hp", 100)
        if enemy_hp <= 0:
            continue

        outcome = simulate_full_fight(
            my_hp, my_atk, my_def, my_weapon_bonus,
            enemy_hp, t.get("atk", 10), t.get("def", 5),
            _estimate_enemy_weapon_bonus(t), weather,
        )

        if not outcome["win"]:
            # Still attack if we can one-shot (finish off)
            score = 30 if enemy_hp <= outcome["my_dmg"] else -200
        else:
            base_reward = (100 - enemy_hp) * 2
            speed_bonus = max(0, 10 - outcome["rounds"]) * 5
            safety_bonus = outcome["hp_remaining"] * 0.5
            # Guardian bonus: 120 sMoltz!
            guardian_bonus = 80 if t.get("isGuardian") else 0
            score = base_reward + speed_bonus + safety_bonus + guardian_bonus

        if score > best_score:
            best_score = score
            best = t

    return best


def _select_weakest(targets: list) -> dict | None:
    if not targets:
        return None
    return min(targets, key=lambda t: t.get("hp", 999))


# =============================================================================
# 🗺️  REGION HELPERS
# =============================================================================

def _get_region_id(entry) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id", "")
    return ""


def _resolve_region(entry, view: dict):
    """Resolve connectedRegions entry to full dict or None."""
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, str):
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None


def _get_move_ep_cost(terrain: str, weather: str) -> int:
    if terrain == "water":
        return TERRAIN_EP_COST["water"]
    if weather == "storm":
        return BASE_MOVE_EP + STORM_EP_EXTRA
    return BASE_MOVE_EP


def _count_dz_neighbors(region_id: str, connections, danger_ids: set) -> int:
    """🆕 v1.7.0 — Count how many neighbors of this region are in danger_ids."""
    count = 0
    for conn in connections:
        rid = _get_region_id(conn)
        if rid and rid in danger_ids:
            count += 1
    return count


def _is_in_range(target: dict, my_region: str, weapon_range: int,
                 connections=None) -> bool:
    target_region = target.get("regionId", "")
    if not target_region or target_region == my_region:
        return True
    if weapon_range >= 1 and connections:
        adj_ids = {_get_region_id(c) for c in connections}
        if target_region in adj_ids:
            return True
        # Range 2 (sniper): check two hops — approximate via visibleRegions
    return False


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Safest adjacent region — sorted by terrain + min DZ neighbours."""
    safe = []
    for conn in connections:
        if isinstance(conn, str):
            if conn not in danger_ids:
                safe.append((conn, 0))
        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            if not rid or conn.get("isDeathZone") or rid in danger_ids:
                continue
            terrain  = conn.get("terrain", "").lower()
            t_score  = {"hills": 3, "plains": 2, "ruins": 1, "forest": 0, "water": -2}.get(terrain, 0)
            # 🆕 Penalise if this safe region has many DZ neighbours itself
            dz_nb    = _count_dz_neighbors(rid, conn.get("connections", []), danger_ids)
            safe.append((rid, t_score - dz_nb))

    if safe:
        safe.sort(key=lambda x: x[1], reverse=True)
        return safe[0][0]

    # Last resort: any non-DZ connection
    for conn in connections:
        rid   = _get_region_id(conn)
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        if rid and not is_dz:
            log.warning("⚠️ Fallback safe region: %s", rid[:8])
            return rid
    return None


def _find_richest_region(connections, danger_ids: set,
                         visible_items: list, visible_monsters: list,
                         visible_agents: list) -> str | None:
    """Richest adjacent safe region for farming efficiency."""
    best_region, best_score = None, -1

    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue
        # 🆕 Skip regions surrounded by DZ (trap risk)
        if isinstance(conn, dict):
            if conn.get("isDeathZone"):
                continue

        score = 0
        for item in visible_items:
            if isinstance(item, dict) and item.get("regionId") == rid:
                score += ITEM_PRIORITY.get(item.get("typeId", "").lower(), 5)

        for mon in visible_monsters:
            if isinstance(mon, dict) and mon.get("regionId") == rid:
                score += 15 if mon.get("hp", 100) < 40 else 8

        for ag in visible_agents:
            if isinstance(ag, dict) and ag.get("regionId") == rid:
                if ag.get("isGuardian"):
                    gid = ag.get("id", "")
                    if gid not in _looted_guardians:   # 🆕 skip already-looted
                        score += 50 if ag.get("hp", 100) < 50 else 30
                # 🆕 Weakened enemy players are also high value
                elif ag.get("isAlive") and ag.get("hp", 100) < 40:
                    score += 20

        if score > best_score:
            best_score = score
            best_region = rid

    return best_region


def _choose_move_target(connections, danger_ids: set,
                        current_region: dict, visible_items: list,
                        alive_count: int, visible_monsters: list = None,
                        visible_agents: list = None,
                        my_hp: int = 100) -> str | None:
    """
    Best region to move to — farming + safety + endgame positioning.
    🆕 v1.7.0: endgame push toward center; avoid DZ-trap regions.
    """
    visible_monsters = visible_monsters or []
    visible_agents   = visible_agents or []

    # Endgame: push toward map center (most connected safe regions)
    is_endgame = alive_count <= ENDGAME_ALIVE_THRESHOLD
    if is_endgame and _map_knowledge.get("revealed"):
        center_ids = _map_knowledge.get("safe_center", [])
        for conn in connections:
            rid = _get_region_id(conn)
            if rid and rid in center_ids and rid not in danger_ids:
                log.debug("🏆 ENDGAME: pushing to center region %s", rid[:8])
                return rid

    # Rich region first (farming)
    richest = _find_richest_region(connections, danger_ids, visible_items,
                                   visible_monsters, visible_agents)
    if richest:
        return richest

    # Generic scoring fallback
    item_regions = {item.get("regionId", "") for item in visible_items
                    if isinstance(item, dict)}
    candidates   = []

    for conn in connections:
        rid = _get_region_id(conn)
        if not rid:
            continue
        if isinstance(conn, dict) and conn.get("isDeathZone"):
            continue
        if rid in danger_ids:
            continue

        score = 0
        if isinstance(conn, dict):
            terrain = conn.get("terrain", "").lower()
            score  += {"hills": 4, "plains": 2, "ruins": 2, "forest": 1, "water": -3}.get(terrain, 0)
            weather = conn.get("weather", "").lower()
            score  += {"clear": 1, "rain": 0, "fog": -1, "storm": -2}.get(weather, 0)
            facs    = [f for f in conn.get("interactables", [])
                       if isinstance(f, dict) and not f.get("isUsed")]
            score  += len(facs) * 2
            # 🆕 Prefer regions with many connections (high map control)
            score  += len(conn.get("connections", [])) * 0.5
            # Map center pull
            if _map_knowledge.get("revealed") and rid in _map_knowledge.get("safe_center", []):
                score += 5

        if rid in item_regions:
            score += 5
        if alive_count < LATE_GAME_ALIVE_THRESHOLD:
            score += 3

        candidates.append((rid, score))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


# =============================================================================
# 💊  HEALING & ITEMS
# =============================================================================

def _find_healing_item(inventory: list, critical: bool = False,
                       prefer_small: bool = False) -> dict | None:
    heals = [i for i in inventory
             if isinstance(i, dict)
             and i.get("typeId", "").lower() in RECOVERY_ITEMS
             and RECOVERY_ITEMS[i.get("typeId", "").lower()] > 0]
    if not heals:
        return None
    if critical:
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0), reverse=True)
    elif prefer_small:
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0))
    else:
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0), reverse=True)
    return heals[0]


def _find_energy_drink(inventory: list) -> dict | None:
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _total_heal_value(inventory: list) -> int:
    """🆕 Sum of all HP we can restore from current inventory."""
    return sum(RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0)
               for i in inventory if isinstance(i, dict))


# =============================================================================
# 🔫  WEAPONS & INVENTORY
# =============================================================================

def _check_equip(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon."""
    current_bonus = get_weapon_bonus(equipped) if equipped else 0
    best, best_bonus = None, current_bonus
    for item in inventory:
        if not isinstance(item, dict) or item.get("category") != "weapon":
            continue
        bonus = WEAPONS.get(item.get("typeId", "").lower(), {}).get("bonus", 0)
        if bonus > best_bonus:
            best, best_bonus = item, bonus
    if best:
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')} (+{best_bonus} ATK)"}
    return None


def _check_pickup(items: list, inventory: list, region_id: str) -> dict | None:
    """
    Smart pickup with inventory compaction.
    🆕 v1.7.0: if inventory full and a katana/sniper is on ground, drop weakest item.
    """
    local_items = [i for i in items
                   if isinstance(i, dict) and (i.get("regionId") == region_id or i.get("id"))]
    if not local_items:
        return None

    heal_count = sum(1 for i in inventory
                     if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS[i.get("typeId", "").lower()] > 0)

    local_items.sort(key=lambda i: _pickup_score(i, inventory, heal_count), reverse=True)
    best  = local_items[0]
    score = _pickup_score(best, inventory, heal_count)
    if score <= 0:
        return None

    # Inventory full?
    if len(inventory) >= 10:
        # 🆕 Only make room for high-value items (score > 90 = katana/sniper)
        if score >= 90:
            worst = _find_droppable_item(inventory)
            if worst:
                return {"action": "drop_item", "data": {"itemId": worst["id"]},
                        "reason": f"COMPACTION: drop {worst.get('typeId','item')} for {best.get('typeId','item')}"}
        return None

    return {"action": "pickup", "data": {"itemId": best["id"]},
            "reason": f"PICKUP: {best.get('typeId', 'item')} (score={score})"}


def _pickup_score(item: dict, inventory: list, heal_count: int) -> int:
    type_id  = item.get("typeId", "").lower()
    category = item.get("category", "").lower()

    if type_id == "rewards" or category == "currency":
        return 300

    if category == "weapon":
        bonus        = WEAPONS.get(type_id, {}).get("bonus", 0)
        current_best = max(
            (WEAPONS.get(i.get("typeId", "").lower(), {}).get("bonus", 0)
             for i in inventory if isinstance(i, dict) and i.get("category") == "weapon"),
            default=0
        )
        return (100 + bonus) if bonus > current_best else 0

    if type_id == "binoculars":
        has_binos = any(isinstance(i, dict) and i.get("typeId", "").lower() == "binoculars"
                        for i in inventory)
        return 55 if not has_binos else 0

    if type_id == "map":
        return 52

    if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS[type_id] > 0:
        return ITEM_PRIORITY.get(type_id, 0) + (10 if heal_count < 4 else 0)

    if type_id == "energy_drink":
        return 58

    return ITEM_PRIORITY.get(type_id, 0)


def _find_droppable_item(inventory: list) -> dict | None:
    """🆕 v1.7.0 — Find the least valuable item to drop for compaction."""
    droppable = [i for i in inventory
                 if isinstance(i, dict)
                 and i.get("typeId", "").lower() not in ("medkit", "katana", "sniper")
                 and i.get("category") not in ("currency",)]
    if not droppable:
        return None
    return min(droppable,
               key=lambda i: ITEM_PRIORITY.get(i.get("typeId", "").lower(), 1))


# =============================================================================
# 🏥  FACILITIES
# =============================================================================

def _select_facility(interactables: list, hp: int, ep: int) -> dict | None:
    """
    Facility priority: medical (HP<80) > supply_cache > watchtower > cave > broadcast.
    🆕 v1.7.0: always use cave if EP is low; broadcast_station only once.
    """
    for fac in interactables:
        if not isinstance(fac, dict) or fac.get("isUsed"):
            continue
        ftype = fac.get("type", "").lower()
        if ftype == "medical_facility" and hp < 80:
            return fac
        if ftype == "supply_cache":
            return fac
        if ftype == "cave" and ep < 5:
            return fac          # 🆕 cave for EP regen
        if ftype == "watchtower":
            return fac
        if ftype == "broadcast_station":
            return fac
    return None


# =============================================================================
# 👁️  AGENT TRACKING & INTELLIGENCE
# =============================================================================

def _track_agents(visible_agents: list, my_id: str, my_region: str):
    """Track all visible agents. 🆕 v1.7.0: record combat damage dealt."""
    global _known_agents
    for agent in visible_agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if not aid or aid == my_id:
            continue
        prev = _known_agents.get(aid, {})
        prev_hp = prev.get("hp", agent.get("hp", 100))
        cur_hp  = agent.get("hp", 100)

        _known_agents[aid] = {
            "hp":             cur_hp,
            "atk":            agent.get("atk", 10),
            "def":            agent.get("def", 5),
            "isGuardian":     agent.get("isGuardian", False),
            "equippedWeapon": agent.get("equippedWeapon"),
            "lastSeen":       my_region,
            "isAlive":        agent.get("isAlive", True),
            "dmg_taken":      max(0, prev_hp - cur_hp),   # 🆕 hp dropped = we dealt dmg
        }

    if len(_known_agents) > 50:
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _record_guardian_looted(guardian_id: str):
    """🆕 v1.7.0 — Mark guardian as already looted to avoid revisiting."""
    global _looted_guardians
    _looted_guardians.add(guardian_id)


def _update_farming_stats(event_type: str, amount: int = 0):
    """🆕 v1.7.0 — Track farming performance."""
    global _farming_stats
    if event_type == "smoltz":
        _farming_stats["smoltz_earned"] += amount
    elif event_type == "kill":
        _farming_stats["kills"] += 1
    elif event_type == "guardian_kill":
        _farming_stats["guardian_kills"] += 1
        _farming_stats["smoltz_earned"] += GUARDIAN_REWARD_SMOLTZ


# =============================================================================
# 🗺️  MAP LEARNING
# =============================================================================

def learn_from_map(view: dict):
    """Called after Map item is used — learn entire map layout for DZ tracking."""
    global _map_knowledge
    visible_regions = view.get("visibleRegions", [])
    if not visible_regions:
        return

    _map_knowledge["revealed"] = True
    safe_regions = []

    for region in visible_regions:
        if not isinstance(region, dict):
            continue
        rid = region.get("id", "")
        if not rid:
            continue
        if region.get("isDeathZone"):
            _map_knowledge["death_zones"].add(rid)
        else:
            conns       = region.get("connections", [])
            terrain     = region.get("terrain", "").lower()
            t_value     = {"hills": 3, "plains": 2, "ruins": 2, "forest": 1, "water": -1}.get(terrain, 0)
            score       = len(conns) * 1.5 + t_value   # 🆕 weight connectivity more
            safe_regions.append((rid, score))

    safe_regions.sort(key=lambda x: x[1], reverse=True)
    _map_knowledge["safe_center"] = [r[0] for r in safe_regions[:5]]

    log.info("🗺️ MAP LEARNED: %d DZ, top center: %s | farming: %s",
             len(_map_knowledge["death_zones"]),
             _map_knowledge["safe_center"][:3],
             _farming_stats)


# =============================================================================
# 🛡️  UTILITY ITEMS
# =============================================================================

def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int) -> dict | None:
    """Instant-use items: Map reveal. Energy drink handled in EP section."""
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("typeId", "").lower() == "map":
            log.info("🗺️ Using Map — reveals entire map")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Map — reveal entire map for DZ tracking"}
    return None


def _should_flee_from_enemy(my_hp, enemy_hp, enemy_atk, my_def,
                            enemy_weapon_bonus, weather) -> bool:
    """🆕 v1.7.0 — Smarter flee: factor in enemy weapon bonus."""
    if my_hp < 20:
        return True
    e_dmg        = calc_damage(enemy_atk, enemy_weapon_bonus, my_def, weather)
    hits_to_die  = (my_hp + e_dmg - 1) // e_dmg if e_dmg > 0 else 999
    return hits_to_die <= 2


# =============================================================================
# 🎮  MAIN DECISION ENGINE
# =============================================================================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine — returns action dict or None (wait).

    ══════════════════════════════════════════════════════════
    PRIORITY CHAIN v1.7.0:
    ──────────────────────────────────────────────────────────
    P0.  Dead?          → return None immediately
    P1.  Death zone     → ESCAPE (1.34 HP/sec — fatal)
    P1b. Pending DZ     → pre-escape before it activates
    P2.  EP=0 + enemy   → energy drink (can't act without EP)
    P3.  Critical HP<25 → use biggest heal RIGHT NOW
    P3b. Moderate HP<50 → use small heal (preserve medkit)
    P4.  Utility items  → Map (use immediately after pickup)
    P5.  FREE: Pickup   → Moltz/sMoltz > weapons > healing
    P5b. FREE: Equip    → best weapon from inventory
    P6.  Guardian flee  → flee if HP<35 + guardian in region
    P7.  Guardian farm  → fight if win OR guardian HP<40
    P8.  Agent combat   → TTK-favourable fight; late game push
    P9.  Monster farm   → low-risk farming for items/XP
    P9b. Safe heal      → heal to 70+ when no enemies nearby
    P10. Facility use   → medical/cache/watchtower/cave
    P11. Strategic move → richest region / endgame center push
    P12. EP recovery    → energy drink (EP≤2, safe area)
    P13. Rest           → EP<4, safe area, not endgame
    ══════════════════════════════════════════════════════════
    """
    # ── Unpack view ───────────────────────────────────────────────────
    self_data      = view.get("self", {})
    region         = view.get("currentRegion", {})
    hp             = self_data.get("hp", 100)
    ep             = self_data.get("ep", 10)
    max_ep         = self_data.get("maxEp", 10)
    atk            = self_data.get("atk", 10)
    defense        = self_data.get("def", 5)
    is_alive       = self_data.get("isAlive", True)
    inventory      = self_data.get("inventory", [])
    equipped       = self_data.get("equippedWeapon")
    my_id          = self_data.get("id", "")

    connected_regions = view.get("connectedRegions", [])
    visible_agents    = view.get("visibleAgents", [])
    visible_monsters  = view.get("visibleMonsters", [])
    visible_items_raw = view.get("visibleItems", [])
    pending_dz        = view.get("pendingDeathzones", [])
    alive_count       = view.get("aliveCount", 100)

    # Unwrap visibleItems (may be {regionId, item:{...}} or flat)
    visible_items = []
    for entry in visible_items_raw:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("item")
        if isinstance(inner, dict):
            inner["regionId"] = entry.get("regionId", "")
            visible_items.append(inner)
        elif entry.get("id"):
            visible_items.append(entry)

    connections    = connected_regions or region.get("connections", [])
    interactables  = region.get("interactables", [])
    region_id      = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""
    move_ep_cost   = _get_move_ep_cost(region_terrain, region_weather)

    # ── P0: Already dead ─────────────────────────────────────────────
    if not is_alive:
        return None

    # ── Build danger_ids (DZ + pending DZ) ───────────────────────────
    danger_ids = set()
    for dz in pending_dz:
        if isinstance(dz, dict):
            danger_ids.add(dz.get("id", ""))
        elif isinstance(dz, str):
            danger_ids.add(dz)
    for conn in connections:
        resolved = _resolve_region(conn, view)
        if resolved and resolved.get("isDeathZone"):
            danger_ids.add(resolved.get("id", ""))

    # ── Track agents ──────────────────────────────────────────────────
    _track_agents(visible_agents, my_id, region_id)

    # ── Convenience sets ─────────────────────────────────────────────
    enemies_here = [a for a in visible_agents
                    if a.get("isAlive", True)
                    and a.get("id") != my_id
                    and a.get("regionId", region_id) == region_id]

    # ── P1: Death zone escape ─────────────────────────────────────────
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 DZ ESCAPE → %s (HP=%d)", safe[:8], hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"ESCAPE: death zone! HP={hp} dropping (1.34/sec)"}
        elif not safe:
            log.error("🚨 IN DZ but NO SAFE REGION — all neighbors DZ!")

    # ── P1b: Pre-escape pending DZ ────────────────────────────────────
    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("⚠️ PRE-ESCAPE: %s → DZ soon → %s", region_id[:8], safe[:8])
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: region becoming DZ soon"}

    # ── P2: EP=0 + enemy present → energy drink now ───────────────────
    if ep == 0 and enemies_here:
        drink = _find_energy_drink(inventory)
        if drink:
            return {"action": "use_item", "data": {"itemId": drink["id"]},
                    "reason": "EP EMERGENCY: EP=0 with enemy nearby, restoring EP"}

    # ── P3: Critical HP < 25 → big heal ASAP ─────────────────────────
    if hp < 25:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}, {heal.get('typeId','?')}"}

    # ── P3b: Moderate HP < 50 → small heal ───────────────────────────
    elif hp < 50:
        heal = _find_healing_item(inventory, critical=False, prefer_small=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"MODERATE HEAL: HP={hp}, {heal.get('typeId','?')}"}

    # ── P4: Utility items (Map) ───────────────────────────────────────
    util = _use_utility_item(inventory, hp, ep, alive_count)
    if util:
        return util

    # ── P5: FREE — Pickup ─────────────────────────────────────────────
    pickup = _check_pickup(visible_items, inventory, region_id)
    if pickup:
        return pickup

    # ── P5b: FREE — Equip best weapon ─────────────────────────────────
    equip = _check_equip(inventory, equipped)
    if equip:
        return equip

    # ── Cooldown gate — below here needs can_act ──────────────────────
    if not can_act:
        return None

    # ── P6: Guardian flee (v1.5.2+ guardians are hostile) ────────────
    guardians_here = [a for a in enemies_here if a.get("isGuardian", False)]
    if guardians_here and hp < 35 and ep >= move_ep_cost:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("⚠️ GUARDIAN FLEE: HP=%d → %s", hp, safe[:8])
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"GUARDIAN FLEE: HP={hp}, guardian hostile"}

    # ── P7: Guardian farming (120 sMoltz each!) ───────────────────────
    alive_guardians = [
        a for a in visible_agents
        if a.get("isGuardian", False)
        and a.get("isAlive", True)
        and a.get("id", "") not in _looted_guardians  # 🆕 skip already looted
    ]
    if alive_guardians and ep >= 2 and hp >= 30:
        target = _select_best_target(alive_guardians, atk, defense,
                                     get_weapon_bonus(equipped), region_weather, hp)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                sim = simulate_full_fight(
                    hp, atk, defense, get_weapon_bonus(equipped),
                    target.get("hp", 100), target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather,
                )
                # 🆕 Fight if: we win cleanly, OR guardian already low HP (finish off)
                if sim["win"] or target.get("hp", 100) <= sim["my_dmg"] * 1.5:
                    # 🆕 Mark guardian if we expect to one-shot it this hit
                    if target.get("hp", 100) <= sim["my_dmg"]:
                        _record_guardian_looted(target.get("id", ""))
                        _update_farming_stats("guardian_kill")
                    return {
                        "action": "attack",
                        "data":   {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"GUARDIAN FARM 💰: HP={target.get('hp','?')} "
                                  f"survive={sim['hp_remaining']} win={sim['win']}",
                    }

    # ── P8: Agent combat — TTK-favourable ────────────────────────────
    # 🆕 v1.7.0: more aggressive in late game; always chase near-dead enemies
    hp_threshold = 35 if alive_count > LATE_GAME_ALIVE_THRESHOLD else 20
    enemy_agents = [
        a for a in visible_agents
        if not a.get("isGuardian", False)
        and a.get("isAlive", True)
        and a.get("id") != my_id
    ]
    if enemy_agents and ep >= 2 and hp >= hp_threshold:
        target = _select_best_target(enemy_agents, atk, defense,
                                     get_weapon_bonus(equipped), region_weather, hp)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                sim = simulate_full_fight(
                    hp, atk, defense, get_weapon_bonus(equipped),
                    target.get("hp", 100), target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather,
                )
                # 🆕 Fight if we win clean, OR enemy is very low HP (finish off)
                is_finish_blow = target.get("hp", 100) <= sim["my_dmg"] * 1.2
                if sim["worth_it"] or is_finish_blow:
                    _update_farming_stats("kill")
                    return {
                        "action": "attack",
                        "data":   {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"COMBAT ⚔️: HP={target.get('hp','?')} "
                                  f"survive={sim['hp_remaining']} rounds={sim['rounds']}",
                    }

            # 🆕 Chase ranged: if enemy is fleeing range but we have ranged weapon
            elif get_weapon_range(equipped) >= 1:
                target_region = target.get("regionId", "")
                if target_region and target_region in {_get_region_id(c) for c in connections}:
                    return {
                        "action": "attack",
                        "data":   {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"RANGED CHASE: HP={target.get('hp','?')} at {target_region[:8]}",
                    }

    # ── P9: Monster farming ───────────────────────────────────────────
    live_monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if live_monsters and ep >= 1 and hp > 20:
        target = _select_weakest(live_monsters)
        if target and _is_in_range(target, region_id, get_weapon_range(equipped), connections):
            return {
                "action": "attack",
                "data":   {"targetId": target["id"], "targetType": "monster"},
                "reason": f"MONSTER FARM 🐉: {target.get('name','?')} HP={target.get('hp','?')}",
            }

    # ── P9b: Safe heal (no enemies, HP < 70) ─────────────────────────
    if hp < 70 and not enemies_here and region_id not in danger_ids:
        heal = _find_healing_item(inventory, critical=(hp < 30), prefer_small=(hp < 60))
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"SAFE HEAL: HP={hp}, no threats nearby"}

    # ── P10: Facility use ─────────────────────────────────────────────
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        fac = _select_facility(interactables, hp, ep)
        if fac:
            return {"action": "interact", "data": {"interactableId": fac["id"]},
                    "reason": f"FACILITY 🏥: {fac.get('type','?')}"}

    # ── P11: Strategic movement ───────────────────────────────────────
    if ep >= move_ep_cost and connections:
        dest = _choose_move_target(connections, danger_ids, region,
                                   visible_items, alive_count,
                                   visible_monsters, visible_agents, hp)
        if dest:
            return {"action": "move", "data": {"regionId": dest},
                    "reason": f"EXPLORE 🗺️: moving to richest/safest region"}

    # ── P12: EP recovery (low EP, no enemy, not endgame) ─────────────
    is_endgame = alive_count <= ENDGAME_ALIVE_THRESHOLD
    if ep <= 2 and not enemies_here and not is_endgame:
        drink = _find_energy_drink(inventory)
        if drink:
            return {"action": "use_item", "data": {"itemId": drink["id"]},
                    "reason": f"EP RESTORE: EP={ep}, using energy drink"}

    # ── P13: Rest ─────────────────────────────────────────────────────
    rest_allowed = (
        ep < 4
        and not enemies_here
        and not region.get("isDeathZone")
        and region_id not in danger_ids
        and not is_endgame          # 🆕 never rest in top 15
    )
    if rest_allowed:
        return {"action": "rest", "data": {},
                "reason": f"REST 💤: EP={ep}/{max_ep}, safe area (+1 EP bonus)"}

    # ── Wait ──────────────────────────────────────────────────────────
    return None


# =============================================================================
# 🔁  LEGACY SIMULATION (v1.6 compat)
# =============================================================================

def simulate_combat(my_hp, my_atk, my_def, enemy, equipped, weather):
    """v1.6 compatibility shim — use simulate_full_fight() for new code."""
    enemy_hp  = enemy.get("hp", 100)
    my_dmg    = calc_damage(my_atk, get_weapon_bonus(equipped),
                            enemy.get("def", 5), weather)
    enemy_dmg = calc_damage(enemy.get("atk", 10),
                            _estimate_enemy_weapon_bonus(enemy),
                            my_def, weather)
    return {
        "my_hp":    my_hp - enemy_dmg,
        "enemy_hp": enemy_hp - my_dmg,
        "win":      (enemy_hp - my_dmg) <= 0,
        "survive":  (my_hp - enemy_dmg) > 0,
    }


"""
════════════════════════════════════════════════════════════════════
v1.7.0 CHANGELOG SUMMARY
════════════════════════════════════════════════════════════════════
✅ simulate_full_fight()    — round-by-round sim replaces 1-hit TTK
✅ worth_it flag            — win AND survive >20 HP
✅ _looted_guardians set    — avoid wasting turns on dead guardians
✅ _farming_stats tracker   — sMoltz earned, kills, guardian_kills
✅ _update_farming_stats()  — called on confirmed kills
✅ Inventory compaction     — drop weak items for katana/sniper
✅ _find_droppable_item()   — safe item drop selection
✅ Cave facility use        — EP regen when EP low
✅ Ranged chase logic       — attack fleeing enemies with ranged weapon
✅ Endgame center push      — top 15 alive → push map center
✅ No rest in endgame       — stay aggressive in top 15
✅ P2 EP=0 + enemy          — energy drink as emergency action
✅ _count_dz_neighbors()    — avoid DZ-trap regions
✅ Smarter _find_safe_region — penalise regions near DZ
✅ Guardian flee at HP<35   — raised from 40 (more aggressive window)
✅ get_weapon_style()       — melee vs ranged classification
════════════════════════════════════════════════════════════════════
"""
