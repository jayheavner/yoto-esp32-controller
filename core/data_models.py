"""Core data structures for the Yoto application.

Contains the fundamental data models used across different UI implementations.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Card:
    """Represents a Yoto audio card with artwork caching support."""
    id: str
    title: str
    art_path: Optional[Path] = None