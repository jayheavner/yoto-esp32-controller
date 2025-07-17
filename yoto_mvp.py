"""
Yoto API MVP - Direct Implementation

SCOPE & PURPOSE:
This MVP bypasses the broken yoto-api Python library (v1.26.1) which has Token initialization 
issues with the 'expires_in' field returned by Yoto's auth API. Instead of monkey-patching 
the library, this implements direct HTTP calls for REST operations and MQTT for device control.

WHAT IT PROVIDES:
- Authentication with Yoto's OAuth2 API
- Device discovery and status checking  
- Card library browsing
- Real-time device control via AWS IoT MQTT (play, pause, resume, stop)
- Complete demo workflow showing end-to-end functionality

ARCHITECTURE:
- REST API: Direct requests.post() calls for auth, devices, and library data
- MQTT: AWS IoT WebSocket connection using custom JWT authorizer for device commands
- State Management: Internal tracking of connection status and device/library data

LIMITATIONS:
- No volume control, seeking, or advanced playback features
- No card chapter/track navigation beyond defaults (chapter 01, track 01)
- No device status monitoring beyond connection events
- Error handling is basic - designed for demo purposes

DEPENDENCIES:
- requests: HTTP API calls
- paho-mqtt: AWS IoT MQTT communication  
- python-dotenv: Credential management

USAGE:
Set YOTO_USERNAME and YOTO_PASSWORD in .env file, then run main() for full demo.
For library usage, instantiate YotoAPI() and call methods in sequence:
login() -> load_devices() -> load_library() -> connect_mqtt() -> device_commands()
"""

import os
import sys
import time
import logging
import requests
import json
import paho.mqtt.client as mqtt
from paho.mqtt.reasoncodes import ReasonCode
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s:%(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Device:
    id: str
    name: str
    device_type: str
    online: bool
    
    def __str__(self) -> str:
        status = "Online" if self.online else "Offline"
        return f"{self.name} ({self.device_type}) - {status}"


@dataclass
class Card:
    id: str
    title: str
    author: Optional[str] = None
    description: Optional[str] = None
    
    def __str__(self) -> str:
        if self.author:
            return f"{self.title} by {self.author}"
        return self.title


class YotoAPI:
    def __init__(self):
        self.base_url = "https://api.yotoplay.com"
        self.client_id = "4P2do5RhHDXvCDZDZ6oti27Ft2XdRrzr"
        self.token_data: Optional[Dict[str, Any]] = None
        self.devices: Dict[str, Device] = {}
        self.library: Dict[str, Card] = {}
        
        # AWS IoT MQTT broker configuration
        self.mqtt_url = "aqrphjqbp3u2z-ats.iot.eu-west-2.amazonaws.com"
        self.mqtt_auth_name = "JwtAuthorizer_mGDDmvLsocFY"
        self.mqtt_client: Optional[mqtt.Client] = None
        self._mqtt_connected = False
        
    def login(self, username: str, password: str) -> bool:
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
            response = requests.post(url, data=data, headers=headers)
            
            if response.status_code != 200:
                logger.error(f"Login failed: {response.status_code} - {response.text}")
                return False
                
            self.token_data = response.json()
            expires_in = self.token_data.get('expires_in') if self.token_data else None
            logger.info(f"Login successful. Token expires in: {expires_in} seconds")
            return True
            
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False
    
    def _get_auth_headers(self) -> Dict[str, str]:
        if not self.token_data:
            raise ValueError("Must login before making authenticated requests")
            
        return {
            "Authorization": f"{self.token_data['token_type']} {self.token_data['access_token']}",
            "Content-Type": "application/json",
            "User-Agent": "Yoto/2.73 (com.yotoplay.Yoto; build:10405; iOS 17.4.0) Alamofire/5.6.4"
        }
    
    def load_devices(self) -> bool:
        try:
            url = f"{self.base_url}/device-v2/devices/mine"
            response = requests.get(url, headers=self._get_auth_headers())
            
            if response.status_code != 200:
                logger.error(f"Get devices failed: {response.status_code} - {response.text}")
                return False
                
            data = response.json()
            self.devices.clear()
            
            for device_data in data.get('devices', []):
                device = Device(
                    id=device_data.get('deviceId', ''),
                    name=device_data.get('name', 'Unknown'),
                    device_type=device_data.get('deviceType', 'unknown'),
                    online=device_data.get('online', False)
                )
                self.devices[device.id] = device
                
            logger.info(f"Loaded {len(self.devices)} devices")
            return True
            
        except Exception as e:
            logger.error(f"Load devices error: {e}")
            return False
    
    def load_library(self) -> bool:
        try:
            url = f"{self.base_url}/card/family/library"
            response = requests.get(url, headers=self._get_auth_headers())
            
            if response.status_code != 200:
                logger.error(f"Get library failed: {response.status_code} - {response.text}")
                return False
                
            data = response.json()
            self.library.clear()
            
            for card_data in data.get('cards', []):
                card_info = card_data.get('card', {})
                metadata = card_info.get('metadata', {})
                
                card = Card(
                    id=card_data.get('cardId', ''),
                    title=card_info.get('title', 'Unknown'),
                    author=metadata.get('author'),
                    description=metadata.get('description')
                )
                self.library[card.id] = card
                
            logger.info(f"Loaded {len(self.library)} cards")
            return True
            
        except Exception as e:
            logger.error(f"Load library error: {e}")
            return False


    def get_card_chapters(self, card_id: str) -> Optional[List[Dict[str, Any]]]:
        try:
            url = f"{self.base_url}/card/details/{card_id}"
            response = requests.get(url, headers=self._get_auth_headers())

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

    def get_online_devices(self) -> List[Device]:
        return [device for device in self.devices.values() if device.online]
    
    @property
    def is_mqtt_connected(self) -> bool:
        # Check both client state and our internal flag since paho can be unreliable
        return (self.mqtt_client is not None and 
                self.mqtt_client.is_connected() and 
                self._mqtt_connected)
    
    def connect_mqtt(self) -> bool:
        if not self.token_data:
            logger.error("Must be logged in before connecting MQTT")
            return False
            
        if not self.devices:
            logger.error("No devices available for MQTT connection")
            return False
        
        try:
            # Client ID must be unique and match pattern expected by AWS IoT
            first_device = next(iter(self.devices.keys()))
            client_id = "YOTOAPI" + first_device.replace("-", "")
            
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
            logger.info("MQTT connection initiated")
            return True
            
        except Exception as e:
            logger.error(f"MQTT connect error: {e}")
            return False
    
    def disconnect_mqtt(self):
        if self.mqtt_client:
            self._mqtt_connected = False
            self.mqtt_client.loop_stop()
            # There is a problem with the disconnect as it expects a ReasonCode object in v2 but no ReasonCode has worked.
            try:
                self.mqtt_client.disconnect()
            except Exception as e:
                logger.error(f"Error during MQTT disconnect: {e}")
            self.mqtt_client = None
            logger.info("MQTT disconnected")
    
    def _on_mqtt_connect(self, client, userdata, connect_flags, reason_code, properties=None):
        # reason_code is a ReasonCode object in v2, need to get the numeric value
        rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
        
        logger.info(f"MQTT connect result: {rc}")
        
        if rc != 0:
            logger.error(f"MQTT connection failed with code {rc}")
            self._mqtt_connected = False
            return
        
        self._mqtt_connected = True
        
        # Subscribe to all device topics first
        for device_id in self.devices.keys():
            topics = [
                f"device/{device_id}/events",
                f"device/{device_id}/status", 
                f"device/{device_id}/response"
            ]
            for topic in topics:
                result = client.subscribe(topic)
                if result[0] != mqtt.MQTT_ERR_SUCCESS:
                    logger.error(f"Failed to subscribe to {topic}: {result[0]}")
        
        # Request initial status from all devices - this may be required for auth
        for device_id in self.devices.keys():
            events_topic = f"device/{device_id}/command/events"
            result = client.publish(events_topic)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to request status for {device_id}: {result.rc}")
    
    def _on_mqtt_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self._mqtt_connected = False
        
        # reason_code is a ReasonCode object in v2, need to get the numeric value
        rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
        
        if rc == 0:
            logger.info("MQTT disconnected normally")
        else:
            logger.warning(f"MQTT disconnected unexpectedly with code {rc}")
            if rc == 7:
                logger.error("MQTT authorization failed - check token validity")
    
    def _on_mqtt_message(self, client, userdata, message, properties=None):
        try:
            payload = message.payload.decode('utf-8')
            
            # Only log non-empty messages to reduce noise
            if payload:
                logger.info(f"MQTT {message.topic}: {payload}")
                
                try:
                    data = json.loads(payload)
                    # Log important state changes
                    if "playbackStatus" in data:
                        logger.info(f"Playback: {data['playbackStatus']}")
                    if "cardId" in data and data["cardId"] != "none":
                        logger.info(f"Active card: {data['cardId']}")
                except json.JSONDecodeError:
                    pass
                    
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
    
    def _publish_device_command(self, device_id: str, command: str, payload: Optional[str] = None) -> bool:
        """Centralized device command publishing with validation"""
        if not self.is_mqtt_connected:
            logger.error("MQTT not connected")
            return False
            
        if device_id not in self.devices:
            logger.error(f"Device {device_id} not found")
            return False
        
        topic = f"device/{device_id}/command/{command}"
        if self.mqtt_client is None:
            logger.error("MQTT client is not initialized")
            return False
        result = self.mqtt_client.publish(topic, payload or "")
        
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error(f"MQTT publish failed for {command}: {result.rc}")
            return False
            
        return True
    
    def play_card(self, device_id: str, card_id: str) -> bool:
        if card_id not in self.library:
            logger.error(f"Card {card_id} not found")
            return False
        
        payload = json.dumps({
            "uri": f"https://yoto.io/{card_id}",
            "chapterKey": "01",
            "trackKey": "01", 
            "secondsIn": 0,
            "cutOff": 0
        })
        
        success = self._publish_device_command(device_id, "card-play", payload)
        
        if success:
            device_name = self.devices[device_id].name
            card_title = self.library[card_id].title
            logger.info(f"Requested play '{card_title}' on {device_name}")
            
        return success
    
    def pause_device(self, device_id: str) -> bool:
        success = self._publish_device_command(device_id, "card-pause")
        if success:
            logger.info(f"Requested pause on {self.devices[device_id].name}")
        return success
    
    def resume_device(self, device_id: str) -> bool:
        success = self._publish_device_command(device_id, "card-resume")
        if success:
            logger.info(f"Requested resume on {self.devices[device_id].name}")
        return success
    
    def stop_device(self, device_id: str) -> bool:
        success = self._publish_device_command(device_id, "card-stop")
        if success:
            logger.info(f"Requested stop on {self.devices[device_id].name}")
        return success


def main():
    username = os.getenv('YOTO_USERNAME')
    password = os.getenv('YOTO_PASSWORD')
    
    if not username or not password:
        print("Please set YOTO_USERNAME and YOTO_PASSWORD in your .env file")
        return
    
    api = YotoAPI()
    
    try:
        print("=== Logging in ===")
        if not api.login(username, password):
            print("Login failed")
            return
        
        print("\n=== Loading devices and library ===")
        if not api.load_devices() or not api.load_library():
            print("Failed to load data")
            return
        
        print(f"\n=== Devices ({len(api.devices)}) ===")
        online_devices = api.get_online_devices()
        for i, device in enumerate(api.devices.values(), 1):
            print(f"  {i}. {device}")
        
        print(f"\n=== Library (showing first 10 of {len(api.library)}) ===")
        for i, card in enumerate(list(api.library.values())[:10], 1):
            print(f"  {i}. {card}")
    
        for card in api.library.values():
            print(f"\nCard: {card.title}")
            chapters = api.get_card_chapters(card.id)
            if chapters:
                for chap in chapters:
                    print(f"  - {chap.get('key')} - {chap.get('title')}")
            else:
                print("  No chapter data available.")
        
        print("\n=== Online Devices ===")    
        if not online_devices:
            print("\nNo online devices found. Cannot demonstrate playback.")
            return
        
        print("\n=== Connecting MQTT ===")
        if not api.connect_mqtt():
            print("MQTT connection failed")
            return
        
        print("Waiting for MQTT to stabilize...")
        time.sleep(5)
        
        if not api.is_mqtt_connected:
            print("MQTT connection lost or rejected")
            return
        
        print("✓ MQTT connected")
        
        target_device = online_devices[0]
        first_card = next(iter(api.library.values()))
        
        print(f"\n=== Demo Playback ===")
        print(f"Target: {target_device}")
        print(f"Card: {first_card}")
        
        if api.play_card(target_device.id, first_card.id):
            print("✓ Play command sent")
            
            time.sleep(2)
            if api.pause_device(target_device.id):
                print("✓ Pause command sent")
                
                time.sleep(2)
                if api.resume_device(target_device.id):
                    print("✓ Resume command sent")
                    
                    time.sleep(2)
                    if api.stop_device(target_device.id):
                        print("✓ Stop command sent")
        
        print("\n=== Demo Complete ===")
        
    except Exception as e:
        logger.error(f"Demo error: {e}")
        import traceback
        print(f"Full traceback:\n{traceback.format_exc()}")
        
    finally:
        api.disconnect_mqtt()


if __name__ == "__main__":
    main()
