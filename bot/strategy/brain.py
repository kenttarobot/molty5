"""
Strategy brain — BERSERKER MODE (Aggressive Counter Attack)
===========================================================
Filosofi: "Serang balik atau mati! Jangan lari kecuali sekarat"

UPGRADE DARI v1.5.2:
1. PRIORITAS UTAMA: BALAS SERANGAN (sebelum healing, sebelum pickup)
2. Hanya kabur jika HP < 20 atau musuh > 5
3. Healing darurat hanya jika HP < 30
4. EP dijaga minimal 20% untuk bisa attack
5. Target prioritas: musuh dengan HP terendah (finish cepat)
6. Guardian farming tetap priority (120 sMoltz)

PRIORITY CHAIN (BERSERKER):
1. DEATHZONE ESCAPE (masih override)
2. ⚔️ COUNTER ATTACK (WAJIB! jika ada musuh di region)
3. HEALING DARURAT (HP < 30)
4. EP RECOVERY (jika EP < 20%)
5. GUARDIAN FARMING
6. PICKUP (sMOLTZ > Weapon > Healing)
7. MONSTER FARM
8. MOVEMENT (hanya jika aman)
9. REST
"""

from bot.utils.logger import get_logger

log = get_logger(__name__)


# =========================
# 🔥 KONFIGURASI BERSERKER
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

# ── BERSERKER THRESHOLDS (AGRESIF TAPI CERDAS) ───────────────────────
BERSERKER_CONFIG = {
    "HEAL_URGENT": 30,          # HP < 30 → HEAL DARURAT!
    "HEAL_MODERATE": 50,        # HP < 50 → HEAL jika aman
    "FLEE_HP": 20,              # HP < 20 → WAJIB KABUR!
    "FLEE_OUTNUMBERED": 5,      # Musuh > 5 → KABUR!
    "EP_MIN_ATTACK": 0.20,      # Minimal EP 20% untuk attack
    "EP_SAFE": 0.15,            # EP < 15% → rest/energy drink
    "COUNTER_ATTACK": True,     # WAJIB counter attack!
    "HEAL_BEFORE_FIGHT": False, # JANGAN heal dulu, attack dulu!
}

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}


# =========================
# 🔥 FUNGSI DASAR (PRESERVED)
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
    global _known_agents, _map_knowledge
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    log.info("=" * 60)
    log.info("⚔️ BERSERKER MODE ACTIVATED ⚔️")
    log.info("   Prioritaskan BALAS SERANGAN!")
    log.info("   Hanya kabur jika HP < 20 atau musuh > 5")
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
# 🔥 BERSERKER UTILITY
# =========================

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
    """Select target with lowest HP (fastest kill)."""
    return min(targets, key=lambda t: t.get("hp", 999))


def _is_in_range(target: dict, my_region: str, weapon_range: int,
                  connections=None) -> bool:
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
        log.info("⚔️ EQUIP: %s (+%d ATK)", best.get('typeId', 'weapon'), best_bonus)
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')}"}
    return None


def _use_utility_item(inventory: list, hp: int, ep: int, alive_count: int) -> dict | None:
    for item in inventory:
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId", "").lower()
        if type_id == "map":
            log.info("🗺️ Using Map!")
            return {"action": "use_item", "data": {"itemId": item["id"]},
                    "reason": "UTILITY: Using Map"}
    return None


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


# =========================
# 🧠 MAIN DECISION (BERSERKER MODE)
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    BERSERKER MODE - PRIORITY CHAIN:
    1. DEATHZONE ESCAPE
    2. ⚔️ COUNTER ATTACK (WAJIB jika ada musuh!)
    3. HEALING DARURAT (HP < 30)
    4. EP RECOVERY
    5. GUARDIAN FARMING
    6. PICKUP
    7. MONSTER FARM
    8. MOVEMENT
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

    # ── Danger map ───────────────────────────────────────────────────
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

    # Deteksi musuh di region yang sama (yang HARUS dilawan!)
    enemies_here = [a for a in visible_agents
                    if a.get("isAlive", True)
                    and a.get("id") != self_data.get("id")
                    and a.get("regionId") == region_id
                    and not a.get("isGuardian", False)]
    
    guardians_here = [a for a in visible_agents
                      if a.get("isGuardian", False) and a.get("isAlive", True)
                      and a.get("regionId") == region_id]

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 1: DEATHZONE ESCAPE (masih override)
    # ═══════════════════════════════════════════════════════════════════
    if region.get("isDeathZone", False):
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("💀 DEATHZONE! Escaping to %s", safe)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}

    if region_id in danger_ids:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("⚠️ Pending DZ! Escaping to %s", safe[:8])
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "PRE-ESCAPE: Death zone soon"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 2: COUNTER ATTACK! (WAJIB jika ada musuh!)
    # ═══════════════════════════════════════════════════════════════════
    if enemies_here and ep_ratio >= BERSERKER_CONFIG["EP_MIN_ATTACK"]:
        # Pilih target dengan HP terendah (paling cepat mati)
        target = _select_weakest(enemies_here)
        w_range = get_weapon_range(equipped)
        
        if _is_in_range(target, region_id, w_range, connections):
            my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                target.get("def", 5), region_weather)
            enemy_hp = target.get("hp", 100)
            
            log.info("⚔️ BERSERKER ATTACK! Enemy HP=%d, My DMG=%d, My HP=%d", 
                    enemy_hp, my_dmg, hp)
            
            # SERANG! Apapun kondisinya (kecuali EP habis)
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "agent"},
                    "reason": f"⚔️ BERSERKER: HP={enemy_hp}"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 3: HEALING DARURAT (HP terlalu rendah)
    # ═══════════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HEAL_URGENT"]:
        heal = _find_healing_item(inventory, critical=True)
        if heal:
            log.info("💚 URGENT HEAL: HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"URGENT HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 4: FLEE (hanya jika benar-benar terdesak)
    # ═══════════════════════════════════════════════════════════════════
    should_flee = False
    flee_reason = ""
    
    if hp < BERSERKER_CONFIG["FLEE_HP"]:
        should_flee = True
        flee_reason = f"CRITICAL HP ({hp})"
    elif len(enemies_here) >= BERSERKER_CONFIG["FLEE_OUTNUMBERED"] and hp < 60:
        should_flee = True
        flee_reason = f"OUTNUMBERED ({len(enemies_here)} enemies)"
    
    if should_flee:
        safe = _find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("🏃 FLEEING: %s", flee_reason)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"FLEE: {flee_reason}"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 5: GUARDIAN FARMING (120 sMoltz)
    # ═══════════════════════════════════════════════════════════════════
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    if guardians and ep >= 2 and hp >= 40:
        target = _select_weakest(guardians)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            my_dmg = calc_damage(atk, get_weapon_bonus(equipped),
                                target.get("def", 5), region_weather)
            if my_dmg >= 10 or target.get("hp", 100) <= my_dmg * 3:
                log.info("💰 GUARDIAN HUNT! 120 sMoltz!")
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "💰 GUARDIAN: 120 sMoltz!"}

    # ═══════════════════════════════════════════════════════════════════
    # FREE ACTIONS (pickup, equip, utility)
    # ═══════════════════════════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 6: MODERATE HEALING (HP rendah tapi aman)
    # ═══════════════════════════════════════════════════════════════════
    if hp < BERSERKER_CONFIG["HEAL_MODERATE"] and not enemies_here and not guardians_here:
        heal = _find_healing_item(inventory, critical=False)
        if heal:
            log.info("💚 MODERATE HEAL: HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 7: EP RECOVERY
    # ═══════════════════════════════════════════════════════════════════
    if ep_ratio < BERSERKER_CONFIG["EP_SAFE"]:
        energy_drink = _find_energy_drink(inventory)
        if energy_drink:
            return {"action": "use_item", "data": {"itemId": energy_drink["id"]},
                    "reason": f"EP: {ep}/{max_ep}"}
        
        if not enemies_here and not guardians_here and region_id not in danger_ids:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 8: MONSTER FARMING
    # ═══════════════════════════════════════════════════════════════════
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep >= 1 and hp > 30:
        target = _select_weakest(monsters)
        w_range = get_weapon_range(equipped)
        if _is_in_range(target, region_id, w_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER: HP={target.get('hp', '?')}"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 9: FACILITY INTERACTION
    # ═══════════════════════════════════════════════════════════════════
    if interactables and ep >= 2 and not region.get("isDeathZone"):
        facility = _select_facility(interactables, hp, ep)
        if facility:
            return {"action": "interact", "data": {"interactableId": facility["id"]},
                    "reason": f"FACILITY: {facility.get('type', 'unknown')}"}

    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 10: MOVEMENT
    # ═══════════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        move_target = _choose_move_target(connections, danger_ids,
                                           region, visible_items, alive_count)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "MOVE: Strategic"}

    # ═══════════════════════════════════════════════════════════════════
    # LAST RESORT: REST
    # ═══════════════════════════════════════════════════════════════════
    if ep < 4 and not enemies_here and not region.get("isDeathZone") and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}/{max_ep}"}

    return None


"""
BERSERKER MODE v2.0 - UPGRADE DARI v1.5.2

PERUBAHAN UTAMA:
1. ✅ PRIORITAS UTAMA: COUNTER ATTACK (sekarang di #2, sebelum healing!)
2. ✅ Hanya kabur jika HP < 20 atau musuh > 5 (tidak pengecut!)
3. ✅ Healing darurat hanya jika HP < 30 (lebih hemat)
4. ✅ EP minimal attack 20% (tidak kehabisan EP)
5. ✅ Target prioritas: musuh dengan HP terendah (finish cepat)
6. ✅ Guardian farming tetap priority (120 sMoltz)
7. ✅ Tidak ada lagi "Favorable agent combat" yang ragu-ragu

PRIORITY CHAIN:
1. DEATHZONE ESCAPE
2. ⚔️ COUNTER ATTACK (WAJIB!)
3. HEALING DARURAT (HP < 30)
4. FLEE (HP < 20 atau musuh > 5)
5. GUARDIAN FARMING
6. PICKUP & EQUIP
7. MODERATE HEALING (HP < 50, aman)
8. EP RECOVERY
9. MONSTER FARM
10. MOVEMENT

Bot sekarang akan:
- LANGSUNG membalas setiap serangan
- TIDAK lari kecuali sekarat
- TIDAK healing dulu sebelum attack
- LEBIH BENGIS tapi tetap pintar jaga EP
"""
