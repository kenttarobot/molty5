"""
Heartbeat loop — main orchestration per heartbeat.md.
State machine: setup → join → play → settle → repeat.

VERSI FINAL (v3.1) - Dead Agent Fast Rejoin
- Lebih stabil keluar dari IN_GAME ketika mati
- Mengurangi spam log dead skip
- Optimasi timing untuk join room kosong
"""

import asyncio
from bot.api_client import MoltyAPI, APIError
from bot.dashboard.state import dashboard_state
from bot.state_router import determine_state, NO_ACCOUNT, NO_IDENTITY, IN_GAME, READY_PAID, READY_FREE
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
    """Main heartbeat loop — runs forever, manages the full agent lifecycle."""

    def __init__(self):
        self.api: MoltyAPI | None = None
        self.memory = AgentMemory()
        self.running = True
        self._agent_key = "agent-1"
        self._agent_name = "Agent"
        self._last_dead_skip_time = 0

    async def run(self):
        log.info("═══════════════════════════════════════════")
        log.info("  MOLTY ROYALE AI AGENT — STARTING")
        log.info("═══════════════════════════════════════════")

        log.info("Config (First-Run Intake answers):")
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
                    log.error("No API key available. Retrying in 60s...")
                    creds = None
                    await asyncio.sleep(60)
            except Exception as e:
                log.error("Account setup error: %s. Retrying in 60s...", e)
                await asyncio.sleep(60)

        if not self.running:
            return

        self.api = MoltyAPI(creds.get("api_key", "") or get_api_key())

        dashboard_state.bots_running = 1
        dashboard_state.add_log("Bot started - Fast Rejoin Mode", "info")

        if ENABLE_MEMORY:
            await self.memory.load()
            if creds.get("agent_name"):
                self.memory.set_agent_name(creds["agent_name"])

        log.info("Molty Royale AI Agent v2.1.1 - Dead Agent Fast Rejoin Mode")
        log.info("Press Ctrl+C to stop")

        consecutive_errors = 0
        while self.running:
            try:
                await self._heartbeat_cycle()
                consecutive_errors = 0
            except KeyboardInterrupt:
                log.info("Shutdown requested")
                self.running = False
            except Exception as e:
                consecutive_errors += 1
                wait = min(10 * (2 ** min(consecutive_errors - 1, 4)), 120)
                log.error("Heartbeat error (#%d): %s. Retrying in %ds...", consecutive_errors, e, wait)
                await asyncio.sleep(wait)

        if self.api:
            await self.api.close()
        log.info("Agent stopped.")

    async def _heartbeat_cycle(self):
        try:
            me = await self.api.get_accounts_me()
        except APIError as e:
            if e.status == 401:
                log.error("Invalid API key. Re-run setup.")
                self.running = False
                return
            raise

        state, ctx = determine_state(me)
        game_id = ctx.get("game_id") if ctx else None

        log.info(f"State: {state} | Game: {game_id[:12] if game_id else 'None'}")

        self._agent_key = str(me.get("agentId", me.get("id", "agent-1")))
        self._agent_name = me.get("agentName", me.get("name", "Agent"))
        balance = me.get("balance", 0)

        dashboard_state.total_smoltz = balance
        dashboard_state.update_agent(self._agent_key, {
            "name": self._agent_name,
            "status": "playing" if state == IN_GAME else "idle",
            "smoltz": balance,
        })

        if state == NO_IDENTITY:
            await self._handle_no_identity(me)
            return

        if state == IN_GAME:
            await self._handle_in_game(ctx)
            return

        if state in (READY_FREE, READY_PAID):
            await self._handle_ready(me, state)
            return

        await asyncio.sleep(5)

    async def _handle_no_identity(self, me: dict):
        # Setup logic (disingkat)
        log.info("Running setup pipeline...")
        creds = load_credentials() or {}
        owner_eoa = creds.get("owner_eoa", "")
        if owner_eoa and AUTO_SC_WALLET:
            await ensure_molty_wallet(self.api, owner_eoa)
        if owner_eoa and AUTO_WHITELIST:
            await ensure_whitelist(self.api, owner_eoa, creds.get("agent_wallet_address", ""))
        if AUTO_IDENTITY:
            await ensure_identity(self.api)
        log.info("✅ Setup completed.")

    async def _handle_ready(self, me: dict, state: str):
        room_type = select_room(me)
        log.info(f"→ Trying to join {room_type.upper()} room...")

        try:
            if room_type == "paid":
                game_id, agent_id = await join_paid_game(self.api)
            else:
                game_id, agent_id = await join_free_game(self.api)

            log.info(f"✅ Joined {room_type} game: {game_id}")
            await self._play_game(game_id, agent_id, room_type)
        except Exception as e:
            log.warning(f"Join {room_type} failed: {e}")
            await asyncio.sleep(8)

    async def _handle_in_game(self, ctx: dict):
        game_id = ctx.get("game_id")
        is_alive = ctx.get("is_alive", True)

        if not is_alive:
            current_time = asyncio.get_event_loop().time()
            if current_time - self._last_dead_skip_time > 15:   # batasi spam log
                log.warning(f"Agent MATI di game {game_id[:12]}. Skipping & waiting for new room...")
                dashboard_state.add_log(f"Dead → skipping game {game_id[:12]}", "warning", self._agent_key)
                self._last_dead_skip_time = current_time

            await asyncio.sleep(7)   # jeda optimal
            return

        # Masih hidup
        entry_type = ctx.get("entry_type", "free")
        await self._play_game(game_id, ctx.get("agent_id"), entry_type)

    async def _play_game(self, game_id: str, agent_id: str, entry_type: str):
        log.info(f"═══ PLAYING GAME: {game_id[:12]} (type={entry_type}) ═══")

        dashboard_state.update_agent(self._agent_key, {
            "status": "playing",
            "room_id": game_id,
            "room_name": f"{entry_type} room",
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

        await asyncio.sleep(4)


if __name__ == "__main__":
    heartbeat = Heartbeat()
    asyncio.run(heartbeat.run())
