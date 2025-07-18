import os
import logging
import asyncio
import threading
from typing import List, Optional, Dict, Any
from PySide6.QtCore import QObject, Slot, Signal, Property
from core.yoto_coordinator import YotoCoordinator
from core.data_models import Card

logger = logging.getLogger(__name__)

# Configure default console logging if not already configured
if not logging.getLogger().hasHandlers():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )


class DesktopCoordinator(QObject):
    def _format_time(self, seconds: int) -> str:
        if seconds is None or seconds < 0:
            return "00:00"
        m, s = divmod(int(seconds), 60)
        return f"{m:02}:{s:02}"

    # Qt signals for property changes
    playbackStateChanged = Signal()
    activeCardChanged = Signal()
    
    def __init__(self):
        super().__init__()
        self.coordinator: Optional[YotoCoordinator] = None
        self._is_authenticated = False
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

        logger.info("Creating DesktopCoordinator instance")
        # Initialize and start monitoring state automatically
        self._initialize_client()
    
    def _initialize_client(self) -> None:
        """Initialize async coordinator and start state monitoring"""
        logger.debug("Initializing coordinator")
        try:
            username = os.getenv("YOTO_USERNAME")
            password = os.getenv("YOTO_PASSWORD")
            device_id = os.getenv("YOTO_DEVICE_ID")
            logger.debug(
                "Init credentials: username=%s device_id=%s", bool(username), device_id
            )

            if username and password:
                self.coordinator = YotoCoordinator(
                    username,
                    password,
                    device_id=device_id,
                )
                future = asyncio.run_coroutine_threadsafe(
                    self.coordinator.start(), self._loop
                )
                future.result()
                self._is_authenticated = True
                self.coordinator.add_listener(self._on_state_change)
                logger.info("Coordinator initialized with MQTT state monitoring")
            else:
                logger.warning("No credentials available for coordinator initialization")
        except Exception as exc:
            logger.exception("Failed to initialize coordinator: %s", exc)
    
    def _on_state_change(self) -> None:
        """Handle state changes from MQTT"""
        if not self.coordinator:
            return
        status = self.coordinator.playback_status
        card = self.coordinator.active_card_id
        if status not in ["playing", "paused", "stopped"]:
            logger.warning(f"Unexpected playback status for further scope: {status}")
        # Only log if status or card has changed since last call
        if not hasattr(self, "_last_status"):
            self._last_status = None
        if not hasattr(self, "_last_card"):
            self._last_card = None

        if status != self._last_status or card != self._last_card:
            logger.info(
            "State change: status=%s card=%s now_playing=%s",
            status,
            card,
            bool(card),
            )
            self._last_status = status
            self._last_card = card
        self.playbackStateChanged.emit()
        self.activeCardChanged.emit()

    # Qt Properties for QML binding
    @Property(bool, notify=playbackStateChanged)
    def isPlaying(self) -> bool:
        """True if currently playing audio"""
        if not self.coordinator:
            return False
        return self.coordinator.playback_status == "playing"

    @Property(bool, notify=playbackStateChanged)
    def isPaused(self) -> bool:
        """True if playback is currently paused"""
        if not self.coordinator:
            return False
        return self.coordinator.playback_status == "paused"

    @Property(bool, notify=activeCardChanged)
    def showNowPlaying(self) -> bool:
        """True if there's an active card (playing or paused)"""
        if not self.coordinator:
            return False
        value = self.coordinator.active_card_id is not None
        logger.debug(
            "showNowPlaying property -> %s (card=%s)",
            value,
            self.coordinator.active_card_id,
        )
        return value

    @Property(bool, notify=activeCardChanged)
    def hasActiveContent(self) -> bool:
        """True if a card is loaded on the device"""
        if not self.coordinator:
            return False
        return bool(self.coordinator.active_card_id)

    @Property(str, notify=playbackStateChanged)
    def playbackStatus(self) -> str:
        """Current playback status: playing, paused, stopped"""
        if not self.coordinator:
            return "stopped"
        return self.coordinator.playback_status

    @Property(str, notify=activeCardChanged)
    def activeCardId(self) -> str:
        """ID of the currently active card"""
        if not self.coordinator:
            return ""
        return self.coordinator.active_card_id or ""
    
    @Property(str, notify=activeCardChanged)
    def currentCardTitle(self) -> str:
        """Title of the currently active card from MQTT."""
        if not self.coordinator:
            return ""
        return self.coordinator.current_card_title or ""
    
    @Property(str, notify=activeCardChanged)
    def activeCardImagePath(self) -> str:
        """File URL to the active card's artwork."""
        if not self.coordinator or not self.coordinator.active_card_id:
            return ""
        return self.getCardArtwork(self.coordinator.active_card_id)

    @Property(str, notify=playbackStateChanged)
    def currentChapterTitle(self) -> str:
        """Title of the chapter currently playing."""
        if not self.coordinator:
            return ""
        return self.coordinator.current_chapter_title or ""
    
    @Property(str, notify=playbackStateChanged)
    def currentTrackTitle(self) -> str:
        """Title of the currently playing track."""
        if not self.coordinator:
            return ""
        return self.coordinator.current_track_title or ""
    
    @Property(int, notify=playbackStateChanged)
    def trackPosition(self) -> int:
        """Current playback position in seconds"""
        if not self.coordinator:
            return 0
        value = self.coordinator.track_position
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except Exception:
            return 0
    
    @Property(int, notify=playbackStateChanged)
    def trackLength(self) -> int:
        """Total track length in seconds"""
        if not self.coordinator:
            return 0
        value = self.coordinator.track_length
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except Exception:
            return 0
    
    # Removed duplicate/incorrect formattedPosition and formattedDuration
    @Property(str, notify=playbackStateChanged)
    def formattedPosition(self) -> str:
        return self._format_time(self.trackPosition)

    @Property(str, notify=playbackStateChanged)
    def formattedDuration(self) -> str:
        return self._format_time(self.trackLength)

    @Property(str, notify=playbackStateChanged)
    def currentChapterIconUrl(self) -> str:
        """Icon URL for the chapter currently playing, if available."""
        if not self.coordinator:
            return ""
        card_id = self.coordinator.active_card_id
        chapter_title = self.coordinator.current_chapter_title
        if not card_id or not chapter_title:
            return ""
        chapters = asyncio.run_coroutine_threadsafe(
            self.coordinator.get_card_chapters(card_id), self._loop
        ).result()
        if not chapters:
            return ""
        for chap in chapters:
            if chap.get("title") == chapter_title:
                return chap.get("iconUrl", "") or chap.get("display", {}).get("icon16x16", "")
        return ""
    
    def getCardArtwork(self, card_id: str) -> str:
        """Get artwork path for a specific card ID"""
        if not self.coordinator:
            return ""
        # Get the library to find the card
        try:
            cards = asyncio.run_coroutine_threadsafe(
                self.coordinator.get_library(), self._loop
            ).result()
            for card in cards:
                if card.id == card_id and card.art_path and card.art_path.exists():
                    from PySide6.QtCore import QUrl
                    path = QUrl.fromLocalFile(str(card.art_path)).toString()
                    logger.debug("Found artwork for card %s -> %s", card_id, path)
                    return path
            logger.info("No artwork found for card %s", card_id)
            return ""
        except Exception as exc:
            logger.exception("Error getting artwork for card %s: %s", card_id, exc)
            return ""
    
    def get_cards(self) -> List[Card]:
        """Get library cards, creating client if needed"""
        logger.info("Fetching card library")
        # Use existing client if available and authenticated
        if self.coordinator and self._is_authenticated:
            cards = asyncio.run_coroutine_threadsafe(
                self.coordinator.get_library(), self._loop
            ).result()
            if cards:
                return cards
        
        # Fallback to create new client (original behavior)
        username = os.getenv("YOTO_USERNAME")
        password = os.getenv("YOTO_PASSWORD")

        if not (username and password):
            logger.error("Missing Yoto credentials")
            raise RuntimeError("Set YOTO_USERNAME and YOTO_PASSWORD environment variables")
        
        if not self.coordinator:
            self.coordinator = YotoCoordinator(username, password)
            future = asyncio.run_coroutine_threadsafe(
                self.coordinator.start(), self._loop
            )
            future.result()
            self.coordinator.add_listener(self._on_state_change)
            self._is_authenticated = True

        cards = asyncio.run_coroutine_threadsafe(
            self.coordinator.get_library(), self._loop
        ).result()
        if not cards:
            logger.error("Failed to load library")
            raise RuntimeError("Failed to load library")

        logger.info("Loaded %d cards", len(cards))
        return cards
    
    @Slot(str, result=list)
    def get_chapters(self, card_id: str) -> List[Dict[str, Any]]:
        """Get chapters for a specific card ID, returns QML-friendly format"""
        logger.info("Fetching chapters for card %s", card_id)
        if not self.coordinator:
            logger.error("Coordinator not available for chapter lookup")
            return []

        try:
            chapters = asyncio.run_coroutine_threadsafe(
                self.coordinator.get_card_chapters(card_id), self._loop
            ).result()
            if chapters is None:
                logger.info(f"No chapters found for card {card_id}")
                return []
            
            # Convert to QML-friendly format
            qml_chapters = []
            for chapter in chapters:
                qml_chapter = {
                    "key": chapter.get("key", ""),
                    "title": chapter.get("title", "Unknown"),
                    "duration": chapter.get("duration", 0),
                    "iconUrl": chapter.get("display", {}).get("icon16x16", "")
                }
                qml_chapters.append(qml_chapter)
            
            logger.info("Returning %d chapters for card %s", len(qml_chapters), card_id)
            return qml_chapters

        except Exception as exc:
            logger.exception("Error getting chapters for %s: %s", card_id, exc)
            return []

    # ------------------------------------------------------------------
    # Transport control slots
    # ------------------------------------------------------------------
    @Slot()
    def play(self) -> None:
        logger.info("Play requested")
        if self.coordinator:
            asyncio.run_coroutine_threadsafe(self.coordinator.play(), self._loop)
        else:
            logger.warning("Play requested but API client not initialized")

    @Slot()
    def pause(self) -> None:
        logger.info("Pause requested")
        if self.coordinator:
            asyncio.run_coroutine_threadsafe(self.coordinator.pause(), self._loop)
        else:
            logger.warning("Pause requested but API client not initialized")

    @Slot()
    def resume(self) -> None:
        logger.info("Resume requested")
        if self.coordinator:
            asyncio.run_coroutine_threadsafe(self.coordinator.play(), self._loop)
        else:
            logger.warning("Resume requested but API client not initialized")

    @Slot()
    def stop(self) -> None:
        logger.info("Stop requested")
        if self.coordinator:
            asyncio.run_coroutine_threadsafe(self.coordinator.stop_player(), self._loop)
        else:
            logger.warning("Stop requested but API client not initialized")

    @Slot()
    def toggle_play_pause(self) -> None:
        logger.info("Toggle play/pause")
        if not self.coordinator:
            logger.warning("Toggle requested but API client not initialized")
            return
        if self.coordinator.playback_status == "playing":
            asyncio.run_coroutine_threadsafe(self.coordinator.pause(), self._loop)
        else:
            asyncio.run_coroutine_threadsafe(self.coordinator.play(), self._loop)


    @Slot(str, int)
    def play_card(self, card_id: str, chapter: int = 1) -> None:
        """Play a library card on the player."""
        logger.info("Play card request: %s chapter %s", card_id, chapter)
        if self.coordinator:
            asyncio.run_coroutine_threadsafe(
                self.coordinator.play_card(card_id, chapter), self._loop
            )
        else:
            logger.warning("Play card requested but API client not initialized")

    @Slot()
    def next_track(self) -> None:
        logger.info("Next track requested")
        if self.coordinator:
            asyncio.run_coroutine_threadsafe(self.coordinator.next_track(), self._loop)
        else:
            logger.warning("Next track requested but API client not initialized")

    @Slot()
    def previous_track(self) -> None:
        logger.info("Previous track requested")
        if self.coordinator:
            asyncio.run_coroutine_threadsafe(self.coordinator.previous_track(), self._loop)
        else:
            logger.warning("Previous track requested but API client not initialized")

    def cleanup(self) -> None:
        """Clean shutdown of coordinator"""
        logger.info("Cleaning up DesktopCoordinator")
        if self.coordinator:
            self.coordinator.remove_listener(self._on_state_change)
            asyncio.run_coroutine_threadsafe(self.coordinator.stop(), self._loop).result()
            self.coordinator = None
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join()
        self._is_authenticated = False
        logger.info("Coordinator cleaned up")

    @Slot()
    def navigateToNowPlaying(self) -> None:
        """Placeholder slot used by tests for navigation"""
        logger.debug("navigateToNowPlaying called")
