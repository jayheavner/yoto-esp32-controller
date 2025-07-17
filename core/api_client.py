import logging
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any
from datetime import time

import requests
from dotenv import load_dotenv
from yoto_api import YotoManager, YotoPlayerConfig
from yoto_api.exceptions import AuthenticationError, YotoException

from core.data_models import Card, DeviceState

load_dotenv()
logger = logging.getLogger(__name__)

# Alias exceptions for backward compatibility
YotoAPIError = YotoException
APIRequestError = YotoException


class YotoAPIClient:
    def _get_card_title(self, card_id: str) -> str:
        """Return the card title for a given card_id, or the card_id if not found."""
        card = self.library.get(card_id)
        if card and hasattr(card, 'title'):
            return card.title
        # Try to get from manager if not in local library
        if self.manager and hasattr(self.manager, 'library'):
            item = self.manager.library.get(card_id)
            if item and hasattr(item, 'title'):
                return item.title
        return card_id
    def play(self) -> None:
        """Start playback on the current device using yoto_api, with diagnostics."""
        if not self.manager:
            logger.error("YotoManager not initialized")
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        player = self.manager.players.get(device_id)
        if not player:
            logger.error("Device %s not found", device_id)
            return
        logger.info("Player state: online=%s, card_id=%s, is_playing=%s, playback_status=%s", player.online, player.card_id, getattr(player, 'is_playing', None), getattr(player, 'playback_status', None))
        if not player.online:
            logger.error("Device %s is not online", device_id)
            return
        if not player.card_id:
            logger.warning(
                "No card loaded in device %s. Cannot start playback", device_id
            )
            return
        try:
            # YotoPlayer objects only expose state. Use the manager to send the
            # actual play/resume command via MQTT.
            self.manager.resume_player(device_id)
            logger.info("Playback started on device %s", device_id)
        except Exception as exc:
            logger.error("Failed to start playback: %s", exc)

    def pause(self) -> None:
        """Pause playback on the current device using yoto_api."""
        if not self.manager:
            logger.error("YotoManager not initialized")
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        player = self.manager.players.get(device_id)
        if not player:
            logger.error("Device %s not found", device_id)
            return
        try:
            # YotoPlayer objects only expose state. Use the manager to send the
            # actual pause command via MQTT.
            assert self.manager is not None
            self.manager.pause_player(device_id)
            logger.info("Playback paused on device %s", device_id)
        except Exception as exc:
            logger.error("Failed to pause playback: %s", exc)

    def resume(self) -> None:
        """Resume playback on the current device using yoto_api."""
        if not self.manager:
            logger.error("YotoManager not initialized")
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        player = self.manager.players.get(device_id)
        if not player:
            logger.error("Device %s not found", device_id)
            return
        try:
            self.manager.resume_player(device_id)
            logger.info("Playback resumed on device %s", device_id)
        except Exception as exc:
            logger.error("Failed to resume playback: %s", exc)

    def stop(self) -> None:
        """Stop playback on the current device using yoto_api."""
        if not self.manager:
            logger.error("YotoManager not initialized")
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        player = self.manager.players.get(device_id)
        if not player:
            logger.error("Device %s not found", device_id)
            return
        try:
            self.manager.stop_player(device_id)
            logger.info("Playback stopped on device %s", device_id)
        except Exception as exc:
            logger.error("Failed to stop playback: %s", exc)

    def next_track(self) -> None:
        """Skip to the next chapter/track on the current device."""
        if not self.manager:
            logger.error("YotoManager not initialized")
            return

        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return

        player = self.manager.players.get(device_id)
        if not player:
            logger.error("Device %s not found", device_id)
            return

        if not player.card_id:
            logger.warning("No card loaded in device %s", device_id)
            return

        chapters = self.get_card_chapters(player.card_id) or []
        if not chapters:
            logger.info("No chapters available for card %s", player.card_id)
            return

        try:
            current = int(player.track_key or player.chapter_key or "1")
        except ValueError:
            current = 1

        if current >= len(chapters):
            logger.info("Already at last track")
            return

        next_key = current + 1
        logger.info("Playing chapter %02d of card %s", next_key, player.card_id)
        self.manager.play_card(
            device_id,
            player.card_id,
            secondsIn=0,
            cutoff=0,
            chapterKey=str(next_key).zfill(2),
            trackKey=next_key,
        )
        # Manually refresh state because the device may not emit an update
        if self.manager:
            self.manager.update_players_status()
        self._update_state_from_player()
        self._notify_state_change()

    def previous_track(self) -> None:
        """Skip to the previous chapter/track on the current device."""
        if not self.manager:
            logger.error("YotoManager not initialized")
            return

        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return

        player = self.manager.players.get(device_id)
        if not player:
            logger.error("Device %s not found", device_id)
            return

        if not player.card_id:
            logger.warning("No card loaded in device %s", device_id)
            return

        chapters = self.get_card_chapters(player.card_id) or []
        if not chapters:
            logger.info("No chapters available for card %s", player.card_id)
            return

        try:
            current = int(player.track_key or player.chapter_key or "1")
        except ValueError:
            current = 1

        if current <= 1:
            logger.info("Already at first track")
            return

        prev_key = current - 1
        logger.info("Playing chapter %02d of card %s", prev_key, player.card_id)
        self.manager.play_card(
            device_id,
            player.card_id,
            secondsIn=0,
            cutoff=0,
            chapterKey=str(prev_key).zfill(2),
            trackKey=prev_key,
        )
        # Manually refresh state because the device may not emit an update
        if self.manager:
            self.manager.update_players_status()
        self._update_state_from_player()
        self._notify_state_change()
    """Wrapper around ``yoto_api`` providing the old client interface."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent / "cache" / "art"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.manager: Optional[YotoManager] = None
        self.playback_status: str = "stopped"
        self.active_card_id: Optional[str] = None
        self.current_card_title: Optional[str] = None
        self.current_chapter_title: Optional[str] = None
        self.current_track_title: Optional[str] = None
        self.track_position: int = 0
        self.track_length: int = 0

        self.device_state = DeviceState()

        # Mirror key fields for backward compatibility
        self.device_state.playback_status = self.playback_status
        self.device_state.card_id = self.active_card_id
        self.device_state.volume = 0

        self.devices: Dict[str, Dict[str, Any]] = {}
        self.library: Dict[str, Card] = {}

        self._state_callbacks: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Callback management
    # ------------------------------------------------------------------
    def add_state_callback(self, callback: Callable[[], None]) -> None:
        self._state_callbacks.append(callback)

    def remove_state_callback(self, callback: Callable[[], None]) -> None:
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)

    def _notify_state_change(self) -> None:
        logger.debug(
            "Notifying %d callbacks: status=%s card=%s",
            len(self._state_callbacks),
            self.playback_status,
            self.active_card_id,
        )
        for callback in self._state_callbacks:
            try:
                callback()
            except Exception as exc:  # pragma: no cover - best effort
                logger.error("Error in state callback: %s", exc)

    # ------------------------------------------------------------------
    # Authentication / connection
    # ------------------------------------------------------------------
    def authenticate(self, username: str, password: str) -> bool:
        try:
            self.manager = YotoManager(username, password)
            self.manager.initialize()
            self._update_devices()
            self.manager.connect_to_events(self._on_event)
            self._update_state_from_player()
            return True
        except AuthenticationError as exc:
            logger.error("Authentication failed: %s", exc)
            return False
        except Exception as exc:  # pragma: no cover - network errors
            logger.error("Authentication error: %s", exc)
            return False

    # ------------------------------------------------------------------
    def _update_devices(self) -> None:
        if not self.manager:
            return
        self.devices.clear()
        for pid, player in self.manager.players.items():
            self.devices[pid] = {
                "name": player.name,
                "device_type": player.device_type,
                "online": player.online,
            }

    def _on_event(self) -> None:
        logger.debug("MQTT event received - updating state")
        self._update_state_from_player()
        self._notify_state_change()

    def _update_state_from_player(self) -> None:
        if not self.manager:
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        player = self.manager.players.get(device_id)
        if not player:
            logger.error("Device %s not found", device_id)
            return
        logger.debug(
            "Player update: online=%s status=%s is_playing=%s card=%s",
            player.online,
            player.playback_status,
            getattr(player, "is_playing", None),
            player.card_id,
        )

        self.playback_status = player.playback_status or (
            "playing" if player.is_playing else "stopped"
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

        # Update extended device state
        self.device_state.playback_status = self.playback_status
        self.device_state.card_id = self.active_card_id
        self.device_state.volume = player.user_volume or self.device_state.volume
        self.device_state.battery = player.battery_level_percentage
        self.device_state.wifi_strength = player.wifi_strength
        if player.temperature_celcius is not None:
            try:
                self.device_state.temperature = float(player.temperature_celcius)
            except (TypeError, ValueError):
                pass
        self.device_state.ambient_light = player.ambient_light_sensor_reading
        logger.debug(
            "Updated state: status=%s card=%s position=%s/%s",
            self.playback_status,
            self.active_card_id,
            self.track_position,
            self.track_length,
        )

    # ------------------------------------------------------------------
    @property
    def is_mqtt_connected(self) -> bool:
        return (
            self.manager is not None
            and self.manager.mqtt_client is not None
            and self.manager.mqtt_client.client is not None
            and self.manager.mqtt_client.client.is_connected()
        )

    # ------------------------------------------------------------------
    def get_library(self) -> List[Card]:
        if not self.manager:
            return []

        self.manager.update_library()
        cards: List[Card] = []
        self.library.clear()
        for cid, item in self.manager.library.items():
            card = Card(id=cid, title=item.title)
            art_path = self._get_or_download_artwork(cid, item.cover_image_large)
            if art_path:
                card.art_path = art_path
            cards.append(card)
            self.library[cid] = card
        logger.info("Loaded %d cards from library", len(cards))
        return cards

    def get_card_chapters(self, card_id: str) -> Optional[List[Dict[str, Any]]]:
        if not self.manager:
            return None
        self.manager.update_card_detail(card_id)
        card = self.manager.library.get(card_id)
        if not card or not card.chapters:
            return []
        chapters: List[Dict[str, Any]] = []
        for chap in card.chapters.values():
            chapters.append(
                {
                    "key": chap.key,
                    "title": chap.title,
                    "duration": chap.duration,
                    "iconUrl": chap.icon,
                }
            )
        return chapters

    # ------------------------------------------------------------------
    def refresh_state(self) -> None:
        """Manually refresh player state from the API."""
        if not self.manager:
            return
        self.manager.check_and_refresh_token()
        self.manager.update_players_status()
        self._update_state_from_player()

    # ------------------------------------------------------------------
    def set_max_volume(self, volume: int, night: bool = False) -> None:
        key = "night_max_volume_limit" if night else "day_max_volume_limit"
        self._apply_config(**{key: volume})

    def set_display_brightness(self, brightness: int, night: bool = False) -> None:
        key = "night_display_brightness" if night else "day_display_brightness"
        self._apply_config(**{key: brightness})

    def set_ambient_color(self, color: str, night: bool = False) -> None:
        key = "night_ambient_colour" if night else "day_ambient_colour"
        self._apply_config(**{key: color})

    def set_day_mode_time(self, time_str: str) -> None:
        self._apply_config(day_mode_time=time.fromisoformat(time_str))

    def set_night_mode_time(self, time_str: str) -> None:
        self._apply_config(night_mode_time=time.fromisoformat(time_str))

    def set_sleep_timer(self, seconds: int) -> None:
        if not self.manager:
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        self.manager.set_sleep(device_id, seconds)

    def enable_alarm(self, index: int) -> None:
        self._set_alarm_state(index, True)

    def disable_alarm(self, index: int) -> None:
        self._set_alarm_state(index, False)

    def _apply_config(self, **kwargs: Any) -> None:
        if not self.manager:
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        config = YotoPlayerConfig(**kwargs)
        self.manager.set_player_config(device_id, config)

    def _set_alarm_state(self, index: int, enabled: bool) -> None:
        if not self.manager:
            return
        device_id = os.getenv("YOTO_DEVICE_ID")
        if not device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        self.manager.update_players_status()
        player = self.manager.players.get(device_id)
        if not player or not player.config or not player.config.alarms:
            logger.error("Alarms not available for %s", device_id)
            return
        if index >= len(player.config.alarms):
            logger.error("Alarm %s out of range", index)
            return
        alarm = player.config.alarms[index]
        alarm.enabled = enabled
        config = YotoPlayerConfig(alarms=player.config.alarms)
        self.manager.set_player_config(device_id, config)

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

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self.manager:
            self.manager.disconnect()
            self.manager = None
        logger.info("YotoAPIClient closed")
