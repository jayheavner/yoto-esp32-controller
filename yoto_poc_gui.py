"""Simple image grid display - use existing YotoClient library"""
import sys
import asyncio
import os
from typing import Any

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QPersistentModelIndex, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from yotobackend.client import YotoClient

QML_CONTENT = """
import QtQuick 6.7

Window {
    width: 480
    height: 800
    visible: true
    color: "#111"
    title: "Image Grid"

    GridView {
        anchors.fill: parent
        anchors.margins: 10
        cellWidth: 150
        cellHeight: 200
        model: imageModel

        delegate: Image {
            width: 140
            height: 190
            source: imagePath
            fillMode: Image.PreserveAspectFit
        }
    }
}
"""

class ImageModel(QAbstractListModel):
    ImagePathRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, cards) -> None:
        super().__init__()
        self.cards = cards
        print(f"ImageModel created with {len(self.cards)} cards")

    def rowCount(self, parent: 'QModelIndex | QPersistentModelIndex' = QModelIndex()) -> int:
        return len(self.cards)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.cards):
            return None
        
        if role == self.ImagePathRole:
            card = self.cards[index.row()]
            if card.art_path.exists():
                return QUrl.fromLocalFile(str(card.art_path)).toString()
        
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {self.ImagePathRole: QByteArray(b"imagePath")}

async def get_cards():
    """Use existing YotoClient to get cards"""
    username = os.getenv("YOTO_USERNAME")
    password = os.getenv("YOTO_PASSWORD")
    
    if not (username and password):
        raise RuntimeError("Set YOTO_USERNAME and YOTO_PASSWORD environment variables")
    
    client = YotoClient(username, password)
    
    # Only do auth and library fetch, skip MQTT
    await client._authenticate()
    cards = await client.get_library()
    
    print(f"Got {len(cards)} cards from library")
    return cards

def main() -> int:
    # Get cards using existing client
    try:
        cards = asyncio.run(get_cards())
    except Exception as e:
        print(f"Failed to load library: {e}")
        return 1
    
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    
    model = ImageModel(cards)
    engine.rootContext().setContextProperty("imageModel", model)
    
    engine.loadData(QML_CONTENT.encode())
    
    if not engine.rootObjects():
        print("Failed to load QML")
        return 1
    
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())