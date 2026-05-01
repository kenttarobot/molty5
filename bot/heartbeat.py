"""
Heartbeat loop v3.5 - Ultra Aggressive Rejoin
- Tidak bergantung pada leave_game()
- Multiple force refresh state ketika agent mati
- Langsung push ke READY state secepat mungkin
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
        self._last_refresh = 0

    async def run(self):
        log.info("═══════════════════════════════════════════")
        log.info("  MOLTY ROYALE AI AGENT — STARTING (Ultra Aggressive v3.5)")
        log.info("═══════════════════════════════════════════")

        log.info("Config:")
        log.info("  ROOM_MODE       = %s", ROOM_MODE)
        log.info("  ENABLE_MEMORY   = %s", ENABLE_MEMORY)

        creds = None
        while self.running and not creds:
            try:
                creds = await ensure_account_ready()
            except Exception as e:
                log.error("Setup error: %s", e)
                await asyncio.sleep(60)

        self.api = MoltyAPI(creds.get("api_key", "") or get_api_key())

        dashboard_state.bots_running = 1
        dashboard_state.add_log("Bot started - Ultra Aggressive Rejoin v3.5", "info")

        if ENABLE_MEMORY:
            await self.memory.load()

        log.info("Molty Royale AI Agent v2.1.5 - Ultra Aggressive Dead Rejoin Mode")

        while self.running:
            try:
                await self._heartbeat_cycle()
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                log.error("Heartbeat error: %s", e)
                await asyncio.sleep(10)

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

        if state == IN_GAME and not is_alive:
            await self._ultra_dead_skip(ctx)
            return

        if state == IN_GAME:
            await self._play_game(game_id, ctx.get("agent_id"), ctx.get("entry_type", "free"))
            return

        if state in (READY_FREE, READY_PAID):
            await self._handle_ready(me, state)
            return

        if state == NO_IDENTITY:
            await self._handle_no_identity()
            return

        await asyncio.sleep(5)

    async def _ultra_dead_skip(self, ctx: dict):
        """Ultra aggressive skip untuk agent yang sudah mati"""
        game_id = ctx.get("game_id", "unknown")
        self._dead_skip_count += 1

        if self._dead_skip_count % 4 == 1:
            log.warning(f"Agent MATI di game {game_id[:12]}. ULTRA SKIP MODE aktif...")
            dashboard_state.add_log(f"Ultra skip dead game {game_id[:12]}", "warning", self._agent_key)

        # Multiple refresh dengan jeda pendek
        for i in range(3):
            await asyncio.sleep(5)
            try:
                me = await self.api.get_accounts_me()
                state, new_ctx = determine_state(me)
                log.info(f"Refresh #{i+1} after dead → State: {state}")
                if state in (READY_FREE, READY_PAID):
                    log.info("✅ Berhasil keluar dari IN_GAME! State sekarang READY.")
                    return
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

            log.info(f"✅ BERHASIL JOIN {room_type} GAME: {game_id}")
            await self._play_game(game_id, agent_id, room_type)
        except Exception as e:
            log.warning(f"Join gagal: {e}")
            await asyncio.sleep(10)

    async def _play_game(self, game_id: str, agent_id: str, entry_type: str):
        log.info(f"═══ PLAYING GAME {game_id[:12]} ({entry_type}) ═══")

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

    async def _handle_no_identity(self):
        log.info("Running setup pipeline...")
        creds = load_credentials() or {}
        owner_eoa = creds.get("owner_eoa", "")
        if owner_eoa:
            if AUTO_SC_WALLET:
                await ensure_molty_wallet(self.api, owner_eoa)
            if AUTO_WHITELIST:
                await ensure_whitelist(self.api, owner_eoa, creds.get("agent_wallet_address", ""))
            if AUTO_IDENTITY:
                await ensure_identity(self.api)
        log.info("Setup completed.")


if __name__ == "__main__":
    heartbeat = Heartbeat()
    asyncio.run(heartbeat.run())
