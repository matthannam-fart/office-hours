import os
import sys
import threading
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper
from PIL import Image, ImageDraw, ImageFont

class StreamDeckHandler:
    def __init__(self, key_callback):
        self.deck = None
        self.key_callback = key_callback
        self.connect()

    def connect(self):
        streamdecks = DeviceManager().enumerate()
        if not streamdecks:
            print("No Stream Deck found.")
            return

        self.deck = streamdecks[0]
        self.deck.open()
        self.deck.reset()

        print(f"Stream Deck Connected: {self.deck.deck_type()}")
        
        self.deck.set_key_callback(self._on_key_change)
        
        # Initial Clear
        self.update_key_image(0, render_oh=True) # OH Logo
        self.update_key_image(1, text="")
        self.update_key_image(2, text="")

    def _on_key_change(self, deck, key, state):
        if self.key_callback:
            self.key_callback(key, state)

    def _get_font(self, size):
        """Get a font that works on both macOS and Windows"""
        font_paths = []
        
        if sys.platform == 'darwin':
            # macOS
            font_paths = [
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/SFNSText.ttf",
                "/Library/Fonts/Arial.ttf",
            ]
        elif sys.platform == 'win32':
            # Windows
            windir = os.environ.get('WINDIR', 'C:\\Windows')
            font_paths = [
                os.path.join(windir, 'Fonts', 'arial.ttf'),
                os.path.join(windir, 'Fonts', 'segoeui.ttf'),
                os.path.join(windir, 'Fonts', 'tahoma.ttf'),
            ]
        else:
            # Linux
            font_paths = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ]
        
        for path in font_paths:
            try:
                if path.endswith('.ttc'):
                    return ImageFont.truetype(path, size, index=1)
                else:
                    return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
        
        return ImageFont.load_default()

    def update_key_color(self, key, r, g, b, label=""):
        color = (r, g, b)
        self.update_key_image(key, text=label, color=color)

    def update_key_image(self, key, text="", color=(0,0,0), render_oh=False):
        if not self.deck: return
        
        # Create Image
        image = PILHelper.create_image(self.deck)
        draw = ImageDraw.Draw(image)
        
        # Background
        draw.rectangle((0, 0, image.width, image.height), fill=color)
        
        # Fonts
        font = self._get_font(14)
        oh_font = self._get_font(24)

        w, h = image.width, image.height

        # Render OH Logo (Bold, Centered)
        if render_oh:
             lbl = "OH"
             bbox = draw.textbbox((0, 0), lbl, font=oh_font)
             tw = bbox[2] - bbox[0]
             th = bbox[3] - bbox[1]
             x = (w - tw) / 2
             y = (h - th) / 2
             
             text_color = "white"
             if (color[0] > 180 and color[1] > 180 and color[2] > 180) or (color[1] > 200):
                  text_color = "black"
             
             draw.text((x, y), lbl, font=oh_font, fill=text_color)
        
        # Render Label
        elif text:
             lines = text.split("\n")
             y = (h - (len(lines)*18)) / 2
             for line in lines:
                 bbox = draw.textbbox((0, 0), line, font=font)
                 tw = bbox[2] - bbox[0]
                 draw.text(((w-tw)/2, y), line, font=font,
                           fill="black" if (color[0]>128 and color[1]>128) else "white")
                 y += 18

        # Transform to native
        native_image = PILHelper.to_native_format(self.deck, image)
        
        try:
            self.deck.set_key_image(key, native_image)
        except Exception as e:
            print(f"Deck Update Error: {e}")

    def close(self):
        if self.deck:
            self.deck.reset()
            self.deck.close()
