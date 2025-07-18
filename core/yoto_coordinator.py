import asyncio
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any

from yoto_api import YotoManager, YotoPlayerConfig
from yoto_api.exceptions import AuthenticationError

from .data_models import Card

logger = logging.getLogger(__name__)


class YotoCoordinator:
    """Async coordinator around ``YotoManager`` with MQTT handling."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        device_id: Optional[str] = None,
        update_interval: int = 30,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.username = username
        self.password = password
        self.device_id = device_id
        self.update_interval = update_interval
        self.cache_dir = cache_dir or Path(__file__).parent.parent / "cache" / "art"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.manager: Optional[YotoManager] = None
        self.playback_status: str = "stopped"
        self.active_card_id: Optional[str] = None
        self.current_card_title: Optional[str] = None
        self.current_chapter_title: Optional[str] = None
        self.current_track_title: Optional[str] = None
        self.track_position: int = 0
        self.track_length: int = 0

        self.library: Dict[str, Card] = {}
        self._callbacks: List[Callable[[], None]] = []
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Authenticate and start the update loop."""
        await self._init_manager()
        self._task = asyncio.create_task(self._update_loop())

    async def stop(self) -> None:
        """Stop the update loop and disconnect."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.manager:
            await asyncio.to_thread(self.manager.disconnect)
            self.manager = None

    # ------------------------------------------------------------------
    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_listeners(self) -> None:
        for callback in list(self._callbacks):
            try:
                callback()
            except Exception as exc:  # pragma: no cover - best effort
                logger.error("Listener error: %s", exc)

    # ------------------------------------------------------------------
    async def _init_manager(self) -> None:
        def create_manager() -> YotoManager:
            mgr = YotoManager(self.username, self.password)
            mgr.initialize()
            return mgr

        try:
            self.manager = await asyncio.to_thread(create_manager)
            await asyncio.to_thread(self.manager.connect_to_events, self._on_event)
            self._update_devices()
            self._update_state_from_player()
        except AuthenticationError as exc:
            logger.error("Authentication failed: %s", exc)
            raise
        except Exception as exc:  # pragma: no cover - network errors
            logger.error("Failed to initialise manager: %s", exc)
            raise

    async def _update_loop(self) -> None:
        assert self.manager is not None
        while True:
            try:
                await asyncio.to_thread(self.manager.check_and_refresh_token)
                await asyncio.to_thread(self.manager.update_players_status)
                await asyncio.sleep(0)  # allow cancellation
                self._update_state_from_player()
                self._notify_listeners()
            except Exception as exc:  # pragma: no cover - best effort
                logger.error("Update loop error: %s", exc)
            await asyncio.sleep(self.update_interval)

    def _on_event(self) -> None:
        """MQTT event callback."""
        self._update_state_from_player()
        self._notify_listeners()

    # ------------------------------------------------------------------
    def _update_devices(self) -> None:
        assert self.manager is not None
        self.devices = {
            pid: {
                "name": player.name,
                "device_type": player.device_type,
                "online": player.online,
            }
            for pid, player in self.manager.players.items()
        }

    def _resolve_device_id(self) -> Optional[str]:
        if self.device_id:
            return self.device_id
        if self.manager and self.manager.players:
            return next(iter(self.manager.players))
        return None

    def _parse_key(self, key: Optional[str]) -> int:
        if not key:
            return 1
        digits = "".join(ch for ch in str(key) if ch.isdigit())
        try:
            return int(digits)
        except Exception:
            return 1

    def _get_card_title(self, card_id: str) -> str:
        card = self.library.get(card_id)
        if card and card.title:
            return card.title
        if self.manager and hasattr(self.manager, "library"):
            item = self.manager.library.get(card_id)
            if item and hasattr(item, "title"):
                return item.title
        return card_id

    def _update_state_from_player(self) -> None:
        if not self.manager:
            return
        device_id = self._resolve_device_id()
        if not device_id:
            return
        player = self.manager.players.get(device_id)
        if not player:
            return
        self.playback_status = player.playback_status or (
            "playing" if getattr(player, "is_playing", False) else "stopped"
        )
        self.active_card_id = player.card_id
        self.current_chapter_title = player.chapter_title
        self.current_track_title = player.track_title
        self.track_position = player.track_position or 0
        self.track_length = player.track_length or 0
        if player.card_id:
            self.current_card_title = self._get_card_title(player.card_id)
        else:
            self.current_card_title = None

    # ------------------------------------------------------------------
    async def get_library(self, force_refresh: bool = False) -> List[Card]:
        if not self.manager:
            return []
        now = asyncio.get_event_loop().time()
        if (
            not force_refresh
            and self.library
            and (now - getattr(self, "_library_timestamp", 0)) < 300
        ):
            return list(self.library.values())
        await asyncio.to_thread(self.manager.update_library)
        self.library.clear()
        for cid, item in self.manager.library.items():
            card = Card(id=cid, title=item.title)
            art_path = await asyncio.to_thread(self._get_or_download_artwork, cid, item.cover_image_large)
            if art_path:
                card.art_path = art_path
            self.library[cid] = card
        self._library_timestamp = now
        return list(self.library.values())

    async def get_card_chapters(self, card_id: str) -> Optional[List[Dict[str, Any]]]:
        if not self.manager:
            return None
        card = self.manager.library.get(card_id)
        if not card or not card.chapters:
            try:
                await asyncio.to_thread(self.manager.update_card_detail, card_id)
                card = self.manager.library.get(card_id)
            except Exception as exc:
                logger.error("Failed to load chapters for %s: %s", card_id, exc)
                return []
        if not card or not card.chapters:
            return []
        return [
            {
                "key": chap.key,
                "title": chap.title,
                "duration": chap.duration,
                "iconUrl": chap.icon,
            }
            for chap in card.chapters.values()
        ]

    # ------------------------------------------------------------------
    async def play(self) -> None:
        if not self.manager:
            return
        device_id = self._resolve_device_id()
        if device_id:
            await asyncio.to_thread(self.manager.resume_player, device_id)

    async def pause(self) -> None:
        if not self.manager:
            return
        device_id = self._resolve_device_id()
        if device_id:
            await asyncio.to_thread(self.manager.pause_player, device_id)

    async def stop_player(self) -> None:
        if not self.manager:
            return
        device_id = self._resolve_device_id()
        if device_id:
            await asyncio.to_thread(self.manager.stop_player, device_id)

    async def play_card(
        self,
        card_id: str,
        chapter: int | str = 1,
        *,
        seconds_in: int = 0,
        cutoff: int = 0,
        track_key: Optional[int] = None,
    ) -> None:
        if not self.manager:
            return
        device_id = self._resolve_device_id()
        if not device_id:
            return
        if track_key is None:
            track_key = self._parse_key(str(chapter))
        chap_key_str = str(chapter).zfill(2)
        await asyncio.to_thread(
            self.manager.play_card,
            device_id,
            card_id,
            seconds_in,
            cutoff,
            chap_key_str,
            track_key,
        )

    async def next_track(self) -> None:
        if not self.manager:
            return
        device_id = self._resolve_device_id()
        if not device_id:
            return
        await asyncio.to_thread(self.manager.next_track, device_id)

    async def previous_track(self) -> None:
        if not self.manager:
            return
        device_id = self._resolve_device_id()
        if not device_id:
            return
        await asyncio.to_thread(self.manager.previous_track, device_id)

    # ------------------------------------------------------------------
    def _get_or_download_artwork(
        self, card_id: str, art_url: Optional[str]
    ) -> Optional[Path]:
        existing = list(self.cache_dir.glob(f"{card_id}.*"))
        if existing:
            return existing[0]
        if not art_url:
            return None
        try:
            import requests

            response = requests.get(art_url, timeout=15)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            art_path = self.cache_dir / f"{card_id}{ext}"
            art_path.write_bytes(response.content)
            return art_path
        except Exception as exc:  # pragma: no cover
            logger.error("Failed to download artwork for %s: %s", card_id, exc)
            return None
