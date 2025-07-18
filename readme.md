# Yoto Project Structure & File Responsibilities

## Directory Structure

```
project_root/
├── .env                             # Environment variables (desktop dev only)
├── .gitignore                       # Git ignore rules
├── README.md                        # Project documentation & setup
├── requirements.txt                 # Python dependencies (desktop)
├── main.py                          # Desktop entry point
├── config/
│   ├── __init__.py
│   ├── base.py                      # Platform-agnostic config interface
│   └── desktop.py                   # Desktop-specific config (env vars)
├── core/
│   ├── __init__.py
│   ├── api_client.py                # Yoto API wrapper (portable)
│   ├── cache_manager.py             # Library + artwork caching (portable)
│   ├── data_models.py               # Card, Library dataclasses (portable)
│   ├── coordinator.py               # YotoCoordinator – async API + MQTT handler
│   └── ui_logic/
│       ├── __init__.py
│       ├── grid_layout.py           # 3-column grid calculations (portable)
│       ├── card_selection.py        # Selection state management (portable)
│       ├── display_controller.py    # What to show where (portable)
│       └── events.py               # Abstract UI events (portable)
├── desktop_ui/
│   ├── __init__.py
│   ├── app.py                       # Qt application lifecycle
│   ├── coordinator.py               # Connect YotoCoordinator to Qt
│   ├── qt_models/
│   │   ├── __init__.py
│   │   └── card_model.py            # Qt-specific model wrapper
│   └── qml/
│       ├── __init__.py
│       └── MainWindow.qml           # Desktop UI definition
└── cache/                           # Main cache folder
    ├── art/                         # Cached card artwork
    │   └── *.jpg                    # Individual card images
    └── data/                        # Cached library data
        ├── library.json             # Cached library metadata
        └── last_update.txt          # Cache timestamp
```

## File Responsibilities

### Root Level Files

**`main.py`** - Desktop entry point
- Single responsibility: Launch desktop application
- Imports and calls `desktop_ui.app.main()`
- Minimal error handling for startup failures

**`.env`** - Development environment variables
- `YOTO_USERNAME` and `YOTO_PASSWORD`
- Desktop-only (not deployed to ESP32)

**`requirements.txt`** - Python dependencies
- PySide6, requests, asyncio dependencies
- Desktop-only (ESP32 uses MicroPython)

### Configuration Layer

**`config/base.py`** - Platform-agnostic configuration interface
- Abstract base class defining configuration contract
- Properties: credentials, cache_paths, device_settings
- No platform-specific imports

**`config/desktop.py`** - Desktop configuration implementation
- Reads from `.env` file and environment variables
- Defines desktop cache paths
- Error handling for missing credentials

### Core Layer (ESP32 Portable)

**`core/api_client.py`** - Yoto API wrapper
- HTTP requests to Yoto API endpoints
- Authentication token management
- Library data fetching
- No Qt or desktop dependencies
- Pure Python standard library + requests

**`core/cache_manager.py`** - Caching system
- Library metadata caching (JSON)
- Artwork file caching
- Cache invalidation and refresh logic
- Filesystem operations (works on ESP32 limited storage)
- Thread-safe operations

**`core/data_models.py`** - Data structures
- `Card` dataclass with id, title, author, art_path
- `Library` collection management
- No external dependencies
- Serializable for JSON caching

**`core/coordinator.py`** - YotoCoordinator – async API + MQTT handler

### UI Logic Layer (ESP32 Portable)

**`core/ui_logic/grid_layout.py`** - Grid mathematics
- Calculate row/column positions for 3-column grid
- Determine which cards are visible in viewport
- Grid dimension calculations
- No UI framework dependencies

**`core/ui_logic/card_selection.py`** - Selection state
- Track currently selected card
- Selection change events
- Multi-select logic (future)
- No UI framework dependencies

**`core/ui_logic/display_controller.py`** - Display coordination
- Determine what content to show
- Handle loading states
- Coordinate between data and UI layers
- Abstract interface for different UI backends

**`core/ui_logic/events.py`** - Abstract UI events
- Card click/touch events
- Navigation events
- Abstract event classes that both Qt and ESP32 can implement

### Desktop UI Layer (Desktop Only)

**`desktop_ui/app.py`** - Qt application setup
- QGuiApplication creation and configuration
- QML engine setup and file loading
- Application lifecycle management
- Resource cleanup on exit

**`desktop_ui/coordinator.py`** - Qt bridge to async coordinator
- Connects `YotoCoordinator` with the Qt event loop
- Ensures thread safety for UI updates
- Handles data loading and user feedback

**`desktop_ui/qt_models/card_model.py`** - Qt model wrapper
- `QAbstractListModel` implementation
- Wraps portable Card objects for Qt consumption
- Qt-specific role definitions
- File URL handling for QML

**`desktop_ui/qml/MainWindow.qml`** - Desktop UI definition
- QML interface for desktop application
- Grid view configuration
- Touch/mouse interaction handling
- Desktop-specific UI elements

### Cache Structure

**`cache/art/`** - Artwork storage
- Individual card artwork files (*.jpg, *.png)
- Named by card ID for easy lookup
- Shared between desktop and ESP32 (different resolutions)

**`cache/data/`** - Metadata storage
- `library.json`: Cached library metadata
- `last_update.txt`: Cache timestamp for refresh logic
- JSON format for easy parsing on both platforms

## ESP32 Migration Path

### Shared Components (No Changes Needed)
- `core/api_client.py`
- `core/cache_manager.py` 
- `core/data_models.py`
- `core/ui_logic/` (entire folder)
- `cache/` structure

### ESP32-Specific Additions (Future)
```
├── config/esp32.py                  # ESP32 hardcoded config
├── esp32_ui/
│   ├── display_driver.py            # Hardware display interface
│   ├── touch_handler.py             # Touch input handling
│   └── simple_gui.py               # MicroPython GUI framework
└── esp32_main.py                    # ESP32 entry point
```

### Migration Benefits
1. **Core business logic unchanged** - API calls, caching, data models
2. **UI logic reusable** - Grid calculations, selection state
3. **Clean interface boundaries** - Easy to swap UI implementations
4. **Incremental testing** - Can test core on ESP32 before UI work
5. **Shared development** - Same cache files work on both platforms

## Development Workflow

1. **Phase 1**: Extract current code into this structure
2. **Phase 2**: Test desktop application with new structure
3. **Phase 3**: Create ESP32 config and test core layer
4. **Phase 4**: Build ESP32 UI layer using shared ui_logic
5. **Phase 5**: Deploy and optimize for ESP32 hardware

This structure provides a clean migration path while maintaining all current functionality and enabling future ESP32 deployment with minimal code duplication.

## Running

Set your credentials in `.env`:

```
YOTO_USERNAME=your_email
YOTO_PASSWORD=your_password
```

Then start the application:

```
python main.py
```
