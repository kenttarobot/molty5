"""
Strategy brain — BERSERKER MODE v2.3 (FIX FACILITY LOOP)
===========================================================
PERBAIKAN:
- FIX: Bot tidak akan stuck di facility (broadcast_station)
- PRIORITAS: Combat > Facility > Movement
- Tambahan: Batasi interact facility yang sama
"""

from bot.utils.logger import get_logger

log = get_logger(__name__)


# =========================
# 🔥 KONFIGURASI BERSERKER v2.3
# =========================

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

ITEM_PRIORITY = {
    "rewards": 300,
    "katana": 100, "sniper": 95, "sword": 90, "pistol": 85,
    "dagger": 80, "bow": 75,
    "medkit": 70, "bandage": 65, "emergency_food": 60, "energy_drink": 58,
    "binoculars": 55,
    "map": 52,
    "megaphone": 40,
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

# ── BERSERKER THRESHOLDS v2.3 ────────────────────────────────────────
BERSERKER_CONFIG = {
    "HEAL_CRITICAL": 25,
    "HEAL_URGENT": 35,
    "HEAL_MODERATE": 50,
    "FLEE_HP": 20,
    "FLEE_OUTNUMBERED": 4,
    "FLEE_STRONG_ENEMY": 30,
    "MIN_HP_TO_ATTACK": 35,
    "EP_MIN_ATTACK": 0.20,
    "EP_SAFE": 0.15,
    
    # Hunting mode
    "HUNTING_MODE": True,
    "HUNT_UNTIL_DEATH": True,
    "TARGET_MARK_DURATION": 10,
    "WOUNDED_HP_THRESHOLD": 50,
    "LOW_HP_PRIORITY": 40,
    "EXECUTE_PRIORITY": 30,
    
    # ── BARU: Facility cooldown ─────────────────────────────────────
    "MAX_FACILITY_INTERACTIONS": 1,      # Maksimal 1x interact per facility
    "FACILITY_COOLDOWN_TURNS": 10,       # Cooldown 10 turn sebelum interact lagi
}

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_hunting_target: dict = None
_hunting_timer: int = 0

# ── BARU: Track facility yang sudah di-interact ──────────────────────
_interacted_facilities: dict = {}  # {facility_id: turn_interacted}


def reset_game_state():
    global _known_agents, _map_knowledge, _hunting_target, _hunting_timer, _interacted_facilities
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _hunting_target = None
    _hunting_timer = 0
    _interacted_facilities = {}
    log.info("=" * 60)
    log.info("BERSERKER MODE v2.3 (FIX FACILITY LOOP)")
    log.info("Prioritas: COMBAT > FACILITY > MOVEMENT")
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

    log.info("MAP LEARNED: %d DZ regions", len(_map_knowledge["death_zones"]))


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
    else:
        heals.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0))
    return heals[0]


def _find_energy_drink(inventory: list) -> dict | None:
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


def _check_pickup(items: list, inventory: list, region_id: str) -> dict | None:
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
        log.info("PICKUP: %s (score=%d)", type_id, score)
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": f"PICKUP: {type_id}"}
    return None


def _pickup_score(item: dict, inventory: list, heal_count: int) -> int:
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
        log.info("EQUIP: %s (+%d ATK)", best.get('typeId', 'weapon'), best_bonus)
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')}"}
    return None


def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int) -> dict | None:
    for item in inventory:
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId", "").lower()
        if type_id == "map":
            log.info("Using Map!")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Using Map"}
    return None


def _select_facility_with_limit(interactables: list, hp: int, ep: int, current_turn: int) -> dict | None:
    """
    Pilih facility dengan batasan:
    - Tidak akan interact facility yang sudah di-interact sebelumnya (atau cooldown)
    - Prioritaskan medical_facility (jika HP rendah)
    """
    global _interacted_facilities
    
    if not interactables:
        return None
    
    # Bersihkan facility lama (lebih dari cooldown)
    cooldown = BERSERKER_CONFIG["FACILITY_COOLDOWN_TURNS"]
    expired = [fid for fid, turn in _interacted_facilities.items() if current_turn - turn > cooldown]
    for fid in expired:
        del _interacted_facilities[fid]
    
    for fac in interactables:
        if not isinstance(fac, dict):
            continue
        if fac.get("isUsed"):
            continue
        
        fid = fac.get("id", "")
        
        # Cek apakah facility sudah pernah di-interact baru-baru ini
        if fid in _interacted_facilities:
            log.debug("Facility %s on cooldown (interacted at turn %d)", 
                     fac.get("type", "unknown"), _interacted_facilities[fid])
            continue
        
        ftype = fac.get("type", "").lower()
        
        # Medical facility: hanya jika HP rendah
        if ftype == "medical_facility" and hp < 70:
            return fac
        
        # Facility lain: hanya jika tidak ada musuh
        if ftype in ["supply_cache", "watchtower", "broadcast_station"]:
            # Batasi maksimal interact per facility
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
        }
    if len(_known_agents) > 50:
        dead = [k for k, v in _known_agents.items() if not v.get("isAlive", True)]
        for d in dead:
            del _known_agents[d]


def _choose_move_target(connections, danger_ids: set,
                         current_region: dict, visible_items: list,
                         alive_count: int) -> str | None:
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


def select_target_with_priority(enemies: list, current_target: dict = None) -> dict | None:
    if not enemies:
        return None
    
    global _hunting_target, _hunting_timer
    
    # PRIORITY 1: Hunting target
    if BERSERKER_CONFIG["HUNTING_MODE"] and _hunting_target:
        target_alive = False
        for enemy in enemies:
            if enemy.get("id") == _hunting_target.get("id"):
                target_alive = True
                _hunting_target = enemy
                break
        
        if target_alive:
            log.info("HUNTING TARGET: Melanjutkan berburu!")
            return _hunting_target
        else:
            log.info("HUNTING COMPLETE: Target sudah mati!")
            _hunting_target = None
    
    # PRIORITY 2: Execute (HP < 30)
    execute_targets = [e for e in enemies if e.get("hp", 100) < BERSERKER_CONFIG["EXECUTE_PRIORITY"]]
    if execute_targets:
        target = min(execute_targets, key=lambda e: e.get("hp", 999))
        log.info("EXECUTE PRIORITY: Musuh HP=%d!", target.get("hp", 0))
        return target
    
    # PRIORITY 3: Wounded (HP < 50)
    wounded_targets = [e for e in enemies if e.get("hp", 100) < BERSERKER_CONFIG["WOUNDED_HP_THRESHOLD"]]
    if wounded_targets:
        target = min(wounded_targets, key=lambda e: e.get("hp", 999))
        log.info("WOUNDED PRIORITY: Musuh HP=%d, terus kejar!", target.get("hp", 0))
        return target
    
    # PRIORITY 4: Weakest
    return _select_weakest(enemies)


def update_hunting_target(target: dict):
    global _hunting_target, _hunting_timer
    if target and BERSERKER_CONFIG["HUNTING_MODE"]:
        _hunting_target = target
        _hunting_timer = BERSERKER_CONFIG["TARGET_MARK_DURATION"]
        log.info("NEW HUNTING TARGET: ID=%s, HP=%d", target.get("id", "unknown")[:8], target.get("hp", 0))


# =========================
# 🧠 MAIN DECISION
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    global _hunting_target, _hunting_timer, _interacted_facilities
    import time
    
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

    # Current turn (gunakan turn counter atau timestamp)
    current_turn = view.get("turn", 0) or int(time.time())

    # Decrement hunting timer
    if _hunting_timer > 0:
        _hunting_timer -= 1
    elif _hunting_target:
        _hunting_target = None

    # Danger map
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

    _track_agents(visible_agents, self_data.get("id", ""), region_id)
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0

    # Deteksi musuh
    enemies_here = [a for a in visible_agents
                    if a.get("isAlive", True)
                    and a.get("id") != self_data.get("id")
                    and a.get("regionId") == region_id
                    and not a.get("isGuardian", False)]
    
    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]

    # Hitung damage musuh terkuat
    strongest_enemy_damage = 0
    for enemy in enemies_here:
        enemy_dmg = calc_damage(enemy.get("atk", 10),
                               _estimate_enemy_weapon_bonus(enemy),
                               defense, region_weather)
        if enemy_dmg > strongest_enemy_damage:
            strongest_enemy_damage = enemy_dmg

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 1: DEATHZONE ESCAPE
    # ═══════════════════════════════════════════════════════════════
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("DEATHZONE! Escaping to %s", safe)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}

    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("Pending DZ! Escaping")
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE"}

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 2: HEALING DARURAT
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HEAL_CRITICAL"]:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            log.warning("CRITICAL HEAL! HP=%d -> using %s", hp, heal.get("typeId", "heal"))
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 3: FLEE (hanya jika benar-benar kritis)
    # ═══════════════════════════════════════════════════════════════
    should_flee = False
    flee_reason = ""
    
    if _hunting_target:
        if hp < 15:
            should_flee = True
            flee_reason = f"CRITICAL HP DURING HUNT ({hp})"
    else:
        if hp < BERSERKER_CONFIG["FLEE_HP"]:
            should_flee = True
            flee_reason = f"CRITICAL HP ({hp})"
        elif strongest_enemy_damage > BERSERKER_CONFIG["FLEE_STRONG_ENEMY"] and hp < 50:
            should_flee = True
            flee_reason = f"ENEMY TOO STRONG (dmg={strongest_enemy_damage})"
        elif len(enemies_here) >= BERSERKER_CONFIG["FLEE_OUTNUMBERED"] and hp < 60:
            should_flee = True
            flee_reason = f"OUTNUMBERED ({len(enemies_here)})"
    
    if should_flee:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("FLEEING: %s", flee_reason)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"FLEE: {flee_reason}"}

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 4: COUNTER ATTACK (PRIORITAS UTAMA!)
    # ═══════════════════════════════════════════════════════════════
    can_attack = (hp >= BERSERKER_CONFIG["MIN_HP_TO_ATTACK"] and 
                  ep_ratio >= BERSERKER_CONFIG["EP_MIN_ATTACK"])
    
    if _hunting_target and hp >= 15:
        can_attack = True
    
    if enemies_here and can_attack:
        target = select_target_with_priority(enemies_here, _hunting_target)
        
        if target:
            w_range = get_weapon_range(equipped)
            
            if _is_in_range(target, region_id, w_range, connections):
                my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                    target.get("def", 5), region_weather)
                enemy_hp = target.get("hp", 100)
                
                if BERSERKER_CONFIG["HUNTING_MODE"] and not _hunting_target:
                    update_hunting_target(target)
                
                log.info("BERSERKER ATTACK! Target HP=%d, My DMG=%d, My HP=%d", 
                        enemy_hp, my_dmg, hp)
                
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"BERSERKER: HP={enemy_hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 5: GUARDIAN FARMING
    # ═══════════════════════════════════════════════════════════════
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and ep >= 2 and hp >= 45 and not _hunting_target:
        target = _select_weakest(guardians)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            log.info("GUARDIAN HUNT! 120 sMoltz!")
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "agent"},
                    "reason": "GUARDIAN: 120 sMoltz!"}

    # ═══════════════════════════════════════════════════════════════
    # FREE ACTIONS (pickup, equip, utility)
    # ═══════════════════════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 6: FACILITY INTERACTION (DENGAN BATASAN!)
    # ═══════════════════════════════════════════════════════════════
    # Hanya interact facility jika TIDAK ADA MUSUH di region!
    if not enemies_here and not guardians_here:
        facility = _select_facility_with_limit(interactables, hp, ep, current_turn)
        if facility:
            fid = facility.get("id", "")
            _interacted_facilities[fid] = current_turn
            log.info("FACILITY INTERACT: %s", facility.get("type", "unknown"))
            return {"action": "interact", "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type', 'unknown')}"}

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 7: MODERATE HEALING
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HEAL_MODERATE"] and not enemies_here and not _hunting_target:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            log.info("MODERATE HEAL: HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 8: EP RECOVERY
    # ═══════════════════════════════════════════════════════════════
    if ep_ratio < BERSERKER_CONFIG["EP_SAFE"]:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP: {ep}/{max_ep}"}
        
        if not enemies_here and region_id not in danger_ids and not _hunting_target:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 9: MONSTER FARMING
    # ═══════════════════════════════════════════════════════════════
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep >= 1 and hp > 40 and not enemies_here and not _hunting_target:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER: HP={target.get('hp', '?')}"}

    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 10: MOVEMENT (KEJAR TARGET ATAU CARI AMAN)
    # ═══════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        if _hunting_target:
            target_region = _hunting_target.get("regionId", "")
            if target_region and target_region != region_id and target_region not in danger_ids:
                log.info("HUNTING: Moving to chase target!")
                return {"action": "move", "data": {"regionId": target_region},
                        "reason": "HUNTING: Kejar target!"}
        
        move_target = _choose_move_target(connections, danger_ids,
                                           region, visible_items, alive_count)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "MOVE: Strategic"}

    # ═══════════════════════════════════════════════════════════════
    # LAST RESORT: REST
    # ═══════════════════════════════════════════════════════════════
    if ep < 4 and not enemies_here and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {}},
              """
╔══════════════════════════════════════════════════════════════════╗
║        BERSERKER BRAIN v3.0 — TOURNAMENT DOMINATOR              ║
╠══════════════════════════════════════════════════════════════════╣
║  UPGRADE dari v2.3:                                             ║
║  ✅ Enemy Profiling — pelajari karakter lawan, simpan di memori ║
║  ✅ Smart Inventory — buang item lemah, ambil yang lebih baik   ║
║  ✅ HP always > 40, EP always > 60%                             ║
║  ✅ NEVER RETREAT saat diserang — balas hingga musuh mati       ║
║  ✅ Recovery Loop — heal + farm jika HP < 30, lalu buru lagi   ║
║  ✅ Counter-Strategy — deteksi pola lawan, eksploitasi kelemahan║
║  ✅ Pursuit System — kejar musuh yang kabur, jangan biarkan lari║
║  ✅ Aggressive Guardian Farm saat recovery                      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import time
import math
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
#  KONFIGURASI BERSERKER v3.0
# ═══════════════════════════════════════════════════════════════════

BERSERKER_CONFIG = {
    # ── HP & EP Management ──────────────────────────────────────────
    "HP_MINIMUM":            40,    # HP harus selalu di atas ini
    "HP_CRITICAL":           30,    # Trigger recovery mode
    "HP_HEAL_URGENT":        45,    # Heal segera jika di bawah ini
    "HP_HEAL_MODERATE":      60,    # Heal oportunistik
    "EP_MINIMUM_RATIO":      0.60,  # EP harus di atas 60%
    "EP_ATTACK_MIN_RATIO":   0.25,  # Minimum EP untuk menyerang
    "EP_SAFE_RATIO":         0.20,  # Istirahat jika EP di bawah ini

    # ── Combat ──────────────────────────────────────────────────────
    "MIN_HP_TO_ATTACK":      35,    # HP minimum untuk menyerang
    "NEVER_FLEE_IF_ATTACKED":True,  # JANGAN PERNAH KABUR saat diserang
    "COUNTER_ATTACK_HP":     25,    # Minimal HP untuk counter attack
    "PURSUIT_ENABLED":       True,  # Kejar musuh yang kabur
    "PURSUIT_MAX_HOPS":      2,     # Maksimal 2 region untuk kejar

    # ── Recovery Mode ───────────────────────────────────────────────
    "RECOVERY_HP_THRESHOLD": 30,    # Masuk recovery mode jika HP < 30
    "RECOVERY_TARGET_HP":    70,    # Keluar recovery mode jika HP >= 70
    "RECOVERY_FARM_GUARDIAN":True,  # Farm guardian saat recovery

    # ── Hunting ─────────────────────────────────────────────────────
    "HUNTING_MODE":          True,
    "HUNT_UNTIL_DEATH":      True,
    "TARGET_MARK_DURATION":  15,    # Tetap kejar selama 15 turn
    "EXECUTE_HP_THRESHOLD":  30,    # Prioritas eksekusi HP < 30
    "WOUNDED_HP_THRESHOLD":  50,

    # ── Enemy Profiling ─────────────────────────────────────────────
    "PROFILE_MEMORY_SIZE":   100,   # Maksimal 100 profil musuh
    "PROFILE_HISTORY_LEN":   20,    # Simpan 20 interaksi per musuh

    # ── Inventory Management ────────────────────────────────────────
    "INV_MAX_CAPACITY":      10,
    "INV_DROP_THRESHOLD":    9,     # Mulai pertimbangkan drop saat 9 slot

    # ── Facility ────────────────────────────────────────────────────
    "MAX_FACILITY_INTERACTIONS": 1,
    "FACILITY_COOLDOWN_TURNS":   10,

    # ── Flee (hanya untuk kondisi ekstrem) ──────────────────────────
    "FLEE_HP":               12,    # Flee hanya jika HP < 12 (hampir mati)
    "FLEE_OUTNUMBERED":      5,     # Flee jika dikeroyok 5+ musuh
}


# ═══════════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_hunting_target: dict = None
_hunting_timer: int = 0
_interacted_facilities: dict = {}
_recovery_mode: bool = False
_last_attacked_by: str = None      # ID musuh yang baru saja menyerang kita
_last_attacked_turn: int = 0

# ── ENEMY PROFILING ─────────────────────────────────────────────────
_enemy_profiles: dict = {}
"""
Struktur profil:
{
  "enemy_id": {
    "id": str,
    "first_seen": int,
    "last_seen": int,
    "encounters": int,
    "kills_on_me": int,       # Berapa kali mereka kill kita
    "times_we_killed": int,   # Berapa kali kita kill mereka
    "avg_hp": float,
    "avg_atk": float,
    "weapon_history": list,   # Senjata yang pernah dipakai
    "preferred_weapon": str,  # Senjata yang paling sering dipakai
    "behavior_tags": set,     # "aggressive", "healer", "runner", "camper"
    "last_hp": int,
    "last_atk": int,
    "last_weapon": str,
    "known_weakness": str,    # "ranged", "melee", "rush", "kite"
    "win_rate_vs": float,     # Win rate kita vs dia
    "interaction_log": list,  # Log 20 interaksi terakhir
  }
}
"""


def reset_game_state():
    global _known_agents, _map_knowledge, _hunting_target, _hunting_timer
    global _interacted_facilities, _recovery_mode, _last_attacked_by, _last_attacked_turn
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _hunting_target = None
    _hunting_timer = 0
    _interacted_facilities = {}
    _recovery_mode = False
    _last_attacked_by = None
    _last_attacked_turn = 0
    # NOTE: _enemy_profiles TIDAK direset — memori musuh persisten antar game!
    log.info("=" * 65)
    log.info("  BERSERKER BRAIN v3.0 — TOURNAMENT DOMINATOR")
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
        # Bersihkan jika terlalu banyak profil
        if len(_enemy_profiles) > BERSERKER_CONFIG["PROFILE_MEMORY_SIZE"]:
            oldest = sorted(_enemy_profiles.keys(),
                            key=lambda k: _enemy_profiles[k]["last_seen"])
            del _enemy_profiles[oldest[0]]
    return _enemy_profiles[enemy_id]


def update_enemy_profile(enemy: dict, current_turn: int, event: str = "seen"):
    """Update profil musuh setiap kita melihat atau berinteraksi dengan mereka."""
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

    # Simpan sample
    profile["hp_samples"].append(hp)
    profile["atk_samples"].append(atk)
    if len(profile["hp_samples"]) > BERSERKER_CONFIG["PROFILE_HISTORY_LEN"]:
        profile["hp_samples"].pop(0)
        profile["atk_samples"].pop(0)

    # Update preferred weapon
    profile["weapon_history"].append(weapon_type)
    if len(profile["weapon_history"]) > BERSERKER_CONFIG["PROFILE_HISTORY_LEN"]:
        profile["weapon_history"].pop(0)
    if profile["weapon_history"]:
        profile["preferred_weapon"] = max(set(profile["weapon_history"]),
                                          key=profile["weapon_history"].count)

    # Deteksi behavior tags
    _analyze_behavior(profile, enemy, event)

    # Analisa kelemahan
    _detect_weakness(profile)

    # Log interaksi
    profile["interaction_log"].append({
        "turn": current_turn,
        "event": event,
        "enemy_hp": hp,
        "weapon": weapon_type,
    })
    if len(profile["interaction_log"]) > BERSERKER_CONFIG["PROFILE_HISTORY_LEN"]:
        profile["interaction_log"].pop(0)


def _analyze_behavior(profile: dict, enemy: dict, event: str):
    """Analisa pola perilaku musuh dari log interaksi."""
    tags = profile["behavior_tags"]
    hp = enemy.get("hp", 100)
    weapon_type = profile["last_weapon"]

    # Tag: aggressive — sering HP rendah masih menyerang
    if event == "attacked_us" and hp < 40:
        tags.add("aggressive")

    # Tag: healer — sering dilihat dengan HP tinggi meski baru fight
    hp_samples = profile["hp_samples"]
    if len(hp_samples) >= 5:
        avg_hp = sum(hp_samples) / len(hp_samples)
        if avg_hp > 70:
            tags.add("healer")
        if avg_hp < 40:
            tags.add("glass_cannon")

    # Tag: ranged fighter
    if weapon_type in ["sniper", "bow", "pistol"]:
        tags.add("ranged")
    elif weapon_type in ["katana", "sword", "dagger"]:
        tags.add("melee")

    # Tag: runner — sering tidak terlihat lagi setelah HP drop
    if event == "disappeared_low_hp":
        tags.add("runner")

    # Tag: camper — sering ditemukan di region yang sama
    if event == "seen" and "camper" not in tags:
        if profile["encounters"] > 3:
            # Sederhana: jika sering ditemukan masih hidup, kemungkinan camper
            pass


def _detect_weakness(profile: dict):
    """Tentukan strategi terbaik untuk mengalahkan musuh ini."""
    tags = profile["behavior_tags"]
    preferred_weapon = profile["preferred_weapon"]
    weapon_tier = WEAPON_TIER.get(preferred_weapon, 0)

    # Musuh ranged → kejar dengan melee (rush)
    if "ranged" in tags:
        profile["known_weakness"] = "rush_melee"

    # Musuh melee → jaga jarak, pakai ranged
    elif "melee" in tags and "ranged" not in tags:
        profile["known_weakness"] = "kite_ranged"

    # Musuh healer → burst damage, jangan beri waktu heal
    elif "healer" in tags:
        profile["known_weakness"] = "burst_no_pause"

    # Musuh glass cannon → tank damage, balas lebih keras
    elif "glass_cannon" in tags:
        profile["known_weakness"] = "outlast"

    # Musuh runner → kejar sampai dapat
    elif "runner" in tags:
        profile["known_weakness"] = "pursuit"

    # Default: serang normal
    else:
        profile["known_weakness"] = "standard"


def on_killed_enemy(enemy_id: str):
    """Dipanggil saat kita berhasil kill musuh."""
    if enemy_id in _enemy_profiles:
        _enemy_profiles[enemy_id]["times_we_killed"] += 1
        wins = _enemy_profiles[enemy_id]["times_we_killed"]
        losses = _enemy_profiles[enemy_id]["kills_on_me"]
        total = wins + losses
        _enemy_profiles[enemy_id]["win_rate_vs"] = wins / total if total > 0 else 0.5
        log.info("PROFILE UPDATE: Killed %s | Win rate vs them: %.0f%%",
                 enemy_id[:8], _enemy_profiles[enemy_id]["win_rate_vs"] * 100)


def on_killed_by_enemy(enemy_id: str):
    """Dipanggil saat kita mati oleh musuh."""
    profile = _get_or_create_profile(enemy_id)
    profile["kills_on_me"] += 1
    wins = profile["times_we_killed"]
    losses = profile["kills_on_me"]
    total = wins + losses
    profile["win_rate_vs"] = wins / total if total > 0 else 0.0
    log.warning("PROFILE UPDATE: Killed by %s | Win rate vs them: %.0f%%",
                enemy_id[:8], profile["win_rate_vs"] * 100)


def get_strategy_vs(enemy_id: str) -> str:
    """Ambil strategi terbaik untuk musuh tertentu."""
    if enemy_id not in _enemy_profiles:
        return "standard"
    profile = _enemy_profiles[enemy_id]
    weakness = profile.get("known_weakness", "standard")
    win_rate = profile.get("win_rate_vs", 0.5)

    # Jika win rate rendah, gunakan strategi yang lebih hati-hati
    if win_rate < 0.3:
        log.info("PROFILE: %s is a dangerous enemy (win_rate=%.0f%%) — using caution",
                 enemy_id[:8], win_rate * 100)
        return f"careful_{weakness}"
    elif win_rate > 0.7:
        log.info("PROFILE: %s is easy prey (win_rate=%.0f%%) — going aggressive",
                 enemy_id[:8], win_rate * 100)
        return f"aggressive_{weakness}"

    return weakness


# ═══════════════════════════════════════════════════════════════════
#  INVENTORY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def _get_item_value(item: dict) -> int:
    """Hitung nilai sebuah item untuk ranking."""
    if not isinstance(item, dict):
        return 0
    type_id = item.get("typeId", "").lower()
    category = item.get("category", "").lower()

    if type_id == "rewards" or category == "currency":
        return 1000  # Jangan pernah buang rewards

    if category == "weapon":
        bonus = WEAPONS.get(type_id, {}).get("bonus", 0)
        return 100 + bonus

    if type_id in RECOVERY_ITEMS:
        return ITEM_PRIORITY.get(type_id, 30)

    return ITEM_PRIORITY.get(type_id, 5)


def _find_worst_item(inventory: list, exclude_equipped_id: str = None) -> dict | None:
    """
    Cari item paling tidak berguna di inventory untuk dibuang.
    Tidak akan membuang: senjata terbaik, rewards, medkit terakhir.
    """
    if not inventory:
        return None

    # Hitung jumlah healing item
    heal_count = sum(1 for i in inventory
                     if isinstance(i, dict) and i.get("typeId", "").lower() in RECOVERY_ITEMS
                     and RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0) > 0)

    # Temukan senjata terbaik di inventory
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

        # JANGAN buang: equipped weapon
        if item_id == exclude_equipped_id:
            continue

        # JANGAN buang: senjata terbaik
        if item_id == best_weapon_id:
            continue

        # JANGAN buang: rewards/currency
        if type_id == "rewards" or item.get("category") == "currency":
            continue

        # JANGAN buang: satu-satunya medkit
        if type_id == "medkit" and heal_count <= 1:
            continue

        value = _get_item_value(item)
        candidates.append((item, value))

    if not candidates:
        return None

    # Kembalikan item dengan nilai terendah
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def _smart_pickup(items: list, inventory: list, region_id: str, equipped) -> dict | None:
    """
    Smart pickup: jika inventory penuh, drop item terburuk dulu jika item baru lebih baik.
    """
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

    # Hitung skor tiap item yang tersedia
    scored = [(i, _pickup_score_v3(i, inventory, heal_count)) for i in local_items]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_item, best_score = scored[0]
    if best_score <= 0:
        return None

    # Inventory masih ada tempat
    if inv_size < BERSERKER_CONFIG["INV_MAX_CAPACITY"]:
        type_id = best_item.get("typeId", "item")
        log.info("PICKUP: %s (score=%d)", type_id, best_score)
        return {"action": "pickup", "data": {"itemId": best_item["id"]},
                "reason": f"PICKUP: {type_id}"}

    # Inventory penuh — cek apakah perlu drop item buruk
    equipped_id = equipped.get("id") if isinstance(equipped, dict) else None
    worst = _find_worst_item(inventory, exclude_equipped_id=equipped_id)
    if worst:
        worst_value = _get_item_value(worst)
        if best_score > worst_value + 10:  # Item baru jauh lebih baik
            log.info("SMART SWAP: Dropping %s (val=%d) for %s (val=%d)",
                     worst.get("typeId", "?"), worst_value,
                     best_item.get("typeId", "?"), best_score)
            return {"action": "drop_item", "data": {"itemId": worst["id"]},
                    "reason": f"SWAP: Drop {worst.get('typeId','?')} untuk {best_item.get('typeId','?')}"}

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
    log.info("MAP LEARNED: %d death zones", len(_map_knowledge["death_zones"]))


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


def _select_facility_with_limit(interactables: list, hp: int, ep: int, current_turn: int) -> dict | None:
    global _interacted_facilities
    if not interactables:
        return None
    cooldown = BERSERKER_CONFIG["FACILITY_COOLDOWN_TURNS"]
    expired = [fid for fid, turn in _interacted_facilities.items() if current_turn - turn > cooldown]
    for fid in expired:
        del _interacted_facilities[fid]
    for fac in interactables:
        if not isinstance(fac, dict) or fac.get("isUsed"):
            continue
        fid = fac.get("id", "")
        if fid in _interacted_facilities:
            continue
        ftype = fac.get("type", "").lower()
        if ftype == "medical_facility" and hp < 70:
            return fac
        if ftype in ["supply_cache", "watchtower", "broadcast_station"]:
            return fac
    return None


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
        # Update profil musuh (bukan guardian)
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
    """Pilih target terbaik berdasarkan situasi dan profil musuh."""
    if not enemies:
        return None

    global _hunting_target

    # PRIORITY 1: Hunting target masih ada
    if BERSERKER_CONFIG["HUNTING_MODE"] and _hunting_target:
        for enemy in enemies:
            if enemy.get("id") == _hunting_target.get("id"):
                _hunting_target = enemy
                log.info("HUNTING: Melanjutkan buru target %s HP=%d",
                         enemy.get("id", "?")[:8], enemy.get("hp", 0))
                return _hunting_target
        # Target sudah mati
        log.info("HUNTING COMPLETE: Target eliminated!")
        _hunting_target = None

    # PRIORITY 2: Execute (HP < 30)
    execute_targets = [e for e in enemies if e.get("hp", 100) < BERSERKER_CONFIG["EXECUTE_HP_THRESHOLD"]]
    if execute_targets:
        target = min(execute_targets, key=lambda e: e.get("hp", 999))
        log.info("EXECUTE: Target HP=%d — FINISH HIM!", target.get("hp", 0))
        return target

    # PRIORITY 3: Wounded (HP < 50)
    wounded = [e for e in enemies if e.get("hp", 100) < BERSERKER_CONFIG["WOUNDED_HP_THRESHOLD"]]
    if wounded:
        target = min(wounded, key=lambda e: e.get("hp", 999))
        log.info("WOUNDED TARGET: HP=%d — terus kejar!", target.get("hp", 0))
        return target

    # PRIORITY 4: Strategi berbasis profil musuh
    if strategy in ("rush_melee", "aggressive_rush_melee"):
        # Kejar musuh ranged (mereka lemah jika kita dekat)
        ranged_enemies = [e for e in enemies
                          if e.get("equippedWeapon", {}) and
                          e.get("equippedWeapon", {}).get("typeId", "").lower() in ["sniper", "bow", "pistol"]]
        if ranged_enemies:
            return min(ranged_enemies, key=lambda e: e.get("hp", 999))

    # PRIORITY 5: Default — target terlemah
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
    """
    Recovery mode: HP < 30.
    1. Heal dulu jika ada item
    2. Farm guardian/monster untuk EP & item
    3. Istirahat jika EP rendah
    4. Jangan kejar PvP sampai HP >= 70
    """
    hp = my.get("hp", 100)
    log.warning("RECOVERY MODE AKTIF — HP=%d | Target: %d",
                hp, BERSERKER_CONFIG["RECOVERY_TARGET_HP"])

    # 1. Heal prioritas utama
    heal = _find_healing_item(inventory, critical=True)
    if heal:
        log.info("RECOVERY HEAL: %s", heal.get("typeId", "heal"))
        return {"action": "use_item", "data": {"itemId": heal["id"]},
                "reason": f"RECOVERY HEAL: HP={hp}"}

    # 2. Energy drink jika EP rendah
    energy_drink = _find_energy_drink(inventory)
    if energy_drink and ep_ratio < 0.5:
        return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                "reason": f"RECOVERY EP: {ep}"}

    # 3. Farm guardian jika aman
    if BERSERKER_CONFIG["RECOVERY_FARM_GUARDIAN"]:
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

    # 4. Farm monster kecil
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

    # 5. Istirahat untuk recovery EP
    if not danger_ids or region_id not in danger_ids:
        log.info("RECOVERY REST")
        return {"action": "rest", "data": {}, "reason": f"RECOVERY REST: HP={hp}"}

    return None


# ═══════════════════════════════════════════════════════════════════
#  MAIN DECISION ENGINE
# ═══════════════════════════════════════════════════════════════════

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    global _hunting_target, _hunting_timer, _interacted_facilities
    global _recovery_mode, _last_attacked_by, _last_attacked_turn

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

    # ── Decrement hunting timer ──────────────────────────────────────
    if _hunting_timer > 0:
        _hunting_timer -= 1
    elif _hunting_target:
        _hunting_target = None

    # ── Danger map ──────────────────────────────────────────────────
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

    # ── Track & Profile agents ───────────────────────────────────────
    _track_agents(visible_agents, my_id, region_id, current_turn)
    move_ep_cost = _get_move_ep_cost(region_terrain, region_weather)
    ep_ratio     = ep / max_ep if max_ep > 0 else 1.0

    # ── Classify visible agents ──────────────────────────────────────
    enemies_here = [a for a in visible_agents
                    if a.get("isAlive", True) and a.get("id") != my_id
                    and a.get("regionId") == region_id
                    and not a.get("isGuardian", False)]

    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]

    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]

    # ── Deteksi apakah kita sedang diserang ─────────────────────────
    just_attacked = (current_turn - _last_attacked_turn) <= 2 and _last_attacked_by

    # ── Hitung damage musuh terkuat ──────────────────────────────────
    strongest_enemy_damage = max(
        (calc_damage(e.get("atk", 10), _estimate_enemy_weapon_bonus(e), defense, region_weather)
         for e in enemies_here),
        default=0
    )

    # ═══════════════════════════════════════════════════════════════
    # [P1] DEATHZONE ESCAPE
    # ═══════════════════════════════════════════════════════════════
    if region.get("isDeathZone", False) or region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("DEATHZONE ESCAPE!")
            return {"action": "move", "data": {"regionId": safe}, "reason": "DEATHZONE ESCAPE"}

    # ═══════════════════════════════════════════════════════════════
    # [P2] UPDATE RECOVERY MODE
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["RECOVERY_HP_THRESHOLD"] and not just_attacked:
        _recovery_mode = True
    elif hp >= BERSERKER_CONFIG["RECOVERY_TARGET_HP"]:
        if _recovery_mode:
            log.info("RECOVERY COMPLETE — Kembali berburu! HP=%d", hp)
        _recovery_mode = False

    if _recovery_mode and not enemies_here:
        result = _handle_recovery_mode(
            self_data, inventory, visible_agents, region_id,
            connections, danger_ids, equipped, region_weather,
            ep, ep_ratio, move_ep_cost, monsters
        )
        if result:
            return result

    # ═══════════════════════════════════════════════════════════════
    # [P3] CRITICAL HEAL (bahkan saat ada musuh)
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HP_CRITICAL"] and not just_attacked:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            log.warning("CRITICAL HEAL! HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════
    # [P4] COUNTER ATTACK — TIDAK BOLEH KABUR SAAT DISERANG!
    # ═══════════════════════════════════════════════════════════════
    if just_attacked and _last_attacked_by and hp >= BERSERKER_CONFIG["COUNTER_ATTACK_HP"]:
        attacker = next((e for e in enemies_here if e.get("id") == _last_attacked_by), None)
        if attacker:
            strategy = get_strategy_vs(_last_attacked_by)
            update_enemy_profile(attacker, current_turn, event="attacked_us")
            log.warning("COUNTER ATTACK! Musuh %s menyerang kita — BALAS! HP=%d Strategy=%s",
                        _last_attacked_by[:8], hp, strategy)
            return {"action": "attack",
                    "data": {"targetId": attacker["id"], "targetType": "agent"},
                    "reason": f"COUNTER: Balas serangan {_last_attacked_by[:8]}"}

    # ═══════════════════════════════════════════════════════════════
    # [P5] EQUIP SENJATA TERBAIK
    # ═══════════════════════════════════════════════════════════════
    equip_action = _check_equip(inventory, equipped)
    if equip_action:
        return equip_action

    # ═══════════════════════════════════════════════════════════════
    # [P6] COMBAT — SERANG MUSUH
    # ═══════════════════════════════════════════════════════════════
    can_attack = (hp >= BERSERKER_CONFIG["MIN_HP_TO_ATTACK"]
                  and ep_ratio >= BERSERKER_CONFIG["EP_ATTACK_MIN_RATIO"])

    # Override: jika hunting target, serang meski HP rendah
    if _hunting_target and hp >= 20:
        can_attack = True

    if enemies_here and can_attack:
        # Ambil strategi berdasarkan profil target
        primary_target_id = (enemies_here[0].get("id", "") if not _hunting_target
                             else _hunting_target.get("id", ""))
        strategy = get_strategy_vs(primary_target_id)
        target = select_target_with_priority(enemies_here, strategy)

        if target:
            w_range = get_weapon_range(equipped)
            if _is_in_range(target, region_id, w_range, connections):
                my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                     target.get("def", 5), region_weather)
                enemy_hp = target.get("hp", 100)

                # Set hunting target
                if not _hunting_target:
                    update_hunting_target(target)

                log.info("ATTACK! Target=%s HP=%d MyDMG=%d MyHP=%d Strategy=%s",
                         target.get("id", "?")[:8], enemy_hp, my_dmg, hp, strategy)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"ATTACK: {strategy} | target_hp={enemy_hp}"}

    # ═══════════════════════════════════════════════════════════════
    # [P7] FLEE EKSTREM (hanya jika nyaris mati dan tidak dalam COUNTER mode)
    # ═══════════════════════════════════════════════════════════════
    should_flee = False
    if not just_attacked:  # JANGAN flee saat sedang diserang
        if hp < BERSERKER_CONFIG["FLEE_HP"]:
            should_flee = True
        elif len(enemies_here) >= BERSERKER_CONFIG["FLEE_OUTNUMBERED"] and hp < 40:
            should_flee = True

    if should_flee:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("EXTREME FLEE: HP=%d enemies=%d", hp, len(enemies_here))
            return {"action": "move", "data": {"regionId": safe}, "reason": "EXTREME FLEE"}

    # ═══════════════════════════════════════════════════════════════
    # [P8] GUARDIAN FARMING
    # ═══════════════════════════════════════════════════════════════
    guardians_all = [a for a in visible_agents
                     if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians_all and ep >= 2 and hp >= 45 and not _hunting_target:
        target = _select_weakest(guardians_all)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            log.info("GUARDIAN FARM! HP=%d", target.get("hp", 0))
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "agent"},
                    "reason": "GUARDIAN FARM: 120 sMoltz!"}

    # ═══════════════════════════════════════════════════════════════
    # [P9] SMART PICKUP / ITEM SWAP
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
    # [P10] FACILITY INTERACTION
    # ═══════════════════════════════════════════════════════════════
    if not enemies_here and not guardians_here:
        facility = _select_facility_with_limit(interactables, hp, ep, current_turn)
        if facility:
            fid = facility.get("id", "")
            _interacted_facilities[fid] = current_turn
            log.info("FACILITY: %s", facility.get("type", "?"))
            return {"action": "interact", "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type','?')}"}

    # ═══════════════════════════════════════════════════════════════
    # [P11] HEAL OPPORTUNISTIK (HP < 60, tidak ada musuh)
    # ═══════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HP_HEAL_URGENT"] and not enemies_here and not _hunting_target:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            log.info("OPPORTUNISTIC HEAL: HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════
    # [P12] EP RECOVERY (target EP > 60%)
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
    # [P13] MONSTER FARMING
    # ═══════════════════════════════════════════════════════════════
    if monsters and ep >= 1 and hp > 40 and not enemies_here and not _hunting_target:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER FARM: HP={target.get('hp','?')}"}

    # ═══════════════════════════════════════════════════════════════
    # [P14] MOVEMENT — KEJAR TARGET ATAU EKSPLORASI
    # ═══════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        # Kejar hunting target ke region lain
        if _hunting_target and BERSERKER_CONFIG["PURSUIT_ENABLED"]:
            target_region = _hunting_target.get("regionId", "")
            if target_region and target_region != region_id and target_region not in danger_ids:
                log.info("PURSUIT: Kejar %s ke %s!",
                         _hunting_target.get("id", "?")[:8], target_region)
                return {"action": "move", "data": {"regionId": target_region},
                        "reason": "PURSUIT: Kejar target!"}

        # Kejar musuh yang terakhir diserang jika HP masih cukup
        if _last_attacked_by and hp >= 40:
            last_attacker = _known_agents.get(_last_attacked_by, {})
            attacker_region = last_attacker.get("regionId", "")
            if attacker_region and attacker_region != region_id and attacker_region not in danger_ids:
                log.info("REVENGE PURSUIT: Kejar %s!", _last_attacked_by[:8])
                return {"action": "move", "data": {"regionId": attacker_region},
                        "reason": "REVENGE: Kejar penyerang!"}

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
    # fallback terakhir kalau tidak ada aksi lain
    return {"action": "rest", "data": {}, "reason": "REST fallback"}
    

# ═══════════════════════════════════════════════════════════════════
#  EVENT HOOKS — Dipanggil dari heartbeat.py / game loop
# ═══════════════════════════════════════════════════════════════════

def on_attacked_by(attacker_id: str, current_turn: int):
    """
    Dipanggil dari heartbeat.py saat bot kita diserang.
    Contoh: brain.on_attacked_by(attacker_id, turn)
    """
    global _last_attacked_by, _last_attacked_turn
    _last_attacked_by = attacker_id
    _last_attacked_turn = current_turn
    log.warning("ATTACKED BY: %s — PREPARING COUNTER!", attacker_id[:8])


def on_enemy_killed(enemy_id: str):
    """Dipanggil saat kita berhasil kill musuh."""
    on_killed_enemy(enemy_id)
    global _hunting_target
    if _hunting_target and _hunting_target.get("id") == enemy_id:
        _hunting_target = None
        log.info("HUNT COMPLETE! %s eliminated.", enemy_id[:8])


def on_we_died(killer_id: str):
    """Dipanggil saat bot kita mati."""
    on_killed_by_enemy(killer_id)
    reset_game_state()


def get_enemy_intel(enemy_id: str) -> dict:
    """Ambil semua data intel tentang satu musuh."""
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
    """Ambil summary semua profil musuh yang tersimpan."""
    return [get_enemy_intel(eid) for eid in _enemy_profiles]


"""
═══════════════════════════════════════════════════════════
  BERSERKER BRAIN v3.0 — CHANGELOG
═══════════════════════════════════════════════════════════

UPGRADE dari v2.3:

[NEW] ENEMY PROFILING SYSTEM
  ✅ Bot mempelajari karakter setiap lawan
  ✅ Menyimpan: HP rata-rata, ATK, senjata favorit, behavior tags
  ✅ Mendeteksi behavior: aggressive, healer, runner, camper, ranged, melee
  ✅ Mendeteksi kelemahan: rush_melee, kite_ranged, burst, outlast, pursuit
  ✅ Menghitung win rate vs setiap musuh
  ✅ Strategi adaptif berdasarkan profil musuh
  ✅ Profil PERSISTEN antar game (tidak direset)

[NEW] SMART INVENTORY MANAGEMENT
  ✅ Drop item paling lemah jika inventory penuh dan ada item lebih baik
  ✅ Tidak akan drop: rewards, senjata terbaik, medkit terakhir
  ✅ Ranking item berdasarkan nilai aktual

[NEW] HP > 40, EP > 60% TARGET
  ✅ EP recovery aktif jika EP < 60%
  ✅ Heal opportunistik jika HP < 60
  ✅ Rest hanya jika EP < 60% dan aman

[NEW] NEVER RETREAT SAAT DISERANG
  ✅ on_attacked_by() hook untuk mendeteksi serangan masuk
  ✅ Counter attack otomatis jika musuh ada di region yang sama
  ✅ Revenge pursuit: kejar penyerang ke region lain
  ✅ Flee hanya jika HP < 12 (hampir mati)

[NEW] RECOVERY MODE
  ✅ Aktif jika HP < 30 dan tidak sedang diserang
  ✅ Prioritas: heal → EP → farm guardian → farm monster → rest
  ✅ Keluar recovery jika HP >= 70
  ✅ Setelah recovery, langsung kembali berburu

[IMPROVED] PURSUIT SYSTEM
  ✅ Kejar hunting target ke region lain
  ✅ Kejar penyerang untuk balas dendam
  ✅ Tidak biarkan musuh kabur jika masih bisa dikejar

═══════════════════════════════════════════════════════════
  CARA INTEGRASI KE HEARTBEAT.PY
═══════════════════════════════════════════════════════════

  import brain

  # Saat bot kita diserang (di event handler damage received):
  brain.on_attacked_by(attacker_id=event["attackerId"], current_turn=turn)

  # Saat kita kill musuh:
  brain.on_enemy_killed(enemy_id=event["targetId"])

  # Saat kita mati:
  brain.on_we_died(killer_id=event["killerId"])

  # Di game loop utama:
  action = brain.decide_action(view=game_state, can_act=True)

  # Debug intel musuh:
  intel = brain.get_all_enemy_intel()
  print(intel)
═══════════════════════════════════════════════════════════
"""
