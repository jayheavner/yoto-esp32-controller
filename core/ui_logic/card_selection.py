"""
Selection state management for card library.

Track currently selected card, handle selection change events,
and provide multi-select logic for future features. No UI framework 
dependencies - works across Qt desktop and ESP32 implementations.
"""
from typing import Optional, Set, List, Callable
from dataclasses import dataclass, field

from ..data_models import Card


@dataclass
class SelectionEvent:
    """Represents a selection change event."""
    selected_card_id: Optional[str]
    selected_card: Optional[Card]
    previous_card_id: Optional[str]
    selection_type: str = "single"  # "single", "multi_add", "multi_remove"
    
    def __str__(self) -> str:
        if self.selected_card:
            return f"SelectionEvent(card='{self.selected_card.title}', type={self.selection_type})"
        return f"SelectionEvent(card=None, type={self.selection_type})"


# Type alias for selection event callbacks
SelectionCallback = Callable[[SelectionEvent], None]


class CardSelection:
    """
    Manages card selection state with event notification.
    
    Supports both single and multi-selection modes. Provides event
    callbacks for UI updates. Designed to work with any UI framework
    or direct rendering system.
    """
    
    def __init__(self, multi_select: bool = False) -> None:
        """
        Initialize selection manager.
        
        Args:
            multi_select: If True, enable multi-selection mode
        """
        self.multi_select = multi_select
        self._selected_card_id: Optional[str] = None
        self._selected_cards: Set[str] = set()
        self._callbacks: List[SelectionCallback] = []
        self._card_lookup: dict[str, Card] = {}
    
    def register_callback(self, callback: SelectionCallback) -> None:
        """
        Register callback for selection change events.
        
        Args:
            callback: Function to call when selection changes
        """
        if callback not in self._callbacks:
            self._callbacks.append(callback)
    
    def unregister_callback(self, callback: SelectionCallback) -> None:
        """
        Unregister selection change callback.
        
        Args:
            callback: Function to remove from callbacks
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def update_card_lookup(self, cards: List[Card]) -> None:
        """
        Update internal card lookup for event creation.
        
        Args:
            cards: List of available cards
        """
        self._card_lookup = {card.id: card for card in cards}
    
    def select_card(self, card_id: Optional[str], notify: bool = True) -> bool:
        """
        Select a card by ID.
        
        Args:
            card_id: ID of card to select, None to clear selection
            notify: If True, trigger selection change callbacks
            
        Returns:
            True if selection changed
        """
        previous_id = self._selected_card_id
        
        if self.multi_select:
            return self._select_multi(card_id, notify)
        else:
            return self._select_single(card_id, previous_id, notify)
    
    def _select_single(self, card_id: Optional[str], previous_id: Optional[str], notify: bool) -> bool:
        """Handle single selection mode."""
        if card_id == previous_id:
            return False
        
        self._selected_card_id = card_id
        self._selected_cards.clear()
        if card_id:
            self._selected_cards.add(card_id)
        
        if notify:
            self._notify_selection_change(card_id, previous_id, "single")
        
        return True
    
    def _select_multi(self, card_id: Optional[str], notify: bool) -> bool:
        """Handle multi-selection mode."""
        if card_id is None:
            # Clear all selections
            if self._selected_cards:
                self._selected_cards.clear()
                self._selected_card_id = None
                if notify:
                    self._notify_selection_change(None, self._selected_card_id, "single")
                return True
            return False
        
        selection_type = "multi_add"
        previous_id = self._selected_card_id
        
        if card_id in self._selected_cards:
            # Toggle off - remove from selection
            self._selected_cards.remove(card_id)
            selection_type = "multi_remove"
            
            # Update primary selection
            if card_id == self._selected_card_id:
                self._selected_card_id = next(iter(self._selected_cards)) if self._selected_cards else None
        else:
            # Add to selection
            self._selected_cards.add(card_id)
            self._selected_card_id = card_id  # Most recent becomes primary
        
        if notify:
            self._notify_selection_change(card_id, previous_id, selection_type)
        
        return True
    
    def select_card_by_index(self, index: int, cards: List[Card], notify: bool = True) -> bool:
        """
        Select card by list index.
        
        Args:
            index: Index in cards list
            cards: List of available cards
            notify: If True, trigger selection change callbacks
            
        Returns:
            True if selection changed
        """
        if 0 <= index < len(cards):
            return self.select_card(cards[index].id, notify)
        return False
    
    def toggle_card(self, card_id: str, notify: bool = True) -> bool:
        """
        Toggle selection state of a card (multi-select mode).
        
        Args:
            card_id: ID of card to toggle
            notify: If True, trigger selection change callbacks
            
        Returns:
            True if selection changed
        """
        if not self.multi_select:
            return self.select_card(card_id, notify)
        
        # In multi-select, always toggle
        return self.select_card(card_id, notify)
    
    def clear_selection(self, notify: bool = True) -> bool:
        """
        Clear all selections.
        
        Args:
            notify: If True, trigger selection change callbacks
            
        Returns:
            True if selection was cleared
        """
        if not self._selected_cards and self._selected_card_id is None:
            return False
        
        previous_id = self._selected_card_id
        self._selected_card_id = None
        self._selected_cards.clear()
        
        if notify:
            self._notify_selection_change(None, previous_id, "single")
        
        return True
    
    def is_selected(self, card_id: str) -> bool:
        """
        Check if card is currently selected.
        
        Args:
            card_id: Card ID to check
            
        Returns:
            True if card is selected
        """
        return card_id in self._selected_cards
    
    def get_selected_card_id(self) -> Optional[str]:
        """
        Get primary selected card ID.
        
        Returns:
            Selected card ID or None if no selection
        """
        return self._selected_card_id
    
    def get_selected_card(self) -> Optional[Card]:
        """
        Get primary selected card object.
        
        Returns:
            Selected Card object or None if no selection
        """
        if self._selected_card_id:
            return self._card_lookup.get(self._selected_card_id)
        return None
    
    def get_selected_card_ids(self) -> List[str]:
        """
        Get all selected card IDs.
        
        Returns:
            List of selected card IDs (empty if no selection)
        """
        return list(self._selected_cards)
    
    def get_selected_cards(self) -> List[Card]:
        """
        Get all selected card objects.
        
        Returns:
            List of selected Card objects
        """
        cards = []
        for card_id in self._selected_cards:
            card = self._card_lookup.get(card_id)
            if card:
                cards.append(card)
        return cards
    
    def get_selection_count(self) -> int:
        """
        Get number of selected cards.
        
        Returns:
            Number of currently selected cards
        """
        return len(self._selected_cards)
    
    def has_selection(self) -> bool:
        """
        Check if any cards are selected.
        
        Returns:
            True if at least one card is selected
        """
        return len(self._selected_cards) > 0
    
    def set_multi_select_mode(self, enabled: bool, clear_current: bool = True) -> None:
        """
        Enable or disable multi-selection mode.
        
        Args:
            enabled: If True, enable multi-selection
            clear_current: If True, clear current selection when changing modes
        """
        if self.multi_select == enabled:
            return
        
        self.multi_select = enabled
        
        if clear_current:
            self.clear_selection()
        elif not enabled and len(self._selected_cards) > 1:
            # Keep only the primary selection in single-select mode
            primary_id = self._selected_card_id
            self._selected_cards.clear()
            if primary_id:
                self._selected_cards.add(primary_id)
    
    def _notify_selection_change(self, card_id: Optional[str], previous_id: Optional[str], selection_type: str) -> None:
        """
        Notify all registered callbacks of selection change.
        
        Args:
            card_id: Newly selected card ID
            previous_id: Previously selected card ID
            selection_type: Type of selection change
        """
        card = self._card_lookup.get(card_id) if card_id else None
        
        event = SelectionEvent(
            selected_card_id=card_id,
            selected_card=card,
            previous_card_id=previous_id,
            selection_type=selection_type
        )
        
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                # Log error but don't let callback failures break selection
                import logging
                logger = logging.getLogger(__name__)
                logger.error("Selection callback error: %s", e)
    
    def get_state_summary(self) -> dict[str, str | int | bool]:
        """
        Get summary of current selection state for debugging.
        
        Returns:
            Dictionary with selection state information
        """
        return {
            'multi_select_mode': self.multi_select,
            'primary_selection': self._selected_card_id or "None",
            'total_selected': len(self._selected_cards),
            'selected_ids': list(self._selected_cards),
            'callback_count': len(self._callbacks)
        }