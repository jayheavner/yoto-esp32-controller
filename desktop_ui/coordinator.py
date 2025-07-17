import asyncio
import os
import logging
from typing import List, Optional, Dict, Any
from PySide6.QtCore import QObject, Slot, Signal, Property
from core.api_client import YotoAPIClient
from core.data_models import Card

logger = logging.getLogger(__name__)


class DesktopCoordinator(QObject):
    # Qt signals for property changes
    playbackStateChanged = Signal()
    activeCardChanged = Signal()
    
    def __init__(self):
        super().__init__()
        self.api_client: Optional[YotoAPIClient] = None
        self._is_authenticated = False
        
        # Initialize and start monitoring state automatically
        self._initialize_client()
    
    def _initialize_client(self) -> None:
        """Initialize API client and start state monitoring"""
        try:
            username = os.getenv("YOTO_USERNAME")
            password = os.getenv("YOTO_PASSWORD")
            
            if username and password:
                self.api_client = YotoAPIClient()
                if self.api_client.authenticate(username, password):
                    self._is_authenticated = True
                    # Connect to state changes for automatic UI updates
                    self.api_client.add_state_callback(self._on_state_change)
                    logger.info("Coordinator initialized with MQTT state monitoring")
                else:
                    logger.warning("Authentication failed during coordinator initialization")
            else:
                logger.warning("No credentials available for coordinator initialization")
        except Exception as e:
            logger.error(f"Failed to initialize coordinator: {e}")
    
    def _on_state_change(self) -> None:
        """Handle state changes from MQTT"""
        if not self.api_client:
            return
            
        # Log unexpected states for further scope/action determination
        status = self.api_client.playback_status
        if status not in ["playing", "paused", "stopped"]:
            logger.warning(f"Unexpected playback status for further scope: {status}")
        
        # Emit Qt signals to update QML properties
        self.playbackStateChanged.emit()
        self.activeCardChanged.emit()
        
        logger.debug(f"State change: status={status}, card={self.api_client.active_card_id}")
    
    # Qt Properties for QML binding
    @Property(bool, notify=playbackStateChanged)
    def isPlaying(self) -> bool:
        """True if currently playing audio"""
        if not self.api_client:
            return False
        return self.api_client.playback_status == "playing"
    
    @Property(bool, notify=activeCardChanged)  
    def showNowPlaying(self) -> bool:
        """True if there's an active card (playing or paused)"""
        if not self.api_client:
            return False
        return self.api_client.active_card_id is not None
    
    @Property(str, notify=playbackStateChanged)
    def playbackStatus(self) -> str:
        """Current playback status: playing, paused, stopped"""
        if not self.api_client:
            return "stopped"
        return self.api_client.playback_status
    
    @Property(str, notify=activeCardChanged)
    def activeCardId(self) -> str:
        """ID of currently active card, empty string if none"""
        if not self.api_client or not self.api_client.active_card_id:
            return ""
        return self.api_client.active_card_id
    
    @Property(str, notify=activeCardChanged)
    def currentCardTitle(self) -> str:
        """Title of currently active card, empty string if none"""
        if not self.api_client or not self.api_client.current_card_title:
            return ""
        return self.api_client.current_card_title

    @Property(str, notify=activeCardChanged)
    def activeCardImagePath(self) -> str:
        """File URL to artwork for the currently active card"""
        if not self.api_client or not self.api_client.active_card_id:
            return ""

        return self.getCardArtwork(self.api_client.active_card_id)
    
    @Property(str, notify=activeCardChanged)
    def currentChapterTitle(self) -> str:
        """Title of currently playing chapter from MQTT"""
        if not self.api_client:
            return ""
        return getattr(self.api_client, 'current_chapter_title', '')
    
    @Property(str, notify=activeCardChanged) 
    def currentTrackTitle(self) -> str:
        """Title of currently playing track from MQTT"""
        if not self.api_client:
            return ""
        return getattr(self.api_client, 'current_track_title', '')
    
    @Property(int, notify=playbackStateChanged)
    def trackPosition(self) -> int:
        """Current playback position in seconds"""
        if not self.api_client:
            return 0
        return getattr(self.api_client, 'track_position', 0)
    
    @Property(int, notify=playbackStateChanged)
    def trackLength(self) -> int:
        """Total track length in seconds"""
        if not self.api_client:
            return 0
        return getattr(self.api_client, 'track_length', 0)
    
    def getCardArtwork(self, card_id: str) -> str:
        """Get artwork path for a specific card ID"""
        if not self.api_client:
            return ""
        
        # Get the library to find the card
        try:
            cards = self.api_client.get_library()
            for card in cards:
                if card.id == card_id and card.art_path and card.art_path.exists():
                    from PySide6.QtCore import QUrl
                    return QUrl.fromLocalFile(str(card.art_path)).toString()
            return ""
        except Exception as e:
            logger.error(f"Error getting artwork for card {card_id}: {e}")
            return ""
    
    def get_cards(self) -> List[Card]:
        """Get library cards, creating client if needed"""
        # Use existing client if available and authenticated
        if self.api_client and self._is_authenticated:
            cards = self.api_client.get_library()
            if cards:
                return cards
        
        # Fallback to create new client (original behavior)
        username = os.getenv("YOTO_USERNAME")
        password = os.getenv("YOTO_PASSWORD")
        
        if not (username and password):
            raise RuntimeError("Set YOTO_USERNAME and YOTO_PASSWORD environment variables")
        
        if not self.api_client:
            self.api_client = YotoAPIClient()
        
        if not self.api_client.authenticate(username, password):
            raise RuntimeError("Authentication failed")
        
        self._is_authenticated = True
        # Add state callback if not already added
        self.api_client.add_state_callback(self._on_state_change)
        
        cards = self.api_client.get_library()
        if not cards:
            raise RuntimeError("Failed to load library")
        
        return cards
    
    @Slot(str, result=list)
    def get_chapters(self, card_id: str) -> List[Dict[str, Any]]:
        """Get chapters for a specific card ID, returns QML-friendly format"""
        if not self.api_client:
            logger.error("API client not available for chapter lookup")
            return []
            
        try:
            chapters = self.api_client.get_card_chapters(card_id)
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
            
            logger.info(f"Returning {len(qml_chapters)} chapters for card {card_id}")
            return qml_chapters
            
        except Exception as e:
            logger.error(f"Error getting chapters for {card_id}: {e}")
            return []
    
    def cleanup(self) -> None:
        """Clean shutdown of coordinator"""
        if self.api_client:
            # Remove our callback before closing
            self.api_client.remove_state_callback(self._on_state_change)
            self.api_client.close()
            self.api_client = None
        self._is_authenticated = False
        logger.info("Coordinator cleaned up")