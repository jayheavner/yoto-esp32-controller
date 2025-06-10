"""yotobackend/client.py – rev‑12 (fixed errors and logging)

Fixed MQTT callback and cleaned up logging for better debugging.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal
import requests

from yoto_mvp import YotoAPI

logger = logging.getLogger(__name__)
CACHE = Path(__file__).resolve().parent.parent / "art_cache"
CACHE.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DeviceState:
    playback_status: Literal["playing", "paused", "stopped"] = "stopped"
    card_id: str | None = None
    volume: int = 8

# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Card:
    id: str
    title: str
    author: str | None = None
    art_path: Path = field(init=False)

    def __post_init__(self):
        self.art_path = CACHE / f"{self.id}"

# ---------------------------------------------------------------------------
StateCb = Callable[[DeviceState], Awaitable[None] | None]

# ---------------------------------------------------------------------------
class YotoClient:
    LIBRARY_URL = "https://api.yotoplay.com/card/family/library"

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.api = YotoAPI()
        self.session = requests.Session()
        self.device_id: str | None = None

    async def start(self) -> None:
        logger.info("Starting Yoto client...")
        await self._authenticate()
        await self._select_device()
        await self._connect_mqtt()
        logger.info("Yoto client started successfully")

    async def stop(self) -> None:
        logger.info("Stopping Yoto client...")
        await asyncio.to_thread(self.api.disconnect_mqtt)
        self.session.close()
        logger.info("Yoto client stopped")

    async def _authenticate(self) -> None:
        logger.info("Authenticating with Yoto API...")
        await asyncio.to_thread(self.api.login, self.email, self.password)
        await asyncio.to_thread(self.api.load_devices)
        logger.info("Authentication successful")

    async def _select_device(self) -> None:
        devices = self.api.get_online_devices()
        if devices:
            self.device_id = devices[0].id
            logger.info("Selected online device: %s (%s)", devices[0].name, devices[0].id)
        else:
            device = next(iter(self.api.devices.values()))
            self.device_id = device.id
            logger.warning("No online devices, using offline device: %s (%s)", device.name, device.id)

    async def _connect_mqtt(self) -> None:
        logger.info("Connecting to MQTT...")
        await asyncio.to_thread(self.api.connect_mqtt)
        logger.info("MQTT connection established")

    async def fetch_raw_library(self) -> list[dict]:
        logger.info("Fetching library from Yoto API...")
        headers = self.api._get_auth_headers()
        resp = await asyncio.to_thread(self.session.get, self.LIBRARY_URL, headers=headers, timeout=20)
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
            author = card_data.get("metadata", {}).get("author")
            
            card = Card(id=card_id, title=title, author=author)
            
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
            art_url = self._extract_art_url(card_data)
            
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
        return cards

    def _ensure_device(self) -> str:
        if not self.device_id:
            raise RuntimeError("No device selected")
        return self.device_id

    async def play(self, card_id: str) -> None:
        device_id = self._ensure_device()
        logger.info("Playing card %s on device %s", card_id, device_id)
        await asyncio.to_thread(self.api.play_card, device_id, card_id)

    async def pause(self) -> None:
        device_id = self._ensure_device()
        logger.info("Pausing playback on device %s", device_id)
        await asyncio.to_thread(self.api.pause_device, device_id)

    async def resume(self) -> None:
        device_id = self._ensure_device()
        logger.info("Resuming playback on device %s", device_id)
        await asyncio.to_thread(self.api.resume_device, device_id)

    async def stop_playback(self) -> None:
        device_id = self._ensure_device()
        logger.info("Stopping playback on device %s", device_id)
        await asyncio.to_thread(self.api.stop_device, device_id)

    def subscribe(self, callback: StateCb) -> None:
        raise NotImplementedError
