from typing import Any, List
from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QPersistentModelIndex, Qt, QUrl
from core.data_models import Card


class CardModel(QAbstractListModel):
    ImagePathRole = Qt.ItemDataRole.UserRole + 1
    CardIdRole = Qt.ItemDataRole.UserRole + 2
    TitleRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self, cards: List[Card]) -> None:
        super().__init__()
        self.cards = cards
        print(f"CardModel created with {len(self.cards)} cards")

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return len(self.cards)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.cards):
            return None
        
        card = self.cards[index.row()]
        
        if role == self.ImagePathRole:
            if card.art_path and card.art_path.exists():
                return QUrl.fromLocalFile(str(card.art_path)).toString()
        elif role == self.CardIdRole:
            return card.id
        elif role == self.TitleRole:
            return card.title
        
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            self.ImagePathRole: QByteArray(b"imagePath"),
            self.CardIdRole: QByteArray(b"cardId"),
            self.TitleRole: QByteArray(b"cardTitle")
        }