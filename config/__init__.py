"""
Configuration package for platform-specific settings.

Provides abstract configuration interface and platform-specific implementations
for desktop and future ESP32 deployments.
"""
from .base import BaseConfiguration, ConfigurationError
from .desktop import DesktopConfiguration

__all__ = [
    'BaseConfiguration',
    'ConfigurationError', 
    'DesktopConfiguration'
]