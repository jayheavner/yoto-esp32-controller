"""
Async/Sync bridge for desktop UI.

Bridges between async core layer and synchronous Qt, handles Qt thread safety,
coordinates data loading with UI updates, and provides error handling with
user feedback. Desktop-only module.
"""
import asyncio
import logging
from typing import Optional, Callable, Any
from concurrent.futures import ThreadPoolExecutor
import threading

from PySide6.QtCore import QObject, Signal, QTimer, QThread
from PySide6.QtQml import qmlRegisterType

from ..core.api_client import YotoAPIClient, YotoAPIError, AuthenticationError, APIRequestError
from ..core.cache_manager import CacheManager, CacheError
from ..core.data_models import Library, Card
from ..core.ui_logic.display_controller import DisplayController, DisplayState, UIBackend
from ..core.ui_logic.grid_layout import GridLayout, GridDimensions
from ..config.base import BaseConfiguration
from .qt_models.card_model import CardModel

logger = logging.getLogger(__name__)


class QtUIBackend(QObject, UIBackend):
    """Qt-specific UI backend implementation."""
    
    # Qt signals for UI updates
    displayStateChanged = Signal(str, str)  # state, message
    cardGridUpdated = Signal()
    selectionChanged = Signal(str)  # selected_card_id
    loadingProgress = Signal(float)  # 0.0 to 1.0
    
    def __init__(self) -> None:
        super().__init__()
    
    def update_display_state(self, display_info) -> None:
        """Update the UI to reflect current display state."""
        message = display_info.message or ""
        self.displayStateChanged.emit(display_info.state.value, message)
        
        if display_info.progress is not None:
            self.loadingProgress.emit(display_info.progress)
    
    def update_card_grid(self, cards, positions) -> None:
        """Update the card grid with new data and positions."""
        self.cardGridUpdated.emit()
    
    def update_selection_visual(self, selected_card_id: Optional[str]) -> None:
        """Update visual selection indicators."""
        card_id = selected_card_id or ""
        self.selectionChanged.emit(card_id)
    
    def show_loading_indicator(self, message: str, progress: Optional[float] = None) -> None:
        """Show loading indicator with optional progress."""
        self.displayStateChanged.emit("loading", message)
        if progress is not None:
            self.loadingProgress.emit(progress)
    
    def hide_loading_indicator(self) -> None:
        """Hide loading indicator."""
        self.displayStateChanged.emit("ready", "")


class DataLoadWorker(QThread):
    """Worker thread for async data loading operations."""
    
    # Signals for progress reporting
    progressUpdated = Signal(str, float)  # message, progress
    dataLoaded = Signal(object)  # Library object
    errorOccurred = Signal(str, str)  # error_type, message
    
    def __init__(self, config: BaseConfiguration) -> None:
        super().__init__()
        self.config = config
        self.should_stop = False
    
    def run(self) -> None:
        """Run the data loading process."""
        try:
            # Set up event loop for async operations
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(self._load_data())
            finally:
                loop.close()
                
        except Exception as e:
            logger.error("Data loading failed: %s", e)
            self.errorOccurred.emit("LoadError", str(e))
    
    async def _load_data(self) -> None:
        """Load data using async operations."""
        try:
            # Initialize components
            self.progressUpdated.emit("Initializing...", 0.1)
            
            api_client = YotoAPIClient()
            cache_manager = CacheManager(self.config.cache_root)
            
            # Check if we should stop
            if self.should_stop:
                return
            
            # Try to load from cache first
            self.progressUpdated.emit("Checking cache...", 0.2)
            library = cache_manager.load_library()
            
            if library and cache_manager.is_library_cache_valid(self.config.cache_max_age_hours):
                logger.info("Using cached library data")
                self.progressUpdated.emit("Loading from cache...", 0.9)
                self.dataLoaded.emit(library)
                return
            
            # Cache miss or expired - fetch from API
            self.progressUpdated.emit("Authenticating...", 0.3)
            
            success = await asyncio.to_thread(
                api_client.authenticate, 
                self.config.username, 
                self.config.password
            )
            
            if not success or self.should_stop:
                return
            
            # Fetch library data
            self.progressUpdated.emit("Fetching library...", 0.5)
            library = await asyncio.to_thread(api_client.fetch_library)
            
            if self.should_stop:
                return
            
            # Save to cache
            self.progressUpdated.emit("Caching data...", 0.7)
            await asyncio.to_thread(cache_manager.save_library, library)
            
            # Download missing artwork
            self.progressUpdated.emit("Downloading artwork...", 0.8)
            download_count = await asyncio.to_thread(
                cache_manager.download_missing_artwork, 
                library, 
                api_client
            )
            
            logger.info("Downloaded %d artwork files", download_count)
            
            # Clean up
            api_client.close()
            
            if not self.should_stop:
                self.progressUpdated.emit("Complete", 1.0)
                self.dataLoaded.emit(library)
                
        except AuthenticationError as e:
            self.errorOccurred.emit("AuthError", str(e))
        except (APIRequestError, CacheError) as e:
            self.errorOccurred.emit("DataError", str(e))
        except Exception as e:
            logger.error("Unexpected error during data loading: %s", e)
            self.errorOccurred.emit("UnknownError", str(e))
    
    def stop(self) -> None:
        """Request the worker to stop."""
        self.should_stop = True


class DesktopCoordinator(QObject):
    """
    Coordinates between async core operations and Qt UI.
    
    Manages data loading, handles thread safety, provides progress
    updates, and bridges the gap between async core logic and
    synchronous Qt event handling.
    """
    
    # Qt signals for UI communication
    libraryLoaded = Signal(object)  # Library object
    loadingProgress = Signal(str, float)  # message, progress (0.0-1.0)
    errorOccurred = Signal(str, str)  # error_type, message
    cardSelected = Signal(str)  # card_id
    
    def __init__(self, config: BaseConfiguration) -> None:
        super().__init__()
        self.config = config
        
        # Core components
        self.grid_layout = GridLayout(GridDimensions(
            columns=config.grid_columns,
            cell_width=config.grid_cell_width,
            cell_height=config.grid_cell_height,
            margin=config.grid_margin,
            spacing=config.grid_spacing
        ))
        
        self.ui_backend = QtUIBackend()
        self.display_controller = DisplayController(self.grid_layout, self.ui_backend)
        
        # Qt models
        self.card_model = CardModel()
        
        # Worker thread for data loading
        self._data_worker: Optional[DataLoadWorker] = None
        
        # Current library
        self._library: Optional[Library] = None
        
        # Connect internal signals
        self._connect_signals()
        
        logger.info("Desktop coordinator initialized")
    
    def _connect_signals(self) -> None:
        """Connect internal signal handlers."""
        # Connect UI backend signals to coordinator
        self.ui_backend.displayStateChanged.connect(self._on_display_state_changed)
        
        # Connect display controller events
        self.display_controller.register_state_callback(self._on_display_controller_state_change)
    
    def register_qml_types(self) -> None:
        """Register custom Qt types for QML usage."""
        qmlRegisterType(CardModel, "YotoModels", 1, 0, "CardModel")
    
    def start_data_loading(self) -> None:
        """Start asynchronous data loading process."""
        if self._data_worker and self._data_worker.isRunning():
            logger.warning("Data loading already in progress")
            return
        
        logger.info("Starting data loading")
        self.loadingProgress.emit("Starting...", 0.0)
        
        # Create and start worker thread
        self._data_worker = DataLoadWorker(self.config)
        self._data_worker.progressUpdated.connect(self.loadingProgress.emit)
        self._data_worker.dataLoaded.connect(self._on_data_loaded)
        self._data_worker.errorOccurred.connect(self.errorOccurred.emit)
        self._data_worker.finished.connect(self._on_worker_finished)
        
        self._data_worker.start()
    
    def stop_data_loading(self) -> None:
        """Stop any ongoing data loading."""
        if self._data_worker and self._data_worker.isRunning():
            logger.info("Stopping data loading")
            self._data_worker.stop()
            self._data_worker.wait(5000)  # Wait up to 5 seconds
    
    def refresh_library(self) -> None:
        """Force refresh of library data from API."""
        # Clear cache and reload
        if self._library:
            try:
                cache_manager = CacheManager(self.config.cache_root)
                cache_manager.clear_cache(keep_artwork=True)  # Keep artwork, refresh metadata
                logger.info("Cache cleared, starting refresh")
            except CacheError as e:
                logger.warning("Failed to clear cache: %s", e)
        
        self.start_data_loading()
    
    def handle_card_click(self, card_id: str) -> None:
        """
        Handle card click/selection from UI.
        
        Args:
            card_id: ID of clicked card
        """
        if self._library:
            card = self.display_controller.handle_card_selection(card_id)
            if card:
                self.cardSelected.emit(card_id)
                logger.debug("Card selected: %s", card.title)
    
    def search_cards(self, query: str) -> None:
        """
        Apply search filter to displayed cards.
        
        Args:
            query: Search query string
        """
        filter_text = query.strip() if query else None
        self.display_controller.apply_filter(filter_text)
        
        # Update card model with filtered results
        filtered_cards = self.display_controller.filtered_cards
        self.card_model.set_cards(filtered_cards)
        
        logger.debug("Search applied: '%s' (%d results)", query, len(filtered_cards))
    
    def get_selected_card(self) -> Optional[Card]:
        """
        Get currently selected card.
        
        Returns:
            Selected Card object or None
        """
        return self.display_controller.selection.get_selected_card()
    
    def _on_data_loaded(self, library: Library) -> None:
        """Handle successful data loading."""
        logger.info("Data loaded successfully: %d cards", library.size)
        
        self._library = library
        self.display_controller.set_library(library)
        
        # Update card model
        self.card_model.set_cards(library.cards_list)
        
        # Emit signal for UI
        self.libraryLoaded.emit(library)
    
    def _on_worker_finished(self) -> None:
        """Handle worker thread completion."""
        if self._data_worker:
            self._data_worker.deleteLater()
            self._data_worker = None
        logger.debug("Data loading worker finished")
    
    def _on_display_state_changed(self, state: str, message: str) -> None:
        """Handle display state changes from UI backend."""
        logger.debug("Display state changed: %s - %s", state, message)
    
    def _on_display_controller_state_change(self, display_info) -> None:
        """Handle display controller state changes."""
        # Convert to progress signal if in loading state
        if display_info.state == DisplayState.LOADING and display_info.progress is not None:
            self.loadingProgress.emit(display_info.message or "Loading...", display_info.progress)
    
    def get_library_stats(self) -> dict[str, int]:
        """
        Get statistics about current library.
        
        Returns:
            Dictionary with library statistics
        """
        if not self._library:
            return {'total_cards': 0, 'cards_with_artwork': 0}
        
        stats = {
            'total_cards': self._library.size,
            'cards_with_artwork': len(self._library.get_cards_with_artwork()),
            'cards_without_artwork': len(self._library.get_cards_without_artwork())
        }
        
        return stats
    
    def cleanup(self) -> None:
        """Clean up resources before shutdown."""
        logger.info("Cleaning up desktop coordinator")
        
        # Stop any running workers
        self.stop_data_loading()
        
        # Clear models
        self.card_model.clear()
        
        logger.info("Desktop coordinator cleanup complete")
    
    @property
    def library(self) -> Optional[Library]:
        """Get current library data."""
        return self._library
    
    @property
    def has_library(self) -> bool:
        """True if library data is loaded."""
        return self._library is not None and self._library.size > 0