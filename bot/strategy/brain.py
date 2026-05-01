"""
Strategy brain — AGGRESSIVE COUNTER ATTACK BOT (FIXED)
==========================================================
FOKUS UTAMA (URGENSI TINGGI):
1. JIKA DISERANG → BALAS LANGSUNG! (jangan kabur/heal/move dulu)
2. JIKA MUSUH > 2 → KABUR!
3. JIKA HP < 30 → HEAL ATAU KABUR!
4. JANGAN MOVE SEWENANG-WENANG KALAU SEDANG DISERANG
"""

from bot.utils.logger import get_logger

log = get_logger(__name__)


# =========================
# 🔥 KONFIGURASI
# =========================

WEAPONS = {
    "fist": {"bonus": 0, "range": 0, "priority": 0},
    "dagger": {"bonus": 10, "range": 0, "priority": 10},
    "bow": {"bonus": 5, "range": 1, "priority": 5},
    "pistol": {"bonus": 10, "range": 1, "priority": 10},
    "sword": {"bonus": 20, "range": 0, "priority": 20},
    "sniper": {"bonus": 28, "range": 2, "priority": 28},
    "katana": {"bonus": 35, "range": 0, "priority": 35},
}

RECOVERY_ITEMS = {"medkit": 50, "bandage": 30, "emergency_food": 20}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0, "rain": 0.05, "fog": 0.10, "storm": 0.15,
}

# ── THRESHOLD ──────────────────────────────────────────────────────
CRITICAL_HP = 30        # HP < 30 → HEAL atau KABUR!
FLEE_HP = 25            # HP < 25 → WAJIB KABUR!
MAX_ENEMIES_BEFORE_FLEE = 2  # Jika musuh > 2 → KABUR!

_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_turn_counter: int = 0


def calc_damage(atk: int, weapon_bonus: int, target_def: int, weather: str = "clear") -> int:
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped) -> int:
    if not equipped:
        return 0
    type_id = equipped.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_priority(equipped) -> int:
    if not equipped:
        return 0
    type_id = equipped.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("priority", 0)


def get_weapon_range(equipped) -> int:
    if not equipped:
        return 0
    type_id = equipped.get("typeId", "").lower()
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
    global _map_knowledge, _turn_counter
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _turn_counter = 0
    log.info("=" * 60)
    log.info("⚔️ AGGRESSIVE COUNTER ATTACK BOT (FIXED)")
    log.info("   Prioritas: BALAS SERANGAN! JANGAN KABUR DULU!")
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


def find_healing_item(inventory: list, critical: bool = False) -> dict | None:
    heals = []
    for item in inventory:
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId", "").lower()
        if type_id in RECOVERY_ITEMS:
            heals.append((RECOVERY_ITEMS[type_id], item))
    if not heals:
        return None
    if critical:
        heals.sort(key=lambda x: x[0], reverse=True)
    else:
        heals.sort(key=lambda x: x[0])
    return heals[0][1]


def find_energy_drink(inventory: list) -> dict | None:
    for item in inventory:
        if isinstance(item, dict) and item.get("typeId", "").lower() == "energy_drink":
            return item
    return None


def find_safe_region(connections, danger_ids: set) -> str | None:
    for conn in connections:
        rid = _get_region_id(conn)
        if rid and rid not in danger_ids:
            return rid
    return None


def get_move_ep_cost(terrain: str, weather: str) -> int:
    if terrain == "water" or weather == "storm":
        return 3
    return 2


# =========================
# 🧠 MAIN DECISION (FIXED)
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    PRIORITAS KETAT:
    1. DEATHZONE ESCAPE (override)
    2. COUNTER ATTACK! (jika ada musuh di region yang sama)
    3. HEALING (jika HP kritis)
    4. KABUR (jika outnumbered atau HP terlalu rendah)
    5. PICKUP & EQUIP
    6. MOVEMENT
    """
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
    
    weapon_bonus = get_weapon_bonus(equipped)
    total_atk = atk + weapon_bonus
    
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
    
    connections = view.get("connectedRegions", []) or region.get("connections", [])
    pending_dz = view.get("pendingDeathzones", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if region else ""
    region_weather = region.get("weather", "").lower() if region else ""
    
    if not is_alive:
        return None
    
    # Danger zones
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
    
    move_ep_cost = get_move_ep_cost(region_terrain, region_weather)
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0
    
    # Deteksi musuh di region yang sama
    enemies_here = [a for a in visible_agents
                    if a.get("isAlive", True)
                    and a.get("id") != self_data.get("id")
                    and a.get("regionId") == region_id
                    and not a.get("isGuardian", False)]
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 1: DEATHZONE ESCAPE
    # ═══════════════════════════════════════════════════════════════
    if region.get("isDeathZone", False) or region_id in danger_ids:
        safe = find_safe_region(connections, danger_ids)
        if safe and ep >= move_ep_cost:
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 2: COUNTER ATTACK! (PALING PENTING!)
    # ═══════════════════════════════════════════════════════════════
    if enemies_here and ep >= 2:
        # Pilih musuh dengan HP terendah
        target = min(enemies_here, key=lambda e: e.get("hp", 999))
        enemy_hp = target.get("hp", 100)
        my_damage = calc_damage(total_atk, 0, target.get("def", 5), region_weather)
        
        # ALWAYS ATTACK if we can kill OR we have weapon OR enemy is low
        if enemy_hp <= my_damage * 2 or total_atk >= 20 or enemy_hp < 50:
            log.info("⚔️ COUNTER ATTACK! Target HP=%d, My DMG=%d, My HP=%d", 
                    enemy_hp, my_damage, hp)
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "agent"},
                    "reason": f"⚔️ COUNTER: HP={enemy_hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 3: CRITICAL HEALING (HP sangat rendah)
    # ═══════════════════════════════════════════════════════════════
    if hp < CRITICAL_HP:
        heal = find_healing_item(inventory, critical=True)
        if heal:
            log.info("💚 CRITICAL HEAL: HP=%d -> using %s", hp, heal.get("typeId", "heal"))
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 4: FLEE (jika outnumbered ATAU HP terlalu rendah)
    # ═══════════════════════════════════════════════════════════════
    should_flee = False
    flee_reason = ""
    
    if len(enemies_here) > MAX_ENEMIES_BEFORE_FLEE and hp < 60:
        should_flee = True
        flee_reason = f"OUTNUMBERED ({len(enemies_here)} enemies)"
    elif hp < FLEE_HP:
        should_flee = True
        flee_reason = f"LOW HP (HP={hp})"
    
    if should_flee:
        safe = find_safe_region(connections, danger_ids)
        if safe and ep >= move_ep_cost:
            log.warning("🏃 FLEEING: %s", flee_reason)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"FLEE: {flee_reason}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 5: PICKUP (sMOLTZ > weapon)
    # ═══════════════════════════════════════════════════════════════
    local_items = [i for i in visible_items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    
    # sMOLTZ first
    currency_items = [i for i in local_items 
                      if "rewards" in i.get("typeId", "").lower()]
    if currency_items:
        return {"action": "pickup", "data": {"itemId": currency_items[0]["id"]},
                "reason": "💰 sMOLTZ"}
    
    # Weapon upgrade
    weapon_items = [i for i in local_items if i.get("category") == "weapon"]
    if weapon_items:
        best = max(weapon_items, key=lambda i: get_weapon_priority(i))
        current_priority = get_weapon_priority(equipped) if equipped else 0
        if get_weapon_priority(best) > current_priority:
            log.info("⚔️ PICKUP WEAPON: %s", best.get("typeId", "weapon"))
            return {"action": "pickup", "data": {"itemId": best["id"]},
                    "reason": "⚔️ WEAPON"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 6: EQUIP WEAPON
    # ═══════════════════════════════════════════════════════════════
    if equipped is None or get_weapon_bonus(equipped) == 0:
        for item in inventory:
            if isinstance(item, dict) and item.get("category") == "weapon":
                log.info("⚔️ EQUIP: %s", item.get("typeId", "weapon"))
                return {"action": "equip", "data": {"itemId": item["id"]},
                        "reason": "⚔️ EQUIP WEAPON"}
    
    if not can_act:
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 7: EP RECOVERY
    # ═══════════════════════════════════════════════════════════════
    if ep_ratio < 0.20:
        energy = find_energy_drink(inventory)
        if energy:
            return {"action": "use_item", "data": {"itemId": energy["id"]},
                    "reason": f"EP: {ep}/{max_ep}"}
        
        if not enemies_here:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 8: MOVEMENT (hanya jika tidak ada musuh)
    # ═══════════════════════════════════════════════════════════════
    if not enemies_here and ep >= move_ep_cost and connections:
        safe = find_safe_region(connections, danger_ids)
        if safe:
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "MOVE: Safe region"}
    
    return None


"""
AGGRESSIVE COUNTER ATTACK BOT (FIXED)

PERUBAHAN KRITIS:
1. ✅ COUNTER ATTACK PRIORITY #2 (sebelum healing, sebelum kabur)
2. ✅ Kabur hanya jika outnumbered (>2 musuh) ATAU HP < 25
3. ✅ Healing hanya jika HP < 30
4. ✅ Movement hanya jika TIDAK ADA MUSUH di region yang sama
5. ✅ Bot akan membalas serangan IMMEDIATELY!
"""
