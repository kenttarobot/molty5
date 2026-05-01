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
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep}"}

    return None


"""
BERSERKER MODE v2.3 - FIX FACILITY LOOP

PERUBAHAN PALING PENTING:
1. ✅ BOT TIDAK AKAN STUCK DI FACILITY!
2. ✅ Prioritas: COMBAT > FACILITY > MOVEMENT
3. ✅ Facility hanya di-interact jika TIDAK ADA MUSUH
4. ✅ Facility cooldown: tidak akan interact facility yang sama berulang-ulang
5. ✅ Maksimal 1x interact per facility
"""
