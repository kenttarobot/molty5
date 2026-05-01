"""
Heartbeat loop — main orchestration per heartbeat.md.
State machine: setup → join → play → settle → repeat.

PERBAIKAN TERBARU (v2):
- Agent yang MATI tidak lagi stuck di IN_GAME
- Skip play_game dan langsung kembali ke cycle utama
- Tambah jeda yang cukup agar state_router bisa update
- Tidak bergantung pada method leave_game yang belum ada
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

        # Phase 0: First-run account setup
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
        dashboard_state.add_log("Bot started", "info")

        if ENABLE_MEMORY:
            await self.memory.load()
            if creds.get("agent_name"):
                self.memory.set_agent_name(creds["agent_name"])
        else:
            log.info("Memory system disabled (ENABLE_MEMORY=false)")

        # Main loop
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
                log.error("Heartbeat error (#%d): %s. Retrying in %ds...", 
                          consecutive_errors, e, wait)
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
        log.info("State: %s", state)

        self._agent_key = str(me.get("agentId", me.get("id", "agent-1")))
        self._agent_name = me.get("agentName", me.get("name", "Agent"))
        balance = me.get("balance", 0)

        dashboard_state.total_smoltz = balance
        dashboard_state.update_agent(self._agent_key, {
            "name": self._agent_name,
            "status": "playing" if state == IN_GAME else "idle",
            "smoltz": balance,
            "whitelisted": state != NO_IDENTITY,
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

    async def _handle_no_identity(self, me: dict):
        creds = load_credentials() or {}
        owner_eoa = creds.get("owner_eoa", "")
        agent_eoa = creds.get("agent_wallet_address", "")

        if not owner_eoa:
            log.error("Owner EOA not set. Re-run setup.")
            await asyncio.sleep(30)
            return

        if AUTO_SC_WALLET:
            wallet_addr = await ensure_molty_wallet(self.api, owner_eoa)
            if not wallet_addr:
                log.info("MoltyRoyale Wallet needs recovery. Check docs.")
                await asyncio.sleep(30)
                return
        else:
            log.info("SC Wallet creation skipped.")

        if AUTO_WHITELIST:
            wl_ok = await ensure_whitelist(self.api, owner_eoa, agent_eoa)
            if not wl_ok:
                log.info("⏳ Whitelist pending — Fund Owner EOA with CROSS.")
                await asyncio.sleep(120)
                return
        else:
            log.info("Whitelist auto-approval skipped.")

        if AUTO_IDENTITY:
            id_ok = await ensure_identity(self.api)
            if not id_ok:
                log.info("Identity registration pending.")
                await asyncio.sleep(30)
                return

        log.info("✅ Full setup complete!")

    async def _handle_ready(self, me: dict, state: str):
        """Join a new game."""
        room_type = select_room(me)
        log.info(f"Attempting to join {room_type} room...")

        try:
            if room_type == "paid":
                game_id, agent_id = await join_paid_game(self.api)
            else:
                game_id, agent_id = await join_free_game(self.api)

            log.info(f"✅ Successfully joined {room_type} game: {game_id}")
            await self._play_game(game_id, agent_id, room_type)
        except Exception as e:
            log.warning("Join failed: %s. Retrying in 10s.", e)
            await asyncio.sleep(10)

    async def _handle_in_game(self, ctx: dict):
        """Handle active game — Jika mati maka langsung skip dan kembali ke cycle."""
        game_id = ctx.get("game_id")
        agent_id = ctx.get("agent_id")
        is_alive = ctx.get("is_alive", True)

        if not is_alive:
            log.info(f"Agent MATI di game {game_id}. Melewati game ini dan akan mencoba join room baru di cycle berikutnya.")
            dashboard_state.add_log(f"Agent mati di {game_id[:12]} → skip & retry join", "warning", self._agent_key)
            
            # Jeda lebih panjang agar state_router punya waktu update status game
            await asyncio.sleep(5)
            return

        # Masih hidup → main game normal
        entry_type = ctx.get("entry_type", "free")
        await self._play_game(game_id, agent_id, entry_type)

    async def _play_game(self, game_id: str, agent_id: str, entry_type: str):
        log.info("═══ PLAYING GAME: %s (type=%s) ═══", game_id, entry_type)

        dashboard_state.update_agent(self._agent_key, {
            "status": "playing",
            "room_id": game_id,
            "room_name": entry_type + " room",
        })
        dashboard_state.add_log(f"Joined {entry_type} game: {game_id[:12]}", "info", self._agent_key)

        self.memory.set_temp_game(game_id)
        await self.memory.save()

        engine = WebSocketEngine(game_id, agent_id)
        engine.dashboard_key = self._agent_key
        engine.dashboard_name = self._agent_name

        game_result = await engine.run()

        # Settlement
        if game_result and game_result.get("status") == "dead":
            await settle_game(game_result, entry_type, self.memory, early_exit=True)
        else:
            await settle_game(game_result, entry_type, self.memory)

        log.info("Game cycle complete. Next cycle in 3s...")
        await asyncio.sleep(3)


if __name__ == "__main__":
    log.info("Molty Royale AI Agent v2.0.0")
    log.info("Press Ctrl+C to stop")
    heartbeat = Heartbeat()
    asyncio.run(heartbeat.run())
