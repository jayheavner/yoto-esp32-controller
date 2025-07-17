"""Core data structures for the Yoto application.

Contains the fundamental data models used across different UI implementations.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DeviceState:
    """Realtime status values for a single Yoto player."""

    playback_status: str = "stopped"
    card_id: Optional[str] = None
    volume: int = 0
    battery: Optional[int] = None
    wifi_strength: Optional[int] = None
    temperature: Optional[float] = None
    ambient_light: Optional[int] = None


@dataclass
class Card:
    """Represents a Yoto audio card with artwork caching support."""
    id: str
    title: str
    art_path: Optional[Path] = None
