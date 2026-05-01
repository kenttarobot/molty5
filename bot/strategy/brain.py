"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v3.0.0 - FULL TEAM COORDINATION + META CORE
==============================================================
TEAM FEATURES:
- Multiple bots in one room work as a team (theobdg1, theobdg2, etc.)
- TEAM_ID detection via environment variable
- Bots NEVER attack each other until all enemies are dead
- Automatic teammate recognition (by name prefix or team_id)
- Shared target priority (avoid wasting DPS on same target)
- Help request system (teammate in danger)

META FEATURES:
- 3 MODE OTAK: RUSH (Early) → HUNT (Mid) → SURVIVE (Late)
- KILL STEAL: Finish enemies with HP < 20
- EXECUTE MODE: Attack if enemy HP <= my damage
- WEAKEST ENEMY FIRST: Primary combat strategy
- EP MANAGEMENT: Maintain above 60% at all times
- EARLY GAME WEAPON PRIORITY: Cari senjata pertama
- SMOLTZ ABSOLUTE PRIORITY: Pickup currency first
"""

from bot.utils.logger import get_logger
import os
import random

log = get_logger(__name__)


# =========================
# 🔥 CONFIGURATION
# =========================

# ── TEAM CONFIGURATION (v3.0.0) ──────────────────────────────────────
TEAM_ID = os.getenv("TEAM_ID", "THEO_SQUAD")
TEAM_CONFIG = {
    "team_id": TEAM_ID,
    "hunt_mode": True,
    "never_attack_teammates": True,
    "fight_teammates_after_wipe": False,
    "bot_name_prefixes": ["theobdg", "MoltyBot", "HunterBot", "BrainBot"],
    
    # NEW FEATURES v3.0.0
    "enable_team_coordination": True,
    "enable_help_requests": True,
    "enable_shared_memory": True,
}

# ── SMOLTZ FARMING CONFIGURATION ─────────────────────────────────────
GUARDIAN_SMOLTZ_REWARD = 120
PLAYER_KILL_SMOLTZ = 100
LOW_HP_FINISH_THRESHOLD = 30
FARMING_HP_MIN = 25

# Mode switching thresholds
RUSH_MAX_ALIVE = 50
HUNT_MAX_ALIVE = 20

# Early game settings
EARLY_GAME_TURNS = 50

# ── EP Management Thresholds ─────────────────────────────────────────
# Default thresholds (akan di-override berdasarkan mode)
EP_SAFE_THRESHOLD_DEFAULT = 0.60
EP_COMBAT_MIN_DEFAULT = 0.30

# ── Weapon stats ──────────────────────────────────────────────────────
WEAPONS = {
    "fist": {"bonus": 0, "range": 0, "value": 0, "priority": 0},
    "dagger": {"bonus": 10, "range": 0, "value": 20, "priority": 10},
    "bow": {"bonus": 5, "range": 1, "value": 15, "priority": 5},
    "pistol": {"bonus": 10, "range": 1, "value": 25, "priority": 10},
    "sword": {"bonus": 20, "range": 0, "value": 40, "priority": 20},
    "sniper": {"bonus": 28, "range": 2, "value": 70, "priority": 28},
    "katana": {"bonus": 35, "range": 0, "value": 80, "priority": 35},
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

# ── Item priority for pickup ──────────────────────────────────────────
ITEM_PRIORITY = {
    "rewards": 1000,       # SMOLTZ - ABSOLUTE HIGHEST!
    "katana": 900,
    "sniper": 850,
    "sword": 800,
    "pistol": 750,
    "dagger": 700,
    "bow": 650,
    "medkit": 500,
    "bandage": 450,
    "emergency_food": 400,
    "energy_drink": 350,
    "binoculars": 200,
    "map": 150,
    "megaphone": 100,
}

RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,
}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain": 0.05,
    "fog": 0.10,
    "storm": 0.15,
}


# =========================
# 🔥 GLOBAL STATE
# =========================

_known_agents: dict = {}
_known_guardians: dict = {}
_total_smoltz_collected: int = 0
_farming_stats: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_current_mode: str = "RUSH"
_turn_counter: int = 0

# Team shared memory (v3.0.0)
_team_memory: dict = {
    "teammates": {},      # {agent_id: {"hp": 100, "region": "x", "last_seen": turn, "needs_help": False}}
    "enemies": {},        # {agent_id: {"hp": 50, "region": "x", "last_seen": turn, "is_guardian": False}}
    "guardians": [],      # [(region_id, hp, turn)]
    "loot": [],           # [(region_id, item_type, priority)]
}

_enemy_wipe_detected = False


# =========================
# 🔥 TEAM DETECTION (v3.0.0)
# =========================

def is_teammate(agent: dict, my_id: str) -> bool:
    """Check if an agent is a teammate."""
    if not agent or agent.get("id") == my_id:
        return True
    
    # Method 1: Check team_id field
    if TEAM_CONFIG["team_id"] and agent.get("team_id") == TEAM_CONFIG["team_id"]:
        return True
    
    # Method 2: Check bot name patterns (theobdg1, theobdg2, etc.)
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


def update_team_memory(view: dict, my_id: str, my_region: str, current_turn: int):
    """Update shared team memory with visible information."""
    global _team_memory
    
    visible_agents = view.get("visibleAgents", [])
    
    for agent in visible_agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if not aid or aid == my_id:
            continue
        
        if is_teammate(agent, my_id):
            _team_memory["teammates"][aid] = {
                "hp": agent.get("hp", 100),
                "region": agent.get("regionId", my_region),
                "last_seen": current_turn,
                "needs_help": agent.get("hp", 100) < 35,
            }
        elif is_enemy(agent, my_id):
            _team_memory["enemies"][aid] = {
                "hp": agent.get("hp", 100),
                "region": agent.get("regionId", ""),
                "last_seen": current_turn,
                "is_guardian": agent.get("isGuardian", False),
            }


def check_teammate_need_help() -> dict | None:
    """Check if any teammate needs emergency help."""
    for tid, info in _team_memory["teammates"].items():
        if info.get("needs_help", False):
            log.warning("🚨 Teammate %s needs help! HP=%d at %s", 
                       tid[:8], info.get("hp", 0), info.get("region", "unknown"))
            return {"need_help": True, "teammate_id": tid, "region": info.get("region")}
    return None


# =========================
# 🔥 HELPER FUNCTIONS
# =========================

def calc_damage(atk: int, weapon_bonus: int, target_def: int,
                weather: str = "clear") -> int:
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped_weapon) -> int:
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_priority(equipped_weapon) -> int:
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("priority", 0)


def get_weapon_range(equipped_weapon) -> int:
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)


def has_weapon_equipped(equipped) -> bool:
    if not equipped:
        return False
    type_id = equipped.get("typeId", "").lower()
    return type_id != "fist" and type_id in WEAPONS and WEAPONS.get(type_id, {}).get("bonus", 0) > 0


def has_any_weapon_in_inventory(inventory: list) -> bool:
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("category") == "weapon":
            return True
    return False


def _resolve_region(entry, view: dict):
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, str):
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None


def _get_region_id(entry) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id", "")
    return ""


def reset_game_state():
    global _known_agents, _known_guardians, _total_smoltz_collected, _farming_stats
    global _map_knowledge, _current_mode, _turn_counter, _team_memory, _enemy_wipe_detected
    
    _known_agents = {}
    _known_guardians = {}
    _total_smoltz_collected = 0
    _turn_counter = 0
    _farming_stats = {
        "guardians_killed": 0,
        "players_killed": 0,
        "monsters_killed": 0,
        "items_collected": 0,
        "total_smoltz": 0,
    }
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _current_mode = "RUSH"
    _team_memory = {
        "teammates": {},
        "enemies": {},
        "guardians": [],
        "loot": [],
    }
    _enemy_wipe_detected = False
    
    log.info("=" * 60)
    log.info("🤖 TEAM HUNT MODE v3.0.0 - Team ID: %s", TEAM_CONFIG["team_id"])
    log.info("   RUSH (Early) → HUNT (Mid) → SURVIVE (Late)")
    log.info("   Bots will NEVER attack teammates!")
    log.info("=" * 60)


def learn_from_map(view: dict):
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

    log.info("🗺️ MAP LEARNED: %d DZ regions", len(_map_knowledge["death_zones"]))


# =========================
# 🧠 MODE MANAGEMENT
# =========================

def _get_mode(alive_count: int) -> tuple[str, dict]:
    global _current_mode
    
    if alive_count >= RUSH_MAX_ALIVE:
        mode = "RUSH"
        thresholds = {
            "EP_SAFE": 0.40,
            "EP_COMBAT": 0.15,
            "description": "🔥 AGGRESSIVE - Kill everything!"
        }
    elif alive_count >= HUNT_MAX_ALIVE:
        mode = "HUNT"
        thresholds = {
            "EP_SAFE": 0.50,
            "EP_COMBAT": 0.30,
            "description": "🐺 SMART HUNTING - Only easy kills"
        }
    else:
        mode = "SURVIVE"
        thresholds = {
            "EP_SAFE": 0.70,
            "EP_COMBAT": 0.50,
            "description": "🧬 SURVIVAL - Only sure wins"
        }
    
    if mode != _current_mode:
        _current_mode = mode
        log.info("🔄 MODE SWITCH: %s - %s", mode, thresholds["description"])
    
    return mode, thresholds


def _is_easy_kill(enemy: dict, my_atk: int, weapon_bonus: int) -> bool:
    hp = enemy.get("hp", 100)
    enemy_def = enemy.get("def", 5)
    damage_per_hit = max(1, my_atk + weapon_bonus - int(enemy_def * 0.5))
    return hp <= damage_per_hit * 2


def _is_dangerous(enemy: dict) -> bool:
    atk = enemy.get("atk", 10)
    hp = enemy.get("hp", 100)
    return atk > 25 or (atk > 20 and hp > 70)


def _select_best_kill_target(targets: list, my_atk: int, weapon_bonus: int) -> dict | None:
    if not targets:
        return None
    
    alive_targets = [t for t in targets if t.get("hp", 0) > 0]
    if not alive_targets:
        return None
    
    return min(alive_targets, key=lambda t: t.get("hp", 999))


# =========================
# 🧠 COMBAT & STRATEGY
# =========================

def estimate_combat_outcome(my_hp, my_atk, my_def, my_weapon_bonus,
                            enemy_hp, enemy_atk, enemy_def, enemy_weapon_bonus,
                            weather) -> dict:
    my_dmg_per_hit = max(1, my_atk + my_weapon_bonus - int(enemy_def * 0.5))
    enemy_dmg_per_hit = max(1, enemy_atk + enemy_weapon_bonus - int(my_def * 0.5))

    weather_penalty = WEATHER_COMBAT_PENALTY.get(weather, 0)
    my_dmg_per_hit = int(my_dmg_per_hit * (1 - weather_penalty))
    enemy_dmg_per_hit = int(enemy_dmg_per_hit * (1 - weather_penalty))

    hits_to_kill = (enemy_hp + my_dmg_per_hit - 1) // my_dmg_per_hit if my_dmg_per_hit > 0 else 999
    hits_to_die = (my_hp + enemy_dmg_per_hit - 1) // enemy_dmg_per_hit if enemy_dmg_per_hit > 0 else 999

    return {
        "win": hits_to_kill <= hits_to_die,
        "my_dmg": my_dmg_per_hit,
        "hits_to_kill": hits_to_kill,
        "hits_to_die": hits_to_die,
    }


def _select_weakest_target(targets: list) -> dict | None:
    if not targets:
        return None
    alive_targets = [t for t in targets if t.get("hp", 0) > 0]
    if not alive_targets:
        return None
    return min(alive_targets, key=lambda t: t.get("hp", 999))


def _select_weakest_monster(monsters: list) -> dict | None:
    if not monsters:
        return None
    alive_monsters = [m for m in monsters if m.get("hp", 0) > 0]
    if not alive_monsters:
        return None
    return min(alive_monsters, key=lambda m: m.get("hp", 999))


# =========================
# 🧠 EARLY GAME WEAPON PRIORITY
# =========================

def _is_early_game() -> bool:
    global _turn_counter
    return _turn_counter < EARLY_GAME_TURNS


def _has_no_weapon(equipped, inventory: list) -> bool:
    has_weapon_equip = has_weapon_equipped(equipped)
    has_weapon_inv = has_any_weapon_in_inventory(inventory)
    return not has_weapon_equip and not has_weapon_inv


def _pickup_smoltz_and_weapon(items: list, inventory: list, region_id: str, 
                               is_early: bool, has_weapon: bool, equipped) -> dict | None:
    """PICKUP PRIORITY: 1. SMOLTZ, 2. WEAPON (early game priority)."""
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None
    
    # PRIORITY 1: SMOLTZ (currency)
    currency_items = [i for i in local_items 
                      if i.get("typeId", "").lower() == "rewards" 
                      or i.get("category", "").lower() == "currency"
                      or "moltz" in i.get("name", "").lower()]
    
    if currency_items:
        best_currency = max(currency_items, key=lambda i: i.get("amount", 1))
        amount = best_currency.get("amount", 50)
        log.info("💰💰💰 SMOLTZ PICKUP! +%d sMoltz", amount)
        return {"action": "pickup", "data": {"itemId": best_currency["id"]},
                "reason": f"💰 SMOLTZ: +{amount}!"}
    
    # PRIORITY 2: WEAPON (early game atau jika belum punya)
    weapon_items = [i for i in local_items if i.get("category") == "weapon"]
    
    if weapon_items:
        weapon_items.sort(key=lambda i: WEAPONS.get(i.get("typeId", "").lower(), {}).get("priority", 0), reverse=True)
        best_weapon = weapon_items[0]
        w_type = best_weapon.get("typeId", "unknown")
        w_priority = WEAPONS.get(w_type, {}).get("priority", 0)
        
        current_priority = get_weapon_priority(equipped) if equipped else 0
        
        if not has_weapon or w_priority > current_priority or is_early:
            log.info("⚔️ WEAPON PICKUP: %s (priority %d)", w_type, w_priority)
            return {"action": "pickup", "data": {"itemId": best_weapon["id"]},
                    "reason": f"WEAPON: {w_type}"}
    
    return None


def _check_equip_best_weapon(inventory: list, equipped) -> dict | None:
    current_bonus = get_weapon_bonus(equipped) if equipped else 0
    current_priority = get_weapon_priority(equipped) if equipped else 0
    
    best = None
    best_bonus = current_bonus
    best_priority = current_priority
    
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("category") == "weapon":
            type_id = item.get("typeId", "").lower()
            bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
            priority = WEAPONS.get(type_id, {}).get("priority", 0)
            
            if priority > best_priority or (priority == best_priority and bonus > best_bonus):
                best = item
                best_bonus = bonus
                best_priority = priority
    
    if best:
        log.info("⚔️ EQUIP BEST WEAPON: %s (+%d ATK)", best.get('typeId', 'weapon'), best_bonus)
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"⚔️ BEST WEAPON: +{best_bonus} ATK"}
    return None


def _find_healing_item(inventory: list, critical: bool = False, prefer_small: bool = False) -> dict | None:
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
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _is_in_range(target: dict, my_region: str, weapon_range: int, connections=None) -> bool:
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


def _get_move_ep_cost(terrain: str, weather: str) -> int:
    if terrain == "water":
        return 3
    if weather == "storm":
        return 3
    return 2


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    weapon = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue
        if isinstance(conn, dict):
            terrain = conn.get("terrain", "").lower()
            weather = conn.get("weather", "").lower()
            if terrain == "water" or weather == "storm":
                continue
        return rid
    return None


def _find_low_hp_enemy_region(enemies: list, current_region_id: str) -> str | None:
    if not enemies:
        return None
    
    low_hp_enemies = [e for e in enemies if e.get("hp", 100) < 40]
    if low_hp_enemies:
        target = min(low_hp_enemies, key=lambda e: e.get("hp", 999))
        target_region = target.get("regionId", "")
        if target_region and target_region != current_region_id:
            return target_region
    return None


def _find_richest_region(connections: list, danger_ids: set,
                         visible_items: list, visible_monsters: list,
                         visible_agents: list, my_id: str = None) -> str | None:
    """Find adjacent region with most valuable targets."""
    best_region = None
    best_score = -1

    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue

        score = 0

        for item in visible_items:
            if isinstance(item, dict) and item.get("regionId") == rid:
                type_id = item.get("typeId", "").lower()
                score += ITEM_PRIORITY.get(type_id, 10)

        for mon in visible_monsters:
            if isinstance(mon, dict) and mon.get("regionId") == rid:
                mon_hp = mon.get("hp", 100)
                score += 15 if mon_hp < 40 else 8

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


def _choose_move_target(connections, danger_ids: set,
                         current_region: dict, visible_items: list,
                         alive_count: int, visible_monsters: list = None,
                         visible_agents: list = None,
                         my_id: str = None) -> str | None:
    """Choose best region to move to. Prioritizes enemies in hunt mode."""
    if visible_monsters is None:
        visible_monsters = []
    if visible_agents is None:
        visible_agents = []

    # In hunt mode, prioritize moving toward enemies
    if TEAM_CONFIG["hunt_mode"] and my_id:
        for conn in connections:
            rid = _get_region_id(conn)
            if rid and rid not in danger_ids:
                for ag in visible_agents:
                    if isinstance(ag, dict) and ag.get("regionId") == rid:
                        if is_enemy(ag, my_id):
                            return rid

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


def _select_facility(interactables: list, hp: int, ep: int) -> dict | None:
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


def _track_smoltz_gain(view: dict, my_id: str):
    global _total_smoltz_collected, _farming_stats
    logs = view.get("recentLogs", [])
    for log_entry in logs:
        if not isinstance(log_entry, dict):
            continue
        msg = log_entry.get("message", "").lower()
        if "killed" in msg and "guardian" in msg:
            _farming_stats["guardians_killed"] = _farming_stats.get("guardians_killed", 0) + 1
            _total_smoltz_collected += GUARDIAN_SMOLTZ_REWARD
            _farming_stats["total_smoltz"] = _farming_stats.get("total_smoltz", 0) + GUARDIAN_SMOLTZ_REWARD
            log.info("💰 GUARDIAN KILL! +%d sMoltz", GUARDIAN_SMOLTZ_REWARD)
        elif "killed" in msg and "player" in msg:
            _farming_stats["players_killed"] = _farming_stats.get("players_killed", 0) + 1
            _total_smoltz_collected += PLAYER_KILL_SMOLTZ
            _farming_stats["total_smoltz"] = _farming_stats.get("total_smoltz", 0) + PLAYER_KILL_SMOLTZ
            log.info("💰 PLAYER KILL! +%d sMoltz", PLAYER_KILL_SMOLTZ)


# =========================
# 🧠 MAIN DECISION FUNCTION
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine - TEAM HUNT MODE v3.0.0
    
    PRIORITY CHAIN:
    1. DEATHZONE ESCAPE
    2. PICKUP SMOLTZ & WEAPON
    3. EQUIP BEST WEAPON
    4. KILL STEAL (HP < 20)
    5. TEAMMATE HELP REQUEST (NEW!)
    6. MODE-BASED COMBAT (RUSH/HUNT/SURVIVE)
    7. GUARDIAN FARMING
    8. MONSTER FARMING
    9. HP/EP MAINTENANCE
    10. MOVEMENT & HUNTING
    """
    global _turn_counter, _enemy_wipe_detected
    _turn_counter += 1
    
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
    
    _track_smoltz_gain(view, my_id)
    
    current_atk_bonus = get_weapon_bonus(equipped)
    total_atk = atk + current_atk_bonus
    
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
    
    connected_regions = view.get("connectedRegions", [])
    pending_dz = view.get("pendingDeathzones", [])
    alive_count = view.get("aliveCount", 100)
    
    connections = connected_regions or region.get("connections", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""
    
    if not is_alive:
        return None
    
    # Update team memory
    if TEAM_CONFIG["enable_team_coordination"]:
        update_team_memory(view, my_id, region_id, _turn_counter)
    
    # Check enemy status for hunt mode
    enemies_exist = are_there_any_enemies_left(view, my_id) if TEAM_CONFIG["hunt_mode"] else True
    
    if TEAM_CONFIG["hunt_mode"] and not enemies_exist and not _enemy_wipe_detected:
        _enemy_wipe_detected = True
        log.info("🏆 ALL ENEMIES ELIMINATED! Team victory achieved!")
    
    # Determine mode & thresholds
    mode, mode_thresholds = _get_mode(alive_count)
    EP_SAFE_THRESHOLD = mode_thresholds["EP_SAFE"]
    EP_COMBAT_MIN = mode_thresholds["EP_COMBAT"]
    
    is_early = _is_early_game()
    has_weapon = not _has_no_weapon(equipped, inventory)
    
    # Build danger map
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
    
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0
    
    enemies_in_region = [a for a in visible_agents 
                         if a.get("regionId") == region_id 
                         and a.get("isAlive") 
                         and is_enemy(a, my_id)]
    
    in_deathzone = region.get("isDeathZone", False) or region_id in danger_ids
    
    # Log team status periodically
    if TEAM_CONFIG["hunt_mode"] and _turn_counter % 20 == 0:
        teammate_count = sum(1 for a in visible_agents if is_teammate(a, my_id))
        enemy_count = sum(1 for a in visible_agents if is_enemy(a, my_id))
        log.debug("📊 TEAM STATUS: %d teammates, %d enemies", teammate_count, enemy_count)
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 1: DEATHZONE ESCAPE
    # ═══════════════════════════════════════════════════════════════════
    if in_deathzone:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 [%s] DEATHZONE! Escaping", mode)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 2: PICKUP SMOLTZ & WEAPON
    # ═══════════════════════════════════════════════════════════════════
    pickup_action = _pickup_smoltz_and_weapon(visible_items, inventory, region_id, 
                                               is_early, has_weapon, equipped)
    if pickup_action:
        return pickup_action
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 3: EQUIP BEST WEAPON
    # ═══════════════════════════════════════════════════════════════════
    equip_action = _check_equip_best_weapon(inventory, equipped)
    if equip_action:
        return equip_action
    
    if not can_act:
        return None
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 4: KILL STEAL (HP < 20)
    # ═══════════════════════════════════════════════════════════════════
    all_enemies = [a for a in visible_agents
                   if a.get("isAlive", True)
                   and a.get("id") != my_id
                   and is_enemy(a, my_id)]
    
    for enemy in all_enemies:
        if enemy.get("hp", 100) < 20:
            w_range = get_weapon_range(equipped)
            if _is_in_range(enemy, region_id, w_range, connections):
                log.info("🔪 KILL STEAL! Enemy HP=%d < 20!", enemy.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": enemy["id"], "targetType": "agent"},
                        "reason": "🔥 KILL STEAL: HP<20!"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 5: TEAMMATE HELP REQUEST (v3.0.0)
    # ═══════════════════════════════════════════════════════════════════
    if TEAM_CONFIG["enable_help_requests"] and mode != "RUSH":
        help_needed = check_teammate_need_help()
        if help_needed and help_needed.get("region") and help_needed["region"] != region_id:
            if ep >= move_ep_cost:
                log.info("🆘 Moving to help teammate at %s", help_needed["region"][:8])
                return {"action": "move", "data": {"regionId": help_needed["region"]},
                        "reason": "TEAM: Helping teammate!"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 6: MODE-BASED COMBAT
    # ═══════════════════════════════════════════════════════════════════
    
    can_fight = ep_ratio >= EP_COMBAT_MIN and hp >= FARMING_HP_MIN
    
    # ── DANGEROUS ENEMY AVOIDANCE (except RUSH mode) ───────────────────
    if mode != "RUSH":
        dangerous_enemies = [e for e in all_enemies if _is_dangerous(e) and e.get("regionId") == region_id]
        if dangerous_enemies and can_fight:
            for dangerous in dangerous_enemies[:3]:
                outcome = estimate_combat_outcome(
                    hp, atk, defense, current_atk_bonus,
                    dangerous.get("hp", 100), dangerous.get("atk", 10), dangerous.get("def", 5),
                    _estimate_enemy_weapon_bonus(dangerous), region_weather
                )
                if not outcome["win"]:
                    safe = _find_safe_region(connections, danger_ids, view)
                    if safe:
                        log.warning("⚠️ [%s] Fleeing from dangerous enemy!", mode)
                        return {"action": "move", "data": {"regionId": safe},
                                "reason": "FLEE: Dangerous enemy"}
    
    # ── MODE RUSH: AGGRESSIVE ─────────────────────────────────────────
    if mode == "RUSH":
        if all_enemies and can_fight:
            target = _select_best_kill_target(all_enemies, total_atk, 0)
            if target:
                w_range = get_weapon_range(equipped)
                if _is_in_range(target, region_id, w_range, connections):
                    enemy_hp = target.get("hp", 100)
                    my_damage = max(1, total_atk - int(target.get("def", 5) * 0.5))
                    if enemy_hp <= my_damage or enemy_hp < 50:
                        log.info("🔥 [RUSH] ATTACK! Enemy HP=%d", enemy_hp)
                        return {"action": "attack",
                                "data": {"targetId": target["id"], "targetType": "agent"},
                                "reason": f"🔥 RUSH: HP={enemy_hp}"}
        
        guardians = [a for a in visible_agents if a.get("isGuardian", False) and a.get("isAlive", True)]
        if guardians and can_fight:
            target = _select_weakest_target(guardians)
            if target:
                w_range = get_weapon_range(equipped)
                if _is_in_range(target, region_id, w_range, connections):
                    if is_enemy(target, my_id):
                        log.info("🎯 [RUSH] GUARDIAN HUNT! +120 sMoltz")
                        return {"action": "attack",
                                "data": {"targetId": target["id"], "targetType": "agent"},
                                "reason": "💰 RUSH: Guardian 120 sMoltz!"}
    
    # ── MODE HUNT: SMART KILLING ──────────────────────────────────────
    elif mode == "HUNT":
        if all_enemies and can_fight:
            easy_targets = [e for e in all_enemies 
                           if _is_easy_kill(e, total_atk, 0)
                           and e.get("regionId") == region_id]
            
            if easy_targets:
                target = _select_best_kill_target(easy_targets, total_atk, 0)
                if target:
                    w_range = get_weapon_range(equipped)
                    if _is_in_range(target, region_id, w_range, connections):
                        enemy_hp = target.get("hp", 100)
                        log.info("🐺 [HUNT] Easy kill! Enemy HP=%d", enemy_hp)
                        return {"action": "attack",
                                "data": {"targetId": target["id"], "targetType": "agent"},
                                "reason": f"🐺 HUNT: Easy kill HP={enemy_hp}"}
        
        guardians = [a for a in visible_agents if a.get("isGuardian", False) and a.get("isAlive", True)]
        if guardians and can_fight:
            target = _select_weakest_target(guardians)
            if target:
                w_range = get_weapon_range(equipped)
                if _is_in_range(target, region_id, w_range, connections):
                    if is_enemy(target, my_id):
                        log.info("🎯 [HUNT] GUARDIAN: 120 sMoltz")
                        return {"action": "attack",
                                "data": {"targetId": target["id"], "targetType": "agent"},
                                "reason": "💰 HUNT: Guardian 120 sMoltz!"}
    
    # ── MODE SURVIVE: ONLY SURE WINS ──────────────────────────────────
    elif mode == "SURVIVE":
        if all_enemies and can_fight and hp >= 50:
            for enemy in all_enemies:
                if enemy.get("regionId") != region_id:
                    continue
                outcome = estimate_combat_outcome(
                    hp, atk, defense, current_atk_bonus,
                    enemy.get("hp", 100), enemy.get("atk", 10), enemy.get("def", 5),
                    _estimate_enemy_weapon_bonus(enemy), region_weather
                )
                if outcome["win"] and outcome["hits_to_die"] > outcome["hits_to_kill"]:
                    w_range = get_weapon_range(equipped)
                    if _is_in_range(enemy, region_id, w_range, connections):
                        log.info("🧬 [SURVIVE] Sure win! Enemy HP=%d", enemy.get("hp", 0))
                        return {"action": "attack",
                                "data": {"targetId": enemy["id"], "targetType": "agent"},
                                "reason": f"🧬 SURVIVE: Sure win"}
        
        enemies_nearby = [e for e in all_enemies if e.get("regionId") == region_id]
        if enemies_nearby:
            safe = _find_safe_region(connections, danger_ids, view)
            if safe:
                log.warning("🧬 [SURVIVE] No sure win, fleeing!")
                return {"action": "move", "data": {"regionId": safe},
                        "reason": "SURVIVE: Fleeing to safety"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 7: GUARDIAN FARMING (fallback)
    # ═══════════════════════════════════════════════════════════════════
    guardians = [a for a in visible_agents if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and can_fight and hp >= 30:
        target = _select_weakest_target(guardians)
        if target and is_enemy(target, my_id):
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("💰 GUARDIAN: 120 sMoltz!")
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "💰 GUARDIAN: 120 sMoltz!"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 8: MONSTER FARMING
    # ═══════════════════════════════════════════════════════════════════
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and can_fight and hp > 20:
        target = _select_weakest_monster(monsters)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": f"MONSTER: HP={target.get('hp', '?')}"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 9: HP & EP MAINTENANCE
    # ═══════════════════════════════════════════════════════════════════
    
    if hp < 30:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}
    elif hp < 50 and not enemies_in_region and not in_deathzone:
        heal = _find_healing_item(inventory, critical=False, prefer_small=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}
    
    if ep_ratio < EP_SAFE_THRESHOLD:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP: {ep}/{max_ep} -> energy drink"}
        
        if not enemies_in_region and not in_deathzone:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 10: MOVEMENT & ACTIVE HUNTING
    # ═══════════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        if mode in ["RUSH", "HUNT"]:
            hunt_region = _find_low_hp_enemy_region(all_enemies, region_id)
            if hunt_region and hunt_region not in danger_ids:
                log.info("🎯 ACTIVE HUNTING: Moving to low HP enemy at %s", hunt_region[:8])
                return {"action": "move", "data": {"regionId": hunt_region},
                        "reason": "HUNT: Chasing low HP enemy!"}
        
        move_target = _choose_move_target(connections, danger_ids, region,
                                           visible_items, alive_count,
                                           visible_monsters, visible_agents, my_id)
        if move_target:
            reason = "HUNT: Moving to find enemies" if enemies_exist else "EXPLORE: Moving"
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": reason}
    
    # ═══════════════════════════════════════════════════════════════════
    # CHAOS FACTOR (10% chance - anti-predict)
    # ═══════════════════════════════════════════════════════════════════
    if random.random() < 0.1 and mode == "RUSH":
        if len(all_enemies) >= 2:
            targets_in_range = [e for e in all_enemies 
                               if _is_in_range(e, region_id, get_weapon_range(equipped), connections)]
            if len(targets_in_range) >= 2:
                second_target = targets_in_range[1] if len(targets_in_range) > 1 else targets_in_range[0]
                log.info("🎲 CHAOS FACTOR: Attacking random target!")
                return {"action": "attack",
                        "data": {"targetId": second_target["id"], "targetType": "agent"},
                        "reason": "🎲 CHAOS: Unpredictable!"}
    
    return None


"""
================================================================================
TEAM HUNT MODE v3.0.0 - FULL TEAM COORDINATION
================================================================================

KEY FEATURES:
-------------
1. TEAM DETECTION:
   - Bot mengenali teammate via TEAM_ID atau nama prefix (theobdg1, theobdg2, etc.)
   - Bots NEVER attack teammates until all enemies are dead

2. TEAM COORDINATION:
   - Shared memory for teammate positions and status
   - Help request system (move to help low HP teammates)
   - Enemy wipe detection

3. 3 MODE OTAK:
   - RUSH (alive >= 50): Barbar, kill semua
   - HUNT (20-49): Cerdas, bunuh yang pasti mati
   - SURVIVE (< 20): Selektif, fight hanya jika pasti menang

4. META FEATURES:
   - KILL STEAL: Finish HP < 20
   - EXECUTE MODE: Attack if enemy HP <= my damage
   - EARLY GAME WEAPON PRIORITY: Cari senjata pertama
   - SMOLTZ ABSOLUTE PRIORITY: Pickup currency first
   - EP MANAGEMENT: Maintain above thresholds based on mode

EXPORTED FUNCTIONS:
------------------
- decide_action() - Main decision engine
- reset_game_state() - Reset per-game tracking
- learn_from_map() - Learn map layout

USAGE:
------
Set environment variable TEAM_ID to enable team mode:
    export TEAM_ID="THEO_SQUAD"
    
Or in docker-compose.yml:
    environment:
      - TEAM_ID=THEO_SQUAD
      - AGENT_NAME=theobdg1
================================================================================
"""
