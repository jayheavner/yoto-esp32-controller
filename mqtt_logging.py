#!/usr/bin/env python3
"""Debug script to check MQTT state tracking"""

import os
import time
import logging
from dotenv import load_dotenv
from desktop_ui.coordinator import DesktopCoordinator

load_dotenv()

# Enable debug logging
logging.basicConfig(level=logging.DEBUG, 
                   format='%(asctime)s %(name)s %(levelname)s: %(message)s')

def main():
    print("=== MQTT State Debug ===")
    
    coordinator = DesktopCoordinator()
    
    # Check if API client was created
    if not coordinator.api_client:
        print("❌ API client not created")
        return
    else:
        print("✓ API client created")
    
    # Check authentication
    if not coordinator._is_authenticated:
        print("❌ Not authenticated")
        return
    else:
        print("✓ Authenticated")
    
    # Check MQTT connection
    if coordinator.api_client.is_mqtt_connected:
        print("✓ MQTT connected")
    else:
        print("❌ MQTT not connected")
        print(f"  MQTT client exists: {coordinator.api_client.mqtt_client is not None}")
        print(f"  Internal connected flag: {coordinator.api_client._mqtt_connected}")
        if coordinator.api_client.mqtt_client:
            print(f"  Paho connected: {coordinator.api_client.mqtt_client.is_connected()}")
    
    # Print current state
    print(f"\n=== Current State ===")
    print(f"Playback Status: {coordinator.playbackStatus}")
    print(f"Is Playing: {coordinator.isPlaying}")
    print(f"Show Now Playing: {coordinator.showNowPlaying}")
    print(f"Active Card ID: {coordinator.activeCardId}")
    print(f"Current Card Title: {coordinator.currentCardTitle}")
    
    print(f"\n=== Raw API Client State ===")
    if coordinator.api_client:
        print(f"Raw playback_status: {coordinator.api_client.playback_status}")
        print(f"Raw active_card_id: {coordinator.api_client.active_card_id}")
        print(f"Raw current_card_title: {coordinator.api_client.current_card_title}")
    
    # Monitor for changes
    print(f"\n=== Monitoring for 30 seconds ===")
    print("Try changing playback state on your device now...")
    
    def state_change_callback():
        print(f"🔄 State changed!")
        print(f"  Playback: {coordinator.playbackStatus}")
        print(f"  Is Playing: {coordinator.isPlaying}")
        print(f"  Active Card: {coordinator.activeCardId}")
    
    # Add callback
    if coordinator.api_client:
        coordinator.api_client.add_state_callback(state_change_callback)
    
    # Wait and monitor
    for i in range(30):
        time.sleep(1)
        if i % 5 == 0:
            print(f"⏱️ {30-i} seconds remaining...")
    
    coordinator.cleanup()
    print("Debug complete")

if __name__ == "__main__":
    main()