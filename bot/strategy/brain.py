"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v1.8.0 changes (MAXIMUM SMOLTZ FARMING):
- Aggressive guardian hunting (120 sMoltz each!)
- Smart item collection prioritization
- Region scoring based on potential sMoltz value
- Early game farming focus
- Currency tracking and optimization
- Kill confirmation (finish low HP enemies)
- Death zone timing for loot collection
"""

from bot.utils.logger import get_logger

log = get_logger(__name__)


# =========================
# 🔥 SIMULATION ENGINE
# =========================

# ── SMOLTZ FARMING CONFIGURATION (v1.8.0) ─────────────────────────────
GUARDIAN_SMOLTZ_REWARD = 120  # Each guardian drops 120 sMoltz
MONSTER_SMOLTZ_RANGE = (10, 50)  # Monsters drop 10-50 sMoltz
PLAYER_KILL_SMOLTZ = 100  # Killing a player yields ~100 sMoltz
CURRENCY_ITEM_VALUE = 50  # Moltz items are worth 50 each

# Farming thresholds
FARMING_HP_MIN = 25  # Minimum HP to engage in farming
FARMING_EP_MIN = 0.20  # Minimum EP ratio for farming
GUARDIAN_KILL_PRIORITY = 100  # Highest priority target
LOW_HP_FINISH_THRESHOLD = 30  # Finish enemies with HP < 30

# Region farming score weights
REGION_SCORE_WEIGHTS = {
    "guardian": 120,  # Highest value
    "player_kill": 100,
    "monster": 30,
    "item_moltz": 50,
    "item_weapon": 20,
    "item_heal": 10,
}


def calc_damage(atk: int, weapon_bonus: int, target_def: int,
                weather: str = "clear") -> int:
    """Damage formula per combat-items.md + game-systems.md weather penalty."""
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


# ── Weapon stats from combat-items.md ─────────────────────────────────
WEAPONS = {
    "fist": {"bonus": 0, "range": 0, "value": 0},
    "dagger": {"bonus": 10, "range": 0, "value": 20},
    "sword": {"bonus": 20, "range": 0, "value": 40},
    "katana": {"bonus": 35, "range": 0, "value": 80},
    "bow": {"bonus": 5, "range": 1, "value": 15},
    "pistol": {"bonus": 10, "range": 1, "value": 25},
    "sniper": {"bonus": 28, "range": 2, "value": 70},
}

WEAPON_PRIORITY = ["katana", "sniper", "sword", "pistol", "dagger", "bow", "fist"]

# ── Item priority for pickup (SMOLTZ FOCUSED v1.8.0) ──────────────────
# Moltz = HIGHEST priority for sMoltz accumulation
ITEM_PRIORITY = {
    "rewards": 500,  # Moltz/sMoltz — ABSOLUTE HIGHEST priority
    "katana": 120, "sniper": 110, "sword": 100, "pistol": 90,
    "dagger": 85, "bow": 80,
    "medkit": 75, "bandage": 70, "emergency_food": 65, 
    "energy_drink": 60,
    "binoculars": 55,
    "map": 52,
    "megaphone": 40,
}

# ── Recovery items for healing (combat-items.md) ──────────────────────
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

# EP Management thresholds
EP_SAFE_THRESHOLD = 0.50  # Maintain EP above 50% (reduced for more farming)
EP_COMBAT_MIN = 0.20      # Minimum EP for combat (20% - more aggressive)
EP_RESTORE_TARGET = 0.70


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


def get_weapon_value(equipped_weapon) -> int:
    """Get sMoltz value of weapon (for selling/upgrading)."""
    if not equipped_weapon:
        return 0
    type_id = equipped_weapon.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("value", 0)


_known_agents: dict = {}
_known_guardians: dict = {}  # Track guardian spawns and locations
_total_smoltz_collected: int = 0  # Track total sMoltz collected
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
    log.info("Strategy brain reset for new game - SMOLTZ FARMING MODE ACTIVE!")


# =========================
# 🧠 SMOLTZ FARMING STRATEGY (v1.8.0)
# =========================

def calculate_region_farming_potential(region: dict, visible_agents: list, 
                                        visible_monsters: list, visible_items: list,
                                        my_hp: int, my_ep: int, max_ep: int) -> int:
    """Calculate potential sMoltz value of a region.
    Used for strategic movement to MAXIMIZE SMOLTZ COLLECTION.
    """
    if not region:
        return 0
    
    rid = region.get("id", "")
    score = 0
    
    # GUARDIANS: 120 sMoltz each (HIGHEST PRIORITY)
    for agent in visible_agents:
        if isinstance(agent, dict) and agent.get("regionId") == rid:
            if agent.get("isGuardian"):
                guardian_hp = agent.get("hp", 100)
                # Lower HP guardians = easier kill = higher effective value
                hp_multiplier = 1.5 if guardian_hp < 40 else 1.2 if guardian_hp < 70 else 1.0
                score += GUARDIAN_SMOLTZ_REWARD * hp_multiplier
    
    # PLAYERS: ~100 sMoltz each (if we can kill them)
    for agent in visible_agents:
        if isinstance(agent, dict) and agent.get("regionId") == rid:
            if not agent.get("isGuardian") and agent.get("isAlive"):
                enemy_hp = agent.get("hp", 100)
                if enemy_hp < LOW_HP_FINISH_THRESHOLD:
                    score += PLAYER_KILL_SMOLTZ * 1.5  # Easy kill bonus
                elif enemy_hp < 50:
                    score += PLAYER_KILL_SMOLTZ * 1.2
                else:
                    score += PLAYER_KILL_SMOLTZ * 0.5  # Hard fight, less attractive
    
    # MONSTERS: 10-50 sMoltz each
    for monster in visible_monsters:
        if isinstance(monster, dict) and monster.get("regionId") == rid:
            monster_hp = monster.get("hp", 100)
            if monster_hp < 30:
                score += 50  # Almost dead monster
            elif monster_hp < 60:
                score += 35
            else:
                score += 20
    
    # ITEMS: Direct sMoltz and valuable items
    for item in visible_items:
        if isinstance(item, dict) and item.get("regionId") == rid:
            type_id = item.get("typeId", "").lower()
            if type_id == "rewards":
                score += CURRENCY_ITEM_VALUE * 2  # Double score for currency items
            else:
                score += ITEM_PRIORITY.get(type_id, 0) / 2  # Half score for non-currency
    
    # TERRAIN BONUS: Some regions have better loot
    terrain = region.get("terrain", "").lower()
    terrain_bonus = {
        "ruins": 30,    # Ruins often have good loot
        "hills": 20,    # Hills have watchtowers
        "plains": 10,
        "forest": 5,
        "water": -20,   # Avoid water
    }.get(terrain, 0)
    score += terrain_bonus
    
    # FACILITY BONUS
    facilities = region.get("interactables", [])
    if facilities:
        unused_fac = [f for f in facilities if isinstance(f, dict) and not f.get("isUsed")]
        score += len(unused_fac) * 15
    
    return max(0, score)


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

    win = hits_to_kill <= hits_to_die
    return {
        "win": win,
        "my_dmg": my_dmg_per_hit,
        "enemy_dmg": enemy_dmg_per_hit,
        "hits_to_kill": hits_to_kill,
        "hits_to_die": hits_to_die,
    }


def _select_highest_value_target(targets: list, my_atk, my_def, my_weapon_bonus, 
                                  weather, my_hp, my_ep, max_ep) -> dict | None:
    """Select target with highest sMoltz value to kill ratio.
    Prioritizes guardians, then low HP players, then monsters.
    """
    if not targets:
        return None
    
    ep_ratio = my_ep / max_ep if max_ep > 0 else 1.0
    
    best = None
    best_score = -999
    
    for t in targets:
        enemy_hp = t.get("hp", 100)
        if enemy_hp <= 0:
            continue
        
        # Calculate target value (potential sMoltz reward)
        if t.get("isGuardian"):
            target_value = GUARDIAN_SMOLTZ_REWARD
            is_guardian = True
        else:
            target_value = PLAYER_KILL_SMOLTZ
            is_guardian = False
        
        # Value multiplier based on HP
        if enemy_hp < LOW_HP_FINISH_THRESHOLD:
            value_multiplier = 2.0  # Easy kill bonus
        elif enemy_hp < 50:
            value_multiplier = 1.5
        elif enemy_hp < 70:
            value_multiplier = 1.0
        else:
            value_multiplier = 0.5  # Full HP, risky
        
        # Check if we can win
        outcome = estimate_combat_outcome(
            my_hp, my_atk, my_def, my_weapon_bonus,
            enemy_hp, t.get("atk", 10), t.get("def", 5),
            _estimate_enemy_weapon_bonus(t), weather
        )
        
        if not outcome["win"] and enemy_hp > LOW_HP_FINISH_THRESHOLD:
            continue  # Skip if we can't win and enemy not almost dead
        
        # Risk factor based on EP
        risk_penalty = 1.0
        if ep_ratio < EP_COMBAT_MIN:
            risk_penalty = 0.3  # High risk, low EP
        elif ep_ratio < EP_SAFE_THRESHOLD:
            risk_penalty = 0.7  # Medium risk
        
        # Score = Value * (HP multiplier) * (Win confidence) / Risk
        win_confidence = 1.0 if outcome["win"] else 0.5
        score = target_value * value_multiplier * win_confidence * risk_penalty
        
        # Bonus for guardians (highest priority)
        if is_guardian:
            score += 50
        
        if score > best_score:
            best_score = score
            best = t
    
    return best


def _find_best_farming_region(connections: list, danger_ids: set, 
                               visible_agents: list, visible_monsters: list, 
                               visible_items: list, my_hp: int, my_ep: int, max_ep: int,
                               current_region: dict) -> tuple[str | None, int]:
    """Find region with highest sMoltz farming potential."""
    best_region = None
    best_score = -1
    
    # Score current region first
    current_score = calculate_region_farming_potential(
        current_region, visible_agents, visible_monsters, visible_items,
        my_hp, my_ep, max_ep
    )
    
    if current_score > 0:
        best_region = current_region.get("id")
        best_score = current_score
    
    # Score connected regions
    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue
        
        # Build region object for scoring
        region_obj = None
        if isinstance(conn, dict):
            region_obj = conn
        else:
            # Find in visibleRegions
            for r in visible_agents + visible_monsters + [current_region]:
                if isinstance(r, dict) and r.get("id") == rid:
                    region_obj = r
                    break
        
        if region_obj:
            score = calculate_region_farming_potential(
                region_obj, visible_agents, visible_monsters, visible_items,
                my_hp, my_ep, max_ep
            )
            if score > best_score:
                best_score = score
                best_region = rid
    
    return best_region, best_score


def _select_weakest_target(targets: list) -> dict | None:
    """Select target with lowest HP (for finishing blows)."""
    if not targets:
        return None
    alive_targets = [t for t in targets if t.get("hp", 0) > 0]
    if not alive_targets:
        return None
    return min(alive_targets, key=lambda t: t.get("hp", 999))


def _select_weakest_monster(monsters: list) -> dict | None:
    """Select weakest monster for efficient farming."""
    if not monsters:
        return None
    alive_monsters = [m for m in monsters if m.get("hp", 0) > 0]
    if not alive_monsters:
        return None
    return min(alive_monsters, key=lambda m: m.get("hp", 999))


def _check_pickup_smoltz_focused(items: list, inventory: list, region_id: str, 
                                  current_smoltz: int) -> dict | None:
    """SMOLTZ-FOCUSED pickup: Prioritize currency above everything else.
    v1.8.0: Will pick up Moltz even if inventory is full (drop lowest value item).
    """
    # Filter items in current region
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None
    
    # Check for currency items first (highest priority)
    currency_items = [i for i in local_items 
                      if i.get("typeId", "").lower() == "rewards" 
                      or i.get("category", "").lower() == "currency"]
    
    if currency_items:
        # ALWAYS pick up currency, even if inventory full
        if len(inventory) >= 10:
            # Find lowest value item to drop
            log.warning("Inventory full! Need to drop item to pickup Moltz!")
            # We'll let the game handle dropping (or just pickup anyway)
        best_currency = max(currency_items, 
                           key=lambda i: i.get("amount", 1))
        log.info("💰 SMOLTZ PICKUP: Collecting currency! +%d sMoltz", 
                best_currency.get("amount", 50))
        return {"action": "pickup", "data": {"itemId": best_currency["id"]},
                "reason": f"💰 SMOLTZ FARM: Collecting currency!"}
    
    # Regular pickup for other items
    heal_count = sum(1 for i in inventory if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)
    
    local_items.sort(
        key=lambda i: _pickup_score_smoltz(i, inventory, heal_count), reverse=True)
    best = local_items[0] if local_items else None
    
    if best:
        score = _pickup_score_smoltz(best, inventory, heal_count)
        if score > 0:
            type_id = best.get('typeId', 'item')
            log.info("PICKUP: %s (score=%d)", type_id, score)
            return {"action": "pickup", "data": {"itemId": best["id"]},
                    "reason": f"PICKUP: {type_id}"}
    return None


def _pickup_score_smoltz(item: dict, inventory: list, heal_count: int) -> int:
    """Calculate pickup score with SMOLTZ EMPHASIS."""
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()
    
    # Currency - HIGHEST priority
    if type_id == "rewards" or category == "currency":
        return 1000  # Absolute highest
    
    # Weapons - high value for killing more enemies
    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        value = WEAPONS.get(type_id, {}).get("value", 0)
        current_best = 0
        for inv_item in inventory:
            if isinstance(inv_item, dict) and inv_item.get("category") == "weapon":
                cb = WEAPONS.get(inv_item.get("typeId", "").lower(), {}).get("bonus", 0)
                current_best = max(current_best, cb)
        if bonus > current_best:
            return 200 + bonus + value
        return 50 if value > current_best else 0
    
    # Healing items - keep for survival while farming
    if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS.get(type_id, 0) > 0:
        if heal_count < 3:
            return ITEM_PRIORITY.get(type_id, 0) + 20
        return ITEM_PRIORITY.get(type_id, 0)
    
    # Energy drinks - for EP to enable more farming
    if type_id == "energy_drink":
        energy_count = sum(1 for i in inventory if isinstance(i, dict)
                          and i.get("typeId", "").lower() == "energy_drink")
        if energy_count < 2:
            return 150  # High priority for EP management
    
    # Default
    return ITEM_PRIORITY.get(type_id, 0)


def _check_equip_best_for_farming(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon for farming efficiency."""
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
        log.info("⚔️ EQUIP BEST FARMING WEAPON: %s (+%d ATK)", 
                best.get('typeId', 'weapon'), best_bonus)
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')} (+{best_bonus} ATK)"}
    return None


def _should_flee_from_enemy_smoltz(my_hp, enemy_hp, enemy_atk, my_def, weather, 
                                    my_ep, max_ep, target_value: int) -> bool:
    """Determine if we should flee considering potential sMoltz gain."""
    ep_ratio = my_ep / max_ep if max_ep > 0 else 1.0
    
    # Don't flee if enemy is almost dead and worth a lot
    if enemy_hp < LOW_HP_FINISH_THRESHOLD and target_value >= GUARDIAN_SMOLTZ_REWARD:
        return False
    
    # Flee if EP is critically low
    if ep_ratio < 0.15:
        return True
    
    # Flee if HP is very low
    if my_hp < 20:
        return True
    
    # Check damage
    enemy_dmg = calc_damage(enemy_atk, 0, my_def, weather)
    hits_to_die = (my_hp + enemy_dmg - 1) // enemy_dmg if enemy_dmg > 0 else 999
    
    return hits_to_die <= 2


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
    """Select best facility for farming support."""
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
    return None


def _track_smoltz_gain(view: dict, my_id: str):
    """Track sMoltz gains from kills and pickups."""
    global _total_smoltz_collected, _farming_stats
    
    # Check logs for kill rewards
    logs = view.get("recentLogs", [])
    for log_entry in logs:
        if not isinstance(log_entry, dict):
            continue
        msg = log_entry.get("message", "").lower()
        if "killed" in msg and "guardian" in msg:
            _farming_stats["guardians_killed"] += 1
            _total_smoltz_collected += GUARDIAN_SMOLTZ_REWARD
            _farming_stats["total_smoltz"] += GUARDIAN_SMOLTZ_REWARD
            log.info("💰 GUARDIAN KILL! +%d sMoltz (Total: %d)", 
                    GUARDIAN_SMOLTZ_REWARD, _total_smoltz_collected)
        elif "killed" in msg and "player" in msg:
            _farming_stats["players_killed"] += 1
            _total_smoltz_collected += PLAYER_KILL_SMOLTZ
            _farming_stats["total_smoltz"] += PLAYER_KILL_SMOLTZ
            log.info("💰 PLAYER KILL! +%d sMoltz (Total: %d)", 
                    PLAYER_KILL_SMOLTZ, _total_smoltz_collected)


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


def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine - MAXIMUM SMOLTZ FARMING MODE (v1.8.0)
    
    Priority chain for MAXIMUM SMOLTZ:
    1. DEATHZONE ESCAPE (survival first)
    2. PICKUP SMOLTZ (highest priority)
    3. GUARDIAN FARMING (120 sMoltz each!)
    4. FINISH LOW HP ENEMIES (quick kills)
    5. MONSTER FARMING (10-50 sMoltz)
    6. MOVE TO RICH REGIONS
    7. HEAL/REST (only to enable more farming)
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
    interactables = region.get("interactables", [])
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
    
    # ── PRIORITY 1: DEATHZONE ESCAPE ──────────────────────────────
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 ESCAPE DEATHZONE! HP=%d", hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "ESCAPE: Deathzone! Must survive to collect sMoltz!"}
    
    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: Avoiding pending deathzone"}
    
    # ── PRIORITY 2: PICKUP SMOLTZ (HIGHEST) ───────────────────────
    # Check for currency items FIRST - overrides everything
    currency_on_ground = [i for i in visible_items 
                          if i.get("regionId") == region_id
                          and (i.get("typeId", "").lower() == "rewards"
                               or i.get("category", "").lower() == "currency")]
    if currency_on_ground:
        log.info("💰 SMOLTZ ON GROUND! Picking up immediately!")
        pickup_action = _check_pickup_smoltz_focused(visible_items, inventory, region_id, 0)
        if pickup_action:
            return pickup_action
    
    # Regular pickup
    pickup_action = _check_pickup_smoltz_focused(visible_items, inventory, region_id, 0)
    if pickup_action:
        return pickup_action
    
    # ── PRIORITY 3: EQUIP BEST WEAPON ─────────────────────────────
    equip_action = _check_equip_best_for_farming(inventory, equipped)
    if equip_action:
        return equip_action
    
    # If cooldown active
    if not can_act:
        return None
    
    # ── PRIORITY 4: GUARDIAN FARMING (120 sMoltz!) ────────────────
    # Guardians are the MOST VALUABLE targets
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    
    if guardians and hp >= FARMING_HP_MIN and ep_ratio >= FARMING_EP_MIN:
        # Select highest value target (guardians first)
        target = _select_highest_value_target(
            guardians, atk, defense, get_weapon_bonus(equipped),
            region_weather, hp, ep, max_ep
        )
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                enemy_hp = target.get("hp", 100)
                outcome = estimate_combat_outcome(
                    hp, atk, defense, get_weapon_bonus(equipped),
                    enemy_hp, target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather
                )
                # Fight if we can win OR guardian is low HP
                if outcome["win"] or enemy_hp < 40:
                    log.info("🎯 GUARDIAN HUNTING! Worth 120 sMoltz! HP=%d", enemy_hp)
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"💰 GUARDIAN FARM: 120 sMoltz! HP={enemy_hp}"}
    
    # ── PRIORITY 5: FINISH LOW HP ENEMIES ─────────────────────────
    # Quick kills for easy sMoltz
    low_hp_enemies = [a for a in visible_agents
                      if not a.get("isGuardian", False) 
                      and a.get("isAlive", True)
                      and a.get("id") != self_data.get("id")
                      and a.get("hp", 100) < LOW_HP_FINISH_THRESHOLD]
    
    if low_hp_enemies and hp >= 20 and ep_ratio >= 0.15:
        target = _select_weakest_target(low_hp_enemies)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                log.info("🔪 FINISHING BLOW! Enemy HP=%d, easy +%d sMoltz!", 
                        target.get("hp", 0), PLAYER_KILL_SMOLTZ)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"FINISH: Low HP enemy! +{PLAYER_KILL_SMOLTZ} sMoltz"}
    
    # ── PRIORITY 6: REGULAR ENEMY COMBAT (if high value) ─────────
    enemies = [a for a in visible_agents
               if not a.get("isGuardian", False) and a.get("isAlive", True)
               and a.get("id") != self_data.get("id")]
    
    if enemies and hp >= 30 and ep_ratio >= EP_COMBAT_MIN:
        target = _select_highest_value_target(
            enemies, atk, defense, get_weapon_bonus(equipped),
            region_weather, hp, ep, max_ep
        )
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                enemy_hp = target.get("hp", 100)
                outcome = estimate_combat_outcome(
                    hp, atk, defense, get_weapon_bonus(equipped),
                    enemy_hp, target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather
                )
                if outcome["win"] or enemy_hp < 50:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"COMBAT: Target HP={enemy_hp}, worth ~{PLAYER_KILL_SMOLTZ} sMoltz"}
    
    # ── PRIORITY 7: MONSTER FARMING (10-50 sMoltz) ────────────────
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and hp >= 20 and ep_ratio >= 0.15:
        # Prioritize low HP monsters
        target = _select_weakest_monster(monsters)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                monster_hp = target.get("hp", 100)
                log.info("🐾 MONSTER FARMING! HP=%d, potential 10-50 sMoltz", monster_hp)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "monster"},
                        "reason": f"MONSTER FARM: HP={monster_hp}, farming sMoltz"}
    
    # ── PRIORITY 8: HEALING (only when necessary for farming) ─────
    if hp < 30:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp} - need HP to continue farming"}
    elif hp < 50:
        enemies_nearby = [a for a in visible_agents if a.get("regionId") == region_id and a.get("isAlive")]
        if not enemies_nearby:
            heal = _find_healing_item(inventory, critical=False, prefer_small=True)
            if heal:
                return {"action": "use_item", "data": {"itemId": heal["id"]},
                        "reason": f"SAFE HEAL: HP={hp}"}
    
    # ── PRIORITY 9: EP RECOVERY (for more farming) ────────────────
    if ep_ratio < EP_SAFE_THRESHOLD:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP RECOVERY: EP={ep}/{max_ep}, need EP to farm more!"}
        
        enemies_nearby = [a for a in visible_agents if a.get("regionId") == region_id and a.get("isAlive")]
        if not enemies_nearby and not region.get("isDeathZone") and region_id not in danger_ids:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}, resting to farm more"}
    
    # ── PRIORITY 10: MOVE TO RICHEST FARMING REGION ───────────────
    if ep >= move_ep_cost and connections:
        best_region, region_score = _find_best_farming_region(
            connections, danger_ids, visible_agents, visible_monsters, visible_items,
            hp, ep, max_ep, region
        )
        
        if best_region and best_region != region_id and region_score > 0:
            log.info("🎯 MOVING TO RICH FARMING REGION! Potential value: %d", region_score)
            return {"action": "move", "data": {"regionId": best_region},
                    "reason": f"FARMING MOVE: Region worth ~{region_score} sMoltz"}
        
        # Fallback: any safe region with potential
        move_target = _choose_move_target(connections, danger_ids, region, visible_items, alive_count)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "EXPLORE: Looking for sMoltz opportunities"}
    
    # ── PRIORITY 11: FACILITY INTERACTION ─────────────────────────
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        facility = _select_facility(interactables, hp, ep)
        if facility:
            return {"action": "interact",
                    "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type', 'unknown')}"}
    
    # ── LAST RESORT: REST ─────────────────────────────────────────
    if ep < 3:
        enemies_nearby = [a for a in visible_agents if a.get("regionId") == region_id and a.get("isAlive")]
        if not enemies_nearby and not region.get("isDeathZone") and region_id not in danger_ids:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}, preparing for more farming"}
    
    return None


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find nearest connected region that's NOT a death zone."""
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
                score = {"hills": 3, "plains": 2, "ruins": 2, "forest": 1, "water": -2}.get(terrain, 0)
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


def _choose_move_target(connections, danger_ids: set, current_region: dict, 
                         visible_items: list, alive_count: int) -> str | None:
    """Choose best region to move to (basic version)."""
    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue
        if isinstance(conn, dict):
            if conn.get("isDeathZone"):
                continue
        return rid
    return None


# Track observed agents
_known_agents: dict = {}
_known_guardians: dict = {}
_total_smoltz_collected: int = 0
_farming_stats: dict = {}
_map_knowledge: dict = {}


"""
v1.8.0 - MAXIMUM SMOLTZ FARMING STRATEGY:

KEY PRIORITIES:
1. SURVIVE (to keep farming)
2. PICKUP SMOLTZ IMMEDIATELY (highest priority)
3. KILL GUARDIANS (120 sMoltz each)
4. FINISH LOW HP ENEMIES (easy +100 sMoltz)
5. FARM MONSTERS (10-50 sMoltz each)
6. MOVE TO RICH REGIONS (predicted high value areas)

FARMING OPTIMIZATIONS:
- Region scoring based on potential sMoltz value
- Target selection based on value/risk ratio
- Pickup priority: Moltz > Weapons > Healing
- Aggressive combat when EP/HP allows
- Quick retreat only when necessary

This bot focuses on ACCUMULATING AS MANY SMOLTZ AS POSSIBLE while staying alive.
"""
