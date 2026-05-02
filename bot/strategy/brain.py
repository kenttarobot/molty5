"""
Strategy brain — BERSERKER MODE v3.2 (FINAL FIX - NO MORE FACILITY LOOP)
===========================================================================
PERBAIKAN DARI v3.1:
- CRITICAL FIX: Broadcast station hanya bisa di-interact SEKALI sepanjang game
- CRITICAL FIX: Facility cooldown sekarang berfungsi dengan benar
- FIX: Bot tidak akan stuck loop di facility manapun
- IMPROVED: Damage comparison lebih akurat
- IMPROVED: Survival logic ditingkatkan
"""

import time
from bot.utils.logger import get_logger

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  KONFIGURASI SENJATA
# ═══════════════════════════════════════════════════════════════════

WEAPONS = {
    "fist":   {"bonus": 0,  "range": 0, "tier": 0},
    "dagger": {"bonus": 10, "range": 0, "tier": 1},
    "bow":    {"bonus": 5,  "range": 1, "tier": 1},
    "pistol": {"bonus": 10, "range": 1, "tier": 2},
    "sword":  {"bonus": 20, "range": 0, "tier": 3},
    "sniper": {"bonus": 28, "range": 2, "tier": 4},
    "katana": {"bonus": 35, "range": 0, "tier": 5},
}

WEAPON_TIER = {w: d["tier"] for w, d in WEAPONS.items()}
WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

ITEM_PRIORITY = {
    "rewards": 300,
    "katana": 100, "sniper": 95, "sword": 90, "pistol": 85,
    "dagger": 80, "bow": 75,
    "medkit": 70, "bandage": 65, "emergency_food": 60, "energy_drink": 58,
    "binoculars": 55, "map": 52, "megaphone": 40,
}

RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20, "energy_drink": 0,
}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0, "rain": 0.05, "fog": 0.10, "storm": 0.15,
}


# ═══════════════════════════════════════════════════════════════════
#  KONFIGURASI BERSERKER v3.2
# ═══════════════════════════════════════════════════════════════════

BERSERKER_CONFIG = {
    # ── HP & EP Management ──────────────────────────────────────────
    "HP_MINIMUM":            50,
    "HP_CRITICAL":           30,
    "HP_HEAL_URGENT":        50,
    "HP_HEAL_MODERATE":      65,
    "EP_MINIMUM_RATIO":      0.60,
    "EP_ATTACK_MIN_RATIO":   0.30,
    "EP_SAFE_RATIO":         0.20,

    # ── Combat & Survival ───────────────────────────────────────────
    "MIN_HP_TO_ATTACK":      45,
    "MIN_HP_TO_ATTACK_GUARDIAN": 70,
    "COUNTER_ATTACK_HP":     35,
    "NEVER_FLEE_IF_ATTACKED": True,
    
    # ── Damage Comparison ───────────────────────────────────────────
    "MAX_ENEMY_DAMAGE_RATIO": 1.5,
    "DANGEROUS_ENEMY_DAMAGE": 25,
    "FLEE_STRONG_ENEMY_RATIO": 1.5,
    
    # ── Pursuit ─────────────────────────────────────────────────────
    "PURSUIT_ENABLED":       True,
    "PURSUIT_MAX_HOPS":      2,
    "PURSUIT_MIN_HP":        50,

    # ── Recovery Mode ───────────────────────────────────────────────
    "RECOVERY_HP_THRESHOLD": 35,
    "RECOVERY_TARGET_HP":    75,
    "RECOVERY_FARM_GUARDIAN": True,
    "RECOVERY_FARM_GUARDIAN_MIN_HP": 55,

    # ── Hunting ─────────────────────────────────────────────────────
    "HUNTING_MODE":          True,
    "HUNT_UNTIL_DEATH":      True,
    "TARGET_MARK_DURATION":  15,
    "EXECUTE_HP_THRESHOLD":  30,
    "WOUNDED_HP_THRESHOLD":  50,

    # ── Enemy Profiling ─────────────────────────────────────────────
    "PROFILE_MEMORY_SIZE":   100,
    "PROFILE_HISTORY_LEN":   20,

    # ── Inventory Management ────────────────────────────────────────
    "INV_MAX_CAPACITY":      10,
    "INV_DROP_THRESHOLD":    9,

    # ── Facility (FIXED!) ───────────────────────────────────────────
    "MAX_FACILITY_INTERACTIONS": 1,
    "FACILITY_COOLDOWN_TURNS":   10,
    "BROADCAST_STATION_ONCE":    True,   # Broadcast station ONLY ONCE per game

    # ── Flee ─────────────────────────────────────────────────────────
    "FLEE_HP":               15,
    "FLEE_OUTNUMBERED":      4,
}


# ═══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_hunting_target: dict = None
_hunting_timer: int = 0
_interacted_facilities: dict = {}  # {facility_id: turn} atau {"broadcast_station": turn}
_recovery_mode: bool = False
_last_attacked_by: str = None
_last_attacked_turn: int = 0
_broadcast_used: bool = False  # Flag khusus untuk broadcast station

# Enemy profiles
_enemy_profiles: dict = {}


def reset_game_state():
    global _known_agents, _map_knowledge, _hunting_target, _hunting_timer
    global _interacted_facilities, _recovery_mode, _last_attacked_by, _last_attacked_turn
    global _broadcast_used
    
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _hunting_target = None
    _hunting_timer = 0
    _interacted_facilities = {}
    _recovery_mode = False
    _last_attacked_by = None
    _last_attacked_turn = 0
    _broadcast_used = False
    
    log.info("=" * 65)
    log.info("  BERSERKER BRAIN v3.2 — NO MORE FACILITY LOOP!")
    log.info("  Enemy profiles loaded: %d", len(_enemy_profiles))
    log.info("=" * 65)


# ═══════════════════════════════════════════════════════════════════
#  ENEMY PROFILING SYSTEM
# ═══════════════════════════════════════════════════════════════════

def _get_or_create_profile(enemy_id: str) -> dict:
    global _enemy_profiles
    if enemy_id not in _enemy_profiles:
        _enemy_profiles[enemy_id] = {
            "id": enemy_id,
            "first_seen": int(time.time()),
            "last_seen": int(time.time()),
            "encounters": 0,
            "kills_on_me": 0,
            "times_we_killed": 0,
            "hp_samples": [],
            "atk_samples": [],
            "weapon_history": [],
            "preferred_weapon": "fist",
            "behavior_tags": set(),
            "last_hp": 100,
            "last_atk": 10,
            "last_weapon": "fist",
            "known_weakness": None,
            "win_rate_vs": 0.5,
            "interaction_log": [],
        }
        if len(_enemy_profiles) > BERSERKER_CONFIG["PROFILE_MEMORY_SIZE"]:
            oldest = sorted(_enemy_profiles.keys(),
                            key=lambda k: _enemy_profiles[k]["last_seen"])
            del _enemy_profiles[oldest[0]]
    return _enemy_profiles[enemy_id]


def update_enemy_profile(enemy: dict, current_turn: int, event: str = "seen"):
    eid = enemy.get("id", "")
    if not eid:
        return
    profile = _get_or_create_profile(eid)
    profile["last_seen"] = int(time.time())
    profile["encounters"] += 1
    hp = enemy.get("hp", 100)
    atk = enemy.get("atk", 10)
    weapon = enemy.get("equippedWeapon")
    weapon_type = weapon.get("typeId", "fist").lower() if isinstance(weapon, dict) else "fist"
    profile["last_hp"] = hp
    profile["last_atk"] = atk
    profile["last_weapon"] = weapon_type
    profile["hp_samples"].append(hp)
    profile["atk_samples"].append(atk)
    if len(profile["hp_samples"]) > BERSERKER_CONFIG["PROFILE_HISTORY_LEN"]:
        profile["hp_samples"].pop(0)
        profile["atk_samples"].pop(0)
    profile["weapon_history"].append(weapon_type)
    if len(profile["weapon_history"]) > BERSERKER_CONFIG["PROFILE_HISTORY_LEN"]:
        profile["weapon_history"].pop(0)
    if profile["weapon_history"]:
        profile["preferred_weapon"] = max(set(profile["weapon_history"]),
                                          key=profile["weapon_history"].count)
    _analyze_behavior(profile, enemy, event)
    _detect_weakness(profile)
    profile["interaction_log"].append({
        "turn": current_turn,
        "event": event,
        "enemy_hp": hp,
        "weapon": weapon_type,
    })
    if len(profile["interaction_log"]) > BERSERKER_CONFIG["PROFILE_HISTORY_LEN"]:
        profile["interaction_log"].pop(0)


def _analyze_behavior(profile: dict, enemy: dict, event: str):
    tags = profile["behavior_tags"]
    hp = enemy.get("hp", 100)
    weapon_type = profile["last_weapon"]
    if event == "attacked_us" and hp < 40:
        tags.add("aggressive")
    hp_samples = profile["hp_samples"]
    if len(hp_samples) >= 5:
        avg_hp = sum(hp_samples) / len(hp_samples)
        if avg_hp > 70:
            tags.add("healer")
        if avg_hp < 40:
            tags.add("glass_cannon")
    if weapon_type in ["sniper", "bow", "pistol"]:
        tags.add("ranged")
    elif weapon_type in ["katana", "sword", "dagger"]:
        tags.add("melee")
    if event == "disappeared_low_hp":
        tags.add("runner")


def _detect_weakness(profile: dict):
    tags = profile["behavior_tags"]
    if "ranged" in tags:
        profile["known_weakness"] = "rush_melee"
    elif "melee" in tags and "ranged" not in tags:
        profile["known_weakness"] = "kite_ranged"
    elif "healer" in tags:
        profile["known_weakness"] = "burst_no_pause"
    elif "glass_cannon" in tags:
        profile["known_weakness"] = "outlast"
    elif "runner" in tags:
        profile["known_weakness"] = "pursuit"
    else:
        profile["known_weakness"] = "standard"


def on_killed_enemy(enemy_id: str):
    if enemy_id in _enemy_profiles:
        _enemy_profiles[enemy_id]["times_we_killed"] += 1
        wins = _enemy_profiles[enemy_id]["times_we_killed"]
        losses = _enemy_profiles[enemy_id]["kills_on_me"]
        total = wins + losses
        _enemy_profiles[enemy_id]["win_rate_vs"] = wins / total if total > 0 else 0.5
        log.info("PROFILE: Killed %s | Win rate: %.0f%%",
                 enemy_id[:8], _enemy_profiles[enemy_id]["win_rate_vs"] * 100)


def on_killed_by_enemy(enemy_id: str):
    profile = _get_or_create_profile(enemy_id)
    profile["kills_on_me"] += 1
    wins = profile["times_we_killed"]
    losses = profile["kills_on_me"]
    total = wins + losses
    profile["win_rate_vs"] = wins / total if total > 0 else 0.0
    log.warning("PROFILE: Killed by %s | Win rate: %.0f%%",
                enemy_id[:8], profile["win_rate_vs"] * 100)


def get_strategy_vs(enemy_id: str) -> str:
    if enemy_id not in _enemy_profiles:
        return "standard"
    profile = _enemy_profiles[enemy_id]
    weakness = profile.get("known_weakness", "standard")
    win_rate = profile.get("win_rate_vs", 0.5)
    if win_rate < 0.3:
        return f"careful_{weakness}"
    elif win_rate > 0.7:
        return f"aggressive_{weakness}"
    return weakness


# ═══════════════════════════════════════════════════════════════════
#  INVENTORY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def _get_item_value(item: dict) -> int:
    if not isinstance(item, dict):
        return 0
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()
    if type_id == "rewards" or category == "currency":
        return 1000
    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        return 100 + bonus
    if type_id in RECOVERY_ITEMS:
        return ITEM_PRIORITY.get(type_id, 30)
    return ITEM_PRIORITY.get(type_id, 5)


def _find_worst_item(inventory: list, exclude_equipped_id: str = None) -> dict | None:
    if not inventory:
        return None
    heal_count = sum(1 for i in inventory
                     if isinstance(i, dict) and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)
    best_weapon_bonus = 0
    best_weapon_id = None
    for item in inventory:
        if isinstance(item, dict) and item.get("category") == "weapon":
            bonus = WEAPONS.get(item.get("typeId", "").lower(), {}).get("bonus", 0)
            if bonus > best_weapon_bonus:
                best_weapon_bonus = bonus
                best_weapon_id = item.get("id")
    candidates = []
    for item in inventory:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id", "")
        type_id = item.get("typeId", "").lower()
        if item_id == exclude_equipped_id:
            continue
        if item_id == best_weapon_id:
            continue
        if type_id == "rewards" or item.get("category") == "currency":
            continue
        if type_id == "medkit" and heal_count <= 1:
            continue
        value = _get_item_value(item)
        candidates.append((item, value))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def _smart_pickup(items: list, inventory: list, region_id: str, equipped) -> dict | None:
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None
    inv_size = len(inventory)
    heal_count = sum(1 for i in inventory if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)
    scored = [(i, _pickup_score_v3(i, inventory, heal_count)) for i in local_items]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_item, best_score = scored[0]
    if best_score <= 0:
        return None
    if inv_size < BERSERKER_CONFIG["INV_MAX_CAPACITY"]:
        type_id = best_item.get("typeId", "item")
        log.info("PICKUP: %s (score=%d)", type_id, best_score)
        return {"action": "pickup", "data": {"itemId": best_item["id"]},
                "reason": f"PICKUP: {type_id}"}
    equipped_id = equipped.get("id") if isinstance(equipped, dict) else None
    worst = _find_worst_item(inventory, exclude_equipped_id=equipped_id)
    if worst:
        worst_value = _get_item_value(worst)
        if best_score > worst_value + 10:
            log.info("SWAP: Drop %s for %s",
                     worst.get("typeId", "?"), best_item.get("typeId", "?"))
            return {"action": "drop_item", "data": {"itemId": worst["id"]},
                    "reason": f"SWAP: Drop {worst.get('typeId','?')}"}
    return None


def _pickup_score_v3(item: dict, inventory: list, heal_count: int) -> int:
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()
    if type_id == "rewards" or category == "currency":
        return 300
    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        current_best = max(
            (WEAPONS.get(i.get("typeId", "").lower(), {}).get("bonus", 0)
             for i in inventory if isinstance(i, dict) and i.get("category") == "weapon"),
            default=0
        )
        return (100 + bonus) if bonus > current_best else 0
    if type_id == "binoculars":
        has = any(isinstance(i, dict) and i.get("typeId", "").lower() == "binoculars"
                  for i in inventory)
        return 55 if not has else 0
    if type_id == "map":
        return 52
    if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS.get(type_id, 0) > 0:
        return ITEM_PRIORITY.get(type_id, 0) + (10 if heal_count < 4 else 0)
    if type_id == "energy_drink":
        return 58
    return ITEM_PRIORITY.get(type_id, 0)


# ═══════════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

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
    log.info("MAP: %d death zones learned", len(_map_knowledge["death_zones"]))


def calc_damage(atk: int, weapon_bonus: int, target_def: int, weather: str = "clear") -> int:
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


def _select_weakest(targets: list) -> dict:
    return min(targets, key=lambda t: t.get("hp", 999))


def _is_in_range(target: dict, my_region: str, weapon_range: int, connections=None) -> bool:
    target_region = target.get("regionId", "")
    if not target_region or target_region == my_region:
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


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
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


def _find_healing_item(inventory: list, critical: bool = False) -> dict | None:
    heals = [i for i in inventory if isinstance(i, dict)
             and i.get("typeId", "").lower() in RECOVERY_ITEMS
             and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0]
    if not heals:
        return None
    heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0),
               reverse=critical)
    return heals[0]


def _find_energy_drink(inventory: list) -> dict | None:
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _check_equip(inventory: list, equipped) -> dict | None:
    current_bonus = get_weapon_bonus(equipped) if equipped else 0
    best, best_bonus = None, current_bonus
    for item in inventory:
        if not isinstance(item, dict) or item.get("category") != "weapon":
            continue
        bonus = WEAPONS.get(item.get("typeId", "").lower(), {}).get("bonus", 0)
        if bonus > best_bonus:
            best, best_bonus = item, bonus
    if best:
        log.info("EQUIP: %s (+%d ATK)", best.get("typeId", "weapon"), best_bonus)
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId','weapon')}"}
    return None


def _use_utility_item(inventory: list) -> dict | None:
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("typeId", "").lower() == "map":
            log.info("Using Map!")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Map"}
    return None


# ═══════════════════════════════════════════════════════════════════
#  FACILITY SELECTION — FIXED! No more infinite loop!
# ═══════════════════════════════════════════════════════════════════

def _select_facility_with_limit(interactables: list, hp: int, ep: int, current_turn: int) -> dict | None:
    """
    Pilih facility dengan batasan KETAT:
    - Broadcast station: HANYA SEKALI sepanjang game!
    - Facility lain: cooldown 10 turn
    - Medical facility: hanya jika HP < 70
    """
    global _interacted_facilities, _broadcast_used
    
    if not interactables:
        return None
    
    cooldown = BERSERKER_CONFIG["FACILITY_COOLDOWN_TURNS"]
    
    # Bersihkan facility expired (kecuali broadcast station yang sudah dipakai)
    expired = []
    for fid, turn in _interacted_facilities.items():
        if fid == "broadcast_station":
            continue  # Jangan hapus broadcast station dari tracking
        if current_turn - turn > cooldown:
            expired.append(fid)
    
    for fid in expired:
        del _interacted_facilities[fid]
        log.debug("Facility %s cooldown expired", fid)
    
    for fac in interactables:
        if not isinstance(fac, dict):
            continue
        
        # SKIP jika facility sudah digunakan (untuk tipe yang support isUsed)
        if fac.get("isUsed"):
            log.debug("Facility already used: isUsed=True")
            continue
        
        fid = fac.get("id", "")
        ftype = fac.get("type", "").lower()
        
        # ═══════════════════════════════════════════════════════════
        # BROADCAST STATION — ONLY ONCE PER GAME!
        # ═══════════════════════════════════════════════════════════
        if ftype == "broadcast_station":
            if _broadcast_used:
                log.debug("Broadcast station already used this game, skipping")
                continue
            if "broadcast_station" in _interacted_facilities:
                log.debug("Broadcast station already used (tracked), skipping")
                continue
            log.info("Broadcast station available (first time this game)")
            return fac
        
        # ═══════════════════════════════════════════════════════════
        # OTHER FACILITIES — with cooldown
        # ═══════════════════════════════════════════════════════════
        if fid in _interacted_facilities:
            last_used = _interacted_facilities[fid]
            turns_ago = current_turn - last_used
            log.debug("Facility %s on cooldown (used %d turns ago)", ftype, turns_ago)
            continue
        
        # Medical facility: hanya jika HP rendah
        if ftype == "medical_facility" and hp < 70:
            return fac
        
        # Supply cache / watchtower
        if ftype in ["supply_cache", "watchtower"]:
            return fac
    
    return None


def _mark_facility_used(facility: dict, current_turn: int):
    """Track bahwa facility sudah digunakan"""
    global _interacted_facilities, _broadcast_used
    
    if not isinstance(facility, dict):
        return
    
    ftype = facility.get("type", "").lower()
    fid = facility.get("id", "")
    
    if ftype == "broadcast_station":
        _broadcast_used = True
        _interacted_facilities["broadcast_station"] = current_turn
        log.info("Broadcast station marked as USED (will not be used again this game)")
    else:
        _interacted_facilities[fid] = current_turn
        log.info("Facility %s marked as used at turn %d", ftype, current_turn)


def _track_agents(visible_agents: list, my_id: str, my_region: str, current_turn: int):
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
            "regionId": agent.get("regionId", my_region),
        }
        if not agent.get("isGuardian", False):
            update_enemy_profile(agent, current_turn, event="seen")
    if len(_known_agents) > 50:
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _choose_move_target(connections, danger_ids: set, current_region: dict,
                        visible_items: list, alive_count: int) -> str | None:
    candidates = []
    item_regions = {i.get("regionId", "") for i in visible_items if isinstance(i, dict)}
    for conn in connections:
        if isinstance(conn, str):
            if conn in danger_ids:
                continue
            score = 1 + (5 if conn in item_regions else 0)
            candidates.append((conn, score))
        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            if not rid or conn.get("isDeathZone") or rid in danger_ids:
                continue
            score = 0
            terrain = conn.get("terrain", "").lower()
            score += {"hills": 4, "plains": 2, "ruins": 2, "forest": 1, "water": -3}.get(terrain, 0)
            if rid in item_regions:
                score += 5
            facs = conn.get("interactables", [])
            score += len([f for f in facs if isinstance(f, dict) and not f.get("isUsed")]) * 2
            weather = conn.get("weather", "").lower()
            score += {"storm": -2, "fog": -1, "rain": 0, "clear": 1}.get(weather, 0)
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


# ═══════════════════════════════════════════════════════════════════
#  TARGET SELECTION
# ═══════════════════════════════════════════════════════════════════

def select_target_with_priority(enemies: list, strategy: str = "standard") -> dict | None:
    if not enemies:
        return None
    global _hunting_target
    if BERSERKER_CONFIG["HUNTING_MODE"] and _hunting_target:
        for enemy in enemies:
            if enemy.get("id") == _hunting_target.get("id"):
                _hunting_target = enemy
                return _hunting_target
        _hunting_target = None
    execute_targets = [e for e in enemies if e.get("hp", 100) < BERSERKER_CONFIG["EXECUTE_HP_THRESHOLD"]]
    if execute_targets:
        return min(execute_targets, key=lambda e: e.get("hp", 999))
    wounded = [e for e in enemies if e.get("hp", 100) < BERSERKER_CONFIG["WOUNDED_HP_THRESHOLD"]]
    if wounded:
        return min(wounded, key=lambda e: e.get("hp", 999))
    if strategy in ("rush_melee", "aggressive_rush_melee"):
        ranged_enemies = [e for e in enemies
                          if e.get("equippedWeapon", {}) and
                          e.get("equippedWeapon", {}).get("typeId", "").lower() in ["sniper", "bow", "pistol"]]
        if ranged_enemies:
            return min(ranged_enemies, key=lambda e: e.get("hp", 999))
    return _select_weakest(enemies)


def update_hunting_target(target: dict):
    global _hunting_target, _hunting_timer
    if target and BERSERKER_CONFIG["HUNTING_MODE"]:
        _hunting_target = target
        _hunting_timer = BERSERKER_CONFIG["TARGET_MARK_DURATION"]
        log.info("NEW HUNT TARGET: %s HP=%d", target.get("id", "?")[:8], target.get("hp", 0))


# ═══════════════════════════════════════════════════════════════════
#  RECOVERY MODE
# ═══════════════════════════════════════════════════════════════════

def _handle_recovery_mode(my, inventory, visible_agents, region_id, connections,
                           danger_ids, equipped, region_weather, ep, ep_ratio,
                           move_ep_cost, monsters) -> dict | None:
    hp = my.get("hp", 100)
    log.info("RECOVERY MODE: HP=%d (target %d)", hp, BERSERKER_CONFIG["RECOVERY_TARGET_HP"])
    heal = _find_healing_item(inventory, critical=True)
    if heal:
        log.info("RECOVERY HEAL: %s", heal.get("typeId", "heal"))
        return {"action": "use_item", "data": {"itemId": heal["id"]},
                "reason": f"RECOVERY HEAL: HP={hp}"}
    energy_drink = _find_energy_drink(inventory)
    if energy_drink and ep_ratio < 0.5:
        return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                "reason": f"RECOVERY EP: {ep}"}
    if BERSERKER_CONFIG["RECOVERY_FARM_GUARDIAN"] and hp >= BERSERKER_CONFIG["RECOVERY_FARM_GUARDIAN_MIN_HP"]:
        guardians = [a for a in visible_agents
                     if a.get("isGuardian", False) and a.get("isAlive", True)
                     and a.get("regionId") == region_id]
        if guardians and ep >= 2:
            target = _select_weakest(guardians)
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("RECOVERY FARM: Guardian HP=%d", target.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "RECOVERY: Farm guardian"}
    if monsters:
        weak_monsters = [m for m in monsters if m.get("hp", 0) > 0 and m.get("hp", 100) < 40]
        if weak_monsters and ep >= 1:
            target = _select_weakest(weak_monsters)
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("RECOVERY FARM: Monster HP=%d", target.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": "RECOVERY: Farm monster"}
    if not danger_ids or region_id not in danger_ids:
        log.info("RECOVERY REST: HP=%d EP=%d", hp, ep)
        return {"action": "rest", "data": {}, "reason": f"RECOVERY REST: HP={hp}"}
    return None


# ═══════════════════════════════════════════════════════════════════
#  MAIN DECISION ENGINE
# ═══════════════════════════════════════════════════════════════════

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    global _hunting_target, _hunting_timer, _interacted_facilities
    global _recovery_mode, _last_attacked_by, _last_attacked_turn, _broadcast_used

    self_data   = view.get("self", {})
    region      = view.get("currentRegion", {})
    hp          = self_data.get("hp", 100)
    ep          = self_data.get("ep", 10)
    max_ep      = self_data.get("maxEp", 10)
    atk         = self_data.get("atk", 10)
    defense     = self_data.get("def", 5)
    is_alive    = self_data.get("isAlive", True)
    inventory   = self_data.get("inventory", [])
    equipped    = self_data.get("equippedWeapon")
    my_id       = self_data.get("id", "")

    visible_agents   = view.get("visibleAgents", [])
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
    pending_dz        = view.get("pendingDeathzones", [])
    alive_count       = view.get("aliveCount", 100)
    current_turn      = view.get("turn", 0) or int(time.time())

    connections       = connected_regions or region.get("connections", [])
    interactables     = region.get("interactables", [])
    region_id         = region.get("id", "")
    region_terrain    = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather    = region.get("weather", "").lower() if isinstance(region, dict) else ""

    if not is_alive:
        return None

    if _hunting_timer > 0:
        _hunting_timer -= 1
    elif _hunting_target:
        _hunting_target = None

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

    _track_agents(visible_agents, my_id, region_id, current_turn)
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)
    ep_ratio     = ep / max_ep if max_ep > 0 else 1.0

    enemies_here = [a for a in visible_agents
                    if a.get("isAlive", True) and a.get("id") != my_id
                    and a.get("regionId") == region_id
                    and not a.get("isGuardian", False)]

    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]

    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]

    just_attacked = (current_turn - _last_attacked_turn) <= 2 and _last_attacked_by

    # Hitung my damage
    my_damage = calc_damage(atk, get_weapon_bonus(equipped), 5, region_weather)
    
    # Hitung strongest enemy damage
    strongest_enemy_damage = max(
        (calc_damage(e.get("atk", 10), _estimate_enemy_weapon_bonus(e), defense, region_weather)
         for e in enemies_here),
        default=0
    )
    
    has_guardian = len(guardians_here) > 0

    # ═══════════════════════════════════════════════════════════════
    # [P1] DEATHZONE ESCAPE
    # ═══════════════════════════════════════════════════════════════
    if region.get("isDeathZone", False) or region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("DEATHZONE ESCAPE! -> %s", safe)
            return {"action": "move", "data": {"regionId": safe}, "reason": "DEATHZONE ESCAPE"}

    # ═══════════════════════════════════════════════════════════════
    # [P2] UPDATE RECOVERY MODE
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["RECOVERY_HP_THRESHOLD"] and not just_attacked:
        _recovery_mode = True
    elif hp >= BERSERKER_CONFIG["RECOVERY_TARGET_HP"]:
        if _recovery_mode:
            log.info("RECOVERY COMPLETE! HP=%d", hp)
        _recovery_mode = False

    # ═══════════════════════════════════════════════════════════════
    # [P3] CRITICAL HEAL (EMERGENCY)
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HP_CRITICAL"]:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            log.warning("CRITICAL HEAL! HP=%d -> %s", hp, heal.get("typeId", "heal"))
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════
    # [P4] RECOVERY MODE (jika tidak ada musuh)
    # ═══════════════════════════════════════════════════════════════
    if _recovery_mode and not enemies_here:
        result = _handle_recovery_mode(
            self_data, inventory, visible_agents, region_id,
            connections, danger_ids, equipped, region_weather,
            ep, ep_ratio, move_ep_cost, monsters
        )
        if result:
            return result

    # ═══════════════════════════════════════════════════════════════
    # [P5] FLEE LOGIC (jika musuh terlalu kuat)
    # ═══════════════════════════════════════════════════════════════
    should_flee = False
    flee_reason = ""

    if not just_attacked:
        if hp < BERSERKER_CONFIG["FLEE_HP"]:
            should_flee = True
            flee_reason = f"HP_CRITICAL: {hp}"
        elif enemies_here and strongest_enemy_damage > my_damage * BERSERKER_CONFIG["FLEE_STRONG_ENEMY_RATIO"]:
            should_flee = True
            flee_reason = f"ENEMY_STRONGER: their_dmg={strongest_enemy_damage} my_dmg={my_damage}"
        elif has_guardian and hp < BERSERKER_CONFIG["MIN_HP_TO_ATTACK_GUARDIAN"]:
            should_flee = True
            flee_reason = f"GUARDIAN_HP_TOO_LOW: hp={hp}"
        elif len(enemies_here) >= BERSERKER_CONFIG["FLEE_OUTNUMBERED"] and hp < 50:
            should_flee = True
            flee_reason = f"OUTNUMBERED: {len(enemies_here)}"

    if should_flee:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("FLEEING! %s -> %s", flee_reason, safe)
            return {"action": "move", "data": {"regionId": safe}, "reason": f"FLEE: {flee_reason}"}

    # ═══════════════════════════════════════════════════════════════
    # [P6] COUNTER ATTACK
    # ═══════════════════════════════════════════════════════════════
    if just_attacked and _last_attacked_by and hp >= BERSERKER_CONFIG["COUNTER_ATTACK_HP"]:
        attacker = next((e for e in enemies_here if e.get("id") == _last_attacked_by), None)
        if attacker:
            strategy = get_strategy_vs(_last_attacked_by)
            update_enemy_profile(attacker, current_turn, event="attacked_us")
            log.warning("COUNTER ATTACK! vs %s (hp=%d) MyHP=%d", 
                       _last_attacked_by[:8], attacker.get("hp", 0), hp)
            return {"action": "attack",
                    "data": {"targetId": attacker["id"], "targetType": "agent"},
                    "reason": f"COUNTER: vs {_last_attacked_by[:8]}"}

    # ═══════════════════════════════════════════════════════════════
    # [P7] HEAL SEBELUM FIGHT
    # ═══════════════════════════════════════════════════════════════
    if enemies_here and strongest_enemy_damage > 20 and hp < 55:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            log.info("PRE-FIGHT HEAL: HP=%d, enemy_dmg=%d", hp, strongest_enemy_damage)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"PRE-FIGHT HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════
    # [P8] EQUIP SENJATA TERBAIK
    # ═══════════════════════════════════════════════════════════════
    equip_action = _check_equip(inventory, equipped)
    if equip_action:
        return equip_action

    # ═══════════════════════════════════════════════════════════════
    # [P9] COMBAT — SERANG MUSUH
    # ═══════════════════════════════════════════════════════════════
    can_attack = (hp >= BERSERKER_CONFIG["MIN_HP_TO_ATTACK"]
                  and ep_ratio >= BERSERKER_CONFIG["EP_ATTACK_MIN_RATIO"])

    if has_guardian and hp < BERSERKER_CONFIG["MIN_HP_TO_ATTACK_GUARDIAN"]:
        can_attack = False

    if enemies_here and strongest_enemy_damage > my_damage * BERSERKER_CONFIG["MAX_ENEMY_DAMAGE_RATIO"]:
        can_attack = False

    if _hunting_target and hp >= 40:
        can_attack = True

    if enemies_here and can_attack:
        primary_target_id = (enemies_here[0].get("id", "") if not _hunting_target
                             else _hunting_target.get("id", ""))
        strategy = get_strategy_vs(primary_target_id)
        target = select_target_with_priority(enemies_here, strategy)

        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                enemy_hp = target.get("hp", 100)
                
                if not _hunting_target:
                    update_hunting_target(target)
                
                log.info("ATTACK! Target %s HP=%d MyDMG=%d MyHP=%d EnemyDMG=%d",
                         target.get("id", "?")[:8], enemy_hp, my_damage, hp, 
                         strongest_enemy_damage)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"ATTACK: target_hp={enemy_hp}"}

    # ═══════════════════════════════════════════════════════════════
    # [P10] GUARDIAN FARMING (dengan syarat ketat)
    # ═══════════════════════════════════════════════════════════════
    guardians_all = [a for a in visible_agents
                     if a.get("isGuardian", False) and a.get("isAlive", True)]
    
    guardian_farm_ok = (hp >= BERSERKER_CONFIG["MIN_HP_TO_ATTACK_GUARDIAN"] 
                        and ep >= 2 
                        and my_damage >= 15
                        and not _hunting_target)
    
    if guardians_all and guardian_farm_ok:
        target = _select_weakest(guardians_all)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            log.info("GUARDIAN FARM: HP=%d MyDMG=%d", target.get("hp", 0), my_damage)
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "agent"},
                    "reason": "GUARDIAN FARM"}

    # ═══════════════════════════════════════════════════════════════
    # [P11] SMART PICKUP / ITEM SWAP
    # ═══════════════════════════════════════════════════════════════
    if not enemies_here:
        pickup_action = _smart_pickup(visible_items, inventory, region_id, equipped)
        if pickup_action:
            return pickup_action

    util_action = _use_utility_item(inventory)
    if util_action:
        return util_action

    if not can_act:
        return None

    # ═══════════════════════════════════════════════════════════════
    # [P12] FACILITY INTERACTION — FIXED!
    # ═══════════════════════════════════════════════════════════════
    if not enemies_here and not guardians_here:
        facility = _select_facility_with_limit(interactables, hp, ep, current_turn)
        if facility:
            # MARK AS USED so it won't be used again (especially broadcast station!)
            _mark_facility_used(facility, current_turn)
            log.info("FACILITY INTERACT: %s", facility.get("type", "?"))
            return {"action": "interact", "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type','?')}"}

    # ═══════════════════════════════════════════════════════════════
    # [P13] HEAL OPPORTUNISTIK
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HP_HEAL_URGENT"] and not enemies_here and not _hunting_target:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            log.info("OPPORTUNISTIC HEAL: HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════
    # [P14] EP RECOVERY
    # ═══════════════════════════════════════════════════════════════
    if ep_ratio < BERSERKER_CONFIG["EP_MINIMUM_RATIO"]:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP DRINK: {ep}/{max_ep}"}
        if not enemies_here and region_id not in danger_ids and not _hunting_target:
            log.info("REST: EP=%d/%d (%.0f%%)", ep, max_ep, ep_ratio * 100)
            return {"action": "rest", "data": {}, "reason": f"REST: EP={ep}/{max_ep}"}

    # ═══════════════════════════════════════════════════════════════
    # [P15] MONSTER FARMING
    # ═══════════════════════════════════════════════════════════════
    if monsters and ep >= 1 and hp > 50 and not enemies_here and not _hunting_target:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER FARM: HP={target.get('hp','?')}"}

    # ═══════════════════════════════════════════════════════════════
    # [P16] MOVEMENT
    # ═══════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        if _hunting_target and BERSERKER_CONFIG["PURSUIT_ENABLED"] and hp >= BERSERKER_CONFIG["PURSUIT_MIN_HP"]:
            target_region = _hunting_target.get("regionId", "")
            if target_region and target_region != region_id and target_region not in danger_ids:
                log.info("PURSUIT: Chase %s to %s", _hunting_target.get("id", "?")[:8], target_region)
                return {"action": "move", "data": {"regionId": target_region},
                        "reason": "PURSUIT: Chase target"}

        if _last_attacked_by and hp >= BERSERKER_CONFIG["PURSUIT_MIN_HP"]:
            last_attacker = _known_agents.get(_last_attacked_by, {})
            attacker_region = last_attacker.get("regionId", "")
            if attacker_region and attacker_region != region_id and attacker_region not in danger_ids:
                log.info("REVENGE: Chase %s", _last_attacked_by[:8])
                return {"action": "move", "data": {"regionId": attacker_region},
                        "reason": "REVENGE: Chase attacker"}

        move_target = _choose_move_target(connections, danger_ids,
                                          region, visible_items, alive_count)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "MOVE: Strategic"}

    # ═══════════════════════════════════════════════════════════════
    # [LAST RESORT] REST
    # ═══════════════════════════════════════════════════════════════
    if ep < 4 and not enemies_here and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {}, "reason": f"REST: EP={ep}/{max_ep}"}
    
    return {"action": "rest", "data": {}, "reason": "REST fallback"}


# ═══════════════════════════════════════════════════════════════════
#  EVENT HOOKS
# ═══════════════════════════════════════════════════════════════════

def on_attacked_by(attacker_id: str, current_turn: int):
    global _last_attacked_by, _last_attacked_turn
    _last_attacked_by = attacker_id
    _last_attacked_turn = current_turn
    log.warning("⚠️ ATTACKED BY: %s — PREPARING COUNTER!", attacker_id[:8])


def on_enemy_killed(enemy_id: str):
    on_killed_enemy(enemy_id)
    global _hunting_target
    if _hunting_target and _hunting_target.get("id") == enemy_id:
        _hunting_target = None
        log.info("✓ HUNT COMPLETE! %s eliminated.", enemy_id[:8])


def on_we_died(killer_id: str):
    on_killed_by_enemy(killer_id)
    reset_game_state()


def get_enemy_intel(enemy_id: str) -> dict:
    if enemy_id not in _enemy_profiles:
        return {}
    p = _enemy_profiles[enemy_id]
    return {
        "id": enemy_id[:8],
        "encounters": p["encounters"],
        "win_rate": f"{p['win_rate_vs']:.0%}",
        "preferred_weapon": p["preferred_weapon"],
        "known_weakness": p["known_weakness"],
        "behavior": list(p["behavior_tags"]),
        "avg_hp": round(sum(p["hp_samples"]) / len(p["hp_samples"]), 1) if p["hp_samples"] else "?",
        "kills_on_me": p["kills_on_me"],
        "times_killed": p["times_we_killed"],
    }


def get_all_enemy_intel() -> list:
    return [get_enemy_intel(eid) for eid in _enemy_profiles]


"""
══════════════════════════════════════════════════════════════════════
  BERSERKER BRAIN v3.2 — FINAL FIX
══════════════════════════════════════════════════════════════════════

PERBAIKAN UTAMA v3.2:

1. BROADCAST STATION FIX — ONLY ONCE PER GAME!
   ✅ Broadcast station sekarang hanya bisa di-interact SEKALI
   ✅ Menggunakan flag _broadcast_used khusus
   ✅ Tidak akan pernah loop di broadcast station lagi

2. FACILITY COOLDOWN WORKING!
   ✅ _mark_facility_used() mencatat penggunaan dengan benar
   ✅ Broadcast station tidak dihapus dari tracking (permanent)

3. SEMUA FACILITY AMAN:
   ✅ medical_facility: cooldown 10 turn
   ✅ supply_cache: cooldown 10 turn  
   ✅ watchtower: cooldown 10 turn
   ✅ broadcast_station: ONLY ONCE

══════════════════════════════════════════════════════════════════════
  CARA INTEGRASI KE HEARTBEAT.PY
══════════════════════════════════════════════════════════════════════

  import brain

  # Reset state di awal game
  brain.reset_game_state()

  # Saat bot kita diserang:
  brain.on_attacked_by(attacker_id=event["attackerId"], current_turn=turn)

  # Saat kita kill musuh:
  brain.on_enemy_killed(enemy_id=event["targetId"])

  # Saat kita mati:
  brain.on_we_died(killer_id=event["killerId"])

  # Di game loop:
  action = brain.decide_action(view=game_state, can_act=True)
  
  # Debug intel musuh:
  intel = brain.get_all_enemy_intel()
  print(intel)

══════════════════════════════════════════════════════════════════════
"""
