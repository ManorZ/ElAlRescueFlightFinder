"""
Windows system tray icon for El Al Rescue Flight Finder.

Provides a pystray-based tray icon with menu options to open the dashboard,
trigger a manual refresh, view crawl status, and quit the application.
"""

import webbrowser
import threading

import pystray
from PIL import Image, ImageDraw, ImageFont

from config import FLASK_PORT

# Global shutdown event, set by run_tray caller or quit_app
_shutdown_event: threading.Event = None

# El Al brand colors
COLOR_BLUE = "#003087"
COLOR_GREEN = "#28a745"


def create_icon_image(color="blue"):
    """Create a simple 64x64 tray icon image.

    Args:
        color: "blue" for normal state, "green" for new-flights-available state.

    Returns:
        A PIL Image suitable for use as a system tray icon.
    """
    fill_color = COLOR_GREEN if color == "green" else COLOR_BLUE
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw a filled circle as the icon background
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=fill_color,
    )

    # Draw "EL" text in the center
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except (OSError, IOError):
        font = ImageFont.load_default()

    text = "EL"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = (size - text_width) // 2
    text_y = (size - text_height) // 2 - bbox[1]
    draw.text((text_x, text_y), text, fill="white", font=font)

    return img


def open_dashboard(icon, item):
    """Open the web dashboard in the default browser."""
    webbrowser.open(f"http://127.0.0.1:{FLASK_PORT}")


def refresh_now(icon, item):
    """Trigger an immediate crawl refresh via the scheduler."""
    from scheduler import trigger_refresh
    trigger_refresh()


def get_status_text():
    """Return a human-readable status string for the last/next crawl times.

    Returns:
        Formatted string like "Last crawl: 14:30 | Next: 15:30"
    """
    from scheduler import get_status
    status = get_status()
    last_crawl = status.get("last_crawl", "N/A")
    next_crawl = status.get("next_crawl", "N/A")
    return f"Last crawl: {last_crawl} | Next: {next_crawl}"


def quit_app(icon, item):
    """Stop the scheduler, tray icon, and signal the application to shut down."""
    from scheduler import stop_scheduler
    stop_scheduler()
    icon.stop()
    if _shutdown_event is not None:
        _shutdown_event.set()


def create_tray_icon():
    """Create and return a configured pystray Icon instance.

    Returns:
        A pystray.Icon ready to be run.
    """
    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", open_dashboard, default=True),
        pystray.MenuItem("Refresh Now", refresh_now),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Status",
            None,
            enabled=False,
            visible=lambda item: True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )

    icon = pystray.Icon(
        name="ElAlFlightFinder",
        icon=create_icon_image(),
        title="El Al Rescue Flight Finder",
        menu=menu,
    )
    return icon


def run_tray(shutdown_event):
    """Create and run the tray icon (blocking).

    This should be called from a dedicated thread. It blocks until the icon
    is stopped (e.g. via quit_app).

    Args:
        shutdown_event: A threading.Event that will be set when the app
                        should shut down.
    """
    global _shutdown_event
    _shutdown_event = shutdown_event

    icon = create_tray_icon()
    icon.run()


def update_icon_for_new_flights(icon, has_new):
    """Update the tray icon color based on whether new flights are available.

    Args:
        icon: The pystray.Icon instance to update.
        has_new: True to show green (new flights), False for blue (normal).
    """
    color = "green" if has_new else "blue"
    icon.icon = create_icon_image(color)
