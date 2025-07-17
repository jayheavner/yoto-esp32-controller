import requests
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dotenv import load_dotenv
from core.data_models import Card
import paho.mqtt.client as mqtt
import threading
import time

load_dotenv()
logger = logging.getLogger(__name__)


class YotoAPIClient:
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.base_url = os.getenv("YOTO_API_BASE_URL", "https://api.yotoplay.com")
        self.client_id = os.getenv("YOTO_CLIENT_ID", "4P2do5RhHDXvCDZDZ6oti27Ft2XdRrzr")
        self.token_data: Optional[Dict[str, Any]] = None
        self.session = requests.Session()
        
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent / "cache" / "art"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # MQTT configuration (from yoto_mvp.py)
        self.mqtt_url = "aqrphjqbp3u2z-ats.iot.eu-west-2.amazonaws.com"
        self.mqtt_auth_name = "JwtAuthorizer_mGDDmvLsocFY"
        self.mqtt_client: Optional[mqtt.Client] = None
        self._mqtt_connected = False
        self._mqtt_retry_count = 0
        self._max_mqtt_retries = 5
        self._mqtt_retry_delay = 5  # seconds
        
        # State tracking
        self.playback_status: str = "stopped"  # "playing", "paused", "stopped"
        self.active_card_id: Optional[str] = None
        self.current_card_title: Optional[str] = None
        self.devices: Dict[str, Dict[str, Any]] = {}  # For backward compatibility

        # Library cache {card_id: Card}
        self.library: Dict[str, Card] = {}
        
        # State change callbacks
        self._state_callbacks: List[Callable[[], None]] = []
        
        # Start MQTT connection in background thread
        self._mqtt_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
    
    def add_state_callback(self, callback: Callable[[], None]) -> None:
        """Add callback to be called when playback state changes"""
        self._state_callbacks.append(callback)
    
    def remove_state_callback(self, callback: Callable[[], None]) -> None:
        """Remove state change callback"""
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)
    
    def _notify_state_change(self) -> None:
        """Notify all callbacks of state change"""
        for callback in self._state_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Error in state callback: {e}")
    
    def authenticate(self, username: str, password: str) -> bool:
        url = f"{self.base_url}/auth/token"
        data = {
            "audience": self.base_url,
            "client_id": self.client_id,
            "grant_type": "password",
            "password": password,
            "username": username,
            "scope": "openid email profile offline_access",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        try:
            response = self.session.post(url, data=data, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Login failed: {response.status_code} - {response.text}")
                return False
                
            self.token_data = response.json()
            expires_in = self.token_data.get('expires_in') if self.token_data else None
            logger.info(f"Authentication successful. Token expires in: {expires_in} seconds")
            
            # Start MQTT connection after successful authentication
            self._start_mqtt_connection()
            
            return True
            
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False
    
    def _get_auth_headers(self) -> Dict[str, str]:
        if not self.token_data:
            raise ValueError("Must authenticate before making authenticated requests")
            
        return {
            "Authorization": f"{self.token_data['token_type']} {self.token_data['access_token']}",
            "Content-Type": "application/json",
            "User-Agent": "Yoto/2.73 (com.yotoplay.Yoto; build:10405; iOS 17.4.0) Alamofire/5.6.4"
        }
    
    def _start_mqtt_connection(self) -> None:
        """Start MQTT connection in background thread"""
        if self._mqtt_thread and self._mqtt_thread.is_alive():
            return
            
        self._shutdown_event.clear()
        self._mqtt_thread = threading.Thread(target=self._mqtt_connection_loop, daemon=True)
        self._mqtt_thread.start()
    
    def _mqtt_connection_loop(self) -> None:
        """Background thread for MQTT connection with retry logic"""
        while not self._shutdown_event.is_set() and self._mqtt_retry_count < self._max_mqtt_retries:
            try:
                if self._connect_mqtt():
                    # Reset retry count on successful connection
                    self._mqtt_retry_count = 0
                    # Keep connection alive
                    while not self._shutdown_event.is_set() and self._mqtt_connected:
                        time.sleep(1)
                else:
                    self._mqtt_retry_count += 1
                    logger.warning(f"MQTT connection failed, retry {self._mqtt_retry_count}/{self._max_mqtt_retries}")
                    
                if not self._shutdown_event.is_set() and self._mqtt_retry_count < self._max_mqtt_retries:
                    self._shutdown_event.wait(self._mqtt_retry_delay)
                    
            except Exception as e:
                logger.error(f"MQTT connection error: {e}")
                self._mqtt_retry_count += 1
                if not self._shutdown_event.is_set() and self._mqtt_retry_count < self._max_mqtt_retries:
                    self._shutdown_event.wait(self._mqtt_retry_delay)
        
        if self._mqtt_retry_count >= self._max_mqtt_retries:
            logger.error("MQTT connection failed after maximum retries - entering graceful degradation")
    
    def _connect_mqtt(self) -> bool:
        """Connect to MQTT broker"""
        if not self.token_data:
            logger.error("Must be authenticated before connecting MQTT")
            return False
            
        try:
            # Get target device ID from environment
            target_device_id = os.getenv("YOTO_DEVICE_ID")
            if not target_device_id:
                logger.error("YOTO_DEVICE_ID environment variable not set")
                return False
            
            # Client ID must be unique and match pattern expected by AWS IoT
            client_id = "YOTOAPI" + target_device_id.replace("-", "")
            
            self.mqtt_client = mqtt.Client(
                client_id=client_id,
                transport="websockets"
            )
            
            # AWS IoT custom authorizer expects this specific username format
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
                logger.error(f"MQTT connect failed: {result}")
                return False
                
            self.mqtt_client.loop_start()
            
            # Wait for connection to establish
            for _ in range(10):  # 5 second timeout
                if self._mqtt_connected:
                    break
                time.sleep(0.5)
            
            return self._mqtt_connected
            
        except Exception as e:
            logger.error(f"MQTT connect error: {e}")
            return False
    
    def _load_devices_for_mqtt(self) -> None:
        """Load devices needed for MQTT connection"""
        try:
            url = f"{self.base_url}/device-v2/devices/mine"
            response = self.session.get(url, headers=self._get_auth_headers())
            
            if response.status_code != 200:
                logger.error(f"Get devices failed: {response.status_code} - {response.text}")
                return
                
            data = response.json()
            self.devices.clear()
            
            for device_data in data.get('devices', []):
                device_id = device_data.get('deviceId', '')
                self.devices[device_id] = {
                    'name': device_data.get('name', 'Unknown'),
                    'device_type': device_data.get('deviceType', 'unknown'),
                    'online': device_data.get('online', False)
                }
                
            logger.info(f"Loaded {len(self.devices)} devices for MQTT")
            
        except Exception as e:
            logger.error(f"Load devices error: {e}")
    
    def _on_mqtt_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        # reason_code is a ReasonCode object in v2, need to get the numeric value
        rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
        
        logger.info(f"MQTT connect result: {rc}")
        
        if rc != 0:
            logger.error(f"MQTT connection failed with code {rc}")
            self._mqtt_connected = False
            return
        
        self._mqtt_connected = True
        
        # Get target device ID from environment
        target_device_id = os.getenv("YOTO_DEVICE_ID")
        if not target_device_id:
            logger.error("YOTO_DEVICE_ID environment variable not set")
            return
        
        # Subscribe to target device topics only
        topics = [
            f"device/{target_device_id}/events",
            f"device/{target_device_id}/status", 
            f"device/{target_device_id}/response"
        ]
        for topic in topics:
            result = client.subscribe(topic)
            if result[0] != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to subscribe to {topic}: {result[0]}")
        
        # Request initial status from target device
        events_topic = f"device/{target_device_id}/command/events"
        result = client.publish(events_topic)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error(f"Failed to request status for {target_device_id}: {result.rc}")
    
    def _on_mqtt_disconnect(self, client, userdata, *args, **kwargs):
        """Handle MQTT disconnect with flexible arguments for version compatibility"""
        self._mqtt_connected = False
        
        # Handle different paho-mqtt versions with different callback signatures
        reason_code = None
        if len(args) >= 2:
            # v2 style: disconnect_flags, reason_code, properties=None
            reason_code = args[1]
        elif len(args) >= 1:
            # v1 style: just reason_code (or v1_rc)
            reason_code = args[0]
        
        # Extract numeric reason code
        if reason_code is not None:
            rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
            
            if rc == 0:
                logger.info("MQTT disconnected normally")
            else:
                logger.warning(f"MQTT disconnected unexpectedly with code {rc}")
                if rc == 7:
                    logger.error("MQTT authorization failed - check token validity")
        else:
            logger.info("MQTT disconnected")
    
    def _on_mqtt_message(self, client, userdata, message, properties=None):
        try:
            payload = message.payload.decode('utf-8')
            
            # Only process non-empty messages
            if not payload:
                return
                
            logger.debug(f"MQTT {message.topic}: {payload}")
            
            try:
                data = json.loads(payload)
                
                # Parse events messages for state changes
                if "/events" in message.topic and isinstance(data, dict):
                    old_status = self.playback_status
                    old_card_id = self.active_card_id
                    
                    # Update playback status
                    if "playbackStatus" in data:
                        self.playback_status = data["playbackStatus"]
                        logger.info(f"Playback status: {self.playback_status}")
                    
                    # Update active card
                    if "cardId" in data:
                        card_id = data["cardId"]
                        if card_id == "none":
                            self.active_card_id = None
                            self.current_card_title = None
                        else:
                            self.active_card_id = card_id
                            # Try to get card title from library if available
                            self.current_card_title = self._get_card_title(card_id)
                        logger.info(f"Active card: {self.active_card_id}")
                    
                    # Notify state change if anything changed
                    if (old_status != self.playback_status or 
                        old_card_id != self.active_card_id):
                        self._notify_state_change()
                
                # Handle any other unexpected states
                elif isinstance(data, dict) and "playbackStatus" in data:
                    status = data["playbackStatus"]
                    if status not in ["playing", "paused", "stopped"]:
                        logger.info(f"Unknown playback status for further scope: {status}")
                        
            except json.JSONDecodeError:
                pass
                
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
    
    def _get_card_title(self, card_id: str) -> Optional[str]:
        """Get card title from library cache or return card_id as fallback"""
        try:
            # Attempt lookup in cached library
            if card_id in self.library:
                return self.library[card_id].title

            # If library not yet loaded, try to load it
            if not self.library:
                self.get_library()
                if card_id in self.library:
                    return self.library[card_id].title

        except Exception as e:
            logger.error(f"Error retrieving title for card {card_id}: {e}")

        return f"Card {card_id}"
    
    @property
    def is_mqtt_connected(self) -> bool:
        """Check if MQTT is connected"""
        return (self.mqtt_client is not None and 
                self.mqtt_client.is_connected() and 
                self._mqtt_connected)
    
    def get_library(self) -> List[Card]:
        try:
            url = f"{self.base_url}/card/family/library"
            response = self.session.get(url, headers=self._get_auth_headers())
            
            if response.status_code != 200:
                logger.error(f"Get library failed: {response.status_code} - {response.text}")
                return []
                
            data = response.json()
            cards = []
            self.library.clear()
            
            for card_data in data.get('cards', []):
                card_info = card_data.get('card', {})
                card_id = card_data.get('cardId', '')
                title = card_info.get('title', 'Unknown')
                
                card = Card(id=card_id, title=title)
                
                # Set artwork path and download if needed
                art_path = self._get_or_download_artwork(card_id, card_info)
                if art_path:
                    card.art_path = art_path
                
                cards.append(card)
                self.library[card_id] = card
                
            logger.info(f"Loaded {len(cards)} cards from library")
            return cards
            
        except Exception as e:
            logger.error(f"Get library error: {e}")
            return []
    
    def get_card_chapters(self, card_id: str) -> Optional[List[Dict[str, Any]]]:
        try:
            url = f"{self.base_url}/card/details/{card_id}"
            response = self.session.get(url, headers=self._get_auth_headers())

            if response.status_code != 200:
                logger.error(f"Get card detail failed: {response.status_code} - {response.text}")
                return None

            detail = response.json()
            chapters = detail.get("card", {}).get("content", {}).get("chapters", [])

            if not chapters:
                logger.info(f"No chapters found for card {card_id}")
                return []

            logger.info(f"Found {len(chapters)} chapters for card {card_id}")
            return chapters

        except Exception as e:
            logger.error(f"Error retrieving chapters for card {card_id}: {e}")
            return None
    
    def _get_or_download_artwork(self, card_id: str, card_info: Dict[str, Any]) -> Optional[Path]:
        # Check if cached artwork exists with any extension
        existing_files = list(self.cache_dir.glob(f"{card_id}.*"))
        if existing_files:
            return existing_files[0]
        
        # Try to download artwork
        try:
            metadata = card_info.get('metadata', {})
            cover = metadata.get('cover', {})
            art_url = cover.get('imageL')
            
            if not art_url:
                logger.warning(f"No artwork URL found for card {card_id}")
                return None
            
            logger.info(f"Downloading artwork for card {card_id}")
            response = self.session.get(art_url, timeout=15)
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
                
            art_path = self.cache_dir / f"{card_id}{ext}"
            art_path.write_bytes(response.content)
            return art_path
            
        except Exception as e:
            logger.error(f"Failed to download artwork for {card_id}: {e}")
            return None
    
    def close(self) -> None:
        """Clean shutdown of client"""
        # Signal shutdown
        self._shutdown_event.set()
        
        # Disconnect MQTT
        if self.mqtt_client:
            self._mqtt_connected = False
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
            except Exception as e:
                logger.error(f"Error during MQTT disconnect: {e}")
            self.mqtt_client = None
        
        # Wait for thread to finish
        if self._mqtt_thread and self._mqtt_thread.is_alive():
            self._mqtt_thread.join(timeout=5)
        
        # Close HTTP session
        self.session.close()
        
        logger.info("YotoAPIClient closed")