"""
Phase 1 Test - MQTT State Tracking Verification
Run this to verify MQTT connection and state tracking works
"""
import os
import time
import logging
from dotenv import load_dotenv
from core.api_client import YotoAPIClient

# Setup logging to see MQTT messages
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_mqtt_state_tracking():
    load_dotenv()
    
    username = os.getenv("YOTO_USERNAME")
    password = os.getenv("YOTO_PASSWORD")
    
    if not (username and password):
        print("ERROR: Set YOTO_USERNAME and YOTO_PASSWORD in .env file")
        return False
    
    print("=== Phase 1 Test: MQTT State Tracking ===")
    
    # Create client
    client = YotoAPIClient()
    
    # Add state callback to verify it works
    def on_state_change():
        print(f"STATE CHANGE - Status: {client.playback_status}, Card: {client.active_card_id}")
    
    client.add_state_callback(on_state_change)
    
    try:
        # Authenticate
        print("1. Authenticating...")
        if not client.authenticate(username, password):
            print("ERROR: Authentication failed")
            return False
        print("✓ Authentication successful")
        
        # Wait for MQTT connection
        print("2. Waiting for MQTT connection...")
        for i in range(30):  # 30 second timeout
            if client.is_mqtt_connected:
                print("✓ MQTT connected successfully")
                break
            time.sleep(1)
            if i % 5 == 0:
                print(f"  Waiting for MQTT... ({i}/30)")
        else:
            print("WARNING: MQTT connection timeout - may still work")
        
        # Show initial state
        print("3. Initial state:")
        print(f"   Playback Status: {client.playback_status}")
        print(f"   Active Card: {client.active_card_id}")
        print(f"   MQTT Connected: {client.is_mqtt_connected}")
        
        # Monitor for state changes
        print("4. Monitoring for state changes (30 seconds)...")
        print("   Try playing/pausing content on your Yoto device now...")
        
        start_time = time.time()
        last_status = client.playback_status
        last_card = client.active_card_id
        
        while time.time() - start_time < 30:
            current_status = client.playback_status
            current_card = client.active_card_id
            
            if current_status != last_status or current_card != last_card:
                print(f"   DETECTED CHANGE: {current_status}, Card: {current_card}")
                last_status = current_status
                last_card = current_card
            
            time.sleep(1)
        
        print("5. Final state:")
        print(f"   Playback Status: {client.playback_status}")
        print(f"   Active Card: {client.active_card_id}")
        print(f"   Current Card Title: {client.current_card_title}")
        
        print("✓ Phase 1 test completed successfully")
        return True
        
    except Exception as e:
        print(f"ERROR: {e}")
        return False
        
    finally:
        client.close()

if __name__ == "__main__":
    success = test_mqtt_state_tracking()
    if success:
        print("\n🎉 Phase 1 PASSED - Ready for Phase 2")
    else:
        print("\n❌ Phase 1 FAILED - Fix issues before proceeding")