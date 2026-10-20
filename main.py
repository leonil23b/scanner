"""
Quick QR Scanner - Android (8.0+ / API 26+) and desktop.

Changes from the Windows version
--------------------------------
* winsound            -> Kivy SoundLoader (works on Android and desktop)
* cv2.VideoCapture(0) -> Kivy Camera widget (Android camera provider)
* Camera permission is requested at runtime
* Portrait phone layout, big touch-friendly buttons, keyboard-safe input
* Vibration on match, screen stays on while scanning
* Detection runs in a background thread so the preview stays smooth
"""

import math
import os
import re
import struct
import threading
import wave

import cv2
import numpy as np

from kivy.app import App
from kivy.clock import Clock
from kivy.core.audio import SoundLoader
from kivy.core.window import Window
from kivy.graphics.texture import Texture
from kivy.lang import Builder
from kivy.properties import ListProperty
from kivy.uix.camera import Camera
from kivy.utils import escape_markup, platform

ANDROID = platform == "android"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOUND_NAME = "qrsound.wav"

# How many times the alert sounds when a target is found
ALERT_REPEATS = 5

# Pause between repeats, in seconds
ALERT_GAP = 0.15

# Consecutive scans without a QR before a target can re-trigger
MISS_FRAMES_BEFORE_RESET = 6

# Scans per second (10 is a good balance for phones)
SCAN_FPS = 10

# 0 = back camera on most phones
CAMERA_INDEX = 0
CAMERA_RESOLUTION = (640, 480)

# Android cameras deliver landscape frames. Rotate so the preview is upright
# in portrait. If the preview looks sideways/upside-down on your phone,
# change this to 0, 90, 180 or 270.
CAMERA_ROTATION = 90 if ANDROID else 0

ROTATE_MAP = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}

COLOR_WHITE = [1, 1, 1, 1]
COLOR_GREEN = [0.2, 1, 0.3, 1]
COLOR_YELLOW = [1, 0.85, 0.2, 1]
COLOR_RED = [1, 0.35, 0.35, 1]


KV = """
BoxLayout:
    orientation: "vertical"
    padding: dp(8)
    spacing: dp(6)

    Label:
        text: "Quick QR Scanner"
        bold: True
        font_size: "20sp"
        size_hint_y: None
        height: dp(34)

    # ---------------- Camera preview ----------------
    FloatLayout:
        id: camera_box
        size_hint_y: 0.40
        canvas.before:
            Color:
                rgba: 0, 0, 0, 1
            Rectangle:
                pos: self.pos
                size: self.size

        Image:
            id: camera_image
            fit_mode: "contain"
            size_hint: 1, 1
            pos_hint: {"x": 0, "y": 0}

        Label:
            id: status_label
            text: "Starting..."
            color: app.status_color
            bold: True
            font_size: "14sp"
            size_hint: 1, None
            height: dp(34)
            pos_hint: {"x": 0, "y": 0}
            text_size: self.width - dp(12), self.height
            halign: "center"
            valign: "middle"
            shorten: True
            shorten_from: "right"
            canvas.before:
                Color:
                    rgba: 0, 0, 0, 0.6
                Rectangle:
                    pos: self.pos
                    size: self.size

    Label:
        id: result_label
        text: "Scanned result: None"
        font_size: "14sp"
        size_hint_y: None
        height: dp(28)
        text_size: self.width, self.height
        halign: "left"
        valign: "middle"
        shorten: True
        shorten_from: "right"

    # ---------------- Target input ----------------
    BoxLayout:
        size_hint_y: None
        height: dp(50)
        spacing: dp(6)

        TextInput:
            id: target_input
            hint_text: "Enter / paste QR code(s)"
            multiline: False
            write_tab: False
            font_size: "16sp"
            padding: dp(10), dp(13)
            on_text_validate: app.add_target()

        Button:
            text: "ADD"
            bold: True
            size_hint_x: None
            width: dp(84)
            on_release: app.add_target()

    # ---------------- Target list ----------------
    ScrollView:
        size_hint_y: 0.60
        bar_width: dp(5)
        do_scroll_x: False

        Label:
            id: targets_label
            text: "Targets: None"
            markup: True
            font_size: "16sp"
            size_hint_y: None
            height: max(self.texture_size[1] + dp(12), dp(40))
            text_size: self.width - dp(12), None
            halign: "left"
            valign: "top"
            padding: dp(6), dp(6)

    # ---------------- Action buttons ----------------
    BoxLayout:
        size_hint_y: None
        height: dp(52)
        spacing: dp(6)

        Button:
            text: "TEST SOUND"
            on_release: app.test_sound()

        Button:
            text: "STOP"
            on_release: app.stop_sound()

        Button:
            text: "RESET"
            on_release: app.reset_targets()
"""


class QuickQRScanner(App):
    status_color = ListProperty(COLOR_WHITE)

    def build(self):
        self.title = "Quick QR Scanner"

        # Keep the input box visible when the on-screen keyboard opens.
        Window.softinput_mode = "below_target"
        Window.clearcolor = (0.07, 0.08, 0.10, 1)

        self.targets = []  # ordered: 1st added = #1
        self.last_found = set()
        self.found_targets = set()
        self.miss_count = 0

        self.alert_running = False
        self.stop_alert = False
        self.alert_count = 0

        self.detector = cv2.QRCodeDetector()
        self.detecting = False
        self.mirrored = False  # remembered: which orientation decoded last
        self.last_points = None

        self.camera = None
        self.scan_event = None
        self.display_texture = None
        self.camera_allowed = not ANDROID

        self.root = Builder.load_string(KV)

        self.sound = self.load_sound()
        return self.root

    # ------------------------------------------------------------------
    # App lifecycle (Android)
    # ------------------------------------------------------------------
    def on_start(self):
        if ANDROID:
            self.keep_screen_on()
            from android.permissions import Permission, request_permissions

            self.set_status("Requesting camera permission...")
            request_permissions([Permission.CAMERA], self.on_permissions)
        else:
            self.start_camera()

    def on_permissions(self, permissions, grants):
        if grants and all(grants):
            self.camera_allowed = True
            Clock.schedule_once(lambda dt: self.start_camera(), 0)
        else:
            Clock.schedule_once(
                lambda dt: self.set_status(
                    "Camera permission denied. Enable it in Settings.",
                    COLOR_RED,
                ),
                0,
            )

    def on_pause(self):
        # Returning True keeps the app alive in the background.
        self.stop_camera()
        return True

    def on_resume(self):
        if self.camera_allowed:
            self.start_camera()

    def on_stop(self):
        self.stop_camera()
        self.stop_sound()

    def keep_screen_on(self):
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass

            @run_on_ui_thread
            def _set():
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                params = autoclass("android.view.WindowManager$LayoutParams")
                activity.getWindow().addFlags(params.FLAG_KEEP_SCREEN_ON)

            _set()
        except Exception as error:
            print("KEEP SCREEN ON ERROR:", repr(error))

    def vibrate(self, ms=300):
        if not ANDROID:
            return
        try:
            from jnius import autoclass

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            context = autoclass("android.content.Context")
            effect = autoclass("android.os.VibrationEffect")
            vibrator = activity.getSystemService(context.VIBRATOR_SERVICE)
            vibrator.vibrate(
                effect.createOneShot(ms, effect.DEFAULT_AMPLITUDE)
            )
        except Exception as error:
            print("VIBRATE ERROR:", repr(error))

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------
    def start_camera(self):
        try:
            if self.camera is None:
                # The widget is kept 1x1 and invisible; we only use its
                # texture and draw our own preview (with the QR outline).
                self.camera = Camera(
                    index=CAMERA_INDEX,
                    resolution=CAMERA_RESOLUTION,
                    play=True,
                    size_hint=(None, None),
                    size=(1, 1),
                    opacity=0,
                )
                self.root.ids.camera_box.add_widget(self.camera)
            else:
                self.camera.play = True

            if self.scan_event is None:
                self.scan_event = Clock.schedule_interval(
                    self.scan_qr, 1.0 / SCAN_FPS
                )
            self.set_status("Scanning...")
        except Exception as error:
            print("CAMERA ERROR:", repr(error))
            self.set_status("Cannot open camera", COLOR_RED)

    def stop_camera(self):
        if self.scan_event is not None:
            self.scan_event.cancel()
            self.scan_event = None
        if self.camera is not None:
            self.camera.play = False

    # ------------------------------------------------------------------
    # Sound (no winsound on Android)
    # ------------------------------------------------------------------
    def find_sound(self):
        folders = (os.path.join(SCRIPT_DIR, "sound"), SCRIPT_DIR)

        for folder in folders:
            path = os.path.join(folder, SOUND_NAME)
            if os.path.isfile(path):
                print("SOUND FILE FOUND:", path)
                return path

        # Fallback: any .wav next to the app
        for folder in folders:
            if os.path.isdir(folder):
                for name in sorted(os.listdir(folder)):
                    if name.lower().endswith(".wav"):
                        path = os.path.join(folder, name)
                        print("SOUND FILE FOUND (fallback):", path)
                        return path

        print("No sound file found.")
        return None

    def make_beep(self):
        """Generate a simple beep WAV when no sound file is bundled."""
        path = os.path.join(self.user_data_dir, "beep.wav")
        if os.path.isfile(path):
            return path

        rate, seconds, freq = 22050, 0.25, 1000
        count = int(rate * seconds)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(
                b"".join(
                    struct.pack(
                        "<h",
                        int(20000 * math.sin(2 * math.pi * freq * i / rate)),
                    )
                    for i in range(count)
                )
            )
        return path

    def load_sound(self):
        path = self.find_sound()
        sound = SoundLoader.load(path) if path else None

        if sound:
            self.set_status("Sound file ready")
            return sound

        print("Falling back to generated beep")
        try:
            sound = SoundLoader.load(self.make_beep())
        except Exception as error:
            print("BEEP ERROR:", repr(error))
            sound = None

        self.set_status(
            "Sound file missing/invalid - using beep"
            if sound
            else "No sound available",
            COLOR_YELLOW,
        )
        return sound

    def play_sound(self):
        # Repeats the alert without blocking the UI.
        if self.alert_running:
            return

        self.alert_running = True
        self.stop_alert = False
        self.alert_count = 0
        self.vibrate()
        self.play_next(0)

    def play_next(self, dt):
        if (
            self.stop_alert
            or self.alert_count >= ALERT_REPEATS
            or self.sound is None
        ):
            self.alert_running = False
            return

        self.alert_count += 1
        print("ALERT", self.alert_count, "of", ALERT_REPEATS)

        try:
            self.sound.stop()
            self.sound.play()
            length = self.sound.length
        except Exception as error:
            print("SOUND ERROR:", repr(error))
            self.alert_running = False
            return

        if not length or length <= 0:
            length = 0.5

        Clock.schedule_once(self.play_next, length + ALERT_GAP)

    def stop_sound(self):
        self.stop_alert = True
        self.alert_running = False
        if getattr(self, "sound", None):
            try:
                self.sound.stop()
            except Exception:
                pass

    def test_sound(self):
        print("Testing sound...")
        self.play_sound()

    # ------------------------------------------------------------------
    # Targets
    # ------------------------------------------------------------------
    def set_status(self, text, color=None):
        self.root.ids.status_label.text = text
        self.status_color = color or COLOR_WHITE

    def add_target(self):
        raw = self.root.ids.target_input.text

        # Split pasted text on whitespace, commas or semicolons.
        codes = [c for c in re.split(r"[\s,;]+", raw) if c]

        if not codes:
            self.set_status("Enter a target first", COLOR_YELLOW)
            return

        added = []
        duplicates = []

        for code in codes:
            if code in self.targets or code in added:
                duplicates.append(code)
            else:
                self.targets.append(code)
                added.append(code)

        self.root.ids.target_input.text = ""
        self.update_targets_label()

        if added:
            last_number = len(self.targets)
            first_number = last_number - len(added) + 1

            if len(added) == 1:
                message = "Target #%d added: %s" % (first_number, added[0])
            else:
                message = "Added %d targets (#%d to #%d)" % (
                    len(added), first_number, last_number
                )

            if duplicates:
                message += " | %d duplicate(s) skipped" % len(duplicates)
            self.set_status(message)
        else:
            self.set_status(
                "Already in list: " + ", ".join(duplicates), COLOR_YELLOW
            )

        print("TARGETS ADDED:", added, "DUPLICATES:", duplicates)

        # On desktop keep the cursor in the box; on a phone hide the
        # keyboard so the camera preview is visible again.
        if ANDROID:
            self.root.ids.target_input.focus = False
        else:
            Clock.schedule_once(
                lambda dt: setattr(self.root.ids.target_input, "focus", True),
                0.1,
            )

    def update_targets_label(self):
        if not self.targets:
            self.root.ids.targets_label.text = "Targets: None"
            return

        lines = []
        for i, target in enumerate(self.targets, start=1):
            text = escape_markup("%d. %s" % (i, target))

            if target in self.found_targets:
                lines.append("[color=00ff00][b]%s  - FOUND[/b][/color]" % text)
            else:
                lines.append(text)

        found = len(self.found_targets)
        self.root.ids.targets_label.text = (
            "Targets (%d found / %d):\n" % (found, len(self.targets))
            + "\n".join(lines)
        )

    def reset_targets(self):
        self.targets.clear()
        self.found_targets.clear()
        self.last_found.clear()
        self.miss_count = 0
        self.stop_sound()
        self.update_targets_label()

        self.root.ids.result_label.text = "Scanned result: None"
        self.set_status("Targets cleared")

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def scan_qr(self, dt):
        camera = self.camera
        if camera is None or not camera.play:
            return

        texture = camera.texture
        if texture is None:
            return

        width, height = texture.size
        try:
            pixels = texture.pixels  # RGBA, bottom-up (GL order)
        except Exception as error:
            print("TEXTURE ERROR:", repr(error))
            return

        rgba = np.frombuffer(pixels, dtype=np.uint8)
        if rgba.size != width * height * 4:
            return
        rgba = rgba.reshape(height, width, 4)

        # GL order is bottom-up; flip to a normal top-down image.
        frame = cv2.flip(rgba, 0)

        rotation = ROTATE_MAP.get(CAMERA_ROTATION)
        if rotation is not None:
            frame = cv2.rotate(frame, rotation)

        rgb = np.ascontiguousarray(frame[:, :, :3])

        # Detect in a background thread (only one at a time).
        if not self.detecting:
            self.detecting = True
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            threading.Thread(
                target=self.detect_worker, args=(gray,), daemon=True
            ).start()

        # Draw the last known QR outline.
        points = self.last_points
        if points is not None:
            for i in range(len(points)):
                start = tuple(int(v) for v in points[i])
                end = tuple(int(v) for v in points[(i + 1) % len(points)])
                cv2.line(rgb, start, end, (0, 255, 0), 4)

        # Show the frame (flip back to GL order for the texture).
        out = cv2.flip(rgb, 0)
        size = (out.shape[1], out.shape[0])

        if self.display_texture is None or self.display_texture.size != size:
            self.display_texture = Texture.create(size=size, colorfmt="rgb")

        self.display_texture.blit_buffer(
            out.tobytes(), colorfmt="rgb", bufferfmt="ubyte"
        )
        self.root.ids.camera_image.texture = self.display_texture

    def detect_worker(self, gray):
        data, points = "", None
        try:
            data, points = self.detect(gray)
        except Exception as error:
            print("QR DETECTION ERROR:", repr(error))
        finally:
            Clock.schedule_once(
                lambda dt: self.on_detect(data, points), 0
            )

    def detect(self, gray):
        """Try the image as-is, then mirrored (some cameras mirror)."""
        width = gray.shape[1]
        outline = None

        for mirrored in (self.mirrored, not self.mirrored):
            image = cv2.flip(gray, 1) if mirrored else gray

            try:
                data, points, _ = self.detector.detectAndDecode(image)
            except cv2.error as error:
                print("QR DETECTION ERROR:", error)
                continue

            if points is not None:
                points = np.asarray(points, dtype=np.float32).reshape(-1, 2)
                if mirrored:
                    points[:, 0] = (width - 1) - points[:, 0]
                if outline is None:
                    outline = points

            if data:
                self.mirrored = mirrored
                return data, points

        return "", outline

    def on_detect(self, data, points):
        self.detecting = False
        self.last_points = points

        if data:
            self.miss_count = 0
            scanned_text = data.strip()

            self.root.ids.result_label.text = "Scanned result: " + scanned_text
            print("SCANNED QR:", repr(scanned_text))

            if scanned_text in self.targets:
                self.set_status("TARGET FOUND: " + scanned_text, COLOR_GREEN)

                if scanned_text not in self.found_targets:
                    self.found_targets.add(scanned_text)
                    self.update_targets_label()

                if scanned_text not in self.last_found:
                    print("MATCH FOUND! PLAYING SOUND!")
                    self.play_sound()

                self.last_found.add(scanned_text)
            else:
                self.set_status("QR does not match target", COLOR_YELLOW)
        else:
            self.root.ids.result_label.text = "Scanned result: None"
            self.set_status("Scanning...")

            # Allow re-trigger only after the QR has been gone for a while.
            self.miss_count += 1
            if self.miss_count > MISS_FRAMES_BEFORE_RESET:
                self.last_found.clear()


if __name__ == "__main__":
    QuickQRScanner().run()
