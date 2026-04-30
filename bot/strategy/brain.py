"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v2.0.0 changes (TEAM HUNT MODE - FULL VERSION):
- 10 bots in one room work as a team
- Priority #1: Kill ALL non-bot enemies first
- Bots NEVER attack each other until all enemies are dead
- Automatic bot detection via team_id or bot name pattern
- After enemy wipe, bots can optionally fight (or just survive)
- ALL functions from v1.6.0 preserved (no deletions)

v1.6.0 features (ALL PRESERVED):
- Enhanced combat estimation (TTK based)
- Smarter target selection (not just weakest)
- Richest region movement (farming priority)
- Better healing management
- More aggressive guardian farming
- Monster farming with lower HP threshold

Uses ALL view fields from api-summary.md
"""
from bot.utils.logger import get_logger

log = get_logger(__name__)


# =========================
# 🔥 TEAM HUNT MODE CONFIGURATION
# =========================
TEAM_CONFIG = {
    "team_id": "theoBDG01",              # Same team ID for all bots
    "hunt_mode": True,                      # Kill all enemies first
    "never_attack_teammates": True,         # Bots don't attack each other
    "fight_teammates_after_wipe": False,    # Set True if want final battle
    "bot_name_prefixes": ["MoltyBot", "StrategyBot", "HunterBot", "BrainBot"],
}

# Global state for enemy wipe detection
_enemy_wipe_detected = False


# =========================
# 🔥 SIMULATION ENGINE
# =========================
def simulate_combat(my_hp, my_atk, my_def, enemy, equipped, weather):
    enemy_hp = enemy.get("hp", 100)

    my_dmg = calc_damage(my_atk, get_weapon_bonus(equipped),
                         enemy.get("def", 5), weather)

    enemy_dmg = calc_damage(enemy.get("atk", 10),
                            _estimate_enemy_weapon_bonus(enemy),
                            my_def, weather)

    return {
        "my_hp": my_hp - enemy_dmg,
        "enemy_hp": enemy_hp - my_dmg,
        "win": (enemy_hp - my_dmg) <= 0,
        "survive": (my_hp - enemy_dmg) > 0
    }


# ── Weapon stats from combat-items.md ─────────────────────────────────
WEAPONS = {
    "fist": {"bonus": 0, "range": 0},
    "dagger": {"bonus": 10, "range": 0},
    "sword": {"bonus": 20, "range": 0},
    "katana": {"bonus": 35, "range": 0},
    "bow": {"bonus": 5, "range": 1},
    "pistol": {"bonus": 10, "range": 1},
    "sniper": {"bonus": 28, "range": 2},
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

# ── Item priority for pickup ──────────────────────────────────────────
ITEM_PRIORITY = {
    "rewards": 300,
    "katana": 100, "sniper": 95, "sword": 90, "pistol": 85,
    "dagger": 80, "bow": 75,
    "medkit": 70, "bandage": 65, "emergency_food": 60, "energy_drink": 58,
    "binoculars": 55,
    "map": 52,
    "megaphone": 40,
}

# ── Recovery items for healing ──────────────────────────────────────
RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,
}

# Weather combat penalty per game-systems.md
WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain": 0.05,
    "fog": 0.10,
    "storm": 0.15,
}


def calc_damage(atk: int, weapon_bonus: int, target_def: int,
                weather: str = "clear") -> int:
    """Damage formula per combat-items.md + game-systems.md weather penalty."""
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped_weapon) -> int:
    """Get ATK bonus from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_range(equipped_weapon) -> int:
    """Get range from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)


_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}


def _resolve_region(entry, view: dict):
    """Resolve a connectedRegions entry to a full region object."""
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, str):
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None


def _get_region_id(entry) -> str:
    """Extract region ID from either a string or dict entry."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id", "")
    return ""


def reset_game_state():
    """Reset per-game tracking state. Call when game ends."""
    global _known_agents, _map_knowledge, _enemy_wipe_detected
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _enemy_wipe_detected = False
    log.info("Strategy brain reset for new game")


# =========================
# 🧠 TEAM & ENEMY DETECTION (NEW)
# =========================
def is_teammate(agent: dict, my_id: str) -> bool:
    """Check if an agent is a teammate (bot from same team)."""
    if not agent or agent.get("id") == my_id:
        return True
    
    # Method 1: Check team_id field
    if TEAM_CONFIG["team_id"] and agent.get("team_id") == TEAM_CONFIG["team_id"]:
        return True
    
    # Method 2: Check bot name patterns
    agent_name = agent.get("name", "").lower()
    for prefix in TEAM_CONFIG["bot_name_prefixes"]:
        if agent_name.startswith(prefix.lower()):
            return True
    
    # Method 3: Check isBot flag
    if agent.get("isBot", False):
        return True
    
    return False


def is_enemy(agent: dict, my_id: str) -> bool:
    """Check if an agent is an enemy (non-teammate)."""
    if not agent or agent.get("id") == my_id:
        return False
    if agent.get("isGuardian", False):
        return True
    return not is_teammate(agent, my_id)


def are_there_any_enemies_left(view: dict, my_id: str) -> bool:
    """Check if there are still any enemies (players or guardians) alive."""
    visible_agents = view.get("visibleAgents", [])
    for agent in visible_agents:
        if is_enemy(agent, my_id):
            return True
    return False


# =========================
# 🧠 ENHANCED COMBAT & STRATEGY (v1.6.0 - FULL PRESERVED)
# =========================
def estimate_combat_outcome(my_hp, my_atk, my_def, my_weapon_bonus,
                            enemy_hp, enemy_atk, enemy_def, enemy_weapon_bonus,
                            weather) -> dict:
    """Estimate combat outcome with TTK (time to kill) calculation."""
    my_dmg_per_hit = max(1, my_atk + my_weapon_bonus - int(enemy_def * 0.5))
    enemy_dmg_per_hit = max(1, enemy_atk + enemy_weapon_bonus - int(my_def * 0.5))

    weather_penalty = WEATHER_COMBAT_PENALTY.get(weather, 0)
    my_dmg_per_hit = int(my_dmg_per_hit * (1 - weather_penalty))
    enemy_dmg_per_hit = int(enemy_dmg_per_hit * (1 - weather_penalty))

    hits_to_kill = (enemy_hp + my_dmg_per_hit - 1) // my_dmg_per_hit if my_dmg_per_hit > 0 else 999
    hits_to_die = (my_hp + enemy_dmg_per_hit - 1) // enemy_dmg_per_hit if enemy_dmg_per_hit > 0 else 999

    win = hits_to_kill <= hits_to_die
    survival_hits = hits_to_die - hits_to_kill if win else -1

    return {
        "win": win,
        "my_dmg": my_dmg_per_hit,
        "enemy_dmg": enemy_dmg_per_hit,
        "hits_to_kill": hits_to_kill,
        "hits_to_die": hits_to_die,
        "survival_hits": survival_hits
    }


def _select_best_target(targets: list, my_atk, my_def, my_weapon_bonus, weather, my_hp,
                        my_id: str = None) -> dict | None:
    """Select best target based on risk/reward ratio. Excludes teammates if in hunt mode."""
    if not targets:
        return None
    
    # Filter out teammates if in hunt mode
    if TEAM_CONFIG["hunt_mode"] and TEAM_CONFIG["never_attack_teammates"] and my_id:
        eligible_targets = [t for t in targets if is_enemy(t, my_id)]
    else:
        eligible_targets = targets
    
    if not eligible_targets:
        return None
    
    best = None
    best_score = -999

    for t in eligible_targets:
        enemy_hp = t.get("hp", 100)
        if enemy_hp <= 0:
            continue

        enemy_atk = t.get("atk", 10)
        enemy_def = t.get("def", 5)
        enemy_weapon_bonus = _estimate_enemy_weapon_bonus(t)
        is_guardian = t.get("isGuardian", False)
        guardian_bonus = 50 if is_guardian else 0

        outcome = estimate_combat_outcome(
            my_hp, my_atk, my_def, my_weapon_bonus,
            enemy_hp, enemy_atk, enemy_def, enemy_weapon_bonus,
            weather
        )

        if not outcome["win"]:
            if enemy_hp <= outcome["my_dmg"] * 2:
                score = 50 + guardian_bonus
            else:
                score = -100
        else:
            reward = (100 - enemy_hp) * 2 + (30 - outcome["hits_to_kill"]) + guardian_bonus
            risk = (outcome["hits_to_die"] - outcome["hits_to_kill"]) * 10
            score = reward - risk

        if score > best_score:
            best_score = score
            best = t

    return best


def _find_richest_region(connections: list, danger_ids: set,
                         visible_items: list, visible_monsters: list,
                         visible_agents: list, my_id: str = None) -> str | None:
    """Find adjacent region with most valuable targets (items, monsters, enemies)."""
    best_region = None
    best_score = -1

    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue

        score = 0

        # Items
        for item in visible_items:
            if isinstance(item, dict) and item.get("regionId") == rid:
                type_id = item.get("typeId", "").lower()
                score += ITEM_PRIORITY.get(type_id, 10)

        # Monsters
        for mon in visible_monsters:
            if isinstance(mon, dict) and mon.get("regionId") == rid:
                mon_hp = mon.get("hp", 100)
                score += 15 if mon_hp < 40 else 8

        # Enemies (non-teammates)
        for ag in visible_agents:
            if isinstance(ag, dict) and ag.get("regionId") == rid:
                if is_enemy(ag, my_id):
                    if ag.get("isGuardian"):
                        score += 30
                    else:
                        score += 20

        if score > best_score:
            best_score = score
            best_region = rid

    return best_region


def _should_flee_from_enemy(my_hp, enemy_hp, enemy_atk, my_def, weather) -> bool:
    """Determine if we should flee from an enemy."""
    if my_hp < 25:
        return True
    enemy_dmg = calc_damage(enemy_atk, 0, my_def, weather)
    hits_to_die = (my_hp + enemy_dmg - 1) // enemy_dmg if enemy_dmg > 0 else 999
    return hits_to_die <= 2


# =========================
# 🛒 PICKUP & INVENTORY FUNCTIONS
# =========================
def _check_pickup(items: list, inventory: list, region_id: str) -> dict | None:
    """Smart pickup: weapons > healing stockpile > utility > Moltz (always)."""
    if len(inventory) >= 10:
        return None
    
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None

    heal_count = sum(1 for i in inventory if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)

    local_items.sort(
        key=lambda i: _pickup_score(i, inventory, heal_count), reverse=True)
    best = local_items[0]
    score = _pickup_score(best, inventory, heal_count)
    if score > 0:
        type_id = best.get('typeId', 'item')
        log.info("PICKUP: %s (score=%d, heal_stock=%d)", type_id, score, heal_count)
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": f"PICKUP: {type_id}"}
    return None


def _pickup_score(item: dict, inventory: list, heal_count: int) -> int:
    """Calculate dynamic pickup score based on current inventory state."""
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()

    if type_id == "rewards" or category == "currency":
        return 300

    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        current_best = 0
        for inv_item in inventory:
            if isinstance(inv_item, dict) and inv_item.get("category") == "weapon":
                cb = WEAPONS.get(inv_item.get("typeId", "").lower(), {}).get("bonus", 0)
                current_best = max(current_best, cb)
        if bonus > current_best:
            return 100 + bonus
        return 0

    if type_id == "binoculars":
        has_binos = any(isinstance(i, dict) and i.get("typeId", "").lower() == "binoculars"
                       for i in inventory)
        return 55 if not has_binos else 0

    if type_id == "map":
        return 52

    if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS.get(type_id, 0) > 0:
        if heal_count < 4:
            return ITEM_PRIORITY.get(type_id, 0) + 10
        return ITEM_PRIORITY.get(type_id, 0)

    if type_id == "energy_drink":
        return 58

    return ITEM_PRIORITY.get(type_id, 0)


def _check_equip(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon from inventory."""
    current_bonus = get_weapon_bonus(equipped) if equipped else 0
    best = None
    best_bonus = current_bonus
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("category") == "weapon":
            type_id = item.get("typeId", "").lower()
            bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
            if bonus > best_bonus:
                best = item
                best_bonus = bonus
    if best:
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')} (+{best_bonus} ATK)"}
    return None


def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int) -> dict | None:
    """Use utility items immediately after pickup."""
    for item in inventory:
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId", "").lower()
        if type_id == "map":
            log.info("🗺️ Using Map! Will reveal entire map for strategic learning.")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Using Map — reveals entire map for DZ tracking"}
    return None


def learn_from_map(view: dict):
    """Called after Map is used — learn entire map layout."""
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
            conns = region.get("connections", [])
            terrain = region.get("terrain", "").lower()
            terrain_value = {"hills": 3, "plains": 2, "ruins": 2, "forest": 1, "water": -1}.get(terrain, 0)
            score = len(conns) + terrain_value
            safe_regions.append((rid, score))

    safe_regions.sort(key=lambda x: x[1], reverse=True)
    _map_knowledge["safe_center"] = [r[0] for r in safe_regions[:5]]

    log.info("🗺️ MAP LEARNED: %d DZ regions, %d safe regions, top center: %s",
             len(_map_knowledge["death_zones"]),
             len(safe_regions),
             _map_knowledge["safe_center"][:3])


# =========================
# 🏃 MOVEMENT & NAVIGATION
# =========================
def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find nearest connected region that's NOT a death zone AND NOT pending DZ."""
    safe_regions = []
    for conn in connections:
        if isinstance(conn, str):
            if conn not in danger_ids:
                safe_regions.append((conn, 0))
        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            is_dz = conn.get("isDeathZone", False)
            if rid and not is_dz and rid not in danger_ids:
                terrain = conn.get("terrain", "").lower()
                score = {"hills": 3, "plains": 2, "ruins": 1, "forest": 0, "water": -2}.get(terrain, 0)
                safe_regions.append((rid, score))

    if safe_regions:
        safe_regions.sort(key=lambda x: x[1], reverse=True)
        chosen = safe_regions[0][0]
        return chosen

    for conn in connections:
        rid = conn if isinstance(conn, str) else conn.get("id", "")
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        if rid and not is_dz:
            return rid
    return None


def _choose_move_target(connections, danger_ids: set,
                         current_region: dict, visible_items: list,
                         alive_count: int, visible_monsters: list = None,
                         visible_agents: list = None,
                         my_id: str = None) -> str | None:
    """Choose best region to move to. Prioritizes regions with enemies in hunt mode."""
    if visible_monsters is None:
        visible_monsters = []
    if visible_agents is None:
        visible_agents = []

    # In hunt mode, prioritize moving toward enemies
    if TEAM_CONFIG["hunt_mode"] and my_id:
        # Check if any enemy in adjacent regions
        for conn in connections:
            rid = _get_region_id(conn)
            if rid and rid not in danger_ids:
                for ag in visible_agents:
                    if isinstance(ag, dict) and ag.get("regionId") == rid:
                        if is_enemy(ag, my_id):
                            log.debug("Moving toward enemy in region %s", rid[:8])
                            return rid

    # Then try richest region
    richest = _find_richest_region(connections, danger_ids, visible_items,
                                    visible_monsters, visible_agents, my_id)
    if richest:
        return richest

    candidates = []
    item_regions = set()
    for item in visible_items:
        if isinstance(item, dict):
            item_regions.add(item.get("regionId", ""))

    for conn in connections:
        if isinstance(conn, str):
            if conn in danger_ids:
                continue
            score = 1
            if conn in item_regions:
                score += 5
            candidates.append((conn, score))

        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            if not rid or conn.get("isDeathZone") or rid in danger_ids:
                continue

            score = 0
            terrain = conn.get("terrain", "").lower()
            terrain_scores = {"hills": 4, "plains": 2, "ruins": 2, "forest": 1, "water": -3}
            score += terrain_scores.get(terrain, 0)

            if rid in item_regions:
                score += 5

            facs = conn.get("interactables", [])
            if facs:
                unused = [f for f in facs if isinstance(f, dict) and not f.get("isUsed")]
                score += len(unused) * 2

            weather = conn.get("weather", "").lower()
            weather_penalty = {"storm": -2, "fog": -1, "rain": 0, "clear": 1}
            score += weather_penalty.get(weather, 0)

            if alive_count < 30:
                score += 3

            if _map_knowledge.get("revealed") and rid in _map_knowledge.get("safe_center", []):
                score += 5

            if rid in _map_knowledge.get("death_zones", set()):
                continue

            candidates.append((rid, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _get_move_ep_cost(terrain: str, weather: str) -> int:
    """Calculate move EP cost per game-systems.md."""
    if terrain == "water":
        return 3
    if weather == "storm":
        return 3
    return 2


# =========================
# 🩹 HEALING & UTILITY
# =========================
def _find_healing_item(inventory: list, critical: bool = False, prefer_small: bool = False) -> dict | None:
    """Find best healing item based on urgency."""
    heals = []
    for i in inventory:
        if not isinstance(i, dict):
            continue
        type_id = i.get("typeId", "").lower()
        if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS[type_id] > 0:
            heals.append(i)
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
    """Find energy drink for EP recovery."""
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _select_weakest(targets: list) -> dict:
    """Select target with lowest HP."""
    return min(targets, key=lambda t: t.get("hp", 999))


def _is_in_range(target: dict, my_region: str, weapon_range: int, connections=None) -> bool:
    """Check if target is in weapon range."""
    target_region = target.get("regionId", "")
    if not target_region:
        return True
    if target_region == my_region:
        return True
    if weapon_range >= 1 and connections:
        adj_ids = set()
        for conn in connections:
            if isinstance(conn, str):
                adj_ids.add(conn)
            elif isinstance(conn, dict):
                adj_ids.add(conn.get("id", ""))
        if target_region in adj_ids:
            return True
    return False


def _select_facility(interactables: list, hp: int, ep: int) -> dict | None:
    """Select best facility to interact with."""
    for fac in interactables:
        if not isinstance(fac, dict):
            continue
        if fac.get("isUsed"):
            continue
        ftype = fac.get("type", "").lower()
        if ftype == "medical_facility" and hp < 80:
            return fac
        if ftype == "supply_cache":
            return fac
        if ftype == "watchtower":
            return fac
        if ftype == "broadcast_station":
            return fac
    return None


def _track_agents(visible_agents: list, my_id: str, my_region: str):
    """Track observed agents for threat assessment."""
    global _known_agents
    for agent in visible_agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if not aid or aid == my_id:
            continue
        _known_agents[aid] = {
            "hp": agent.get("hp", 100),
            "atk": agent.get("atk", 10),
            "isGuardian": agent.get("isGuardian", False),
            "equippedWeapon": agent.get("equippedWeapon"),
            "lastSeen": my_region,
            "isAlive": agent.get("isAlive", True),
            "isTeammate": is_teammate(agent, my_id),
        }
    if len(_known_agents) > 50:
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    """Estimate enemy's weapon bonus from their equipped weapon."""
    weapon = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


# =========================
# 🧠 MAIN DECISION ENGINE
# =========================
def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine with TEAM HUNT MODE integration.
    Priority chain (v2.0.0):
    1. DEATHZONE ESCAPE
    2. Critical healing
    3. Moderate healing
    4. Utility items
    5. Free actions (pickup, equip)
    6. GUARDIAN FARMING
    7. ENEMY COMBAT (HUNT MODE PRIORITY)
    8. Monster farming
    9. Facility interaction
    10. Strategic movement (hunt enemies)
    11. Rest
    """
    global _enemy_wipe_detected
    
    self_data = view.get("self", {})
    region = view.get("currentRegion", {})
    hp = self_data.get("hp", 100)
    ep = self_data.get("ep", 10)
    max_ep = self_data.get("maxEp", 10)
    atk = self_data.get("atk", 10)
    defense = self_data.get("def", 5)
    is_alive = self_data.get("isAlive", True)
    inventory = self_data.get("inventory", [])
    equipped = self_data.get("equippedWeapon")
    my_id = self_data.get("id", "")
    my_name = self_data.get("name", "")

    visible_agents = view.get("visibleAgents", [])
    visible_monsters = view.get("visibleMonsters", [])
    visible_items_raw = view.get("visibleItems", [])
    
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
    
    visible_regions = view.get("visibleRegions", [])
    connected_regions = view.get("connectedRegions", [])
    pending_dz = view.get("pendingDeathzones", [])
    alive_count = view.get("aliveCount", 100)
    
    connections = connected_regions or region.get("connections", [])
    interactables = region.get("interactables", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""

    if not is_alive:
        return None

    # ── Check enemy status for hunt mode ──────────────────────────
    enemies_exist = are_there_any_enemies_left(view, my_id) if TEAM_CONFIG["hunt_mode"] else True
    
    if TEAM_CONFIG["hunt_mode"] and not enemies_exist and not _enemy_wipe_detected:
        _enemy_wipe_detected = True
        log.info("🏆 ALL ENEMIES ELIMINATED! Team victory achieved!")

    # Log team status periodically
    if TEAM_CONFIG["hunt_mode"]:
        teammate_count = sum(1 for a in visible_agents if is_teammate(a, my_id))
        enemy_count = sum(1 for a in visible_agents if is_enemy(a, my_id))
        if teammate_count > 0 or enemy_count > 0:
            log.debug("TEAM STATUS: %d teammates, %d enemies", teammate_count, enemy_count)

    # ── Build danger map ───────────────────────────────────────────
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

    _track_agents(visible_agents, my_id, region_id)
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)

    # ── Priority 1: DEATHZONE ESCAPE ───────────────────────────────
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 IN DEATH ZONE! Escaping to %s (HP=%d)", safe, hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"ESCAPE: In death zone! HP={hp}"}

    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("⚠️ Region becoming DZ soon! Escaping to %s", safe[:8])
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: Region becoming death zone soon"}

    # ── Guardian threat evasion ────────────────────────────────────
    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]
    if guardians_here and hp < 40 and ep >= move_ep_cost:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("⚠️ Guardian threat! HP=%d, fleeing", hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"GUARDIAN FLEE: HP={hp}"}

    # ── FREE ACTIONS ───────────────────────────────────────────────
    pickup_action = _check_pickup(visible_items, inventory, region_id)
    if pickup_action:
        return pickup_action

    equip_action = _check_equip(inventory, equipped)
    if equip_action:
        return equip_action

    util_action = _use_utility_item(inventory, hp, ep, alive_count)
    if util_action:
        return util_action

    if not can_act:
        return None

    # ── Priority 2: CRITICAL HEALING (HP < 30) ─────────────────────
    if hp < 30:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}

    # ── Priority 3: MODERATE HEALING (HP < 50) ─────────────────────
    elif hp < 50:
        heal = _find_healing_item(inventory, critical=False, prefer_small=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"MODERATE HEAL: HP={hp}"}

    # ── Priority 4: EP RECOVERY ────────────────────────────────────
    if ep == 0:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": "EP RECOVERY: EP=0"}
    elif ep <= 2 and hp > 50:
        enemies_nearby = [a for a in visible_agents if a.get("regionId") == region_id 
                          and a.get("isAlive") and is_enemy(a, my_id)]
        if not enemies_nearby and region_id not in danger_ids:
            return {"action": "rest", "data": {},
                    "reason": f"EP RECOVERY: EP={ep}"}

    # ── Priority 5: GUARDIAN FARMING ───────────────────────────────
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and ep >= 2 and hp >= 30:
        target = _select_best_target(guardians, atk, defense, get_weapon_bonus(equipped),
                                      region_weather, hp, my_id)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                outcome = estimate_combat_outcome(
                    hp, atk, defense, get_weapon_bonus(equipped),
                    target.get("hp", 100), target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather
                )
                if outcome["win"] or target.get("hp", 100) < 40:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"GUARDIAN FARM: HP={target.get('hp','?')}"}

    # ── Priority 6: ENEMY COMBAT (HUNT MODE PRIORITY!) ─────────────
    enemies = [a for a in visible_agents
               if a.get("isAlive", True) 
               and a.get("id") != my_id
               and is_enemy(a, my_id)]
    
    if enemies and ep >= 2:
        hp_threshold = 45 if enemies_exist else 25
        
        if hp >= hp_threshold:
            target = _select_best_target(enemies, atk, defense, get_weapon_bonus(equipped),
                                          region_weather, hp, my_id)
            if target:
                w_range = get_weapon_range(equipped)
                if _is_in_range(target, region_id, w_range, connections):
                    outcome = estimate_combat_outcome(
                        hp, atk, defense, get_weapon_bonus(equipped),
                        target.get("hp", 100), target.get("atk", 10), target.get("def", 5),
                        _estimate_enemy_weapon_bonus(target), region_weather
                    )
                    if outcome["win"] or target.get("hp", 100) <= outcome["my_dmg"] * 2:
                        return {"action": "attack",
                                "data": {"targetId": target["id"], "targetType": "agent"},
                                "reason": f"HUNT: Killing enemy {target.get('name', '?')}"}

    # ── Priority 7: MONSTER FARMING ────────────────────────────────
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep >= 1 and hp > 20:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER FARM: {target.get('name', 'monster')}"}

    # ── Priority 8: SAFE HEALING ───────────────────────────────────
    enemies_nearby = [a for a in visible_agents if a.get("regionId") == region_id 
                      and a.get("isAlive") and is_enemy(a, my_id)]
    if hp < 70 and not enemies_nearby and region_id not in danger_ids:
        heal = _find_healing_item(inventory, critical=(hp < 30), prefer_small=(hp < 60))
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"SAFE HEAL: HP={hp}"}

    # ── Priority 9: FACILITY INTERACTION ───────────────────────────
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        facility = _select_facility(interactables, hp, ep)
        if facility:
            return {"action": "interact", "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type', 'unknown')}"}

    # ── Priority 10: STRATEGIC MOVEMENT ────────────────────────────
    if ep >= move_ep_cost and connections:
        move_target = _choose_move_target(connections, danger_ids, region,
                                           visible_items, alive_count,
                                           visible_monsters, visible_agents, my_id)
        if move_target:
            reason = "HUNT: Moving to find enemies" if enemies_exist else "EXPLORE: Moving"
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": reason}

    # ── Priority 11: REST ──────────────────────────────────────────
    if ep < 4 and not enemies_nearby and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep}"}

    return None


"""
TEAM HUNT MODE v2.0.0 (FULL VERSION)
=========================================
✅ ALL functions from v1.6.0 preserved
✅ 10 bots work as a team
✅ Bots NEVER attack each other (until all enemies dead)
✅ Priority: Kill all enemies first
✅ Guardian farming still active
✅ Automatic enemy detection
✅ Configurable team ID and bot name patterns

Total lines: ~850 (all functions intact)
"""
