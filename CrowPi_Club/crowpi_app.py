#!/usr/bin/env python3
# ==================================================================
#  crowpi_app.py  -  the CrowPi Club app engine
#
#  Every lesson opens as a full-screen app built from this file:
#    LEARN      - what the part is and how it works
#    TRY IT     - live controls and readings for that part
#    CHALLENGE  - things to do; the kit checks them and ticks them off
#  plus the DIP switch check for BUTTON MODE lessons, and
#  "On the kit right now": the LED matrix, LCD and 7-segment on screen.
#  The CrowPi Show (Day 22) and the games use it too.
#
#  The lesson files just say which lesson to open. DON'T CHANGE THIS FILE.
# ==================================================================
import crowpi_kit                  # CrowPi Club setup - always keep this line first
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
import tkinter as tk
from crowpi_kit import (LCD, SevenSegment, DHT20, LightSensor, RFID, IRRemote, ButtonMatrix,
                        open_matrix, draw_picture, scroll_text, HEART, SMILE, ARROWS)
from gpiozero import Buzzer, OutputDevice, DigitalInputDevice, Button, DistanceSensor, PWMOutputDevice


# ---------------- colours and sizes (the same look as CrowPi Lessons) ----------------
BG = "#1B2230"
PANEL = "#262F42"
PANEL_2 = "#313C54"
TEXT = "#C9D3E3"
WHITE = "#FFFFFF"
GREEN = "#2E9E5B"
GREEN_2 = "#36B86A"
ORANGE = "#E07B00"
BLUE = "#2F6FD0"
PURPLE = "#7B3FC4"
RED = "#D64545"
YELLOW = "#FFD866"
GREY = "#5A6378"

ARROW_PINS = {"UP": 26, "DOWN": 13, "LEFT": 25, "RIGHT": 19}
# Games need the admin password (the same one as in CrowPi Lessons).
# Only its fingerprint (SHA-256) is kept here, not the password itself.
GAMES_PASSWORD_SHA256 = "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"
TESTING = bool(os.environ.get("CROWPI_SHOW_TEST"))       # (only for testing the app)
SCORES_FILE = os.path.join(os.path.expanduser("~"), ".crowpi_show_scores.json")
PROGRESS_FILE = os.path.join(os.path.expanduser("~"), ".crowpi_progress.json")
HERE = os.path.dirname(os.path.abspath(__file__))                        # the lesson folder
REQUEST_FILE = os.path.join(os.path.expanduser("~"), ".crowpi_run_request")  # read by the Thonny helper
SETTINGS_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")),
                             "crowpi-lessons.json")                    # the CrowPi Lessons settings


def games_locked_setting():
    """Set by the installer: are the games behind the admin password? (yes unless it says no)"""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f).get("games_locked", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def thonny_process():
    """The Thonny window this lesson runs in (its process number), or None."""
    pid = os.getppid()
    for _ in range(4):
        if pid <= 1:
            return None
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                command = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
            with open("/proc/%d/status" % pid, encoding="utf-8") as f:
                parent = int([line.split()[1] for line in f if line.startswith("PPid:")][0])
        except (OSError, ValueError, IndexError):
            return None
        if "thonny" in command.lower() and "backend" not in command and "launcher" not in command:
            return pid
        pid = parent
    return None
STAR = ["...X....", "...X....", "XXXXXXX.", ".XXXXX..", "..XXX...", ".XX.XX..", "XX...XX.", "........"]
CHECK = ["........", ".......X", "......XX", ".....XX.", "X...XX..", "XX.XX...", ".XXX....", "..X....."]


# ==================================================================
#  THE KIT: starts every part, reads the sensors in the background
# ==================================================================
class Kit:
    def __init__(self):
        self.lock = threading.RLock()
        self.missing: dict = {}
        print("Starting the parts...")
        self.lcd = self.part("LCD screen", LCD)
        self.seg = self.part("7-segment display", SevenSegment)
        self.matrix = self.part("LED matrix", open_matrix)
        self.buzzer = self.part("buzzer", lambda: Buzzer(18))
        self.relay = self.part("relay", lambda: OutputDevice(21))
        self.weather = self.part("temperature sensor", DHT20)
        self.light = self.part("light sensor", LightSensor)
        self.touch = self.part("touch sensor", lambda: DigitalInputDevice(17))
        self.distance = self.part("distance sensor",
                                  lambda: DistanceSensor(echo=12, trigger=16, max_distance=2))
        self.remote = self.part("IR receiver", IRRemote)
        self.cards = self.part("RFID reader", lambda: RFID(reset_pin=None))
        self.camera = "looking"
        self.pwm = None
        self.arrows: dict = {}
        self.grid = None
        self.mode = ""
        self.use_arrows()
        self.state: dict = {"temperature": None, "humidity": None, "light": None, "distance": None,
                      "touch": False, "arrows": set(), "grid": None,
                      "ir": "", "card": None}
        self.events: deque = deque(maxlen=200)       # (what happened, detail)
        self.lcd_text = ("", "")
        self.seg_number = None
        self.seg_text = ""
        self.matrix_rows = ["........"] * 8
        self.running = True
        threading.Thread(target=self._find_camera, daemon=True).start()
        threading.Thread(target=self._poll, daemon=True).start()

    def part(self, name, make):
        try:
            device = make()
            print("  ready   ", name)
            return device
        except Exception as problem:
            print("  SKIPPED ", name, "-", problem)
            self.missing[name] = str(problem)
            return None

    def _find_camera(self):
        try:
            if crowpi_kit.find_usb_camera():
                self.camera = "USB camera"
            elif crowpi_kit.find_ribbon_camera_tool():
                self.camera = "camera"
            else:
                self.camera = ""
        except Exception:
            self.camera = ""

    # ---- the arrow buttons and the 16-button grid share wires: one at a time ----
    def use_arrows(self):
        with self.lock:
            if self.mode == "arrows":
                return
            if self.grid:
                self.grid.close()
                self.grid = None
            try:
                self.arrows = {name: Button(pin) for name, pin in ARROW_PINS.items()}
                self.missing.pop("arrow buttons", None)
            except Exception as problem:
                self.arrows = {}
                self.missing["arrow buttons"] = str(problem)
            self.mode = "arrows"

    def use_grid(self):
        with self.lock:
            if self.mode == "grid":
                return
            for button in self.arrows.values():
                button.close()
            self.arrows = {}
            try:
                self.grid = ButtonMatrix()
                self.missing.pop("button grid", None)
            except Exception as problem:
                self.grid = None
                self.missing["button grid"] = str(problem)
            self.mode = "grid"

    # ---- background reading: the window never waits for a sensor ----
    def _poll(self):
        last_slow = 0.0
        last_card = 0.0
        while self.running:
            now = time.time()
            try:
                self._read_fast(now)
                if self.cards and now - last_card > 0.4:
                    last_card = now
                    self._read_card()
                if now - last_slow > 1.5:
                    last_slow = now
                    self._read_slow()
            except Exception as problem:
                print("sensor problem:", problem)
            time.sleep(0.03)

    def _read_fast(self, now):
        state = self.state
        with self.lock:
            if self.touch:
                touched = self.touch.value == 1
                if touched and not state["touch"]:
                    self.events.append(("touch", now))
                state["touch"] = touched
            if self.mode == "arrows" and self.arrows:
                pressed = {name for name, button in self.arrows.items() if button.is_pressed}
                for name in pressed - state["arrows"]:
                    if len(pressed) < 4:                     # all 4 at once = switches wrong
                        self.events.append(("arrow", name))
                state["arrows"] = pressed
            elif self.mode == "grid" and self.grid:
                key = self.grid.read()
                if key and key != state["grid"]:
                    self.events.append(("grid", key))
                state["grid"] = key
            if self.distance:
                state["distance"] = self.distance.distance * 100
            if self.remote:
                key = self.remote.read_key()
                if key:
                    state["ir"] = key
                    self.events.append(("ir", key))

    def _read_card(self):
        with self.lock:
            card = self.cards.read_id_now()
        if card and card != self.state["card"]:
            self.events.append(("card", card))
        self.state["card"] = card

    def _read_slow(self):
        if self.weather:
            try:
                with self.lock:
                    self.state["temperature"], self.state["humidity"] = self.weather.read()
            except Exception:
                pass
        if self.light:
            try:
                with self.lock:
                    self.state["light"] = self.light.read()
            except Exception:
                pass

    # ---- the outputs: each one is safe to call even if that part is missing ----
    def beep(self, seconds=0.08, times=1):
        with self.lock:
            if self.buzzer:
                self.buzzer.beep(on_time=seconds, off_time=0.08, n=times)
            elif self.pwm:
                self.pwm.frequency = 880
                self.pwm.blink(on_time=seconds, off_time=0.08, n=times)

    # ---- notes for the piano: the buzzer switched on and off very fast ----
    def use_tones(self):
        with self.lock:
            if self.pwm:
                return
            if self.buzzer:
                self.buzzer.close()
                self.buzzer = None
            try:
                self.pwm = PWMOutputDevice(18, frequency=440)
            except Exception as problem:
                self.missing["buzzer"] = str(problem)

    def tone(self, frequency):
        if self.pwm:
            with self.lock:
                self.pwm.frequency = frequency
                self.pwm.value = 0.5

    def quiet(self):
        if self.pwm:
            with self.lock:
                self.pwm.value = 0

    def buzzing(self):
        try:
            device = self.buzzer or self.pwm
            return bool(device and device.value)
        except Exception:
            return False

    # ---- RFID card text (runs in the background: the card has to be held still) ----
    def read_card_text(self, done, timeout=8):
        def work():
            try:
                with self.lock:
                    card, text = self.cards.read_text(timeout=timeout)
                done(card, text)
            except Exception as problem:
                print("RFID:", problem)
                done(None, None)
        threading.Thread(target=work, daemon=True).start()

    def write_card_text(self, text, done, timeout=8):
        def work():
            try:
                with self.lock:
                    card = self.cards.write_text(text, timeout=timeout)
                done(card)
            except Exception as problem:
                print("RFID:", problem)
                done(None)
        threading.Thread(target=work, daemon=True).start()

    def backlight(self, on):
        if self.lcd:
            try:
                with self.lock:
                    self.lcd.backlight(on)
            except Exception:
                pass

    def show_time(self, hours, minutes, colon=True):
        self.seg_number = None
        self.seg_text = "%02d:%02d" % (hours, minutes) if colon else "%02d %02d" % (hours, minutes)
        if self.seg:
            try:
                with self.lock:
                    self.seg.show_time(hours, minutes, colon)
            except Exception:
                pass

    def say(self, line1, line2=""):
        if self.lcd and (line1, line2) != self.lcd_text:
            self.lcd_text = (line1, line2)
            try:
                with self.lock:
                    self.lcd.show(line1[:16], line2[:16])
            except Exception:
                pass

    def show_number(self, number):
        number = int(round(number))
        if self.seg and number != self.seg_number:
            self.seg_number = number
            self.seg_text = str(max(-999, min(9999, number)))
            try:
                with self.lock:
                    self.seg.show_number(number)
            except Exception:
                pass

    def show_text(self, text):
        self.seg_number = None
        self.seg_text = text.strip()
        if self.seg:
            try:
                with self.lock:
                    self.seg.show(text)
            except Exception:
                pass

    def picture(self, rows):
        self.matrix_rows = [str(r)[:8].ljust(8, ".") for r in rows[:8]]
        if self.matrix:
            try:
                with self.lock:
                    draw_picture(self.matrix, rows)
            except Exception:
                pass

    def squares(self, buttons):
        """Light the 2x2 squares for these grid buttons (1-16)."""
        rows = [["."] * 8 for _ in range(8)]
        for button in buttons:
            row, column = ButtonMatrix.row_and_column(button)
            for y in (row * 2, row * 2 + 1):
                for x in (column * 2, column * 2 + 1):
                    rows[y][x] = "X"
        self.picture(["".join(r) for r in rows])

    def dots(self, points):
        rows = [["."] * 8 for _ in range(8)]
        for x, y in points:
            if 0 <= x < 8 and 0 <= y < 8:
                rows[y][x] = "X"
        self.picture(["".join(r) for r in rows])

    def clear_matrix(self):
        self.matrix_rows = ["........"] * 8
        if self.matrix:
            try:
                with self.lock:
                    self.matrix.clear()
            except Exception:
                pass

    def scroll(self, text):
        if self.matrix:
            def work():
                try:
                    with self.lock:
                        scroll_text(self.matrix, text)
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()

    def relay_on(self, on):
        if self.relay:
            with self.lock:
                self.relay.value = 1 if on else 0

    def take_photo(self, done):
        """Take a photo in the background; done(path or None) is called afterwards."""
        def work():
            try:
                done(crowpi_kit.take_photo())
            except Exception as problem:
                print("camera:", problem)
                done(None)
        threading.Thread(target=work, daemon=True).start()

    def close(self):
        self.running = False
        time.sleep(0.1)
        self.say("CROWPI CLUB", "See you soon!")
        self.clear_matrix()
        self.show_text("    ")
        self.relay_on(False)
        self.quiet()


# ==================================================================
#  THE WINDOW
# ==================================================================
class ShowApp:
    """The big window: a title, tabs along the top, and one screen below."""
    def __init__(self, root, kit, title, tabs, lesson=None, games_locked=False):
        self.root = root
        self.kit = kit
        self.title = title
        self.tabs = tabs                     # [(tab name, Screen class, colour), ...]
        self.lesson = lesson
        self.games_locked = games_locked
        root.title(title)
        root.configure(bg=BG)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = sw, sh - 80                       # the whole screen under the top bar
        root.geometry("%dx%d+0+0" % (width, height))
        try:
            root.attributes("-zoomed", True)              # maximized: fills the screen, the top bar stays
        except tk.TclError:
            pass
        root.minsize(760, 480)
        self.scale = max(0.76, min(2.2, min(width / 1000, height / 520)))
        self.scores = self.load_scores()
        self.screen = None
        self.games_unlocked = False
        self.photos_taken: list = []              # photos from this lesson (the yearbook picks one)
        self.yearbook_done = True
        self.yearbook_window = None
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build_header()
        self.build_footer()
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=12, pady=(4, 2))
        self.open(tabs[0][1])
        self.root.after(50, self.tick)

    # ---------- helpers ----------
    def font(self, size, bold=True):
        return ("DejaVu Sans", max(8, int(size * self.scale)), "bold" if bold else "normal")

    def label(self, parent, text="", size=11, color=TEXT, bold=False, bg=None, **options):
        return tk.Label(parent, text=text, fg=color, bg=bg or parent["bg"], font=self.font(size, bold),
                        **options)

    def button(self, parent, text, command, color=PANEL_2, size=11, **options):
        return tk.Button(parent, text=text, command=command, bg=color, fg=WHITE,
                         activebackground=color, activeforeground=WHITE, relief="flat", bd=0,
                         font=self.font(size), cursor="hand2", **options)

    def load_scores(self):
        try:
            with open(SCORES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_score(self, game, score, lower_is_better=False):
        """Returns True for a new high score."""
        best = self.scores.get(game)
        if not lower_is_better and score <= 0:
            return False                      # 0 points is never a high score
        better = best is None or (score < best if lower_is_better else score > best)
        if better:
            self.scores[game] = score
            try:
                with open(SCORES_FILE, "w", encoding="utf-8") as f:
                    json.dump(self.scores, f)
            except OSError:
                pass
        return better

    # ---------- header ----------
    def build_header(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=12, pady=(8, 2))
        self.label(header, self.title.upper(), 20 if len(self.title) < 22 else 16, WHITE,
                   True).pack(side="left")
        self.nav = {}
        for name, screen, color in reversed(self.tabs):
            b = self.button(header, name, lambda s=screen: self.open(s), color, 11, padx=12, pady=4)
            b.pack(side="right", padx=4)
            self.nav[screen] = b
        ready = 11 - len([m for m in self.kit.missing if m not in ("arrow buttons", "button grid")])
        self.parts_label = self.label(header, "%d of 11 parts ready" % ready, 10, YELLOW, True,
                                      cursor="hand2")
        if self.lesson is None:
            self.parts_label.pack(side="left", padx=14)
        self.parts_label.bind("<Button-1>", lambda _e: self.show_missing())

    # ---------- footer: Close, Shut down, Back, Next ----------
    def build_footer(self):
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        self.button(footer, "Close lesson", self.close_all, RED, 12, padx=14, pady=4).pack(side="left")
        self.button(footer, "Shut down", self.shut_down, PANEL_2, 10, padx=12, pady=4).pack(side="left", padx=8)
        self.next_button = self.button(footer, "Next  >", self.go_next, GREEN, 13, padx=18, pady=4)
        self.next_button.pack(side="right")
        self.back_button = self.button(footer, "<  Back", self.go_back, PANEL_2, 12, padx=14, pady=4)
        self.back_button.pack(side="right", padx=6)

    def tab_index(self):
        classes = [t[1] for t in self.tabs]
        return classes.index(type(self.screen)) if type(self.screen) in classes else -1

    def next_lesson_file(self):
        """The file of the next club day (only from a lesson's last tab)."""
        lesson = self.lesson
        day = getattr(lesson, "day", 0) if lesson is not None else 0
        if not day or not isinstance(lesson, Lesson):
            return None
        days = {l.day: l.key for l in LESSONS.values() if l.day}
        days[22] = "crowpi_show.py"
        name = days.get(day + 1)
        return name if name and os.path.exists(os.path.join(HERE, name)) else None

    def update_footer(self):
        i = self.tab_index()
        self.next_button.pack_forget()
        self.back_button.pack_forget()
        if i < len(self.tabs) - 1:
            self.next_button.config(text="Next:  %s  >" % self.tabs[i + 1][0])
            self.next_button.pack(side="right")
        elif self.next_lesson_file():
            self.next_button.config(text="Next lesson  >")
            self.next_button.pack(side="right")
        if i > 0:
            self.back_button.pack(side="right", padx=6)

    def go_next(self):
        i = self.tab_index()
        if i < len(self.tabs) - 1:
            self.open(self.tabs[i + 1][1])
        elif self.next_lesson_file():
            self.open_next_lesson()

    def go_back(self):
        i = self.tab_index()
        if i > 0:
            self.open(self.tabs[i - 1][1])

    def open_next_lesson(self):
        path = os.path.join(HERE, self.next_lesson_file() or "")
        print("[app] next lesson:", os.path.basename(path))
        thonny = thonny_process()
        from_app = bool(os.environ.get("CROWPI_FROM_APP"))          # kiosk: CrowPi Lessons starts it
        note = os.path.join(os.path.expanduser("~"), ".crowpi_next_lesson") if from_app else REQUEST_FILE
        try:
            with open(note, "w", encoding="utf-8") as f:
                json.dump({"file": path, "time": time.time()}, f)     # Thonny (or the app) opens and runs it
        except OSError:
            pass
        self.close()
        if thonny is None and not from_app and not TESTING:        # (not started from Thonny)
            subprocess.Popen([sys.executable, path], start_new_session=True)

    def close_all(self):
        """Close the lesson AND Thonny (the CrowPi Lessons app stays open)."""
        thonny = thonny_process()
        print("[app] close (Thonny: %s)" % thonny)
        self.close()
        if thonny:
            try:
                os.kill(thonny, signal.SIGTERM)
            except OSError:
                pass
            print("See you next time!")
            sys.stdout.flush()
            os._exit(0)                   # (this lesson ran inside Thonny - nothing is left behind)

    def shut_down(self):
        from tkinter import messagebox
        if not TESTING and not messagebox.askyesno(
                "Shut down", "Shut down the CrowPi now?\nWait until the green light stops blinking "
                "before you switch it off.", parent=self.root):
            return
        print("[app] shut down")
        self.close()
        if TESTING:
            return
        try:
            if subprocess.call(["systemctl", "poweroff"], timeout=20) != 0:
                subprocess.call(["sudo", "-n", "shutdown", "-h", "now"], timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass

    def ask_password(self):
        """The games are locked: ask for the admin password. True if it's right."""
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Admin password")
        window.transient(self.root)
        self.label(window, "Games are locked", 16, WHITE, True).pack(padx=24, pady=(14, 4))
        self.label(window, "Type the admin password:", 12).pack(padx=24)
        entry = tk.Entry(window, show="*", font=self.font(14, False), width=16)
        entry.pack(padx=24, pady=8)
        note = self.label(window, "", 11, "#FF8A80", True)
        note.pack()
        answer = {"ok": False}

        def check(_event=None):
            if entry.get().strip().lower() == "reset":          # "reset" = clear the high scores
                entry.delete(0, "end")
                from tkinter import messagebox
                if messagebox.askyesno("Reset high scores", "Reset ALL the game high scores on this kit?",
                                       parent=window):
                    self.scores = {}
                    try:
                        os.remove(SCORES_FILE)
                    except OSError:
                        pass
                    note.config(text="High scores reset!", fg="#7CFFB0")
                return
            if hashlib.sha256(entry.get().encode("utf-8")).hexdigest() == GAMES_PASSWORD_SHA256:
                answer["ok"] = True
                window.destroy()
            else:
                note.config(text="Wrong password")
                entry.delete(0, "end")

        row = tk.Frame(window, bg=BG)
        row.pack(pady=(6, 14))
        self.button(row, "Unlock", check, GREEN, 13, padx=16, pady=6).pack(side="left", padx=8)
        self.button(row, "Cancel", window.destroy, PANEL_2, 13, padx=16, pady=6).pack(side="left", padx=8)
        entry.bind("<Return>", check)
        window.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_height()) // 3
        window.geometry("+%d+%d" % (max(0, x), max(0, y)))
        try:
            window.grab_set()
        except tk.TclError:
            pass
        entry.focus_force()                   # ready to type straight away
        self.root.wait_window(window)
        return answer["ok"]

    def show_missing(self):
        window = tk.Toplevel(self.root, bg=BG)
        window.title("Parts")
        text = "\n".join("%s: %s" % (k, v[:60]) for k, v in self.kit.missing.items()) or \
            "Every part started!"
        self.label(window, "Parts that didn't start", 14, WHITE, True).pack(padx=20, pady=(14, 6))
        self.label(window, text, 11, justify="left").pack(padx=20, pady=4)
        self.button(window, "OK", window.destroy, GREEN, 12, padx=18, pady=4).pack(pady=12)

    # ---------- screens ----------
    def next_tab(self, screen_class):
        """The tab after this one (the switch check's Continue button goes there)."""
        classes = [t[1] for t in self.tabs]
        i = classes.index(screen_class) if screen_class in classes else -1
        self.open(classes[min(i + 1, len(classes) - 1)])

    def open(self, screen_class):
        if screen_class is GamesScreen and self.games_locked and not self.games_unlocked:
            if not self.ask_password():
                return
            self.games_unlocked = True
        if self.screen:
            self.screen.leave()
        for child in self.body.winfo_children():
            child.destroy()
        self.screen = screen_class(self, self.body)
        for screen, b in self.nav.items():
            b.config(relief="sunken" if screen is screen_class else "flat",
                     bd=3 if screen is screen_class else 0)
        print("[screen]", screen_class.__name__)
        self.update_footer()

    def tick(self):
        events = []
        while self.kit.events:
            events.append(self.kit.events.popleft())
        try:
            if self.screen:
                self.screen.tick(events)
                mirror = getattr(self.screen, "mirror", None)
                if mirror:
                    mirror.update()
        except tk.TclError:
            pass
        if TESTING:
            self.test_commands()
        self.root.after(50, self.tick)

    def test_commands(self):
        """(Only for testing the app: does what a student would click.)"""
        path = os.environ.get("CROWPI_UI", "")
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            return
        done = getattr(self, "ui_done", 0)
        self.ui_done = len(lines)
        for line in lines[done:]:
            what, _, arg = line.partition(" ")
            screen = self.screen
            act = None
            if isinstance(screen, ChallengeScreen) and 0 <= screen.index < len(screen.acts):
                act = screen.acts[screen.index]
            try:
                if what == "tab":
                    self.open(self.tabs[int(arg)][1])
                elif what == "start" and isinstance(screen, ChallengeScreen):
                    screen.start_show()
                elif what == "answer" and isinstance(act, Quiz):
                    act.pick(act.answer)
                elif what == "entry" and isinstance(act, Entry):
                    act.entry.delete(0, "end")
                    act.entry.insert(0, arg)
                    act.send()
                elif what == "draw" and isinstance(act, Draw):
                    act.editor.set(arg.split(","))
                elif what == "photo" and isinstance(act, Photo):
                    act.countdown()
                elif what == "field" and isinstance(screen, CodeScreen):
                    name, _, value = arg.partition(" ")
                    screen.vars[name].set(value)
                elif what in ("next", "back", "closeall", "shutdown"):
                    {"next": self.go_next, "back": self.go_back, "closeall": self.close_all,
                     "shutdown": self.shut_down}[what]()
                    return
                elif what == "upload" and isinstance(screen, CodeScreen):
                    screen.upload()
                elif what == "stopcode" and isinstance(screen, CodeScreen):
                    screen.stop()
                print("[ui]", line)
            except Exception as problem:
                print("[ui] FAILED", line, problem)

    def close(self):
        if getattr(self, "closed", False):
            return
        if [p for p in self.photos_taken if os.path.exists(p)] and self.yearbook_window is None:
            choose_yearbook_photo(self)          # (photos not chosen yet: ask before closing)
        self.closed = True
        if self.screen:
            self.screen.leave()
        self.kit.close()
        self.root.destroy()


class Screen:
    """One page of the app. tick() is called 20 times a second with what happened on the kit."""
    def __init__(self, app, parent):
        self.app = app
        self.kit = app.kit
        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(fill="both", expand=True)
        self.build()

    def build(self):
        pass

    def tick(self, events):
        pass

    def leave(self):
        pass


# ==================================================================
#  1. SWITCH CHECK
# ==================================================================
class SwitchScreen(Screen):
    def build(self):
        app = self.app
        self.confirmed = False
        self.arrow_name = ""
        self.button_number = 0
        app.label(self.frame, "Step 1: set the DIP switches to BUTTON MODE", 17, WHITE, True).pack(
            anchor="w", pady=(4, 2))
        app.label(self.frame, "The two blocks of 8 tiny switches near the bottom of the board. "
                              "Flip them only while nothing is running.", 11).pack(anchor="w")
        self.canvas = tk.Canvas(self.frame, bg=PANEL, highlightthickness=0,
                                height=int(210 * app.scale))
        self.canvas.pack(fill="x", pady=10)
        self.status = app.label(self.frame, "", 15, YELLOW, True, justify="left")
        self.status.pack(anchor="w", pady=(4, 8))
        row = tk.Frame(self.frame, bg=BG)
        row.pack(anchor="w")
        self.go = app.button(row, "Continue", lambda: app.next_tab(SwitchScreen),
                             PANEL_2, 13, padx=24, pady=8)
        self.go.pack(side="left")
        self.grid_mode = getattr(app.lesson, "mode", "arrows") == "grid"
        if self.grid_mode:
            self.kit.use_grid()
            self.kit.say("Set BUTTON MODE", "press a button")
        else:
            self.kit.use_arrows()
            self.kit.say("Set BUTTON MODE", "then press arrow")
        self.kit.show_text("SET")
        self.frame.after(60, self.draw)

    def draw(self):
        c = self.canvas
        c.delete("all")
        width = c.winfo_width() or 900
        s = self.app.scale
        for block, (title, up, color) in enumerate(
                (("LEFT block (UX1) - label starts with BUTTON:  all 8 UP", True, GREEN),
                 ("RIGHT block (UX5) - starts with 1:13-VIBRATION:  all 8 DOWN", False, ORANGE))):
            x0 = 30 * s + block * width / 2
            c.create_text(x0, 22 * s, text=title, anchor="w", fill=WHITE,
                          font=self.app.font(10), width=width / 2 - 50 * s)
            for i in range(8):
                x = x0 + i * 46 * s
                c.create_rectangle(x, 55 * s, x + 32 * s, 150 * s, fill="#E8EDF5", outline="")
                knob_y = 60 * s if up else 105 * s
                c.create_rectangle(x + 4 * s, knob_y, x + 28 * s, knob_y + 40 * s, fill=color, outline="")
                c.create_text(x + 16 * s, 168 * s, text=str(i + 1), fill=TEXT, font=self.app.font(10))
            c.create_text(x0 + 372 * s, 102 * s, text="UP" if up else "DOWN", fill=color,
                          font=self.app.font(16), anchor="w")
        c.create_text(width / 2, 196 * s, text="ON = UP.   Never have both blocks all UP.",
                      fill=YELLOW, font=self.app.font(10))

    def tick(self, events):
        kit = self.kit
        if self.grid_mode:
            for what, detail in events:
                if what == "grid" and not self.confirmed:
                    self.confirmed = True
                    kit.beep(0.1, 2)
                    kit.say("Switches OK!", "BUTTON MODE")
                    kit.picture(CHECK)
                    kit.show_text("GOOD")
                    self.go.config(bg=GREEN, activebackground=GREEN)
                    self.button_number = detail
            if self.confirmed:
                self.status.config(text="BUTTON MODE confirmed - button %d worked!  Click Continue."
                                   % self.button_number, fg=GREEN_2)
            else:
                self.status.config(text="Now press any button in the 16-button grid to test the "
                                        "switches...\nNothing happens? Check the switches again.",
                                   fg=YELLOW)
            return
        if not kit.arrows:
            self.status.config(text="The arrow buttons didn't start, so the switches can't be checked.\n"
                                    "Set BUTTON MODE as shown and click Continue.", fg=YELLOW)
            return
        for what, detail in events:
            if what == "arrow" and not self.confirmed:
                self.arrow_name = detail
                self.confirmed = True
                kit.beep(0.1, 2)
                kit.say("Switches OK!", "BUTTON MODE")
                kit.picture(CHECK)
                kit.show_text("GOOD")
                self.go.config(bg=GREEN, activebackground=GREEN)
        if self.confirmed:
            self.status.config(text="BUTTON MODE confirmed - the %s arrow worked!  Click Continue."
                               % self.arrow_name, fg=GREEN_2)
        elif len(kit.state["arrows"]) == 4:
            self.status.config(text="All four arrow buttons read 'pressed' - the RIGHT block is UP.\n"
                                    "Flip all 8 RIGHT switches DOWN and all 8 LEFT switches UP.", fg=RED)
        else:
            self.status.config(text="Now press any arrow button on the kit to test the switches...",
                               fg=YELLOW)


# ==================================================================
#  2. DASHBOARD: every sensor, live
# ==================================================================
class DashboardScreen(Screen):
    def build(self):
        app = self.app
        self.kit.use_arrows()
        grid = tk.Frame(self.frame, bg=BG)
        grid.pack(fill="both", expand=True)
        self.values = {}
        tiles = [("Temperature", "weather"), ("Humidity", "weather"), ("Light", "light"),
                 ("Distance", "distance"), ("7-segment", "seg"), ("Touch", "touch"),
                 ("Arrow buttons", "arrows"), ("IR remote", "remote"), ("RFID card", "cards"),
                 ("Buzzer + relay", "buzzer"), ("LED matrix", "matrix"), ("LCD screen", "lcd")]
        for i, (title, part) in enumerate(tiles):
            tile = tk.Frame(grid, bg=PANEL, padx=10, pady=6)
            tile.grid(row=i // 4, column=i % 4, padx=5, pady=5, sticky="nsew")
            app.label(tile, title.upper(), 9, YELLOW, True).pack(anchor="w")
            body = tk.Frame(tile, bg=PANEL)
            body.pack(fill="both", expand=True)
            present = bool(self.kit.arrows) if part == "arrows" else getattr(self.kit, part, None)
            if not present:
                app.label(body, "not found", 14, GREY, True).pack(anchor="w", pady=10)
                continue
            if title == "Buzzer + relay":
                row = tk.Frame(body, bg=PANEL)
                row.pack(anchor="w", pady=8)
                app.button(row, "Beep", lambda: self.kit.beep(0.15), GREEN, 11, padx=10,
                           pady=4).pack(side="left", padx=(0, 6))
                app.button(row, "Click relay", self.click_relay, BLUE, 11, padx=10,
                           pady=4).pack(side="left")
            elif title == "LED matrix":
                row = tk.Frame(body, bg=PANEL)
                row.pack(anchor="w", pady=8)
                for name, rows in (("Heart", HEART), ("Smile", SMILE), ("Star", STAR)):
                    app.button(row, name, lambda r=rows: self.kit.picture(r), PURPLE, 10, padx=6,
                               pady=4).pack(side="left", padx=(0, 4))
            elif title == "7-segment":
                row = tk.Frame(body, bg=PANEL)
                row.pack(anchor="w", pady=8)
                app.button(row, "Dice", self.dice, ORANGE, 10, padx=8, pady=4).pack(side="left", padx=(0, 4))
                app.button(row, "Clock", self.clock, BLUE, 10, padx=8, pady=4).pack(side="left")
            elif title == "LCD screen":
                self.entry = tk.Entry(body, font=app.font(11, False), width=16)
                self.entry.insert(0, "Hello CrowPi!")
                self.entry.pack(anchor="w", pady=(6, 3))
                app.button(body, "Show on LCD", self.send, ORANGE, 10, padx=8, pady=2).pack(anchor="w")
            else:
                value = app.label(body, "...", 20, WHITE, True)
                value.pack(anchor="w")
                note = app.label(body, "", 9)
                note.pack(anchor="w")
                bar = tk.Canvas(body, height=10, bg=PANEL_2, highlightthickness=0)
                bar.pack(fill="x", pady=(3, 0))
                self.values[title] = (value, note, bar)
        for c in range(4):
            grid.columnconfigure(c, weight=1, uniform="tiles")
        for r in range(3):
            grid.rowconfigure(r, weight=1, uniform="tiles")
        self.message_until = 0.0
        self.seg_until = 0.0
        self.touches = 0

    def dice(self):
        self.seg_until = time.time() + 4
        self.kit.show_number(random.randint(1, 6))
        self.kit.beep(0.05)

    def clock(self):
        self.seg_until = time.time() + 4
        now = time.localtime()
        self.kit.show_time(now.tm_hour, now.tm_min)

    def click_relay(self):
        self.kit.relay_on(True)
        self.frame.after(300, lambda: self.kit.relay_on(False))

    def send(self):
        text = self.entry.get()
        self.kit.say(text[:16], text[16:32])
        self.message_until = time.time() + 10

    def set(self, title, value, note="", fraction=None, color=GREEN):
        if title not in self.values:
            return
        label, small, bar = self.values[title]
        label.config(text=value)
        small.config(text=note)
        bar.delete("all")
        if fraction is not None:
            bar.create_rectangle(0, 0, max(2, bar.winfo_width() * max(0.0, min(1.0, fraction))), 10,
                                 fill=color, outline="")

    def tick(self, events):
        s = self.kit.state
        kit = self.kit
        for what, detail in events:
            if what == "arrow":
                kit.picture(ARROWS[detail])
                kit.beep(0.04)
            elif what == "touch":
                self.touches += 1
                kit.picture(HEART)
            elif what == "ir":
                kit.beep(0.04)
            elif what == "card":
                kit.beep(0.2)
                kit.say("Card:", str(detail)[-12:])
                self.message_until = time.time() + 4
        t, h, light, d = s["temperature"], s["humidity"], s["light"], s["distance"]
        if t is not None:
            self.set("Temperature", "%.1f °C" % t, "breathe on it to warm it", (t - 10) / 30, ORANGE)
            self.set("Humidity", "%d %%" % h, "air wetness", h / 100, BLUE)
        if light is not None:
            self.set("Light", "%d lux" % light, "cover it with your hand", min(1.0, light / 800), YELLOW)
        if d is not None:
            self.set("Distance", "%.0f cm" % d, "hand in front of the eyes", 1 - min(1.0, d / 100), GREEN)
            if time.time() > self.seg_until:
                kit.show_number(d)
        self.set("Touch", "TOUCHED" if s["touch"] else "-", "touches: %d" % self.touches,
                 1.0 if s["touch"] else 0.0, PURPLE)
        arrows = " ".join(("[%s]" % n[0]) if n in s["arrows"] else n[0] for n in ARROW_PINS)
        self.set("Arrow buttons", arrows, "pressed = [ ]")
        self.set("IR remote", s["ir"] or "-", "press a remote button")
        self.set("RFID card", str(s["card"])[-8:] if s["card"] else "-", "tap a card")
        if time.time() > self.message_until and t is not None:
            kit.say("Temp %.1f C" % t, "Humidity %d %%" % h)


class KitMirror:
    """The kit's LED matrix, LCD and 7-segment display, drawn on the screen."""
    def __init__(self, app, parent, small=False):
        self.app = app
        self.kit = app.kit
        s = app.scale * (0.7 if small else 1)
        self.frame = tk.Frame(parent, bg=PANEL, padx=12, pady=8 if not small else 4)
        app.label(self.frame, "ON THE KIT RIGHT NOW", 9, YELLOW, True).grid(row=0, column=0,
                                                                            columnspan=3, sticky="w")
        self.dot = int(20 * s)
        self.matrix = tk.Canvas(self.frame, width=self.dot * 8 + 8, height=self.dot * 8 + 8,
                                bg="#111111", highlightthickness=0)
        self.matrix.grid(row=1, column=0, rowspan=2, padx=(0, 16), pady=4)
        self.lcd = tk.Label(self.frame, text="", bg="#7FB241", fg="#1E2B12", justify="left",
                            font=("DejaVu Sans Mono", max(9, int(17 * s)), "bold"), width=16,
                            anchor="w", padx=8, pady=4)
        self.lcd.grid(row=1, column=1, sticky="w", pady=4)
        self.seg = tk.Label(self.frame, text="", bg="#140404", fg="#FF3B30", width=5,
                            font=("DejaVu Sans Mono", max(12, int(34 * s)), "bold"), padx=8)
        self.seg.grid(row=2, column=1, sticky="w")
        self.shown = None

    def update(self):
        kit = self.kit
        now = (tuple(kit.matrix_rows), kit.lcd_text, kit.seg_text)
        if now == self.shown:
            return
        self.shown = now
        c, d = self.matrix, self.dot
        c.delete("all")
        for y, row in enumerate(kit.matrix_rows):
            for x, ch in enumerate(row):
                on = ch not in " .0_-"
                c.create_oval(4 + x * d + 1, 4 + y * d + 1, 4 + (x + 1) * d - 1, 4 + (y + 1) * d - 1,
                              fill="#FF3030" if on else "#3A1010", outline="")
        self.lcd.config(text="%-16s\n%-16s" % kit.lcd_text)
        self.seg.config(text=kit.seg_text.rjust(4)[:5])


# ==================================================================
#  3. THE SHOW: one act for every part
# ==================================================================
class Act:
    title = ""
    needs: tuple = ()
    auto = False                       # True = runs by itself, no action needed

    def __init__(self, show):
        self.show = show
        self.kit = show.kit
        self.started = 0.0
        self.skipped = False                # set when the part stops working mid-act

    def available(self):
        for need in self.needs:
            if need == "camera":
                if not self.kit.camera or self.kit.camera == "looking":
                    return False
            elif need == "buzzer":
                if not (self.kit.buzzer or self.kit.pwm):
                    return False
            elif not getattr(self.kit, need, None):
                return False
        return True

    def start(self):
        self.started = time.time()

    def instruction(self):
        return ""

    def tick(self, events):
        """Return True when the act is finished."""
        del events                          # (each act uses the kit's events its own way)
        return False

    def live(self):
        return ""

    def progress(self):
        return 0.0


class Countdown(Act):
    title = "Countdown"
    needs = ("seg",)
    auto = True

    def instruction(self):
        return "3... 2... 1... The show is starting!"

    def tick(self, events):
        second = int(time.time() - self.started)
        if second < 3:
            if getattr(self, "shown", None) != second:
                self.shown = second
                self.kit.show_number(3 - second)
                self.kit.beep(0.08)
                self.kit.say("THE CROWPI SHOW", "Starting in %d" % (3 - second))
            return False
        self.kit.show_text("GO")
        self.kit.beep(0.4)
        return True

    def progress(self):
        return (time.time() - self.started) / 3


class RelayAct(Act):
    title = "Relay"
    needs = ("relay",)
    auto = True

    def instruction(self):
        return "Listen: the relay is an electric switch. Click... click... click!"

    def tick(self, events):
        step = int((time.time() - self.started) / 0.35)
        self.kit.relay_on(step % 2 == 0 and step < 6)
        self.kit.say("Act: RELAY", "click click click")
        return step >= 7

    def progress(self):
        return (time.time() - self.started) / 2.4


class MatrixAct(Act):
    title = "LED matrix"
    needs = ("matrix",)
    auto = True

    def instruction(self):
        return "Look at the LED matrix: 64 tiny lights make pictures!"

    def tick(self, events):
        pictures = [HEART, SMILE, STAR, ARROWS["UP"], ARROWS["RIGHT"], ARROWS["DOWN"], ARROWS["LEFT"]]
        i = int((time.time() - self.started) / 0.5)
        if i < len(pictures):
            if getattr(self, "shown", None) != i:
                self.shown = i
                self.kit.picture(pictures[i])
                self.kit.say("Act: LED MATRIX", "64 lights!")
            return False
        return True

    def progress(self):
        return (time.time() - self.started) / 3.5


class ArrowAct(Act):
    title = "Arrow buttons"

    def start(self):
        super().start()
        self.kit.use_arrows()
        self.got: set = set()
        self.kit.say("Press all four", "arrow buttons!")

    def available(self):
        self.kit.use_arrows()
        return bool(self.kit.arrows)

    def instruction(self):
        return "Press all 4 arrow buttons: UP, DOWN, LEFT and RIGHT."

    def tick(self, events):
        for what, detail in events:
            if what == "arrow":
                self.got.add(detail)
                self.kit.picture(ARROWS[detail])
                self.kit.beep(0.05)
        return len(self.got) == 4

    def live(self):
        return "Pressed: " + (", ".join(sorted(self.got)) or "none yet")

    def progress(self):
        return len(self.got) / 4


class TouchAct(Act):
    title = "Touch sensor"
    needs = ("touch",)

    def start(self):
        super().start()
        self.count = 0
        self.kit.say("Touch the pad", "3 times!")

    def instruction(self):
        return "Touch the round touch pad 3 times."

    def tick(self, events):
        for what, _ in events:
            if what == "touch":
                self.count += 1
                self.kit.show_number(self.count)
                self.kit.picture(HEART)
                self.kit.beep(0.05)
        return self.count >= 3

    def live(self):
        return "Touches: %d of 3" % self.count

    def progress(self):
        return self.count / 3


class DistanceAct(Act):
    title = "Distance sensor"
    needs = ("distance",)

    def start(self):
        super().start()
        self.kit.say("Hand closer than", "10 cm!")

    def instruction(self):
        return "Move your hand closer than 10 cm to the two silver 'eyes'."

    def tick(self, events):
        d = self.kit.state["distance"]
        if d is not None:
            self.kit.show_number(d)
            if d < 10:
                self.kit.beep(0.15)
                return True
        return False

    def live(self):
        d = self.kit.state["distance"]
        return "Distance: %.0f cm" % d if d is not None else ""

    def progress(self):
        d = self.kit.state["distance"]
        return 0.0 if d is None else 1 - min(1.0, max(0.0, (d - 10) / 60))


class LightAct(Act):
    title = "Light sensor"
    needs = ("light",)

    def start(self):
        super().start()
        self.start_light = self.kit.state["light"]
        self.kit.say("Cover the light", "sensor!")

    def instruction(self):
        return "Make it dark: cover the light sensor with your hand."

    def tick(self, events):
        light = self.kit.state["light"]
        if light is None:
            return False
        if self.start_light is None:
            self.start_light = light
        if light < max(3.0, self.start_light * 0.4):
            self.kit.beep(0.15)
            return True
        return False

    def live(self):
        light = self.kit.state["light"]
        return "Light: %d lux" % light if light is not None else ""

    def progress(self):
        light = self.kit.state["light"]
        if light is None or not self.start_light:
            return 0.0
        return min(1.0, (1 - light / self.start_light) / 0.6)


class WeatherAct(Act):
    title = "Temperature sensor"
    needs = ("weather",)

    def start(self):
        super().start()
        self.start_t = self.kit.state["temperature"]
        self.start_h = self.kit.state["humidity"]
        self.kit.say("Breathe on the", "temp sensor!")

    def instruction(self):
        return "Breathe gently on the temperature sensor (warm, wet air!)."

    def tick(self, events):
        t, h = self.kit.state["temperature"], self.kit.state["humidity"]
        if t is None:
            return False
        if self.start_t is None:
            self.start_t, self.start_h = t, h
        if t - self.start_t >= 0.5 or h - self.start_h >= 3:
            self.kit.beep(0.15)
            return True
        return False

    def live(self):
        t, h = self.kit.state["temperature"], self.kit.state["humidity"]
        return "%.1f °C   %d %% humidity" % (t, h) if t is not None else "reading..."

    def progress(self):
        t, h = self.kit.state["temperature"], self.kit.state["humidity"]
        if t is None or self.start_t is None:
            return 0.0
        return max((t - self.start_t) / 0.5, (h - self.start_h) / 3, 0.0)


class GridAct(Act):
    title = "Button grid"

    def start(self):
        super().start()
        self.kit.use_grid()
        self.hits = 0
        self.target = random.randint(1, 16)
        self.kit.squares([self.target])
        self.kit.say("Press the button", "that lights up!")

    def available(self):
        return True

    def instruction(self):
        return "Press the button under the square that lights up on the matrix (3 times)."

    def tick(self, events):
        if not self.kit.grid:
            self.skipped = True
            self.kit.use_arrows()
            return True
        for what, key in events:
            if what == "grid" and key == self.target:
                self.hits += 1
                self.kit.beep(0.05)
                self.target = random.choice([b for b in range(1, 17) if b != self.target])
                self.kit.squares([self.target])
        if self.hits >= 3:
            self.kit.use_arrows()
            return True
        return False

    def live(self):
        return "Button to press: %d   (hits: %d of 3)" % (self.target, self.hits)

    def progress(self):
        return self.hits / 3


class RemoteAct(Act):
    title = "IR remote"
    needs = ("remote",)

    def start(self):
        super().start()
        self.keys: set = set()
        self.kit.say("Press 3 remote", "buttons!")

    def instruction(self):
        return "Point the IR remote at the kit and press 3 different buttons."

    def tick(self, events):
        for what, key in events:
            if what == "ir":
                self.keys.add(key)
                self.kit.say("Remote:", key)
                self.kit.beep(0.05)
        return len(self.keys) >= 3

    def live(self):
        return "Buttons: " + (", ".join(sorted(self.keys)) or "none yet")

    def progress(self):
        return len(self.keys) / 3


class CardAct(Act):
    title = "RFID card"
    needs = ("cards",)

    def start(self):
        super().start()
        self.kit.say("Tap your card", "on the reader!")

    def instruction(self):
        return "Tap the white card or the blue tag on the RFID reader."

    def tick(self, events):
        for what, card in events:
            if what == "card":
                self.card = card
                self.kit.say("Card number:", str(card)[-12:])
                self.kit.beep(0.3)
                return True
        return False


class PhotoAct(Act):
    title = "Camera"
    needs = ("camera",)

    def start(self):
        super().start()
        self.photo = None
        self.taken = False
        self.finished = False
        self.kit.say("Everybody", "SMILE!")

    def instruction(self):
        return "Everybody SMILE! The camera takes a photo in 3 seconds..."

    def tick(self, events):
        second = int(time.time() - self.started)
        if second < 3:
            self.kit.show_number(3 - second)
            return False
        if not self.taken:
            self.taken = True
            self.kit.show_text("SNAP")
            self.kit.beep(0.3)
            self.kit.take_photo(self.got_photo)
        return self.finished

    def got_photo(self, path):
        self.photo = path
        self.show.photo = path
        self.finished = True

    def live(self):
        return "Click!" if self.taken else ""


ACTS = [Countdown, RelayAct, MatrixAct, ArrowAct, TouchAct, DistanceAct, LightAct,
        WeatherAct, GridAct, RemoteAct, CardAct, PhotoAct]


# ==================================================================
#  4. GAMES: on the screen AND the kit
# ==================================================================
GAMES = [("Snake", "arrow buttons + LED matrix", "Eat the dots, don't crash!", "SnakeGame"),
         ("Reaction", "touch pad + buzzer", "Touch when it says GO!", "ReactionGame"),
         ("Simon Says", "button grid + LED matrix", "Repeat the pattern", "SimonGame"),
         ("Whack-a-Mole", "button grid + LED matrix", "Hit the moles in 30 s", "WhackGame")]


class GamesScreen(Screen):
    def build(self):
        app = self.app
        self.kit.use_arrows()
        self.kit.say("CROWPI GAMES", "Pick a game!")
        self.kit.picture(STAR)
        app.label(self.frame, "Pick a game - it plays on the screen AND on the kit!", 15, WHITE,
                  True).pack(anchor="w", pady=(4, 8))
        grid = tk.Frame(self.frame, bg=BG)
        grid.pack(fill="both", expand=True)
        colors = [GREEN, ORANGE, BLUE, PURPLE]
        for i, (name, parts, about, cls) in enumerate(GAMES):
            card = tk.Frame(grid, bg=colors[i], cursor="hand2")
            card.grid(row=i // 2, column=i % 2, padx=8, pady=8, sticky="nsew")
            best = app.scores.get(name)
            unit = " ms" if name == "Reaction" else ""
            for text, size, bold in ((name, 22, True), (about, 13, False), (parts, 11, False),
                                     ("Best: %s%s" % (best, unit) if best is not None else "No score yet",
                                      12, True)):
                lab = app.label(card, text, size, WHITE, bold, bg=colors[i])
                lab.pack(anchor="w", padx=16, pady=2)
                lab.bind("<Button-1>", self.player(cls))
            card.bind("<Button-1>", self.player(cls))
        for c in range(2):
            grid.columnconfigure(c, weight=1, uniform="games")
        for r in range(2):
            grid.rowconfigure(r, weight=1, uniform="games")

    def player(self, class_name):
        """What happens when a game card is clicked."""
        def play(_event):
            self.app.open(globals()[class_name])
        return play


class Game(Screen):
    """Base for the games: a board on the left, the score on the right."""
    name = ""
    cells = 8

    def build(self):
        app = self.app
        top = tk.Frame(self.frame, bg=BG)
        top.pack(fill="x")
        in_games = any(t[1] is GamesScreen for t in app.tabs)
        if in_games:                          # (otherwise the window title already says it)
            app.label(top, self.name.upper(), 18, WHITE, True).pack(side="left")
        if in_games:
            app.button(top, "Back to games", lambda: app.open(GamesScreen), PANEL_2, 11, padx=12,
                       pady=4).pack(side="right")
        self.again = app.button(top, "Play again", self.restart, GREEN, 11, padx=12, pady=4)
        area = tk.Frame(self.frame, bg=BG)
        area.pack(fill="both", expand=True, pady=6)
        self.size = int(400 * app.scale)
        self.board = tk.Canvas(area, width=self.size, height=self.size, bg=PANEL, highlightthickness=0)
        self.board.pack(side="left")
        side = tk.Frame(area, bg=BG, padx=20)
        side.pack(side="left", fill="both", expand=True)
        self.score_label = app.label(side, "", 30, YELLOW, True)
        self.score_label.pack(anchor="w")
        self.message = app.label(side, "", 14, TEXT, wraplength=int(420 * app.scale), justify="left")
        self.message.pack(anchor="w", pady=8)
        best = app.scores.get(self.name)
        self.best_label = app.label(side, "", 12, GREEN_2, True)
        self.best_label.pack(anchor="w")
        self.mirror = KitMirror(app, side)
        self.mirror.frame.pack(anchor="w", pady=(14, 0))
        self.show_best(best)
        self.setup()
        self.restart()

    def show_best(self, best):
        self.best_label.config(text="Best: %s" % best if best is not None else "Best: no score yet")

    def setup(self):
        pass

    def restart(self):
        self.again.pack_forget()
        self.over = False

    def game_over(self, score, text, lower_is_better=False):
        self.over = True
        if self.app.save_score(self.name, score, lower_is_better):
            text += "\nNEW HIGH SCORE!"
            self.kit.beep(0.1, 3)
            self.kit.say("NEW HIGH SCORE!", str(score))
        else:
            self.kit.say("GAME OVER", "Score: %s" % score)
        self.show_best(self.app.scores.get(self.name))
        self.message.config(text=text)
        self.again.pack(side="right", padx=8)

    def cell(self, x, y, color, pad=3):
        size = self.size / self.cells
        self.board.create_rectangle(x * size + pad, y * size + pad, (x + 1) * size - pad,
                                    (y + 1) * size - pad, fill=color, outline="")

    def leave(self):
        self.kit.use_arrows()


class SnakeGame(Game):
    name = "Snake"
    moves = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
    opposite = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}

    def setup(self):
        self.kit.use_arrows()
        self.app.root.bind("<Key>", self.key)        # the keyboard arrows work too

    def restart(self):
        super().restart()
        self.snake = [(3, 4), (2, 4), (1, 4)]
        self.direction = "RIGHT"
        self.next_direction = "RIGHT"
        self.food = self.new_food()
        self.score = 0
        self.speed = 0.45
        self.waiting = True                   # starts when you press an arrow
        self.next_step = time.time()
        self.message.config(text="Press an arrow button to start! Steer with the arrow buttons "
                                 "(or the keyboard arrows). Eat the yellow dot!")
        self.kit.show_number(0)
        self.kit.say("SNAKE", "Score: 0")
        self.draw()

    def new_food(self):
        return random.choice([(x, y) for x in range(8) for y in range(8) if (x, y) not in self.snake])

    def key(self, event):
        if event.keysym.upper() in self.moves:
            self.turn(event.keysym.upper())

    def turn(self, name):
        if self.waiting:
            self.waiting = False
            self.next_step = time.time() + 0.2
        if name != self.opposite[self.direction]:
            self.next_direction = name

    def tick(self, events):
        for what, detail in events:
            if what == "arrow":
                self.turn(detail)
        if self.over or self.waiting or time.time() < self.next_step:
            return
        self.next_step = time.time() + self.speed
        self.direction = self.next_direction
        dx, dy = self.moves[self.direction]
        head = (self.snake[0][0] + dx, self.snake[0][1] + dy)
        if not (0 <= head[0] < 8 and 0 <= head[1] < 8) or head in self.snake:
            self.kit.beep(0.6)
            self.draw(crashed=True)
            self.game_over(self.score, "CRASH! You ate %d dots." % self.score)
            return
        self.snake.insert(0, head)
        if head == self.food:
            self.score += 1
            self.kit.show_number(self.score)
            self.kit.say("SNAKE", "Score: %d" % self.score)
            self.kit.beep(0.04)
            self.speed = max(0.15, self.speed * 0.93)
            if len(self.snake) == 64:
                self.game_over(self.score, "You filled the whole screen!")
                return
            self.food = self.new_food()
        else:
            self.snake.pop()
        self.draw()

    def draw(self, crashed=False):
        self.board.delete("all")
        for i, (x, y) in enumerate(self.snake):
            self.cell(x, y, RED if crashed else (GREEN_2 if i == 0 else GREEN))
        self.cell(self.food[0], self.food[1], YELLOW, 8)
        self.score_label.config(text="Score: %d" % self.score)
        blink = int(time.time() * 3) % 2 == 0
        self.kit.dots(self.snake + ([self.food] if blink else []))

    def leave(self):
        self.app.root.unbind("<Key>")
        super().leave()


class ReactionGame(Game):
    name = "Reaction"

    def restart(self):
        super().restart()
        self.round = 0
        self.times: list = []
        self.kit.clear_matrix()
        self.new_round()

    def new_round(self):
        self.round += 1
        self.state = "wait"
        self.go_time = time.time() + random.uniform(2, 5)
        self.paint(RED, "WAIT...")
        self.message.config(text="Round %d of 5: touch the pad when the screen turns GREEN "
                                 "and the buzzer beeps. Not before!" % self.round)
        self.kit.say("Round %d of 5" % self.round, "Wait for beep...")
        self.kit.show_text("----")

    def paint(self, color, text):
        self.board.delete("all")
        self.board.create_rectangle(0, 0, self.size, self.size, fill=color, outline="")
        self.board.create_text(self.size / 2, self.size / 2, text=text, fill=WHITE,
                               font=self.app.font(34))

    def tick(self, events):
        if self.over:
            return
        touched = any(what == "touch" for what, _ in events)
        now = time.time()
        if self.state == "wait":
            if touched:
                self.paint(ORANGE, "TOO EARLY!")
                self.kit.say("TOO EARLY!", "Wait for beep")
                self.state = "pause"
                self.pause_until = now + 1.5
                self.round -= 1
            elif now >= self.go_time:
                self.state = "go"
                if TESTING:
                    print("TEST go")
                self.paint(GREEN, "GO!")
                self.kit.beep(0.2)
        elif self.state == "go":
            if touched:
                ms = int((now - self.go_time) * 1000)
                self.times.append(ms)
                self.kit.show_number(ms)
                self.kit.say("%d ms" % ms, "Round %d of 5" % self.round)
                self.paint(BLUE, "%d ms" % ms)
                self.state = "pause"
                self.pause_until = now + 1.5
            elif now - self.go_time > 3:
                self.paint(ORANGE, "TOO SLOW")
                self.state = "pause"
                self.pause_until = now + 1.5
        elif self.state == "pause" and now >= self.pause_until:
            if self.round >= 5:
                best = min(self.times) if self.times else 9999
                self.paint(PURPLE, "%d ms" % best)
                self.game_over(best, "Your best time: %d ms\nAll times: %s" % (
                    best, ", ".join(str(t) for t in self.times) or "none"), lower_is_better=True)
            else:
                self.new_round()
        best_now = min(self.times) if self.times else None
        self.score_label.config(text="Best now: %s" % ("%d ms" % best_now if best_now else "-"))

    def show_best(self, best):
        self.best_label.config(text="Best ever: %s ms" % best if best is not None else "Best ever: -")


class GridGame(Game):
    """Simon and Whack-a-Mole use the 16-button grid: 4x4 squares on the screen and the matrix."""
    cells = 4

    def setup(self):
        self.kit.use_grid()
        self.board.bind("<Button-1>", self.click)    # clicking the screen works too

    def click(self, event):
        size = self.size / 4
        self.pressed(int(event.y // size) * 4 + int(event.x // size) + 1)

    def draw_grid(self, lit=(), color=YELLOW):
        self.board.delete("all")
        for button in range(1, 17):
            row, column = ButtonMatrix.row_and_column(button)
            self.cell(column, row, color if button in lit else PANEL_2, 6)
            size = self.size / 4
            self.board.create_text(column * size + size / 2, row * size + size / 2, text=str(button),
                                   fill=BG if button in lit else GREY, font=self.app.font(14))
        self.kit.squares(lit)

    def tick(self, events):
        for what, key in events:
            if what == "grid":
                self.pressed(key)
        if not self.kit.grid and not self.over:
            self.message.config(text="The button grid didn't start - set BUTTON MODE, or click the "
                                     "squares on the screen instead.")
        self.step()

    def pressed(self, button):
        pass

    def step(self):
        pass


class SimonGame(GridGame):
    name = "Simon Says"
    corners = [1, 4, 13, 16]

    def restart(self):
        super().restart()
        self.sequence: list = []
        self.next_round()

    def next_round(self):
        self.sequence.append(random.choice(self.corners))
        if TESTING:
            print("TEST pattern", " ".join(str(b) for b in self.sequence))
        self.showing = 0
        self.show_until = time.time() + 1.0
        self.answer = 0
        self.score_label.config(text="Round %d" % len(self.sequence))
        self.kit.show_number(len(self.sequence))
        self.message.config(text="Watch the pattern...")
        self.kit.say("SIMON SAYS", "Round %d" % len(self.sequence))
        self.draw_grid()

    def step(self):
        if self.over or self.showing < 0:                 # -1 = the player's turn
            return
        now = time.time()
        if now < self.show_until:
            return
        i = self.showing
        if i < len(self.sequence) * 2:
            if i % 2 == 0:
                self.draw_grid([self.sequence[i // 2]])
                self.kit.beep(0.08)
                self.show_until = now + 0.55
            else:
                self.draw_grid()
                self.show_until = now + 0.25
            self.showing += 1
        else:
            self.showing = -1
            self.message.config(text="Your turn! Press the same buttons in the same order.")
            self.kit.say("Your turn!", "Round %d" % len(self.sequence))

    def pressed(self, button):
        if self.over or self.showing >= 0 or self.answer < 0:
            return
        if button == self.sequence[self.answer]:
            self.draw_grid([button], GREEN_2)
            self.kit.beep(0.04)
            self.answer += 1
            if self.answer == len(self.sequence):
                self.message.config(text="Correct! Next round...")
                self.kit.picture(SMILE)
                self.answer = -1                      # no presses until the next pattern
                self.frame.after(900, self.next_round)
        else:
            self.draw_grid([button], RED)
            self.kit.beep(0.6)
            score = len(self.sequence) - 1
            self.game_over(score, "Wrong button! You remembered %d in a row." % score)


class WhackGame(GridGame):
    name = "Whack-a-Mole"
    seconds = 30

    def restart(self):
        super().restart()
        self.score = 0
        self.mole = 0
        self.mole_time = 1.4
        self.game_end = time.time() + 3 + self.seconds
        self.start_time = time.time() + 3
        self.hide_at = 0.0
        self.message.config(text="Hit the button under the yellow mole before it hides! "
                                 "You can click the screen too.")
        self.kit.show_number(0)

    def step(self):
        if self.over:
            return
        now = time.time()
        if now < self.start_time:
            count = int(self.start_time - now) + 1
            self.score_label.config(text="Get ready: %d" % count)
            self.kit.say("WHACK-A-MOLE", "Starting in %d" % count)
            return
        if now >= self.game_end:
            self.draw_grid()
            self.kit.clear_matrix()
            self.game_over(self.score, "TIME'S UP! You whacked %d moles." % self.score)
            return
        if now >= self.hide_at:
            self.mole = random.choice([b for b in range(1, 17) if b != self.mole])
            self.hide_at = now + self.mole_time
            self.draw_grid([self.mole])
        self.score_label.config(text="Moles: %d" % self.score)
        self.kit.say("Moles: %d" % self.score, "Time: %d s" % int(self.game_end - now))

    def pressed(self, button):
        if self.over or time.time() < self.start_time:
            return
        if button == self.mole:
            self.score += 1
            self.kit.show_number(self.score)
            self.kit.beep(0.04)
            self.mole_time = max(0.5, self.mole_time * 0.95)
            self.hide_at = time.time() + 0.15
            self.draw_grid([button], GREEN_2)
            self.mole = 0
        else:
            self.kit.beep(0.02, 2)




# ==================================================================
#  THE LESSON ENGINE
# ==================================================================
class Lesson:
    """Everything one lesson app needs to know."""
    def __init__(self, key, day, title, banner, learn, tryit, challenges, switches=False,
                 mode="arrows", parts="", tryit_name="Try it", hook=None, setup=None):
        self.key = key                    # e.g. "buzzer.py"
        self.day = day
        self.title = title
        self.banner = banner
        self.learn = learn                # [(heading, explanation), ...]
        self.tryit = tryit                # a Screen class
        self.challenges = challenges      # [function(screen) -> Act, ...]
        self.switches = switches          # True = BUTTON MODE (adds the switch check)
        self.mode = mode                  # "arrows" or "grid"
        self.parts = parts
        self.tryit_name = tryit_name
        self.hook = hook                  # hook(screen, events): runs on Try it and Challenge
        self.setup = setup                # setup(kit): once, when the app opens
        self.code = None                  # the Program for the Code tab (see CODE)


def day_name(lesson):
    return "Day %d" % lesson.day if lesson.day else "Bonus"


def lesson_mode(app):
    lesson = app.lesson
    if lesson is not None and lesson.mode == "grid":
        app.kit.use_grid()
    else:
        app.kit.use_arrows()


def save_progress(key, done, total):
    try:
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            progress = json.load(f)
    except (OSError, ValueError):
        progress = {}
    best = progress.get(key, {}).get("done", 0)
    progress[key] = {"done": max(done, best), "of": total, "time": time.time()}
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f)
    except OSError:
        pass


# ---------------- widgets ----------------
class Graph:
    """A live line graph of the last readings."""
    def __init__(self, app, parent, color=GREEN, height=110, points=90):
        self.canvas = tk.Canvas(parent, height=int(height * app.scale), bg=PANEL_2, highlightthickness=0)
        self.color = color
        self.values: deque = deque(maxlen=points)
        self.points = points

    def add(self, value):
        if value is None:
            return
        self.values.append(float(value))
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if len(self.values) < 2 or w < 10:
            return
        low, high = min(self.values), max(self.values)
        if high - low < 1:
            low, high = low - 0.5, high + 0.5
        xy = []
        for i, v in enumerate(self.values):
            xy += [i * w / (self.points - 1), h - 6 - (v - low) / (high - low) * (h - 12)]
        c.create_line(*xy, fill=self.color, width=3, smooth=True)
        c.create_text(6, 4, text="%g" % round(high, 1), anchor="nw", fill=TEXT, font=("DejaVu Sans", 8))
        c.create_text(6, h - 4, text="%g" % round(low, 1), anchor="sw", fill=TEXT, font=("DejaVu Sans", 8))


class MatrixEditor:
    """An 8x8 grid you click to draw. changed(rows) is called after every click."""
    def __init__(self, app, parent, changed, cell=30):
        self.cell = int(cell * app.scale)
        self.rows = [["."] * 8 for _ in range(8)]
        self.changed = changed
        size = self.cell * 8 + 4
        self.canvas = tk.Canvas(parent, width=size, height=size, bg="#111111", highlightthickness=0,
                                cursor="hand2")
        self.canvas.bind("<Button-1>", self.click)
        self.draw()

    def click(self, event):
        x, y = (event.x - 2) // self.cell, (event.y - 2) // self.cell
        if 0 <= x < 8 and 0 <= y < 8:
            self.rows[y][x] = "." if self.rows[y][x] == "X" else "X"
            self.draw()
            self.changed(self.picture())

    def set(self, rows):
        rows = list(rows[:8]) + ["........"] * (8 - len(rows[:8]))
        self.rows = [list(str(r)[:8].ljust(8, ".")) for r in rows]
        self.rows = [["X" if ch not in " .0_-" else "." for ch in r] for r in self.rows]
        self.draw()
        self.changed(self.picture())

    def picture(self):
        return ["".join(r) for r in self.rows]

    def count(self):
        return sum(r.count("X") for r in self.rows)

    def draw(self):
        c, d = self.canvas, self.cell
        c.delete("all")
        for y in range(8):
            for x in range(8):
                on = self.rows[y][x] == "X"
                c.create_oval(2 + x * d + 2, 2 + y * d + 2, 2 + (x + 1) * d - 2, 2 + (y + 1) * d - 2,
                              fill="#FF3030" if on else "#3A1010", outline="")


class Grid4:
    """The 16-button grid on the screen. clicked(button) when a square is clicked."""
    def __init__(self, app, parent, labels=None, clicked=None, cell=70):
        self.app = app
        self.cell = int(cell * app.scale)
        self.labels = labels or {}
        self.clicked = clicked
        self.canvas = tk.Canvas(parent, width=self.cell * 4 + 4, height=self.cell * 4 + 4, bg=PANEL,
                                highlightthickness=0, cursor="hand2")
        self.canvas.bind("<Button-1>", self.click)
        self.lit: dict = {}
        self.draw()

    def click(self, event):
        x, y = (event.x - 2) // self.cell, (event.y - 2) // self.cell
        if self.clicked and 0 <= x < 4 and 0 <= y < 4:
            self.clicked(y * 4 + x + 1)

    def show(self, lit):
        """lit = {button: colour}"""
        if lit != self.lit:
            self.lit = dict(lit)
            self.draw()

    def draw(self):
        c, d = self.canvas, self.cell
        c.delete("all")
        for b in range(1, 17):
            row, col = ButtonMatrix.row_and_column(b)
            color = self.lit.get(b, PANEL_2)
            c.create_rectangle(2 + col * d + 4, 2 + row * d + 4, 2 + (col + 1) * d - 4,
                               2 + (row + 1) * d - 4, fill=color, outline="")
            text = self.labels.get(b, str(b))
            c.create_text(2 + col * d + d / 2, 2 + row * d + d / 2, text=text,
                          fill=BG if b in self.lit else TEXT, font=self.app.font(12))


def play_beeps(screen, beeps, start=0.0):
    """beeps = [(seconds on, seconds after)]. Plays them one after another."""
    t = start
    for on, gap in beeps:
        screen.frame.after(int(t * 1000), lambda on=on: screen.kit.beep(on))
        t += on + gap
    return t


# ---------------- 1. LEARN ----------------
class LearnScreen(Screen):
    def build(self):
        app, lesson = self.app, self.app.lesson
        lesson_mode(app)
        left = tk.Frame(self.frame, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(self.frame, bg=BG)
        right.pack(side="right", fill="y", padx=(10, 0))
        app.label(left, "WHAT YOU NEED TO KNOW", 10, YELLOW, True).pack(anchor="w", pady=(2, 3))
        wrap = int(580 * app.scale)
        for i, (head, body) in enumerate(lesson.learn, 1):
            card = tk.Frame(left, bg=PANEL if i % 2 else PANEL_2, padx=10, pady=4)
            card.pack(fill="x", pady=2)
            num = tk.Label(card, text=str(i), bg=GREEN, fg=WHITE, font=app.font(13), width=2)
            num.pack(side="left", anchor="n", padx=(0, 10))
            text = tk.Frame(card, bg=card["bg"])
            text.pack(side="left", fill="x", expand=True)
            app.label(text, head, 12, WHITE, True, wraplength=wrap, justify="left").pack(anchor="w")
            app.label(text, body, 10, TEXT, wraplength=wrap, justify="left").pack(anchor="w")
        self.mirror = KitMirror(app, right, small=True)
        self.mirror.frame.pack(anchor="n", fill="x")
        if lesson.parts:
            app.label(right, "You'll use:  " + lesson.parts, 11, YELLOW, True, wraplength=int(280 * app.scale),
                      justify="left").pack(anchor="w", pady=(8, 4))
        app.button(right, lesson.tryit_name + "  >", lambda: app.open(lesson.tryit), ORANGE, 13,
                   padx=16, pady=5).pack(fill="x", pady=3)
        if lesson.code is not None:
            app.button(right, "Code  >", lambda: app.open(CodeScreen), TEAL, 13, padx=16,
                       pady=5).pack(fill="x", pady=3)
        app.button(right, "Challenge  >", lambda: app.open(ChallengeScreen), PURPLE, 13, padx=16,
                   pady=5).pack(fill="x", pady=3)
        self.kit.say(day_name(lesson).upper(), lesson.title[:16])
        self.kit.picture(SMILE)


# ---------------- 2. TRY IT ----------------
class TryScreen(Screen):
    """Controls on the left, the kit mirror on the right. Lessons fill in setup() and update()."""
    hint = ""

    def build(self):
        app = self.app
        lesson_mode(app)
        self.left = tk.Frame(self.frame, bg=BG)
        self.left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(self.frame, bg=BG)
        right.pack(side="right", fill="y", padx=(10, 0))
        self.mirror = KitMirror(app, right)
        self.mirror.frame.pack(anchor="n")
        if self.hint:
            app.label(right, self.hint, 11, YELLOW, True, wraplength=int(300 * app.scale),
                      justify="left").pack(anchor="w", pady=8)
        self.setup()

    def setup(self):
        pass

    def update(self, events):
        pass

    def tick(self, events):
        hook = getattr(self.app.lesson, "hook", None)
        if hook:
            hook(self, events)
        self.update(events)

    # helpers for the lessons
    def row(self, parent=None, pady=4):
        frame = tk.Frame(parent or self.left, bg=BG)
        frame.pack(anchor="w", fill="x", pady=pady)
        return frame

    def btn(self, parent, text, command, color=PANEL_2, size=12):
        b = self.app.button(parent, text, command, color, size, padx=12, pady=5)
        b.pack(side="left", padx=(0, 6))
        return b

    def big(self, parent, title, color=WHITE, size=30):
        box = tk.Frame(parent, bg=PANEL, padx=12, pady=6)
        box.pack(side="left", padx=(0, 8), fill="both", expand=True)
        self.app.label(box, title.upper(), 9, YELLOW, True).pack(anchor="w")
        value = self.app.label(box, "-", size, color, True)
        value.pack(anchor="w")
        return value

    def heading(self, text):
        self.app.label(self.left, text, 10, YELLOW, True).pack(anchor="w", pady=(6, 2))


# ---------------- 3. CHALLENGE ----------------
class Step(Act):
    """A challenge with its own title and instructions."""
    def __init__(self, show, title, text, needs=(), hide_mirror=False):
        super().__init__(show)
        self.title = title
        self.text = text
        self.needs = needs
        self.hide_mirror = hide_mirror

    def instruction(self):
        return self.text


class Count(Step):
    """Done after n events of one kind (touches, arrow presses, remote keys...)."""
    def __init__(self, show, title, text, event, n=1, distinct=False, accept=None, react=None, **kw):
        super().__init__(show, title, text, **kw)
        self.event, self.n, self.distinct = event, n, distinct
        self.accept, self.react = accept, react
        self.got: list = []

    def start(self):
        super().start()
        self.got = []

    def tick(self, events):
        for what, detail in events:
            if what == self.event and (self.accept is None or self.accept(detail)):
                if not self.distinct or detail not in self.got:
                    self.got.append(detail)
                    if self.react:
                        self.react(self.kit, detail)
                    else:
                        self.kit.beep(0.05)
        return len(self.got) >= self.n

    def live(self):
        if self.distinct and self.got:
            return "So far: " + ", ".join(str(g) for g in self.got)
        return "%d of %d" % (len(self.got), self.n)

    def progress(self):
        return len(self.got) / self.n


class Until(Step):
    """Done when test(kit, act) is True (for 'hold' seconds in a row)."""
    def __init__(self, show, title, text, test, setup=None, live=None, progress=None, hold=0.0, **kw):
        super().__init__(show, title, text, **kw)
        self.test, self.setup, self.live_text, self.progress_of = test, setup, live, progress
        self.hold = hold
        self.since = None
        self.memory: dict = {}

    def start(self):
        super().start()
        self.since = None
        self.memory = {}
        if self.setup:
            self.setup(self.kit, self)

    def tick(self, events):
        self.events = events
        try:
            ok = self.test(self.kit, self)
        except (TypeError, KeyError):
            ok = False                         # (a reading isn't ready yet)
        if not ok:
            self.since = None
            return False
        if self.since is None:
            self.since = time.time()
        if time.time() - self.since >= self.hold:
            self.kit.beep(0.15)
            return True
        return False

    def live(self):
        text = ""
        if self.live_text:
            try:
                text = self.live_text(self.kit, self)
            except (TypeError, KeyError, ValueError):
                text = ""
        if self.hold and self.since is not None:
            text += "   holding... %.1f s" % (time.time() - self.since)
        return text

    def progress(self):
        if self.hold and self.since is not None:
            return (time.time() - self.since) / self.hold
        if self.progress_of:
            try:
                return self.progress_of(self.kit, self)
            except (TypeError, KeyError, ZeroDivisionError):
                return 0.0
        return 0.0


class Quiz(Step):
    """The kit shows or plays something, then you pick the answer on the screen."""
    def __init__(self, show, title, text, ask, rounds=3, **kw):
        super().__init__(show, title, text, **kw)
        self.ask, self.rounds = ask, rounds
        self.right = 0
        self.replay = None

    def start(self):
        super().start()
        self.right = 0
        self.box = self.show.extra
        self.question = self.show.app.label(self.box, "", 14, WHITE, True)
        self.question.pack(anchor="w")
        self.options = tk.Frame(self.box, bg=BG)
        self.options.pack(anchor="w", pady=6)
        self.feedback = self.show.app.label(self.box, "", 12, YELLOW, True)
        self.feedback.pack(anchor="w")
        self.new_round()

    def new_round(self):
        for child in self.options.winfo_children():
            child.destroy()
        question, options, answer = self.ask(self)
        self.answer = answer
        if TESTING:
            print("TEST answer", answer)
        self.question.config(text=question)
        for option in options:
            self.show.app.button(self.options, str(option), lambda o=option: self.pick(o), BLUE, 13,
                                 padx=14, pady=6).pack(side="left", padx=(0, 6))
        if self.replay:
            self.show.app.button(self.options, "Again", self.replay, PANEL_2, 11, padx=10,
                                 pady=6).pack(side="left", padx=(12, 0))

    def pick(self, option):
        self.replay = None
        if str(option) == str(self.answer):
            self.right += 1
            self.kit.beep(0.05, 2)
            self.feedback.config(text="Right!  (%d of %d)" % (self.right, self.rounds), fg=GREEN_2)
        else:
            self.kit.beep(0.4)
            self.feedback.config(text="Not quite - it was %s. Try another one!" % self.answer, fg="#FF8A80")
        if self.right < self.rounds:
            self.show.frame.after(900, self.new_round)

    def tick(self, events):
        return self.right >= self.rounds

    def live(self):
        return ""

    def progress(self):
        return self.right / self.rounds


class Entry(Step):
    """Type something on the screen; submit(kit, text) says if it's right (True) or what's wrong."""
    def __init__(self, show, title, text, prompt, submit, **kw):
        super().__init__(show, title, text, **kw)
        self.prompt, self.submit = prompt, submit
        self.ok = False

    def start(self):
        super().start()
        self.ok = False
        box = self.show.extra
        self.show.app.label(box, self.prompt, 13, WHITE, True).pack(anchor="w")
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", pady=6, fill="x")
        self.entry = tk.Entry(row, font=self.show.app.font(15, False), width=28)
        self.entry.pack(side="left")
        self.entry.bind("<Return>", lambda _e: self.send())
        self.show.app.button(row, "Send", self.send, GREEN, 12, padx=14, pady=4).pack(side="left", padx=6)
        self.feedback = self.show.app.label(box, "", 12, YELLOW, True)
        self.feedback.pack(anchor="w")
        self.entry.focus_set()

    def send(self):
        result = self.submit(self.kit, self.entry.get())
        if result is True:
            self.ok = True
        else:
            self.feedback.config(text=str(result or "Try again!"))

    def tick(self, events):
        return self.ok


class Targets(Step):
    """The kit shows a target (an arrow, a square, a key) - hit it, n times."""
    def __init__(self, show, title, text, event, pick, display, n=5, **kw):
        super().__init__(show, title, text, **kw)
        self.event, self.pick, self.display, self.n = event, pick, display, n
        self.hits = 0
        self.target = None

    def start(self):
        super().start()
        self.hits = 0
        self.next_target()

    def next_target(self):
        old = self.target
        self.target = self.pick()
        while self.target == old:
            self.target = self.pick()
        self.display(self.kit, self.target)
        if TESTING:
            print("TEST target", self.target)

    def tick(self, events):
        for what, detail in events:
            if what == self.event and detail == self.target:
                self.hits += 1
                self.kit.beep(0.05)
                if self.hits < self.n:
                    self.next_target()
        return self.hits >= self.n

    def live(self):
        return "Target: %s     hits: %d of %d" % (self.target, self.hits, self.n)

    def progress(self):
        return self.hits / self.n


class InOrder(Step):
    """Press these buttons in this order (a wrong one starts again)."""
    def __init__(self, show, title, text, event, order, **kw):
        super().__init__(show, title, text, **kw)
        self.event, self.order = event, list(order)
        self.at = 0

    def start(self):
        super().start()
        self.at = 0

    def tick(self, events):
        for what, detail in events:
            if what != self.event:
                continue
            if detail == self.order[self.at]:
                self.at += 1
            else:
                self.at = 1 if detail == self.order[0] else 0
        return self.at >= len(self.order)

    def live(self):
        done = " ".join(str(b) for b in self.order[:self.at])
        todo = " ".join(str(b) for b in self.order[self.at:])
        return "Done: %s    Next: %s" % (done or "-", todo)

    def progress(self):
        return self.at / len(self.order)


class ChallengeScreen(Screen):
    """The challenges (the show's acts work the same way)."""
    word = "Challenge"
    heading = "CHALLENGES"

    def make_acts(self):
        return [make(self) for make in self.app.lesson.challenges]

    def intro(self):
        n = len(self.acts)
        return ("Ready for the challenge?",
                "%d things to do with your CrowPi. The kit checks each one and ticks it off. "
                "Missing a part? Click 'Skip'." % n)

    def build(self):
        app = self.app
        lesson_mode(app)
        self.photo = None
        self.photo_image = None
        self.acts = self.make_acts()
        self.index = -1
        self.waiting = False
        self.results: list = []
        left = tk.Frame(self.frame, bg=PANEL, padx=12, pady=8)
        left.pack(side="left", fill="y")
        app.label(left, self.heading, 10, YELLOW, True).pack(anchor="w", pady=(0, 4))
        self.rows = []
        for i, act in enumerate(self.acts):
            row = app.label(left, "  %2d  %s" % (i + 1, act.title), 11, TEXT)
            row.pack(anchor="w")
            self.rows.append(row)
        right = tk.Frame(self.frame, bg=BG, padx=18)
        right.pack(side="left", fill="both", expand=True)
        title, text = self.intro()
        self.act_title = app.label(right, title, 22, WHITE, True)
        self.act_title.pack(anchor="w", pady=(6, 4))
        self.text = app.label(right, text, 14, TEXT, wraplength=int(600 * app.scale), justify="left")
        self.text.pack(anchor="w")
        self.live = app.label(right, "", 15, YELLOW, True)
        self.live.pack(anchor="w", pady=8)
        self.bar = tk.Canvas(right, height=18, bg=PANEL_2, highlightthickness=0)
        self.bar.pack(fill="x", pady=4)
        buttons = tk.Frame(right, bg=BG)
        buttons.pack(side="bottom", anchor="w", pady=6)     # (packed first: always on screen)
        self.extra = tk.Frame(right, bg=BG)
        self.extra.pack(anchor="w", fill="x", pady=4)
        self.bottom = tk.Frame(right, bg=BG)
        self.bottom.pack(anchor="w", fill="x", pady=4)
        self.mirror = KitMirror(self.app, self.bottom, small=True)
        self.mirror.frame.pack(side="left", anchor="n")
        self.picture = tk.Label(self.bottom, bg=BG)
        self.picture.pack(side="left", anchor="n", padx=16)
        self.start_button = app.button(buttons, "Start!", self.start_show, ORANGE, 14, padx=18, pady=8)
        self.start_button.pack(side="left")
        self.skip_button = app.button(buttons, "Skip this one", self.skip, PANEL_2, 12, padx=14, pady=8)
        self.welcome()

    def welcome(self):
        lesson = self.app.lesson
        self.kit.say("CHALLENGE", day_name(lesson))

    def start_show(self):
        self.start_button.pack_forget()
        self.skip_button.pack(side="left")
        self.index = -1
        self.waiting = False
        self.results = []
        self.acts = self.make_acts()
        for i, row in enumerate(self.rows):
            row.config(fg=TEXT, text="  %2d  %s" % (i + 1, self.acts[i].title))
        self.next_act()

    def clear_extra(self):
        for child in self.extra.winfo_children():
            child.destroy()

    def next_act(self):
        self.clear_extra()
        self.index += 1
        while self.index < len(self.acts) and not self.acts[self.index].available():
            self.mark(self.index, "skipped")
            self.index += 1
        if self.index >= len(self.acts):
            self.mirror.frame.pack(side="left", anchor="n")
            self.finale()
            return
        act = self.acts[self.index]
        self.rows[self.index].config(fg=YELLOW)
        self.act_title.config(text="%s %d: %s" % (self.word, self.index + 1, act.title))
        self.text.config(text=act.instruction())
        if getattr(act, "hide_mirror", False):
            self.mirror.frame.pack_forget()            # no peeking - look at the real kit!
        else:
            self.mirror.frame.pack(side="left", anchor="n")
        print("%s %d: %s - %s" % (self.word, self.index + 1, act.title, act.instruction()))
        act.start()

    def mark(self, index, result):
        print("   ", self.acts[index].title, "-", result.upper())
        self.results.append(result)
        mark = {"done": "  OK", "skipped": "  --"}[result]
        self.rows[index].config(text="%s  %s" % (mark, self.acts[index].title),
                                fg=GREEN_2 if result == "done" else GREY)

    def skip(self):
        if self.waiting:
            return
        if 0 <= self.index < len(self.acts):
            self.mark(self.index, "skipped")
            lesson_mode(self.app)
            self.next_act()

    def tick(self, events):
        hook = getattr(self.app.lesson, "hook", None)
        if hook:
            hook(self, events)
        if not 0 <= self.index < len(self.acts):
            return
        if self.waiting:
            return
        act = self.acts[self.index]
        if act.tick(events):
            self.mark(self.index, "skipped" if act.skipped else "done")
            if not act.auto and not act.skipped:
                self.kit.picture(CHECK)
            self.live.config(text="Done!" if not act.skipped else "")
            self.waiting = True                    # a moment to see the tick, then the next one
            self.frame.after(1200 if isinstance(act, Quiz) else 700, self.after_act)
            return
        self.live.config(text=act.live())
        self.bar.delete("all")
        width = self.bar.winfo_width()
        self.bar.create_rectangle(0, 0, width * max(0.0, min(1.0, act.progress())), 18, fill=GREEN,
                                  outline="")

    def after_act(self):
        self.waiting = False
        self.next_act()

    def finale(self):
        done = self.results.count("done")
        total = len(self.acts)
        self.index = len(self.acts)
        self.skip_button.pack_forget()
        self.start_button.config(text="Do them again")
        self.start_button.pack(side="left")
        lesson = self.app.lesson
        stars = "*" * done + "-" * (total - done)
        if done == total:
            self.act_title.config(text="LESSON COMPLETE!  %s" % stars)
            self.text.config(text="You did all %d challenges. %s is done - well done!" % (total, day_name(lesson)))
            self.kit.say("LESSON COMPLETE", "%s  %s" % (day_name(lesson), stars))
        else:
            self.act_title.config(text="%d of %d done  %s" % (done, total, stars))
            self.text.config(text="Nice work! Click 'Do them again' to get them all.")
            self.kit.say("%d of %d done!" % (done, total), stars)
        print("FINISHED: %d of %d challenges done" % (done, total))
        self.live.config(text="")
        self.bar.delete("all")
        self.kit.picture(STAR)
        self.kit.show_text("%d-%d" % (done, total))
        self.kit.beep(0.1, 3)
        save_progress(lesson.key, done, total)

    def show_photo(self, path):
        try:
            from PIL import Image
            small = os.path.join(os.path.expanduser("~"), ".crowpi_show_photo.png")
            image = Image.open(path)
            image.thumbnail((int(320 * self.app.scale), int(200 * self.app.scale)))
            image.save(small)
            self.photo_image = tk.PhotoImage(file=small)
            self.picture.config(image=self.photo_image)
        except Exception as problem:
            print("Couldn't show the photo:", problem)

    def leave(self):
        self.kit.relay_on(False)
        self.kit.quiet()
        lesson_mode(self.app)


class ShowScreen(ChallengeScreen):
    """The CrowPi Show: 12 acts, one for every part."""
    word = "Act"
    heading = "THE ACTS"

    def make_acts(self):
        return [act(self) for act in ACTS]

    def intro(self):
        return ("Ready for the show?", "12 acts - one for every part of the CrowPi. Do what the screen "
                "says: the kit checks it and ticks it off.\nMissing a part? That act is skipped.")

    def welcome(self):
        self.start_button.config(text="Start the show!")
        self.skip_button.config(text="Skip this act")
        self.kit.say("THE CROWPI SHOW", "Ready?")
        self.kit.picture(SMILE)

    def finale(self):
        done = self.results.count("done")
        self.index = len(self.acts)
        self.skip_button.pack_forget()
        self.start_button.config(text="Run the show again")
        self.start_button.pack(side="left")
        self.act_title.config(text="THE END!  %d of %d acts done" % (done, len(self.acts)))
        print("THE END! %d of %d acts done" % (done, len(self.acts)))
        self.text.config(text="Every part of the CrowPi did its job. Take a bow!")
        self.live.config(text="Photo saved in Pictures > CrowPi_Photos" if self.photo else "")
        self.bar.delete("all")
        self.kit.say("THE CROWPI SHOW", "* THE END *")
        self.kit.show_number(done)
        self.kit.beep(0.1, 3)
        self.kit.scroll("THE END")
        if self.photo:
            self.show_photo(self.photo)
        save_progress("crowpi_show.py", done, len(self.acts))

    def leave(self):
        self.kit.use_arrows()
        self.kit.relay_on(False)


class GuessGame(GridGame):
    name = "Guess the Number"

    def restart(self):
        super().restart()
        self.secret = random.randint(1, 16)
        self.guesses = 0
        self.tried: dict = {}
        self.message.config(text="I'm thinking of a number from 1 to 16. Press a button (or click "
                                 "a square) to guess!")
        self.kit.say("Guess my number", "1 to 16")
        self.kit.show_number(0)
        self.draw_grid()

    def draw_grid(self, lit=(), color=YELLOW):
        self.board.delete("all")
        size = self.size / 4
        for button in range(1, 17):
            row, column = ButtonMatrix.row_and_column(button)
            self.cell(column, row, self.tried.get(button, PANEL_2), 6)
            self.board.create_text(column * size + size / 2, row * size + size / 2, text=str(button),
                                   fill=TEXT if button not in self.tried else BG, font=self.app.font(14))

    def pressed(self, button):
        if self.over:
            return
        self.guesses += 1
        self.kit.show_number(self.guesses)
        if button < self.secret:
            self.tried[button] = BLUE
            self.message.config(text="%d is too low - HIGHER!" % button)
            self.kit.say("%d? No..." % button, "HIGHER!")
            self.kit.picture(ARROWS["UP"])
            self.kit.beep(0.05)
        elif button > self.secret:
            self.tried[button] = ORANGE
            self.message.config(text="%d is too high - LOWER!" % button)
            self.kit.say("%d? No..." % button, "LOWER!")
            self.kit.picture(ARROWS["DOWN"])
            self.kit.beep(0.05)
        else:
            self.tried[button] = GREEN_2
            self.score_label.config(text="Guesses: %d" % self.guesses)
            self.kit.picture(SMILE)
            self.kit.beep(0.1, 3)
            self.draw_grid()
            self.game_over(self.guesses, "YES! It was %d. You needed %d guesses." % (button, self.guesses),
                           lower_is_better=True)
            return
        self.draw_grid()
        self.score_label.config(text="Guesses: %d" % self.guesses)

    def show_best(self, best):
        self.best_label.config(text="Fewest guesses: %s" % best if best is not None else "Best: -")


# ==================================================================
#  OPEN AN APP
# ==================================================================
class GameSpec:
    def __init__(self, title, game, mode, banner):
        self.title, self.game, self.mode, self.banner = title, game, mode, banner
        self.day = 0


GAME_APPS = {
    "snake": GameSpec("Snake", "SnakeGame", "arrows", "GAME - Snake"),
    "whack": GameSpec("Whack-a-Mole", "WhackGame", "grid", "GAME - Whack-a-Mole"),
    "guess": GameSpec("Guess the Number", "GuessGame", "grid", "GAME - Guess the Number"),
}


def run(name):
    """Open the app for a lesson, the show or a game."""
    if name == "show":
        crowpi_kit.banner("Day 22 - The CrowPi Show", switches=crowpi_kit.BUTTON_MODE,
                          parts="IR receiver + remote, RFID card, camera (all optional)", window=False)
        title = "The CrowPi Show"
        tabs = [("Switch Check", SwitchScreen, GREEN), ("Dashboard", DashboardScreen, BLUE),
                ("The Show", ShowScreen, ORANGE), ("Games", GamesScreen, PURPLE)]
        lesson, locked = None, games_locked_setting()
    elif name in GAME_APPS:
        spec = GAME_APPS[name]
        crowpi_kit.banner(spec.banner, switches=crowpi_kit.BUTTON_MODE, window=False)
        title = spec.title
        tabs = [("Switch Check", SwitchScreen, GREEN), (spec.title, globals()[spec.game], PURPLE)]
        lesson, locked = spec, False
    else:
        lesson = LESSONS[name]
        crowpi_kit.banner(lesson.banner, switches=crowpi_kit.BUTTON_MODE if lesson.switches else
                          "No switches needed.", parts=lesson.parts or None, window=False)
        title = "%s: %s" % (day_name(lesson), lesson.title)
        tabs = [("Learn", LearnScreen, BLUE), (lesson.tryit_name, lesson.tryit, ORANGE),
                ("Challenge", ChallengeScreen, PURPLE)]
        lesson.code = CODE.get(name)
        if lesson.code is not None:
            tabs.insert(2, ("Code", CodeScreen, TEAL))
        if lesson.switches:
            tabs.insert(0, ("Switch Check", SwitchScreen, GREEN))
        locked = False
    print("The lesson opens in its own window. Close the window when you're done.")
    kit = Kit()
    setup = getattr(lesson, "setup", None)
    if setup:
        setup(kit)
    root = tk.Tk(className="CrowPiLesson")
    ShowApp(root, kit, title, tabs, lesson=lesson, games_locked=locked)
    try:
        root.mainloop()
    finally:
        if kit.running:
            kit.close()
    crowpi_kit.finish("See you next time!")


# ==================================================================
#  THE LESSONS
# ==================================================================
def options_with(answer, others, n=3):
    """The answer plus some wrong ones, mixed up."""
    wrong = [o for o in dict.fromkeys(others) if str(o) != str(answer)]
    random.shuffle(wrong)
    choices = [answer] + wrong[:n - 1]
    random.shuffle(choices)
    return choices


def events_of(events, kind):
    return [detail for what, detail in events if what == kind]


PICTURES = {"Smile": SMILE, "Heart": HEART, "Star": STAR, "Tick": CHECK, "Up arrow": ARROWS["UP"],
            "Down arrow": ARROWS["DOWN"]}
WORDS = ["PIZZA", "ROBOT", "TIGER", "MANGO", "ROCKET", "PANDA", "CASTLE", "DRAGON", "PLANET", "COOKIE"]
FULL = ["XXXXXXXX"] * 8


# ---------------- Day 1: Hello CrowPi ----------------
def say_hello(kit, name):
    name = name.strip()[:16] or "Coder"
    kit.say("Hello,", name)
    kit.show_text("HI")
    kit.beep(0.1, 2)
    kit.scroll("HI " + name.upper())
    return name


class HelloTry(TryScreen):
    hint = "Type your name and click 'Say hello' - then look at the real kit!"

    def setup(self):
        self.heading("YOUR NAME")
        row = self.row()
        self.name = tk.Entry(row, font=self.app.font(16, False), width=18)
        self.name.pack(side="left", padx=(0, 8))
        self.name.bind("<Return>", lambda _e: self.hello())
        self.btn(row, "Say hello", self.hello, GREEN, 13)
        self.heading("PICTURES ON THE LED MATRIX")
        row = self.row()
        for name in ("Smile", "Heart", "Star"):
            self.btn(row, name, lambda n=name: self.kit.picture(PICTURES[n]), BLUE)
        self.heading("THE 7-SEGMENT DISPLAY")
        row = self.row()
        for text in ("HI", "YO", "COOL"):
            self.btn(row, text, lambda t=text: self.kit.show_text(t), PURPLE)
        self.btn(row, "Beep!", lambda: self.kit.beep(0.1), ORANGE)
        self.said = self.app.label(self.left, "", 13, YELLOW, True)
        self.said.pack(anchor="w", pady=8)
        self.kit.say("Hello! What's", "your name?")
        self.kit.show_text("HI")
        self.kit.picture(SMILE)

    def hello(self):
        name = say_hello(self.kit, self.name.get())
        self.said.config(text="The kit says hello to %s!" % name)


def hello_entry(kit, text):
    if not text.strip():
        return "Type your name first!"
    say_hello(kit, text)
    return True


def lcd_word_quiz(act):
    word = random.choice(WORDS)
    act.kit.say("Secret word:", word)
    return "What does the LCD screen on the kit say?", options_with(word, WORDS), word


def count_lights_quiz(act):
    n = random.randint(2, 7)
    spots = random.sample([(x, y) for x in range(8) for y in range(8)], n)
    act.kit.dots(spots)
    return "How many lights are on on the LED matrix?", options_with(n, [n - 1, n + 1, n + 2, n - 2]), n


# ---------------- Day 2: Buzzer ----------------
BEEP_PATTERNS = {
    "Doorbell": [(0.15, 0.15), (0.5, 0)],
    "Alarm": [(0.08, 0.06)] * 6,
    "SOS": [(0.1, 0.1)] * 3 + [(0.4, 0.1)] * 3 + [(0.1, 0.1)] * 3,
    "One long": [(1.0, 0)],
}


class BuzzerTry(TryScreen):
    hint = "The buzzer is the round black part. It beeps when the program turns it ON."

    def setup(self):
        self.heading("HOW LONG IS ONE BEEP?  (seconds)")
        self.length = tk.Scale(self.left, from_=0.05, to=1.0, resolution=0.05, orient="horizontal",
                               length=int(380 * self.app.scale), bg=BG, fg=WHITE, highlightthickness=0,
                               troughcolor=PANEL_2, font=self.app.font(10, False))
        self.length.set(0.2)
        self.length.pack(anchor="w")
        row = self.row()
        self.btn(row, "Beep once", lambda: self.play([(self.length.get(), 0)]), ORANGE, 13)
        self.btn(row, "Beep 3 times", lambda: self.play([(self.length.get(), 0.15)] * 3), ORANGE, 13)
        self.heading("PATTERNS")
        row = self.row()
        for name in BEEP_PATTERNS:
            self.btn(row, name, lambda n=name: self.play(BEEP_PATTERNS[n], n), BLUE)
        self.status = self.app.label(self.left, "", 14, YELLOW, True)
        self.status.pack(anchor="w", pady=10)
        self.kit.say("Buzzer", "Click a button!")
        self.beeps = 0

    def play(self, pattern, name="beep"):
        self.beeps += len(pattern)
        self.kit.show_number(self.beeps)
        self.kit.say("Playing:", name)
        self.status.config(text="Playing: %s  (%d beeps so far)" % (name, self.beeps))
        play_beeps(self, pattern)

    def update(self, events):
        on = self.kit.buzzing()
        self.kit.picture(FULL if on else ["........"] * 3 + ["...XX..."] * 2 + ["........"] * 3)


def count_beeps_quiz(act):
    n = random.randint(1, 5)
    act.replay = lambda: play_beeps(act.show, [(0.15, 0.25)] * n)
    act.show.frame.after(400, act.replay)
    return "Listen! How many beeps?", [1, 2, 3, 4, 5], n


def short_long_quiz(act):
    answer = random.choice(["Short", "Long"])
    act.replay = lambda: act.kit.beep(0.1 if answer == "Short" else 0.8)
    act.show.frame.after(400, act.replay)
    return "Listen! Was that a short beep or a long beep?", ["Short", "Long"], answer


def pattern_quiz(act):
    answer = random.choice(list(BEEP_PATTERNS))
    act.replay = lambda: play_beeps(act.show, BEEP_PATTERNS[answer])
    act.show.frame.after(400, act.replay)
    return "Listen! Which pattern is it?", list(BEEP_PATTERNS), answer


# ---------------- Day 3: Buttons + Buzzer ----------------
ARROW_BEEPS = {"UP": 0.05, "DOWN": 0.3, "LEFT": 0.12, "RIGHT": 0.12}


class ArrowTiles:
    """Four arrow tiles that light up when the real buttons are pressed."""
    def __init__(self, app, parent):
        self.app = app
        self.frame = tk.Frame(parent, bg=BG)
        self.tiles = {}
        places = {"UP": (0, 1), "LEFT": (1, 0), "DOWN": (1, 1), "RIGHT": (1, 2)}
        symbols = {"UP": "^", "DOWN": "v", "LEFT": "<", "RIGHT": ">"}
        for name, (r, c) in places.items():
            tile = tk.Label(self.frame, text=symbols[name] + "\n" + name, bg=PANEL_2, fg=TEXT, width=7,
                            height=3, font=app.font(14))
            tile.grid(row=r, column=c, padx=4, pady=4)
            self.tiles[name] = tile

    def show(self, pressed):
        for name, tile in self.tiles.items():
            tile.config(bg=ORANGE if name in pressed else PANEL_2, fg=WHITE if name in pressed else TEXT)


class ArrowTry(TryScreen):
    hint = "Press the arrow buttons on the kit. Each one beeps differently!"

    def setup(self):
        self.heading("THE ARROW BUTTONS")
        self.tiles = ArrowTiles(self.app, self.left)
        self.tiles.frame.pack(anchor="w")
        self.last = self.app.label(self.left, "Press a button...", 16, YELLOW, True)
        self.last.pack(anchor="w", pady=10)
        self.presses = 0
        self.kit.say("Press an arrow", "button!")
        self.kit.show_number(0)

    def update(self, events):
        self.tiles.show(self.kit.state["arrows"])
        for name in events_of(events, "arrow"):
            self.presses += 1
            self.kit.beep(ARROW_BEEPS[name])
            self.kit.picture(ARROWS[name])
            self.kit.say("You pressed", name)
            self.kit.show_number(self.presses)
            self.last.config(text="You pressed %s  (%d presses)" % (name, self.presses))


def arrow_react(kit, name):
    kit.beep(ARROW_BEEPS.get(name, 0.05))
    kit.picture(ARROWS[name])


def show_arrow_target(kit, name):
    kit.picture(ARROWS[name])
    kit.say("Press:", name)


# ---------------- Day 4: Relay ----------------
class RelayTry(TryScreen):
    hint = "Listen for the CLICK and watch the little light next to the relay."

    def setup(self):
        row = self.row()
        self.state = self.big(row, "The relay is", size=36)
        self.clicks_label = self.big(row, "Clicks", size=36)
        self.heading("CONTROLS")
        row = self.row()
        self.btn(row, "ON", lambda: self.set(True), GREEN, 14)
        self.btn(row, "OFF", lambda: self.set(False), RED, 14)
        self.btn(row, "Switch", lambda: self.set(not self.on()), BLUE, 14)
        row = self.row()
        self.btn(row, "Click 5 times", lambda: self.pattern(5, 0.3), ORANGE)
        self.btn(row, "Fast clicks", lambda: self.pattern(8, 0.12), ORANGE)
        self.clicks = 0
        self.kit.say("Relay", "Click ON!")

    def on(self):
        return bool(self.kit.relay and self.kit.relay.value)

    def set(self, on):
        if on != self.on():
            self.clicks += 1
        self.kit.relay_on(on)

    def pattern(self, n, gap):
        def flip(i):
            return lambda: self.set(i % 2 == 0)
        for i in range(n * 2):
            self.frame.after(int(i * gap * 1000), flip(i))

    def update(self, events):
        on = self.on()
        self.state.config(text="ON" if on else "OFF", fg=GREEN_2 if on else "#FF8A80")
        self.clicks_label.config(text=str(self.clicks))
        self.kit.say("Relay is " + ("ON" if on else "OFF"), "Clicks: %d" % self.clicks)
        self.kit.show_number(self.clicks)
        self.kit.picture(FULL if on else ["........"] * 8)


def relay_clicks_quiz(act):
    n = random.randint(2, 5)

    def play():
        for i in range(n * 2):
            act.show.frame.after(int(i * 350), act.kit.relay_on, i % 2 == 0)
    act.replay = play
    act.show.frame.after(400, play)
    return "Listen! How many times does the relay turn ON?", [1, 2, 3, 4, 5], n


def relay_on_off_quiz(act):
    on = random.choice([True, False])
    act.kit.relay_on(not on)
    act.show.frame.after(300, lambda: act.kit.relay_on(on))
    return "Look at the relay's little light: is the relay ON or OFF now?", ["ON", "OFF"], \
        "ON" if on else "OFF"


def relay_switch(kit, _detail):
    kit.relay_on(not (kit.relay and kit.relay.value))
    kit.beep(0.03)


# ---------------- Day 5: Light sensor ----------------
def light_now(kit):
    return kit.state["light"]


class LightTry(TryScreen):
    hint = "Cover the light sensor with your hand, or shine a phone torch on it!"

    def setup(self):
        row = self.row()
        self.lux = self.big(row, "Light (lux)", size=40)
        self.word = self.big(row, "That's", size=26)
        self.heading("THE LAST 30 SECONDS")
        self.graph = Graph(self.app, self.left, YELLOW)
        self.graph.canvas.pack(fill="x")
        row = self.row()
        self.night = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Night light: when it's dark, light up the matrix", variable=self.night,
                       bg=BG, fg=WHITE, selectcolor=PANEL_2, activebackground=BG,
                       font=self.app.font(12)).pack(side="left")
        self.last = 0.0

    def update(self, events):
        lux = light_now(self.kit)
        if lux is None:
            self.lux.config(text="...")
            return
        if time.time() - self.last > 0.3:
            self.last = time.time()
            self.graph.add(lux)
        word = "DARK" if lux < 10 else "dim" if lux < 100 else "bright" if lux < 1000 else "VERY BRIGHT"
        self.lux.config(text="%.0f" % lux)
        self.word.config(text=word)
        self.kit.show_number(lux)
        self.kit.say("Light: %.0f lux" % lux, word)
        if self.night.get():
            self.kit.picture(FULL if lux < 10 else ["........"] * 8)
        else:
            rows = max(0, min(8, int(lux ** 0.5 / 4)))
            self.kit.picture(["........"] * (8 - rows) + ["XXXXXXXX"] * rows)


def remember_light(kit, act):
    act.memory["start"] = max(light_now(kit) or 100, 1)


def live_light(kit, act):
    kit.show_number(light_now(kit))
    return "Now: %.0f lux   (at the start: %.0f lux)" % (light_now(kit), act.memory["start"])


# ---------------- Day 6: Temperature ----------------
class WeatherTry(TryScreen):
    hint = "Breathe gently on the sensor (the little white box) and watch the numbers!"

    def setup(self):
        row = self.row()
        self.temp = self.big(row, "Temperature", "#FF8A65", 32)
        self.hum = self.big(row, "Humidity", "#4FC3F7", 32)
        self.heading("TEMPERATURE")
        self.temp_graph = Graph(self.app, self.left, "#FF8A65", 80)
        self.temp_graph.canvas.pack(fill="x")
        self.heading("HUMIDITY (how much water is in the air)")
        self.hum_graph = Graph(self.app, self.left, "#4FC3F7", 80)
        self.hum_graph.canvas.pack(fill="x")
        row = self.row()
        self.fahrenheit = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Show Fahrenheit", variable=self.fahrenheit, bg=BG, fg=WHITE,
                       selectcolor=PANEL_2, activebackground=BG, font=self.app.font(12)).pack(side="left")
        self.last = 0.0

    def update(self, events):
        t, h = self.kit.state["temperature"], self.kit.state["humidity"]
        if t is None:
            self.temp.config(text="...")
            return
        if time.time() - self.last > 1:
            self.last = time.time()
            self.temp_graph.add(t)
            self.hum_graph.add(h)
        shown = t * 9 / 5 + 32 if self.fahrenheit.get() else t
        unit = "F" if self.fahrenheit.get() else "C"
        self.temp.config(text="%.1f %s" % (shown, unit))
        self.hum.config(text="%.0f %%" % h)
        self.kit.say("Temp: %.1f %s" % (shown, unit), "Humidity: %.0f%%" % h)
        self.kit.show_number(shown)
        self.kit.picture(SMILE if 18 <= t <= 26 else ARROWS["UP"] if t > 26 else ARROWS["DOWN"])


def temperature_quiz(act):
    t = act.kit.state["temperature"]
    t = 20 if t is None else int(round(t))
    act.kit.show_number(t)
    act.kit.say("Temperature is", "on the display!")
    return "What temperature does the 7-segment display show? (in C)", \
        options_with(t, [t - 4, t + 3, t - 2, t + 5]), t


def humidity_quiz(act):
    h = act.kit.state["humidity"]
    h = 45 if h is None else int(round(h))
    act.kit.show_number(h)
    act.kit.say("Humidity is on", "the display!")
    return "The 7-segment shows the humidity. Is it ABOVE or BELOW 50 %?", ["Above 50", "Below 50"], \
        "Above 50" if h >= 50 else "Below 50"


def remember_weather(kit, act):
    act.memory["t"] = kit.state["temperature"]
    act.memory["h"] = kit.state["humidity"]


def warmed_up(kit, act):
    if act.memory["t"] is None:
        remember_weather(kit, act)
        return False
    return kit.state["humidity"] >= act.memory["h"] + 5 or kit.state["temperature"] >= act.memory["t"] + 1


def live_weather(kit, act):
    kit.show_number(kit.state["humidity"])
    return "Humidity %.0f %% (started at %.0f)   Temp %.1f C (started at %.1f)" % (
        kit.state["humidity"], act.memory["h"], kit.state["temperature"], act.memory["t"])


# ---------------- Day 7: Weather station ----------------
SUN = ["...X....", ".X.X.X..", "..XXX...", "XXXXXXX.", "..XXX...", ".X.X.X..", "...X....", "........"]
MOON = ["..XXX...", ".XX.....", "XX......", "XX......", "XX......", ".XX.....", "..XXX...", "........"]


def comfort(t):
    return "TOO COLD" if t < 18 else "TOO HOT" if t > 26 else "JUST RIGHT"


class WeatherStationTry(TryScreen):
    hint = "Your own weather station: the LCD reads out every sensor in turn. Cover the light sensor for night!"

    def setup(self):
        row = self.row()
        self.temp = self.big(row, "Temperature", "#FF8A65", 26)
        self.hum = self.big(row, "Humidity", "#4FC3F7", 26)
        self.lux = self.big(row, "Light", YELLOW, 26)
        row = self.row()
        self.verdict = self.big(row, "Comfort", GREEN_2, 22)
        self.sky = self.big(row, "Sky", WHITE, 22)
        self.heading("THE LCD SHOWS")
        row = self.row()
        self.show_mode = tk.StringVar(value="All, in turn")
        for name in ("All, in turn", "Temperature", "Humidity", "Light"):
            tk.Radiobutton(row, text=name, value=name, variable=self.show_mode, bg=BG, fg=WHITE,
                           selectcolor=PANEL_2, activebackground=BG, font=self.app.font(12)).pack(side="left",
                                                                                            padx=(0, 10))
        row = self.row()
        self.app.label(row, "Station name:", 12, WHITE, True).pack(side="left")
        self.name = tk.Entry(row, font=self.app.font(13, False), width=14)
        self.name.insert(0, "CrowPi Weather")
        self.name.pack(side="left", padx=8)
        self.turn = 0
        self.last = 0.0

    def update(self, events):
        s = self.kit.state
        t, h, lux = s["temperature"], s["humidity"], s["light"]
        if t is None or lux is None:
            self.temp.config(text="...")
            return
        night = lux < 10
        self.temp.config(text="%.1f C" % t)
        self.hum.config(text="%.0f %%" % h)
        self.lux.config(text="%.0f lux" % lux)
        self.verdict.config(text=comfort(t))
        self.sky.config(text="NIGHT" if night else "DAY")
        self.kit.picture(MOON if night else SUN)
        mode = self.show_mode.get()
        if mode == "All, in turn" and time.time() - self.last > 3:
            self.last = time.time()
            self.turn = (self.turn + 1) % 3
        which = {"Temperature": 0, "Humidity": 1, "Light": 2}.get(mode, self.turn)
        name = self.name.get()[:16] or "Weather"
        line, number = [("%.1f C  " % t + comfort(t)[:7], t), ("Humidity %.0f%%" % h, h),
                        ("Light %.0f lux" % lux, lux)][which]
        self.kit.say(name, line)
        self.kit.show_number(number)


def weather_reading_quiz(act):
    s = act.kit.state
    t = 21.0 if s["temperature"] is None else s["temperature"]
    h = 45.0 if s["humidity"] is None else s["humidity"]
    kind = random.choice(["temperature", "humidity"])
    value = int(round(t if kind == "temperature" else h))
    act.kit.show_number(value)
    act.kit.say("Weather station", "Look at 7-seg!")
    return "The 7-segment display shows the %s. What is it?" % kind, \
        options_with(value, [value - 3, value + 4, value - 6, value + 2]), value


def comfort_quiz(act):
    t = act.kit.state["temperature"]
    t = 21.0 if t is None else t
    act.kit.show_number(t)
    act.kit.say("Temperature:", "%.1f C" % t)
    return "Is this room TOO COLD (under 18), JUST RIGHT or TOO HOT (over 26)?", \
        ["TOO COLD", "JUST RIGHT", "TOO HOT"], comfort(t)


def station_name(kit, text):
    text = text.strip()[:16]
    if not text:
        return "Give your station a name first!"
    kit.say(text, "is on the air!")
    kit.picture(SUN)
    kit.beep(0.05, 2)
    return True


# ---------------- Day 8: Distance ----------------
def distance_now(kit):
    return kit.state["distance"]


class DistanceTry(TryScreen):
    hint = "Hold your hand in front of the two round 'eyes' and move it closer and further."

    def setup(self):
        row = self.row()
        self.cm = self.big(row, "Distance", size=40)
        self.zone = self.big(row, "That's", size=26)
        self.heading("THE LAST 15 SECONDS")
        self.graph = Graph(self.app, self.left, GREEN_2)
        self.graph.canvas.pack(fill="x")
        self.heading("HOW FAR (0 to 100 cm)")
        self.bar = tk.Canvas(self.left, height=30, bg=PANEL_2, highlightthickness=0)
        self.bar.pack(fill="x")

    def update(self, events):
        d = distance_now(self.kit)
        if d is None:
            return
        self.graph.add(d)
        zone = "very close" if d < 10 else "close" if d < 30 else "medium" if d < 60 else "far"
        self.cm.config(text="%.0f cm" % d)
        self.zone.config(text=zone)
        w = self.bar.winfo_width()
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, w * min(d, 100) / 100, 30, fill=GREEN, outline="")
        self.kit.show_number(d)
        self.kit.say("Distance: %.0f cm" % d, zone)
        rows = max(0, min(8, int((60 - d) / 7.5)))
        self.kit.picture(["........"] * (8 - rows) + ["XXXXXXXX"] * rows)


def live_distance(kit, _act):
    kit.show_number(distance_now(kit))
    return "Now: %.0f cm" % distance_now(kit)


# ---------------- Day 9: LCD ----------------
class LcdTry(TryScreen):
    hint = "The LCD has 2 lines of 16 letters each."

    def setup(self):
        self.lines = []
        for n in (1, 2):
            self.heading("LINE %d" % n)
            entry = tk.Entry(self.left, font=("DejaVu Sans Mono", int(16 * self.app.scale)), width=16)
            entry.pack(anchor="w")
            entry.bind("<Return>", lambda _e: self.send())
            self.lines.append(entry)
        self.lines[0].insert(0, "Hello")
        self.lines[1].insert(0, "CrowPi Club!")
        row = self.row(pady=8)
        self.btn(row, "Show it", self.send, GREEN, 13)
        self.btn(row, "Scroll line 1", self.start_scroll, BLUE)
        self.btn(row, "Clear", lambda: self.kit.say("", ""), PANEL_2)
        row = self.row()
        self.btn(row, "Light ON", lambda: self.kit.backlight(True), ORANGE)
        self.btn(row, "Light OFF", lambda: self.kit.backlight(False), PANEL_2)
        self.count = self.app.label(self.left, "", 12, YELLOW, True)
        self.count.pack(anchor="w", pady=6)
        self.scrolling = None
        self.send()

    def send(self):
        self.scrolling = None
        self.kit.say(self.lines[0].get(), self.lines[1].get())
        self.count.config(text="Line 1: %d letters   Line 2: %d letters   (16 fit)" % (
            len(self.lines[0].get()), len(self.lines[1].get())))

    def start_scroll(self):
        self.scrolling = (" " * 16 + self.lines[0].get() + " ", 0, 0.0)

    def update(self, events):
        if self.scrolling:
            text, at, last = self.scrolling
            if time.time() - last > 0.25:
                self.kit.say(text[at:at + 16], self.lines[1].get())
                self.scrolling = (text, (at + 1) % len(text), time.time())

    def leave(self):
        self.kit.backlight(True)


def lcd_name(kit, text):
    if not text.strip():
        return "Type your name first!"
    kit.say("Hi there,", text.strip()[:16])
    return True


def lcd_two_lines(kit, text):
    if "|" not in text:
        return "Put a | between the two lines, like HELLO|WORLD"
    one, two = text.split("|", 1)
    if not one.strip() or not two.strip():
        return "Both lines need some words!"
    kit.say(one.strip()[:16], two.strip()[:16])
    return True


# ---------------- Day 10: RFID ----------------
class RfidTry(TryScreen):
    hint = "Hold the card flat on the RFID area (marked with the wavy symbol) for 2 seconds."

    def setup(self):
        row = self.row()
        self.card = self.big(row, "Card number", size=24)
        self.heading("CARD TEXT (up to 16 letters)")
        row = self.row()
        self.text = tk.Entry(row, font=self.app.font(15, False), width=18)
        self.text.pack(side="left", padx=(0, 8))
        self.btn(row, "Write it", self.write, ORANGE)
        self.btn(row, "Read it", self.read, BLUE)
        self.status = self.app.label(self.left, "Tap a card...", 14, YELLOW, True, wraplength=500,
                                     justify="left")
        self.status.pack(anchor="w", pady=10)
        self.result = None
        self.kit.say("Tap a card", "on the reader")

    def write(self):
        text = self.text.get()[:16]
        self.status.config(text="Hold the card on the reader now...")
        self.kit.say("Writing...", "Hold card still")
        self.kit.write_card_text(text, lambda card: setattr(self, "result", ("wrote", card, text)))

    def read(self):
        self.status.config(text="Hold the card on the reader now...")
        self.kit.say("Reading...", "Hold card still")
        self.kit.read_card_text(lambda card, text: setattr(self, "result", ("read", card, text)))

    def update(self, events):
        for card in events_of(events, "card"):
            self.card.config(text=str(card))
            self.kit.beep(0.05)
            self.kit.say("Card:", str(card))
            self.kit.picture(CHECK)
        if self.result:
            what, card, text = self.result
            self.result = None
            if card is None:
                self.status.config(text="No card found - try again and hold it still.")
                self.kit.say("No card", "Try again")
            elif what == "wrote":
                self.status.config(text="Wrote '%s' on card %s" % (text, card))
                self.kit.say("Saved on card:", text)
                self.kit.beep(0.05, 2)
            else:
                self.status.config(text="Card %s says: '%s'" % (card, (text or "").strip()))
                self.kit.say("Card says:", (text or "").strip())
                self.kit.beep(0.05, 2)


class CardWrite(Entry):
    """Write a name on the card."""
    def __init__(self, show):
        super().__init__(show, "Write on the card", "Type your name, click Send, then hold the card on "
                         "the reader until it beeps.", "Your name:", self.send_text, needs=("cards",))
        self.result = None
        self.busy = False

    def send_text(self, kit, text):
        text = text.strip()[:16]
        if not text:
            return "Type your name first!"
        if self.busy:
            return "Hold the card on the reader..."
        self.busy = True
        self.show.written = text
        kit.say("Writing...", "Hold card still")
        kit.write_card_text(text, lambda card: setattr(self, "result", card))
        return "Hold the card on the reader now..."

    def tick(self, events):
        if self.busy and self.result is not None:
            self.kit.say("Saved on card:", self.show.written)
            return True
        if self.busy and time.time() - self.started > 60:
            self.busy = False
        return False


class CardRead(Step):
    """Read the name back."""
    def __init__(self, show):
        super().__init__(show, "Read it back", "Now READ the card: hold it on the reader again. "
                         "Does it remember your name?", needs=("cards",))
        self.card_text = None
        self.trying = False
        self.wanted = ""

    def start(self):
        super().start()
        self.wanted = getattr(self.show, "written", "")
        self.again()

    def again(self):
        self.trying = True
        self.kit.say("Reading...", "Hold card still")
        self.kit.read_card_text(self.got)

    def got(self, card, text):
        self.card_text = (text or "").strip() if card else None
        self.trying = False

    def tick(self, events):
        if not self.trying:
            if self.card_text and (not self.wanted or self.card_text == self.wanted):
                self.kit.say("Card says:", self.card_text)
                return True
            self.again()
        return False

    def live(self):
        return "Waiting for the card..." if self.card_text is None else "The card says: '%s'" % self.card_text


# ---------------- Day 11: LED matrix ----------------
class MatrixTry(TryScreen):
    hint = "Click the dots to draw. Your picture appears on the real LED matrix!"

    def setup(self):
        area = self.row()
        self.editor = MatrixEditor(self.app, area, self.drawn)
        self.editor.canvas.pack(side="left")
        side = tk.Frame(area, bg=BG, padx=12)
        side.pack(side="left", anchor="n")
        self.app.label(side, "READY-MADE", 10, YELLOW, True).pack(anchor="w")
        for name in ("Smile", "Heart", "Star", "Up arrow"):
            self.app.button(side, name, lambda n=name: self.editor.set(PICTURES[n]), BLUE, 11, padx=10,
                            pady=3, width=10).pack(anchor="w", pady=2)
        self.app.button(side, "Flip colours", self.invert, PURPLE, 11, padx=10, pady=3,
                        width=10).pack(anchor="w", pady=2)
        self.app.button(side, "Clear", lambda: self.editor.set(["........"] * 8), PANEL_2, 11, padx=10,
                        pady=3, width=10).pack(anchor="w", pady=2)
        self.app.label(side, "SCROLL WORDS", 10, YELLOW, True).pack(anchor="w", pady=(10, 0))
        self.words = tk.Entry(side, font=self.app.font(13, False), width=12)
        self.words.pack(anchor="w")
        self.app.button(side, "Scroll", lambda: self.kit.scroll(self.words.get().upper() or "HI"), ORANGE,
                        11, padx=10, pady=3, width=10).pack(anchor="w", pady=2)
        self.editor.set(SMILE)

    def invert(self):
        self.editor.set(["".join("." if ch == "X" else "X" for ch in r) for r in self.editor.picture()])

    def drawn(self, rows):
        self.kit.picture(rows)
        self.kit.show_number(sum(r.count("X") for r in rows))
        self.kit.say("Lights on:", str(sum(r.count("X") for r in rows)))


def picture_quiz(act):
    name = random.choice(list(PICTURES))
    act.kit.picture(PICTURES[name])
    return "What picture is on the LED matrix?", options_with(name, list(PICTURES)), name


class Draw(Step):
    """Draw on the screen; done when test(picture) is True."""
    def __init__(self, show, title, text, test):
        super().__init__(show, title, text)
        self.test = test

    def start(self):
        super().start()
        self.editor = MatrixEditor(self.show.app, self.show.extra, self.kit.picture, cell=22)
        self.editor.canvas.pack(anchor="w")
        self.kit.clear_matrix()

    def tick(self, events):
        return self.test(self.editor.picture())

    def live(self):
        return "Lights on: %d" % self.editor.count()


def four_corners(rows):
    lit = {(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "X"}
    return lit == {(0, 0), (7, 0), (0, 7), (7, 7)}


# ---------------- Day 12: 7-segment ----------------
class SegmentTry(TryScreen):
    hint = "The 7-segment display shows 4 numbers. Each number is made of 7 little bars."

    def setup(self):
        self.heading("TYPE UP TO 4 NUMBERS OR LETTERS")
        row = self.row()
        self.entry = tk.Entry(row, font=self.app.font(18, False), width=6)
        self.entry.pack(side="left", padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self.show_typed())
        self.btn(row, "Show", self.show_typed, GREEN, 13)
        self.heading("FUN THINGS")
        row = self.row()
        self.btn(row, "Count up", lambda: self.start("up"), BLUE)
        self.btn(row, "Countdown 10", lambda: self.start("down"), BLUE)
        self.btn(row, "Clock", lambda: self.start("clock"), PURPLE)
        self.btn(row, "Roll a dice", self.dice, ORANGE)
        self.btn(row, "Stop", lambda: self.start(""), PANEL_2)
        self.status = self.app.label(self.left, "", 14, YELLOW, True)
        self.status.pack(anchor="w", pady=10)
        self.mode = ""
        self.value = 0
        self.last = 0.0
        self.kit.show_text("8888")
        self.kit.say("7-segment", "display")

    def show_typed(self):
        self.mode = ""
        self.kit.show_text(self.entry.get()[:4])
        self.status.config(text="Showing: %s" % self.entry.get()[:4])

    def start(self, mode):
        self.mode = mode
        self.value = 10 if mode == "down" else 0
        self.last = 0.0

    def dice(self):
        self.mode = ""
        n = random.randint(1, 6)
        self.kit.show_number(n)
        self.kit.beep(0.05)
        self.status.config(text="You rolled a %d!" % n)

    def update(self, events):
        if not self.mode or time.time() - self.last < (1 if self.mode != "up" else 0.2):
            return
        self.last = time.time()
        if self.mode == "clock":
            now = time.localtime()
            self.kit.show_time(now.tm_hour, now.tm_min, now.tm_sec % 2 == 0)
            self.status.config(text="The time is %02d:%02d" % (now.tm_hour, now.tm_min))
        elif self.mode == "up":
            self.value += 1
            self.kit.show_number(self.value)
            self.status.config(text="Counting: %d" % self.value)
        else:
            self.kit.show_number(self.value)
            self.status.config(text="Countdown: %d" % self.value)
            if self.value == 0:
                self.kit.beep(0.5)
                self.mode = ""
            self.value -= 1


def number_quiz(act):
    n = random.randint(10, 9999)
    act.kit.show_number(n)
    near = [n + 1, n - 10, int(str(n)[::-1]), n + 100, n - 1]
    return "What number is on the 7-segment display?", options_with(n, [m for m in near if m != n]), n


def up_down_quiz(act):
    answer = random.choice(["Up", "Down"])
    start = random.randint(10, 80)
    numbers = [start + i if answer == "Up" else start - i for i in range(5)]

    def play():
        for i, number in enumerate(numbers):
            act.show.frame.after(i * 500, lambda number=number: act.kit.show_number(number))
    act.replay = play
    play()
    return "Watch the display! Is it counting UP or DOWN?", ["Up", "Down"], answer


def show_2026(kit, text):
    text = text.strip()
    kit.show_text(text[:4])
    return True if text == "2026" else "It shows %s - make it show 2026!" % (text or "nothing")


# ---------------- Day 13: Touch ----------------
class TouchTry(TryScreen):
    hint = "Touch the round touch pad lightly with one finger."

    def setup(self):
        row = self.row()
        self.now = self.big(row, "Right now", size=32)
        self.taps_label = self.big(row, "Taps", size=32)
        self.held_label = self.big(row, "Held for", size=32)
        row = self.row()
        self.lamp = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Lamp mode: each tap turns the relay and matrix ON / OFF",
                       variable=self.lamp, bg=BG, fg=WHITE, selectcolor=PANEL_2, activebackground=BG,
                       font=self.app.font(12)).pack(side="left")
        self.taps = 0
        self.lamp_on = False
        self.since = None

    def update(self, events):
        for _ in events_of(events, "touch"):
            self.taps += 1
            self.kit.beep(0.03)
            if self.lamp.get():
                self.lamp_on = not self.lamp_on
                self.kit.relay_on(self.lamp_on)
        touched = self.kit.state["touch"]
        if touched and self.since is None:
            self.since = time.time()
        elif not touched:
            self.since = None
        held = time.time() - self.since if self.since else 0
        self.now.config(text="TOUCHED" if touched else "-", fg=GREEN_2 if touched else TEXT)
        self.taps_label.config(text=str(self.taps))
        self.held_label.config(text="%.1f s" % held)
        self.kit.show_number(self.taps)
        self.kit.say("Touched!" if touched else "Touch the pad", "Taps: %d" % self.taps)
        lamp = self.lamp.get() and self.lamp_on
        self.kit.picture(FULL if lamp or touched else HEART if self.lamp.get() else SMILE)

    def leave(self):
        self.kit.relay_on(False)


def five_fast_taps(kit, act):
    taps = act.memory.setdefault("taps", [])
    now = time.time()
    for _ in events_of(act.events, "touch"):
        taps.append(now)
        kit.beep(0.03)
    taps[:] = [t for t in taps if now - t <= 5]
    kit.show_number(len(taps))
    return len(taps) >= 5


def live_taps(_kit, act):
    return "Taps in the last 5 seconds: %d" % len(act.memory.get("taps", []))


# ---------------- Day 14: Button matrix ----------------
class GridTry(TryScreen):
    hint = "Press the 16 buttons on the kit. The screen shows which one!"

    def setup(self):
        area = self.row()
        self.grid4 = Grid4(self.app, area)
        self.grid4.canvas.pack(side="left")
        side = tk.Frame(area, bg=BG, padx=12)
        side.pack(side="left", anchor="n", fill="both", expand=True)
        self.last = self.app.label(side, "Press a button!", 22, YELLOW, True)
        self.last.pack(anchor="w")
        self.where = self.app.label(side, "", 13, TEXT, True, justify="left")
        self.where.pack(anchor="w", pady=6)
        self.kit.say("Press a button", "1 to 16")
        self.kit.clear_matrix()

    def update(self, events):
        held = self.kit.state["grid"]
        self.grid4.show({held: ORANGE} if held else {})
        for button in events_of(events, "grid"):
            row, column = ButtonMatrix.row_and_column(button)
            self.last.config(text="Button %d" % button)
            self.where.config(text="Row %d, column %d" % (row + 1, column + 1))
            self.kit.show_number(button)
            self.kit.say("Button %d" % button, "Row %d Col %d" % (row + 1, column + 1))
            self.kit.squares([button])
            self.kit.beep(0.03)


def grid_react(kit, button):
    kit.squares([button])
    kit.show_number(button)
    kit.beep(0.03)


def show_grid_target(kit, button):
    kit.squares([button])
    kit.show_number(button)
    kit.say("Press button", str(button))


# ---------------- Day 15: Button piano ----------------
NOTES = {
    1: ("C", 262), 2: ("D", 294), 3: ("E", 330), 4: ("F", 349),
    5: ("G", 392), 6: ("A", 440), 7: ("B", 494), 8: ("high C", 523),
    9: ("high D", 587), 10: ("high E", 659), 11: ("high F", 698), 12: ("high G", 784),
    13: ("high A", 880), 14: ("high B", 988), 15: ("top C", 1047), 16: ("top D", 1175),
}
MARY = [3, 2, 1, 2, 3, 3, 3, 0, 2, 2, 2, 0, 3, 5, 5]


def piano_setup(kit):
    kit.use_tones()


def piano_hook(screen, _events):
    """Hold a button = play its note."""
    app = screen.app
    held = screen.kit.state["grid"] if screen.kit.mode == "grid" else None
    if held != getattr(app, "piano_held", None):
        app.piano_held = held
        if held:
            name, frequency = NOTES[held]
            screen.kit.tone(frequency)
            screen.kit.say("Button %d" % held, "%s  %d Hz" % (name, frequency))
            screen.kit.squares([held])
            screen.kit.show_number(frequency)
        elif not getattr(app, "piano_auto", False):
            screen.kit.quiet()


def play_notes(screen, buttons, gap=0.35):
    """Play a tune by itself (0 = a rest)."""
    app = screen.app
    app.piano_auto = True
    for i, button in enumerate(buttons):
        if button:
            screen.frame.after(int(i * gap * 1000), lambda b=button: (screen.kit.tone(NOTES[b][1]),
                                                                      screen.kit.squares([b])))
        screen.frame.after(int((i + 0.8) * gap * 1000), screen.kit.quiet)

    def finished():
        app.piano_auto = False
    screen.frame.after(int(len(buttons) * gap * 1000) + 100, finished)


class PianoTry(TryScreen):
    hint = "Hold a button to play its note. Button 1 is the lowest, 16 the highest. Try 3 2 1 2 3 3 3!"

    def setup(self):
        area = self.row()
        labels = {b: "%d\n%s" % (b, n) for b, (n, _f) in NOTES.items()}
        self.grid4 = Grid4(self.app, area, labels, self.clicked, cell=66)
        self.grid4.canvas.pack(side="left")
        side = tk.Frame(area, bg=BG, padx=12)
        side.pack(side="left", anchor="n")
        self.note = self.app.label(side, "Play a note!", 20, YELLOW, True)
        self.note.pack(anchor="w")
        self.app.button(side, "Play 'Mary had a little lamb'", lambda: play_notes(self, MARY), ORANGE, 12,
                        padx=10, pady=4).pack(anchor="w", pady=6)
        self.app.button(side, "Play the scale", lambda: play_notes(self, list(range(1, 9)) +
                                                                   list(range(8, 0, -1)), 0.25),
                        BLUE, 12, padx=10, pady=4).pack(anchor="w")
        self.kit.say("Button Piano", "Hold a button!")

    def clicked(self, button):
        play_notes(self, [button], 0.4)
        self.note.config(text="%s  (%d Hz)" % NOTES[button])

    def update(self, events):
        held = self.kit.state["grid"]
        self.grid4.show({held: ORANGE} if held else {})
        if held:
            self.note.config(text="%s  (%d Hz)" % NOTES[held])


def higher_note_quiz(act):
    a, b = random.sample(range(1, 17), 2)
    act.replay = lambda: play_notes(act.show, [a, 0, b], 0.5)
    act.show.frame.after(300, act.replay)
    return "Listen to two notes. Which one is HIGHER?", ["First", "Second"], "First" if a > b else "Second"


# ---------------- Day 16: IR remote ----------------
class RemoteTry(TryScreen):
    hint = "Point the remote at the IR receiver and press a key. It talks with invisible light!"

    def setup(self):
        row = self.row()
        self.key = self.big(row, "Last key", size=36)
        self.count_label = self.big(row, "Keys pressed", size=36)
        self.heading("KEY HISTORY")
        self.log = tk.Listbox(self.left, height=7, bg=PANEL, fg=WHITE, font=self.app.font(12, False),
                              highlightthickness=0, bd=0)
        self.log.pack(fill="x")
        self.count = 0
        self.kit.say("Press a key on", "the remote!")

    def update(self, events):
        for key in events_of(events, "ir"):
            self.count += 1
            self.key.config(text=key)
            self.count_label.config(text=str(self.count))
            self.log.insert(0, "  %s   %s" % (time.strftime("%H:%M:%S"), key))
            self.kit.say("Remote key:", key)
            self.kit.show_text(key[:4])
            self.kit.beep(0.03)
            if key in ("CH+", "VOL+", "NEXT"):
                self.kit.picture(ARROWS["UP"])
            elif key in ("CH-", "VOL-", "PREV"):
                self.kit.picture(ARROWS["DOWN"])
            else:
                self.kit.picture(SMILE)


def ir_react(kit, key):
    kit.say("Remote key:", key)
    kit.show_text(key[:4])
    kit.beep(0.03)


def show_key_target(kit, key):
    kit.say("Press this key:", key)
    kit.show_text(key)


# ---------------- Day 17: Camera ----------------
YEARBOOK = os.path.join(os.path.expanduser("~"), "Pictures", "CrowPi_Photos", "Yearbook")


def choose_yearbook_photo(app):
    """Pick ONE photo from this lesson for the yearbook. That one is saved in
    Pictures > CrowPi_Photos > Yearbook (with your name), the others are deleted.
    Returns True when a photo was saved (or none was wanted)."""
    photos = [p for p in app.photos_taken if os.path.exists(p)]
    if not photos:
        return True
    window = tk.Toplevel(app.root, bg=BG)
    window.title("Yearbook photo")
    window.transient(app.root)
    app.label(window, "Pick your yearbook photo", 18, WHITE, True).pack(padx=20, pady=(14, 2))
    app.label(window, "Tap the photo you like best. Only that one is kept - the others are deleted.", 11,
              TEXT).pack(padx=20)
    row = tk.Frame(window, bg=BG)
    row.pack(padx=20, pady=6)
    app.label(row, "Your name:", 13, WHITE, True).pack(side="left")
    name = tk.Entry(row, font=app.font(14, False), width=18)
    name.pack(side="left", padx=8)
    grid = tk.Frame(window, bg=BG)
    grid.pack(padx=20, pady=6)
    chosen: dict = {"path": None, "saved": False}
    tiles = {}
    images = []
    size = int(200 * app.scale)
    for i, path in enumerate(photos[-8:]):
        tile = tk.Frame(grid, bg=PANEL, padx=4, pady=4, cursor="hand2")
        tile.grid(row=i // 4, column=i % 4, padx=6, pady=6)
        try:
            from PIL import Image
            small = os.path.join(os.path.expanduser("~"), ".crowpi_yearbook_%d.png" % i)
            image = Image.open(path)
            image.thumbnail((size, size))
            image.save(small)
            images.append(tk.PhotoImage(file=small))
            picture = tk.Label(tile, image=images[-1], bg=PANEL)
        except Exception:
            picture = tk.Label(tile, text="Photo %d" % (i + 1), width=14, height=6, bg=PANEL_2, fg=WHITE,
                               font=app.font(12))
        picture.pack()
        label = app.label(tile, "Photo %d" % (i + 1), 11, TEXT, True, bg=PANEL)
        label.pack()
        tiles[path] = tile

        def pick(_event=None, p=path):
            chosen["path"] = p
            for other, t in tiles.items():
                t.config(bg=GREEN if other == p else PANEL)
            note.config(text="")
        for widget in (tile, picture, label):
            widget.bind("<Button-1>", pick)
    note = app.label(window, "", 12, "#FF8A80", True)
    note.pack()

    def save():
        if not chosen["path"]:
            note.config(text="Tap a photo first!")
            return
        who = "".join(ch for ch in name.get().strip() if ch.isalnum() or ch in " -_")[:30].strip()
        if not who:
            note.config(text="Type your name first!")
            name.focus_set()
            return
        os.makedirs(YEARBOOK, exist_ok=True)
        target = os.path.join(YEARBOOK, who.replace(" ", "_") + os.path.splitext(chosen["path"])[1].lower())
        shutil.copyfile(chosen["path"], target)
        for path in photos:                         # only the yearbook photo is kept
            try:
                os.remove(path)
            except OSError:
                pass
        app.photos_taken = []
        app.yearbook_done = True
        chosen["saved"] = True
        print("[yearbook] saved", target)
        window.destroy()

    def none():
        for path in photos:
            try:
                os.remove(path)
            except OSError:
                pass
        app.photos_taken = []
        app.yearbook_done = True
        chosen["saved"] = True
        window.destroy()

    buttons = tk.Frame(window, bg=BG)
    buttons.pack(pady=(6, 14))
    app.button(buttons, "Save this one for the yearbook", save, GREEN, 13, padx=16, pady=6).pack(side="left", padx=6)
    app.button(buttons, "Keep none", none, PANEL_2, 11, padx=12, pady=6).pack(side="left", padx=6)
    window.update_idletasks()
    x = app.root.winfo_rootx() + (app.root.winfo_width() - window.winfo_width()) // 2
    y = app.root.winfo_rooty() + (app.root.winfo_height() - window.winfo_height()) // 3
    window.geometry("+%d+%d" % (max(0, x), max(0, y)))
    try:
        window.grab_set()
    except tk.TclError:
        pass
    name.focus_force()
    app.yearbook_window = window
    app.root.wait_window(window)
    app.yearbook_window = None
    return chosen["saved"]


class YearbookStep(Step):
    """The last camera challenge: choose ONE photo for the yearbook."""
    def __init__(self, show):
        super().__init__(show, "Yearbook photo", "Pick your best photo for the class yearbook. "
                         "Only that one is kept.", needs=("camera",))

    def start(self):
        super().start()
        self.show.app.yearbook_done = False
        self.show.app.button(self.show.extra, "Choose my yearbook photo", self.choose, ORANGE, 13,
                             padx=14, pady=6).pack(anchor="w")

    def choose(self):
        choose_yearbook_photo(self.show.app)

    def tick(self, events):
        return bool(self.show.app.yearbook_done)

    def live(self):
        n = len([p for p in self.show.app.photos_taken if os.path.exists(p)])
        return "%d photos to choose from" % n if n else "Take a photo first (Try it tab or the other challenges)."


class CameraTry(TryScreen):
    hint = "Photos are saved in Pictures > CrowPi_Photos. Pick ONE for the yearbook!"

    def setup(self):
        row = self.row()
        self.btn(row, "Take a photo (3, 2, 1...)", self.countdown, ORANGE, 14)
        self.btn(row, "Choose my yearbook photo", lambda: choose_yearbook_photo(self.app), GREEN, 14)
        self.status = self.app.label(self.left, "", 13, YELLOW, True)
        self.status.pack(anchor="w", pady=6)
        self.picture = tk.Label(self.left, bg=BG)
        self.picture.pack(anchor="w")
        self.path = None
        self.image = None
        self.taking = False
        self.kit.say("Camera", "Smile!")

    def countdown(self):
        if self.taking:
            return
        if not self.kit.camera or self.kit.camera == "looking":
            self.status.config(text="No camera found - plug in a USB camera, then open the lesson again.")
            return
        self.taking = True
        def count(n):
            self.kit.show_number(n)
            self.kit.beep(0.05)
            self.status.config(text="%d..." % n)
        for i in range(3):
            self.frame.after(i * 1000, count, 3 - i)
        self.frame.after(3000, self.snap)

    def snap(self):
        self.status.config(text="SMILE!")
        self.kit.show_text("SNAP")
        self.kit.take_photo(lambda path: setattr(self, "path", path or ""))

    def update(self, events):
        if self.path is not None:
            path, self.path = self.path, None
            self.taking = False
            if not path:
                self.status.config(text="The photo didn't work - try again.")
                return
            self.kit.beep(0.1)
            self.app.photos_taken.append(path)
            self.app.yearbook_done = False
            self.status.config(text="Photo %d taken! Take more, then choose one for the yearbook."
                                    % len(self.app.photos_taken))
            try:
                from PIL import Image
                small = os.path.join(os.path.expanduser("~"), ".crowpi_lesson_photo.png")
                image = Image.open(path)
                image.thumbnail((int(420 * self.app.scale), int(280 * self.app.scale)))
                image.save(small)
                self.image = tk.PhotoImage(file=small)
                self.picture.config(image=self.image)
            except Exception as problem:
                print("Couldn't show the photo:", problem)


class Photo(Step):
    def __init__(self, show, title, text):
        super().__init__(show, title, text, needs=("camera",))
        self.path = None

    def start(self):
        super().start()
        self.path = None
        self.asked = False
        self.show.app.button(self.show.extra, "Take it! (3, 2, 1...)", self.countdown, ORANGE, 13,
                             padx=14, pady=6).pack(anchor="w")

    def countdown(self):
        if self.asked:
            return
        self.asked = True
        for i in range(3):
            self.show.frame.after(i * 1000, self.kit.show_number, 3 - i)
            self.show.frame.after(i * 1000, self.kit.beep, 0.05)
        self.show.frame.after(3000, lambda: self.kit.take_photo(lambda p: setattr(self, "path", p or "")))

    def tick(self, events):
        if self.path is None:
            return False
        if not self.path:
            self.path = None
            self.asked = False
            return False
        self.show.show_photo(self.path)
        self.show.app.photos_taken.append(self.path)
        return True

    def live(self):
        return "Click 'Take it!' when you're ready." if not self.asked else "Smile..."


# ---------------- Day 18: Reaction ----------------
class Reaction(Step):
    """Touch the pad as soon as it beeps - faster than 'goal' milliseconds."""
    def __init__(self, show, goal):
        super().__init__(show, "Faster than %d ms" % goal, "Wait for the BEEP (and the matrix lighting "
                         "up), then touch the pad as fast as you can. Under %d ms wins!" % goal,
                         needs=("touch",))
        self.goal = goal
        self.last = None

    def start(self):
        super().start()
        self.new_round()

    def new_round(self):
        self.state = "wait"
        self.go = time.time() + random.uniform(2, 4)
        self.kit.clear_matrix()
        self.kit.say("Wait for it...", "")

    def tick(self, events):
        touched = bool(events_of(events, "touch"))
        now = time.time()
        if self.state == "wait":
            if touched:
                self.kit.say("TOO EARLY!", "Wait for beep")
                self.go = now + random.uniform(2, 4)
            elif now >= self.go:
                self.state = "go"
                if TESTING:
                    print("TEST go")
                self.kit.picture(FULL)
                self.kit.beep(0.15)
        elif self.state == "go":
            if touched:
                self.last = int((now - self.go) * 1000)
                self.kit.show_number(self.last)
                if self.last < self.goal:
                    self.kit.say("%d ms - YES!" % self.last, "")
                    return True
                self.kit.say("%d ms" % self.last, "Faster! Again...")
                self.new_round()
            elif now - self.go > 3:
                self.new_round()
        return False

    def live(self):
        return "Your last time: %d ms" % self.last if self.last else "Get ready..."


# ---------------- Day 19: Parking sensor ----------------
STOP_SIGN = ["X......X", ".X....X.", "..X..X..", "...XX...", "...XX...", "..X..X..", ".X....X.",
             "X......X"]


def parking_hook(screen, _events):
    """The parking sensor: beeps faster as you get closer."""
    kit = screen.kit
    d = distance_now(kit)
    if d is None:
        return
    kit.show_number(d)
    now = time.time()
    if d < 5:
        kit.picture(STOP_SIGN)
        kit.say("STOP!", "%.0f cm" % d)
        if now >= getattr(screen, "next_beep", 0):
            kit.beep(0.25)
            screen.next_beep = now + 0.3
        return
    rows = max(0, min(8, int((45 - d) / 5)))
    kit.picture(["........"] * (8 - rows) + ["XXXXXXXX"] * rows)
    kit.say("Distance: %.0f cm" % d, "Keep going" if d > 30 else "Slowly..." if d > 10 else "Nearly!")
    if d <= 50 and now >= getattr(screen, "next_beep", 0):
        kit.beep(0.04)
        screen.next_beep = now + 0.05 + d / 100


class ParkingTry(TryScreen):
    hint = "Pretend your hand is a car backing into a parking space. Move it slowly towards the sensor!"

    def setup(self):
        row = self.row()
        self.cm = self.big(row, "Distance", size=40)
        self.zone = self.big(row, "Driver says", size=26)
        self.heading("THE PARKING SPACE")
        self.road = tk.Canvas(self.left, height=int(110 * self.app.scale), bg=PANEL_2, highlightthickness=0)
        self.road.pack(fill="x")

    def update(self, events):
        d = distance_now(self.kit)
        if d is None:
            return
        self.cm.config(text="%.0f cm" % d)
        zone = "STOP!" if d < 5 else "Nearly..." if d < 10 else "Slowly" if d < 30 else "Keep going"
        self.zone.config(text=zone, fg="#FF8A80" if d < 5 else YELLOW if d < 30 else GREEN_2)
        c = self.road
        w, h = c.winfo_width(), c.winfo_height()
        c.delete("all")
        c.create_rectangle(w - 16, 0, w, h, fill=RED, outline="")
        x = w - 16 - min(d, 60) / 60 * (w - 140)
        c.create_rectangle(x - 110, h / 2 - 22, x, h / 2 + 22, fill=BLUE, outline="")
        c.create_text(x - 55, h / 2, text="CAR", fill=WHITE, font=self.app.font(14))


# ---------------- Day 20: Security alarm ----------------
class Alarm:
    """The alarm: save a key card, arm, watch the tripwire, ring, key card turns it off.
    The tripwire is the distance sensor: anything closer than TRIP_CM sets it off."""
    ARM_SECONDS = 10
    TRIP_CM = 30

    def __init__(self, kit):
        self.kit = kit
        self.key = None
        self.state = "need key"
        self.armed_at = 0.0
        self.note = "Tap a card to save it as the KEY."
        self.times_rung = 0

    def tick(self, events):
        kit, now = self.kit, time.time()
        for card in events_of(events, "card"):
            if self.key is None:
                self.key = card
                self.arm()
            elif card != self.key:
                self.note = "WRONG CARD! Only the key card works."
                kit.say("WRONG CARD!", "")
                kit.beep(0.05, 3)
            elif self.state in ("alarm", "watching", "arming"):
                self.state = "off"
                self.note = "Alarm OFF. Tap the key card again to turn it back on."
                kit.say("Alarm OFF", "Key card OK")
                kit.picture(CHECK)
                kit.beep(0.05, 2)
            else:
                self.arm()
        if self.state == "arming":
            left = self.ARM_SECONDS - (now - self.armed_at)
            kit.show_number(max(0, left) + 0.5)
            kit.say("Alarm ON in", "%d seconds" % (left + 1))
            if left <= 0:
                self.state = "watching"
                self.note = "WATCHING... anything closer than %d cm to the distance sensor sets it off!" \
                    % self.TRIP_CM
                kit.say("ALARM IS ON", "Watching...")
                kit.show_text("ON")
                kit.picture(["........"] * 3 + ["...XX..."] * 2 + ["........"] * 3)
        elif self.state == "watching":
            d = kit.state["distance"]
            if d is not None and d < self.TRIP_CM:
                self.state = "alarm"
                self.times_rung += 1
                self.note = "INTRUDER! Tap the key card to stop the alarm."
        if self.state == "alarm":
            flash = int(now * 4) % 2 == 0
            kit.picture(FULL if flash else ["........"] * 8)
            kit.say("!!! INTRUDER !!!", "Tap key card")
            kit.show_text("HELP" if flash else "    ")
            if not kit.buzzing():
                kit.beep(0.12)

    def arm(self):
        self.state = "arming"
        self.armed_at = time.time()
        self.note = "Key saved! Walk away - the alarm turns on in %d seconds." % self.ARM_SECONDS
        self.kit.beep(0.05)


def alarm_of(screen):
    alarm = getattr(screen.app, "alarm", None)
    if alarm is None:
        alarm = screen.app.alarm = Alarm(screen.kit)
    return alarm


def security_hook(screen, events):
    alarm_of(screen).tick(events)


class AlarmTry(TryScreen):
    hint = ("The distance sensor is the tripwire: once the alarm is ON, a hand closer than 30 cm sets it off. "
            "Tap the key card on the reader from the side.")

    def setup(self):
        row = self.row()
        self.state = self.big(row, "Alarm", size=30)
        self.key = self.big(row, "Key card", size=20)
        self.note = self.app.label(self.left, "", 15, YELLOW, True, wraplength=int(520 * self.app.scale),
                                   justify="left")
        self.note.pack(anchor="w", pady=10)
        row = self.row()
        self.btn(row, "Forget the key card", self.reset, PANEL_2)

    def reset(self):
        self.app.alarm = Alarm(self.kit)

    def update(self, events):
        alarm = alarm_of(self)
        names = {"need key": ("no key yet", TEXT), "arming": ("ARMING", YELLOW), "watching": ("ON", GREEN_2),
                 "alarm": ("RINGING!", "#FF8A80"), "off": ("OFF", TEXT)}
        text, color = names[alarm.state]
        self.state.config(text=text, fg=color)
        self.key.config(text=str(alarm.key) if alarm.key else "-")
        self.note.config(text=alarm.note)


def alarm_rang(_kit, act):
    alarm = alarm_of(act.show)
    act.memory.setdefault("rung", alarm.times_rung)
    if alarm.state == "off" and alarm.key:
        alarm.arm()
    return alarm.times_rung > act.memory["rung"]


def alarm_live(_kit, act):
    return alarm_of(act.show).note


# ---------------- Day 21: Simon says ----------------
class Simon(Step):
    """Copy the pattern on the button grid - up to 'goal' rounds."""
    corners = [1, 4, 13, 16]

    def __init__(self, show, goal):
        super().__init__(show, "Reach round %d" % goal, "Watch the corner squares light up, then press the "
                         "same buttons in the same order. A mistake starts again from round 1.")
        self.goal = goal
        self.best = 0

    def start(self):
        super().start()
        self.sequence: list = []
        self.next_round()

    def next_round(self):
        self.sequence.append(random.choice(self.corners))
        if TESTING:
            print("TEST pattern", " ".join(str(b) for b in self.sequence))
        self.showing = 0
        self.show_until = time.time() + 1.0
        self.answer = 0
        self.kit.say("SIMON SAYS", "Round %d" % len(self.sequence))
        self.kit.show_number(len(self.sequence))
        self.kit.clear_matrix()

    def tick(self, events):
        now = time.time()
        if self.showing >= 0:
            if now >= self.show_until:
                i = self.showing
                if i < len(self.sequence) * 2:
                    if i % 2 == 0:
                        self.kit.squares([self.sequence[i // 2]])
                        self.kit.beep(0.08)
                        self.show_until = now + 0.55
                    else:
                        self.kit.clear_matrix()
                        self.show_until = now + 0.25
                    self.showing += 1
                else:
                    self.showing = -1
                    self.kit.say("Your turn!", "Round %d" % len(self.sequence))
            return False
        for button in events_of(events, "grid"):
            if button == self.sequence[self.answer]:
                self.kit.squares([button])
                self.kit.beep(0.04)
                self.answer += 1
                if self.answer == len(self.sequence):
                    self.best = max(self.best, len(self.sequence))
                    if len(self.sequence) >= self.goal:
                        return True
                    self.next_round()
                    return False
            else:
                self.kit.beep(0.5)
                self.kit.say("Oops! Again", "from round 1")
                self.sequence = []
                self.next_round()
                return False
        return False

    def live(self):
        return "Round %d of %d" % (len(self.sequence), self.goal) + ("   - your turn!" if self.showing < 0
                                                                     else "   - watch...")

    def progress(self):
        return (len(self.sequence) - 1) / self.goal


# ---------------- Bonus: Matrix lights ----------------
def lights_of(screen):
    lights = getattr(screen.app, "lights", None)
    if lights is None:
        lights = screen.app.lights = set()
    return lights


def lights_hook(screen, events):
    lights = lights_of(screen)
    for button in events_of(events, "grid"):
        lights.symmetric_difference_update({button})
        screen.kit.squares(lights)
        screen.kit.show_number(len(lights))
        screen.kit.beep(0.02)
    screen.kit.squares(lights)


class LightsTry(TryScreen):
    hint = "Each button turns its square ON or OFF. You can click the squares on the screen too."

    def setup(self):
        area = self.row()
        self.grid4 = Grid4(self.app, area, clicked=self.clicked)
        self.grid4.canvas.pack(side="left")
        side = tk.Frame(area, bg=BG, padx=12)
        side.pack(side="left", anchor="n")
        self.count = self.app.label(side, "", 20, YELLOW, True)
        self.count.pack(anchor="w")
        self.app.button(side, "All on", lambda: self.set(set(range(1, 17))), BLUE, 12, padx=10,
                        pady=4).pack(anchor="w", pady=4)
        self.app.button(side, "All off", lambda: self.set(set()), PANEL_2, 12, padx=10,
                        pady=4).pack(anchor="w")
        self.kit.say("Matrix Lights", "Press buttons!")

    def set(self, lights):
        current = lights_of(self)
        current.clear()
        current.update(lights)

    def clicked(self, button):
        lights_of(self).symmetric_difference_update({button})
        self.kit.beep(0.02)

    def update(self, events):
        lights = lights_of(self)
        self.grid4.show({b: YELLOW for b in lights})
        self.count.config(text="%d of 16 on" % len(lights))
        self.kit.show_number(len(lights))


CHECKER = ({1, 3, 6, 8, 9, 11, 14, 16}, {2, 4, 5, 7, 10, 12, 13, 15})


# ---------------- all the lessons ----------------
LESSONS = {
    "hello": Lesson(
        "hello_crowpi.py", 1, "Hello, CrowPi!", "Day 1 - Hello, CrowPi!",
        [("A computer you can build with",
          "The CrowPi is a Raspberry Pi computer with lots of parts built in: screens, lights, sensors "
          "and buttons."),
         ("Programs tell the parts what to do",
          "A program is a list of instructions. This app sends instructions to the LCD screen, "
          "the LED matrix, the 7-segment display and the buzzer."),
         ("Three screens!",
          "The LCD shows words. The LED matrix (8 x 8 red dots) shows pictures. The 7-segment display "
          "shows numbers. The panel on the right copies what they show.")],
        HelloTry,
        [lambda s: Entry(s, "Say hello", "Type your name and click Send - the kit says hello to you!",
                         "Your name:", hello_entry),
         lambda s: Quiz(s, "Read the LCD", "Look at the LCD screen ON THE KIT and pick the word it shows.",
                        lcd_word_quiz, rounds=2, needs=("lcd",), hide_mirror=True),
         lambda s: Quiz(s, "Count the lights", "Look at the LED matrix ON THE KIT and count the red dots.",
                        count_lights_quiz, rounds=2, needs=("matrix",), hide_mirror=True)],
        parts="LCD screen, LED matrix, 7-segment display, buzzer"),
    "buzzer": Lesson(
        "buzzer.py", 2, "The Buzzer", "Day 2 - Buzzer",
        [("ON makes a sound", "The buzzer makes a sound while the program keeps it ON, and stops when "
                              "it turns it OFF."),
         ("Timing is everything", "A short ON = a short beep. Change how long it stays ON and how long "
                                  "it waits, and you can make patterns like a doorbell or SOS."),
         ("Can't hear it?", "Check the buzzer switch in the CrowPi Lessons app is ON.")],
        BuzzerTry,
        [lambda s: Quiz(s, "Count the beeps", "Listen carefully and count the beeps.", count_beeps_quiz,
                        rounds=2, needs=("buzzer",)),
         lambda s: Quiz(s, "Short or long?", "Is the beep short or long?", short_long_quiz, rounds=3,
                        needs=("buzzer",)),
         lambda s: Quiz(s, "Name that pattern", "Which beep pattern is playing?", pattern_quiz, rounds=2,
                        needs=("buzzer",))],
        parts="the buzzer"),
    "button_buzzer": Lesson(
        "button_buzzer.py", 3, "Buttons + Buzzer", "Day 3 - Buttons + Buzzer",
        [("Inputs and outputs", "A button is an INPUT: it tells the program something. The buzzer is an "
                                "OUTPUT: the program tells it what to do."),
         ("Press = do something", "The program keeps checking: is a button pressed? When it is, it beeps "
                                  "and shows that arrow."),
         ("BUTTON MODE", "The arrow buttons only work when the DIP switches are set to BUTTON MODE.")],
        ArrowTry,
        [lambda s: Count(s, "Press all 4 arrows", "Press UP, DOWN, LEFT and RIGHT (any order).", "arrow", 4,
                         distinct=True, react=arrow_react),
         lambda s: Count(s, "UP three times", "Press the UP arrow 3 times.", "arrow", 3,
                         accept=lambda d: d == "UP", react=arrow_react),
         lambda s: Targets(s, "Follow the arrows", "Press the arrow that the LED matrix shows. 5 to go!",
                           "arrow", lambda: random.choice(list(ARROW_PINS)), show_arrow_target, 5)],
        switches=True, parts="the 4 arrow buttons, buzzer, LED matrix"),
    "relay": Lesson(
        "relay.py", 4, "The Relay", "Day 4 - Relay",
        [("A switch the computer flips", "A relay is a switch inside a little box. The program flips it "
                                         "ON and OFF - you hear it CLICK."),
         ("Small signal, big job", "Real relays let a tiny computer switch big things like lamps, fans "
                                   "and pumps."),
         ("Watch the light", "The little light next to the relay shows when it's ON.")],
        RelayTry,
        [lambda s: Quiz(s, "Count the clicks", "Listen: how many times does the relay click ON?",
                        relay_clicks_quiz, rounds=2, needs=("relay",)),
         lambda s: Quiz(s, "ON or OFF?", "Look at the relay's light on the kit.", relay_on_off_quiz,
                        rounds=2, needs=("relay",)),
         lambda s: Count(s, "Make a light switch", "Touch the touch pad to switch the relay ON and OFF. "
                         "Do it 4 times!", "touch", 4, react=relay_switch, needs=("relay", "touch"))],
        parts="the relay (and the touch pad)"),
    "light": Lesson(
        "light_sensor.py", 5, "Light Sensor", "Day 5 - Light Sensor",
        [("It measures light", "The light sensor measures how bright it is, in LUX."),
         ("Dark to bright", "A dark cupboard is about 1 lux, a classroom about 300, and sunshine "
                            "over 10,000!"),
         ("Machines that see light", "Phones use one to dim the screen. Street lights use one to turn on "
                                     "at night.")],
        LightTry,
        [lambda s: Until(s, "Make it dark", "Cover the light sensor with your hand.",
                         lambda k, a: light_now(k) < max(5, a.memory["start"] / 4), remember_light,
                         live_light, hold=1, needs=("light",)),
         lambda s: Until(s, "Make it bright", "Shine a torch on the sensor (or take it near a window).",
                         lambda k, a: light_now(k) > max(50, a.memory["start"] * 2), remember_light,
                         live_light, hold=1, needs=("light",)),
         lambda s: Until(s, "Halfway", "Make the light about HALF of what it is now - cover it a little. "
                         "Hold it there for 2 seconds.",
                         lambda k, a: 0.3 * a.memory["start"] <= light_now(k) <= 0.7 * a.memory["start"],
                         remember_light, live_light, hold=2, needs=("light",))],
        parts="the light sensor"),
    "dht20": Lesson(
        "dht20.py", 6, "Temperature + Humidity", "Day 6 - Temperature + Humidity",
        [("Two readings in one", "The DHT20 sensor measures the TEMPERATURE (how warm) and the HUMIDITY "
                                 "(how much water is in the air)."),
         ("Celsius", "Room temperature is about 20 C. Water freezes at 0 C and boils at 100 C."),
         ("Your breath is warm and wet", "Breathe on the sensor and both numbers go up!")],
        WeatherTry,
        [lambda s: Quiz(s, "Read the temperature", "The 7-segment display shows the temperature.",
                        temperature_quiz, rounds=1, needs=("weather",), hide_mirror=True),
         lambda s: Until(s, "Breathe on it", "Breathe gently on the sensor until the humidity goes up by 5 "
                         "(or the temperature by 1).", warmed_up, remember_weather, live_weather,
                         needs=("weather",)),
         lambda s: Quiz(s, "Humid or dry?", "Read the humidity on the kit.", humidity_quiz, rounds=1,
                        needs=("weather",), hide_mirror=True)],
        parts="the temperature sensor (DHT20)"),
    "weather": Lesson(
        "weather_station.py", 7, "Weather Station", "Day 7 - Weather Station",
        [("All together", "A weather station reads several sensors and reports them all: temperature, "
                          "humidity and light."),
         ("Readings take turns", "The LCD only has two lines, so the station shows one reading at a time, "
                                 "every 3 seconds."),
         ("Day or night?", "The light sensor tells the station if it's day or night - under 10 lux shows "
                           "the moon."),
         ("Comfort zone", "Between 18 C and 26 C most people feel just right. The station says TOO COLD or "
                          "TOO HOT outside that."),
         ("Real weather stations", "Real ones also measure wind and rain, and send their readings over the "
                                   "internet.")],
        WeatherStationTry,
        [lambda s: Entry(s, "Name your station", "Every weather station has a name. Type one and click Send.",
                         "Station name:", station_name, needs=("lcd",)),
         lambda s: Quiz(s, "Read the weather", "Look at the 7-segment display ON THE KIT.", weather_reading_quiz,
                        rounds=2, needs=("weather", "seg"), hide_mirror=True),
         lambda s: Quiz(s, "Comfort check", "Is the room comfortable?", comfort_quiz, rounds=1,
                        needs=("weather",)),
         lambda s: Until(s, "Make it night", "Cover the light sensor until the station says it's night "
                         "(the moon).", lambda k, a: light_now(k) < 10, remember_light, live_light, hold=1,
                         needs=("light",)),
         lambda s: Until(s, "Change the weather", "Breathe gently on the temperature sensor until the humidity "
                         "goes up by 5.", warmed_up, remember_weather, live_weather, needs=("weather",))],
        parts="the temperature sensor, light sensor, LCD, 7-segment, LED matrix"),
    "distance": Lesson(
        "distance.py", 8, "Distance Sensor", "Day 8 - Distance Sensor",
        [("It uses sound", "The ultrasonic sensor sends out a sound too high for us to hear and listens "
                           "for the echo."),
         ("Echo time = distance", "The longer the echo takes to come back, the further away the thing is."),
         ("Like a bat!", "Bats and dolphins find things the same way.")],
        DistanceTry,
        [lambda s: Until(s, "Really close", "Hold your hand closer than 10 cm.",
                         lambda k, a: distance_now(k) < 10, live=live_distance, hold=0.5,
                         needs=("distance",)),
         lambda s: Until(s, "Just right", "Hold your hand between 20 and 30 cm away for 2 seconds.",
                         lambda k, a: 20 <= distance_now(k) <= 30, live=live_distance, hold=2,
                         needs=("distance",)),
         lambda s: Until(s, "Far away", "Move your hand away - more than 50 cm.",
                         lambda k, a: distance_now(k) > 50, live=live_distance, hold=0.5,
                         needs=("distance",))],
        parts="the distance sensor"),
    "lcd": Lesson(
        "lcd.py", 9, "LCD Screen", "Day 9 - LCD Screen",
        [("Words on a screen", "The LCD shows letters and numbers: 2 lines of 16 letters each."),
         ("The program sends letters", "Your program sends the letters one at a time over two little "
                                       "wires (called I2C)."),
         ("Light behind it", "The backlight can be turned ON and OFF.")],
        LcdTry,
        [lambda s: Entry(s, "Hi there!", "Put your name on the LCD.", "Your name:", lcd_name,
                         needs=("lcd",)),
         lambda s: Quiz(s, "Secret word", "Look at the LCD ON THE KIT and pick the secret word.",
                        lcd_word_quiz, rounds=2, needs=("lcd",), hide_mirror=True),
         lambda s: Entry(s, "Two lines", "Write two lines at once: put a | between them, like "
                         "HELLO|WORLD", "Line 1 | Line 2:", lcd_two_lines, needs=("lcd",))],
        parts="the LCD screen"),
    "rfid": Lesson(
        "rfid.py", 10, "RFID Cards", "Day 10 - RFID Cards",
        [("A chip in a card", "The white card and the blue tag have a tiny chip and an aerial inside - "
                              "no battery!"),
         ("Every card has a number", "Each card has its own number. The reader can also save words "
                                     "on the card and read them back."),
         ("Where you see it", "Bus passes, door keys and library cards work like this.")],
        RfidTry,
        [lambda s: Count(s, "Tap a card", "Hold a card on the RFID reader.", "card", 1,
                         react=lambda k, c: (k.beep(0.05), k.say("Card:", str(c))), needs=("cards",)),
         CardWrite,
         CardRead],
        parts="the RFID reader + the white card or blue key tag"),
    "matrix": Lesson(
        "matrix_demo.py", 11, "LED Matrix", "Day 11 - LED Matrix",
        [("64 little lights", "The LED matrix has 8 rows and 8 columns of red lights = 64 lights."),
         ("Pictures from dots", "Turn on the right dots and you get a picture: a smile, a heart, letters."),
         ("Moving words", "Move the picture one dot at a time and words scroll across.")],
        MatrixTry,
        [lambda s: Quiz(s, "Which picture?", "Look at the LED matrix ON THE KIT.", picture_quiz, rounds=2,
                        needs=("matrix",), hide_mirror=True),
         lambda s: Draw(s, "Draw something", "Click the dots below to draw anything with at least 10 "
                        "lights. Watch it appear on the kit!", lambda rows: sum(r.count("X") for r in rows)
                        >= 10),
         lambda s: Draw(s, "Four corners", "Light ONLY the 4 corner dots.", four_corners)],
        parts="the LED matrix"),
    "segment": Lesson(
        "segment.py", 12, "7-Segment Display", "Day 12 - 7-Segment Display",
        [("Numbers from bars", "Each number is made of 7 little bars (segments). Turning on different "
                               "bars makes 0 to 9."),
         ("4 places", "This display has 4 places, so it can show 0 to 9999 - or a time like 12:30."),
         ("Everywhere!", "Microwaves, alarm clocks and scoreboards use them.")],
        SegmentTry,
        [lambda s: Quiz(s, "Read the number", "Look at the 7-segment display ON THE KIT.", number_quiz,
                        rounds=2, needs=("seg",), hide_mirror=True),
         lambda s: Quiz(s, "Up or down?", "Watch the display count.", up_down_quiz, rounds=2,
                        needs=("seg",), hide_mirror=True),
         lambda s: Entry(s, "Show 2026", "Make the display show 2026.", "What should it show?", show_2026,
                         needs=("seg",))],
        parts="the 7-segment display"),
    "touch": Lesson(
        "touch.py", 13, "Touch Sensor", "Day 13 - Touch Sensor",
        [("It feels your finger", "The touch pad notices your finger because your body holds a tiny bit "
                                  "of electricity."),
         ("No moving parts", "Unlike a button, nothing moves - just touch it."),
         ("In your pocket", "Phone screens are touch sensors too!")],
        TouchTry,
        [lambda s: Count(s, "Three taps", "Tap the touch pad 3 times.", "touch", 3, needs=("touch",)),
         lambda s: Until(s, "Hold it", "Keep your finger on the pad for 2 seconds.",
                         lambda k, a: k.state["touch"], hold=2, needs=("touch",)),
         lambda s: Until(s, "Speed tapper", "Tap 5 times in 5 seconds!", five_fast_taps, live=live_taps,
                         needs=("touch",))],
        parts="the touch pad"),
    "button_matrix": Lesson(
        "button_matrix.py", 14, "Button Matrix", "Day 14 - Button Matrix",
        [("16 buttons, 8 wires", "The 16 buttons are wired in 4 rows and 4 columns, so only 8 wires "
                                 "are needed."),
         ("Scanning", "The program checks one column at a time, very fast, to find the pressed button."),
         ("BUTTON MODE", "The buttons only work when the DIP switches are set to BUTTON MODE.")],
        GridTry,
        [lambda s: InOrder(s, "First and last", "Press button 1 (top left), then button 16 (bottom right).",
                           "grid", [1, 16]),
         lambda s: Count(s, "Four corners", "Press the 4 corner buttons: 1, 4, 13 and 16.", "grid", 4,
                         distinct=True, accept=lambda b: b in (1, 4, 13, 16), react=grid_react),
         lambda s: Targets(s, "Hit the lit button", "Press the button that lights up on the matrix. 5 to go!",
                           "grid", lambda: random.randint(1, 16), show_grid_target, 5)],
        switches=True, mode="grid", parts="the 16-button grid"),
    "piano": Lesson(
        "piano.py", 15, "Button Piano", "Day 15 - Button Piano",
        [("Sound is shaking air", "The buzzer shakes very fast. Faster shaking = a higher note."),
         ("Hertz", "We count the shakes per second in Hz. The note A is 440 Hz - 440 shakes a second!"),
         ("16 notes", "Each button plays a different note: 1 is the lowest, 16 the highest.")],
        PianoTry,
        [lambda s: InOrder(s, "Play the scale", "Play buttons 1, 2, 3, 4, 5, 6, 7, 8 in order.", "grid",
                           range(1, 9)),
         lambda s: InOrder(s, "Mary had a little lamb", "Play 3 2 1 2 3 3 3.", "grid", [3, 2, 1, 2, 3, 3, 3]),
         lambda s: Quiz(s, "Higher or lower?", "Listen to two notes.", higher_note_quiz, rounds=2,
                        needs=("buzzer",))],
        switches=True, mode="grid", parts="the 16-button grid, buzzer", hook=piano_hook, setup=piano_setup),
    "ir": Lesson(
        "IR.py", 16, "IR Remote", "Day 16 - IR Remote",
        [("Invisible light", "The remote flashes infrared light - too red for our eyes to see."),
         ("A secret code", "Each key flashes a different code. The receiver reads the code and the "
                           "program knows which key it was."),
         ("Point it!", "Point the remote at the receiver. It's like a TV remote.")],
        RemoteTry,
        [lambda s: Count(s, "Three different keys", "Press 3 different keys on the remote.", "ir", 3,
                         distinct=True, react=ir_react, needs=("remote",)),
         lambda s: Targets(s, "Press the number", "Press the number the LCD shows. 3 to go!", "ir",
                           lambda: random.choice("12345"), show_key_target, 3, needs=("remote",)),
         lambda s: InOrder(s, "Secret code", "Type the secret code 1 2 3 4 on the remote.", "ir",
                           ["1", "2", "3", "4"], needs=("remote",))],
        parts="the IR receiver (in the IR socket) + the remote"),
    "camera": Lesson(
        "camera.py", 17, "Camera", "Day 17 - Camera",
        [("Light into numbers", "A camera has millions of tiny light sensors. Each one measures one dot "
                                "(pixel) of the picture."),
         ("Red, green, blue", "Every pixel is saved as three numbers: how red, how green, how blue."),
         ("Your yearbook photo", "Take as many as you like, then pick ONE for the class yearbook - it's "
                                       "saved with your name and the others are deleted.")],
        CameraTry,
        [lambda s: Photo(s, "Selfie!", "Look at the camera and take a selfie."),
         lambda s: Photo(s, "Favourite thing", "Hold up your favourite thing and take a photo of it.")],
        parts="a camera (USB or ribbon)"),
    "reaction": Lesson(
        "reaction_game.py", 18, "Reaction Game", "Day 18 - Reaction Game",
        [("Measuring time", "The program notes the time of the BEEP and the time of your touch. The "
                            "difference is your reaction time."),
         ("Milliseconds", "1 second = 1000 milliseconds (ms). Most people react in 200 to 400 ms."),
         ("No cheating!", "Touch before the beep and it says TOO EARLY.")],
        ReactionGame,
        [lambda s: Reaction(s, 500), lambda s: Reaction(s, 350)],
        tryit_name="Play", parts="the touch pad, buzzer, LED matrix"),
    "parking": Lesson(
        "parking_sensor.py", 19, "Parking Sensor", "Day 19 - Parking Sensor",
        [("Like a real car", "Cars have distance sensors in the bumper that beep when you get close to a "
                             "wall."),
         ("Closer = faster beeps", "The program waits less between beeps when the distance is smaller."),
         ("STOP!", "Under 5 cm the matrix shows an X and the buzzer beeps non-stop.")],
        ParkingTry,
        [lambda s: Until(s, "Park it", "Park your hand 5 to 10 cm from the sensor and stay there for "
                         "2 seconds.", lambda k, a: 5 <= distance_now(k) <= 10, live=live_distance, hold=2,
                         needs=("distance",)),
         lambda s: Until(s, "Too close!", "Go closer than 5 cm to see the STOP sign.",
                         lambda k, a: distance_now(k) < 5, live=live_distance, hold=0.5,
                         needs=("distance",)),
         lambda s: Until(s, "Drive away", "Back out - more than 50 cm, until it goes quiet.",
                         lambda k, a: distance_now(k) > 50, live=live_distance, hold=1,
                         needs=("distance",))],
        parts="the distance sensor, buzzer, LED matrix", hook=parking_hook),
    "security": Lesson(
        "security_alarm.py", 20, "Security Alarm", "Day 20 - Security Alarm",
        [("Three parts together", "The RFID reader is the KEY, the distance sensor is the TRIPWIRE and the "
                                  "buzzer is the ALARM."),
         ("States", "The alarm is always in one state: waiting for a key, arming, watching, ringing, or off."),
         ("Only your card", "The first card you tap becomes the key. Other cards are WRONG CARD.")],
        AlarmTry,
        [lambda s: Until(s, "Save your key card", "Tap a card on the RFID reader. It becomes the KEY.",
                         lambda k, a: alarm_of(a.show).key is not None, live=alarm_live,
                         needs=("cards",)),
         lambda s: Until(s, "Set it off!", "Wait for the countdown, then put your hand in front of the "
                         "distance sensor (closer than 30 cm).", alarm_rang, live=alarm_live,
                         needs=("cards", "distance")),
         lambda s: Until(s, "Stop the alarm", "Tap your KEY card to turn the alarm off.",
                         lambda k, a: alarm_of(a.show).state == "off", live=alarm_live, needs=("cards",))],
        parts="the distance sensor, buzzer, RFID reader + a card", hook=security_hook),
    "simon": Lesson(
        "simon_says.py", 21, "Simon Says", "Day 21 - Simon Says",
        [("A memory game", "The kit shows a pattern. You copy it. Each round the pattern gets one longer."),
         ("Lists", "The program keeps the pattern in a LIST and adds a new button to the end each round."),
         ("BUTTON MODE", "The button grid needs the DIP switches set to BUTTON MODE.")],
        SimonGame,
        [lambda s: Simon(s, 3), lambda s: Simon(s, 5)],
        switches=True, mode="grid", tryit_name="Play", parts="the 16-button grid, LED matrix, buzzer"),
    "matrix_lights": Lesson(
        "matrix_lights.py", 0, "Matrix Lights", "Bonus - Matrix Lights",
        [("Buttons control lights", "Each of the 16 buttons controls one square of the LED matrix."),
         ("Toggle", "Press once = ON, press again = OFF. That's called a TOGGLE."),
         ("Make patterns", "Try a checkerboard, a frame, or the first letter of your name.")],
        LightsTry,
        [lambda s: Until(s, "All on", "Turn on all 16 squares.", lambda k, a: len(lights_of(a.show)) == 16,
                         live=lambda k, a: "%d of 16 on" % len(lights_of(a.show))),
         lambda s: Until(s, "Checkerboard", "Make a checkerboard: every other square on.",
                         lambda k, a: lights_of(a.show) in CHECKER,
                         live=lambda k, a: "%d of 16 on" % len(lights_of(a.show))),
         lambda s: Until(s, "Lights out", "Turn every square off.", lambda k, a: not lights_of(a.show),
                         live=lambda k, a: "%d of 16 on" % len(lights_of(a.show)))],
        switches=True, mode="grid", parts="the 16-button grid, LED matrix", hook=lights_hook),
}


# ==================================================================
#  THE CODE TAB: the lesson's real program. Every line is locked -
#  only the yellow values can be changed. Then "Upload to kit".
# ==================================================================
CODE_FILE = os.path.join(os.path.expanduser("~"), ".crowpi_code.json")
TEAL = "#138A8A"
PICTURE_NAMES = ["SMILE", "HEART", "STAR", "CHECK", "ARROW_UP", "ARROW_DOWN", "FULL"]
REMOTE_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "CH-", "CH", "CH+", "VOL-", "VOL+",
               "PLAY/PAUSE"]


class Field:
    """One value the student may change.
    kind: "int", "float", "text", "choice" (words in quotes), "name" (a picture name), "bool"."""
    def __init__(self, kind, default, low=None, high=None, choices=None, help_text=""):
        self.kind, self.default, self.low, self.high = kind, default, low, high
        self.choices = choices or []
        self.help = help_text

    def check(self, raw):
        """(True, value) or (False, what's wrong)."""
        raw = str(raw).strip()
        if self.kind in ("int", "float"):
            try:
                value = int(raw) if self.kind == "int" else float(raw)
            except ValueError:
                return False, ("a whole number" if self.kind == "int" else "a number") + \
                    " from %s to %s" % (self.low, self.high)
            if not self.low <= value <= self.high:
                return False, "a number from %s to %s" % (self.low, self.high)
            return True, value
        if self.kind == "text":
            if len(raw) > (self.high or 16):
                return False, "at most %d letters" % (self.high or 16)
            return True, raw
        if self.kind == "bool":
            return (True, raw == "True") if raw in ("True", "False") else (False, "True or False")
        return (True, raw) if raw in self.choices else (False, "one of the choices")

    def code(self, value):
        """How the value is written in the program."""
        if self.kind in ("name", "bool"):
            return str(value)
        return repr(value)


class Program:
    def __init__(self, source, fields, mission="", done=None):
        self.source = source.strip("\n")
        self.fields = fields
        self.mission = mission
        self.done = done                  # done(values, kit) -> True when the mission is complete


class ProgramStopped(Exception):
    pass


def load_code_file():
    try:
        with open(CODE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_code_file(data):
    try:
        with open(CODE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


TOKENS = re.compile(r"(?P<comment>#.*$)|(?P<string>\"[^\"]*\")|"
                    r"(?P<keyword>\b(?:while|if|elif|else|for|in|not|and|or|True|False|None)\b)|"
                    r"(?P<number>\b\d+(?:\.\d+)?\b)|(?P<function>\b\w+(?=\())")
CODE_COLORS = {"comment": "#7F8CA3", "string": "#9CDB7A", "keyword": "#FF8FD1", "number": "#FFB86B",
               "function": "#7FC8FF", "line": "#55607A"}


class CodeScreen(Screen):
    """The program for this lesson - change the yellow values, then upload it to the kit."""
    def build(self):
        app = self.app
        lesson = app.lesson
        lesson_mode(app)
        self.program: Program = lesson.code
        self.saved = load_code_file()
        mine = self.saved.get(lesson.key, {})
        self.vars: dict = {}
        self.widgets: dict = {}
        self.running = None
        self.stopping = threading.Event()
        self.inbox: deque = deque(maxlen=100)
        self.output: deque = deque(maxlen=200)
        self.values: dict = {}
        self.mission_done = lesson.key in self.saved.get("missions", [])
        # --- right: the kit and the program's output ---
        right = tk.Frame(self.frame, bg=BG)
        right.pack(side="right", fill="y", padx=(10, 0))
        self.mirror = KitMirror(app, right, small=True)
        self.mirror.frame.pack(anchor="n", fill="x")
        app.label(right, "PROGRAM OUTPUT", 9, YELLOW, True).pack(anchor="w", pady=(8, 2))
        self.console = tk.Text(right, height=8, width=34, bg="#0E121A", fg="#9CDB7A",
                               font=("DejaVu Sans Mono", max(8, int(10 * app.scale))), relief="flat",
                               state="disabled", highlightthickness=0)
        self.console.pack(fill="both", expand=True)
        self.status = app.label(right, "Not running", 11, GREY, True)
        self.status.pack(anchor="w", pady=4)
        # --- left: the code ---
        left = tk.Frame(self.frame, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        top = tk.Frame(left, bg=BG)
        top.pack(fill="x")
        self.mission = app.label(top, "", 12, YELLOW, True, wraplength=int(600 * app.scale), justify="left")
        self.mission.pack(anchor="w")
        self.show_mission()
        app.label(left, "Grey code is locked. Change the YELLOW values, then click Upload to kit.", 10,
                  TEXT).pack(anchor="w", pady=(2, 4))
        size = max(9, int(12 * app.scale))
        source_lines = self.program.source.splitlines()
        extra = sum(1 for line in source_lines if "«" in line) * 0.45      # (the yellow boxes are taller)
        self.text = tk.Text(left, bg="#141A26", fg="#D7DEEA", font=("DejaVu Sans Mono", size),
                            relief="flat", padx=10, pady=8, wrap="none", cursor="arrow", width=46,
                            height=int(len(source_lines) + extra + 0.99), highlightthickness=0)
        self.text.pack(fill="x")
        for tag, color in CODE_COLORS.items():
            self.text.tag_config(tag, foreground=color)
        lines = self.program.source.splitlines()
        for number, line in enumerate(lines, 1):
            self.text.insert("end", "%2d  " % number, "line")
            for i, piece in enumerate(re.split(r"«(\w+)»", line)):
                if i % 2:
                    self.add_field(piece, mine.get(piece))
                else:
                    self.add_code(piece)
            if number < len(lines):
                self.text.insert("end", "\n")
        self.text.config(state="disabled")         # locked: only the yellow boxes can change
        buttons = tk.Frame(left, bg=BG)
        buttons.pack(anchor="w", pady=6)
        app.button(buttons, "Upload to kit", self.upload, GREEN, 14, padx=16, pady=6).pack(side="left")
        app.button(buttons, "Stop", self.stop, RED, 12, padx=14, pady=6).pack(side="left", padx=6)
        app.button(buttons, "Reset values", self.reset, PANEL_2, 11, padx=12, pady=6).pack(side="left")
        self.message = app.label(left, "", 12, YELLOW, True, wraplength=int(600 * app.scale), justify="left")
        self.message.pack(anchor="w")
        self.check_all()

    # ---------- building the code view ----------
    def add_code(self, text):
        at = 0
        for m in TOKENS.finditer(text):
            if m.start() > at:
                self.text.insert("end", text[at:m.start()])
            self.text.insert("end", m.group(0), m.lastgroup or "")
            at = m.end()
        self.text.insert("end", text[at:])

    def add_field(self, name, saved):
        field = self.program.fields[name]
        ok, value = field.check(saved) if saved is not None else (False, None)
        start = value if ok else field.default
        var = self.vars.get(name) or tk.StringVar(value=str(start))     # (a value can be used twice)
        font = ("DejaVu Sans Mono", max(9, int(12 * self.app.scale)), "bold")
        if field.kind in ("int", "float", "text"):
            width = max(4, len(str(start)) + 2) if field.kind != "text" else (field.high or 16) + 1
            widget = tk.Entry(self.text, textvariable=var, width=width, font=font, bg="#FFE36E", fg="#1B2230",
                              relief="flat", insertbackground="#1B2230", justify="center")
        else:
            choices = ["True", "False"] if field.kind == "bool" else field.choices
            widget = tk.OptionMenu(self.text, var, *choices)
            widget.config(font=font, bg="#FFE36E", fg="#1B2230", activebackground="#FFF0A8",
                          relief="flat", highlightthickness=0, bd=0, indicatoron=False, padx=6, pady=0)
            widget["menu"].config(font=font)
        quoted = field.kind in ("text", "choice")        # words are written in "quotes"
        if quoted:
            self.text.insert("end", '"', "string")
        self.text.window_create("end", window=widget, padx=1)
        if quoted:
            self.text.insert("end", '"', "string")
        if name not in self.vars:
            var.trace_add("write", lambda *_: self.check_all())
        self.vars[name] = var
        self.widgets.setdefault(name, []).append(widget)

    # ---------- values ----------
    def check_all(self):
        """Colour each box: yellow = OK, red = not allowed. Returns the values if all are OK."""
        values, problems = {}, []
        for name, var in self.vars.items():
            ok, result = self.program.fields[name].check(var.get())
            for widget in self.widgets[name]:
                widget.config(bg="#FFE36E" if ok else "#FF8A80")
            if ok:
                values[name] = result
            else:
                problems.append("%s must be %s" % (name, result))
        if problems:
            self.message.config(text="Fix the red box: " + problems[0], fg="#FF8A80")
            return None
        if self.message.cget("fg") == "#FF8A80":
            self.message.config(text="", fg=YELLOW)
        return values

    def reset(self):
        for name, var in self.vars.items():
            var.set(str(self.program.fields[name].default))
        self.message.config(text="Values reset to the start.", fg=YELLOW)

    def show_mission(self):
        if not self.program.mission:
            return
        if self.mission_done:
            self.mission.config(text="MISSION COMPLETE!  " + self.program.mission, fg=GREEN_2)
        else:
            self.mission.config(text="MISSION: " + self.program.mission, fg=YELLOW)

    # ---------- running the program ----------
    def upload(self):
        values = self.check_all()
        if values is None:
            return
        self.stop()
        if self.running is not None:
            self.running.join(timeout=1.5)
        source = self.program.source
        for name, value in values.items():
            source = source.replace("«%s»" % name, self.program.fields[name].code(value))
        key = self.app.lesson.key
        self.saved[key] = values
        save_code_file(self.saved)
        self.values = values
        self.inbox.clear()
        self.stopping.clear()
        self.write("--- uploaded ---")
        print("[code] upload", values)
        self.message.config(text="Uploaded! Look at the kit.", fg=GREEN_2)
        self.running = threading.Thread(target=self.execute, args=(source,), daemon=True)
        self.running.start()

    def execute(self, source):
        try:
            exec(compile(source, "lesson program", "exec"), self.namespace())     # (only our own code + checked values)
            self.output.append("--- program finished ---")
        except ProgramStopped:
            self.output.append("--- stopped ---")
        except Exception as problem:
            self.output.append("Oops: %s" % problem)
        finally:
            self.kit.quiet()

    def stop(self):
        if self.running is not None and self.running.is_alive():
            self.stopping.set()

    def write(self, line):
        self.console.config(state="normal")
        self.console.insert("end", line + "\n")
        self.console.see("end")
        self.console.config(state="disabled")

    def tick(self, events):
        for event in events:
            self.inbox.append(event)
        while self.output:
            line = self.output.popleft()
            self.write(line)
            print("[code]", line)
        alive = self.running is not None and self.running.is_alive()
        self.status.config(text="RUNNING" if alive else "Not running", fg=GREEN_2 if alive else GREY)
        done = self.program.done
        if self.values and done and not self.mission_done:          # (checked from the first upload on)
            try:
                complete = done(self.values, self.kit)
            except (TypeError, KeyError):
                complete = False
            if complete:
                self.mission_done = True
                self.saved.setdefault("missions", []).append(self.app.lesson.key)
                save_code_file(self.saved)
                self.show_mission()
                self.message.config(text="MISSION COMPLETE! Nice hacking.", fg=GREEN_2)
                print("[code] mission complete")

    def leave(self):
        self.stop()
        if self.running is not None:
            self.running.join(timeout=1.0)
        self.kit.quiet()
        self.kit.relay_on(False)

    # ---------- what the program can use ----------
    def namespace(self):
        kit = self.kit
        stopping = self.stopping
        inbox = self.inbox

        def check():
            if stopping.is_set():
                raise ProgramStopped()

        def take(kind):
            check()
            for event in list(inbox):
                if event[0] == kind:
                    try:
                        inbox.remove(event)
                    except ValueError:
                        pass
                    return event[1]
            return None

        def wait(seconds):
            end = time.time() + max(0.0, float(seconds))
            while True:
                check()
                left = end - time.time()
                if left <= 0:
                    return
                time.sleep(min(0.05, left))

        def beep(seconds=0.1):
            check()
            kit.beep(seconds)
            wait(seconds + 0.05)

        def say(line1, line2=""):
            check()
            kit.say(str(line1), str(line2))

        def show(value):
            check()
            if isinstance(value, (int, float)):
                kit.show_number(value)
            else:
                kit.show_text(str(value)[:4])

        def picture(rows):
            check()
            kit.picture(rows)

        def clear():
            check()
            kit.clear_matrix()

        def scroll(text):
            check()
            kit.scroll(str(text).upper())
            wait(0.5 * (len(str(text)) + 2))

        def relay(on):
            check()
            kit.relay_on(bool(on))


        def tone(hertz):
            check()
            kit.tone(max(50, min(4000, hertz)))

        def quiet():
            kit.quiet()

        def reading(name, digits=0, missing=0):
            value = kit.state[name]
            return missing if value is None else (round(value) if digits == 0 else round(value, digits))

        def light():
            check()
            return reading("light")

        def temperature():
            check()
            return reading("temperature", 1)

        def humidity():
            check()
            return reading("humidity")

        def distance():
            check()
            return reading("distance", 0, 999)

        def touched():
            check()
            return bool(kit.state["touch"])

        def tapped():
            return take("touch") is not None

        def arrow_pressed(name):
            check()
            return name in kit.state["arrows"]

        def held_button():
            check()
            return kit.state["grid"] if kit.mode == "grid" else None

        def next_button():
            return take("grid")

        def wait_for_button():
            while True:
                button = next_button()
                if button:
                    return button
                wait(0.02)

        def wait_for_touch():
            while not tapped():
                wait(0.01)

        def remote_key():
            return take("ir")

        def card():
            return take("card")

        def light_square(button):
            check()
            kit.squares([button])

        def light_squares(buttons):
            check()
            kit.squares(list(buttons))

        def take_photo():
            check()
            if not kit.camera or kit.camera == "looking":
                print("No camera found - plug one in and open the lesson again.")
                return
            result: list = []
            kit.take_photo(result.append)
            while not result:
                wait(0.1)
            print("Photo saved:", os.path.basename(result[0]) if result[0] else "didn't work")

        def random_between(low, high):
            return random.uniform(min(low, high), max(low, high))

        def random_buttons(count, which):
            pool = [1, 4, 13, 16] if which == "corners" else list(range(1, 17))
            buttons = [random.choice(pool) for _ in range(count)]
            if TESTING:
                print("TEST pattern", " ".join(str(b) for b in buttons))
            return buttons

        def clock():
            return time.time()

        def stop():
            raise ProgramStopped()

        def say_print(*things):
            self.output.append(" ".join(str(t) for t in things))

        safe = {"range": range, "len": len, "str": str, "int": int, "round": round, "abs": abs,
                "min": min, "max": max, "list": list, "True": True, "False": False, "None": None}
        names = {"__builtins__": safe, "print": say_print}
        for f in (wait, beep, say, show, picture, clear, scroll, relay, tone, quiet, light, temperature,
                  humidity, distance, touched, tapped, arrow_pressed, held_button, next_button,
                  wait_for_button, wait_for_touch, remote_key, card, light_square, light_squares, take_photo,
                  random_between, random_buttons, clock, stop):
            names[f.__name__] = f
        names.update({"SMILE": SMILE, "HEART": HEART, "STAR": STAR, "CHECK": CHECK, "FULL": FULL,
                      "STOP": STOP_SIGN, "ARROW_UP": ARROWS["UP"], "ARROW_DOWN": ARROWS["DOWN"],
                      "ARROW_LEFT": ARROWS["LEFT"], "ARROW_RIGHT": ARROWS["RIGHT"]})
        return names


def I(default, low, high, help_text=""):
    return Field("int", default, low, high, help_text=help_text)


def F(default, low, high, help_text=""):
    return Field("float", default, low, high, help_text=help_text)


def T(default, most=16):
    return Field("text", default, high=most)


def PIC(default):
    return Field("name", default, choices=PICTURE_NAMES)


def KEY(default):
    return Field("choice", default, choices=REMOTE_KEYS)


CODE = {
    "hello": Program('''
# Day 1: Hello, CrowPi!
name = «name»
say("Hello,", name)
show("HI")
picture(«picture»)
for i in range(«beeps»):      # how many happy beeps
    beep(0.1)
scroll("HI " + name)
''', {"name": T("Coder"), "picture": PIC("SMILE"), "beeps": I(2, 0, 10)},
        "Make the kit beep exactly 5 times and show a HEART.",
        lambda v, k: v["beeps"] == 5 and v["picture"] == "HEART"),

    "buzzer": Program('''
# Day 2: The Buzzer
length = «length»     # seconds the buzzer stays ON
gap = «gap»           # seconds of quiet after each beep
times = «times»
for i in range(times):
    beep(length)
    show(i + 1)
    wait(gap)
say("Done!", str(times) + " beeps")
''', {"length": F(0.2, 0.05, 2), "gap": F(0.3, 0.05, 2), "times": I(3, 1, 20)},
        "Make 3 LONG beeps: each one at least 1 second.",
        lambda v, k: v["times"] == 3 and v["length"] >= 1),

    "button_buzzer": Program('''
# Day 3: Buttons + Buzzer
while True:
    if arrow_pressed("UP"):
        beep(«up_beep»)
        picture(ARROW_UP)
    if arrow_pressed("DOWN"):
        beep(«down_beep»)
        picture(ARROW_DOWN)
    if arrow_pressed("LEFT"):
        say(«left_words»)
    if arrow_pressed("RIGHT"):
        say(«right_words»)
    wait(0.1)
''', {"up_beep": F(0.05, 0.02, 2), "down_beep": F(0.3, 0.02, 2), "left_words": T("Left!"),
      "right_words": T("Right!")},
        "Make LEFT say your name, then press LEFT on the kit.",
        lambda v, k: v["left_words"] not in ("", "Left!") and "LEFT" in k.state["arrows"]),

    "relay": Program('''
# Day 4: The Relay
on_time = «on_time»       # seconds ON
off_time = «off_time»     # seconds OFF
for i in range(«clicks»):
    relay(True)
    show(i + 1)
    wait(on_time)
    relay(False)
    wait(off_time)
say("Relay done")
''', {"on_time": F(0.5, 0.1, 5), "off_time": F(0.5, 0.1, 5), "clicks": I(5, 1, 30)},
        "Make a heartbeat: 10 clicks, faster than 0.2 seconds OFF.",
        lambda v, k: v["clicks"] == 10 and v["off_time"] < 0.2),

    "light": Program('''
# Day 5: Night light
dark = «dark»          # below this many lux = night
while True:
    lux = light()
    show(lux)
    if lux < dark:
        picture(«night_picture»)
        say("Night time!", str(lux) + " lux")
    else:
        clear()
        say("Day time", str(lux) + " lux")
    wait(0.5)
''', {"dark": I(20, 1, 2000), "night_picture": PIC("STAR")},
        "Change the night picture to FULL and make it say Night time when you cover the sensor.",
        lambda v, k: v["night_picture"] == "FULL" and (k.state["light"] or 9999) < v["dark"]),

    "dht20": Program('''
# Day 6: Comfort meter
too_hot = «too_hot»      # degrees C
too_cold = «too_cold»
while True:
    t = temperature()
    show(t)
    if t > too_hot:
        say("TOO HOT!", str(t) + " C")
    elif t < too_cold:
        say("TOO COLD!", str(t) + " C")
    else:
        say("Just right", str(t) + " C")
    wait(1)
''', {"too_hot": I(26, -10, 60), "too_cold": I(18, -10, 60)},
        "Make it say TOO HOT in this room (set too_hot lower than the temperature).",
        lambda v, k: (k.state["temperature"] or -99) > v["too_hot"]),

    "weather": Program('''
# Day 7: Weather station
name = «name»
while True:
    say(name, str(temperature()) + " C")
    show(temperature())
    wait(«seconds»)
    say(name, "Humidity " + str(humidity()) + "%")
    show(humidity())
    wait(«seconds»)
    say(name, "Light " + str(light()) + " lux")
    if light() < «dark»:
        picture(«night_picture»)
    else:
        picture(«day_picture»)
    wait(«seconds»)
''', {"name": T("My Station"), "seconds": F(3, 0.5, 10), "dark": I(10, 1, 1000),
      "night_picture": PIC("STAR"), "day_picture": PIC("SMILE")},
        "Give your station its own name, make the night picture a HEART - then cover the light sensor.",
        lambda v, k: v["name"] != "My Station" and v["night_picture"] == "HEART" and
        (k.state["light"] or 9999) < v["dark"]),

    "distance": Program('''
# Day 8: Too close!
close = «close»        # centimetres
while True:
    cm = distance()
    show(cm)
    if cm < close:
        picture(«picture»)
        beep(0.05)
    else:
        clear()
    wait(0.2)
''', {"close": I(10, 2, 150), "picture": PIC("STAR")},
        "Make it warn you at 30 cm with the FULL picture, then test it with your hand.",
        lambda v, k: v["close"] == 30 and v["picture"] == "FULL" and (k.state["distance"] or 999) < 30),

    "lcd": Program('''
# Day 9: LCD messages
say(«line1», «line2»)
wait(«seconds»)
say(«line3», «line4»)
wait(«seconds»)
say(«line1», «line2»)
''', {"line1": T("Hello"), "line2": T("CrowPi Club!"), "line3": T("Coding is"), "line4": T("awesome"),
      "seconds": F(2, 0.5, 10)},
        "Write a 2-screen joke: question on screen 1, answer on screen 2.",
        lambda v, k: v["line1"] != "Hello" and v["line3"] != "Coding is"),

    "rfid": Program('''
# Day 10: Card reader
say(«waiting_words»)
while True:
    number = card()
    if number:
        say(«hello_words», str(number))
        picture(«picture»)
        beep(«beep»)
    wait(0.2)
''', {"waiting_words": T("Tap a card"), "hello_words": T("Welcome!"), "picture": PIC("CHECK"),
      "beep": F(0.1, 0.02, 2)},
        "Make the card show a HEART and say your own welcome, then tap it.",
        lambda v, k: v["picture"] == "HEART" and v["hello_words"] != "Welcome!" and k.state["card"]),

    "matrix": Program('''
# Day 11: Animation
speed = «speed»         # seconds per picture
for i in range(«times»):
    picture(«first»)
    wait(speed)
    picture(«second»)
    wait(speed)
scroll(«words»)
''', {"speed": F(0.5, 0.05, 3), "times": I(5, 1, 50), "first": PIC("SMILE"), "second": PIC("HEART"),
      "words": T("HI", 12)},
        "Make a fast blink: speed under 0.1 and FULL as the second picture.",
        lambda v, k: v["speed"] < 0.1 and v["second"] == "FULL"),

    "segment": Program('''
# Day 12: Counter
number = «start»
step = «step»          # added every time (try a minus number!)
for i in range(«count»):
    show(number)
    number = number + step
    wait(«speed»)
beep(0.5)
''', {"start": I(0, -999, 9999), "step": I(1, -100, 100), "count": I(10, 1, 100), "speed": F(0.5, 0.05, 3)},
        "Make a countdown from 10 to 0 (start 10, step -1, count 11).",
        lambda v, k: v["start"] == 10 and v["step"] == -1 and v["count"] == 11),

    "touch": Program('''
# Day 13: Tap counter
taps = 0
goal = «goal»
while True:
    if tapped():
        taps = taps + 1
        show(taps)
        beep(«beep»)
        if taps == goal:
            say(«win_words»)
            picture(STAR)
    wait(0.02)
''', {"goal": I(5, 1, 100), "beep": F(0.03, 0.01, 1), "win_words": T("You did it!")},
        "Set the goal to 10 and reach it.",
        lambda v, k: v["goal"] == 10 and k.seg_text == "10"),

    "button_matrix": Program('''
# Day 14: Secret button
secret = «secret»
while True:
    button = next_button()
    if button:
        show(button)
        light_square(button)
        if button == secret:
            say(«secret_words»)
            beep(0.3)
    wait(0.05)
''', {"secret": I(7, 1, 16), "secret_words": T("SECRET FOUND!")},
        "Make button 16 the secret, then find it on the kit.",
        lambda v, k: v["secret"] == 16 and k.seg_text == "16"),

    "piano": Program('''
# Day 15: Your own piano
low = «low»          # Hz for button 1
step = «step»        # how much higher each next button is
while True:
    button = held_button()
    if button:
        tone(low + (button - 1) * step)
        show(button)
    else:
        quiet()
    wait(0.02)
''', {"low": I(262, 100, 2000), "step": I(40, 5, 200)},
        "Make button 1 play the note A (440 Hz), then play it.",
        lambda v, k: v["low"] == 440 and k.state["grid"] == 1),

    "ir": Program('''
# Day 16: TV remote
while True:
    key = remote_key()
    if key == «on_key»:
        relay(True)
        say("Relay ON")
    if key == «off_key»:
        relay(False)
        say("Relay OFF")
    if key == «picture_key»:
        picture(«picture»)
    wait(0.05)
''', {"on_key": KEY("1"), "off_key": KEY("2"), "picture_key": KEY("3"), "picture": PIC("SMILE")},
        "Make CH+ turn the relay ON and CH- turn it OFF, then try it.",
        lambda v, k: v["on_key"] == "CH+" and v["off_key"] == "CH-" and k.state["ir"] in ("CH+", "CH-")),

    "camera": Program('''
# Day 17: Photo booth
for n in range(«countdown», 0, -1):
    show(n)
    beep(0.05)
    wait(1)
say(«smile_words»)
take_photo()
say("Saved!", "CrowPi_Photos")
''', {"countdown": I(3, 1, 10), "smile_words": T("SMILE!")},
        "Give yourself a 10 second countdown and take the photo.",
        lambda v, k: v["countdown"] == 10 and k.lcd_text[0] == "Saved!"),

    "reaction": Program('''
# Day 18: Reaction timer
say("Wait for it...")
clear()
wait(random_between(«shortest», «longest»))
picture(FULL)
beep(0.1)
start = clock()
wait_for_touch()
ms = round((clock() - start) * 1000)
show(ms)
if ms < «fast»:
    say("SUPER FAST!", str(ms) + " ms")
else:
    say("Too slow!", str(ms) + " ms")
''', {"shortest": F(2, 0.5, 20), "longest": F(5, 0.5, 20), "fast": I(400, 50, 3000)},
        "Make the wait between 1 and 10 seconds so nobody can guess it.",
        lambda v, k: v["shortest"] == 1 and v["longest"] == 10),

    "parking": Program('''
# Day 19: Parking sensor
stop_at = «stop_at»      # centimetres
slow_at = «slow_at»
while True:
    cm = distance()
    show(cm)
    if cm < stop_at:
        picture(STOP)
        beep(0.2)
    elif cm < slow_at:
        clear()
        beep(0.04)
        wait(cm / 100)
    else:
        clear()
    wait(0.05)
''', {"stop_at": I(5, 1, 100), "slow_at": I(50, 2, 200)},
        "Make it say STOP at 15 cm and start beeping at 80 cm.",
        lambda v, k: v["stop_at"] == 15 and v["slow_at"] == 80),

    "security": Program('''
# Day 20: Alarm
arm_time = «arm_time»       # seconds to walk away
say("Alarm ON in", str(arm_time) + " s")
wait(arm_time)
say("Watching...")
while distance() > «tripwire»:      # cm - closer than this = intruder!
    wait(0.1)
for i in range(«alarm_beeps»):
    say(«alarm_words»)
    picture(FULL)
    beep(0.2)
    clear()
    wait(0.1)
say("Alarm over")
''', {"arm_time": I(10, 1, 60), "tripwire": I(30, 5, 150), "alarm_beeps": I(10, 1, 50),
      "alarm_words": T("INTRUDER!")},
        "Give yourself 5 seconds to leave, then set the alarm off.",
        lambda v, k: v["arm_time"] == 5 and k.lcd_text[0] == "Alarm over"),

    "simon": Program('''
# Day 21: Simon says
pattern = random_buttons(«length», «which»)
for button in pattern:
    light_square(button)
    beep(0.08)
    wait(«speed»)
    clear()
    wait(0.2)
say("Your turn!")
for button in pattern:
    if wait_for_button() != button:
        say("Wrong button!")
        beep(0.6)
        stop()
say("You got it!")
picture(STAR)
''', {"length": I(4, 1, 20), "which": Field("choice", "corners", choices=["corners", "all 16"]),
      "speed": F(0.5, 0.1, 3)},
        "Beat a pattern of 6 using all 16 buttons.",
        lambda v, k: v["length"] >= 6 and v["which"] == "all 16" and k.lcd_text[0] == "You got it!"),

    "matrix_lights": Program('''
# Bonus: Matrix lights
lit = []
while True:
    button = next_button()
    if button:
        if button in lit:
            lit.remove(button)
        else:
            lit.append(button)
        light_squares(lit)
        show(len(lit))
        if len(lit) == «goal»:
            say(«win_words»)
    wait(0.05)
''', {"goal": I(16, 1, 16), "win_words": T("ALL ON!")},
        "Set the goal to 8 and make a checkerboard.",
        lambda v, k: v["goal"] == 8 and k.seg_text == "8"),
}


# ==================================================================
#  LONGER LESSONS: two more Learn cards and more challenges for each
# ==================================================================
def text_quiz(act):
    word = random.choice(["HI", "YO", "GO", "COOL", "PI", "FUN", "WIN", "YES"])
    act.kit.show_text(word)
    return "What word is on the 7-segment display?", options_with(word, ["HI", "YO", "GO", "COOL", "PI",
                                                                          "FUN", "WIN", "YES"]), word


def beep_speed_quiz(act):
    answer = random.choice(["Fast", "Slow"])
    gap = 0.08 if answer == "Fast" else 0.6
    act.replay = lambda: play_beeps(act.show, [(0.08, gap)] * 4)
    act.show.frame.after(400, act.replay)
    return "Listen! Are the beeps FAST or SLOW?", ["Fast", "Slow"], answer


def longer_beep_quiz(act):
    a, b = random.sample([0.1, 0.3, 0.7], 2)
    act.replay = lambda: play_beeps(act.show, [(a, 0.6), (b, 0)])
    act.show.frame.after(400, act.replay)
    return "Two beeps. Which one is LONGER?", ["First", "Second"], "First" if a > b else "Second"


def relay_speed_quiz(act):
    answer = random.choice(["Fast", "Slow"])
    gap = 120 if answer == "Fast" else 600

    def play():
        for i in range(8):
            act.show.frame.after(i * gap, act.kit.relay_on, i % 2 == 0)
    act.replay = play
    act.show.frame.after(400, play)
    return "Listen to the relay. Is it clicking FAST or SLOW?", ["Fast", "Slow"], answer


def relay_follows_touch(kit, _act):
    touched = bool(kit.state["touch"])
    kit.relay_on(touched)
    return touched


def light_blinks(kit, act):
    """Cover and uncover the sensor: count the dark moments."""
    lux = light_now(kit)
    dark = lux < act.memory["start"] / 3
    if dark and not act.memory.get("dark"):
        act.memory["blinks"] = act.memory.get("blinks", 0) + 1
        kit.beep(0.05)
    act.memory["dark"] = dark
    return act.memory.get("blinks", 0) >= 3


def live_blinks(kit, act):
    return "Dark moments: %d of 3   (now %.0f lux)" % (act.memory.get("blinks", 0), light_now(kit))


def remember_peak(kit, act):
    act.memory["peak"] = kit.state["humidity"]


def cooled_down(kit, act):
    h = kit.state["humidity"]
    if act.memory.get("peak") is None:
        act.memory["peak"] = h
    act.memory["peak"] = max(act.memory["peak"], h)
    return h <= act.memory["peak"] - 3


def live_cool(kit, act):
    return "Humidity %.0f %%  (highest %.0f %% - wait for it to drop by 3)" % (kit.state["humidity"],
                                                                               act.memory.get("peak") or 0)


def fahrenheit_quiz(act):
    t = act.kit.state["temperature"]
    c = 20 if t is None else int(round(t))
    f = round(c * 9 / 5 + 32)
    act.kit.show_number(c)
    act.kit.say("It is %d C" % c, "What's that in F?")
    return "The kit says %d C. What is that in Fahrenheit? (F = C x 9 / 5 + 32)" % c, \
        options_with(f, [f - 10, f + 8, c, f + 20]), f


def far_then_close(kit, act):
    d = distance_now(kit)
    if d > 40:
        act.memory["far"] = True
    return bool(act.memory.get("far")) and d < 10


def lcd_line_quiz(act):
    word = random.choice(WORDS)
    line = random.choice(["Line 1", "Line 2"])
    act.kit.say(word if line == "Line 1" else "", word if line == "Line 2" else "")
    return "Which line of the LCD shows the word?", ["Line 1", "Line 2"], line


def sixteen_letters(kit, text):
    if text.strip() == "16":
        kit.say("1234567890123456", "16 letters!")
        return True
    return "Not quite - count the boxes on one line of the LCD (try the Try it tab!)"


def show_help(kit, text):
    text = text.strip().upper()
    kit.show_text(text[:4])
    return True if text == "HELP" else "It shows %s - make it say HELP" % (text or "nothing")


def bars_quiz(act):
    number, bars = random.choice([(8, 7), (1, 2), (7, 3), (4, 4), (0, 6)])
    act.kit.show_number(number)
    return "Look at the %d on the display. How many little bars make it?" % number, \
        options_with(bars, [2, 3, 4, 5, 6, 7]), bars


def double_tap(kit, act):
    now = time.time()
    for _ in events_of(act.events, "touch"):
        last = act.memory.get("last", 0)
        act.memory["last"] = now
        kit.beep(0.03)
        if now - last < 0.6:
            return True
    return False


def top_row(rows):
    return rows[0] == "XXXXXXXX" and all(r == "........" for r in rows[1:])


def frame_picture(rows):
    return all(rows[y] == ("XXXXXXXX" if y in (0, 7) else "X......X") for y in range(8))


def wrong_card(_kit, act):
    return alarm_of(act.show).note.startswith("WRONG")


def armed_again(_kit, act):
    return alarm_of(act.show).state in ("arming", "watching")


def far_then_park(kit, act):
    d = distance_now(kit)
    if d > 40:
        act.memory["far"] = True
    return bool(act.memory.get("far")) and 10 <= d <= 20


DIAGONAL = {1, 6, 11, 16}
FRAME = {1, 2, 3, 4, 5, 8, 9, 12, 13, 14, 15, 16}


def lights_live(_kit, act):
    return "%d of 16 on" % len(lights_of(act.show))


MORE: dict = {   # lesson: ([extra Learn cards], [(position or None = at the end, challenge)])
    "hello": ([("Inputs and outputs", "You typed your name on the keyboard - an INPUT. The screens and the buzzer are "
                                      "OUTPUTS: they show or play something."),
               ("Keep it safe", "Always shut down with Menu > Shutdown before you unplug. Pulling the plug can "
                                "break the SD card.")],
              [(None, lambda s: Quiz(s, "Read the 7-segment", "Look at the 7-segment display ON THE KIT.", text_quiz,
                                     rounds=2, needs=("seg",), hide_mirror=True)),
               (None, lambda s: Quiz(s, "Which picture?", "Look at the LED matrix ON THE KIT.", picture_quiz,
                                     rounds=2, needs=("matrix",), hide_mirror=True))]),
    "buzzer": ([("Hertz", "The buzzer clicks very fast to make a sound. Clicks per second = Hz. More Hz = a "
                          "higher sound."),
                ("Real-life beeps", "Microwaves, alarm clocks, trucks reversing - all use beep patterns to tell "
                                    "you something without words.")],
               [(None, lambda s: Quiz(s, "Fast or slow?", "Listen to the beeps.", beep_speed_quiz, rounds=2,
                                      needs=("buzzer",))),
                (None, lambda s: Quiz(s, "Which is longer?", "Listen to two beeps.", longer_beep_quiz, rounds=2,
                                      needs=("buzzer",)))]),
    "button_buzzer": ([("Pressed or not", "A button is a switch. Pressed = the wire connects = the program reads "
                                          "a 1 (or True)."),
                       ("Checking all the time", "The program checks the buttons many times a second, so even a "
                                                 "quick tap is seen.")],
                      [(None, lambda s: InOrder(s, "Arrow code", "Press UP, UP, DOWN, DOWN (a wrong one starts "
                                                "again).", "arrow", ["UP", "UP", "DOWN", "DOWN"])),
                       (None, lambda s: Count(s, "Ten presses", "Press any arrows 10 times as fast as you can.",
                                              "arrow", 10, react=arrow_react))]),
    "relay": ([("Two circuits", "The relay keeps two circuits apart: the tiny Pi circuit and the big circuit it "
                                "switches. That keeps the Pi safe."),
               ("Never wall power!", "In the club we only listen to the click. Never connect wall power to the "
                                     "relay.")],
              [(None, lambda s: Quiz(s, "Fast or slow clicks?", "Listen to the relay.", relay_speed_quiz, rounds=2,
                                     needs=("relay",))),
               (None, lambda s: Until(s, "Hold it ON", "Hold your finger on the touch pad: the relay stays ON while "
                                      "you touch. Keep it ON for 3 seconds.", relay_follows_touch, hold=3,
                                      needs=("relay", "touch")))]),
    "light": ([("Numbers that slide", "A button is only ON or OFF. The light sensor gives a number that slides "
                                      "smoothly from dark to bright."),
               ("Thresholds", "Pick a number: below it = dark, above it = bright. That's how night lights know "
                              "when to turn on.")],
              [(None, lambda s: Until(s, "Blink the light", "Cover and uncover the sensor 3 times (slowly - it "
                                      "reads about once a second).", light_blinks, remember_light, live_blinks,
                                      needs=("light",))),
               (None, lambda s: Until(s, "Super dark", "Make it really dark: cup both hands over the sensor. Under "
                                      "3 lux for 2 seconds!", lambda k, a: light_now(k) < 3, remember_light,
                                      live_light, hold=2, needs=("light",)))]),
    "dht20": ([("Sensors need time", "The sensor reads about once a second, and the air takes a moment to "
                                     "change. Be patient!"),
               ("Fahrenheit", "Some countries use Fahrenheit: F = C x 9 / 5 + 32. 20 C is 68 F.")],
              [(2, lambda s: Until(s, "Cool down", "Now stop breathing on it and wait: the humidity drops again.",
                                   cooled_down, remember_peak, live_cool, needs=("weather",))),
               (None, lambda s: Quiz(s, "Celsius to Fahrenheit", "Use the formula on the screen.",
                                     fahrenheit_quiz, rounds=1, needs=("weather",)))]),
    "distance": ([("Speed of sound", "Sound travels about 343 metres a second - the sensor times the echo in "
                                     "millionths of a second!"),
                  ("Soft things", "Soft things like jumpers swallow the sound, so the reading jumps around. Flat "
                                  "hard things work best.")],
                 [(None, lambda s: Until(s, "Exactly 15", "Hold your hand about 15 cm away (12 to 18) for 2 seconds.",
                                         lambda k, a: 12 <= distance_now(k) <= 18, live=live_distance, hold=2,
                                         needs=("distance",))),
                  (None, lambda s: Until(s, "Zoom in", "Start far away (more than 40 cm), then move in closer "
                                         "than 10 cm.", far_then_close, live=live_distance,
                                         needs=("distance",)))]),
    "lcd": ([("Strings", "In Python, words are called STRINGS and live in quotes: \"Hello\". The LCD shows "
                         "strings."),
             ("Tiny screen design", "16 letters is not much! Short words and smart abbreviations make a great "
                                    "tiny screen.")],
            [(None, lambda s: Quiz(s, "Which line?", "Look at the LCD ON THE KIT.", lcd_line_quiz, rounds=2,
                                   needs=("lcd",), hide_mirror=True)),
             (None, lambda s: Entry(s, "How many letters?", "How many letters fit on ONE line of the LCD?",
                                    "Type a number:", sixteen_letters, needs=("lcd",)))]),
    "rfid": ([("Radio power", "The reader sends out radio waves. They power the card's chip just long enough "
                              "for it to answer."),
              ("Close only", "It only works a few centimetres away - that's on purpose, so nobody reads your "
                             "card from across the room.")],
             [(None, lambda s: Count(s, "Tap three times", "Tap the card 3 times (take it away in between).", "card",
                                     3, react=lambda k, c: k.beep(0.05), needs=("cards",))),
              (None, lambda s: Until(s, "Hold it still", "Hold the card on the reader for 3 seconds.",
                                     lambda k, a: k.state["card"] is not None, hold=3, needs=("cards",)))]),
    "matrix": ([("Rows and columns", "Every light has an address: its row (0 to 7) and its column (0 to 7)."),
                ("Pictures are lists", "A picture is a list of 8 rows. X = on, . = off. \"XXXXXXXX\" is a full "
                                       "row.")],
               [(None, lambda s: Draw(s, "Top row", "Light ONLY the top row: all 8 lights.", top_row)),
                (None, lambda s: Draw(s, "A frame", "Draw a frame: every light around the edge, nothing inside.",
                                      frame_picture))]),
    "segment": ([("Letters too", "It can also show some letters: H, E, L, P, A, b, C, d... but not all of them."),
                 ("Colon", "The two dots in the middle are the colon - for showing a time like 12:30.")],
                [(None, lambda s: Entry(s, "Say HELP", "Make the display show HELP.", "What should it show?",
                                        show_help, needs=("seg",))),
                 (None, lambda s: Quiz(s, "Count the bars", "Look at the 7-segment display ON THE KIT.", bars_quiz,
                                       rounds=2, needs=("seg",), hide_mirror=True))]),
    "touch": ([("Tap vs hold", "A program can count TAPS (touched, then let go) or time a HOLD (how long it "
                               "stays touched)."),
               ("Double tap", "Two taps close together can mean something else - like double-clicking a mouse.")],
              [(None, lambda s: Count(s, "Ten taps", "Tap the pad 10 times.", "touch", 10, needs=("touch",))),
               (None, lambda s: Until(s, "Double tap", "Tap twice, quickly!", double_tap, needs=("touch",)))]),
    "button_matrix": ([("Button numbers", "Button 1 is top left. Count along the row: 1 2 3 4, then 5 starts the "
                                          "next row."),
                       ("Keypads everywhere", "Phones, ATMs and door locks use the same row-and-column trick.")],
                      [(None, lambda s: InOrder(s, "First row", "Press 1, 2, 3, 4 in order.", "grid", [1, 2, 3, 4])),
                       (None, lambda s: InOrder(s, "Diagonal", "Press 1, 6, 11, 16 - corner to corner.", "grid",
                                                [1, 6, 11, 16]))]),
    "piano": ([("Octaves", "Double the Hz = the same note, but higher. Button 8 (523 Hz) is high C, double "
                           "button 1 (262 Hz)."),
               ("Songs are lists", "A song is a list of notes and how long each one plays.")],
              [(None, lambda s: InOrder(s, "Twinkle twinkle", "Play 1 1 5 5 6 6 5.", "grid", [1, 1, 5, 5, 6, 6, 5])),
               (None, lambda s: InOrder(s, "Down the scale", "Play 8 7 6 5 4 3 2 1.", "grid",
                                        [8, 7, 6, 5, 4, 3, 2, 1]))]),
    "ir": ([("Range", "Infrared bounces off walls, so it can work even when you point a bit wrong."),
            ("Every key has a number", "The remote sends a number for each key. The program turns the number into "
                                       "a name like VOL+.")],
           [(None, lambda s: InOrder(s, "Countdown", "Press 5 4 3 2 1.", "ir", ["5", "4", "3", "2", "1"],
                                     needs=("remote",))),
            (None, lambda s: Count(s, "Volume up", "Press VOL+ 3 times.", "ir", 3, accept=lambda k: k == "VOL+",
                                   react=ir_react, needs=("remote",)))]),
    "camera": ([("Resolution", "More pixels = a sharper photo. A photo can have millions of pixels."),
                ("Ask first", "Always ask before you take a photo of someone.")],
               [(None, lambda s: Photo(s, "Silly face", "Make your silliest face and take a photo.")),
                (None, lambda s: Photo(s, "Team photo", "Get your team in the picture!")),
                (None, YearbookStep)]),
    "reaction": ([("Random waits", "The wait before the beep is random, so nobody can guess when to touch."),
                  ("Best score", "The program remembers your fastest time and checks if each new one is "
                                 "faster.")],
                 [(0, lambda s: Count(s, "Practice taps", "Warm up: tap the touch pad 5 times.", "touch", 5,
                                      needs=("touch",))),
                  (1, lambda s: Reaction(s, 600)),
                  (3, lambda s: Reaction(s, 400))]),
    "parking": ([("Feedback", "Beeps, lights and numbers all tell the driver the same thing - so they don't "
                              "have to look."),
                 ("Mapping", "The program turns a distance (cm) into a beep speed and a number of rows.")],
                [(None, lambda s: Until(s, "Slow approach", "Start far (more than 40 cm), then stop 10 to 20 cm away.",
                                        far_then_park,
                                        live=live_distance, hold=1, needs=("distance",))),
                 (None, lambda s: Until(s, "Hold at 20", "Hold your hand about 20 cm away (17 to 23) for 3 seconds.",
                                        lambda k, a: 17 <= distance_now(k) <= 23, live=live_distance, hold=3,
                                        needs=("distance",)))]),
    "security": ([("Real alarms", "Real alarms use the same parts: a sensor, a key (card or code) and a siren."),
                  ("The countdown", "The countdown gives the owner time to leave before the alarm starts "
                                    "watching.")],
                 [(1, lambda s: Until(s, "Wrong card", "Tap the OTHER card or tag (not your key). The alarm "
                                      "should say WRONG CARD.", wrong_card, live=alarm_live, needs=("cards",))),
                  (None, lambda s: Until(s, "Arm it again", "Tap your key card again to turn the alarm back on.",
                                         armed_again, live=alarm_live, needs=("cards",)))]),
    "simon": ([("Getting harder", "Every round adds one more button, so the list grows: 1, then 2, then 3..."),
               ("Memory tricks", "Say the buttons out loud, or give the corners names - it really helps!")],
              [(0, lambda s: Simon(s, 2)), (2, lambda s: Simon(s, 4)), (None, lambda s: Simon(s, 6))]),
    "matrix_lights": ([("Squares", "Each button controls a 2 x 2 square of lights on the matrix."),
                       ("Sets", "The program keeps a SET of the squares that are on. Pressing adds or removes "
                                "one.")],
                      [(None, lambda s: Until(s, "Diagonal", "Light only the diagonal: 1, 6, 11 and 16.",
                                              lambda k, a: lights_of(a.show) == DIAGONAL, live=lights_live)),
                       (None, lambda s: Until(s, "Frame", "Light only the squares around the edge (12 of them).",
                                              lambda k, a: lights_of(a.show) == FRAME, live=lights_live))]),
}


for _name, (_learn, _extra) in MORE.items():
    _lesson = LESSONS[_name]
    _lesson.learn = _lesson.learn + _learn
    _list = list(_lesson.challenges)
    for _at, _make in _extra:
        if _at is None:
            _list.append(_make)
        else:
            _list.insert(_at, _make)
    _lesson.challenges = _list
