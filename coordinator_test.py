"""
Test coordinator properties and Qt signal integration
"""
import sys
import os
from PySide6.QtCore import QCoreApplication
from desktop_ui.coordinator import DesktopCoordinator

def test_coordinator_properties():
    app = QCoreApplication(sys.argv)
    
    print("=== Coordinator Properties Test ===")
    
    # Create coordinator
    coordinator = DesktopCoordinator()
    
    # Test initial state
    print("1. Initial state:")
    print(f"   playbackStatus: '{coordinator.playbackStatus}'")
    print(f"   activeCardId: '{coordinator.activeCardId}'")
    print(f"   currentCardTitle: '{coordinator.currentCardTitle}'")
    print(f"   isPlaying: {coordinator.isPlaying}")
    print(f"   isPaused: {coordinator.isPaused}")
    print(f"   hasActiveContent: {coordinator.hasActiveContent}")
    print(f"   showNowPlaying: {coordinator.showNowPlaying}")
    
    # Test signal connections
    print("2. Testing signal connections...")
    
    signal_received = {"playback": False, "card": False}
    
    def on_playback_changed():
        signal_received["playback"] = True
        print(f"   📡 playbackStateChanged signal received")
    
    def on_card_changed():
        signal_received["card"] = True
        print(f"   📡 activeCardChanged signal received")
    
    coordinator.playbackStateChanged.connect(on_playback_changed)
    coordinator.activeCardChanged.connect(on_card_changed)
    
    # Simulate state change by directly updating the API client
    print("3. Simulating state change...")
    coordinator.api_client.playback_status = "playing"
    coordinator.api_client.active_card_id = "test123"
    coordinator.api_client.current_card_title = "Test Card"
    
    # Trigger state change callback
    coordinator._on_state_change()
    
    # Process Qt events
    app.processEvents()
    
    # Check results
    print("4. After state change:")
    print(f"   playbackStatus: '{coordinator.playbackStatus}'")
    print(f"   activeCardId: '{coordinator.activeCardId}'")
    print(f"   currentCardTitle: '{coordinator.currentCardTitle}'")
    print(f"   isPlaying: {coordinator.isPlaying}")
    print(f"   isPaused: {coordinator.isPaused}")
    print(f"   hasActiveContent: {coordinator.hasActiveContent}")
    print(f"   showNowPlaying: {coordinator.showNowPlaying}")
    
    # Verify signals
    print("5. Signal verification:")
    print(f"   playbackStateChanged fired: {signal_received['playback']}")
    print(f"   activeCardChanged fired: {signal_received['card']}")
    
    # Test navigateToNowPlaying slot
    print("6. Testing navigateToNowPlaying slot...")
    coordinator.navigateToNowPlaying()
    
    # Cleanup
    coordinator.cleanup()
    
    # Results
    success = (
        coordinator.playbackStatus == "playing" and
        coordinator.activeCardId == "test123" and
        coordinator.currentCardTitle == "Test Card" and
        coordinator.isPlaying and
        coordinator.hasActiveContent and
        coordinator.showNowPlaying and
        signal_received["playback"] and
        signal_received["card"]
    )
    
    if success:
        print("✅ Coordinator test PASSED")
    else:
        print("❌ Coordinator test FAILED")
    
    return success

if __name__ == "__main__":
    success = test_coordinator_properties()
    if success:
        print("\n🎉 Coordinator properties working correctly")
    else:
        print("\n❌ Coordinator properties test failed")