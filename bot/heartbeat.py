"""
Heartbeat loop — main orchestration.
VERSI AGRESIF v3.3 - Fast Rejoin after Death
- Langsung force keluar dari IN_GAME jika agent mati
- Refresh state lebih sering
"""

import asyncio
import time
from bot.api_client import MoltyAPI, APIError
from bot.dashboard.state import dashboard_state
from bot.state_router import determine_state, IN_GAME, READY_FREE, READY_PAID, NO_IDENTITY
from bot.setup.account_setup import ensure_account_ready
from bot.setup.wallet_setup import ensure_molty_wallet
from bot.setup.whitelist import ensure_whitelist
from bot.setup.identity import ensure_identity
from bot.game.room_selector import select_room
from bot.game.free_join import join_free_game
from bot.game.paid_join import join_paid_game
from bot.game.websocket_engine import WebSocketEngine
from bot.game.settlement import settle_game
from bot.memory.agent_memory import AgentMemory
from bot.credentials import load_credentials, get_api_key
from bot.config import (
    ADVANCED_MODE, ROOM_MODE, AUTO_WHITELIST,
    AUTO_SC_WALLET, ENABLE_MEMORY, AUTO_IDENTITY,
)
from bot.utils.logger import get_logger

log = get_logger(__name__)


class Heartbeat:
    def __init__(self):
        self.api: MoltyAPI | None = None
        self.memory = AgentMemory()
        self.running = True
        self._agent_key = "agent-1"
        self._agent_name = "Agent"
        self._dead_skip_count = 0

    async def run(self):
        log.info("═══════════════════════════════════════════")
        log.info("  MOLTY ROYALE AI AGENT — STARTING (Fast Rejoin v3.3)")
        log.info("═══════════════════════════════════════════")

        log.info("Config:")
        log.info("  ADVANCED_MODE   = %s", ADVANCED_MODE)
        log.info("  AUTO_SC_WALLET  = %s", AUTO_SC_WALLET)
        log.info("  AUTO_WHITELIST  = %s", AUTO_WHITELIST)
        log.info("  ENABLE_MEMORY   = %s", ENABLE_MEMORY)
        log.info("  AUTO_IDENTITY   = %s", AUTO_IDENTITY)
        log.info("  ROOM_MODE       = %s", ROOM_MODE)

        creds = None
        while self.running and not creds:
            try:
                creds = await ensure_account_ready()
                api_key = creds.get("api_key", "") or get_api_key()
                if not api_key:
                    await asyncio.sleep(60)
                    continue
            except Exception as e:
                log.error("Setup error: %s", e)
                await asyncio.sleep(60)

        self.api = MoltyAPI(creds.get("api_key", "") or get_api_key())

        dashboard_state.bots_running = 1
        dashboard_state.add_log("Bot started - Aggressive Fast Rejoin", "info")

        if ENABLE_MEMORY:
            await self.memory.load()

        log.info("Molty Royale AI Agent v2.1.3 - Aggressive Dead Rejoin Mode")

        while self.running:
            try:
                await self._heartbeat_cycle()
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                log.error("Heartbeat error: %s", e)
                await asyncio.sleep(10)

        if self.api:
            await self.api.close()

    async def _heartbeat_cycle(self):
        try:
            me = await self.api.get_accounts_me()
        except APIError as e:
            if e.status == 401:
                log.error("Invalid API key")
                self.running = False
                return
            raise

        state, ctx = determine_state(me)
        game_id = ctx.get("game_id") if ctx else None
        is_alive = ctx.get("is_alive", True) if ctx else True

        log.info(f"State: {state} | Game: {game_id[:12] if game_id else 'None'} | Alive: {is_alive}")

        self._agent_key = str(me.get("agentId", me.get("id", "agent-1")))
        self._agent_name = me.get("agentName", me.get("name", "Agent"))

        dashboard_state.update_agent(self._agent_key, {
            "name": self._agent_name,
            "status": "playing" if state == IN_GAME else "idle",
        })

        if state == NO_IDENTITY:
            await self._handle_no_identity(me)
            return

        if state == IN_GAME:
            if not is_alive:
                await self._handle_dead_skip()
                return
            else:
                # Masih hidup → main
                await self._play_game(game_id, ctx.get("agent_id"), ctx.get("entry_type", "free"))
                return

        # State READY
        if state in (READY_FREE, READY_PAID):
            await self._handle_ready(me, state)
            return

        await asyncio.sleep(4)

    async def _handle_dead_skip(self):
        """Agresif skip ketika agent mati"""
        self._dead_skip_count += 1
        if self._dead_skip_count % 3 == 1:   # kurangi spam log
            log.warning("Agent MATI → Aggressive skip mode. Akan coba join room baru secepatnya.")
            dashboard_state.add_log("Dead → Fast rejoin mode", "warning", self._agent_key)

        # Refresh state lebih sering
        await asyncio.sleep(5)

        # Force refresh
        try:
            me = await self.api.get_accounts_me()
            state, ctx = determine_state(me)
            log.info(f"Force refresh state: {state}")
        except:
            pass

    async def _handle_ready(self, me: dict, state: str):
        room_type = select_room(me)
        log.info(f"→ JOINING {room_type.upper()} ROOM...")

        try:
            if room_type == "paid":
                game_id, agent_id = await join_paid_game(self.api)
            else:
                game_id, agent_id = await join_free_game(self.api)

            log.info(f"✅ Berhasil join {room_type} game: {game_id}")
            await self._play_game(game_id, agent_id, room_type)
        except Exception as e:
            log.warning(f"Join gagal: {e}")
            await asyncio.sleep(8)

    async def _play_game(self, game_id: str, agent_id: str, entry_type: str):
        log.info(f"═══ PLAYING {game_id[:12]} ({entry_type}) ═══")

        dashboard_state.update_agent(self._agent_key, {
            "status": "playing",
            "room_id": game_id,
        })

        self.memory.set_temp_game(game_id)
        await self.memory.save()

        engine = WebSocketEngine(game_id, agent_id)
        engine.dashboard_key = self._agent_key
        engine.dashboard_name = self._agent_name

        game_result = await engine.run()

        if game_result and game_result.get("status") == "dead":
            await settle_game(game_result, entry_type, self.memory, early_exit=True)
        else:
            await settle_game(game_result, entry_type, self.memory)

        await asyncio.sleep(3)


if __name__ == "__main__":
    heartbeat = Heartbeat()
    asyncio.run(heartbeat.run())
