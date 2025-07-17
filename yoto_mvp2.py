import os
import time
import logging
from functools import wraps
from dotenv import load_dotenv
from yoto_api import YotoManager

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def require_connection(func):
    # Prevents method execution if YotoManager is not initialized
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.yoto_manager is None:
            logger.error("YotoManager is not initialized.")
            return False
        return func(self, *args, **kwargs)
    return wrapper

def ensure_mqtt(func):
    # Establishes MQTT connection before transport control methods
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.yoto_manager is None:
            logger.error("YotoManager is not initialized.")
            return False
        
        if not getattr(self.yoto_manager, 'mqtt_client', None):
            logger.info("Connecting to MQTT for playback control...")
            self.yoto_manager.connect_to_events()
            time.sleep(2)
        
        return func(self, *args, **kwargs)
    return wrapper

class YotoMVP:
    def __init__(self):
        # Validate credentials first
        username = os.getenv('YOTO_USERNAME')
        password = os.getenv('YOTO_PASSWORD')
        
        if not username or not password:
            raise ValueError("Please set YOTO_USERNAME and YOTO_PASSWORD in your .env file")
        
        # Only store after validation
        self.username = username
        self.password = password
        self.yoto_manager = None
        self.players = {}
        self.library = {}
    
    def connect(self):
        try:
            logger.info("Connecting to Yoto API...")
            self.yoto_manager = YotoManager(self.username, self.password)
            
            # Authenticate and get initial data
            self.yoto_manager.check_and_refresh_token()
            logger.info("✓ Authentication successful")
            
            # Update player status
            self.yoto_manager.update_players_status()
            self.players = self.yoto_manager.players
            logger.info(f"✓ Found {len(self.players)} player(s)")
            
            # Update library
            self.yoto_manager.update_library()
            self.library = self.yoto_manager.library
            logger.info(f"✓ Found {len(self.library)} library item(s)")
            
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False
    
    @require_connection
    def list_players(self):
        print("\n" + "="*50)
        print("YOTO PLAYERS")
        print("="*50)
        
        if not self.players:
            print("No players found.")
            return
        
        for i, (player_id, player) in enumerate(self.players.items(), 1):
            print(f"\n{i}. Player ID: {player_id}")
            print(f"   Name: {getattr(player, 'name', 'Unknown')}")
            print(f"   Online: {getattr(player, 'online', 'Unknown')}")
            print(f"   Volume: {getattr(player, 'volume', 'Unknown')}")
            
            # Current playback info
            if hasattr(player, 'current_card'):
                current_card = getattr(player, 'current_card', None)
                if current_card:
                    print(f"   Current Card: {current_card}")
            
            if hasattr(player, 'playback_status'):
                status = getattr(player, 'playback_status', 'Unknown')
                print(f"   Status: {status}")
    
    @require_connection
    def list_library(self):
        print("\n" + "="*50)
        print("LIBRARY ITEMS")
        print("="*50)
        
        if not self.library:
            print("No library items found.")
            return
        
        for i, (card_id, card) in enumerate(self.library.items(), 1):
            print(f"\n{i}. Card ID: {card_id}")
            print(f"   Title: {getattr(card, 'title', 'Unknown')}")
            print(f"   Type: {getattr(card, 'content_type', 'Unknown')}")
            
            # Try to get additional details
            if hasattr(card, 'description'):
                desc = getattr(card, 'description', '')
                if desc:
                    print(f"   Description: {desc[:100]}{'...' if len(desc) > 100 else ''}")
            
            if hasattr(card, 'duration'):
                duration = getattr(card, 'duration', None)
                if duration:
                    print(f"   Duration: {duration}s")
    
    def get_player_by_index(self, index):
        # 1-based indexing for user interface
        player_list = list(self.players.keys())
        if 1 <= index <= len(player_list):
            return player_list[index - 1]
        return None
    
    def get_card_by_index(self, index):
        # 1-based indexing for user interface
        card_list = list(self.library.keys())
        if 1 <= index <= len(card_list):
            return card_list[index - 1]
        return None
    
    @ensure_mqtt
    def play_card(self, player_index, card_index):
        player_id = self.get_player_by_index(player_index)
        card_id = self.get_card_by_index(card_index)
        
        if not player_id:
            print(f"Invalid player index: {player_index}")
            return False
        
        if not card_id:
            print(f"Invalid card index: {card_index}")
            return False
        
        try:
            assert self.yoto_manager is not None  # For type checker - decorator ensures this
            card = self.library[card_id]
            card_title = getattr(card, 'title', 'Unknown')
            player_name = getattr(self.players[player_id], 'name', 'Unknown')
            
            logger.info(f"Playing '{card_title}' on '{player_name}'...")
            
            # Use MQTT card_play with the exact parameters that work
            self.yoto_manager.mqtt_client.card_play(
                player_id,
                card_id,
                secondsIn=0,
                cutoff=0,
                chapterKey="1",
                trackKey=1
            )
            
            print(f"✓ Started playing '{card_title}' on '{player_name}'")
            return True
            
        except Exception as e:
            logger.error(f"Failed to play card: {e}")
            return False
    
    @ensure_mqtt
    def pause_player(self, player_index):
        player_id = self.get_player_by_index(player_index)
        
        if not player_id:
            print(f"Invalid player index: {player_index}")
            return False
        
        try:
            assert self.yoto_manager is not None  # For type checker - decorator ensures this
            self.yoto_manager.pause_player(player_id)
            player_name = getattr(self.players[player_id], 'name', 'Unknown')
            print(f"✓ Paused '{player_name}'")
            return True
            
        except Exception as e:
            logger.error(f"Failed to pause player: {e}")
            return False
    
    @ensure_mqtt
    def stop_player(self, player_index):
        player_id = self.get_player_by_index(player_index)
        
        if not player_id:
            print(f"Invalid player index: {player_index}")
            return False
        
        try:
            assert self.yoto_manager is not None  # For type checker - decorator ensures this
            self.yoto_manager.stop_player(player_id)
            player_name = getattr(self.players[player_id], 'name', 'Unknown')
            print(f"✓ Stopped '{player_name}'")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop player: {e}")
            return False
    
    @require_connection
    def refresh_data(self):
        print("Refreshing data...")
        assert self.yoto_manager is not None  # For type checker - decorator ensures this
        self.yoto_manager.update_players_status()
        self.yoto_manager.update_library()
        self.players = self.yoto_manager.players
        self.library = self.yoto_manager.library
        print("✓ Data refreshed")
    
    def interactive_menu(self):
        while True:
            print("\n" + "="*50)
            print("YOTO MVP - INTERACTIVE MENU")
            print("="*50)
            print("1. List Players")
            print("2. List Library")
            print("3. Play Card")
            print("4. Pause Player")
            print("5. Stop Player")
            print("6. Refresh Data")
            print("0. Exit")
            
            try:
                choice = input("\nEnter your choice (0-6): ").strip()
                
                if choice == '0':
                    print("Goodbye!")
                    break
                elif choice == '1':
                    self.list_players()
                elif choice == '2':
                    self.list_library()
                elif choice == '3':
                    self.list_players()
                    player_idx = int(input("Enter player number: "))
                    self.list_library()
                    card_idx = int(input("Enter card number: "))
                    self.play_card(player_idx, card_idx)
                elif choice == '4':
                    self.list_players()
                    player_idx = int(input("Enter player number to pause: "))
                    self.pause_player(player_idx)
                elif choice == '5':
                    self.list_players()
                    player_idx = int(input("Enter player number to stop: "))
                    self.stop_player(player_idx)
                elif choice == '6':
                    self.refresh_data()
                else:
                    print("Invalid choice. Please try again.")
                    
            except ValueError:
                print("Invalid input. Please enter a number.")
            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
    
    def disconnect(self):
        if self.yoto_manager and getattr(self.yoto_manager, 'mqtt_client', None):
            self.yoto_manager.disconnect()
            logger.info("Disconnected from Yoto API")

def main():
    mvp = YotoMVP()
    
    try:
        # Connect to Yoto API
        if not mvp.connect():
            print("Failed to connect to Yoto API. Please check your credentials.")
            return
        
        # Show initial data
        mvp.list_players()
        mvp.list_library()
        
        # Start interactive menu
        mvp.interactive_menu()
        
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        mvp.disconnect()

if __name__ == "__main__":
    main()