"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v2.0.0 - ULTIMATE SMOLTZ FARMING WITH HIGH ATK PRIORITY
==========================================================
KEY FEATURES:
- HIGH ATK PRIORITY: Always equip best weapon, hunt for weapon upgrades
- HP/EP MANAGEMENT: Maintain both above 60% at all times
- SMOLTZ FIRST: Pickup currency immediately, hunt guardians (120 sMoltz)
- WEAKEST ENEMY FIRST: Target lowest HP enemies for quick kills
- AGGRESSIVE FARMING: Balance between survival and maximum sMoltz collection
"""

from bot.utils.logger import get_logger

log = get_logger(__name__)


# =========================
# 🔥 CONFIGURATION
# =========================

# ── SMOLTZ FARMING CONFIGURATION ─────────────────────────────────────
GUARDIAN_SMOLTZ_REWARD = 120  # Each guardian drops 120 sMoltz
MONSTER_SMOLTZ_RANGE = (10, 50)  # Monsters drop 10-50 sMoltz
PLAYER_KILL_SMOLTZ = 100  # Killing a player yields ~100 sMoltz
CURRENCY_ITEM_VALUE = 50  # Moltz items are worth 50 each

# Farming thresholds
FARMING_HP_MIN = 25  # Minimum HP to engage in farming
FARMING_EP_MIN = 0.20  # Minimum EP ratio for farming
LOW_HP_FINISH_THRESHOLD = 30  # Finish enemies with HP < 30

# HP/EP Management thresholds (v2.0.0 - Maintain above 60%)
HP_SAFE_THRESHOLD = 60    # Maintain HP above 60%
HP_CRITICAL = 30          # Critical HP, must heal
EP_SAFE_THRESHOLD = 0.60  # Maintain EP above 60%
EP_CRITICAL = 0.30        # Critical EP, prioritize recovery
EP_COMBAT_MIN = 0.40      # Minimum EP for combat (40%)

# ── Weapon stats with ATK priority ────────────────────────────────────
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

# ── Item priority for pickup (v2.0.0 - ATK first then SMOLTZ) ─────────
ITEM_PRIORITY = {
    "rewards": 1000,       # SMOLTZ - HIGHEST!
    "katana": 900,         # Best weapon (+35 ATK)
    "sniper": 850,         # +28 ATK
    "sword": 800,          # +20 ATK
    "pistol": 750,         # +10 ATK
    "dagger": 700,         # +10 ATK
    "bow": 650,            # +5 ATK
    "medkit": 500,         # Healing
    "bandage": 450,        # Healing
    "emergency_food": 400, # Healing
    "energy_drink": 350,   # EP recovery
    "binoculars": 200,     # Vision
    "map": 150,            # Map reveal
    "megaphone": 100,      # Communication
}

# ── Recovery items for healing ───────────────────────────────────────
RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,
}

# Weather combat penalty
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
    """Calculate damage with weather penalty."""
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped_weapon) -> int:
    """Get ATK bonus from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_priority(equipped_weapon) -> int:
    """Get weapon priority (higher = better)."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("priority", 0)


def get_weapon_range(equipped_weapon) -> int:
    """Get range from equipped weapon."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)


# Global state
_known_agents: dict = {}
_known_guardians: dict = {}
_total_smoltz_collected: int = 0
_farming_stats: dict = {
    "guardians_killed": 0,
    "players_killed": 0,
    "monsters_killed": 0,
    "items_collected": 0,
    "total_smoltz": 0,
}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": [], "rich_regions": []}


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
    """Reset per-game tracking state."""
    global _known_agents, _known_guardians, _total_smoltz_collected, _farming_stats, _map_knowledge
    _known_agents = {}
    _known_guardians = {}
    _total_smoltz_collected = 0
    _farming_stats = {
        "guardians_killed": 0,
        "players_killed": 0,
        "monsters_killed": 0,
        "items_collected": 0,
        "total_smoltz": 0,
    }
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": [], "rich_regions": []}
    log.info("=" * 50)
    log.info("🎯 ULTIMATE SMOLTZ FARMING MODE v2.0.0")
    log.info("   - High ATK priority")
    log.info("   - HP/EP > 60% maintenance")
    log.info("   - Weakest enemy first")
    log.info("=" * 50)


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

    log.info("🗺️ MAP LEARNED: %d DZ regions, %d safe regions",
             len(_map_knowledge["death_zones"]), len(safe_regions))


# =========================
# 🧠 HP/EP MANAGEMENT (v2.0.0 - Maintain above 60%)
# =========================

def _check_hp_status(hp: int, max_hp: int = 100) -> dict:
    """Check HP status and return recommendations.
    v2.0.0: Maintain HP above 60% at all times.
    """
    hp_ratio = hp / max_hp if max_hp > 0 else 1.0
    
    if hp < HP_CRITICAL:
        return {"status": "critical", "need_heal": True, "can_fight": False, "priority": 1}
    elif hp < HP_SAFE_THRESHOLD:
        return {"status": "low", "need_heal": True, "can_fight": True, "priority": 2}
    else:
        return {"status": "good", "need_heal": False, "can_fight": True, "priority": 5}


def _check_ep_status(ep: int, max_ep: int) -> dict:
    """Check EP status and return recommendations.
    v2.0.0: Maintain EP above 60% at all times.
    """
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0
    
    if ep_ratio < EP_CRITICAL:
        return {"status": "critical", "need_rest": True, "can_fight": False, "priority": 1}
    elif ep_ratio < EP_SAFE_THRESHOLD:
        return {"status": "low", "need_rest": True, "can_fight": True, "priority": 2}
    else:
        return {"status": "good", "need_rest": False, "can_fight": True, "priority": 4}


def _manage_hp(hp: int, inventory: list, enemies_nearby: bool, in_deathzone: bool) -> dict | None:
    """Manage HP - heal if below 60%, prioritize keeping HP high."""
    hp_status = _check_hp_status(hp)
    
    # Critical HP: Use best healing available
    if hp_status["status"] == "critical":
        # Find best healing item (Medkit first)
        medkit = _find_healing_item(inventory, critical=True)
        if medkit:
            log.warning("💚 CRITICAL HP=%d! Using %s", hp, medkit.get('typeId', 'heal'))
            return {"action": "use_item", "data": {"itemId": medkit["id"]},
                    "reason": f"CRITICAL HP: {hp} -> healing"}
    
    # Low HP (below 60%): Heal if safe
    elif hp_status["status"] == "low":
        if not enemies_nearby and not in_deathzone:
            heal = _find_healing_item(inventory, critical=False, prefer_small=True)
            if heal:
                log.info("💚 HP MAINTENANCE: %d -> using %s", hp, heal.get('typeId', 'heal'))
                return {"action": "use_item", "data": {"itemId": heal["id"]},
                        "reason": f"HP MAINTENANCE: {hp} -> target >60%"}
    
    return None


def _manage_ep(ep: int, max_ep: int, inventory: list, enemies_nearby: bool, 
               in_deathzone: bool, danger_ids: set, connections: list, view: dict) -> dict | None:
    """Manage EP - rest or use energy drink if below 60%."""
    ep_status = _check_ep_status(ep, max_ep)
    
    # Critical EP: Must recover immediately
    if ep_status["status"] == "critical":
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            log.warning("⚡ CRITICAL EP=%d/%d! Using energy drink", ep, max_ep)
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"CRITICAL EP: {ep}/{max_ep} -> restore"}
        
        # No energy drink, need to rest or flee to safe zone
        if not enemies_nearby and not in_deathzone:
            return {"action": "rest", "data": {},
                    "reason": f"CRITICAL EP: {ep}/{max_ep} -> resting"}
        else:
            # Flee to safe region
            safe = _find_safe_region(connections, danger_ids, view)
            if safe:
                return {"action": "move", "data": {"regionId": safe},
                        "reason": f"EP CRITICAL: fleeing to safe region"}
    
    # Low EP (below 60%): Recover if safe
    elif ep_status["status"] == "low":
        energy_drink = _find_energy_drink(inventory)
        if energy_drink and ep / max_ep < 0.5:
            log.info("⚡ EP MAINTENANCE: %d/%d -> using energy drink", ep, max_ep)
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP MAINTENANCE: {ep}/{max_ep} -> target >60%"}
        
        if not enemies_nearby and not in_deathzone and ep / max_ep < 0.5:
            return {"action": "rest", "data": {},
                    "reason": f"EP MAINTENANCE: {ep}/{max_ep} -> resting"}
    
    return None


# =========================
# 🧠 WEAPON & ATK MANAGEMENT (v2.0.0 - Always high ATK)
# =========================

def _check_equip_best_weapon(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon for maximum ATK.
    v2.0.0: ALWAYS prioritize high ATK weapons.
    """
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
            
            # Prioritize higher ATK bonus
            if priority > best_priority or (priority == best_priority and bonus > best_bonus):
                best = item
                best_bonus = bonus
                best_priority = priority
    
    if best:
        log.info("⚔️ EQUIP BEST WEAPON: %s (+%d ATK) [Priority: %d -> %d]", 
                best.get('typeId', 'weapon'), best_bonus, current_priority, best_priority)
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"⚔️ MAX ATK: {best.get('typeId', 'weapon')} (+{best_bonus} ATK)"}
    return None


def _should_upgrade_weapon(current_weapon: dict, available_weapons: list) -> bool:
    """Check if there's a better weapon available."""
    current_priority = get_weapon_priority(current_weapon) if current_weapon else 0
    
    for weapon in available_weapons:
        if not isinstance(weapon, dict):
            continue
        w_type = weapon.get("typeId", "").lower()
        w_priority = WEAPONS.get(w_type, {}).get("priority", 0)
        if w_priority > current_priority:
            return True
    return False


# =========================
# 🧠 SMOLTZ FARMING (v2.0.0 - SMOLTZ first, weakest enemy first)
# =========================

def _pickup_smoltz_first(items: list, inventory: list, region_id: str) -> dict | None:
    """PICKUP SMOLTZ FIRST - Highest priority in v2.0.0."""
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None
    
    # Currency items = SMOLTZ - ABSOLUTE HIGHEST PRIORITY
    currency_items = [i for i in local_items 
                      if i.get("typeId", "").lower() == "rewards" 
                      or i.get("category", "").lower() == "currency"]
    
    if currency_items:
        best_currency = max(currency_items, key=lambda i: i.get("amount", 1))
        amount = best_currency.get("amount", 50)
        log.info("💰💰💰 SMOLTZ PICKUP! +%d sMoltz (Priority #1!)", amount)
        return {"action": "pickup", "data": {"itemId": best_currency["id"]},
                "reason": f"💰 SMOLTZ FIRST: +{amount} sMoltz!"}
    
    # Then high ATK weapons
    high_value_items = [i for i in local_items if i.get("category") == "weapon"]
    if high_value_items:
        high_value_items.sort(key=lambda i: WEAPONS.get(i.get("typeId", "").lower(), {}).get("priority", 0), reverse=True)
        best = high_value_items[0]
        w_type = best.get("typeId", "unknown")
        log.info("🎯 WEAPON PICKUP: %s (increases ATK!)", w_type)
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": f"WEAPON: {w_type} (ATK up!)"}
    
    return None


def _select_weakest_enemy_first(enemies: list) -> dict | None:
    """Select enemy with LOWEST HP first.
    v2.0.0: Primary combat strategy - quick kills = more sMoltz!
    """
    if not enemies:
        return None
    
    alive_enemies = [e for e in enemies if e.get("hp", 0) > 0]
    if not alive_enemies:
        return None
    
    # Sort by HP ascending (weakest first)
    weakest = min(alive_enemies, key=lambda e: e.get("hp", 999))
    log.debug("🎯 WEAKEST FIRST: Enemy HP=%d", weakest.get("hp", 0))
    return weakest


def _select_weakest_guardian_first(guardians: list) -> dict | None:
    """Select guardian with LOWEST HP first for quick 120 sMoltz."""
    if not guardians:
        return None
    
    alive_guardians = [g for g in guardians if g.get("hp", 0) > 0]
    if not alive_guardians:
        return None
    
    weakest = min(alive_guardians, key=lambda g: g.get("hp", 999))
    log.info("🎯 GUARDIAN WEAKEST FIRST: HP=%d (120 sMoltz!)", weakest.get("hp", 0))
    return weakest


def _select_weakest_monster_first(monsters: list) -> dict | None:
    """Select monster with LOWEST HP first for efficient farming."""
    if not monsters:
        return None
    
    alive_monsters = [m for m in monsters if m.get("hp", 0) > 0]
    if not alive_monsters:
        return None
    
    weakest = min(alive_monsters, key=lambda m: m.get("hp", 999))
    return weakest


def estimate_combat_outcome(my_hp, my_atk, my_def, my_weapon_bonus,
                            enemy_hp, enemy_atk, enemy_def, enemy_weapon_bonus,
                            weather) -> dict:
    """Estimate combat outcome with TTK calculation."""
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


def _find_healing_item(inventory: list, critical: bool = False, prefer_small: bool = False) -> dict | None:
    """Find best healing item."""
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


def _get_move_ep_cost(terrain: str, weather: str) -> int:
    """Calculate move EP cost."""
    if terrain == "water":
        return 3
    if weather == "storm":
        return 3
    return 2


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    """Estimate enemy's weapon bonus."""
    weapon = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find nearest connected region that's NOT a death zone."""
    for conn in connections:
        if isinstance(conn, str):
            if conn not in danger_ids:
                return conn
        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            is_dz = conn.get("isDeathZone", False)
            if rid and not is_dz and rid not in danger_ids:
                return rid
    return None


def _track_smoltz_gain(view: dict, my_id: str):
    """Track sMoltz gains from kills and pickups."""
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
            log.info("💰💰💰 GUARDIAN KILL! +%d sMoltz (Total: %d)", 
                    GUARDIAN_SMOLTZ_REWARD, _total_smoltz_collected)
        elif "killed" in msg and "player" in msg:
            _farming_stats["players_killed"] = _farming_stats.get("players_killed", 0) + 1
            _total_smoltz_collected += PLAYER_KILL_SMOLTZ
            _farming_stats["total_smoltz"] = _farming_stats.get("total_smoltz", 0) + PLAYER_KILL_SMOLTZ
            log.info("💰 PLAYER KILL! +%d sMoltz (Total: %d)", 
                    PLAYER_KILL_SMOLTZ, _total_smoltz_collected)


# =========================
# 🧠 MAIN DECISION FUNCTION
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine - ULTIMATE SMOLTZ FARMING MODE v2.0.0
    
    PRIORITY CHAIN:
    1. DEATHZONE ESCAPE (survival first)
    2. HP MAINTENANCE (keep >60%)
    3. EP MAINTENANCE (keep >60%)
    4. PICKUP SMOLTZ (highest priority!)
    5. EQUIP BEST WEAPON (max ATK)
    6. GUARDIAN FARMING (weakest first - 120 sMoltz!)
    7. FINISH LOW HP ENEMIES (quick kills)
    8. WEAKEST ENEMY FIRST (primary combat)
    9. WEAKEST MONSTER FARMING
    10. MOVE TO RICH REGIONS
    """
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
    
    # Track sMoltz gains
    _track_smoltz_gain(view, self_data.get("id", ""))
    
    # Current ATK status
    current_atk_bonus = get_weapon_bonus(equipped)
    total_atk = atk + current_atk_bonus
    
    # View fields
    visible_agents = view.get("visibleAgents", [])
    visible_monsters = view.get("visibleMonsters", [])
    visible_items_raw = view.get("visibleItems", [])
    
    # Unwrap items
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
    hp_ratio = hp / 100 if hp else 1.0
    
    enemies_nearby = [a for a in visible_agents 
                     if a.get("regionId") == region_id 
                     and a.get("isAlive") 
                     and not a.get("isGuardian", False)
                     and a.get("id") != self_data.get("id")]
    
    in_deathzone = region.get("isDeathZone", False) or region_id in danger_ids
    
    # Log current status periodically
    log.debug("📊 STATUS: HP=%d(%.0f%%) EP=%d/%d(%.0f%%) ATK=%d+%d=%d", 
              hp, hp_ratio*100, ep, max_ep, ep_ratio*100, atk, current_atk_bonus, total_atk)
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 1: DEATHZONE ESCAPE (overrides everything!)
    # ═══════════════════════════════════════════════════════════════
    if in_deathzone:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨🚨🚨 DEATHZONE! Escaping to %s (HP=%d)", safe, hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE: Must survive!"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 2: HP MAINTENANCE (keep above 60%)
    # ═══════════════════════════════════════════════════════════════
    hp_action = _manage_hp(hp, inventory, bool(enemies_nearby), in_deathzone)
    if hp_action:
        return hp_action
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 3: EP MAINTENANCE (keep above 60%)
    # ═══════════════════════════════════════════════════════════════
    ep_action = _manage_ep(ep, max_ep, inventory, bool(enemies_nearby), 
                           in_deathzone, danger_ids, connections, view)
    if ep_action:
        return ep_action
    
    # ═══════════════════════════════════════════════════════════════
    # FREE ACTIONS (no cooldown)
    # ═══════════════════════════════════════════════════════════════
    
    # PRIORITY 4: PICKUP SMOLTZ FIRST!
    pickup_action = _pickup_smoltz_first(visible_items, inventory, region_id)
    if pickup_action:
        return pickup_action
    
    # PRIORITY 5: EQUIP BEST WEAPON (max ATK)
    equip_action = _check_equip_best_weapon(inventory, equipped)
    if equip_action:
        return equip_action
    
    # If cooldown active, only free actions allowed
    if not can_act:
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # COMBAT & FARMING ACTIONS (require EP)
    # ═══════════════════════════════════════════════════════════════
    
    # Check if we have enough EP to fight
    ep_status = _check_ep_status(ep, max_ep)
    can_fight = ep_status["can_fight"] and hp >= FARMING_HP_MIN
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 6: GUARDIAN FARMING (120 sMoltz - WEAKEST FIRST!)
    # ═══════════════════════════════════════════════════════════════
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    
    if guardians and can_fight:
        target = _select_weakest_guardian_first(guardians)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                enemy_hp = target.get("hp", 100)
                # Fight if we can win OR guardian is low HP
                if enemy_hp < 50 or total_atk >= 30:
                    log.info("🎯🎯🎯 GUARDIAN HUNT! +120 sMoltz! Enemy HP=%d, My ATK=%d", enemy_hp, total_atk)
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"💰💰💰 GUARDIAN: 120 sMoltz! HP={enemy_hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 7: FINISH LOW HP ENEMIES (quick kills)
    # ═══════════════════════════════════════════════════════════════
    low_hp_enemies = [a for a in visible_agents
                      if not a.get("isGuardian", False) 
                      and a.get("isAlive", True)
                      and a.get("id") != self_data.get("id")
                      and a.get("hp", 100) < LOW_HP_FINISH_THRESHOLD]
    
    if low_hp_enemies and can_fight:
        target = _select_weakest_enemy_first(low_hp_enemies)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("🔪🔪🔪 FINISHING BLOW! Enemy HP=%d, easy +%d sMoltz!", 
                        target.get("hp", 0), PLAYER_KILL_SMOLTZ)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"FINISH: Low HP enemy! +{PLAYER_KILL_SMOLTZ} sMoltz"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 8: WEAKEST ENEMY FIRST (primary combat strategy)
    # ═══════════════════════════════════════════════════════════════
    enemies = [a for a in visible_agents
               if not a.get("isGuardian", False) and a.get("isAlive", True)
               and a.get("id") != self_data.get("id")]
    
    if enemies and can_fight and hp >= 40:
        target = _select_weakest_enemy_first(enemies)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                enemy_hp = target.get("hp", 100)
                outcome = estimate_combat_outcome(
                    hp, atk, defense, current_atk_bonus,
                    enemy_hp, target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather
                )
                if outcome["win"] or enemy_hp < 50:
                    log.info("🎯 WEAKEST FIRST: Enemy HP=%d, My ATK=%d", enemy_hp, total_atk)
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"WEAKEST FIRST: HP={enemy_hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 9: WEAKEST MONSTER FARMING
    # ═══════════════════════════════════════════════════════════════
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and can_fight and hp >= 25:
        target = _select_weakest_monster_first(monsters)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                monster_hp = target.get("hp", 100)
                log.info("🐾 WEAKEST MONSTER: HP=%d", monster_hp)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": f"MONSTER FARM: HP={monster_hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 10: HEALING (if HP below 60% and no enemies)
    # ═══════════════════════════════════════════════════════════════
    if hp < HP_SAFE_THRESHOLD and not enemies_nearby and not in_deathzone:
        heal = _find_healing_item(inventory, critical=(hp < 30), prefer_small=(hp < 50))
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HP MAINTENANCE: {hp} -> target >60%"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 11: MOVE TO FIND MORE SMOLTZ
    # ═══════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        # Try to move to region with guardians or items
        move_target = None
        
        # Look for regions with guardians
        for conn in connections:
            rid = _get_region_id(conn)
            if rid and rid not in danger_ids:
                for agent in visible_agents:
                    if agent.get("regionId") == rid and agent.get("isGuardian"):
                        move_target = rid
                        log.info("🎯 Moving to guardian region: %s", rid[:8])
                        break
                if move_target:
                    break
        
        if not move_target:
            # Fallback: any safe region
            move_target = _find_safe_region(connections, danger_ids, view)
        
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "EXPLORE: Hunting for sMoltz"}
    
    # ═══════════════════════════════════════════════════════════════
    # LAST RESORT: REST (recover EP)
    # ═══════════════════════════════════════════════════════════════
    if ep < 4 and not enemies_nearby and not in_deathzone:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}, preparing for more farming"}
    
    return None


"""
================================================================================
v2.0.0 - ULTIMATE SMOLTZ FARMING BOT
================================================================================

KEY FEATURES:
-------------
1. HIGH ATK PRIORITY:
   - Always equip the best weapon available
   - Prioritize weapon pickups (Katana > Sniper > Sword)
   - Fight only when ATK is sufficient

2. HP/EP MAINTENANCE (always above 60%):
   - Auto-heal when HP < 60%
   - Auto-rest or use energy drink when EP < 60%
   - Critical recovery when HP < 30% or EP < 30%

3. SMOLTZ FIRST:
   - Pickup currency items IMMEDIATELY (highest priority)
   - Hunt guardians (120 sMoltz each) - weakest first
   - Finish low HP players (100 sMoltz)

4. WEAKEST ENEMY FIRST:
   - Primary combat strategy: target lowest HP enemies
   - Quick kills = more sMoltz per hour
   - Less EP wasted on prolonged fights

PRIORITY CHAIN:
--------------
1. DEATHZONE ESCAPE (survival)
2. HP MAINTENANCE (>60%)
3. EP MAINTENANCE (>60%)
4. PICKUP SMOLTZ
5. EQUIP BEST WEAPON
6. GUARDIAN FARMING (weakest)
7. FINISH LOW HP ENEMIES
8. WEAKEST ENEMY FIRST
9. MONSTER FARMING (weakest)
10. MOVE FOR SMOLTZ
11. REST

EXPORTED FUNCTIONS:
------------------
- decide_action() - Main decision engine
- reset_game_state() - Reset per-game tracking
- learn_from_map() - Learn map layout
================================================================================
"""
