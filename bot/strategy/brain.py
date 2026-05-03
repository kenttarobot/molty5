# ═══════════════════════════════════════════════════════════════════
#  REQUIRED FUNCTIONS FOR WEBSOCKET_ENGINE
# ═══════════════════════════════════════════════════════════════════

def learn_from_map(view: dict):
    """
    Mempelajari peta - diperlukan oleh websocket_engine
    """
    global _map_knowledge
    
    try:
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
        
        log.info(f"🗺️ learn_from_map: {len(_map_knowledge['death_zones'])} death zones, {len(_map_knowledge['safe_center'])} safe zones")
        
    except Exception as e:
        log.error(f"Error in learn_from_map: {e}")


def get_weapon_bonus_for_engine(weapon) -> int:
    """Utility function untuk engine"""
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "fist").lower()
    return WEAPONS.get(type_id, {}).get("bonus", 0)


def get_weapon_range_for_engine(weapon) -> int:
    """Utility function untuk engine"""
    if not weapon:
        return 0
    type_id = weapon.get("typeId", "fist").lower()
    return WEAPONS.get(type_id, {}).get("range", 0)


def calc_damage_for_engine(atk: int, weapon_bonus: int, target_def: int, weather: str = "clear") -> int:
    """Utility function untuk engine"""
    base = atk + weapon_bonus - int(target_def * 0.5)
    penalty = WEATHER_COMBAT_PENALTY.get(weather, 0.0)
    return max(1, int(base * (1 - penalty)))
