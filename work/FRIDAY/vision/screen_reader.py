import mss
from PIL import Image

class ScreenReader:
    def __init__(self):
        self.sct = mss.mss()

    def capture_screen(self, filename="screenshot.png"):
        screenshot = self.sct.shot(output=filename)
        return screenshot

    def get_screen_data(self):
        monitor = self.sct.monitors[1]
        sct_img = self.sct.grab(monitor)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
