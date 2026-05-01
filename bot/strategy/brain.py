"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v3.1.1 - FIXED: Removed broken claim_smoltz function
==============================================================
FIXES:
- REMOVED _claim_smoltz_from_inventory (item currency cannot be used)
- SMOLTZ automatically added when picking up $Moltz items
- Fixed ACTION_FAILED error
"""

from bot.utils.logger import get_logger
import random

log = get_logger(__name__)


# =========================
# 🔥 CONFIGURATION
# =========================

GUARDIAN_SMOLTZ_REWARD = 120
PLAYER_KILL_SMOLTZ = 100
LOW_HP_FINISH_THRESHOLD = 30
FARMING_HP_MIN = 25

# Mode switching thresholds
RUSH_MAX_ALIVE = 50
HUNT_MAX_ALIVE = 20

# Early game settings
EARLY_GAME_TURNS = 50

# Weapon stats
WEAPONS = {
    "fist": {"bonus": 0, "range": 0, "value": 0, "priority": 0},
    "dagger": {"bonus": 10, "range": 0, "value": 20, "priority": 10},
    "bow": {"bonus": 5, "range": 1, "value": 15, "priority": 5},
    "pistol": {"bonus": 10, "range": 1, "value": 25, "priority": 10},
    "sword": {"bonus": 20, "range": 0, "value": 40, "priority": 20},
    "sniper": {"bonus": 28, "range": 2, "value": 70, "priority": 28},
    "katana": {"bonus": 35, "range": 0, "value": 80, "priority": 35},
}

ITEM_PRIORITY = {
    "rewards": 1000,
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


# Global state
_known_agents: dict = {}
_known_guardians: dict = {}
_total_smoltz_collected: int = 0
_farming_stats: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_current_mode: str = "RUSH"
_turn_counter: int = 0


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
    global _known_agents, _known_guardians, _total_smoltz_collected, _farming_stats, _map_knowledge, _current_mode, _turn_counter
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
    log.info("=" * 60)
    log.info("🤖 META CORE v3.1.1 - EARLY GAME WEAPON PRIORITY")
    log.info("   RUSH (Early) → HUNT (Mid) → SURVIVE (Late)")
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
    """
    PICKUP PRIORITY:
    1. SMOLTZ (absolute priority)
    2. WEAPON (high priority di early game atau jika belum punya weapon)
    """
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
        
        # Ambil weapon jika: belum punya weapon ATAU weapon lebih bagus ATAU early game
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
    global _turn_counter
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
    
    _track_smoltz_gain(view, self_data.get("id", ""))
    
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
    
    mode, mode_thresholds = _get_mode(alive_count)
    EP_SAFE_THRESHOLD = mode_thresholds["EP_SAFE"]
    EP_COMBAT_MIN = mode_thresholds["EP_COMBAT"]
    
    is_early = _is_early_game()
    has_weapon = not _has_no_weapon(equipped, inventory)
    
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
                         and not a.get("isGuardian", False)
                         and a.get("id") != self_data.get("id")]
    
    in_deathzone = region.get("isDeathZone", False) or region_id in danger_ids
    
    log.debug("📊 [%s][TURN:%d] HP=%d EP=%d/%d ATK=%d WEAPON=%s", 
              mode, _turn_counter, hp, ep, max_ep, total_atk,
              equipped.get("typeId", "fist") if equipped else "fist")
    
    # PRIORITY 1: DEATHZONE ESCAPE
    if in_deathzone:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 [%s] DEATHZONE! Escaping", mode)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}
    
    # PRIORITY 2: PICKUP SMOLTZ & WEAPON
    pickup_action = _pickup_smoltz_and_weapon(visible_items, inventory, region_id, 
                                               is_early, has_weapon, equipped)
    if pickup_action:
        return pickup_action
    
    # PRIORITY 3: EQUIP BEST WEAPON
    equip_action = _check_equip_best_weapon(inventory, equipped)
    if equip_action:
        return equip_action
    
    if not can_act:
        return None
    
    # PRIORITY 4: KILL STEAL (HP < 20)
    all_enemies = [a for a in visible_agents
                   if a.get("isAlive", True)
                   and a.get("id") != self_data.get("id")]
    
    for enemy in all_enemies:
        if enemy.get("hp", 100) < 20:
            w_range = get_weapon_range(equipped)
            if _is_in_range(enemy, region_id, w_range, connections):
                log.info("🔪 KILL STEAL! Enemy HP=%d < 20!", enemy.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": enemy["id"], "targetType": "agent"},
                        "reason": "🔥 KILL STEAL: HP<20!"}
    
    can_fight = ep_ratio >= EP_COMBAT_MIN and hp >= FARMING_HP_MIN
    
    # DANGEROUS ENEMY AVOIDANCE
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
    
    # MODE RUSH: AGGRESSIVE
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
                    log.info("🎯 [RUSH] GUARDIAN HUNT! +120 sMoltz")
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": "💰 RUSH: Guardian 120 sMoltz!"}
    
    # MODE HUNT: SMART KILLING
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
                    log.info("🎯 [HUNT] GUARDIAN: 120 sMoltz")
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": "💰 HUNT: Guardian 120 sMoltz!"}
    
    # MODE SURVIVE: ONLY SURE WINS
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
    
    # GUARDIAN FARMING
    guardians = [a for a in visible_agents if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and can_fight and hp >= 30:
        target = _select_weakest_target(guardians)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("💰 GUARDIAN: 120 sMoltz!")
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "💰 GUARDIAN: 120 sMoltz!"}
    
    # MONSTER FARMING
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and can_fight and hp > 20:
        target = _select_weakest_monster(monsters)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": f"MONSTER: HP={target.get('hp', '?')}"}
    
    # HP & EP MAINTENANCE
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
    
    # MOVEMENT & ACTIVE HUNTING
    if ep >= move_ep_cost and connections:
        if mode in ["RUSH", "HUNT"]:
            hunt_region = _find_low_hp_enemy_region(all_enemies, region_id)
            if hunt_region and hunt_region not in danger_ids:
                log.info("🎯 ACTIVE HUNTING: Moving to low HP enemy at %s", hunt_region[:8])
                return {"action": "move", "data": {"regionId": hunt_region},
                        "reason": "HUNT: Chasing low HP enemy!"}
        
        move_target = _find_safe_region(connections, danger_ids, view)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": f"MOVE: {mode} strategy"}
    
    # CHAOS FACTOR
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
v3.1.1 - FIXED: Removed broken claim_smoltz function
================================================================================

FIXES:
- REMOVED _claim_smoltz_from_inventory (item currency cannot be used in this game)
- SMOLTZ automatically added to balance when picking up $Moltz items
- No more ACTION_FAILED errors

KEY PRIORITIES:
1. Deathzone escape
2. Pickup SMOLTZ (absolute priority)
3. Pickup weapons (early game priority)
4. Equip best weapon
5. Kill steal (HP < 20)
6. Mode-based combat (RUSH/HUNT/SURVIVE)
7. Guardian farming (120 sMoltz)
8. Monster farming
9. HP/EP maintenance
================================================================================
"""
