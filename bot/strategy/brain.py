"""
Strategy brain — SIMPLE AGGRESSIVE (TEST VERSION)
- Minimal logika, maksimal aksi
- Pastikan bot bisa attack/flee/heal
"""

import time
from bot.utils.logger import get_logger

log = get_logger(__name__)


def decide_action(view: dict, can_act: bool, memory_temp: dict = None) -> dict | None:
    """Simple aggressive bot - guaranteed to work"""
    
    # DEBUG LOG
    log.warning("=" * 40)
    log.warning("SIMPLE BOT DECIDE_ACTION")
    
    if not can_act:
        log.warning("CANNOT ACT - returning None")
        return None
    
    self_data = view.get("self", {})
    hp = self_data.get("hp", 100)
    ep = self_data.get("ep", 10)
    inventory = self_data.get("inventory", [])
    equipped = self_data.get("equippedWeapon")
    my_id = self_data.get("id", "")
    
    region = view.get("currentRegion", {})
    region_id = region.get("id", "")
    
    visible_agents = view.get("visibleAgents", [])
    visible_items = view.get("visibleItems", [])
    connections = view.get("connectedRegions", []) or region.get("connections", [])
    
    log.warning(f"HP: {hp}, EP: {ep}, can_act: {can_act}")
    log.warning(f"Visible agents: {len(visible_agents)}")
    
    # Find enemies in same region
    enemies_here = []
    for agent in visible_agents:
        if agent.get("id") != my_id and agent.get("regionId") == region_id:
            if not agent.get("isGuardian", False):
                enemies_here.append(agent)
    
    log.warning(f"Enemies in same region: {len(enemies_here)}")
    
    # ============ PRIORITY 1: FLEE IF HP CRITICAL ============
    if hp < 15:
        # Cari region aman
        for conn in connections:
            if isinstance(conn, dict):
                rid = conn.get("id", "")
                if rid and not conn.get("isDeathZone"):
                    log.warning(f"🏃 FLEE to {rid} (HP={hp})")
                    return {"action": "move", "data": {"regionId": rid}, "reason": "FLEE_HP_LOW"}
            elif isinstance(conn, str):
                if conn:
                    log.warning(f"🏃 FLEE to {conn} (HP={hp})")
                    return {"action": "move", "data": {"regionId": conn}, "reason": "FLEE_HP_LOW"}
    
    # ============ PRIORITY 2: HEAL IF NEEDED ============
    if hp < 40:
        # Cari healing item
        for item in inventory:
            if isinstance(item, dict):
                item_type = item.get("typeId", "").lower()
                if item_type in ["medkit", "bandage", "emergency_food"]:
                    log.warning(f"💊 HEAL with {item_type} (HP={hp})")
                    return {"action": "use_item", "data": {"itemId": item["id"]}, "reason": "HEAL"}
        
        # Cari medical facility
        interactables = region.get("interactables", [])
        for fac in interactables:
            if isinstance(fac, dict) and fac.get("type", "").lower() == "medical_facility":
                if not fac.get("isUsed"):
                    log.warning(f"🏥 USE MEDICAL FACILITY (HP={hp})")
                    return {"action": "interact", "data": {"interactableId": fac["id"]}, "reason": "MEDICAL"}
    
    # ============ PRIORITY 3: ATTACK ENEMIES ============
    if enemies_here and hp >= 20:
        # Pilih target dengan HP terendah
        target = min(enemies_here, key=lambda e: e.get("hp", 999))
        target_id = target.get("id")
        target_hp = target.get("hp", 100)
        
        log.warning(f"⚔️ ATTACK {target_id[:8]} (HP={target_hp})")
        return {"action": "attack", "data": {"targetId": target_id, "targetType": "agent"}, "reason": "ATTACK"}
    
    # ============ PRIORITY 4: EQUIP BETTER WEAPON ============
    current_bonus = 0
    if equipped:
        weapon_type = equipped.get("typeId", "fist").lower()
        current_bonus = {"katana": 35, "sniper": 28, "sword": 20, "pistol": 10, "dagger": 10, "bow": 5, "fist": 0}.get(weapon_type, 0)
    
    best_weapon = None
    best_bonus = current_bonus
    for item in inventory:
        if isinstance(item, dict) and item.get("category") == "weapon":
            weapon_type = item.get("typeId", "fist").lower()
            bonus = {"katana": 35, "sniper": 28, "sword": 20, "pistol": 10, "dagger": 10, "bow": 5, "fist": 0}.get(weapon_type, 0)
            if bonus > best_bonus:
                best_bonus = bonus
                best_weapon = item
    
    if best_weapon:
        log.warning(f"🔫 EQUIP {best_weapon.get('typeId')} (+{best_bonus} dmg)")
        return {"action": "equip", "data": {"itemId": best_weapon["id"]}, "reason": "EQUIP"}
    
    # ============ PRIORITY 5: PICKUP ITEMS ============
    if visible_items:
        for item in visible_items:
            if isinstance(item, dict):
                log.warning(f"📦 PICKUP {item.get('typeId', 'item')}")
                return {"action": "pickup", "data": {"itemId": item["id"]}, "reason": "PICKUP"}
    
    # ============ PRIORITY 6: MOVE TOWARD ENEMY ============
    # Cari musuh di region lain
    enemies_elsewhere = []
    for agent in visible_agents:
        if agent.get("id") != my_id and agent.get("regionId") != region_id:
            if not agent.get("isGuardian", False):
                enemies_elsewhere.append(agent)
    
    if enemies_elsewhere and hp > 30:
        target_region = enemies_elsewhere[0].get("regionId")
        if target_region:
            log.warning(f"🎯 MOVE TO ENEMY at {target_region}")
            return {"action": "move", "data": {"regionId": target_region}, "reason": "SEEK_ENEMY"}
    
    # ============ PRIORITY 7: MOVE TO SAFE REGION ============
    for conn in connections:
        if isinstance(conn, dict):
            rid = conn.get("id", "")
            is_dz = conn.get("isDeathZone", False)
            if rid and not is_dz and rid != region_id:
                log.warning(f"🚶 MOVE to {rid}")
                return {"action": "move", "data": {"regionId": rid}, "reason": "MOVE"}
        elif isinstance(conn, str):
            if conn and conn != region_id:
                log.warning(f"🚶 MOVE to {conn}")
                return {"action": "move", "data": {"regionId": conn}, "reason": "MOVE"}
    
    # ============ LAST RESORT: REST ============
    if ep < 8:
        log.warning(f"😴 REST (EP={ep})")
        return {"action": "rest", "data": {}, "reason": "REST"}
    
    log.warning("😴 IDLE REST")
    return {"action": "rest", "data": {}, "reason": "IDLE"}


def reset_game_state():
    log.info("Simple bot reset")


def on_attacked_by(attacker_id: str, current_turn: int, damage: int = None):
    log.warning(f"⚠️ Attacked by {attacker_id[:8]} for {damage} damage")


def on_enemy_killed(enemy_id: str):
    log.info(f"✅ Killed {enemy_id[:8]}")


def on_we_died(killer_id: str, combat_summary: dict = None):
    log.warning(f"💀 Died by {killer_id[:8]}")


def print_learning_summary():
    print("Simple bot - no learning")


def get_all_enemy_intel():
    return []
