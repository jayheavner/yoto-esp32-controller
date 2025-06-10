from typing import Any, List
from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QPersistentModelIndex, Qt, QUrl
from core.data_models import Card


class CardModel(QAbstractListModel):
    ImagePathRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, cards: List[Card]) -> None:
        super().__init__()
        self.cards = cards
        print(f"CardModel created with {len(self.cards)} cards")

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self.cards)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.cards):
            return None
        
        if role == self.ImagePathRole:
            card = self.cards[index.row()]
            if card.art_path and card.art_path.exists():
                return QUrl.fromLocalFile(str(card.art_path)).toString()
        
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {self.ImagePathRole: QByteArray(b"imagePath")}
