import sys
import os
from pathlib import Path
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from desktop_ui.coordinator import DesktopCoordinator
from desktop_ui.qt_models.card_model import CardModel


def main() -> int:
    # Set Qt Quick Controls style to Basic to allow background customization
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    
    coordinator = DesktopCoordinator()
    
    try:
        cards = coordinator.get_cards()
    except Exception as e:
        print(f"Failed to load library: {e}")
        return 1
    
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    
    model = CardModel(cards)
    
    engine.rootContext().setContextProperty("imageModel", model)
    engine.rootContext().setContextProperty("coordinator", coordinator)
    
    qml_file = Path(__file__).parent / "qml" / "MainWindow.qml"
    engine.load(qml_file)
    
    if not engine.rootObjects():
        print("Failed to load QML")
        coordinator.cleanup()
        return 1
    
    try:
        return app.exec()
    finally:
        coordinator.cleanup()