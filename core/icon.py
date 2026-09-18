"""Shared icon generation: the same simple circle glyph is used for the
tray icon and every Tk window's title bar, so Settings doesn't show Tk's
generic default icon (a feather, on Windows) while the tray shows
something else entirely.
"""

from PIL import Image, ImageDraw

SIZE = 64
COLOR_RUNNING = (66, 133, 244, 255)  # blue
COLOR_PAUSED = (154, 160, 166, 255)  # gray


def make_image(rgba=COLOR_RUNNING):
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 6
    draw.ellipse((margin, margin, SIZE - margin, SIZE - margin), fill=rgba)
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
