"""yotobackend/client.py – Self-contained Yoto Client

A complete async client for Yoto API that handles authentication, device management,
library browsing, and real-time device control via MQTT.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Dict, List, Optional, Any
import requests
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)
CACHE = Path(__file__).resolve().parent.parent / "art_cache"
CACHE.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DeviceState:
    """Track per-device state information."""

    playback_status: Literal["playing", "paused", "stopped"] = "stopped"
    card_id: str | None = None
    volume: int = 8
    battery: int | None = None
    wifi_strength: int | None = None
    temperature: float | None = None
    ambient_light: int | None = None

# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Card:
    id: str
    title: str
    art_path: Path = field(init=False)

    def __post_init__(self):
        self.art_path = CACHE / f"{self.id}"

# ---------------------------------------------------------------------------
@dataclass
class YotoDevice:
    id: str
    name: str
    device_type: str
    online: bool
    
    def __str__(self) -> str:
        status = "Online" if self.online else "Offline"
        return f"{self.name} ({self.device_type}) - {status}"

# ---------------------------------------------------------------------------
StateCb = Callable[[DeviceState], Awaitable[None] | None]

# ---------------------------------------------------------------------------
class YotoClient:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        
        # Yoto API configuration
        self.base_url = "https://api.yotoplay.com"
        self.client_id = "4P2do5RhHDXvCDZDZ6oti27Ft2XdRrzr"
        self.token_data: Optional[Dict[str, Any]] = None
        self.token_expires_at: float = 0.0
        self.devices: Dict[str, YotoDevice] = {}
        self.device_state: Dict[str, DeviceState] = {}
        self.library: Dict[str, Card] = {}
        
        # MQTT configuration
        self.mqtt_url = "aqrphjqbp3u2z-ats.iot.eu-west-2.amazonaws.com"
        self.mqtt_auth_name = "JwtAuthorizer_mGDDmvLsocFY"
        self.mqtt_client: Optional[mqtt.Client] = None
        self._mqtt_connected = False
        
        # Device selection
        self.device_id: str | None = None

        self._refresh_task: Optional[asyncio.Task] = None
        self._state_task: Optional[asyncio.Task] = None
        self._callbacks: list[StateCb] = []

    async def start(self) -> None:
        logger.info("Starting Yoto client...")
        await self._authenticate()
        await self._load_devices()
        await self._select_device()
        await self._connect_mqtt()
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._state_task = asyncio.create_task(self._state_loop())
        logger.info("Yoto client started successfully")

    async def stop(self) -> None:
        logger.info("Stopping Yoto client...")
        if self._refresh_task:
            self._refresh_task.cancel()
            with contextlib.suppress(Exception):
                await self._refresh_task
        if self._state_task:
            self._state_task.cancel()
            with contextlib.suppress(Exception):
                await self._state_task
        await self._disconnect_mqtt()
        self.session.close()
        logger.info("Yoto client stopped")

    async def _authenticate(self) -> None:
        logger.info("Authenticating with Yoto API...")
        url = f"{self.base_url}/auth/token"
        data = {
            "audience": self.base_url,
            "client_id": self.client_id,
            "grant_type": "password",
            "password": self.password,
            "username": self.email,
            "scope": "openid email profile offline_access",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        response = await asyncio.to_thread(
            self.session.post, url, data=data, headers=headers
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"Login failed: {response.status_code} - {response.text}")
            
        self.token_data = response.json()
        expires_in = self.token_data.get("expires_in", 0)
        self.token_expires_at = time.monotonic() + float(expires_in)
        logger.info("Authentication successful. Token expires in: %s seconds", expires_in)

    def _get_auth_headers(self) -> Dict[str, str]:
        if not self.token_data:
            raise ValueError("Must authenticate before making authenticated requests")
            
        return {
            "Authorization": f"{self.token_data['token_type']} {self.token_data['access_token']}",
            "Content-Type": "application/json",
            "User-Agent": "Yoto/2.73 (com.yotoplay.Yoto; build:10405; iOS 17.4.0) Alamofire/5.6.4"
        }

    async def _load_devices(self) -> None:
        logger.info("Loading devices...")
        url = f"{self.base_url}/device-v2/devices/mine"
        headers = self._get_auth_headers()
        
        response = await asyncio.to_thread(self.session.get, url, headers=headers)
        
        if response.status_code != 200:
            raise RuntimeError(f"Get devices failed: {response.status_code} - {response.text}")
            
        data = response.json()
        self.devices.clear()
        
        for device_data in data.get('devices', []):
            device = YotoDevice(
                id=device_data.get('deviceId', ''),
                name=device_data.get('name', 'Unknown'),
                device_type=device_data.get('deviceType', 'unknown'),
                online=device_data.get('online', False)
            )
            self.devices[device.id] = device
            
        logger.info("Loaded %d devices", len(self.devices))

    async def _select_device(self) -> None:
        online_devices = [device for device in self.devices.values() if device.online]
        if online_devices:
            self.device_id = online_devices[0].id
            logger.info("Selected online device: %s (%s)", online_devices[0].name, online_devices[0].id)
        elif self.devices:
            device = next(iter(self.devices.values()))
            self.device_id = device.id
            logger.warning("No online devices, using offline device: %s (%s)", device.name, device.id)
        else:
            raise RuntimeError("No devices available")

    @property
    def is_mqtt_connected(self) -> bool:
        return (self.mqtt_client is not None and 
                self.mqtt_client.is_connected() and 
                self._mqtt_connected)

    async def _connect_mqtt(self) -> None:
        if not self.token_data:
            raise RuntimeError("Must be authenticated before connecting MQTT")
            
        if not self.devices:
            raise RuntimeError("No devices available for MQTT connection")
        
        logger.info("Connecting to MQTT...")
        
        # Client ID must be unique and match pattern expected by AWS IoT
        first_device = next(iter(self.devices.keys()))
        client_id = "YOTOAPI" + first_device.replace("-", "")
        
        def create_and_connect():
            self.mqtt_client = mqtt.Client(
                client_id=client_id,
                transport="websockets"
            )
            
            # AWS IoT custom authorizer expects this specific username format
            if not self.token_data or 'access_token' not in self.token_data:
                raise RuntimeError("Authentication token missing. Please authenticate first.")
            self.mqtt_client.username_pw_set(
                username=f"_?x-amz-customauthorizer-name={self.mqtt_auth_name}",
                password=self.token_data['access_token']
            )
            
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            self.mqtt_client.on_message = self._on_mqtt_message
            
            self.mqtt_client.tls_set()
            
            result = self.mqtt_client.connect(host=self.mqtt_url, port=443, keepalive=60)
            if result != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT connect failed: {result}")
                
            self.mqtt_client.loop_start()
            return result
        
        await asyncio.to_thread(create_and_connect)
        
        # Wait for connection to establish
        for _ in range(10):  # 5 second timeout
            if self.is_mqtt_connected:
                break
            await asyncio.sleep(0.5)
        
        if not self.is_mqtt_connected:
            raise RuntimeError("MQTT connection failed to establish")
            
        logger.info("MQTT connection established")

    async def _disconnect_mqtt(self) -> None:
        if self.mqtt_client:
            def disconnect():
                self._mqtt_connected = False
                if self.mqtt_client is not None:
                    self.mqtt_client.loop_stop()
                    try:
                        self.mqtt_client.disconnect()
                    except Exception as e:
                        logger.error("Error during MQTT disconnect: %s", e)
                    self.mqtt_client = None
                
            await asyncio.to_thread(disconnect)
            logger.info("MQTT disconnected")

    def _on_mqtt_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        # reason_code is a ReasonCode object in v2, need to get the numeric value
        rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
        
        logger.info("MQTT connect result: %s", rc)
        
        if rc != 0:
            logger.error("MQTT connection failed with code %s", rc)
            self._mqtt_connected = False
            return
        
        self._mqtt_connected = True
        
        # Subscribe to all device topics
        for device_id in self.devices.keys():
            topics = [
                f"device/{device_id}/events",
                f"device/{device_id}/status", 
                f"device/{device_id}/response"
            ]
            for topic in topics:
                result = client.subscribe(topic)
                if result[0] != mqtt.MQTT_ERR_SUCCESS:
                    logger.error("Failed to subscribe to %s: %s", topic, result[0])
        
        # Request initial status from all devices
        for device_id in self.devices.keys():
            events_topic = f"device/{device_id}/command/events"
            result = client.publish(events_topic)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error("Failed to request status for %s: %s", device_id, result.rc)

    def _on_mqtt_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self._mqtt_connected = False
        
        # reason_code is a ReasonCode object in v2, need to get the numeric value
        rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
        
        if rc == 0:
            logger.info("MQTT disconnected normally")
        else:
            logger.warning("MQTT disconnected unexpectedly with code %s", rc)
            if rc == 7:
                logger.error("MQTT authorization failed - check token validity")

    def _on_mqtt_message(self, client, userdata, message, properties=None):
        try:
            payload = message.payload.decode('utf-8')
            
            # Only log non-empty messages to reduce noise
            if payload:
                logger.info("MQTT %s: %s", message.topic, payload)
                
                try:
                    data = json.loads(payload)
                    # Log important state changes
                    if "playbackStatus" in data:
                        logger.info("Playback: %s", data['playbackStatus'])
                    if "cardId" in data and data["cardId"] != "none":
                        logger.info("Active card: %s", data['cardId'])
                except json.JSONDecodeError:
                    pass
                    
        except Exception as e:
            logger.error("Error processing MQTT message: %s", e)

    async def _publish_device_command(self, device_id: str, command: str, payload: Optional[str] = None) -> bool:
        """Centralized device command publishing with validation"""
        if not self.is_mqtt_connected:
            logger.error("MQTT not connected")
            return False
            
        if device_id not in self.devices:
            logger.error("Device %s not found", device_id)
            return False
        
        topic = f"device/{device_id}/command/{command}"
        
        def publish():
            if self.mqtt_client is None:
                raise RuntimeError("MQTT client is not initialized")
            result = self.mqtt_client.publish(topic, payload or "")
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT publish failed for {command}: {result.rc}")
            return True
        
        try:
            await asyncio.to_thread(publish)
            return True
        except Exception as e:
            logger.error("Failed to publish command %s: %s", command, e)
            return False

    async def fetch_raw_library(self) -> list[dict]:
        logger.info("Fetching library from Yoto API...")
        url = f"{self.base_url}/card/family/library"
        headers = self._get_auth_headers()
        resp = await asyncio.to_thread(self.session.get, url, headers=headers, timeout=20)
        resp.raise_for_status()
        cards_data = resp.json().get("cards", [])
        logger.info("Fetched %d cards from library", len(cards_data))
        return cards_data

    def _extract_art_url(self, card_data: dict) -> str:
        return card_data["metadata"]["cover"]["imageL"]

    async def build_card_list(self, raw_library: list[dict]) -> list[Card]:
        logger.info("Building card list from %d library entries...", len(raw_library))
        cards: list[Card] = []
        cached_count = 0
        
        for entry in raw_library:
            card_id = entry.get("cardId", "unknown")
            card_data = entry.get("card", {})
            title = card_data.get("title", "Unknown")
            
            card = Card(id=card_id, title=title)
            
            # Check if cached artwork exists with any extension
            existing_files = list(CACHE.glob(f"{card.id}.*"))
            if existing_files:
                card.art_path = existing_files[0]
                cached_count += 1
                
            cards.append(card)
            
        logger.info("Built %d cards (%d with cached artwork, %d need download)", 
                   len(cards), cached_count, len(cards) - cached_count)
        return cards

    async def download_missing_art(self, cards: list[Card], raw_library: list[dict]) -> None:
        card_data_lookup = {entry.get("cardId"): entry.get("card", {}) for entry in raw_library}
        download_count = 0
        
        for card in cards:
            # Check if any cached file exists for this card ID
            existing_files = list(CACHE.glob(f"{card.id}.*"))
            if existing_files:
                card.art_path = existing_files[0]
                continue
                
            card_data = card_data_lookup.get(card.id, {})
            if not card_data or "metadata" not in card_data:
                continue
                
            try:
                art_url = self._extract_art_url(card_data)
            except KeyError:
                logger.warning("No artwork URL found for card %s", card.id)
                continue
            
            logger.info("Downloading artwork for '%s' (%s)", card.title, card.id)
            response = await asyncio.to_thread(self.session.get, art_url, timeout=15)
            response.raise_for_status()
            
            # Detect file extension from Content-Type
            content_type = response.headers.get('content-type', '').lower()
            if 'jpeg' in content_type or 'jpg' in content_type:
                ext = '.jpg'
            elif 'png' in content_type:
                ext = '.png'
            elif 'webp' in content_type:
                ext = '.webp'
            else:
                ext = '.jpg'
                
            card.art_path = CACHE / f"{card.id}{ext}"
            await asyncio.to_thread(card.art_path.write_bytes, response.content)
            download_count += 1
            
        if download_count > 0:
            logger.info("Downloaded %d artwork files", download_count)
        else:
            logger.info("All artwork already cached")

    async def get_library(self) -> list[Card]:
        raw_library = await self.fetch_raw_library()
        cards = await self.build_card_list(raw_library)
        await self.download_missing_art(cards, raw_library)
        
        # Update internal library for device commands
        self.library.clear()
        for card in cards:
            self.library[card.id] = card
            
        return cards

    def _ensure_device(self) -> str:
        if not self.device_id:
            raise RuntimeError("No device selected")
        return self.device_id

    async def play(self, card_id: str) -> None:
        device_id = self._ensure_device()
        
        if card_id not in self.library:
            logger.error("Card %s not found in library", card_id)
            raise ValueError(f"Card {card_id} not found")
        
        payload = json.dumps({
            "uri": f"https://yoto.io/{card_id}",
            "chapterKey": "01",
            "trackKey": "01", 
            "secondsIn": 0,
            "cutOff": 0
        })
        
        logger.info("Playing card %s on device %s", card_id, device_id)
        success = await self._publish_device_command(device_id, "card-play", payload)
        
        if success:
            device_name = self.devices[device_id].name
            card_title = self.library[card_id].title
            logger.info("Requested play '%s' on %s", card_title, device_name)
        else:
            raise RuntimeError("Failed to send play command")

    async def pause(self) -> None:
        device_id = self._ensure_device()
        logger.info("Pausing playback on device %s", device_id)
        success = await self._publish_device_command(device_id, "card-pause")
        if not success:
            raise RuntimeError("Failed to send pause command")

    async def resume(self) -> None:
        device_id = self._ensure_device()
        logger.info("Resuming playback on device %s", device_id)
        success = await self._publish_device_command(device_id, "card-resume")
        if not success:
            raise RuntimeError("Failed to send resume command")

    async def stop_playback(self) -> None:
        device_id = self._ensure_device()
        logger.info("Stopping playback on device %s", device_id)
        success = await self._publish_device_command(device_id, "card-stop")
        if not success:
            raise RuntimeError("Failed to send stop command")

    def subscribe(self, callback: StateCb) -> None:
        """Subscribe to state change notifications."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------
    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self._refresh_token_if_needed()

    async def _state_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.refresh_device_state()

    async def _refresh_token_if_needed(self) -> None:
        if time.monotonic() > self.token_expires_at - 60:
            await self._refresh_token()

    async def _refresh_token(self) -> None:
        if not self.token_data or "refresh_token" not in self.token_data:
            return
        url = f"{self.base_url}/auth/token"
        data = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.token_data["refresh_token"],
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = await asyncio.to_thread(self.session.post, url, data=data, headers=headers)
        if resp.status_code == 200:
            new_data = resp.json()
            self.token_data.update(new_data)
            expires_in = new_data.get("expires_in", 0)
            self.token_expires_at = time.monotonic() + float(expires_in)
            logger.info("Refreshed access token")
        else:
            logger.error("Failed to refresh token: %s - %s", resp.status_code, resp.text)

    # ------------------------------------------------------------------
    # Device state handling
    # ------------------------------------------------------------------
    async def refresh_device_state(self) -> None:
        device_id = self._ensure_device()
        url = f"{self.base_url}/device-v2/{device_id}/status"
        headers = self._get_auth_headers()
        resp = await asyncio.to_thread(self.session.get, url, headers=headers)
        if resp.status_code != 200:
            logger.error("Failed to fetch device status: %s", resp.text)
            return
        data = resp.json()
        state = self.device_state.setdefault(device_id, DeviceState())
        state.playback_status = data.get("playbackStatus", state.playback_status)
        card = data.get("cardId")
        state.card_id = None if card == "none" else card or state.card_id
        state.volume = data.get("userVolumePercentage", state.volume)
        state.battery = data.get("batteryLevelPercentage")
        state.wifi_strength = data.get("wifiStrength")
        if data.get("temperatureCelcius") is not None:
            try:
                state.temperature = float(data.get("temperatureCelcius"))
            except (TypeError, ValueError):
                state.temperature = state.temperature
        state.ambient_light = data.get("ambientLightSensorReading")
        for cb in list(self._callbacks):
            try:
                result = cb(state)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # pragma: no cover
                logger.error("Callback error: %s", exc)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    async def _update_player_config(self, config: Dict[str, Any]) -> None:
        device_id = self._ensure_device()
        url = f"{self.base_url}/device-v2/{device_id}/config"
        payload = json.dumps({"deviceId": device_id, "config": config})
        headers = self._get_auth_headers()
        resp = await asyncio.to_thread(self.session.put, url, headers=headers, data=payload)
        if resp.status_code != 200:
            logger.error("Failed to update config: %s", resp.text)

    async def set_max_volume(self, volume: int, night: bool = False) -> None:
        key = "nightMaxVolumeLimit" if night else "maxVolumeLimit"
        await self._update_player_config({key: str(volume)})

    async def set_brightness(self, brightness: int, night: bool = False) -> None:
        key = "nightDisplayBrightness" if night else "dayDisplayBrightness"
        await self._update_player_config({key: str(brightness)})

    async def set_ambient_color(self, color: str, night: bool = False) -> None:
        key = "nightAmbientColour" if night else "ambientColour"
        await self._update_player_config({key: color})

    async def set_day_mode_time(self, time_str: str) -> None:
        await self._update_player_config({"dayTime": time_str})

    async def set_night_mode_time(self, time_str: str) -> None:
        await self._update_player_config({"nightTime": time_str})

    async def set_sleep_timer(self, seconds: int) -> None:
        device_id = self._ensure_device()
        payload = json.dumps({"seconds": seconds})
        await self._publish_device_command(device_id, "sleep", payload)

    async def enable_alarm(self, index: int) -> None:
        await self._set_alarm_state(index, True)

    async def disable_alarm(self, index: int) -> None:
        await self._set_alarm_state(index, False)

    async def _get_player_config(self) -> Dict[str, Any]:
        device_id = self._ensure_device()
        url = f"{self.base_url}/device-v2/{device_id}/config"
        headers = self._get_auth_headers()
        resp = await asyncio.to_thread(self.session.get, url, headers=headers)
        if resp.status_code != 200:
            logger.error("Failed to fetch config: %s", resp.text)
            return {}
        return resp.json().get("config", {})

    async def _set_alarm_state(self, index: int, enabled: bool) -> None:
        config = await self._get_player_config()
        alarms = config.get("alarms", [])
        if index >= len(alarms):
            logger.error("Alarm %s not found", index)
            return
        parts = alarms[index].split(",")
        if len(parts) >= 6:
            parts[-1] = "1" if enabled else "0"
            alarms[index] = ",".join(parts)
            await self._update_player_config({"alarms": alarms})


