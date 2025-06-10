import asyncio
import os
from typing import List
from core.api_client import YotoAPIClient
from core.data_models import Card


class DesktopCoordinator:
    
    def __init__(self):
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
    
    def cleanup(self) -> None:
        self.api_client.close()
