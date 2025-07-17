"""
UI logic package - portable across platforms.

Grid layout calculations, card selection management, display coordination,
and event handling. No UI framework dependencies.
"""
from .grid_layout import GridLayout, GridDimensions, GridPosition, ViewportInfo
from .card_selection import CardSelection, SelectionEvent
from .display_controller import DisplayController, DisplayState, DisplayInfo, UIBackend

__all__ = [
    'GridLayout',
    'GridDimensions', 
    'GridPosition',
    'ViewportInfo',
    'CardSelection',
    'SelectionEvent',
    'DisplayController',
    'DisplayState',
    'DisplayInfo',
    'UIBackend'
]