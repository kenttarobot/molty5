"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v3.0.0 - ULTIMATE UPGRADE FROM v1.5.2
==============================================================
ENHANCEMENTS:
- 3 MODE OTAK: RUSH (Early) → HUNT (Mid) → SURVIVE (Late)
- EP MANAGEMENT: Maintain EP above thresholds based on mode
- KILL STEAL: Buru musuh HP < 20
- ACTIVE HUNTING: Ngejar musuh low HP
- THREAT MEMORY: Hindari musuh berbahaya
- EARLY GAME WEAPON RUSH: Cari senjata pertama
- TEAM COORDINATION: Multiple bot support via TEAM_ID
- EXECUTE MODE: Attack if enemy HP <= my damage
"""

from bot.utils.logger import get_logger
import os
import random

log = get_logger(__name__)


# =========================
# 🔥 CONFIGURATION
# =========================

# ── TEAM CONFIGURATION (v3.0.0) ──────────────────────────────────────
TEAM_ID = os.getenv("TEAM_ID", None)
TEAM_CONFIG = {
    "team_id": TEAM_ID,
    "hunt_mode": True,
    "never_attack_teammates": True,
    "bot_name_prefixes": ["theobdg", "MoltyBot", "HunterBot"],
}

# ── SMOLTZ FARMING ───────────────────────────────────────────────────
GUARDIAN_SMOLTZ_REWARD = 120
PLAYER_KILL_SMOLTZ = 100
LOW_HP_FINISH_THRESHOLD = 30
FARMING_HP_MIN = 25

# Mode switching thresholds (berdasarkan aliveCount)
RUSH_MAX_ALIVE = 50      # Mode RUSH sampai 50 player tersisa
HUNT_MAX_ALIVE = 20      # Mode HUNT dari 50-20 player

# Early game settings
EARLY_GAME_TURNS = 50

# ── Mode-based EP Thresholds ─────────────────────────────────────────
MODE_THRESHOLDS = {
    "RUSH": {"EP_SAFE": 0.40, "EP_COMBAT": 0.15, "HP_FIGHT": 35},
    "HUNT": {"EP_SAFE": 0.50, "EP_COMBAT": 0.30, "HP_FIGHT": 40},
    "SURVIVE": {"EP_SAFE": 0.70, "EP_COMBAT": 0.50, "HP_FIGHT": 50},
}

# ── Weapon stats (PRESERVED from v1.5.2) ─────────────────────────────
WEAPONS = {
    "fist": {"bonus": 0, "range": 0, "priority": 0},
    "dagger": {"bonus": 10, "range": 0, "priority": 10},
    "sword": {"bonus": 20, "range": 0, "priority": 20},
    "katana": {"bonus": 35, "range": 0, "priority": 35},
    "bow": {"bonus": 5, "range": 1, "priority": 5},
    "pistol": {"bonus": 10, "range": 1, "priority": 10},
    "sniper": {"bonus": 28, "range": 2, "priority": 28},
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

# ── Item priority (PRESERVED with SMOLTZ boost) ──────────────────────
ITEM_PRIORITY = {
    "rewards": 1000,       # SMOLTZ - HIGHEST!
    "katana": 900, "sniper": 850, "sword": 800,
    "pistol": 750, "dagger": 700, "bow": 650,
    "medkit": 500, "bandage": 450, "emergency_food": 400,
    "energy_drink": 350, "binoculars": 200, "map": 150,
}

RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,
}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0, "rain": 0.05, "fog": 0.10, "storm": 0.15,
}


# =========================
# 🔥 GLOBAL STATE
# =========================

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_current_mode: str = "RUSH"
_turn_counter: int = 0
_total_smoltz_collected: int = 0
_team_memory: dict = {"teammates": {}, "enemies": {}}
_enemy_wipe_detected: bool = False


# =========================
# 🔥 TEAM DETECTION (v3.0.0)
# =========================

def is_teammate(agent: dict, my_id: str) -> bool:
    """Check if agent is teammate (for team coordination)."""
    if not agent or agent.get("id") == my_id:
        return True
    
    if TEAM_ID and agent.get("team_id") == TEAM_ID:
        return True
    
    agent_name = agent.get("name", "").lower()
    for prefix in TEAM_CONFIG["bot_name_prefixes"]:
        if agent_name.startswith(prefix.lower()):
            return True
    
    if agent.get("isBot", False):
        return True
    
    return False


def is_enemy(agent: dict, my_id: str) -> bool:
    """Check if agent is enemy (non-teammate)."""
    if not agent or agent.get("id") == my_id:
        return False
    if agent.get("isGuardian", False):
        return True
    return not is_teammate(agent, my_id)


# =========================
# 🔥 MODE MANAGEMENT (v3.0.0 - NEW!)
# =========================

def _get_mode(alive_count: int) -> tuple[str, dict]:
    """Determine bot mode based on remaining players."""
    global _current_mode
    
    if alive_count >= RUSH_MAX_ALIVE:
        mode = "RUSH"
        thresholds = MODE_THRESHOLDS["RUSH"]
    elif alive_count >= HUNT_MAX_ALIVE:
        mode = "HUNT"
        thresholds = MODE_THRESHOLDS["HUNT"]
    else:
        mode = "SURVIVE"
        thresholds = MODE_THRESHOLDS["SURVIVE"]
    
    if mode != _current_mode:
        _current_mode = mode
        log.info("🔄 MODE SWITCH: %s (alive=%d)", mode, alive_count)
    
    return mode, thresholds


def _is_easy_kill(enemy: dict, my_atk: int, weapon_bonus: int) -> bool:
    """Check if enemy can be killed in 1-2 hits."""
    hp = enemy.get("hp", 100)
    enemy_def = enemy.get("def", 5)
    damage_per_hit = max(1, my_atk + weapon_bonus - int(enemy_def * 0.5))
    return hp <= damage_per_hit * 2


def _is_dangerous(enemy: dict) -> bool:
    """Check if enemy is dangerous (high ATK)."""
    return enemy.get("atk", 10) > 25


# =========================
# 🔥 SIMULATION ENGINE (PRESERVED)
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


def get_weapon_range(equipped_weapon) -> int:
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)


def get_weapon_priority(equipped_weapon) -> int:
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("priority", 0)


def has_weapon_equipped(equipped) -> bool:
    if not equipped:
        return False
    type_id = equipped.get("typeId", "").lower()
    return type_id != "fist" and WEAPONS.get(type_id, {}).get("bonus", 0) > 0


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
    """Reset per-game tracking state (PRESERVED + extended)."""
    global _known_agents, _map_knowledge, _current_mode, _turn_counter, _total_smoltz_collected
    global _team_memory, _enemy_wipe_detected
    
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _current_mode = "RUSH"
    _turn_counter = 0
    _total_smoltz_collected = 0
    _team_memory = {"teammates": {}, "enemies": {}}
    _enemy_wipe_detected = False
    
    log.info("=" * 60)
    log.info("🤖 ULTIMATE BOT v3.0.0 - Team ID: %s", TEAM_ID or "SOLO")
    log.info("   RUSH (Early) → HUNT (Mid) → SURVIVE (Late)")
    log.info("=" * 60)


def learn_from_map(view: dict):
    """Called after Map is used — learn entire map layout (PRESERVED)."""
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
# 🧠 WEAPON & PICKUP (ENHANCED v3.0.0)
# =========================

def _is_early_game() -> bool:
    global _turn_counter
    return _turn_counter < EARLY_GAME_TURNS


def _has_no_weapon(equipped, inventory: list) -> bool:
    has_weapon_equip = has_weapon_equipped(equipped)
    has_weapon_inv = any(i.get("category") == "weapon" for i in inventory if isinstance(i, dict))
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
        log.info("💰 SMOLTZ PICKUP! +%d sMoltz", amount)
        return {"action": "pickup", "data": {"itemId": best_currency["id"]},
                "reason": f"💰 SMOLTZ: +{amount}!"}
    
    # PRIORITY 2: WEAPON (early game priority)
    weapon_items = [i for i in local_items if i.get("category") == "weapon"]
    
    if weapon_items:
        weapon_items.sort(key=lambda i: WEAPONS.get(i.get("typeId", "").lower(), {}).get("priority", 0), reverse=True)
        best_weapon = weapon_items[0]
        w_type = best_weapon.get("typeId", "unknown")
        w_priority = WEAPONS.get(w_type, {}).get("priority", 0)
        
        current_priority = get_weapon_priority(equipped) if equipped else 0
        
        if not has_weapon or w_priority > current_priority or is_early:
            log.info("⚔️ WEAPON PICKUP: %s", w_type)
            return {"action": "pickup", "data": {"itemId": best_weapon["id"]},
                    "reason": f"WEAPON: {w_type}"}
    
    return None


def _check_equip_best_weapon(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon (ENHANCED)."""
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
        log.info("⚔️ EQUIP: %s (+%d ATK)", best.get('typeId', 'weapon'), best_bonus)
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"⚔️ BEST WEAPON: +{best_bonus} ATK"}
    return None


def _find_healing_item(inventory: list, critical: bool = False, prefer_small: bool = False) -> dict | None:
    """Find best healing item (PRESERVED)."""
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


# =========================
# 🧠 MOVEMENT & NAVIGATION (PRESERVED + ENHANCED)
# =========================

def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find nearest connected region that's NOT a death zone (PRESERVED)."""
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
        return safe_regions[0][0]

    for conn in connections:
        rid = conn if isinstance(conn, str) else conn.get("id", "")
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        if rid and not is_dz:
            return rid
    return None


def _find_low_hp_enemy_region(enemies: list, current_region_id: str) -> str | None:
    """Find region of lowest HP enemy for active hunting (NEW!)."""
    if not enemies:
        return None
    
    low_hp_enemies = [e for e in enemies if e.get("hp", 100) < 40]
    if low_hp_enemies:
        target = min(low_hp_enemies, key=lambda e: e.get("hp", 999))
        target_region = target.get("regionId", "")
        if target_region and target_region != current_region_id:
            return target_region
    return None


def _choose_move_target(connections, danger_ids: set,
                         current_region: dict, visible_items: list,
                         alive_count: int, visible_monsters: list = None,
                         visible_agents: list = None, my_id: str = None) -> str | None:
    """Choose best region to move to (PRESERVED + enemy priority)."""
    if visible_monsters is None:
        visible_monsters = []
    if visible_agents is None:
        visible_agents = []

    # In hunt mode, prioritize moving toward enemies (ENHANCED!)
    if TEAM_ID and my_id:
        for conn in connections:
            rid = _get_region_id(conn)
            if rid and rid not in danger_ids:
                for ag in visible_agents:
                    if isinstance(ag, dict) and ag.get("regionId") == rid:
                        if is_enemy(ag, my_id):
                            return rid

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
    """Calculate move EP cost (PRESERVED)."""
    if terrain == "water":
        return 3
    if weather == "storm":
        return 3
    return 2


def _is_in_range(target: dict, my_region: str, weapon_range: int, connections=None) -> bool:
    """Check if target is in weapon range (PRESERVED)."""
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


# =========================
# 🧠 COMBAT & TARGETING (ENHANCED v3.0.0)
# =========================

def _select_weakest(targets: list) -> dict:
    """Select target with lowest HP (PRESERVED)."""
    return min(targets, key=lambda t: t.get("hp", 999))


def _select_best_kill_target(targets: list, my_atk: int, weapon_bonus: int) -> dict | None:
    """Select best target for quick kill (ENHANCED)."""
    if not targets:
        return None
    alive_targets = [t for t in targets if t.get("hp", 0) > 0]
    if not alive_targets:
        return None
    return min(alive_targets, key=lambda t: t.get("hp", 999))


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    """Estimate enemy's weapon bonus (PRESERVED)."""
    weapon = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def _track_agents(visible_agents: list, my_id: str, my_region: str):
    """Track observed agents (PRESERVED)."""
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
            "isTeammate": is_teammate(agent, my_id) if TEAM_ID else False,
        }
    if len(_known_agents) > 50:
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _track_smoltz_gain(view: dict, my_id: str):
    """Track sMoltz gains (NEW!)."""
    global _total_smoltz_collected
    logs = view.get("recentLogs", [])
    for log_entry in logs:
        if not isinstance(log_entry, dict):
            continue
        msg = log_entry.get("message", "").lower()
        if "killed" in msg and "guardian" in msg:
            _total_smoltz_collected += GUARDIAN_SMOLTZ_REWARD
            log.info("💰 GUARDIAN KILL! +%d sMoltz", GUARDIAN_SMOLTZ_REWARD)
        elif "killed" in msg and "player" in msg:
            _total_smoltz_collected += PLAYER_KILL_SMOLTZ
            log.info("💰 PLAYER KILL! +%d sMoltz", PLAYER_KILL_SMOLTZ)


# =========================
# 🧠 MAIN DECISION ENGINE (v3.0.0 - ULTIMATE)
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine - ULTIMATE v3.0.0
    
    PRIORITY CHAIN (ENHANCED):
    1. DEATHZONE ESCAPE (override everything)
    2. PICKUP SMOLTZ & WEAPON (absolute priority)
    3. EQUIP BEST WEAPON
    4. KILL STEAL (HP < 20) - NEW!
    5. MODE-BASED COMBAT (RUSH/HUNT/SURVIVE) - NEW!
    6. GUARDIAN FARMING
    7. MONSTER FARMING
    8. HEALING & EP MANAGEMENT
    9. MOVEMENT & ACTIVE HUNTING - NEW!
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
    
    # View fields
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
    interactables = region.get("interactables", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""
    
    if not is_alive:
        return None
    
    # ── Determine mode & thresholds (NEW!) ───────────────────────────
    mode, mode_thresholds = _get_mode(alive_count)
    EP_SAFE_THRESHOLD = mode_thresholds["EP_SAFE"]
    EP_COMBAT_MIN = mode_thresholds["EP_COMBAT"]
    HP_FIGHT_THRESHOLD = mode_thresholds["HP_FIGHT"]
    
    is_early = _is_early_game()
    has_weapon = not _has_no_weapon(equipped, inventory)
    
    # ── Build danger map (PRESERVED) ─────────────────────────────────
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
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0
    
    enemies_in_region = [a for a in visible_agents 
                         if a.get("regionId") == region_id 
                         and a.get("isAlive") 
                         and not a.get("isGuardian", False)
                         and (not TEAM_ID or is_enemy(a, my_id))]
    
    # ── Priority 1: DEATHZONE ESCAPE (PRESERVED) ─────────────────────
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 DEATHZONE! Escaping to %s", safe)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}
    
    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("⚠️ Pending DZ! Escaping to %s", safe[:8])
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: Death zone soon"}
    
    # ── Guardian threat evasion (PRESERVED) ─────────────────────────
    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]
    if guardians_here and hp < 40 and ep >= move_ep_cost:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("⚠️ Guardian threat! HP=%d, fleeing", hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"GUARDIAN FLEE: HP={hp}"}
    
    # ── Priority 2: PICKUP SMOLTZ & WEAPON (ENHANCED) ────────────────
    pickup_action = _pickup_smoltz_and_weapon(visible_items, inventory, region_id, 
                                               is_early, has_weapon, equipped)
    if pickup_action:
        return pickup_action
    
    # ── Priority 3: EQUIP BEST WEAPON ────────────────────────────────
    equip_action = _check_equip_best_weapon(inventory, equipped)
    if equip_action:
        return equip_action
    
    if not can_act:
        return None
    
    # ── Priority 4: KILL STEAL (HP < 20) - NEW! ──────────────────────
    all_enemies = [a for a in visible_agents
                   if a.get("isAlive", True)
                   and a.get("id") != my_id
                   and (not TEAM_ID or is_enemy(a, my_id))]
    
    for enemy in all_enemies:
        if enemy.get("hp", 100) < 20:
            w_range = get_weapon_range(equipped)
            if _is_in_range(enemy, region_id, w_range, connections):
                log.info("🔪 KILL STEAL! Enemy HP=%d < 20!", enemy.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": enemy["id"], "targetType": "agent"},
                        "reason": "🔥 KILL STEAL: HP<20!"}
    
    # ── Dangerous enemy avoidance (NEW!) ────────────────────────────
    if mode != "RUSH" and TEAM_ID:
        dangerous_enemies = [e for e in all_enemies if _is_dangerous(e) and e.get("regionId") == region_id]
        if dangerous_enemies:
            safe = _find_safe_region(connections, danger_ids, view)
            if safe:
                log.warning("⚠️ [%s] Fleeing from dangerous enemy!", mode)
                return {"action": "move", "data": {"regionId": safe},
                        "reason": "FLEE: Dangerous enemy"}
    
    # ── Priority 5: MODE-BASED COMBAT (NEW!) ─────────────────────────
    can_fight = ep_ratio >= EP_COMBAT_MIN and hp >= HP_FIGHT_THRESHOLD
    
    # MODE RUSH: Aggressive - attack almost everything
    if mode == "RUSH":
        if all_enemies and can_fight:
            target = _select_best_kill_target(all_enemies, total_atk, 0)
            if target:
                w_range = get_weapon_range(equipped)
                if _is_in_range(target, region_id, w_range, connections):
                    enemy_hp = target.get("hp", 100)
                    my_damage = max(1, total_atk - int(target.get("def", 5) * 0.5))
                    # EXECUTE MODE: attack if can kill or low HP
                    if enemy_hp <= my_damage or enemy_hp < 50:
                        log.info("🔥 [RUSH] ATTACK! Enemy HP=%d", enemy_hp)
                        return {"action": "attack",
                                "data": {"targetId": target["id"], "targetType": "agent"},
                                "reason": f"🔥 RUSH: HP={enemy_hp}"}
    
    # MODE HUNT: Smart killing - only easy kills
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
                        log.info("🐺 [HUNT] Easy kill! Enemy HP=%d", target.get("hp", 0))
                        return {"action": "attack",
                                "data": {"targetId": target["id"], "targetType": "agent"},
                                "reason": f"🐺 HUNT: Easy kill"}
    
    # MODE SURVIVE: Only sure wins
    elif mode == "SURVIVE":
        if all_enemies and can_fight and hp >= 50:
            for enemy in all_enemies:
                if enemy.get("regionId") != region_id:
                    continue
                my_damage = max(1, total_atk - int(enemy.get("def", 5) * 0.5))
                enemy_hp = enemy.get("hp", 100)
                if my_damage >= enemy_hp:
                    w_range = get_weapon_range(equipped)
                    if _is_in_range(enemy, region_id, w_range, connections):
                        log.info("🧬 [SURVIVE] Sure kill! Enemy HP=%d", enemy_hp)
                        return {"action": "attack",
                                "data": {"targetId": enemy["id"], "targetType": "agent"},
                                "reason": f"🧬 SURVIVE: Sure kill"}
        
        # No sure win, flee
        enemies_nearby = [e for e in all_enemies if e.get("regionId") == region_id]
        if enemies_nearby:
            safe = _find_safe_region(connections, danger_ids, view)
            if safe:
                log.warning("🧬 [SURVIVE] No sure win, fleeing!")
                return {"action": "move", "data": {"regionId": safe},
                        "reason": "SURVIVE: Fleeing"}
    
    # ── Priority 6: GUARDIAN FARMING (PRESERVED) ─────────────────────
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and ep >= 2 and hp >= 35:
        target = _select_weakest(guardians)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                target.get("def", 5), region_weather)
            guardian_dmg = calc_damage(target.get("atk", 10),
                                       _estimate_enemy_weapon_bonus(target),
                                       defense, region_weather)
            if my_dmg >= guardian_dmg or target.get("hp", 100) <= my_dmg * 3:
                log.info("💰 GUARDIAN: 120 sMoltz!")
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"💰 GUARDIAN: 120 sMoltz!"}
    
    # ── Priority 7: MONSTER FARMING (PRESERVED) ──────────────────────
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep >= 2 and hp > 20:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER: HP={target.get('hp', '?')}"}
    
    # ── Priority 8: HEALING & EP MANAGEMENT (ENHANCED) ───────────────
    
    # Critical healing
    if hp < 30:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}
    
    # Moderate healing (mode-based threshold)
    heal_threshold = 60 if mode == "SURVIVE" else 50
    if hp < heal_threshold and not enemies_in_region:
        heal = _find_healing_item(inventory, critical=False, prefer_small=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}
    
    # EP Management (mode-based)
    if ep_ratio < EP_SAFE_THRESHOLD:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP: {ep}/{max_ep} -> energy drink"}
        
        if not enemies_in_region and region_id not in danger_ids:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}
    
    # ── Priority 9: MOVEMENT & ACTIVE HUNTING (ENHANCED) ─────────────
    if ep >= move_ep_cost and connections:
        # Active hunting: chase low HP enemies
        if mode in ["RUSH", "HUNT"]:
            hunt_region = _find_low_hp_enemy_region(all_enemies, region_id)
            if hunt_region and hunt_region not in danger_ids:
                log.info("🎯 ACTIVE HUNTING: Moving to low HP enemy")
                return {"action": "move", "data": {"regionId": hunt_region},
                        "reason": "HUNT: Chasing enemy!"}
        
        # Strategic movement
        move_target = _choose_move_target(connections, danger_ids, region,
                                           visible_items, alive_count,
                                           visible_monsters, visible_agents, my_id if TEAM_ID else None)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": f"MOVE: {mode} strategy"}
    
    # ── Priority 10: REST (fallback) ─────────────────────────────────
    if ep < 4 and not enemies_in_region and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep}"}
    
    return None


"""
================================================================================
v3.0.0 - ULTIMATE UPGRADE FROM v1.5.2
================================================================================

CHANGELOG:
----------
v3.0.0 NEW FEATURES:
1. 3 MODE OTAK: RUSH (Early) → HUNT (Mid) → SURVIVE (Late)
2. EP MANAGEMENT: Thresholds based on game mode
3. KILL STEAL: Buru musuh HP < 20
4. ACTIVE HUNTING: Ngejar musuh low HP
5. THREAT MEMORY: Hindari musuh berbahaya (ATK > 25)
6. EARLY GAME WEAPON RUSH: Cari senjata di 50 turn pertama
7. TEAM COORDINATION: Multiple bot support via TEAM_ID
8. EXECUTE MODE: Attack if enemy HP <= my damage
9. SMOLTZ TRACKING: Track total sMoltz collected

PRESERVED FROM v1.5.2:
- Deathzone escape priority
- Guardian threat evasion
- Smart pickup system
- Map learning (learn_from_map)
- Weapon auto-equip
- Weather penalty
- Facility interaction
- Healing strategy

EXPORTED FUNCTIONS:
- decide_action() - Main decision engine
- reset_game_state() - Reset per-game tracking
- learn_from_map() - Learn map layout
================================================================================
"""
