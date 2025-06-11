import requests
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
from core.data_models import Card
import logging

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
                print(f"Login failed: {response.status_code} - {response.text}")
                return False
                
            self.token_data = response.json()
            expires_in = self.token_data.get('expires_in') if self.token_data else None
            print(f"Authentication successful. Token expires in: {expires_in} seconds")
            return True
            
        except Exception as e:
            print(f"Authentication error: {e}")
            return False
    
    def _get_auth_headers(self) -> Dict[str, str]:
        if not self.token_data:
            raise ValueError("Must authenticate before making authenticated requests")
            
        return {
            "Authorization": f"{self.token_data['token_type']} {self.token_data['access_token']}",
            "Content-Type": "application/json",
            "User-Agent": "Yoto/2.73 (com.yotoplay.Yoto; build:10405; iOS 17.4.0) Alamofire/5.6.4"
        }
    
    def get_library(self) -> List[Card]:
        try:
            url = f"{self.base_url}/card/family/library"
            response = self.session.get(url, headers=self._get_auth_headers())
            
            if response.status_code != 200:
                print(f"Get library failed: {response.status_code} - {response.text}")
                return []
                
            data = response.json()
            cards = []
            
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
                
            print(f"Loaded {len(cards)} cards from library")
            return cards
            
        except Exception as e:
            print(f"Get library error: {e}")
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
                print(f"No artwork URL found for card {card_id}")
                return None
            
            print(f"Downloading artwork for card {card_id}")
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
            print(f"Failed to download artwork for {card_id}: {e}")
            return None
    
    def close(self) -> None:
        self.session.close()