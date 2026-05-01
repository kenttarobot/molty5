"""
Strategy brain — ULTRA AGGRESSIVE SMART BOT
==================================================
Filosofi: "Attack like a beast, think like a predator"

KARAKTER:
- BARBAR: Attack first, ask questions later
- AGGRESIF: Kejar musuh ke ujung map
- PINTAR: Tahu kapan harus attack dan kapan harus kabur
- EFISIEN: Tidak buang EP untuk target tidak worth it

CORE PRINCIPLES:
1. Always hunt for kills (sMOLTZ = win)
2. Never waste EP on impossible fights
3. Target weakest enemies first (quick kills)
4. Run from dangerous fights (to fight another day)
5. Prioritize weapons and sMOLTZ above all
"""

from bot.utils.logger import get_logger
import os
import random

log = get_logger(__name__)


# =========================
# 🔥 AGGRESSIVE CONFIGURATION
# =========================

# ── KILLER INSTINCT CONFIG ───────────────────────────────────────────
KILLER_CONFIG = {
    # Health thresholds
    "MIN_HP_TO_FIGHT": 20,           # Fight sampai HP 20!
    "CRITICAL_HP": 25,                # Critical = 25 (bukan 30)
    "SAFE_HP": 50,                    # Heal only below 50
    
    # EP thresholds (agresif!)
    "EP_MIN_TO_FIGHT": 0.15,          # 15% EP aja berani fight!
    "EP_SAFE": 0.35,                  # EP 35% dianggap aman
    
    # Kill thresholds
    "KILL_STEAL_THRESHOLD": 35,       # Buru musuh HP < 35
    "EASY_KILL_THRESHOLD": 50,        # HP < 50 = easy kill
    "EXECUTE_THRESHOLD": 2,           # Execute if HP <= damage × 2
    
    # Aggression scaling
    "AGGRESSION_BASE": 0.8,           # 80% aggression (high)
    "LATE_GAME_BONUS": 1.2,           # +20% aggression late game
}

# ── Mode Switching (Simpler: just AGGRESSIVE and SURVIVAL) ───────────
RUSH_THRESHOLD = 30   # Mode AGGRESSIVE if alive > 30
# Mode SURVIVAL if alive <= 30

# ── Weapon priority (sama)
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
    "rewards": 1000,      # sMOLTZ absolute!
    "katana": 900,
    "sniper": 850,
    "sword": 800,
    "pistol": 750,
    "dagger": 700,
    "bow": 650,
    "medkit": 450,
    "bandage": 400,
    "emergency_food": 350,
    "energy_drink": 300,
    "binoculars": 150,
}

RECOVERY_ITEMS = {"medkit": 50, "bandage": 30, "emergency_food": 20}

WEATHER_COMBAT_PENALTY = {
    "clear": 0.0, "rain": 0.05, "fog": 0.10, "storm": 0.15,
}

# ── Reward values
GUARDIAN_SMOLTZ = 120
PLAYER_KILL_SMOLTZ = 100


# =========================
# 🔥 GLOBAL STATE
# =========================

_known_agents: dict = {}
_known_enemies: dict = {}
_map_knowledge: dict = {"revealed": False, "death_zones": set(), "safe_center": []}
_turn_counter: int = 0
_total_smoltz: int = 0
_kill_count: int = 0


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
    if not equipped:
        return False
    return get_weapon_bonus(equipped) > 0


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
    global _known_agents, _known_enemies, _map_knowledge, _turn_counter, _total_smoltz, _kill_count
    _known_agents = {}
    _known_enemies = {}
    _map_knowledge = {"revealed": False, "death_zones": set(), "safe_center": []}
    _turn_counter = 0
    _total_smoltz = 0
    _kill_count = 0
    log.info("=" * 60)
    log.info("🔥 ULTRA AGGRESSIVE SMART BOT v4.0.0")
    log.info("   Filosofi: Attack like a beast, think like a predator")
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
# 🔥 KILLER LOGIC (INTI AGGRESIF)
# =========================

def can_execute(enemy_hp: int, my_damage: int) -> bool:
    """Check if we can execute enemy in 1-2 hits."""
    return enemy_hp <= my_damage * KILLER_CONFIG["EXECUTE_THRESHOLD"]


def is_worth_fighting(enemy_hp: int, my_damage: int, enemy_damage: int, my_hp: int) -> tuple[bool, str]:
    """
    Smart fight decision: Kapan fight dan kapan kabur.
    Returns: (should_fight, reason)
    """
    hits_to_kill = (enemy_hp + my_damage - 1) // my_damage
    hits_to_die = (my_hp + enemy_damage - 1) // enemy_damage if enemy_damage > 0 else 999
    
    # CASE 1: Execute - pasti mati dalam 1-2 hits
    if can_execute(enemy_hp, my_damage):
        return True, "EXECUTE"
    
    # CASE 2: Easy kill - kita mati lebih lambat
    if hits_to_kill <= hits_to_die:
        return True, f"WIN: {hits_to_kill} vs {hits_to_die} hits"
    
    # CASE 3: Trade kill - kita mati tapi musuh juga mati
    if hits_to_kill == hits_to_die:
        return True, "TRADE"
    
    # CASE 4: Musuh terlalu kuat
    if hits_to_die <= 2 and enemy_hp > 50:
        return False, "TOO_STRONG"
    
    # CASE 5: If enemy low HP, tetap fight meskipun trade
    if enemy_hp < 30:
        return True, "LOW_HP_ENEMY"
    
    # Default: fight jika HP kita cukup
    return my_hp > 30, "DEFAULT"


def is_dangerous_target(enemy: dict, my_atk: int, my_hp: int) -> bool:
    """Identify enemies we should avoid."""
    enemy_atk = enemy.get("atk", 10)
    enemy_hp = enemy.get("hp", 100)
    enemy_weapon = get_weapon_bonus(enemy.get("equippedWeapon"))
    
    total_enemy_dmg = enemy_atk + enemy_weapon
    
    # Dangerous if:
    # 1. Very high ATK and high HP
    if total_enemy_dmg > 30 and enemy_hp > 60:
        return True
    # 2. Our HP is too low
    if my_hp < 30 and enemy_hp > 40:
        return True
    # 3. Enemy has Katana/Sniper and we have low HP
    if total_enemy_dmg > 25 and my_hp < 50:
        return True
    
    return False


def calculate_damage_estimate(my_atk: int, weapon_bonus: int, enemy_def: int, weather: str) -> int:
    """Calculate estimated damage per hit."""
    return max(1, my_atk + weapon_bonus - int(enemy_def * 0.5))


# =========================
# 🔥 TARGET SELECTION (AGGRESIF + PINTAR)
# =========================

def select_best_target(enemies: list, my_atk: int, my_hp: int, weapon_bonus: int, weather: str) -> dict | None:
    """
    Select best target with aggressive + smart logic.
    Priority: Execute > Kill Steal > Weakest > Trade Kill
    """
    if not enemies:
        return None
    
    candidates = []
    my_damage = calculate_damage_estimate(my_atk, weapon_bonus, 5, weather)
    
    for enemy in enemies:
        enemy_hp = enemy.get("hp", 0)
        if enemy_hp <= 0:
            continue
        
        enemy_def = enemy.get("def", 5)
        enemy_damage = calculate_damage_estimate(enemy.get("atk", 10), 
                                                  get_weapon_bonus(enemy.get("equippedWeapon")),
                                                  my_atk, weather)
        
        # Calculate score
        score = 0
        
        # EXECUTE: highest priority
        if can_execute(enemy_hp, my_damage):
            score = 10000 + (100 - enemy_hp)
        
        # KILL STEAL: very high priority
        elif enemy_hp < KILLER_CONFIG["KILL_STEAL_THRESHOLD"]:
            score = 5000 + (100 - enemy_hp)
        
        # EASY KILL: high priority
        elif enemy_hp < KILLER_CONFIG["EASY_KILL_THRESHOLD"]:
            score = 2000 + (100 - enemy_hp)
        
        # LOW HP enemy (below 60)
        elif enemy_hp < 60:
            score = 1000 + (60 - enemy_hp)
        
        # NORMAL: lower HP = higher score
        else:
            score = 100 - enemy_hp
        
        # Guardian bonus (+120 sMOLTZ)
        if enemy.get("isGuardian", False):
            score += 500
        
        # Penalty for dangerous enemies
        if is_dangerous_target(enemy, my_atk, my_hp):
            score -= 300
        
        candidates.append((score, enemy))
    
    if not candidates:
        return None
    
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]
    
    log.debug("🎯 Target selected: HP=%d, Score=%d", 
              best.get("hp", 0), candidates[0][0])
    
    return best


# =========================
# 🔥 PICKUP & WEAPON (AGGRESIF - weapon first!)
# =========================

def is_early_game() -> bool:
    global _turn_counter
    return _turn_counter < 30  # First 30 turns = early


def pickup_priority(items: list, inventory: list, region_id: str, equipped) -> dict | None:
    """
    PICKUP PRIORITY:
    1. sMOLTZ (absolute priority)
    2. WEAPON (upgrade or first weapon)
    3. Healing (keep for survival)
    """
    local_items = [i for i in items
                   if isinstance(i, dict) and i.get("regionId") == region_id]
    if not local_items:
        local_items = [i for i in items if isinstance(i, dict) and i.get("id")]
    if not local_items:
        return None
    
    # 1. sMOLTZ - ALWAYS!
    currency_items = [i for i in local_items 
                      if "rewards" in i.get("typeId", "").lower() 
                      or "moltz" in i.get("name", "").lower()]
    
    if currency_items:
        best = max(currency_items, key=lambda i: i.get("amount", 1))
        amount = best.get("amount", 50)
        log.info("💰 sMOLTZ PICKUP! +%d", amount)
        return {"action": "pickup", "data": {"itemId": best["id"]},
                "reason": f"💰 sMOLTZ: +{amount}!"}
    
    # 2. WEAPON - upgrade or first weapon
    weapon_items = [i for i in local_items if i.get("category") == "weapon"]
    if weapon_items:
        weapon_items.sort(key=lambda i: get_weapon_priority(i), reverse=True)
        best = weapon_items[0]
        current_priority = get_weapon_priority(equipped) if equipped else 0
        weapon_priority = get_weapon_priority(best)
        
        if weapon_priority > current_priority or not has_weapon_equipped(equipped):
            log.info("⚔️ WEAPON PICKUP: %s", best.get("typeId", "unknown"))
            return {"action": "pickup", "data": {"itemId": best["id"]},
                    "reason": f"⚔️ WEAPON: {best.get('typeId', 'unknown')}"}
    
    return None


def equip_best_weapon(inventory: list, equipped) -> dict | None:
    """Always equip best available weapon."""
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
        log.info("⚔️ EQUIP: %s (+%d ATK)", best.get("typeId", "weapon"), 
                 get_weapon_bonus(best))
        return {"action": "equip", "data": {"itemId": best["id"]},
                "reason": f"⚔️ EQUIP: {best.get('typeId', 'weapon')}"}
    return None


def find_healing_item(inventory: list, critical: bool = False) -> dict | None:
    """Find best healing item (agresif: heal only when critical)."""
    heals = []
    for i in inventory:
        if not isinstance(i, dict):
            continue
        type_id = i.get("typeId", "").lower()
        if type_id in RECOVERY_ITEMS:
            heals.append((RECOVERY_ITEMS[type_id], i))
    
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
    for i in inventory:
        if isinstance(i, dict) and i.get("typeId", "").lower() == "energy_drink":
            return i
    return None


# =========================
# 🔥 MOVEMENT (AGGRESIF - chase enemies!)
# =========================

def find_safe_region(connections, danger_ids: set, view: dict = None) -> str | None:
    """Find region not in death zone."""
    for conn in connections:
        rid = _get_region_id(conn)
        if not rid or rid in danger_ids:
            continue
        return rid
    return None


def find_enemy_region(enemies: list, current_region: str) -> str | None:
    """Find region with enemies to chase."""
    if not enemies:
        return None
    
    # Prioritize enemies with lowest HP
    low_hp_enemies = [e for e in enemies if e.get("hp", 100) < 50]
    if low_hp_enemies:
        target = min(low_hp_enemies, key=lambda e: e.get("hp", 999))
        target_region = target.get("regionId", "")
        if target_region and target_region != current_region:
            return target_region
    
    # Any enemy in adjacent region
    for enemy in enemies:
        target_region = enemy.get("regionId", "")
        if target_region and target_region != current_region:
            return target_region
    
    return None


def choose_move(connections: list, danger_ids: set, region: dict, 
                visible_items: list, enemies: list, current_region_id: str) -> str | None:
    """
    Choose best region to move to.
    Priority: Enemies > Items > Safe region
    """
    # 1. Chase enemies!
    enemy_region = find_enemy_region(enemies, current_region_id)
    if enemy_region and enemy_region not in danger_ids:
        log.info("🎯 CHASING: Moving to enemy region %s", enemy_region[:8])
        return enemy_region
    
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
    safe = find_safe_region(connections, danger_ids)
    if safe:
        return safe
    
    return None


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
    if not weapon:
        return 0
    return get_weapon_bonus(weapon)


def track_enemies(visible_agents: list, my_id: str, my_region: str):
    """Track enemy positions for hunting."""
    global _known_enemies
    for agent in visible_agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if not aid or aid == my_id or agent.get("isGuardian", False):
            continue
        
        _known_enemies[aid] = {
            "hp": agent.get("hp", 100),
            "region": agent.get("regionId", my_region),
            "last_seen": _turn_counter,
            "isAlive": True,
        }
    
    # Cleanup dead
    dead = [k for k, v in _known_enemies.items() if not v.get("isAlive", True)]
    for d in dead:
        del _known_enemies[d]


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
            log.info("💰 GUARDIAN KILL! +%d sMOLTZ (Total: %d)", GUARDIAN_SMOLTZ, _total_smoltz)
        elif "killed" in msg and "player" in msg:
            _total_smoltz += PLAYER_KILL_SMOLTZ
            _kill_count += 1
            log.info("💰 PLAYER KILL! +%d sMOLTZ (Total: %d, Kills: %d)", 
                    PLAYER_KILL_SMOLTZ, _total_smoltz, _kill_count)


# =========================
# 🧠 MAIN DECISION ENGINE (ULTRA AGGRESIF)
# =========================

def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """
    ULTRA AGGRESSIVE SMART BOT
    
    CORE PRINCIPLES:
    1. Always prioritize kills over farming
    2. Attack first, analyze later
    3. Chase low HP enemies
    4. Run only from certain death
    5. Heal only when critical
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
    
    track_smoltz(view)
    
    weapon_bonus = get_weapon_bonus(equipped)
    total_atk = atk + weapon_bonus
    
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
    
    # Determine aggression level
    is_aggressive = alive_count > RUSH_THRESHOLD
    aggression_multiplier = KILLER_CONFIG["AGGRESSION_BASE"]
    if not is_aggressive:
        aggression_multiplier *= KILLER_CONFIG["LATE_GAME_BONUS"]
    
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
    
    track_enemies(visible_agents, self_data.get("id", ""), region_id)
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 1: DEATHZONE ESCAPE (only deathzone overrides!)
    # ─────────────────────────────────────────────────────────────────
    if region.get("isDeathZone", False):
        safe = find_safe_region(connections, danger_ids, view)
        if safe:
            log.warning("💀 DEATHZONE! Escaping!")
            return {"action": "move", "data": {"regionId": safe},
                    "reason": "DEATHZONE ESCAPE"}
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 2: PICKUP (sMOLTZ and weapons only)
    # ─────────────────────────────────────────────────────────────────
    pickup = pickup_priority(visible_items, inventory, region_id, equipped)
    if pickup:
        return pickup
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 3: EQUIP BEST WEAPON
    # ─────────────────────────────────────────────────────────────────
    equip = equip_best_weapon(inventory, equipped)
    if equip:
        return equip
    
    if not can_act:
        return None
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 4: HEAL (only if CRITICAL - aggressive!)
    # ─────────────────────────────────────────────────────────────────
    # Aggressive: heal only below 25 HP!
    heal_threshold = KILLER_CONFIG["CRITICAL_HP"]
    if hp < heal_threshold:
        heal = find_healing_item(inventory, critical=True)
        if heal:
            log.info("💚 HEAL (CRITICAL): HP=%d", hp)
            return {"action": "use_item", "data": {"itemId": heal["id"]},
                    "reason": f"CRITICAL HEAL: HP={hp}"}
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 5: KILL! (main priority - aggressive)
    # ─────────────────────────────────────────────────────────────────
    
    # All enemies (players)
    enemies = [a for a in visible_agents
               if a.get("isAlive", True)
               and a.get("id") != self_data.get("id")
               and not a.get("isGuardian", False)]
    
    # Guardians (worth 120 sMOLTZ)
    guardians = [a for a in visible_agents
                 if a.get("isGuardian", False) and a.get("isAlive", True)]
    
    # Check EP enough to fight
    ep_min = KILLER_CONFIG["EP_MIN_TO_FIGHT"]
    hp_min = KILLER_CONFIG["MIN_HP_TO_FIGHT"]
    
    can_engage = ep_ratio >= ep_min and hp >= hp_min
    
    # FIGHT ENEMIES - with smart target selection!
    if enemies and can_engage:
        target = select_best_target(enemies, total_atk, hp, weapon_bonus, region_weather)
        if target:
            weapon_range = get_weapon_range(equipped)
            if in_range(target, region_id, weapon_range, connections):
                enemy_hp = target.get("hp", 100)
                my_damage = calculate_damage_estimate(total_atk, 0, target.get("def", 5), region_weather)
                enemy_damage = estimate_enemy_weapon_bonus(target) + target.get("atk", 10)
                
                should_fight, reason = is_worth_fighting(enemy_hp, my_damage, enemy_damage, hp)
                
                if should_fight:
                    log.info("⚔️ ATTACK! %s | My HP=%d Enemy HP=%d Dmg=%d", 
                            reason, hp, enemy_hp, my_damage)
                    return {"action": "attack",
                            "data": {"targetId": target["id"], "targetType": "agent"},
                            "reason": f"{reason}: HP={enemy_hp}"}
    
    # FIGHT GUARDIANS (high priority - 120 sMOLTZ!)
    if guardians and can_engage and hp >= 30:
        target = select_best_target(guardians, total_atk, hp, weapon_bonus, region_weather)
        if target:
            weapon_range = get_weapon_range(equipped)
            if in_range(target, region_id, weapon_range, connections):
                log.info("💰 GUARDIAN HUNT! +120 sMOLTZ | HP=%d", target.get("hp", 0))
                return {"action": "attack",
                        "data": {"targetId": target["id"], "targetType": "agent"},
                        "reason": f"💰 GUARDIAN: 120 sMOLTZ!"}
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 6: MONSTER FARMING (easy kills)
    # ─────────────────────────────────────────────────────────────────
    monsters = [m for m in visible_monsters if m.get("hp", 0) > 0]
    if monsters and ep >= 1 and hp > 20:
        target = min(monsters, key=lambda m: m.get("hp", 999))
        weapon_range = get_weapon_range(equipped)
        if in_range(target, region_id, weapon_range, connections):
            return {"action": "attack",
                    "data": {"targetId": target["id"], "targetType": "monster"},
                    "reason": f"MONSTER: HP={target.get('hp', '?')}"}
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 7: EP RECOVERY (if really low)
    # ─────────────────────────────────────────────────────────────────
    if ep_ratio < 0.20:
        energy = find_energy_drink(inventory)
        if energy:
            return {"action": "use_item", "data": {"itemId": energy["id"]},
                    "reason": f"EP: {ep}/{max_ep} -> restore"}
        
        # Only rest if no enemies nearby
        enemies_nearby = [e for e in visible_agents 
                         if e.get("regionId") == region_id and e.get("isAlive")]
        if not enemies_nearby and region_id not in danger_ids:
            return {"action": "rest", "data": {},
                    "reason": f"REST: EP={ep}/{max_ep}"}
    
    # ─────────────────────────────────────────────────────────────────
    # PRIORITY 8: CHASE & MOVE (aggressive hunting)
    # ─────────────────────────────────────────────────────────────────
    if ep >= move_ep_cost and connections:
        # Chase enemies!
        move_target = choose_move(connections, danger_ids, region, visible_items, 
                                   enemies, region_id)
        if move_target:
            log.info("🏃 CHASING: Moving to hunt enemies")
            return {"action": "move", "data": {"regionId": move_target},
                    "reason": "AGGRESSIVE: Hunting enemies"}
    
    # ─────────────────────────────────────────────────────────────────
    # LAST RESORT: REST
    # ─────────────────────────────────────────────────────────────────
    if ep < 3 and region_id not in danger_ids:
        return {"action": "rest", "data": {},
                "reason": f"REST: EP={ep}"}
    
    return None


"""
================================================================================
ULTRA AGGRESSIVE SMART BOT v4.0.0
================================================================================

FILOSOFI:
---------
"Attack like a beast, think like a predator"

KARAKTER:
---------
1. BARBAR: Attack first! Hanya lari dari deathzone
2. AGGRESIF: Kejar musuh ke ujung map, prioritaskan kill
3. PINTAR: Hitung damage, cari target worth it, hindari musuh terlalu kuat
4. EFISIEN: Tidak buang EP untuk target tidak worth it

KEY BEHAVIORS:
-------------
✅ Attack jika bisa execute dalam 1-2 hits (no hesitation!)
✅ Chase low HP enemies (active hunting)
✅ Only heal when HP < 25 (very aggressive!)
✅ Prioritize sMOLTZ and weapons above all
✅ Fight guardians (120 sMOLTZ) aggressively
✅ Calculate damage before fighting (smart!)
✅ Flee only from certain death or too strong enemies

PRIORITY CHAIN:
--------------
1. DEATHZONE ESCAPE (only thing that overrides fighting)
2. PICKUP sMOLTZ & WEAPONS
3. EQUIP BEST WEAPON
4. HEAL (only if critical - HP < 25)
5. KILL ENEMIES (with smart target selection)
6. GUARDIAN HUNT (120 sMOLTZ)
7. MONSTER FARM
8. REST (if EP very low)
9. CHASE & MOVE

This bot is ULTRA AGGRESSIVE but SMART!
================================================================================
"""
