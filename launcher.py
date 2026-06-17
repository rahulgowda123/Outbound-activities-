"""Entry point for the standalone RepDashboard.exe.

Starts the Flask server on 127.0.0.1:5050 and pops open the default browser.
Closing this console window stops the server.
"""
from __future__ import annotations

import threading
import time
import webbrowser

from app import HUBSPOT_TOKEN, app, _user_dir

PORT = 5050
URL = f"http://localhost:{PORT}"


def _open_browser_when_ready() -> None:
    # tiny delay so Flask is listening by the time the browser hits it
    time.sleep(1.2)
    try:
        webbrowser.open(URL)
    except Exception:
        pass


def main() -> None:
    print("=" * 60)
    print("  CloudFuze Rep Dashboard")
    print("=" * 60)
    if not HUBSPOT_TOKEN:
        print()
        print("  WARNING: HUBSPOT_TOKEN missing.")
        print(f"  Create a .env file at: {_user_dir() / '.env'}")
        print("  with one line:")
        print("    HUBSPOT_TOKEN=pat-na1-xxxxxxxx-...")
        print("  ...then close this window and re-launch.")
        print()
    else:
        print()
        print(f"  Token loaded from: {_user_dir() / '.env'}")
        print(f"  Opening {URL} in your browser ...")
        print("  Close this window to stop the server.")
        print()

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    try:
        # use_reloader=False because PyInstaller-frozen apps can't re-exec
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
    except OSError as e:
        print(f"\nERROR: could not start the server: {e}")
        print(f"Is port {PORT} already in use? (Another copy of this tool, or MBR_Dashboard.)")
        input("\nPress Enter to close ...")


if __name__ == "__main__":
    main()
