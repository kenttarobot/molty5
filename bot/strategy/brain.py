"""
Strategy brain — main decision engine with priority-based action selection.
Implements the game-loop.md priority chain for high win rate.

v1.7.0 changes (EP MANAGEMENT & WEAKEST ENEMY PRIORITY):
- EP management: maintain EP above 60% at all times
- Priority targeting: weakest enemy first (lowest HP)
- Smart EP recovery before engaging in combat
- Balanced farming vs EP preservation
- Rest early when EP drops below threshold
- EP-aware combat decisions
"""
from bot.utils.logger import get_logger

log = get_logger(__name__)


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
# Moltz = ALWAYS pickup (highest). Weapons > healing > utility.
# Binoculars = passive (vision+1 just by holding), always pickup.
ITEM_PRIORITY = {
    "rewards": 300,  # Moltz/sMoltz — ALWAYS pickup first
    "katana": 100, "sniper": 95, "sword": 90, "pistol": 85,
    "dagger": 80, "bow": 75,
    "medkit": 70, "bandage": 65, "emergency_food": 60, "energy_drink": 58,
    "binoculars": 55,  # Passive: vision +1 permanent, always pickup
    "map": 52,          # Use immediately to reveal entire map
    "megaphone": 40,
}

# ── Recovery items for healing (combat-items.md) ──────────────────────
# For normal healing (HP<70): prefer Emergency Food (save Bandage/Medkit)
# For critical healing (HP<30): prefer Bandage then Medkit
RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
    "energy_drink": 0,  # EP restore, not HP
}

# Weather combat penalty per game-systems.md
WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain": 0.05,   # -5%
    "fog": 0.10,    # -10%
    "storm": 0.15,  # -15%
}

# EP Management thresholds (v1.7.0)
EP_SAFE_THRESHOLD = 0.60  # Maintain EP above 60%
EP_COMBAT_MIN = 0.30      # Minimum EP for combat (30%)
EP_RESTORE_TARGET = 0.80  # Target EP when resting (80%)


def calc_damage(atk: int, weapon_bonus: int, target_def: int,
                weather: str = "clear") -> int:
    """Damage formula per combat-items.md + game-systems.md weather penalty.
    Base: ATK + bonus - (DEF * 0.5), min 1.
    Weather: clear=0%, rain=-5%, fog=-10%, storm=-15%.
    """
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
# Map knowledge: track all revealed DZ/pending DZ/safe regions after using Map
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}


def _resolve_region(entry, view: dict):
    """Resolve a connectedRegions entry to a full region object.
    Per v1.5.2 gotchas.md §3: entries are EITHER full Region objects
    (when adjacent region is within vision) OR bare string IDs (when out-of-vision).
    Returns the full object, or None if out-of-vision.
    """
    if isinstance(entry, dict):
        return entry  # Full object
    if isinstance(entry, str):
        # Look up in visibleRegions
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None  # Out-of-vision — only ID is known


def _get_region_id(entry) -> str:
    """Extract region ID from either a string or dict entry."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id", "")
    return ""


def reset_game_state():
    """Reset per-game tracking state. Call when game ends."""
    global _known_agents, _map_knowledge
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    log.info("Strategy brain reset for new game")


# =========================
# 🧠 ENHANCED COMBAT & STRATEGY (v1.7.0 - EP Focus)
# =========================
def estimate_combat_outcome(my_hp, my_atk, my_def, my_weapon_bonus,
                            enemy_hp, enemy_atk, enemy_def, enemy_weapon_bonus,
                            weather) -> dict:
    """Estimate combat outcome with TTK (time to kill) calculation.
    More accurate than simple damage comparison.
    """
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


def _select_weakest_target(targets: list) -> dict | None:
    """Select target with LOWEST HP (v1.7.0 - primary strategy).
    Prioritizes finishing off enemies quickly to reduce incoming damage.
    """
    if not targets:
        return None
    
    # Filter out dead/invalid targets
    alive_targets = [t for t in targets if t.get("hp", 0) > 0]
    if not alive_targets:
        return None
    
    # Sort by HP ascending (weakest first)
    return min(alive_targets, key=lambda t: t.get("hp", 999))


def _select_best_target_for_farming(targets: list, my_atk, my_def, my_weapon_bonus, weather, my_hp, my_ep, max_ep) -> dict | None:
    """Select best target balancing weakest-first with EP efficiency.
    v1.7.0: Prioritizes weakest enemies, but considers EP cost vs reward.
    """
    if not targets:
        return None

    # Sort by HP (weakest first) - this is our primary strategy
    sorted_by_hp = sorted(targets, key=lambda t: t.get("hp", 999))
    
    # Calculate EP efficiency for top candidates
    ep_ratio = my_ep / max_ep if max_ep > 0 else 1.0
    is_ep_critical = ep_ratio < EP_COMBAT_MIN
    
    for t in sorted_by_hp[:5]:  # Check top 5 weakest
        enemy_hp = t.get("hp", 100)
        if enemy_hp <= 0:
            continue
            
        # Estimate kills needed (based on damage)
        my_dmg = max(1, my_atk + my_weapon_bonus - int(t.get("def", 5) * 0.5))
        hits_needed = (enemy_hp + my_dmg - 1) // my_dmg
        
        # EP efficiency score: low HP enemies = high efficiency
        efficiency = (100 - enemy_hp) / max(1, hits_needed)
        
        # If EP is critical, only engage if we can kill in 1-2 hits
        if is_ep_critical and hits_needed > 2:
            continue
            
        # Check if we can win the fight
        outcome = estimate_combat_outcome(
            my_hp, my_atk, my_def, my_weapon_bonus,
            enemy_hp, t.get("atk", 10), t.get("def", 5),
            _estimate_enemy_weapon_bonus(t), weather
        )
        
        # Prioritize fights we can win, especially against weak enemies
        if outcome["win"] or enemy_hp <= my_dmg * 2:  # Can finish in 1-2 hits
            return t
    
    # Fallback: return weakest enemy
    return sorted_by_hp[0] if sorted_by_hp else None


def _select_weakest_monster(monsters: list) -> dict | None:
    """Select weakest monster for efficient farming."""
    if not monsters:
        return None
    alive_monsters = [m for m in monsters if m.get("hp", 0) > 0]
    if not alive_monsters:
        return None
    return min(alive_monsters, key=lambda m: m.get("hp", 999))


def _find_richest_region(connections: list, danger_ids: set,
                         visible_items: list, visible_monsters: list,
                         visible_agents: list) -> str | None:
    """Find adjacent region with most valuable targets (items, monsters, guardians).
    Used for strategic movement to maximize farming efficiency.
    """
    best_region = None
    best_score = -1

    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue

        score = 0

        # Items: high value pickup
        for item in visible_items:
            if isinstance(item, dict) and item.get("regionId") == rid:
                type_id = item.get("typeId", "").lower()
                score += ITEM_PRIORITY.get(type_id, 10)

        # Monsters: easy XP and possible drops
        for mon in visible_monsters:
            if isinstance(mon, dict) and mon.get("regionId") == rid:
                mon_hp = mon.get("hp", 100)
                # Lower HP monsters = better farming targets
                score += 20 if mon_hp < 30 else 12 if mon_hp < 60 else 8

        # Guardians: 120 sMoltz each!
        for ag in visible_agents:
            if isinstance(ag, dict) and ag.get("regionId") == rid and ag.get("isGuardian"):
                guardian_hp = ag.get("hp", 100)
                score += 35 if guardian_hp < 40 else 25

        if score > best_score:
            best_score = score
            best_region = rid

    return best_region


def _should_flee_from_enemy(my_hp, enemy_hp, enemy_atk, my_def, weather, my_ep, max_ep) -> bool:
    """Determine if we should flee from an enemy based on estimated damage and EP.
    v1.7.0: Also flee if EP is too low to fight effectively.
    """
    ep_ratio = my_ep / max_ep if max_ep > 0 else 1.0
    
    # Flee if EP is critically low (can't fight effectively)
    if ep_ratio < EP_COMBAT_MIN:
        return True
    
    # Flee if HP is very low
    if my_hp < 25:
        return True

    enemy_dmg = calc_damage(enemy_atk, 0, my_def, weather)
    hits_to_die = (my_hp + enemy_dmg - 1) // enemy_dmg if enemy_dmg > 0 else 999

    # Flee if we die in 2 hits or less
    return hits_to_die <= 2


def _check_ep_status(ep: int, max_ep: int) -> dict:
    """Check EP status and return recommendations.
    v1.7.0: EP management is critical for sustained farming.
    """
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0
    
    if ep_ratio < EP_COMBAT_MIN:
        return {"status": "critical", "need_rest": True, "can_fight": False}
    elif ep_ratio < EP_SAFE_THRESHOLD:
        return {"status": "low", "need_rest": True, "can_fight": True}
    else:
        return {"status": "good", "need_rest": False, "can_fight": True}


# ── CURSE HANDLING — DISABLED in v1.5.2 ───────────────────────────────
# Curse is temporarily disabled per strategy.md v1.5.2.
# Guardians no longer set victim EP to 0 and no whisper-question/answer flow.
# Legacy code kept below for reference — will re-enable when curse returns.
#
# def _check_curse(messages, my_id) -> dict | None:
#     """DISABLED: Guardian curse is temporarily disabled in v1.5.2."""
#     return None
#
# def _solve_curse_question(question) -> str:
#     """DISABLED: Guardian curse is temporarily disabled in v1.5.2."""
#     return ""


def _check_pickup(items: list, inventory: list, region_id: str) -> dict | None:
    """Smart pickup: weapons > healing stockpile > utility > Moltz (always).
    Max inventory = 10 per limits.md.
    Strategy:
    - Moltz ($rewards): ALWAYS pickup, highest priority
    - Weapons: pickup if better than current OR no weapon equipped
    - Healing: stockpile for endgame (keep at least 2-3 healing items)
    - Binoculars: passive vision+1, always pickup
    - Map: pickup and use immediately
    - Energy Drink: HIGH priority for EP management (v1.7.0)
    """
    if len(inventory) >= 10:
        return None
    # Filter items in current region (items may lack regionId field)
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    # Fallback: if regionId filter found nothing, use all visible items
    # (the game may not set regionId on item objects)
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None

    # Count current healing items for stockpile management
    heal_count = sum(1 for i in inventory if isinstance(i, dict)
                     and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)

    # Sort by priority — Moltz always first, Energy Drink high priority
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
    """Calculate dynamic pickup score based on current inventory state.
    v1.7.0: Energy Drink gets higher priority for EP management.
    """
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()

    # Moltz/sMoltz — ALWAYS pickup
    if type_id == "rewards" or category == "currency":
        return 300

    # Energy Drink - high priority for EP management (v1.7.0)
    if type_id == "energy_drink":
        # Check if we already have energy drinks
        energy_count = sum(1 for i in inventory if isinstance(i, dict)
                          and i.get("typeId", "").lower() == "energy_drink")
        if energy_count < 3:  # Keep up to 3 energy drinks
            return 200  # Very high priority
        return 100

    # Weapons: higher score if no weapon or this is better
    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        # Check current best weapon in inventory
        current_best = 0
        for inv_item in inventory:
            if isinstance(inv_item, dict) and inv_item.get("category") == "weapon":
                cb = WEAPONS.get(inv_item.get("typeId", "").lower(), {}).get("bonus", 0)
                current_best = max(current_best, cb)
        if bonus > current_best:
            return 100 + bonus  # Better weapon = very high priority
        return 0  # Already have equal or better

    # Binoculars: passive vision+1 permanent, always pickup
    if type_id == "binoculars":
        has_binos = any(isinstance(i, dict) and i.get("typeId", "").lower() == "binoculars"
                       for i in inventory)
        return 55 if not has_binos else 0  # Don't stack

    # Map: always pickup (will be used immediately)
    if type_id == "map":
        return 52

    # Healing items: stockpile for endgame (want 3-4 items)
    if type_id in RECOVERY_ITEMS and RECOVERY_ITEMS.get(type_id, 0) > 0:
        if heal_count < 4:  # Need more healing for endgame
            return ITEM_PRIORITY.get(type_id, 0) + 10
        return ITEM_PRIORITY.get(type_id, 0)  # Normal priority

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


def _find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find nearest connected region that's NOT a death zone AND NOT pending DZ.
    Per v1.5.2 gotchas.md §3: connectedRegions entries are EITHER full Region objects
    (when visible) OR bare string IDs (when out-of-vision). Use _resolve_region().
    danger_ids = set of all DZ + pending DZ region IDs.
    """
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
        log.debug("Safe region selected: %s (score=%d, %d candidates)",
                  chosen[:8], safe_regions[0][1], len(safe_regions))
        return chosen

    # Last resort: any non-DZ connection (even if pending)
    for conn in connections:
        rid = conn if isinstance(conn, str) else conn.get("id", "")
        is_dz = conn.get("isDeathZone", False) if isinstance(conn, dict) else False
        if rid and not is_dz:
            log.warning("No fully safe region! Using fallback: %s", rid[:8])
            return rid
    return None


def _find_healing_item(inventory: list, critical: bool = False, prefer_small: bool = False) -> dict | None:
    """Find best healing item based on urgency.
    critical=True (HP<30): prefer Bandage(30) then Medkit(50) — big heals first
    critical=False (HP<70): prefer Emergency Food(20) — save big heals for later
    prefer_small=True: use smallest heal available (for moderate healing)
    """
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
        # Critical: use biggest heal first (Medkit > Bandage > Emergency Food)
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0), reverse=True)
    elif prefer_small:
        # Use smallest heal first (Emergency Food > Bandage > Medkit)
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0))
    else:
        # Default: use biggest heal (for safety)
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0), reverse=True)
    return heals[0]


def _find_energy_drink(inventory: list) -> dict | None:
    """Find energy drink for EP recovery (+5 EP per combat-items.md)."""
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _select_weakest(targets: list) -> dict:
    """Select target with lowest HP."""
    return min(targets, key=lambda t: t.get("hp", 999))


def _is_in_range(target: dict, my_region: str, weapon_range: int,
                  connections=None) -> bool:
    """Check if target is in weapon range.
    Per combat-items.md: melee = same region, ranged = 1-2 regions.
    """
    target_region = target.get("regionId", "")

    # No regionId on target — assume same region (visible agents in same region)
    if not target_region:
        return True

    if target_region == my_region:
        return True  # Same region — melee and ranged both work

    if weapon_range >= 1 and connections:
        # Check if target is in an adjacent region (range 1+)
        adj_ids = set()
        for conn in connections:
            if isinstance(conn, str):
                adj_ids.add(conn)
            elif isinstance(conn, dict):
                adj_ids.add(conn.get("id", ""))
        if target_region in adj_ids:
            return True

    # Target is out of weapon range
    return False


def _select_facility(interactables: list, hp: int, ep: int) -> dict | None:
    """Select best facility to interact with per game-systems.md.
    Facilities: supply_cache, medical_facility, watchtower, broadcast_station, cave.
    v1.7.0: Consider EP needs when selecting facilities.
    """
    for fac in interactables:
        if not isinstance(fac, dict):
            continue
        if fac.get("isUsed"):
            continue
        ftype = fac.get("type", "").lower()
        # Priority: medical (if HP < 80) > supply_cache > watchtower > broadcast_station
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
    """Track observed agents for threat assessment (agent-memory.md temp.knownAgents)."""
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
        }
    # Limit size
    if len(_known_agents) > 50:
        # Remove dead agents first
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int, max_ep: int) -> dict | None:
    """Use utility items immediately after pickup.
    Map: reveals entire map → triggers _learn_from_map next view.
    Binoculars: PASSIVE (vision+1 just by holding) — no use_item needed.
    Energy Drink: Use when EP is below safe threshold (v1.7.0)
    """
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0
    
    for item in inventory:
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId", "").lower()
        # Map: use immediately to reveal entire map
        if type_id == "map":
            log.info("🗺️ Using Map! Will reveal entire map for strategic learning.")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Using Map — reveals entire map for DZ tracking"}
        
        # Energy Drink: use when EP is below safe threshold (v1.7.0)
        if type_id == "energy_drink" and ep_ratio < EP_SAFE_THRESHOLD:
            log.info("⚡ Using Energy Drink! EP=%d/%d (%.0f%%)", ep, max_ep, ep_ratio * 100)
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": f"EP MANAGEMENT: EP={ep}/{max_ep}, restoring +5 EP"}
    
    return None


def learn_from_map(view: dict):
    """Called after Map is used — learn entire map layout.
    Track all death zones, pending DZ, and find safe center regions.
    Per game-guide.md: Map reveals entire map (1-time consumable).
    """
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
            # Count connections — center regions have more connections
            conns = region.get("connections", [])
            terrain = region.get("terrain", "").lower()
            terrain_value = {"hills": 3, "plains": 2, "ruins": 2, "forest": 1, "water": -1}.get(terrain, 0)
            score = len(conns) + terrain_value
            safe_regions.append((rid, score))

    # Sort by connectivity+terrain — highest = most likely center
    safe_regions.sort(key=lambda x: x[1], reverse=True)
    _map_knowledge["safe_center"] = [r[0] for r in safe_regions[:5]]

    log.info("🗺️ MAP LEARNED: %d DZ regions, %d safe regions, top center: %s",
             len(_map_knowledge["death_zones"]),
             len(safe_regions),
             _map_knowledge["safe_center"][:3])


def _choose_move_target(connections, danger_ids: set,
                         current_region: dict, visible_items: list,
                         alive_count: int, visible_monsters: list = None,
                         visible_agents: list = None) -> str | None:
    """Choose best region to move to.
    CRITICAL: NEVER move into a death zone or pending death zone!
    ENHANCED: Prioritize regions with valuable targets (farming).
    v1.7.0: Also consider EP efficiency for farming.
    """
    if visible_monsters is None:
        visible_monsters = []
    if visible_agents is None:
        visible_agents = []

    # First, try to find richest region for farming
    richest = _find_richest_region(connections, danger_ids, visible_items,
                                    visible_monsters, visible_agents)
    if richest:
        log.debug("Moving to richest region: %s", richest[:8])
        return richest

    candidates = []

    # Build set of regions with visible items for attraction
    item_regions = set()
    for item in visible_items:
        if isinstance(item, dict):
            item_regions.add(item.get("regionId", ""))

    for conn in connections:
        if isinstance(conn, str):
            # HARD BLOCK: never move into danger zone
            if conn in danger_ids:
                continue
            score = 1
            if conn in item_regions:
                score += 5
            candidates.append((conn, score))

        elif isinstance(conn, dict):
            rid = conn.get("id", "")
            # HARD BLOCK: never move into DZ or pending DZ
            if not rid or conn.get("isDeathZone") or rid in danger_ids:
                continue

            score = 0
            terrain = conn.get("terrain", "").lower()

            # Terrain scoring per game-systems.md
            terrain_scores = {
                "hills": 4, "plains": 2, "ruins": 2,
                "forest": 1, "water": -3,
            }
            score += terrain_scores.get(terrain, 0)

            if rid in item_regions:
                score += 5

            # Facilities attract
            facs = conn.get("interactables", [])
            if facs:
                unused = [f for f in facs if isinstance(f, dict) and not f.get("isUsed")]
                score += len(unused) * 2

            # Avoid weather penalties
            weather = conn.get("weather", "").lower()
            weather_penalty = {"storm": -2, "fog": -1, "rain": 0, "clear": 1}
            score += weather_penalty.get(weather, 0)

            # Late game: strong bonus for safe regions
            if alive_count < 30:
                score += 3

            # MAP KNOWLEDGE: prefer center regions learned from Map
            if _map_knowledge.get("revealed") and rid in _map_knowledge.get("safe_center", []):
                score += 5  # Strong pull toward center

            # MAP KNOWLEDGE: avoid known death zones
            if rid in _map_knowledge.get("death_zones", set()):
                continue  # HARD BLOCK

            candidates.append((rid, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    Main decision engine. Returns action dict or None (wait).

    Priority chain per game-loop.md §3 (v1.7.0 EP MANAGEMENT & WEAKEST FIRST):
    1. DEATHZONE ESCAPE (overrides everything — 1.34 HP/sec!)
    1b. Pre-escape pending death zone
    2. [DISABLED] Curse resolution — curse temporarily disabled
    2b. Guardian threat evasion (guardians now attack players!)
    3. EP MANAGEMENT (maintain EP > 60%) - NEW v1.7.0
    4. Critical healing (HP < 30)
    4b. Moderate healing (HP < 50) - prefer small heals
    5. Use utility items (Map, Energy Drink for EP)
    6. Free actions (pickup, equip)
    7. Guardian farming (120 sMoltz per kill — aggressive!)
    8. WEAKEST ENEMY FIRST (primary combat strategy) - ENHANCED v1.7.0
    9. Monster farming (weakest monster first)
    10. Facility interaction
    11. Strategic movement (prioritize richest regions)
    12. Rest (when EP below threshold)

    Uses ALL api-summary.md view fields for decision making.
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

    # View-level fields per api-summary.md
    visible_agents = view.get("visibleAgents", [])
    visible_monsters = view.get("visibleMonsters", [])
    visible_npcs = view.get("visibleNPCs", [])
    visible_items_raw = view.get("visibleItems", [])
    # Unwrap: each visibleItem is { regionId, item: { id, name, typeId, ... } }
    visible_items = []
    for entry in visible_items_raw:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("item")
        if isinstance(inner, dict):
            inner["regionId"] = entry.get("regionId", "")
            visible_items.append(inner)
        elif entry.get("id"):
            visible_items.append(entry)  # Legacy flat format
    visible_regions = view.get("visibleRegions", [])
    connected_regions = view.get("connectedRegions", [])
    pending_dz = view.get("pendingDeathzones", [])
    recent_logs = view.get("recentLogs", [])
    messages = view.get("recentMessages", [])
    alive_count = view.get("aliveCount", 100)

    # Fallback connections from currentRegion if connectedRegions empty
    connections = connected_regions or region.get("connections", [])
    interactables = region.get("interactables", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if isinstance(region, dict) else ""
    region_weather = region.get("weather", "").lower() if isinstance(region, dict) else ""

    if not is_alive:
        return None  # Dead — wait for game_ended

    # ── Build FULL danger map (DZ + pending DZ) ───────────────────
    # Used by ALL movement decisions to NEVER move into danger.
    # v1.5.2: pendingDeathzones entries are {id, name} objects
    danger_ids = set()
    for dz in pending_dz:
        if isinstance(dz, dict):
            danger_ids.add(dz.get("id", ""))
        elif isinstance(dz, str):
            danger_ids.add(dz)  # Legacy fallback
    # Also mark currently-active death zones from connected regions
    for conn in connections:
        resolved = _resolve_region(conn, view)
        if resolved and resolved.get("isDeathZone"):
            danger_ids.add(resolved.get("id", ""))

    # Track visible agents for memory
    _track_agents(visible_agents, self_data.get("id", ""), region_id)

    # Check EP status
    ep_status = _check_ep_status(ep, max_ep)
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0

    # ── Priority 1: DEATHZONE ESCAPE (overrides everything) ───────
    # Per game-systems.md: 1.34 HP/sec damage — bot dies fast!
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🚨 IN DEATH ZONE! Escaping to %s (HP=%d)", safe, hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"ESCAPE: In death zone! HP={hp} dropping fast (1.34/sec)"}
        elif not safe:
            log.error("🚨 IN DEATH ZONE but NO SAFE REGION! All neighbors are DZ!")

    # ── Priority 1b: Pre-escape pending death zone ────────────────
    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("⚠️ Region %s becoming DZ soon! Escaping to %s", region_id[:8], safe)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: Region becoming death zone soon"}

    # ── Priority 2: Curse resolution — DISABLED in v1.5.2 ─────────
    # Curse is temporarily disabled. Guardians no longer curse players.
    # Legacy code kept inert — will re-enable when curse returns.
    # (was: _check_curse → whisper answer to guardian)

    # ── Priority 2b: Guardian threat evasion (v1.5.2) ─────────────
    # Guardians now ATTACK player agents directly! Flee if low HP or low EP.
    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]
    if guardians_here and (hp < 40 or ep_status["status"] == "critical") and ep >= move_ep_cost:
        # Low HP or low EP + guardian in same region = flee!
        safe = _find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("⚠️ Guardian threat! HP=%d, EP=%.0f%%, fleeing to safety", 
                       hp, ep_ratio * 100)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"GUARDIAN FLEE: HP={hp}, EP={ep}/{max_ep}, too dangerous"}

    # ── Priority 3: EP MANAGEMENT (NEW v1.7.0) ─────────────────────
    # Rest or use energy drink to maintain EP above 60%
    if ep_status["need_rest"]:
        # First priority: use energy drink if available
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            log.info("⚡ EP MANAGEMENT: EP=%d/%d (%.0f%%), using energy drink", 
                    ep, max_ep, ep_ratio * 100)
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP MANAGEMENT: Restoring EP from {ep}/{max_ep}"}
        
        # Check if safe to rest (no enemies nearby)
        enemies_nearby = [a for a in visible_agents 
                         if a.get("regionId") == region_id 
                         and a.get("isAlive") 
                         and not a.get("isGuardian", False)]
        
        monsters_nearby = [m for m in visible_monsters 
                          if m.get("regionId") == region_id 
                          and m.get("hp", 0) > 0]
        
        if not enemies_nearby and not monsters_nearby and region_id not in danger_ids:
            if ep_ratio < EP_SAFE_THRESHOLD:
                log.info("😴 EP MANAGEMENT: EP=%d/%d (%.0f%%), resting to restore", 
                        ep, max_ep, ep_ratio * 100)
                return {"action": "rest", "data": {},
                        "reason": f"EP MANAGEMENT: Restoring EP (target >{EP_SAFE_THRESHOLD*100:.0f}%)"}
        elif ep_ratio < EP_COMBAT_MIN:
            # EP critical even with enemies - need to retreat to safe region
            safe = _find_safe_region(connections, danger_ids, view)
            if safe and ep >= move_ep_cost:
                log.warning("🏃 EP CRITICAL: EP=%d/%d (%.0f%%) with enemies nearby! Retreating to safe region", 
                           ep, max_ep, ep_ratio * 100)
                return {"action": "move", "data": {"regionId": safe},
                        "reason": f"EP CRITICAL: Retreating to restore EP"}

    # ── FREE ACTIONS (no cooldown, do before main action) ─────────

    # Auto-pickup Moltz (currency) and valuable items
    pickup_action = _check_pickup(visible_items, inventory, region_id)
    if pickup_action:
        return pickup_action

    # Auto-equip better weapon
    equip_action = _check_equip(inventory, equipped)
    if equip_action:
        return equip_action

    # Use utility items: Map (reveal map), Energy Drink (EP restore)
    util_action = _use_utility_item(inventory, hp, ep, alive_count, max_ep)
    if util_action:
        return util_action

    # If cooldown active, only free actions allowed
    if not can_act:
        return None

    # (Death zone escape already handled above as Priority 1)

    # ── Priority 4: CRITICAL Healing management (HP < 30) ─────────
    if hp < 30:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}, using {heal.get('typeId', 'heal')}"}

    # ── Priority 4b: MODERATE Healing (HP < 50) ───────────────────
    # Use small heals first to save big heals for critical moments
    elif hp < 50:
        heal = _find_healing_item(inventory, critical=False, prefer_small=True)
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"MODERATE HEAL: HP={hp}, using {heal.get('typeId', 'heal')}"}

    # ── Priority 5: Guardian farming ──────────────────────────────
    # Only 5 guardians per free room — each worth 120 sMoltz!
    # Guardians now ATTACK back — fight only if EP is sufficient
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and ep_status["can_fight"] and hp >= 30:
        # Select weakest guardian first (lowest HP)
        target = _select_weakest_target(guardians)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                outcome = estimate_combat_outcome(
                    hp, atk, defense, get_weapon_bonus(equipped),
                    target.get("hp", 100), target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather
                )
                # Fight if we win OR guardian is low HP (finish off)
                if outcome["win"] or target.get("hp", 100) < 40:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"GUARDIAN FARM: Weakest guardian HP={target.get('hp','?')} "
                                      f"(120 sMoltz! EP={ep}/{max_ep})"}

    # ── Priority 6: WEAKEST ENEMY FIRST (v1.7.0 primary combat) ────
    # Prioritize enemies with lowest HP to eliminate threats quickly
    hp_threshold = 40 if alive_count > 20 else 25
    enemies = [a for a in visible_agents
               if not a.get("isGuardian", False) and a.get("isAlive", True)
               and a.get("id") != self_data.get("id")]
    
    if enemies and ep_status["can_fight"] and hp >= hp_threshold:
        # Select weakest enemy (lowest HP) - PRIMARY STRATEGY
        target = _select_weakest_target(enemies)
        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                outcome = estimate_combat_outcome(
                    hp, atk, defense, get_weapon_bonus(equipped),
                    target.get("hp", 100), target.get("atk", 10), target.get("def", 5),
                    _estimate_enemy_weapon_bonus(target), region_weather
                )
                enemy_hp = target.get("hp", 100)
                # Fight if we win OR enemy is very low HP (can finish off)
                # Also fight if we have EP advantage
                if outcome["win"] or enemy_hp <= outcome["my_dmg"] * 2:
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"COMBAT (WEAKEST FIRST): Enemy HP={enemy_hp}, "
                                      f"my EP={ep}/{max_ep} ({ep_ratio*100:.0f}%), win={outcome['win']}"}

    # ── Priority 7: Monster farming (weakest monster first) ──────
    # Monsters are easier targets — can farm even with lower EP
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep_status["can_fight"] and hp > 20:
        # Select weakest monster (lowest HP) for efficient farming
        target = _select_weakest_monster(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER FARM (WEAKEST): {target.get('name', 'monster')} HP={target.get('hp', '?')}"}

    # ── Priority 8: Healing when safe (HP < 70, no enemies) ──────
    enemies_nearby = [a for a in visible_agents 
                     if a.get("regionId") == region_id 
                     and a.get("isAlive") 
                     and not a.get("isGuardian", False)]
    monsters_nearby = [m for m in visible_monsters if m.get("regionId") == region_id and m.get("hp", 0) > 0]
    
    if hp < 70 and not enemies_nearby and not monsters_nearby and region_id not in danger_ids:
        heal = _find_healing_item(inventory, critical=(hp < 30), prefer_small=(hp < 60))
        if heal:
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"SAFE HEAL: HP={hp}, area safe, using {heal.get('typeId', 'heal')}"}

    # ── Priority 9: Facility interaction ──────────────────────────
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        facility = _select_facility(interactables, hp, ep)
        if facility:
            return {"action": "interact",
                    "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type', 'unknown')}"}

    # ── Priority 10: Strategic movement (ENHANCED: prioritize farming) ───
    # Use connectedRegions — NEVER move into DZ or pending DZ!
    if ep >= move_ep_cost and connections:
        move_target = _choose_move_target(connections, danger_ids,
                                           region, visible_items, alive_count,
                                           visible_monsters, visible_agents)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "EXPLORE: Moving to richer region for farming"}

    # ── Priority 11: Rest (EP below safe threshold) ───────────────
    # Fallback rest if EP is below safe threshold and we've done everything else
    if ep_ratio < EP_SAFE_THRESHOLD and not enemies_nearby and not monsters_nearby and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep} ({ep_ratio*100:.0f}%), target >{EP_SAFE_THRESHOLD*100:.0f}%"}
    
    # Rest when EP < 4 as fallback
    if ep < 4 and not enemies_nearby and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep}, area is safe (+1 bonus EP)"}

    return None  # Wait for next turn


# ── Helper functions ──────────────────────────────────────────────────

def _get_move_ep_cost(terrain: str, weather: str) -> int:
    """Calculate move EP cost per game-systems.md.
    Base: 2. Storm: +1. Water terrain: 3.
    """
    if terrain == "water":
        return 3
    if weather == "storm":
        return 3  # 2 base + 1 storm
    return 2


def _estimate_enemy_weapon_bonus(agent: dict) -> int:
    """Estimate enemy's weapon bonus from their equipped weapon."""
    weapon = agent.get("equippedWeapon")
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "").lower() if isinstance(weapon, dict) else ""
    return WEAPONS.get(type_id, {}).get("bonus", 0)


# Track observed agents for memory (threat assessment)
_known_agents: dict = {}


"""
View fields from api-summary.md (all implemented above — v1.7.0 EP MANAGEMENT):
✅ self          — hp, ep, atk, def, inventory, equippedWeapon, isAlive
✅ currentRegion — id, name, terrain, weather, connections, interactables, isDeathZone
✅ connectedRegions — full Region objects OR bare string IDs (type-safe via _resolve_region)
✅ visibleRegions  — used for connectedRegions fallback + region ID lookup
✅ visibleAgents   — guardians (HOSTILE!) + enemies + combat targeting
✅ visibleMonsters — monster farming targets (more aggressive)
✅ visibleNPCs     — acknowledged (NPCs are flavor per game-systems.md)
✅ visibleItems    — pickup + movement attraction scoring
✅ pendingDeathzones — {id, name} entries for death zone escape + movement planning
✅ recentLogs      — available for analysis
✅ recentMessages  — communication (curse disabled in v1.5.2)
✅ aliveCount      — adaptive aggression (late game adjustment)

v1.7.0 ENHANCEMENTS:
✅ EP MANAGEMENT: Maintain EP above 60% (EP_SAFE_THRESHOLD)
✅ WEAKEST ENEMY FIRST: Primary combat strategy (_select_weakest_target)
✅ Energy Drink priority: High pickup priority and auto-use for EP restoration
✅ EP-aware combat decisions: Only fight when EP > 30% (EP_COMBAT_MIN)
✅ Smart resting: Rest early when EP drops below threshold
✅ EP-critical retreat: Move to safe region when EP critical with enemies nearby
✅ Weakest monster farming: Prioritize lowest HP monsters for efficiency
"""
