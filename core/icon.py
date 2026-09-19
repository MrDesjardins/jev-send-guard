"""Shared icon generation: the same glyph is used for the tray icon and
every Tk window's title bar, so Settings doesn't show Tk's generic default
icon (a feather, on Windows) while the tray shows something else entirely.

A rounded badge + checkmark rather than a plain filled circle — at actual
tray-icon sizes (effectively 16-32px once the OS downscales this), a
recognizable glyph reads better than an attempt at finer detail, and a
bare dot doesn't read as an app icon at all.
"""

from PIL import Image, ImageDraw

SIZE = 64
COLOR_RUNNING = (66, 133, 244, 255)  # blue
COLOR_PAUSED = (154, 160, 166, 255)  # gray
_WHITE = (255, 255, 255, 255)


def make_image(rgba=COLOR_RUNNING):
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 4
    draw.rounded_rectangle(
        (margin, margin, SIZE - margin, SIZE - margin), radius=16, fill=rgba
    )
    draw.line(
        [(18, 32), (27, 42), (46, 20)],
        fill=_WHITE,
        width=6,
        joint="curve",
    )
    return image


def set_window_icon(window, rgba=COLOR_RUNNING):
    """Sets a Tk window's/dialog's title-bar icon to match the tray icon.
    Keeps a reference on the widget itself since Tk doesn't retain a
    PhotoImage internally — once Python garbage-collects it, the icon
    silently blanks out."""
    try:
        from PIL import ImageTk

        photo = ImageTk.PhotoImage(make_image(rgba))
        window.iconphoto(True, photo)
        window._icon_photo_ref = photo  # prevent GC; cosmetic only either way
    except Exception:
        pass  # a failed icon must never be the reason a window doesn't open
