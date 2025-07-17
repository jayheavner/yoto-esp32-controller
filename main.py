"""
Desktop entry point
 - Single responsibility: Launch desktop application
 - Imports and calls desktop_ui.app.main()
 - Minimal error handling for startup failures
"""
import sys
from desktop_ui.app import main

if __name__ == "__main__":
    sys.exit(main())