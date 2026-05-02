"""
Strategy brain — BERSERKER MODE v4.2 (SURVIVAL FIRST)
===========================================================================
PERBAIKAN DARI v4.1:
- FIX: Syntax error di baris flee_reason
- FIX: Bot terlalu agresif cari musuh di early game
- ADDED: Safe distancing - jauhi musuh dengan damage > 1.5x our damage
- ADDED: Hit-and-run tactic untuk musuh kuat
- IMPROVED: Early game = FARMING, bukan FIGHTING
===========================================================================
"""

import time
from collections import defaultdict, deque
from enum import Enum
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
#  KONFIGURASI BERSERKER v4.2 (SURVIVAL FIRST)
# ═══════════════════════════════════════════════════════════════════

BERSERKER_CONFIG = {
    # ── HP & EP Management ──────────────────────────────────────────
    "HP_MINIMUM":            50,
    "HP_CRITICAL":           25,
    "HP_HEAL_URGENT":        45,
    "HP_HEAL_MODERATE":      60,
    "EP_MINIMUM_RATIO":      0.50,
    "EP_ATTACK_MIN_RATIO":   0.25,
    "EP_SAFE_RATIO":         0.15,

    # ── Combat & Survival ───────────────────────────────────────────
    "MIN_HP_TO_ATTACK":      50,
    "MIN_HP_TO_ATTACK_GUARDIAN": 65,
    "COUNTER_ATTACK_HP":     30,
    "NEVER_FLEE_IF_ATTACKED": True,
    
    # ── Damage Comparison (REALISTIC) ────────────────────────────────
    "MAX_ENEMY_DAMAGE_RATIO": 2.0,
    "DANGEROUS_ENEMY_DAMAGE": 25,
    "FLEE_STRONG_ENEMY_RATIO": 1.8,
    
    # ── Survival Mode (BARU!) ────────────────────────────────────────
    "SURVIVAL_MODE_HP":      40,
    "SURVIVAL_FLEE_RATIO":   1.3,
    "FARM_TURNS_BEFORE_FIGHT": 30,
    
    # ── Pursuit ─────────────────────────────────────────────────────
    "PURSUIT_ENABLED":       True,
    "PURSUIT_MAX_HOPS":      2,
    "PURSUIT_MIN_HP":        60,
    
    # ── Recovery Mode ───────────────────────────────────────────────
    "RECOVERY_HP_THRESHOLD": 35,
    "RECOVERY_TARGET_HP":    75,
    "RECOVERY_FARM_GUARDIAN": True,
    "RECOVERY_FARM_GUARDIAN_MIN_HP": 55,

    # ── Hunting ─────────────────────────────────────────────────────
    "HUNTING_MODE":          True,
    "HUNT_UNTIL_DEATH":      False,
    "TARGET_MARK_DURATION":  15,
    "EXECUTE_HP_THRESHOLD":  30,
    "WOUNDED_HP_THRESHOLD":  50,
    "MIN_HP_TO_HUNT":        70,

    # ── Enemy Profiling ─────────────────────────────────────────────
    "PROFILE_MEMORY_SIZE":   100,
    "PROFILE_HISTORY_LEN":   20,

    # ── Inventory Management ────────────────────────────────────────
    "INV_MAX_CAPACITY":      10,
    "INV_DROP_THRESHOLD":    9,

    # ── Facility ────────────────────────────────────────────────────
    "MAX_FACILITY_INTERACTIONS": 1,
    "FACILITY_COOLDOWN_TURNS":   10,
    "BROADCAST_STATION_ONCE":    True,

    # ── Flee ─────────────────────────────────────────────────────────
    "FLEE_HP":               15,
    "FLEE_OUTNUMBERED":      3,
}


# ═══════════════════════════════════════════════════════════════════
#  ENEMY PLAYER STYLE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════

class PlayerStyle(Enum):
    AGGRESSOR = "aggressor"
    KITER = "kiter"
    HEALER = "healer"
    CAMPER = "camper"
    BERSERKER = "berserker"
    COUNTER_ATTACKER = "counter"
    OPPORTUNIST = "opportunist"
    ESCAPIST = "escapist"
    STRONG = "strong"


# ═══════════════════════════════════════════════════════════════════
#  ENHANCED ENEMY PROFILE
# ═══════════════════════════════════════════════════════════════════

class EnemyMemory:
    def __init__(self, enemy_id: str):
        self.id = enemy_id
        self.first_seen = time.time()
        
        self.encounters = 0
        self.victories_against_us = 0
        self.defeats_by_us = 0
        self.last_encounter_turn = 0
        self.last_encounter_result = None
        
        self.combat_logs = deque(maxlen=20)
        self.opening_moves = deque(maxlen=10)
        self.retreat_thresholds = []
        self.heal_tendency = 0.0
        self.pursuit_tendency = 0.0
        self.aggression_score = 0.5
        self.patience_score = 0.5
        
        self.weapon_preferences = defaultdict(int)
        self.favored_terrain = defaultdict(int)
        self.favored_weather = defaultdict(int)
        
        self.primary_style = PlayerStyle.OPPORTUNIST
        self.secondary_style = None
        self.confidence = 0.5
        
        self.known_counters = []
        self.what_worked = deque(maxlen=10)
        self.what_failed = deque(maxlen=10)
        
        self.current_adaptation = None
        self.adaptation_start_turn = 0
        
        self.real_damage_samples = []
        self.estimated_damage = 10
        
    def record_real_damage(self, damage: int):
        self.real_damage_samples.append(damage)
        if len(self.real_damage_samples) > 10:
            self.real_damage_samples.pop(0)
        self.estimated_damage = sum(self.real_damage_samples) // max(1, len(self.real_damage_samples))
        
    def record_combat(self, combat_data: dict):
        self.combat_logs.append({
            "turn": combat_data.get("turn", 0),
            "my_hp_start": combat_data.get("my_hp_start", 100),
            "my_hp_end": combat_data.get("my_hp_end", 100),
            "enemy_hp_start": combat_data.get("enemy_hp_start", 100),
            "enemy_hp_end": combat_data.get("enemy_hp_end", 100),
            "result": combat_data.get("result", "unknown"),
            "my_strategy": combat_data.get("my_strategy", "standard"),
            "enemy_behavior": combat_data.get("enemy_behavior", []),
            "weapon_used": combat_data.get("weapon_used", "fist"),
            "duration_turns": combat_data.get("duration_turns", 0),
            "i_initiated": combat_data.get("i_initiated", False),
            "enemy_initiated": combat_data.get("enemy_initiated", False),
        })
        
        self.last_encounter_turn = combat_data.get("turn", 0)
        self.last_encounter_result = combat_data.get("result", "unknown")
        
        if combat_data.get("result") == "loss":
            self.victories_against_us += 1
        elif combat_data.get("result") == "win":
            self.defeats_by_us += 1
    
    def update_style_analysis(self):
        if len(self.combat_logs) < 2:
            return
        
        initiations = sum(1 for log in self.combat_logs if log.get("enemy_initiated", False))
        self.aggression_score = min(1.0, initiations / max(1, len(self.combat_logs)))
        
        heals_in_combat = 0
        for log in self.combat_logs:
            if "healed" in str(log.get("enemy_behavior", [])):
                heals_in_combat += 1
        self.heal_tendency = heals_in_combat / max(1, len(self.combat_logs))
        
        if self.aggression_score > 0.7:
            if self.heal_tendency < 0.2:
                self.primary_style = PlayerStyle.BERSERKER
            else:
                self.primary_style = PlayerStyle.AGGRESSOR
        elif self.heal_tendency > 0.5:
            self.primary_style = PlayerStyle.HEALER
        elif self.retreat_thresholds and min(self.retreat_thresholds) < 40:
            self.primary_style = PlayerStyle.ESCAPIST
        elif self.aggression_score < 0.3:
            self.primary_style = PlayerStyle.CAMPER
        elif "counter" in str(self.combat_logs):
            self.primary_style = PlayerStyle.COUNTER_ATTACKER
        
        if self.estimated_damage > 25:
            self.primary_style = PlayerStyle.STRONG
        
        self.confidence = min(0.9, 0.5 + (len(self.combat_logs) * 0.05))
    
    def get_counter_strategy(self) -> str:
        style = self.primary_style
        
        counter_map = {
            PlayerStyle.AGGRESSOR: "bait_and_punish",
            PlayerStyle.BERSERKER: "kite_and_drain",
            PlayerStyle.HEALER: "burst_damage_no_pause",
            PlayerStyle.KITER: "close_range_rush",
            PlayerStyle.CAMPER: "force_move_hunt",
            PlayerStyle.COUNTER_ATTACKER: "never_attack_first",
            PlayerStyle.OPPORTUNIST: "show_weak_then_trap",
            PlayerStyle.ESCAPIST: "corner_and_chase",
            PlayerStyle.STRONG: "avoid_at_all_costs",
        }
        
        return counter_map.get(style, "standard")
    
    def get_combat_advice(self, my_hp: int, my_damage: int, current_turn: int) -> dict:
        advice = {
            "should_fight": True,
            "strategy": self.get_counter_strategy(),
            "risk_level": "medium",
            "special_notes": []
        }
        
        if self.estimated_damage > 25 and my_damage < 20:
            advice["should_fight"] = False
            advice["special_notes"].append(f"STRONG ENEMY! Damage: {self.estimated_damage}")
            return advice
        
        win_rate_vs_us = self.victories_against_us / max(1, self.encounters)
        if win_rate_vs_us > 0.6 and self.encounters > 2:
            advice["should_fight"] = False
            advice["special_notes"].append(f"Enemy beats us {win_rate_vs_us:.0%} of time - AVOID!")
            return advice
        
        if my_hp < 40:
            advice["should_fight"] = False
            advice["special_notes"].append(f"HP too low: {my_hp}")
        
        return advice


# ═══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_hunting_target: dict = None
_hunting_timer: int = 0
_interacted_facilities: dict = {}
_recovery_mode: bool = False
_last_attacked_by: str = None
_last_attacked_turn: int = 0
_broadcast_used: bool = False
_broadcast_region_used: str = None

_enemy_profiles: dict = {}
_enemy_memories: dict = {}
_global_meta_analysis = {
    "most_dangerous_enemy": None,
    "highest_winrate_enemy": None,
    "common_winning_strategies": defaultdict(int),
    "common_losing_strategies": defaultdict(int),
    "adaptation_active": False,
}
_current_combat_state = {
    "in_combat": False,
    "with_enemy": None,
    "start_turn": 0,
    "my_hp_start": 100,
    "enemy_hp_start": 100,
    "my_strategy": "standard",
}
_active_special_counters: dict = {}


# ═══════════════════════════════════════════════════════════════════
#  ENEMY PROFILING SYSTEM (Legacy)
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
#  FACILITY SELECTION
# ═══════════════════════════════════════════════════════════════════

def _select_facility_with_limit(interactables: list, hp: int, ep: int, current_turn: int, current_region_id: str) -> dict | None:
    global _interacted_facilities, _broadcast_used
    
    if not interactables:
        return None
    
    if _broadcast_used:
        filtered_interactables = []
        for fac in interactables:
            if isinstance(fac, dict) and fac.get("type", "").lower() == "broadcast_station":
                log.debug("Broadcast station skipped (already used this game)")
                continue
            filtered_interactables.append(fac)
        interactables = filtered_interactables
    
    cooldown = BERSERKER_CONFIG["FACILITY_COOLDOWN_TURNS"]
    
    expired = []
    for fid, turn in _interacted_facilities.items():
        if current_turn - turn > cooldown:
            expired.append(fid)
    for fid in expired:
        del _interacted_facilities[fid]
        log.debug("Facility %s cooldown expired", fid)
    
    for fac in interactables:
        if not isinstance(fac, dict):
            continue
        
        if fac.get("isUsed"):
            log.debug("Facility already used: isUsed=True")
            continue
        
        fid = fac.get("id", "")
        ftype = fac.get("type", "").lower()
        
        if ftype == "broadcast_station":
            if _broadcast_used:
                continue
            log.info("✅ Broadcast station available (first time this game)!")
            return fac
        
        if fid in _interacted_facilities:
            last_used = _interacted_facilities[fid]
            turns_ago = current_turn - last_used
            log.debug("Facility %s on cooldown (used %d turns ago)", ftype, turns_ago)
            continue
        
        if ftype == "medical_facility" and hp < 70:
            return fac
        
        if ftype in ["supply_cache", "watchtower"]:
            return fac
    
    return None


def _mark_facility_used(facility: dict, current_turn: int, current_region_id: str = None):
    global _interacted_facilities, _broadcast_used, _broadcast_region_used
    
    if not isinstance(facility, dict):
        return
    
    ftype = facility.get("type", "").lower()
    fid = facility.get("id", "")
    
    if ftype == "broadcast_station":
        _broadcast_used = True
        _broadcast_region_used = current_region_id
        log.warning("🚫 BROADCAST STATION MARKED AS USED! Will not be used again this game.")
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
                        visible_items: list, alive_count: int, is_survival_mode: bool = False) -> str | None:
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
            
            agents_in_region = conn.get("agents", [])
            enemy_count = len([a for a in agents_in_region if a.get("isGuardian") == False])
            if enemy_count > 1:
                score -= 10 * enemy_count
            
            if rid in item_regions:
                score += 5
            
            facs = conn.get("interactables", [])
            score += len([f for f in facs if isinstance(f, dict) and not f.get("isUsed")]) * 2
            
            weather = conn.get("weather", "").lower()
            score += {"storm": -2, "fog": -1, "rain": 0, "clear": 1}.get(weather, 0)
            
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
    return _select_weakest(enemies)


def update_hunting_target(target: dict):
    global _hunting_target, _hunting_timer
    if target and BERSERKER_CONFIG["HUNTING_MODE"]:
        _hunting_target = target
        _hunting_timer = BERSERKER_CONFIG["TARGET_MARK_DURATION"]
        log.info("🎯 NEW HUNT TARGET: %s HP=%d", target.get("id", "?")[:8], target.get("hp", 0))


# ═══════════════════════════════════════════════════════════════════
#  RECOVERY MODE
# ═══════════════════════════════════════════════════════════════════

def _handle_recovery_mode(my, inventory, visible_agents, region_id, connections,
                           danger_ids, equipped, region_weather, ep, ep_ratio,
                           move_ep_cost, monsters) -> dict | None:
    hp = my.get("hp", 100)
    log.info("🔄 RECOVERY MODE: HP=%d (target %d)", hp, BERSERKER_CONFIG["RECOVERY_TARGET_HP"])
    heal = _find_healing_item(inventory, critical=True)
    if heal:
        log.info("💊 RECOVERY HEAL: %s", heal.get("typeId", "heal"))
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
                log.info("👹 RECOVERY FARM: Guardian HP=%d", target.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "RECOVERY: Farm guardian"}
    if monsters:
        weak_monsters = [m for m in monsters if m.get("hp", 0) > 0 and m.get("hp", 100) < 40]
        if weak_monsters and ep >= 1:
            target = _select_weakest(weak_monsters)
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("🐾 RECOVERY FARM: Monster HP=%d", target.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": "RECOVERY: Farm monster"}
    if not danger_ids or region_id not in danger_ids:
        log.info("😴 RECOVERY REST: HP=%d EP=%d", hp, ep)
        return {"action": "rest", "data": {}, "reason": f"RECOVERY REST: HP={hp}"}
    return None


# ═══════════════════════════════════════════════════════════════════
#  ADAPTIVE LEARNING CORE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def get_or_create_memory(enemy_id: str) -> EnemyMemory:
    global _enemy_memories
    if enemy_id not in _enemy_memories:
        _enemy_memories[enemy_id] = EnemyMemory(enemy_id)
    return _enemy_memories[enemy_id]


def start_combat_tracking(enemy_id: str, turn: int, my_hp: int, enemy_hp: int, my_strategy: str):
    global _current_combat_state
    _current_combat_state = {
        "in_combat": True,
        "with_enemy": enemy_id,
        "start_turn": turn,
        "my_hp_start": my_hp,
        "enemy_hp_start": enemy_hp,
        "my_strategy": my_strategy,
        "enemy_actions": [],
    }
    log.info(f"📊 COMBAT TRACKING STARTED vs {enemy_id[:8]}")


def end_combat_tracking(result: str, my_hp_end: int, enemy_hp_end: int, turn: int):
    global _current_combat_state, _enemy_memories
    
    if not _current_combat_state["in_combat"]:
        return
    
    enemy_id = _current_combat_state["with_enemy"]
    memory = get_or_create_memory(enemy_id)
    
    combat_data = {
        "turn": turn,
        "my_hp_start": _current_combat_state["my_hp_start"],
        "my_hp_end": my_hp_end,
        "enemy_hp_start": _current_combat_state["enemy_hp_start"],
        "enemy_hp_end": enemy_hp_end,
        "result": result,
        "my_strategy": _current_combat_state["my_strategy"],
        "enemy_behavior": _current_combat_state.get("enemy_actions", []),
        "duration_turns": turn - _current_combat_state["start_turn"],
        "i_initiated": False,
        "enemy_initiated": False,
    }
    
    memory.record_combat(combat_data)
    memory.update_style_analysis()
    
    if result == "loss":
        _global_meta_analysis["common_losing_strategies"][_current_combat_state["my_strategy"]] += 1
        winrate_vs_us = memory.victories_against_us / max(1, memory.encounters)
        if winrate_vs_us > 0.6:
            _global_meta_analysis["most_dangerous_enemy"] = enemy_id
    else:
        _global_meta_analysis["common_winning_strategies"][_current_combat_state["my_strategy"]] += 1
    
    if result == "win":
        memory.what_worked.append(_current_combat_state["my_strategy"])
    else:
        memory.what_failed.append(_current_combat_state["my_strategy"])
    
    log.info(f"📊 COMBAT ENDED vs {enemy_id[:8]}: {result.upper()}")
    
    _current_combat_state["in_combat"] = False


def get_adaptive_strategy_vs(enemy_id: str, my_hp: int, my_damage: int, current_turn: int) -> str:
    if enemy_id not in _enemy_memories:
        return "standard"
    
    memory = _enemy_memories[enemy_id]
    advice = memory.get_combat_advice(my_hp, my_damage, current_turn)
    
    if not advice["should_fight"]:
        return "flee_recommended"
    
    if memory.what_worked:
        return memory.what_worked[-1]
    
    return advice["strategy"]


def get_global_adaptation() -> dict:
    global _global_meta_analysis
    
    adaptation = {
        "adjust_thresholds": {},
        "priority_changes": [],
    }
    
    if _global_meta_analysis["common_losing_strategies"].get("aggressive", 0) > 5:
        adaptation["adjust_thresholds"]["MIN_HP_TO_ATTACK"] = 55
        adaptation["priority_changes"].append("more_careful")
        log.info("🔄 GLOBAL ADAPTATION: Being more careful")
    
    return adaptation


def record_enemy_action_in_combat(action: str, details: dict):
    if _current_combat_state["in_combat"]:
        _current_combat_state["enemy_actions"].append({
            "turn": details.get("turn", 0),
            "action": action,
            "hp": details.get("hp", 0),
            "weapon": details.get("weapon", ""),
        })


def on_defeated_by_enemy(enemy: dict, combat_summary: dict):
    enemy_id = enemy.get("id", "")
    if not enemy_id:
        return
    
    memory = get_or_create_memory(enemy_id)
    
    reasons = []
    my_hp_final = combat_summary.get("my_hp_final", 0)
    enemy_hp_final = combat_summary.get("enemy_hp_final", 100)
    
    if my_hp_final < 20 and enemy_hp_final > 50:
        reasons.append("got_outdamaged_significantly")
    elif my_hp_final < 10 and enemy_hp_final < 30:
        reasons.append("close_fight_lost")
    
    my_strategy = combat_summary.get("my_strategy", "standard")
    enemy_style = memory.primary_style
    
    log.error(f"💀 DEFEAT ANALYSIS vs {enemy_id[:8]}: {', '.join(reasons)}")
    
    winrate = memory.victories_against_us / max(1, memory.encounters)
    if winrate > 0.7 and memory.encounters >= 3:
        log.warning(f"🚨 ENEMY {enemy_id[:8]} HAS {winrate:.0%} WINRATE VS US! WILL AVOID!")
        _active_special_counters[enemy_id] = {
            "strategy": "avoid_at_all_costs",
            "active_until": combat_summary.get("turn", 0) + 100,
        }


def get_special_counter(enemy_id: str) -> str | None:
    if enemy_id in _active_special_counters:
        counter = _active_special_counters[enemy_id]
        return counter["strategy"]
    return None


def get_learning_report() -> dict:
    report = {
        "total_enemies_learned": len(_enemy_memories),
        "most_dangerous": None,
        "enemy_breakdown": [],
        "meta_analysis": dict(_global_meta_analysis),
        "active_adaptations": len(_active_special_counters),
    }
    
    most_dangerous = None
    highest_winrate = 0
    
    for eid, memory in _enemy_memories.items():
        winrate = memory.victories_against_us / max(1, memory.encounters)
        if winrate > highest_winrate and memory.encounters >= 2:
            highest_winrate = winrate
            most_dangerous = eid
        
        report["enemy_breakdown"].append({
            "id": eid[:8],
            "encounters": memory.encounters,
            "winrate_vs_us": f"{winrate:.0%}",
            "style": memory.primary_style.value,
            "confidence": f"{memory.confidence:.0%}",
            "effective_counter": memory.get_counter_strategy(),
            "estimated_damage": memory.estimated_damage,
        })
    
    report["most_dangerous"] = most_dangerous[:8] if most_dangerous else None
    return report


def print_learning_summary():
    report = get_learning_report()
    
    print("\n" + "="*60)
    print("🧠 ADAPTIVE LEARNING SUMMARY")
    print("="*60)
    print(f"Total enemies learned: {report['total_enemies_learned']}")
    print(f"Most dangerous enemy: {report['most_dangerous']}")
    print(f"Active adaptations: {report['active_adaptations']}")
    print("\nEnemy Breakdown:")
    
    for e in report["enemy_breakdown"][:10]:
        print(f"  • {e['id']}: {e['style']} | Dmg:{e['estimated_damage']} | Win vs us: {e['winrate_vs_us']} | Counter: {e['effective_counter']}")
    
    print("="*60 + "\n")


def reset_game_state():
    global _known_agents, _map_knowledge, _hunting_target, _hunting_timer
    global _interacted_facilities, _recovery_mode, _last_attacked_by, _last_attacked_turn
    global _broadcast_used, _broadcast_region_used, _enemy_memories, _global_meta_analysis
    global _active_special_counters, _current_combat_state
    
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _hunting_target = None
    _hunting_timer = 0
    _interacted_facilities = {}
    _recovery_mode = False
    _last_attacked_by = None
    _last_attacked_turn = 0
    _broadcast_used = False
    _broadcast_region_used = None
    _enemy_memories = {}
    _global_meta_analysis = {
        "most_dangerous_enemy": None,
        "highest_winrate_enemy": None,
        "common_winning_strategies": defaultdict(int),
        "common_losing_strategies": defaultdict(int),
        "adaptation_active": False,
    }
    _active_special_counters = {}
    _current_combat_state = {
        "in_combat": False,
        "with_enemy": None,
        "start_turn": 0,
        "my_hp_start": 100,
        "enemy_hp_start": 100,
        "my_strategy": "standard",
    }
    
    log.info("=" * 65)
    log.info("  BERSERKER BRAIN v4.2 — SURVIVAL FIRST!")
    log.info("  Prioritaskan survival, jangan cari mati di early game")
    log.info("=" * 65)


# ═══════════════════════════════════════════════════════════════════
#  MAIN DECISION ENGINE v4.2 (SURVIVAL FIRST)
# ═══════════════════════════════════════════════════════════════════

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    global _hunting_target, _hunting_timer, _interacted_facilities
    global _recovery_mode, _last_attacked_by, _last_attacked_turn, _broadcast_used
    global _current_combat_state

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
    my_weapon   = equipped.get("typeId", "fist").lower() if equipped else "fist"

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

    is_early_game = current_turn < BERSERKER_CONFIG["FARM_TURNS_BEFORE_FIGHT"]
    is_survival_mode = hp < BERSERKER_CONFIG["SURVIVAL_MODE_HP"]

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

    my_damage = calc_damage(atk, get_weapon_bonus(equipped), 5, region_weather)
    
    strongest_enemy_damage = max(
        (calc_damage(e.get("atk", 10), _estimate_enemy_weapon_bonus(e), defense, region_weather)
         for e in enemies_here),
        default=0
    )
    
    for e in enemies_here:
        eid = e.get("id", "")
        if eid in _enemy_memories:
            mem = _enemy_memories[eid]
            if mem.estimated_damage > strongest_enemy_damage:
                strongest_enemy_damage = mem.estimated_damage
    
    has_guardian = len(guardians_here) > 0

    # COMBAT TRACKING MANAGEMENT
    if enemies_here and not _current_combat_state["in_combat"]:
        enemy = enemies_here[0]
        start_combat_tracking(
            enemy_id=enemy.get("id", ""),
            turn=current_turn,
            my_hp=hp,
            enemy_hp=enemy.get("hp", 100),
            my_strategy="berserker_v4"
        )
    elif not enemies_here and _current_combat_state["in_combat"]:
        end_combat_tracking(
            result="escape",
            my_hp_end=hp,
            enemy_hp_end=0,
            turn=current_turn
        )

    # EARLY GAME: FARMING FIRST
    if is_early_game:
        log.info("🌱 EARLY GAME FARMING MODE (Turn %d/%d)", current_turn, BERSERKER_CONFIG["FARM_TURNS_BEFORE_FIGHT"])
        
        if enemies_here:
            safe = _find_safe_region(connections, danger_ids, view)
            if safe and ep >= move_ep_cost:
                log.warning("🏃 EARLY GAME: Avoiding enemy, moving to %s", safe)
                return {"action": "move", "data": {"regionId": safe}, "reason": "EARLY: Avoid enemy"}
        
        pickup_action = _smart_pickup(visible_items, inventory, region_id, equipped)
        if pickup_action:
            return pickup_action
        
        if monsters and hp > 60 and ep >= 1:
            target = _select_weakest(monsters)
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("🐾 EARLY FARM: Monster HP=%d", target.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": "EARLY: Farm monster"}

    # DEATHZONE ESCAPE
    if region.get("isDeathZone", False) or region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("💀 DEATHZONE ESCAPE! -> %s", safe)
            return {"action": "move", "data": {"regionId": safe}, "reason": "DEATHZONE ESCAPE"}

    # UPDATE RECOVERY MODE
    if hp < BERSERKER_CONFIG["RECOVERY_HP_THRESHOLD"]:
        _recovery_mode = True
    elif hp >= BERSERKER_CONFIG["RECOVERY_TARGET_HP"]:
        if _recovery_mode:
            log.info("✅ RECOVERY COMPLETE! HP=%d", hp)
        _recovery_mode = False

    # CRITICAL HEAL
    if hp < BERSERKER_CONFIG["HP_CRITICAL"]:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            log.warning("🚨 CRITICAL HEAL! HP=%d -> %s", hp, heal.get("typeId", "heal"))
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}

    # RECOVERY MODE
    if _recovery_mode and not enemies_here:
        result = _handle_recovery_mode(
            self_data, inventory, visible_agents, region_id,
            connections, danger_ids, equipped, region_weather,
            ep, ep_ratio, move_ep_cost, monsters
        )
        if result:
            return result

    # FLEE LOGIC - FIXED SYNTAX
    should_flee = False
    flee_reason = ""

    if is_survival_mode and not just_attacked:
        if enemies_here and strongest_enemy_damage > my_damage * BERSERKER_CONFIG["SURVIVAL_FLEE_RATIO"]:
            should_flee = True
            flee_reason = f"SURVIVAL: their_dmg={strongest_enemy_damage} my_dmg={my_damage}"
    else:
        if hp < BERSERKER_CONFIG["FLEE_HP"]:
            should_flee = True
            flee_reason = f"HP_CRITICAL: {hp}"
        elif enemies_here and strongest_enemy_damage > my_damage * BERSERKER_CONFIG["FLEE_STRONG_ENEMY_RATIO"]:
            weakest_enemy_hp = min((e.get("hp", 999) for e in enemies_here), default=999)
            if weakest_enemy_hp > my_damage * 4:
                should_flee = True
                flee_reason = f"ENEMY_STRONGER: their_dmg={strongest_enemy_damage} my_dmg={my_damage}"
        elif len(enemies_here) >= BERSERKER_CONFIG["FLEE_OUTNUMBERED"]:
            should_flee = True
            flee_reason = f"OUTNUMBERED: {len(enemies_here)}"

    if should_flee:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🏃 FLEEING! %s -> %s", flee_reason, safe)
            return {"action": "move", "data": {"regionId": safe}, "reason": f"FLEE: {flee_reason}"}

    # COUNTER ATTACK
    if just_attacked and _last_attacked_by and hp >= BERSERKER_CONFIG["COUNTER_ATTACK_HP"]:
        attacker = next((e for e in enemies_here if e.get("id") == _last_attacked_by), None)
        if attacker:
            attacker_damage = calc_damage(attacker.get("atk", 10), 
                                          _estimate_enemy_weapon_bonus(attacker), 
                                          defense, region_weather)
            if hp > attacker_damage * 2:
                log.warning("⚔️ COUNTER ATTACK! vs %s (hp=%d) MyHP=%d", 
                           _last_attacked_by[:8], attacker.get("hp", 0), hp)
                return {"action": "attack",
                        "data": {"targetId": attacker["id"], "targetType": "agent"},
                        "reason": f"COUNTER: vs {_last_attacked_by[:8]}"}

    # PRE-FIGHT HEAL
    if enemies_here and strongest_enemy_damage > 10 and hp < 50:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            log.info("💊 PRE-FIGHT HEAL: HP=%d, enemy_dmg=%d", hp, strongest_enemy_damage)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"PRE-FIGHT HEAL: HP={hp}"}

    # EQUIP BEST WEAPON
    equip_action = _check_equip(inventory, equipped)
    if equip_action:
        return equip_action

    # COMBAT
    can_attack = (hp >= BERSERKER_CONFIG["MIN_HP_TO_ATTACK"]
                  and ep_ratio >= BERSERKER_CONFIG["EP_ATTACK_MIN_RATIO"]
                  and not is_survival_mode)

    if has_guardian and hp < BERSERKER_CONFIG["MIN_HP_TO_ATTACK_GUARDIAN"]:
        can_attack = False

    if enemies_here and strongest_enemy_damage > my_damage * BERSERKER_CONFIG["MAX_ENEMY_DAMAGE_RATIO"]:
        can_attack = False

    if _hunting_target and hp >= BERSERKER_CONFIG["MIN_HP_TO_HUNT"]:
        can_attack = True

    if enemies_here and can_attack:
        target = select_target_with_priority(enemies_here, "standard")

        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                enemy_hp = target.get("hp", 100)
                
                if not _hunting_target and hp >= BERSERKER_CONFIG["MIN_HP_TO_HUNT"]:
                    update_hunting_target(target)
                
                log.info("🔥 ATTACK! Target %s HP=%d MyDMG=%d MyHP=%d EnemyDMG=%d",
                         target.get("id", "?")[:8], enemy_hp, my_damage, hp, 
                         strongest_enemy_damage)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"ATTACK: target_hp={enemy_hp}"}

    # GUARDIAN FARMING
    guardians_all = [a for a in visible_agents
                     if a.get("isGuardian", False) and a.get("isAlive", True)]
    
    guardian_farm_ok = (hp >= BERSERKER_CONFIG["MIN_HP_TO_ATTACK_GUARDIAN"] 
                        and ep >= 2 
                        and my_damage >= 12
                        and not _hunting_target
                        and not is_survival_mode)
    
    if guardians_all and guardian_farm_ok:
        target = _select_weakest(guardians_all)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            log.info("👹 GUARDIAN FARM: HP=%d MyDMG=%d", target.get("hp", 0), my_damage)
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "agent"},
                    "reason": "GUARDIAN FARM"}

    # SMART PICKUP
    if not enemies_here:
        pickup_action = _smart_pickup(visible_items, inventory, region_id, equipped)
        if pickup_action:
            return pickup_action

    util_action = _use_utility_item(inventory)
    if util_action:
        return util_action

    if not can_act:
        return None

    # FACILITY INTERACTION
    if not enemies_here and not guardians_here and not is_survival_mode:
        facility = _select_facility_with_limit(interactables, hp, ep, current_turn, region_id)
        if facility:
            _mark_facility_used(facility, current_turn, region_id)
            ftype = facility.get("type", "?")
            log.info("🏭 FACILITY INTERACT: %s", ftype)
            return {"action": "interact", "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {ftype}"}

    # HEAL OPPORTUNISTIK
    if hp < BERSERKER_CONFIG["HP_HEAL_URGENT"] and not enemies_here and not _hunting_target:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            log.info("💊 OPPORTUNISTIC HEAL: HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}

    # EP RECOVERY
    if ep_ratio < BERSERKER_CONFIG["EP_MINIMUM_RATIO"]:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP DRINK: {ep}/{max_ep}"}
        if not enemies_here and region_id not in danger_ids and not _hunting_target:
            log.info("😴 REST: EP=%d/%d (%.0f%%)", ep, max_ep, ep_ratio * 100)
            return {"action": "rest", "data": {}, "reason": f"REST: EP={ep}/{max_ep}"}

    # MONSTER FARMING
    if monsters and ep >= 1 and hp > 50 and not enemies_here and not _hunting_target and not is_survival_mode:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            log.info("🐾 MONSTER FARM: HP=%d", target.get("hp", 0))
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER FARM: HP={target.get('hp','?')}"}

    # MOVEMENT
    if ep >= move_ep_cost and connections:
        if not is_survival_mode and not is_early_game:
            if _hunting_target and BERSERKER_CONFIG["PURSUIT_ENABLED"] and hp >= BERSERKER_CONFIG["PURSUIT_MIN_HP"]:
                target_region = _hunting_target.get("regionId", "")
                if target_region and target_region != region_id and target_region not in danger_ids:
                    log.info("🎯 PURSUIT: Chase %s to %s", _hunting_target.get("id", "?")[:8], target_region)
                    return {"action": "move", "data": {"regionId": target_region},
                            "reason": "PURSUIT: Chase target"}

        move_target = _choose_move_target(connections, danger_ids,
                                          region, visible_items, alive_count, is_survival_mode)
        if move_target:
            log.info("🚶 MOVE: Strategic to %s", move_target)
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "MOVE: Strategic"}

    # LAST RESORT REST
    if ep < 4 and not enemies_here and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {}, "reason": f"REST: EP={ep}/{max_ep}"}
    
    return {"action": "rest", "data": {}, "reason": "REST fallback"}


# ═══════════════════════════════════════════════════════════════════
#  EVENT HOOKS
# ═══════════════════════════════════════════════════════════════════

def on_attacked_by(attacker_id: str, current_turn: int, damage: int = None):
    global _last_attacked_by, _last_attacked_turn
    _last_attacked_by = attacker_id
    _last_attacked_turn = current_turn
    
    if damage and attacker_id:
        memory = get_or_create_memory(attacker_id)
        memory.record_real_damage(damage)
    
    log.warning("⚠️ ATTACKED BY: %s for %d damage", attacker_id[:8], damage or 0)


def on_enemy_killed(enemy_id: str):
    on_killed_enemy(enemy_id)
    global _hunting_target
    if _hunting_target and _hunting_target.get("id") == enemy_id:
        _hunting_target = None
        log.info("✓ HUNT COMPLETE! %s eliminated.", enemy_id[:8])


def on_we_died(killer_id: str, combat_summary: dict = None):
    on_killed_by_enemy(killer_id)
    if combat_summary:
        on_defeated_by_enemy({"id": killer_id}, combat_summary)
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
