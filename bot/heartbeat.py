"""
Heartbeat loop — main orchestration per heartbeat.md.
State machine: setup → join → play → settle → repeat.
Respects First-Run Intake config flags for Railway/Docker deployment.

v2.0.0 MAJOR UPGRADE — DEAD-REJOIN ARCHITECTURE:
════════════════════════════════════════════════════════════════
🚀 INSTANT REJOIN ON DEATH:
   Saat agent mati di sebuah game, bot TIDAK lagi menunggu
   game_ended. Sebaliknya:
   1. WebSocketEngine mengembalikan GameResult dengan is_dead=True
      SEGERA setelah event agent_died diterima.
   2. Heartbeat spawns asyncio.Task baru → join + play room baru
      PARALEL dengan settlement task game lama.
   3. Settlement game lama berjalan di background task tersendiri
      (menunggu game_ended untuk klaim reward jika ada).
   4. Slot permainan aktif dibatasi MAX_CONCURRENT_GAMES (default 2)
      untuk mencegah spam join.

🔄 CONCURRENT GAME SLOTS:
   _active_game_tasks: dict[game_id → asyncio.Task]
   Setiap task mengelola satu game dari join → play → settle.
   Task di-cleanup otomatis saat selesai.

📊 DASHBOARD MULTI-SLOT:
   Setiap game slot punya key dashboard sendiri (agent-1, agent-2, …)
   sehingga status tiap slot terlihat terpisah.

⚡ ZERO IDLE TIME:
   Bot selalu dalam kondisi bermain selama ada slot tersedia.
   Jika semua slot penuh, heartbeat cycle menunggu slot bebas.
════════════════════════════════════════════════════════════════
"""
import asyncio
from bot.api_client import MoltyAPI, APIError
from bot.dashboard.state import dashboard_state
from bot.state_router import (
    determine_state,
    NO_ACCOUNT, NO_IDENTITY, IN_GAME, READY_PAID, READY_FREE,
)
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

# ── Concurrent game slots ────────────────────────────────────────────────────
# Berapa game yang boleh berjalan paralel.
# 1 = perilaku lama (sequential)
# 2 = langsung join room baru saat mati (recommended)
# 3+ = lebih agresif, pastikan API / wallet mendukung
MAX_CONCURRENT_GAMES: int = 2

# Jeda minimal antar join untuk menghindari rate-limit API
JOIN_COOLDOWN_SECONDS: float = 3.0

# Jika mati di game, tunggu sebentar sebelum join baru (beri server waktu)
DEAD_REJOIN_DELAY_SECONDS: float = 2.0


class Heartbeat:
    """
    Main heartbeat loop — runs forever, manages the full agent lifecycle.

    v2.0.0: Supports concurrent game slots via asyncio.Task pool.
    Saat agent mati, langsung spawn task baru untuk join room lain
    tanpa menunggu game aktif selesai.
    """

    def __init__(self):
        self.api: MoltyAPI | None = None
        self.memory = AgentMemory()
        self.running = True
        self._agent_key  = "agent-1"
        self._agent_name = "Agent"

        # ── Concurrent slot management ──────────────────────────────
        # game_id → asyncio.Task  (satu task per active game slot)
        self._active_game_tasks: dict[str, asyncio.Task] = {}

        # Slot counter untuk dashboard key yang unik (agent-1, agent-2, …)
        self._slot_counter: int = 0

        # Lock untuk join agar tidak double-join bersamaan
        self._join_lock = asyncio.Lock()

        # Timestamp join terakhir (rate-limit guard)
        self._last_join_time: float = 0.0

    # =========================================================================
    # 🚀  ENTRY POINT
    # =========================================================================

    async def run(self):
        """Entry point — runs the heartbeat loop indefinitely."""
        log.info("═══════════════════════════════════════════")
        log.info("  MOLTY ROYALE AI AGENT — STARTING v2.0.0")
        log.info("  MAX_CONCURRENT_GAMES = %d", MAX_CONCURRENT_GAMES)
        log.info("═══════════════════════════════════════════")

        log.info("Config (First-Run Intake answers):")
        log.info("  ADVANCED_MODE   = %s", ADVANCED_MODE)
        log.info("  AUTO_SC_WALLET  = %s", AUTO_SC_WALLET)
        log.info("  AUTO_WHITELIST  = %s", AUTO_WHITELIST)
        log.info("  ENABLE_MEMORY   = %s", ENABLE_MEMORY)
        log.info("  AUTO_IDENTITY   = %s", AUTO_IDENTITY)
        log.info("  ROOM_MODE       = %s", ROOM_MODE)

        # Phase 0: First-run intake + account setup
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
        dashboard_state.add_log("Bot started v2.0.0 — instant rejoin enabled", "info")

        if ENABLE_MEMORY:
            await self.memory.load()
            if creds.get("agent_name"):
                self.memory.set_agent_name(creds["agent_name"])
        else:
            log.info("Memory system disabled (ENABLE_MEMORY=false)")

        # Main loop — NEVER exits, NEVER crashes
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

        # Graceful shutdown: tunggu semua game task selesai
        if self._active_game_tasks:
            log.info("Waiting for %d active game tasks to finish...",
                     len(self._active_game_tasks))
            await asyncio.gather(*self._active_game_tasks.values(),
                                 return_exceptions=True)

        if self.api:
            await self.api.close()
        log.info("Agent stopped.")

    # =========================================================================
    # 🔄  HEARTBEAT CYCLE
    # =========================================================================

    async def _heartbeat_cycle(self):
        """Single heartbeat cycle: check state → route → act."""
        # Bersihkan task yang sudah selesai
        self._cleanup_finished_tasks()

        try:
            me = await self.api.get_accounts_me()
        except APIError as e:
            if e.status == 401:
                log.error("Invalid API key. Re-run setup.")
                self.running = False
                return
            raise

        state, ctx = determine_state(me)
        log.info("State: %s | Active slots: %d/%d",
                 state, len(self._active_game_tasks), MAX_CONCURRENT_GAMES)

        # Dashboard update
        self._agent_key  = str(me.get("agentId", me.get("id", "agent-1")))
        self._agent_name = me.get("agentName", me.get("name", "Agent"))
        balance = me.get("balance", 0)
        dashboard_state.total_smoltz = balance
        dashboard_state.update_agent(self._agent_key, {
            "name":        self._agent_name,
            "status":      "playing" if state == IN_GAME else "idle",
            "smoltz":      balance,
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

    # =========================================================================
    # 🎮  GAME LIFECYCLE  (UPGRADED v2.0.0)
    # =========================================================================

    async def _handle_in_game(self, ctx: dict):
        """
        Resume atau mulai bermain game aktif.

        v2.0.0: Jika agent sudah mati (is_alive=False) DAN slot tersedia,
        langsung spawn task join room baru TANPA menunggu game ini selesai.
        """
        game_id    = ctx["game_id"]
        agent_id   = ctx["agent_id"]
        entry_type = ctx.get("entry_type", "free")
        is_alive   = ctx.get("is_alive", True)

        # Game ini sudah punya task yang berjalan → skip (sudah ditangani)
        if game_id in self._active_game_tasks:
            if is_alive:
                log.debug("Game %s already has active task, skipping.", game_id[:12])
            else:
                # Mati, tapi task game lama masih berjalan (settlement/spectate)
                # → coba spawn slot baru
                await self._try_spawn_new_slot(entry_type, reason="dead_in_existing_game")
            return

        if not is_alive:
            log.info("⚰️  Agent dead in game %s — spawning settlement task + new slot",
                     game_id[:12])
            # Spawn settlement-only task untuk game lama (klaim reward)
            self._spawn_game_task(game_id, agent_id, entry_type, dead_on_arrival=True)
            # Langsung coba join room baru
            await self._try_spawn_new_slot(entry_type, reason="dead_on_rejoin")
        else:
            # Normal: game sedang berjalan, buat task untuk game ini
            self._spawn_game_task(game_id, agent_id, entry_type, dead_on_arrival=False)

    async def _handle_ready(self, me: dict, state: str):
        """Join game baru jika ada slot tersedia."""
        if len(self._active_game_tasks) >= MAX_CONCURRENT_GAMES:
            log.debug("All %d slots busy. Waiting...", MAX_CONCURRENT_GAMES)
            await asyncio.sleep(5)
            return

        room_type = select_room(me)
        await self._try_spawn_new_slot(room_type, reason="ready")

    async def _try_spawn_new_slot(self, room_type: str, reason: str = ""):
        """
        Coba join + spawn task game baru jika slot tersedia.
        Thread-safe via _join_lock.
        """
        if len(self._active_game_tasks) >= MAX_CONCURRENT_GAMES:
            log.debug("All slots busy (%s). Skipping new join.", reason)
            return

        # Rate-limit guard
        import time
        elapsed = time.monotonic() - self._last_join_time
        if elapsed < JOIN_COOLDOWN_SECONDS:
            wait = JOIN_COOLDOWN_DELAY_SECONDS - elapsed
            log.debug("Join cooldown: waiting %.1fs", wait)
            await asyncio.sleep(JOIN_COOLDOWN_SECONDS - elapsed)

        async with self._join_lock:
            # Re-check setelah lock (race condition guard)
            if len(self._active_game_tasks) >= MAX_CONCURRENT_GAMES:
                return

            if reason.startswith("dead"):
                log.info("💀→🔄 Instant rejoin: agent mati, join room baru... (%s)", reason)
                await asyncio.sleep(DEAD_REJOIN_DELAY_SECONDS)

            try:
                if room_type == "paid":
                    game_id, agent_id = await join_paid_game(self.api)
                else:
                    game_id, agent_id = await join_free_game(self.api)

                import time
                self._last_join_time = time.monotonic()

                log.info("✅ Joined new %s room: %s (reason=%s)",
                         room_type, game_id[:12], reason)
                self._spawn_game_task(game_id, agent_id, room_type, dead_on_arrival=False)

            except APIError as e:
                if e.code == "NO_IDENTITY":
                    log.error("Identity required for join.")
                    return
                log.warning("Join failed (%s): %s. Will retry next cycle.", reason, e)
                await asyncio.sleep(10)
            except RuntimeError as e:
                log.warning("Join failed (%s): %s. Will retry next cycle.", reason, e)
                await asyncio.sleep(10)

    def _spawn_game_task(self, game_id: str, agent_id: str,
                         entry_type: str, dead_on_arrival: bool):
        """
        Buat asyncio.Task untuk mengelola satu game slot penuh:
        play → on_death callback → settle.
        """
        if game_id in self._active_game_tasks:
            log.debug("Task for game %s already exists.", game_id[:12])
            return

        # Slot key untuk dashboard (agent-1, agent-2, …)
        self._slot_counter += 1
        slot_key  = f"agent-{self._slot_counter}"
        slot_name = f"{self._agent_name}#{self._slot_counter}"

        task = asyncio.create_task(
            self._run_game_slot(
                game_id, agent_id, entry_type,
                slot_key, slot_name, dead_on_arrival,
            ),
            name=f"game-slot-{game_id[:8]}",
        )
        self._active_game_tasks[game_id] = task

        # Auto-cleanup saat task selesai
        task.add_done_callback(
            lambda t: self._on_task_done(game_id, t)
        )

        log.info("🎮 Spawned game task [%s] game=%s dead_on_arrival=%s",
                 slot_key, game_id[:12], dead_on_arrival)

    async def _run_game_slot(self, game_id: str, agent_id: str,
                             entry_type: str, slot_key: str,
                             slot_name: str, dead_on_arrival: bool):
        """
        Coroutine utama satu game slot.

        Flow normal  : play → (mati → trigger rejoin) → settle
        dead_on_arrival: langsung settle (tidak perlu main, klaim reward saja)
        """
        # Dashboard
        dashboard_state.update_agent(slot_key, {
            "name":      slot_name,
            "status":    "spectating" if dead_on_arrival else "playing",
            "room_id":   game_id,
            "room_name": entry_type + " room",
        })
        dashboard_state.add_log(
            f"{'Settlement' if dead_on_arrival else 'Playing'} "
            f"{entry_type} game: {game_id[:12]}",
            "info", slot_key,
        )

        self.memory.set_temp_game(game_id)
        await self.memory.save()

        game_result = None

        if not dead_on_arrival:
            # ── Jalankan WebSocket engine ──────────────────────────
            engine = WebSocketEngine(game_id, agent_id)
            engine.dashboard_key  = slot_key
            engine.dashboard_name = slot_name

            # 🆕 Pasang callback: dipanggil SEGERA saat agent mati
            # Ini yang memicu join room baru tanpa tunggu game berakhir
            engine.on_agent_died = self._on_agent_died_in_slot

            game_result = await engine.run()

            # Cek apakah mati sebelum game berakhir
            if game_result and game_result.get("died_before_end"):
                log.info("⚰️  [%s] Died before game ended — settlement pending, "
                         "new slot already spawned.", slot_key)
        else:
            # dead_on_arrival: tunggu game_ended lewat WS (spectate saja)
            log.info("👁️  [%s] Spectating game %s for settlement...",
                     slot_key, game_id[:12])
            engine = WebSocketEngine(game_id, agent_id)
            engine.dashboard_key   = slot_key
            engine.dashboard_name  = slot_name
            engine.spectate_only   = True   # 🆕 Flag: jangan kirim action
            game_result = await engine.run()

        # ── Settlement ─────────────────────────────────────────────
        if game_result:
            try:
                await settle_game(game_result, entry_type, self.memory)
                log.info("✅ [%s] Settlement complete for game %s",
                         slot_key, game_id[:12])
            except Exception as e:
                log.error("Settlement error [%s]: %s", slot_key, e)

        dashboard_state.update_agent(slot_key, {"status": "idle"})
        log.info("🏁 [%s] Game slot finished: %s", slot_key, game_id[:12])

    async def _on_agent_died_in_slot(self, game_id: str, entry_type: str):
        """
        🆕 Callback dipanggil oleh WebSocketEngine SEGERA setelah
        event `agent_died` diterima — SEBELUM game berakhir.

        Ini adalah inti dari fitur instant-rejoin:
        bot langsung cari room baru tanpa menunggu game_ended.
        """
        log.info("💀 Agent died in game %s — triggering instant rejoin!", game_id[:12])
        dashboard_state.add_log(
            f"Died in {game_id[:12]} — finding new room instantly!", "warning"
        )
        # Spawn slot baru secara paralel (non-blocking)
        asyncio.create_task(
            self._try_spawn_new_slot(entry_type, reason=f"died_in_{game_id[:8]}"),
            name=f"rejoin-after-{game_id[:8]}",
        )

    # =========================================================================
    # 🧹  TASK MANAGEMENT
    # =========================================================================

    def _cleanup_finished_tasks(self):
        """Hapus task yang sudah selesai dari registry."""
        done = [gid for gid, t in self._active_game_tasks.items() if t.done()]
        for gid in done:
            task = self._active_game_tasks.pop(gid)
            exc  = task.exception() if not task.cancelled() else None
            if exc:
                log.error("Game task %s raised exception: %s", gid[:12], exc)

    def _on_task_done(self, game_id: str, task: asyncio.Task):
        """Done callback — log hasil task."""
        if task.cancelled():
            log.warning("Game task %s was cancelled.", game_id[:12])
        elif task.exception():
            log.error("Game task %s raised: %s", game_id[:12], task.exception())
        else:
            log.info("Game task %s completed successfully.", game_id[:12])

    # =========================================================================
    # 🔧  SETUP HANDLERS  (tidak berubah dari v1.0)
    # =========================================================================

    async def _handle_no_identity(self, me: dict):
        """Setup pipeline: wallet → whitelist → identity."""
        creds     = load_credentials() or {}
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
            log.info("SC Wallet creation skipped (AUTO_SC_WALLET=false)")

        if AUTO_WHITELIST:
            wl_ok = await ensure_whitelist(self.api, owner_eoa, agent_eoa)
            if not wl_ok:
                log.info(
                    "⏳ Whitelist pending — fund Owner EOA: %s then retry in 2min.",
                    owner_eoa,
                )
                await asyncio.sleep(120)
                return
        else:
            log.info("Whitelist skipped (AUTO_WHITELIST=false). "
                     "Approve at https://www.moltyroyale.com")

        if AUTO_IDENTITY:
            id_ok = await ensure_identity(self.api)
            if not id_ok:
                log.info("Identity registration pending. Retry in 30s.")
                await asyncio.sleep(30)
                return
        else:
            log.info("Identity skipped (AUTO_IDENTITY=false)")

        log.info("✅ Full setup complete!")


# =============================================================================
# 📝  CATATAN UNTUK INTEGRASI WebSocketEngine
# =============================================================================
#
# Agar fitur instant-rejoin bekerja, WebSocketEngine perlu 2 perubahan kecil:
#
# 1. TAMBAH ATRIBUT `on_agent_died` dan `spectate_only`:
#
#    class WebSocketEngine:
#        def __init__(self, game_id, agent_id):
#            ...
#            self.on_agent_died = None   # callback async: (game_id, entry_type) → None
#            self.spectate_only = False  # jika True, terima event tapi jangan action
#
# 2. PANGGIL CALLBACK saat event `agent_died` diterima (di ws message handler):
#
#    async def _handle_event(self, event: dict):
#        event_type = event.get("type")
#
#        if event_type == "agent_died":
#            agent_id = event.get("agentId")
#            if agent_id == self.agent_id:
#                # Tandai hasil game
#                self._game_result["died_before_end"] = True
#                # Trigger instant-rejoin callback (non-blocking)
#                if self.on_agent_died:
#                    asyncio.create_task(
#                        self.on_agent_died(self.game_id, self._entry_type)
#                    )
#                # Jangan disconnect — tetap sambungkan untuk settlement
#
#        if event_type == "game_ended":
#            await self._handle_game_ended(event)
#            self._done.set()   # signal bahwa engine.run() boleh return
#
# 3. SPECTATE MODE — skip action dispatch jika spectate_only=True:
#
#    async def _process_view(self, view: dict):
#        if self.spectate_only:
#            return   # Tidak kirim action, hanya tunggu game_ended
#        ... # logika decide_action normal
#
# Tidak ada perubahan lain yang diperlukan di WebSocketEngine.
# =============================================================================
