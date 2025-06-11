import asyncio
import os
from typing import List, Optional, Dict, Any
from PySide6.QtCore import QObject, Slot
from core.api_client import YotoAPIClient
from core.data_models import Card


class DesktopCoordinator(QObject):
    
    def __init__(self):
        super().__init__()
        self.api_client = YotoAPIClient()
    
    def get_cards(self) -> List[Card]:
        username = os.getenv("YOTO_USERNAME")
        password = os.getenv("YOTO_PASSWORD")
        
        if not (username and password):
            raise RuntimeError("Set YOTO_USERNAME and YOTO_PASSWORD environment variables")
        
        if not self.api_client.authenticate(username, password):
            raise RuntimeError("Authentication failed")
        
        cards = self.api_client.get_library()
        if not cards:
            raise RuntimeError("Failed to load library")
        
        return cards
    
    @Slot(str, result=list)
    def get_chapters(self, card_id: str) -> List[Dict[str, Any]]:
        """Get chapters for a specific card ID, returns QML-friendly format"""
        try:
            chapters = self.api_client.get_card_chapters(card_id)
            if chapters is None:
                print(f"No chapters found for card {card_id}")
                return []
            
            # Convert to QML-friendly format
            qml_chapters = []
            for chapter in chapters:
                qml_chapter = {
                    "key": chapter.get("key", ""),
                    "title": chapter.get("title", "Unknown"),
                    "duration": chapter.get("duration", 0),
                    "iconUrl": chapter.get("display", {}).get("icon16x16", "")
                }
                qml_chapters.append(qml_chapter)
            
            print(f"Returning {len(qml_chapters)} chapters for card {card_id}")
            return qml_chapters
            
        except Exception as e:
            print(f"Error getting chapters for {card_id}: {e}")
            return []
    
    def cleanup(self) -> None:
        self.api_client.close()