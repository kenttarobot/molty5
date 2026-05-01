"""
Strategy brain — BALANCED AGGRESSIVE BOT
==================================================
FILOSOFI: "Strong and steady wins the race"

KARAKTER:
- AGGRESIF TAPI CERDAS: Attack tapi jaga HP/EP
- TAHAN LAMA: Prioritaskan survival
- SMART FARMING: Cari target mudah
- HEALING AWARE: Jangan biarkan HP kritis

BALANCE:
- HP: Jaga di atas 50%
- EP: Jaga di atas 40%
- Attack: Hanya jika bisa menang atau target HP rendah
- Healing: Prioritaskan sebelum fight
"""

from bot.utils.logger import get_logger
import os
import random

log = get_logger(__name__)


# =========================
# 🔥 BALANCED CONFIGURATION
# =========================

# ── HEALTH & EP MANAGEMENT (KUNCI KELANGSUNGAN HIDUP!) ───────────────
HEALTH_CONFIG = {
    "HP_CRITICAL": 35,           # HP < 35% → HEAL NOW!
    "HP_LOW": 55,                # HP < 55% → Heal if safe
    "HP_SAFE": 70,               # HP > 70% → Good to fight
    "HP_MIN_TO_FIGHT": 45,       # Minimal HP untuk berani fight
    
    "EP_CRITICAL": 0.30,         # EP < 30% → Rest/Energy drink
    "EP_LOW": 0.45,              # EP < 45% → Be careful
    "EP_SAFE": 0.60,             # EP > 60% → Good to fight
    "EP_MIN_TO_FIGHT": 0.35,     # Minimal EP untuk fight
    
    "HEAL_PRIORITY": "small_first",  # Pakai heal kecil dulu
}

# ── KILLER INSTINCT (TETAP AGGRESIF) ──────────────────────────────────
KILLER_CONFIG = {
    "KILL_STEAL_THRESHOLD": 40,   # Buru musuh HP < 40
    "EASY_KILL_THRESHOLD": 55,    # HP < 55 = easy kill
    "EXECUTE_MULTIPLIER": 2,      # Execute if HP <= damage × 2
    "GUARDIAN_PRIORITY": True,    # Guardian tetap prioritas
}

# ── Mode selection (based on alive count) ────────────────────────────
AGGRESSIVE_MODE_THRESHOLD = 40    # Early game (alive > 40) = agresif
# Late game (alive <= 40) = survival mode

# ── Weapon stats
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
    "rewards": 1000,      # sMOLTZ - highest!
    "medkit": 550,        # Healing - HIGH priority!
    "bandage": 500,       # Healing
    "emergency_food": 450,
    "katana": 400,
    "sniper": 380,
    "sword": 350,
    "energy_drink": 300,  # EP recovery
    "pistol": 250,
    "dagger": 200,
    "bow": 150,
}

RECOVERY_ITEMS = {
    "medkit": 50, "bandage": 30, "emergency_food": 20,
}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0, "rain": 0.05, "fog": 0.10, "storm": 0.15,
}

GUARDIAN_SMOLTZ = 120
PLAYER_KILL_SMOLTZ = 100


# =========================
# 🔥 GLOBAL STATE
# =========================

_known_agents: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_turn_counter: int = 0
_total_smoltz: int = 0
_kill_count: int = 0
_damage_taken: int = 0


# =========================
# 🔥 HELPER FUNCTIONS
# =========================

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


def has_weapon_equipped(equipped) -> bool:
    return get_weapon_bonus(equipped) > 0 if equipped else False


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
    global _known_agents, _map_knowledge, _turn_counter, _total_smoltz, _kill_count, _damage_taken
    _known_agents = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _turn_counter = 0
    _total_smoltz = 0
    _kill_count = 0
    _damage_taken = 0
    log.info("=" * 60)
    log.info("⚖️ BALANCED AGGRESSIVE BOT v1.0")
    log.info("   Strong and steady wins the race")
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
# 🔥 HEALTH & EP MANAGEMENT (KUNCI TIDAK CEPAT MATI!)
# =========================

def check_hp_status(hp: int) -> dict:
    """Check HP and return action recommendation."""
    if hp < HEALTH_CONFIG["HP_CRITICAL"]:
        return {"status": "critical", "need_heal": True, "can_fight": False, "priority": 1}
    elif hp < HEALTH_CONFIG["HP_LOW"]:
        return {"status": "low", "need_heal": True, "can_fight": True, "priority": 2}
    else:
        return {"status": "good", "need_heal": False, "can_fight": True, "priority": 4}


def check_ep_status(ep: int, max_ep: int) -> dict:
    """Check EP and return action recommendation."""
    ep_ratio = ep / max_ep if max_ep > 0 else 1.0
    
    if ep_ratio < HEALTH_CONFIG["EP_CRITICAL"]:
        return {"status": "critical", "need_rest": True, "can_fight": False, "priority": 1}
    elif ep_ratio < HEALTH_CONFIG["EP_LOW"]:
        return {"status": "low", "need_rest": True, "can_fight": True, "priority": 2}
    else:
        return {"status": "good", "need_rest": False, "can_fight": True, "priority": 3}


def find_best_healing_item(inventory: list, critical: bool = False) -> dict | None:
    """
    Find best healing item.
    critical=True: use biggest heal (Medkit first)
    critical=False: use smallest heal first (save big for emergency)
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
        # Critical: use biggest heal
        heals.sort(key=lambda x: x[0], reverse=True)
    else:
        # Normal: use smallest heal first
        heals.sort(key=lambda x: x[0])
    
    return heals[0][1]


def find_energy_drink(inventory: list) -> dict | None:
    for item in inventory:
        if isinstance(item, dict) and item.get("typeId", "").lower() == "energy_drink":
            return item
    return None


# =========================
# 🔥 TARGET SELECTION (PINTAR, JANGAN ASUL!)
# =========================

def can_execute(enemy_hp: int, my_damage: int) -> bool:
    """Check if can kill enemy in 1-2 hits."""
    return enemy_hp <= my_damage * HEALTH_CONFIG["EXECUTE_MULTIPLIER"]


def is_fight_worth_it(my_hp: int, my_damage: int, enemy_hp: int, enemy_damage: int) -> tuple[bool, str]:
    """
    Smart fight decision.
    Returns: (should_fight, reason)
    """
    hits_to_kill = (enemy_hp + my_damage - 1) // my_damage
    hits_to_die = (my_hp + enemy_damage - 1) // enemy_damage if enemy_damage > 0 else 999
    
    # CASE 1: Execute - pasti mati
    if can_execute(enemy_hp, my_damage):
        return True, "EXECUTE"
    
    # CASE 2: We win
    if hits_to_kill <= hits_to_die:
        return True, f"WIN ({hits_to_kill} hits)"
    
    # CASE 3: Enemy low HP, worth trade
    if enemy_hp < 40:
        return True, "LOW_HP_ENEMY"
    
    # CASE 4: We will die first
    if hits_to_die <= 2:
        return False, "TOO_STRONG"
    
    # CASE 5: Default - fight only if HP > 50
    return my_hp > 50, "DEFAULT"


def select_best_target(enemies: list, my_hp: int, my_damage: int, weather: str) -> dict | None:
    """Select best target to attack (smart priority)."""
    if not enemies:
        return None
    
    candidates = []
    
    for enemy in enemies:
        enemy_hp = enemy.get("hp", 0)
        if enemy_hp <= 0:
            continue
        
        enemy_def = enemy.get("def", 5)
        enemy_atk = enemy.get("atk", 10)
        enemy_weapon = get_weapon_bonus(enemy.get("equippedWeapon"))
        enemy_damage = calc_damage(enemy_atk, enemy_weapon, 5, weather)
        
        # Calculate score
        score = 0
        
        # Execute priority (highest)
        if can_execute(enemy_hp, my_damage):
            score = 10000 + (100 - enemy_hp)
        
        # Kill steal (HP < 40)
        elif enemy_hp < KILLER_CONFIG["KILL_STEAL_THRESHOLD"]:
            score = 5000 + (100 - enemy_hp)
        
        # Easy kill (HP < 55)
        elif enemy_hp < KILLER_CONFIG["EASY_KILL_THRESHOLD"]:
            score = 2000 + (100 - enemy_hp)
        
        # Guardian bonus
        if enemy.get("isGuardian", False):
            score += 500
        
        # Penalty for dangerous enemies
        if enemy_damage > my_damage * 1.5 and enemy_hp > 50:
            score -= 300
        
        candidates.append((score, enemy))
    
    if not candidates:
        return None
    
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# =========================
# 🔥 PICKUP & INVENTORY
# =========================

def pickup_priority(items: list, inventory: list, region_id: str, equipped, hp: int) -> dict | None:
    """
    Smart pickup priority:
    1. Healing items (if HP low)
    2. sMOLTZ (always)
    3. Weapons (upgrade)
    """
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None
    
    hp_status = check_hp_status(hp)
    
    # 1. Healing items (high priority if HP low!)
    if hp_status["need_heal"]:
        healing_items = [i for i in local_items 
                        if i.get("typeId", "").lower() in RECOVERY_ITEMS]
        if healing_items:
            # Prioritize bigger heals when critical
            if hp_status["status"] == "critical":
                healing_items.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0), reverse=True)
            else:
                healing_items.sort(key=lambda i: RECOVERY_ITEMS.get(i.get("typeId", "").lower(), 0))
            
            best = healing_items[0]
            log.info("💚 HEALING PICKUP: %s (HP=%d)", best.get("typeId", "heal"), hp)
            return {"action": "pickup", "data": {"itemId": best["id"]},
                    "reason": f"HEALING: HP={hp}"}
    
    # 2. sMOLTZ currency
    currency_items = [i for i in local_items 
                      if "rewards" in i.get("typeId", "").lower() 
                      or "moltz" in i.get("name", "").lower()]
    if currency_items:
        best = max(currency_items, key=lambda i: i.get("amount", 1))
        log.info("💰 sMOLTZ PICKUP!")
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": "💰 sMOLTZ"}
    
    # 3. Weapons (upgrade)
    weapon_items = [i for i in local_items if i.get("category") == "weapon"]
    if weapon_items:
        weapon_items.sort(key=lambda i: get_weapon_priority(i), reverse=True)
        best = weapon_items[0]
        current_priority = get_weapon_priority(equipped) if equipped else 0
        if get_weapon_priority(best) > current_priority:
            log.info("⚔️ WEAPON PICKUP: %s", best.get("typeId", "weapon"))
            return {"action": "pickup", "data": {"itemId": best["id"]},
                    "reason": f"WEAPON: {best.get('typeId', 'weapon')}"}
    
    return None


def equip_best_weapon(inventory: list, equipped) -> dict | None:
    """Auto-equip best weapon."""
    current_priority = get_weapon_priority(equipped) if equipped else 0
    best = None
    
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("category") == "weapon":
            priority = get_weapon_priority(item)
            if priority > current_priority:
                best = item
                current_priority = priority
    
    if best:
        log.info("⚔️ EQUIP: %s (+%d ATK)", best.get("typeId", "weapon"), get_weapon_bonus(best))
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"EQUIP: {best.get('typeId', 'weapon')}"}
    return None


# =========================
# 🔥 MOVEMENT
# =========================

def find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find region not in death zone."""
    for conn in connections:
        rid = _get_region_id(conn)
        if rid and rid not in danger_ids:
            return rid
    return None


def choose_move_target(connections, danger_ids: set, visible_items: list, 
                        enemies: list, current_region_id: str) -> str | None:
    """Choose best region to move to."""
    # 1. Chase low HP enemies
    low_hp_enemies = [e for e in enemies if e.get("hp", 100) < 50]
    if low_hp_enemies:
        for conn in connections:
            rid = _get_region_id(conn)
            if rid and rid not in danger_ids:
                for enemy in low_hp_enemies:
                    if enemy.get("regionId") == rid:
                        return rid
    
    # 2. Move to item-rich region
    item_regions = set()
    for item in visible_items:
        if isinstance(item, dict):
            item_regions.add(item.get("regionId", ""))
    
    for conn in connections:
        rid = _get_region_id(conn)
        if rid and rid not in danger_ids and rid in item_regions:
            return rid
    
    # 3. Any safe region
    return find_safe_region(connections, danger_ids)


def in_range(target: dict, my_region: str, weapon_range: int, connections=None) -> bool:
    target_region = target.get("regionId", "")
    if not target_region or target_region == my_region:
        return True
    if weapon_range >= 1 and connections:
        adj_ids = set()
        for conn in connections:
            adj_ids.add(_get_region_id(conn))
        if target_region in adj_ids:
            return True
    return False


def get_move_ep_cost(terrain: str, weather: str) -> int:
    if terrain == "water" or weather == "storm":
        return 3
    return 2


def estimate_enemy_weapon_bonus(agent: dict) -> int:
    weapon = agent.get("equippedWeapon")
    return get_weapon_bonus(weapon) if weapon else 0


def track_agents(visible_agents: list, my_id: str, my_region: str):
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


def track_smoltz(view: dict):
    global _total_smoltz, _kill_count
    logs = view.get("recentLogs", [])
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message", "").lower()
        if "killed" in msg and "guardian" in msg:
            _total_smoltz += GUARDIAN_SMOLTZ
            _kill_count += 1
            log.info("💰 GUARDIAN KILL! +%d sMOLTZ", GUARDIAN_SMOLTZ)
        elif "killed" in msg and "player" in msg:
            _total_smoltz += PLAYER_KILL_SMOLTZ
            _kill_count += 1
            log.info("💰 PLAYER KILL! +%d sMOLTZ", PLAYER_KILL_SMOLTZ)


# =========================
# 🧠 MAIN DECISION ENGINE (BALANCED)
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    BALANCED AGGRESSIVE BOT
    
    PRIORITY CHAIN (SURVIVAL FIRST!):
    1. DEATHZONE ESCAPE
    2. HEALING (if HP low)
    3. EP RECOVERY (if EP low)
    4. PICKUP (healing > sMOLTZ > weapons)
    5. EQUIP BEST WEAPON
    6. FIGHT (if can win)
    7. FARM (monsters/guardians)
    8. MOVE
    9. REST
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
    my_id = self_data.get("id", "")
    
    track_smoltz(view)
    
    weapon_bonus = get_weapon_bonus(equipped)
    total_atk = atk + weapon_bonus
    my_damage = calc_damage(total_atk, 0, 5, "clear")
    
    # View fields
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
    alive_count = view.get("aliveCount", 100)
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
    
    track_agents(visible_agents, my_id, region_id)
    
    hp_status = check_hp_status(hp)
    ep_status = check_ep_status(ep, max_ep)
    
    # Determine mode (aggressive vs survival)
    is_aggressive_mode = alive_count > AGGRESSIVE_MODE_THRESHOLD
    
    # Log status periodically
    if _turn_counter % 10 == 0:
        log.info("📊 STATUS: HP=%d EP=%d/%d (%.0f%%) ATK=%d Mode=%s", 
                hp, ep, max_ep, ep_ratio*100, total_atk,
                "AGGRESSIVE" if is_aggressive_mode else "SURVIVAL")
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 1: DEATHZONE ESCAPE (OVERRIDE EVERYTHING!)
    # ═══════════════════════════════════════════════════════════════════
    if region.get("isDeathZone", False) or region_id in danger_ids:
        safe = find_safe_region(connections, danger_ids, view)
        if safe and ep >= move_ep_cost:
            log.warning("💀 DEATHZONE! Escaping to %s", safe[:8])
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 2: HEALING (JANGAN BIARKAN HP RENDAH!)
    # ═══════════════════════════════════════════════════════════════════
    if hp_status["need_heal"]:
        heal = find_best_healing_item(inventory, critical=(hp_status["status"] == "critical"))
        if heal:
            log.info("💚 HEALING: HP=%d -> using %s", hp, heal.get("typeId", "heal"))
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"HEAL: HP={hp}"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 3: EP RECOVERY (JANGAN SAMPAI EP HABIS!)
    # ═══════════════════════════════════════════════════════════════════
    if ep_status["need_rest"]:
        energy = find_energy_drink(inventory)
        if energy:
            log.info("⚡ EP RECOVERY: %d/%d -> energy drink", ep, max_ep)
            return {"action": "use_item", "data": {"itemId": energy["id"]},
                    "reason": f"EP: {ep}/{max_ep}"}
        
        # Only rest if safe
        enemies_nearby = [a for a in visible_agents 
                         if a.get("regionId") == region_id and a.get("isAlive")]
        if not enemies_nearby and region_id not in danger_ids:
            log.info("😴 REST: EP=%d/%d", ep, max_ep)
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 4: PICKUP (healing > sMOLTZ > weapons)
    # ═══════════════════════════════════════════════════════════════════
    pickup = pickup_priority(visible_items, inventory, region_id, equipped, hp)
    if pickup:
        return pickup
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 5: EQUIP BEST WEAPON
    # ═══════════════════════════════════════════════════════════════════
    equip = equip_best_weapon(inventory, equipped)
    if equip:
        return equip
    
    if not can_act:
        return None
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 6: FIGHT ENEMIES (BUT ONLY IF CAN WIN!)
    # ═══════════════════════════════════════════════════════════════════
    
    # Check if we can fight
    can_fight = (hp >= HEALTH_CONFIG["HP_MIN_TO_FIGHT"] and 
                 ep_ratio >= HEALTH_CONFIG["EP_MIN_TO_FIGHT"])
    
    # Enemies (players)
    enemies = [a for a in visible_agents
               if a.get("isAlive", True)
               and a.get("id") != my_id
               and not a.get("isGuardian", False)]
    
    if enemies and can_fight:
        target = select_best_target(enemies, hp, my_damage, region_weather)
        if target:
            weapon_range = get_weapon_range(equipped)
            if in_range(target, region_id, weapon_range, connections):
                enemy_hp = target.get("hp", 100)
                enemy_def = target.get("def", 5)
                enemy_damage = calc_damage(target.get("atk", 10), 
                                           estimate_enemy_weapon_bonus(target),
                                           defense, region_weather)
                
                should_fight, reason = is_fight_worth_it(hp, my_damage, enemy_hp, enemy_damage)
                
                # In aggressive mode, more willing to fight
                if should_fight or (is_aggressive_mode and enemy_hp < 60):
                    log.info("⚔️ ATTACK! %s | My HP=%d Enemy HP=%d Dmg=%d", 
                            reason, hp, enemy_hp, my_damage)
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"FIGHT: {reason}"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 7: GUARDIAN FARMING (priority in aggressive mode)
    # ═══════════════════════════════════════════════════════════════════
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    
    if guardians and can_fight and hp >= 40:
        target = select_best_target(guardians, hp, my_damage, region_weather)
        if target:
            weapon_range = get_weapon_range(equipped)
            if in_range(target, region_id, weapon_range, connections):
                log.info("💰 GUARDIAN HUNT! +120 sMOLTZ | HP=%d", target.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": "💰 GUARDIAN: 120 sMOLTZ!"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 8: MONSTER FARMING (safe and easy)
    # ═══════════════════════════════════════════════════════════════════
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep >= 1 and hp > 30:
        target = min(monsters, key=lambda m: m.get("hp", 999))
        weapon_range = get_weapon_range(equipped)
        if in_range(target, region_id, weapon_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER: HP={target.get('hp', '?')}"}
    
    # ═══════════════════════════════════════════════════════════════════
    # PRIORITY 9: MOVEMENT (strategic positioning)
    # ═══════════════════════════════════════════════════════════════════
    if ep >= move_ep_cost and connections:
        move_target = choose_move_target(connections, danger_ids, visible_items, 
                                          enemies, region_id)
        if move_target:
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "MOVE: Strategic positioning"}
    
    # ═══════════════════════════════════════════════════════════════════
    # LAST RESORT: REST
    # ═══════════════════════════════════════════════════════════════════
    if ep < 3 and region_id not in danger_ids:
        enemies_nearby = [a for a in visible_agents 
                         if a.get("regionId") == region_id and a.get("isAlive")]
        if not enemies_nearby:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}"}
    
    return None


"""
================================================================================
BALANCED AGGRESSIVE BOT v1.0
================================================================================

PERBAIKAN DARI SEBELUMNYA:
--------------------------
1. ❌ SEBELUMNYA: HP 0 di turn dini
   ✅ SEKARANG: Prioritaskan healing, jaga HP > 50%

2. ❌ SEBELUMNYA: EP cepat habis
   ✅ SEKARANG: Jaga EP > 40%, rest/energy drink jika perlu

3. ❌ SEBELUMNYA: Attack sembarangan
   ✅ SEKARANG: Hitung damage dulu, hanya fight jika bisa menang

4. ❌ SEBELUMNYA: Tidak ada healing item
   ✅ SEKARANG: Prioritaskan pickup healing item jika HP rendah

PRIORITAS BARU (SURVIVAL FIRST!):
---------------------------------
1. DEATHZONE ESCAPE
2. HEALING (jika HP < 55%)
3. EP RECOVERY (jika EP < 45%)
4. PICKUP (healing > sMOLTZ > weapons)
5. FIGHT (hanya jika bisa menang!)
6. FARM (monsters/guardians)
7. MOVE
8. REST

Gunakan script ini untuk bot yang:
- Tahan lama (tidak mati cepat)
- Tetap agresif (masih hunting kills)
- Pintar memilih target
- Jaga HP dan EP
================================================================================
"""
