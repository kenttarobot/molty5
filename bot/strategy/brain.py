"""
Strategy brain — AGGRESSIVE COUNTER ATTACK BOT (UPGRADED)
==========================================================
FOKUS UTAMA:
1. BALAS SERANGAN! Jangan cuma healing doang!
2. Kabur jika terlalu banyak musuh
3. Prioritaskan musuh yang sudah melukai kita
4. SMART FARMING: Prioritaskan sMOLTZ & senjata
5. EP & HP MANAGEMENT: Jaga agar tidak cepat habis

UPGRADE v2.1:
- Improved target selection (weakest enemy first)
- Better fleeing logic (kabur kalau kalah jumlah)
- Smart healing (jangan boros)
- Counter attack priority! 
"""

from bot.utils.logger import get_logger
import random

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

ITEM_PRIORITY = {
    "rewards": 1000,      # sMOLTZ - tertinggi!
    "katana": 900,
    "sniper": 850,
    "sword": 800,
    "medkit": 550,
    "bandage": 500,
    "emergency_food": 450,
    "energy_drink": 300,
    "pistol": 250,
    "dagger": 200,
    "bow": 150,
}

RECOVERY_ITEMS = {
    "medkit": 50,
    "bandage": 30,
    "emergency_food": 20,
}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0,
    "rain": 0.05,
    "fog": 0.10,
    "storm": 0.15,
}

# ── THRESHOLD YANG LEBIH CERDAS ──────────────────────────────────────
HEAL_THRESHOLD = 35       # Healing hanya jika HP < 35 (lebih hemat!)
CRITICAL_HP = 25          # HP kritis, harus healing segera
FLEE_HP_THRESHOLD = 30    # Kabur jika HP < 30
FLEE_OUTNUMBERED = 3      # Kabur jika musuh >= 3

EP_MIN_FIGHT = 0.25       # Minimal EP 25% untuk fight
EP_SAFE_REST = 0.20       # Rest jika EP < 20%

# ── Reward values ────────────────────────────────────────────────────
GUARDIAN_SMOLTZ = 120
PLAYER_KILL_SMOLTZ = 100

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_turn_counter: int = 0
_total_smoltz: int = 0
_kill_count: int = 0


# =========================
# 🔥 FUNGSI DASAR
# =========================

def calc_damage(atk: int, weapon_bonus: int, target_def: int, weather: str = "clear") -> int:
    """Hitung damage dengan weather penalty."""
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))


def get_weapon_bonus(equipped) -> int:
    """Dapatkan ATK bonus dari senjata yang dipakai."""
    if not equipped:
        return 0
    type_id = equipped.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_priority(equipped) -> int:
    """Dapatkan priority weapon (semakin tinggi semakin bagus)."""
    if not equipped:
        return 0
    type_id = equipped.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("priority", 0)


def get_weapon_range(equipped) -> int:
    """Dapatkan range weapon (0=melee, 1=ranged, 2=sniper)."""
    if not equipped:
        return 0
    type_id = equipped.get("typeId", "").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)


def _resolve_region(entry, view: dict):
    """Resolve region entry (bisa string atau dict)."""
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, str):
        for r in view.get("visibleRegions", []):
            if isinstance(r, dict) and r.get("id") == entry:
                return r
    return None


def _get_region_id(entry) -> str:
    """Extract region ID dari entry."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id", "")
    return ""


def reset_game_state():
    """Reset state untuk game baru."""
    global _known_agents, _map_knowledge, _turn_counter, _total_smoltz, _kill_count
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _turn_counter = 0
    _total_smoltz = 0
    _kill_count = 0
    log.info("=" * 60)
    log.info("⚔️ AGGRESSIVE COUNTER ATTACK BOT v2.1")
    log.info("   Prioritaskan membalas serangan!")
    log.info("   Kabur jika terlalu banyak musuh!")
    log.info("=" * 60)


def learn_from_map(view: dict):
    """Belajar map setelah menggunakan Map item."""
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
# 🔥 HEALING & UTILITY
# =========================

def find_healing_item(inventory: list, critical: bool = False) -> dict | None:
    """
    Cari healing item terbaik.
    critical=True: pakai yang paling besar (Medkit dulu)
    critical=False: pakai yang paling kecil (hemat)
    """
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
        heals.sort(key=lambda x: x[0], reverse=True)  # Besar dulu
    else:
        heals.sort(key=lambda x: x[0])  # Kecil dulu (hemat)
    
    return heals[0][1]


def find_energy_drink(inventory: list) -> dict | None:
    """Cari energy drink untuk EP recovery."""
    for item in inventory:
        if isinstance(item, dict) and item.get("typeId", "").lower() == "energy_drink":
            return item
    return None


def find_safe_region(connections, danger_ids: set) -> str | None:
    """Cari region yang aman (bukan death zone)."""
    for conn in connections:
        rid = _get_region_id(conn)
        if rid and rid not in danger_ids:
            return rid
    return None


def get_move_ep_cost(terrain: str, weather: str) -> int:
    """Hitung EP cost untuk move."""
    if terrain == "water" or weather == "storm":
        return 3
    return 2


def estimate_enemy_weapon_bonus(agent: dict) -> int:
    """Estimasi bonus senjata musuh."""
    weapon = agent.get("equippedWeapon")
    return get_weapon_bonus(weapon) if weapon else 0


def track_smoltz(view: dict):
    """Track sMOLTZ dari kill."""
    global _total_smoltz, _kill_count
    logs = view.get("recentLogs", [])
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message", "").lower()
        if "killed" in msg and "guardian" in msg:
            _total_smoltz += GUARDIAN_SMOLTZ
            _kill_count += 1
            log.info("💰 GUARDIAN KILL! +%d sMOLTZ (Total: %d)", GUARDIAN_SMOLTZ, _total_smoltz)
        elif "killed" in msg and "player" in msg:
            _total_smoltz += PLAYER_KILL_SMOLTZ
            _kill_count += 1
            log.info("💰 PLAYER KILL! +%d sMOLTZ (Total: %d, Kills: %d)", 
                    PLAYER_KILL_SMOLTZ, _total_smoltz, _kill_count)


# =========================
# 🔥 TARGET SELECTION
# =========================

def select_weakest_target(targets: list) -> dict | None:
    """Pilih target dengan HP terendah (paling mudah dibunuh)."""
    if not targets:
        return None
    alive_targets = [t for t in targets if t.get("hp", 0) > 0]
    if not alive_targets:
        return None
    return min(alive_targets, key=lambda t: t.get("hp", 999))


def can_execute(enemy_hp: int, my_damage: int) -> bool:
    """Cek apakah bisa kill dalam 1-2 hit."""
    return enemy_hp <= my_damage * 2


# =========================
# 🧠 MAIN DECISION ENGINE
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    MAIN DECISION ENGINE - AGGRESSIVE COUNTER ATTACK
    
    PRIORITY CHAIN:
    1. DEATHZONE ESCAPE (override semua)
    2. BALAS SERANGAN! (jika ada musuh)
    3. KABUR jika terlalu banyak musuh
    4. PICKUP (sMOLTZ > weapon > healing)
    5. EQUIP WEAPON
    6. HEALING (hanya jika HP kritis)
    7. EP RECOVERY
    8. FARM MONSTER
    9. MOVEMENT
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
    
    # Track sMOLTZ
    track_smoltz(view)
    
    weapon_bonus = get_weapon_bonus(equipped)
    total_atk = atk + weapon_bonus
    
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
    
    connections = view.get("connectedRegions", []) or region.get("connections", [])
    pending_dz = view.get("pendingDeathzones", [])
    region_id = region.get("id", "")
    region_terrain = region.get("terrain", "").lower() if region else ""
    region_weather = region.get("weather", "").lower() if region else ""
    
    if not is_alive:
        return None
    
    # ── Danger zones ────────────────────────────────────────────────
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
    
    # ── Enemy detection ─────────────────────────────────────────────
    enemies_in_region = [a for a in visible_agents
                         if a.get("isAlive", True)
                         and a.get("id") != self_data.get("id")
                         and a.get("regionId") == region_id
                         and not a.get("isGuardian", False)]
    
    guardians_in_region = [a for a in visible_agents
                           if a.get("isGuardian", False) 
                           and a.get("isAlive", True)
                           and a.get("regionId") == region_id]
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 1: DEATHZONE ESCAPE
    # ═══════════════════════════════════════════════════════════════
    if region.get("isDeathZone", False) or region_id in danger_ids:
        safe = find_safe_region(connections, danger_ids)
        if safe and ep >= move_ep_cost:
            log.warning("💀 DEATHZONE! Escaping to %s", safe[:8])
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 2: BALAS SERANGAN! (PALING PENTING!)
    # ═══════════════════════════════════════════════════════════════
    if enemies_in_region and ep >= 2 and hp > CRITICAL_HP:
        # Pilih musuh dengan HP terendah
        target = select_weakest_target(enemies_in_region)
        if target:
            enemy_hp = target.get("hp", 100)
            my_damage = calc_damage(total_atk, 0, target.get("def", 5), region_weather)
            
            # Attack jika: bisa execute ATAU HP kita cukup ATAU musuh low HP
            if can_execute(enemy_hp, my_damage) or hp > 50 or enemy_hp < 40:
                log.info("⚔️ COUNTER ATTACK! Target HP=%d, My DMG=%d", enemy_hp, my_damage)
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"⚔️ COUNTER: HP={enemy_hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 3: KABUR JIKA TERLALU BANYAK MUSUH
    # ═══════════════════════════════════════════════════════════════
    if len(enemies_in_region) >= FLEE_OUTNUMBERED and hp < 60:
        safe = find_safe_region(connections, danger_ids)
        if safe and ep >= move_ep_cost:
            log.warning("🏃 OUTNUMBERED! (%d enemies) FLEEING!", len(enemies_in_region))
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "FLEE: Outnumbered!"}
    
    # Kabur juga jika HP terlalu rendah
    if hp < FLEE_HP_THRESHOLD and enemies_in_region:
        safe = find_safe_region(connections, danger_ids)
        if safe and ep >= move_ep_cost:
            log.warning("🏃 LOW HP! HP=%d, FLEEING!", hp)
            return {"action": "move", "data": {"regionId": safe},
                    "reason": f"FLEE: HP={hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 4: PICKUP (sMOLTZ > weapon > healing)
    # ═══════════════════════════════════════════════════════════════
    local_items = [i for i in visible_items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    
    # 4a. sMOLTZ (priority tertinggi!)
    currency_items = [i for i in local_items 
                      if "rewards" in i.get("typeId", "").lower()
                      or "moltz" in i.get("name", "").lower()]
    if currency_items:
        best = max(currency_items, key=lambda i: i.get("amount", 1))
        log.info("💰 sMOLTZ PICKUP! +%d", best.get("amount", 50))
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": "💰 sMOLTZ"}
    
    # 4b. Weapon upgrade
    weapon_items = [i for i in local_items if i.get("category") == "weapon"]
    if weapon_items:
        best_weapon = max(weapon_items, key=lambda i: get_weapon_priority(i))
        current_priority = get_weapon_priority(equipped) if equipped else 0
        if get_weapon_priority(best_weapon) > current_priority:
            log.info("⚔️ WEAPON PICKUP: %s", best_weapon.get("typeId", "weapon"))
            return {"action": "pickup", "data": {"itemId": best_weapon["id"]},
                    "reason": "⚔️ WEAPON UPGRADE"}
    
    # 4c. Healing item (jika HP rendah)
    if hp < HEAL_THRESHOLD:
        healing_items = [i for i in local_items 
                        if i.get("typeId", "").lower() in RECOVERY_ITEMS]
        if healing_items:
            best_heal = healing_items[0]
            log.info("💚 HEALING PICKUP: %s (HP=%d)", best_heal.get("typeId", "heal"), hp)
            return {"action": "pickup", "data": {"itemId": best_heal["id"]},
                    "reason": f"HEALING: HP={hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 5: EQUIP WEAPON
    # ═══════════════════════════════════════════════════════════════
    if equipped is None or get_weapon_bonus(equipped) == 0:
        for item in inventory:
            if isinstance(item, dict) and item.get("category") == "weapon":
                log.info("⚔️ EQUIP WEAPON: %s", item.get("typeId", "weapon"))
                return {"action": "equip", "data": {"itemId": item["id"]},
                        "reason": "⚔️ EQUIP WEAPON"}
    
    if not can_act:
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 6: HEALING (hanya jika HP benar-benar rendah!)
    # ═══════════════════════════════════════════════════════════════
    if hp < CRITICAL_HP:
        heal = find_healing_item(inventory, critical=True)
        if heal:
            log.info("💚 CRITICAL HEAL: HP=%d -> using %s", hp, heal.get("typeId", "heal"))
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}
    elif hp < HEAL_THRESHOLD and not enemies_in_region:
        # Healing hanya jika tidak ada musuh (safe)
        heal = find_healing_item(inventory, critical=False)
        if heal:
            log.info("💚 SAFE HEAL: HP=%d -> using %s", hp, heal.get("typeId", "heal"))
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 7: EP RECOVERY
    # ═══════════════════════════════════════════════════════════════
    if ep_ratio < EP_SAFE_REST:
        energy = find_energy_drink(inventory)
        if energy:
            log.info("⚡ EP RECOVERY: %d/%d -> energy drink", ep, max_ep)
            return {"action": "use_item", "data": {"itemId": energy["id"]},
                    "reason": f"EP: {ep}/{max_ep}"}
        
        if not enemies_in_region:
            log.info("😴 REST: EP=%d/%d", ep, max_ep)
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 8: FARM MONSTER (jika aman)
    # ═══════════════════════════════════════════════════════════════
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0 and m.get("regionId") == region_id]
    if monsters and ep >= 1 and hp > 40 and not enemies_in_region:
        target = select_weakest_target(monsters)
        if target:
            log.info("🐾 MONSTER FARM: HP=%d", target.get("hp", 0))
            return {"action": "attack", "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": "MONSTER FARM"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 9: GUARDIAN FARM (jika HP cukup)
    # ═══════════════════════════════════════════════════════════════
    if guardians_in_region and ep >= 2 and hp > 50:
        target = select_weakest_target(guardians_in_region)
        if target:
            log.info("💰 GUARDIAN HUNT! +120 sMOLTZ!")
            return {"action": "attack", "data": {"targetId": target["id"], "targetType": "agent"},
                    "reason": "💰 GUARDIAN: 120 sMOLTZ!"}
    
    # ═══════════════════════════════════════════════════════════════
    # PRIORITY 10: MOVEMENT
    # ═══════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        safe = find_safe_region(connections, danger_ids)
        if safe:
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "MOVE: Safe region"}
    
    return None


"""
================================================================================
AGGRESSIVE COUNTER ATTACK BOT v2.1 (UPGRADED)
================================================================================

PERUBAHAN UTAMA DARI VERSI SEBELUMNYA:
----------------------------------------
1. ✅ BALAS SERANGAN! (Priority #2 setelah deathzone)
2. ✅ Kabur jika terlalu banyak musuh (>=3)
3. ✅ Healing hanya jika HP < 35 (lebih hemat)
4. ✅ Prioritaskan sMOLTZ dulu, baru weapon, baru healing
5. ✅ Attack balas jika ada musuh di region yang sama
6. ✅ Kabur jika HP terlalu rendah (<30)
7. ✅ Critical heal pakai item besar, safe heal pakai item kecil

PRIORITY CHAIN:
--------------
1. DEATHZONE ESCAPE (override semua)
2. BALAS SERANGAN! (counter attack)
3. KABUR jika outnumbered atau low HP
4. PICKUP (sMOLTZ > weapon > healing)
5. EQUIP WEAPON
6. HEALING (critical > safe)
7. EP RECOVERY
8. FARM MONSTER
9. GUARDIAN FARM
10. MOVEMENT

THRESHOLDS:
-----------
- HEAL_THRESHOLD = 35 (heal jika HP < 35)
- CRITICAL_HP = 25 (heal besar jika HP < 25)
- FLEE_HP_THRESHOLD = 30 (kabur jika HP < 30)
- FLEE_OUTNUMBERED = 3 (kabur jika musuh >= 3)
- EP_MIN_FIGHT = 25% (minimal EP untuk fight)
================================================================================
"""
