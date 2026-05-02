"""
GOD MODE AI v6 FINAL (COMPATIBLE)
================================
- No import error
- Adaptive learning
- Risk-aware combat
- Stable for production
"""

from collections import defaultdict

# =============================
# MEMORY SYSTEM
# =============================
_enemy_memory = defaultdict(lambda: {
    "wins": 0,
    "losses": 0,
    "encounters": 0,
    "avg_enemy_atk": 10
})


def reset_learning_state():
    global _enemy_memory
    _enemy_memory = defaultdict(lambda: {
        "wins": 0,
        "losses": 0,
        "encounters": 0,
        "avg_enemy_atk": 10
    })


# backward compatibility
def reset_game_state():
    reset_learning_state()


def learn_from_map(*args, **kwargs):
    return None


def on_combat_event(event_type, data):
    enemy = data.get("enemy", {})
    eid = enemy.get("id", "unknown")
    mem = _enemy_memory[eid]

    if event_type == "we_killed_enemy":
        mem["wins"] += 1
        mem["encounters"] += 1

    elif event_type == "we_were_killed":
        mem["losses"] += 1
        mem["encounters"] += 1

    if "enemy_atk" in data:
        mem["avg_enemy_atk"] = int((mem["avg_enemy_atk"] + data["enemy_atk"]) / 2)


# =============================
# STRATEGY
# =============================
def get_strategy(eid):
    mem = _enemy_memory[eid]

    if mem["encounters"] < 2:
        return "standard"

    winrate = mem["wins"] / max(1, mem["encounters"])

    if winrate < 0.3:
        return "defensive"
    elif winrate > 0.7:
        return "aggressive"

    return "balanced"


# =============================
# UTILITIES
# =============================
def safe_rest():
    return {"action": "rest", "data": {}, "reason": "SAFE"}


def find_enemies(view):
    self_data = view.get("self", {})
    my_id = self_data.get("id", "")
    region_id = view.get("currentRegion", {}).get("id", "")

    return [
        a for a in view.get("visibleAgents", [])
        if a.get("id") != my_id
        and a.get("regionId") == region_id
        and a.get("isAlive", True)
        and not a.get("isGuardian", False)
    ]


def find_best_target(enemies):
    return sorted(enemies, key=lambda e: (e.get("hp", 999), e.get("atk", 999)))[0]


# =============================
# MAIN AI
# =============================
def decide_action_v4(view, can_act=True, memory_temp=None):
    try:
        self_data = view.get("self", {})
        hp = self_data.get("hp", 100)

        enemies = find_enemies(view)

        if enemies:
            target = find_best_target(enemies)
            eid = target.get("id", "")
            enemy_hp = target.get("hp", 100)
            enemy_atk = target.get("atk", 10)

            strategy = get_strategy(eid)

            # ===== SAFETY =====
            if hp < enemy_atk * 1.5:
                return safe_rest()

            # ===== EXECUTE =====
            if enemy_hp < 20:
                return {
                    "action": "attack",
                    "data": {"targetId": eid, "targetType": "agent"},
                    "reason": "EXECUTE"
                }

            # ===== AGGRESSIVE =====
            if strategy == "aggressive" and hp > 60:
                return {
                    "action": "attack",
                    "data": {"targetId": eid, "targetType": "agent"},
                    "reason": "PRESSURE"
                }

            # ===== DEFENSIVE =====
            if strategy == "defensive":
                if hp < 75:
                    return safe_rest()

            # ===== BALANCED =====
            if hp > enemy_atk * 2:
                return {
                    "action": "attack",
                    "data": {"targetId": eid, "targetType": "agent"},
                    "reason": "SAFE_KILL"
                }

        # ===== NO ENEMY =====
        if hp < 85:
            return safe_rest()

        return {"action": "rest", "data": {}, "reason": "IDLE"}

    except Exception as e:
        return {"action": "rest", "data": {}, "reason": f"ERROR:{str(e)}"}


# =============================
# IMPORTANT: COMPATIBILITY FIX
# =============================
def decide_action(view, can_act=True, memory_temp=None):
    return decide_action_v4(view, can_act, memory_temp)


# =============================
# DEBUG
# =============================
def print_learning_summary():
    print("=== GOD MODE MEMORY ===")
    for k, v in _enemy_memory.items():
        print(k[:6], v)
