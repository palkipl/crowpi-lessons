#!/usr/bin/env python3
# ==================================================================
#  CrowPi Lessons  -  the app
#
#  Click a lesson: it opens in Thonny and starts by itself.
#  Lessons that need special switches or parts ask you to check first.
#
#  How to open it:
#   - double-click "CrowPi Lessons" in the CrowPi_Club folder
#     (it installs itself, then: Desktop icon, Raspberry menu > Education,
#      and it opens by itself when the kit starts)
#   - or install crowpi-lessons_1.7_all.deb (the same app as a normal
#     Raspberry Pi OS package; its lessons live in /usr/share/crowpi-lessons)
#
#  Started from a folder with a NEWER version than the one installed, it
#  installs that version by itself (and removes old lessons and old versions).
#  Started from the same or an older version, it opens the installed copy.
#  If it was opened from inside the zip file (the archive program copies just one
#  file into /tmp/xa-...), it finds the real lesson folder by itself or
#  offers to unzip the lessons for you.
# ==================================================================
import glob
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile

APP_NAME = "CrowPi Lessons"
APP_VERSION = "2.0"                  # raise this for every new version: kits update by themselves
APP_ID = "crowpi-lessons"
WINDOW_CLASS = "CrowPiLessons"
HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
DESKTOP = os.path.join(HOME, "Desktop")
INSTALL_DIR = os.path.join(HOME, "CrowPi_Club")          # (not on the Desktop: only the app icon is there)
SYSTEM_DIR = "/usr/share/crowpi-lessons"
MY_PROGRAMS = os.path.join(HOME, "My_CrowPi_Programs")
PHOTOS = os.path.join(HOME, "Pictures", "CrowPi_Photos")
OLD_DESKTOP = os.path.join(HOME, "Old_Desktop_Files")         # what was on the Desktop before
# where older versions kept things (on the Desktop) - moved on install
OLD_INSTALL_DIR = os.path.join(DESKTOP, "CrowPi_Club")
OLD_MY_PROGRAMS = os.path.join(DESKTOP, "My_CrowPi_Programs")
OLD_PHOTOS = os.path.join(DESKTOP, "CrowPi_Photos")
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config"))
CONFIG_FILE = os.path.join(CONFIG_HOME, "crowpi-lessons.json")
REQUEST = os.path.join(HOME, ".crowpi_run_request")
PROGRESS = os.path.join(HOME, ".crowpi_progress.json")      # lesson apps save finished challenges here
CODE_VALUES = os.path.join(HOME, ".crowpi_code.json")
SCORES = os.path.join(HOME, ".crowpi_show_scores.json")      # the games' high scores ("reset" clears them)
DATA_HOME = os.environ.get("XDG_DATA_HOME", os.path.join(HOME, ".local", "share"))
USER_MENU_ENTRY = os.path.join(DATA_HOME, "applications", APP_ID + ".desktop")
SYSTEM_MENU_ENTRY = os.path.join("/usr/share/applications", APP_ID + ".desktop")
DESKTOP_ICON = os.path.join(DESKTOP, "CrowPi_Lessons.desktop")
USER_ICON = os.path.join(DATA_HOME, "icons", "hicolor", "128x128", "apps", APP_ID + ".png")
SYSTEM_ICON = "/usr/share/icons/hicolor/128x128/apps/" + APP_ID + ".png"
MARKERS = ("crowpi_kit.py", "buzzer.py", "lessons_app.py")
AUTOSTART = os.path.join(CONFIG_HOME, "autostart", APP_ID + ".desktop")
# lesson folders from before the app existed - moved to the Rubbish Bin on install
OLD_FOLDERS = ("CrowPi_All_Manual", "CrowPi_Manual_Lessons", "CrowPi_Club_Lessons")

READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
WRITABLE = READ_ONLY | stat.S_IWUSR
RUNNABLE = WRITABLE | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH

ANY, BUTTON, MOTOR = "any", "BUTTON", "MOTOR"
COLORS = {ANY: "#2E9E5B", BUTTON: "#E07B00", MOTOR: "#2F6FD0"}
ACTIVE = {ANY: "#36B86A", BUTTON: "#F28C0F", MOTOR: "#3C80E6"}
BG = "#1B2230"
MODE_TEXT = {
    BUTTON: "BUTTON MODE\nLeft switch block (UX1): all 8 UP\nRight switch block (UX5): all 8 DOWN",
}

# (button text, file, switch mode, parts to plug in)
LESSONS = [
    ("Day 1\nHello CrowPi", "hello_crowpi.py", ANY, ""),
    ("Day 2\nBuzzer", "buzzer.py", ANY, ""),
    ("Day 3\nButtons + Buzzer", "button_buzzer.py", BUTTON, ""),
    ("Day 4\nRelay", "relay.py", ANY, ""),
    ("Day 5\nLight Sensor", "light_sensor.py", ANY, ""),
    ("Day 6\nTemperature", "dht20.py", ANY, ""),
    ("Day 7\nWeather Station", "weather_station.py", ANY, ""),
    ("Day 8\nDistance", "distance.py", ANY, ""),
    ("Day 9\nLCD Screen", "lcd.py", ANY, ""),
    ("Day 10\nRFID Cards", "rfid.py", ANY, "the white card or blue key tag"),
    ("Day 11\nLED Matrix", "matrix_demo.py", ANY, ""),
    ("Day 12\n7-Segment", "segment.py", ANY, ""),
    ("Day 13\nTouch Sensor", "touch.py", ANY, ""),
    ("Day 14\nButton Matrix", "button_matrix.py", BUTTON, ""),
    ("Day 15\nButton Piano", "piano.py", BUTTON, ""),
    ("Day 16\nIR Remote", "IR.py", ANY, "the IR receiver in the IR socket, and the remote"),
    ("Day 17\nCamera", "camera.py", ANY, ""),
    ("Day 18\nReaction Game", "reaction_game.py", ANY, ""),
    ("Day 19\nParking Sensor", "parking_sensor.py", ANY, ""),
    ("Day 20\nSecurity Alarm", "security_alarm.py", ANY, "the white card or blue key tag"),
    ("Day 21\nSimon Says", "simon_says.py", BUTTON, ""),
    ("Day 22\nThe CrowPi Show", "crowpi_show.py", BUTTON,
     "the IR receiver + remote, an RFID card (and the camera if you have one)"),
]
BONUS = [
    ("Matrix Lights", "matrix_lights.py", BUTTON, ""),
    ("Kit Check", "check_kit.py", ANY, ""),
]
GAMES = [
    ("Snake", "game_snake.py", BUTTON, ""),
    ("Whack-a-Mole", "game_whack.py", BUTTON, ""),
    ("Guess the Number", "game_guess.py", BUTTON, ""),
    ("Reaction Game", "reaction_game.py", ANY, ""),
    ("Simon Says", "simon_says.py", BUTTON, ""),
]
# The Games button asks for the admin password. Only its fingerprint (SHA-256)
# is kept here, not the password itself. Change it: see TEACHER_GUIDE.txt.
GAMES_PASSWORD_SHA256 = "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"
# Lessons taken out of the club (motor parts). "Install" removes old copies of them.
RETIRED = ("vibration.py", "motion.py", "setup_kit.py", "stepmotor.py", "servo.py", "tilt.py", "blinking_led.py",
           "sound.py", "rfid_read.py", "rfid_write.py", "matrix_buzzer.py")


# ==================================================================
#  Finding the lesson folder
# ==================================================================
def reset_high_scores(parent):
    """Ask first, then clear every game's high score. True if they were cleared."""
    from tkinter import messagebox
    if not messagebox.askyesno("Reset high scores", "Reset ALL the game high scores on this kit?",
                               parent=parent):
        return False
    try:
        os.remove(SCORES)
    except OSError:
        pass
    return True


def lessons_done():
    """The lesson files whose challenges were all done (saved by the lesson apps)."""
    try:
        with open(PROGRESS, encoding="utf-8") as f:
            progress = json.load(f)
        return {name for name, p in progress.items() if p.get("done", 0) >= p.get("of", 1)}
    except (OSError, ValueError, AttributeError):
        return set()


def has_lessons(folder):
    return bool(folder) and all(os.path.isfile(os.path.join(folder, m)) for m in MARKERS)


def in_archive_temp(path):
    """True for the temporary folders archive programs use when you open a
    file straight from inside a zip (xarchiver: /tmp/xa-..., file-roller: /tmp/.fr-...)."""
    real = os.path.realpath(path)
    return any(real.startswith(prefix) for prefix in
               ("/tmp/xa-", "/tmp/.fr-", "/tmp/file-roller", "/tmp/engrampa", "/tmp/.ark"))


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(**values):
    config = load_config()
    config.update(values)
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=1)
    except OSError:
        pass


def launch_folders():
    """The folders this app was started from: its launcher, its own file, the current folder."""
    folders = []
    for arg in sys.argv[1:]:
        if arg.startswith("--"):
            continue
        path = os.path.abspath(os.path.expanduser(arg))
        folders.append(path if os.path.isdir(path) else os.path.dirname(path))
    folders.append(HERE)
    try:
        folders.append(os.getcwd())
    except OSError:
        pass
    return folders


def candidate_folders():
    found = []

    def add(folder):
        if folder and folder not in found:
            found.append(folder)

    for folder in launch_folders():
        add(folder)
        add(os.path.join(folder, "CrowPi_Club"))
    add(load_config().get("lesson_folder"))
    add(INSTALL_DIR)
    add(SYSTEM_DIR)
    add(os.path.join(HOME, "CrowPi_Club"))
    add(os.path.join(HOME, "Downloads", "CrowPi_Club"))
    add(os.path.join(HOME, "Downloads", "CrowPi_Club", "CrowPi_Club"))
    for pattern in ("/media/*/*/CrowPi_Club", "/media/*/*/*/CrowPi_Club", "/media/*/CrowPi_Club",
                    "/mnt/*/CrowPi_Club", "/run/media/*/*/CrowPi_Club"):
        for folder in sorted(glob.glob(pattern)):
            add(folder)
    return found


def find_lesson_folder():
    for folder in candidate_folders():
        if has_lessons(folder) and not in_archive_temp(folder):
            return os.path.realpath(folder)
    return None


def find_zip_files():
    """CrowPi_Club zip files in Downloads, on the Desktop, in the home folder or on USB sticks."""
    places = [os.path.join(HOME, "Downloads"), DESKTOP, HOME]
    places += glob.glob("/media/*/*") + glob.glob("/media/*/*/*")
    zips = []
    for place in places:
        for path in glob.glob(os.path.join(place, "CrowPi_Club*.zip")):
            if path not in zips:
                zips.append(path)
    return sorted(zips, key=os.path.getmtime, reverse=True)


def unzip_lessons(zip_path):
    """Unzip the lesson files to ~/CrowPi_Club. Returns that folder."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        apps = [n for n in names if n.endswith("lessons_app.py")]
        if not apps:
            raise ValueError("That zip file has no CrowPi lessons in it.")
        prefix = apps[0][:-len("lessons_app.py")]
        os.makedirs(INSTALL_DIR, exist_ok=True)
        for name in names:
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = name[len(prefix):]
            if "/" in relative or "\\" in relative or relative.startswith("."):
                continue                                 # only the lesson files themselves
            target = os.path.join(INSTALL_DIR, relative)
            if os.path.exists(target):
                os.chmod(target, WRITABLE)
            with archive.open(name) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
    save_config(lesson_folder=INSTALL_DIR)
    return INSTALL_DIR


# ==================================================================
#  Installing on this kit (no terminal needed)
# ==================================================================
def thonny_user_dir():
    if os.environ.get("THONNY_USER_DIR"):
        return os.path.expanduser(os.environ["THONNY_USER_DIR"])
    return os.path.join(CONFIG_HOME, "Thonny")


def user_plugin_path():
    version = "python%d.%d" % sys.version_info[:2]
    return os.path.join(thonny_user_dir(), "plugins", "lib", version, "site-packages",
                        "thonnycontrib", "crowpi_autorun", "__init__.py")


SYSTEM_PLUGIN = "/usr/lib/python3/dist-packages/thonnycontrib/crowpi_autorun/__init__.py"


def autorun_ready():
    return os.path.exists(user_plugin_path()) or os.path.exists(SYSTEM_PLUGIN)


def desktop_entry(command, icon):
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=" + APP_NAME + "\n"
            "GenericName=CrowPi lesson launcher\n"
            "Comment=Click a CrowPi Club lesson: it opens in Thonny and starts\n"
            "Exec=" + command + "\n"
            "Icon=" + icon + "\n"
            "Terminal=false\n"
            "Categories=Education;\n"
            "StartupWMClass=" + WINDOW_CLASS + "\n")


def ensure_my_programs(folder):
    """Make ~/My_CrowPi_Programs with a copy of crowpi_kit.py (for Save As copies)."""
    try:
        os.makedirs(MY_PROGRAMS, exist_ok=True)
        readme = os.path.join(MY_PROGRAMS, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write("Save your own versions of the lessons here.\n"
                        "In Thonny: File > Save As... > Home > My_CrowPi_Programs\n"
                        "Give your file a new name, like buzzer_alex.py\n"
                        "Keep the line  import crowpi_kit  at the top.\n")
        source = os.path.join(folder, "crowpi_kit.py")
        target = os.path.join(MY_PROGRAMS, "crowpi_kit.py")
        if os.path.exists(source):
            if os.path.exists(target):
                os.chmod(target, WRITABLE)
            shutil.copyfile(source, target)
            os.chmod(target, READ_ONLY)
    except OSError:
        pass


def folder_version(folder):
    """The APP_VERSION of the lessons in a folder, as numbers: "1.3" -> (1, 3).
    Folders from before version numbers -> (0,). No lessons -> None."""
    if not has_lessons(folder):
        return None
    try:
        with open(os.path.join(folder, "lessons_app.py"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("APP_VERSION"):
                    text = line.split("=", 1)[1].split("#")[0].strip().strip("\"'")
                    return tuple(int(part) for part in text.split("."))
    except (OSError, ValueError):
        pass
    return (0,)


def version_text(version):
    return ".".join(str(part) for part in version) if version and version != (0,) else "old"


def autostart_entry(command, icon, on=True):
    entry = desktop_entry(command, icon)
    if not on:
        entry += "Hidden=true\n"                 # also hides the apt package's start-up entry
    return entry


def set_autostart(on, command=None, icon=None):
    """Open CrowPi Lessons by itself when the kit starts (on) - or not (off)."""
    command = command or 'python3 "' + os.path.join(INSTALL_DIR, "lessons_app.py") + '"'
    icon = icon or (USER_ICON if os.path.exists(USER_ICON) else APP_ID)
    try:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w", encoding="utf-8") as f:
            f.write(autostart_entry(command, icon, on))
    except OSError:
        return False
    save_config(autostart=bool(on))
    return True


def autostart_on():
    return load_config().get("autostart", True)


def move_to_trash(path):
    """Move a file or folder to the Rubbish Bin (it can be restored from there)."""
    try:
        if subprocess.run(["gio", "trash", path], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=30).returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:                                           # no 'gio': do it the same way by hand
        trash = os.path.join(DATA_HOME, "Trash")
        os.makedirs(os.path.join(trash, "files"), exist_ok=True)
        os.makedirs(os.path.join(trash, "info"), exist_ok=True)
        base = name = os.path.basename(path.rstrip("/"))
        number = 1
        while os.path.exists(os.path.join(trash, "files", name)):
            number += 1
            name = "%s.%d" % (base, number)
        with open(os.path.join(trash, "info", name + ".trashinfo"), "w", encoding="utf-8") as f:
            f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n"
                    % (os.path.abspath(path), time.strftime("%Y-%m-%dT%H:%M:%S")))
        shutil.move(path, os.path.join(trash, "files", name))
        return True
    except OSError:
        return False


def remove_old_versions(new_version, log=print):
    """Old lesson folders -> Rubbish Bin. An older apt package -> removed."""
    for place in (DESKTOP, os.path.join(HOME, "Downloads"), HOME, os.path.join(HOME, "Documents")):
        for name in OLD_FOLDERS:
            folder = os.path.join(place, name)
            if os.path.isdir(folder) and move_to_trash(folder):
                log("Old lessons moved to the Rubbish Bin: " + folder)
    package = folder_version(SYSTEM_DIR)
    if package is not None and package < new_version and shutil.which("apt-get"):
        try:                                       # works when the kit doesn't need a password
            subprocess.Popen(["sudo", "-n", "apt-get", "remove", "-y", "crowpi-lessons"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            log("Removing the old crowpi-lessons package (version %s)" % version_text(package))
        except OSError:
            pass


def unique_path(path):
    while os.path.exists(path):
        stem, extension = os.path.splitext(path)
        path = stem + "_old" + extension
    return path


def merge_folder(old, new, log=print):
    """Move everything from an old folder into a new one (never overwrites anything)."""
    if not os.path.isdir(old) or os.path.realpath(old) == os.path.realpath(new):
        return
    os.makedirs(new, exist_ok=True)
    for name in sorted(os.listdir(old)):
        shutil.move(os.path.join(old, name), unique_path(os.path.join(new, name)))
    try:
        os.rmdir(old)
    except OSError:
        pass
    log("Moved %s to %s" % (old, new))


def move_old_install(log=print):
    """Older versions lived on the Desktop. Students' own files go to My_CrowPi_Programs,
    the old lesson copy is removed, and the photos and programs folders move home."""
    old = OLD_INSTALL_DIR
    if os.path.isdir(old) and os.path.realpath(old) != os.path.realpath(INSTALL_DIR):
        for name in sorted(os.listdir(old)):
            path = os.path.join(old, name)
            student_file = (os.path.isfile(path) and os.stat(path).st_mode & stat.S_IWUSR   # lessons are locked
                            and not os.path.exists(os.path.join(INSTALL_DIR, name)) and name not in RETIRED
                            and not name.startswith((".", "CrowPi_check_report")))
            if student_file:
                os.makedirs(MY_PROGRAMS, exist_ok=True)
                shutil.move(path, unique_path(os.path.join(MY_PROGRAMS, name)))
                log("Kept a student's file: " + name)
        for folder, _dirs, files in os.walk(old):
            os.chmod(folder, stat.S_IRWXU)
            for name in files:
                os.chmod(os.path.join(folder, name), WRITABLE)
        shutil.rmtree(old, ignore_errors=True)
        log("Removed the old lesson folder on the Desktop")
    merge_folder(OLD_MY_PROGRAMS, MY_PROGRAMS, log)
    merge_folder(OLD_PHOTOS, PHOTOS, log)


def pcmanfm_desktop_files():
    """The desktop settings files of the Raspberry Pi desktop (made from the system ones if missing)."""
    folder = os.path.join(CONFIG_HOME, "pcmanfm")
    files = glob.glob(os.path.join(folder, "*", "desktop-items-*.conf"))
    if files:
        return files
    made = []
    for system in sorted(glob.glob("/etc/xdg/pcmanfm/LXDE-pi/desktop-items-*.conf")) or \
            ["/etc/xdg/pcmanfm/LXDE-pi/desktop-items-0.conf"]:
        mine = os.path.join(folder, "LXDE-pi", os.path.basename(system))
        try:
            os.makedirs(os.path.dirname(mine), exist_ok=True)
            if os.path.exists(system):
                shutil.copyfile(system, mine)
            made.append(mine)
        except OSError:
            pass
    return made


def show_desktop_icons(show):
    """Show or hide the Rubbish Bin, USB drives and Documents icons on the Desktop."""
    values = {"show_trash": "1" if show else "0", "show_mounts": "1" if show else "0",
              "show_documents": "1" if show else "0"}
    for path in pcmanfm_desktop_files():
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        if "[*]" not in lines:
            lines = ["[*]"] + lines
        lines = [line for line in lines if line.split("=")[0].strip() not in values]
        at = lines.index("[*]") + 1
        lines[at:at] = ["%s=%s" % item for item in values.items()]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass
    if shutil.which("pcmanfm"):
        try:
            subprocess.Popen(["pcmanfm", "--reconfigure"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass


def tidy_desktop(log=print):
    """Make CrowPi Lessons the only thing on the Desktop. Everything else moves (never deleted)
    to the folder Old_Desktop_Files in the home folder."""
    if os.path.isdir(DESKTOP):
        for name in sorted(os.listdir(DESKTOP)):
            if name == os.path.basename(DESKTOP_ICON) or name.startswith("."):
                continue
            os.makedirs(OLD_DESKTOP, exist_ok=True)
            shutil.move(os.path.join(DESKTOP, name), unique_path(os.path.join(OLD_DESKTOP, name)))
            log("Moved off the Desktop: " + name)
    show_desktop_icons(False)
    log("The Desktop now only shows CrowPi Lessons")


# ------------------------------------------------------------------
#  Locking the games that came on the kit (Minecraft, Doom, ...)
#  Their menu entries are replaced (for this user) by copies that open
#  game_lock.py first, which asks for the admin password.
# ------------------------------------------------------------------
USER_APPS = os.path.join(DATA_HOME, "applications")
GAME_LOCK_MARK = "X-CrowPi-Locked=true"
GAME_WORDS = ("minecraft", "doom", "quake", "tetris", "chess", "solitaire", "mines", "sudoku", "scratch-game")
APP_FOLDERS = ("/usr/share/applications", "/usr/local/share/applications",
               "/var/lib/flatpak/exports/share/applications",
               os.path.join(DATA_HOME, "flatpak", "exports", "share", "applications"))


def read_desktop_entry(path):
    values: dict = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return [], {}
    section = ""
    for line in lines:
        if line.startswith("["):
            section = line.strip()
        elif "=" in line and section == "[Desktop Entry]":
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip())
    return lines, values


def installed_games():
    """(file name, path, entry values) for every game in the Raspberry menu."""
    games = {}
    for folder in APP_FOLDERS:
        for path in sorted(glob.glob(os.path.join(folder, "*.desktop"))):
            name = os.path.basename(path)
            if name in games or name.startswith(APP_ID):
                continue
            _lines, values = read_desktop_entry(path)
            categories = values.get("Categories", "").split(";")
            words = (name + " " + values.get("Name", "") + " " + values.get("Exec", "")).lower()
            is_game = "Game" in categories or any(word in words for word in GAME_WORDS)
            if is_game and values.get("Exec") and values.get("NoDisplay", "").lower() != "true":
                games[name] = (path, values)
    return games


def desktop_quote(word):
    if word and not any(ch in word for ch in ' \t"\'\\$`<>|&;()*?#~'):
        return word
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'


def locked_exec(command, name, folder):
    import shlex
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    words = [w for w in words if not (len(w) == 2 and w.startswith("%"))]     # %f, %U ... (file codes)
    lock = os.path.join(folder, "game_lock.py")
    return " ".join(desktop_quote(w) for w in ["python3", lock, "--name", name, "--"] + words)


def lock_installed_games(lock, folder=INSTALL_DIR, log=print):
    """lock=True: every installed game asks for the admin password. False: back to normal."""
    os.makedirs(USER_APPS, exist_ok=True)
    for path in glob.glob(os.path.join(USER_APPS, "*.desktop")):        # remove our old locks first
        lines, _values = read_desktop_entry(path)
        if GAME_LOCK_MARK in lines:
            try:
                os.remove(path)
            except OSError:
                pass
    if not lock:
        log("Installed games: open (no password)")
        return []
    locked = []
    for name, (path, values) in installed_games().items():
        mine = os.path.join(USER_APPS, name)
        if os.path.exists(mine):                       # the user already has their own copy: lock that
            path = mine
        lines, _values = read_desktop_entry(path)
        title = values.get("Name", name[:-8])
        new = []
        for line in lines:
            key = line.partition("=")[0].strip()
            if key == "Exec":
                line = "Exec=" + locked_exec(line.partition("=")[2].strip(), title, folder)
            elif key == "TryExec" or key == "DBusActivatable":
                continue
            new.append(line)
            if line.strip() == "[Desktop Entry]":
                new.append(GAME_LOCK_MARK)
        try:
            with open(mine, "w", encoding="utf-8") as f:
                f.write("\n".join(new) + "\n")
            locked.append(title)
        except OSError as problem:
            log("Could not lock %s: %s" % (title, problem))
    log("Installed games locked with the admin password: " + (", ".join(locked) or "none found"))
    return locked


def games_locked():
    return load_config().get("games_locked", True) is not False


def install_kit(source, log=print):
    """Copy the lessons to ~/CrowPi_Club, lock them, remove old lessons, and add
    the app to the Desktop, the Raspberry menu and start-up, plus the Thonny helper."""
    target = INSTALL_DIR
    os.makedirs(target, exist_ok=True)
    new_files = set()
    if os.path.realpath(source) != os.path.realpath(target):
        for name in sorted(os.listdir(source)):
            path = os.path.join(source, name)
            if (name.startswith(".") or name.startswith("CrowPi_check_report")
                    or not os.path.isfile(path)):
                continue
            destination = os.path.join(target, name)
            if os.path.exists(destination):
                os.chmod(destination, WRITABLE)
            shutil.copyfile(path, destination)
            new_files.add(name)
        log("Copied %d lesson files to %s" % (len(new_files), target))
        # files that aren't in the new version: old lessons are deleted, but anything
        # a student saved here (not locked) is moved to My_CrowPi_Programs
        for name in sorted(os.listdir(target)):
            path = os.path.join(target, name)
            if name in new_files or not os.path.isfile(path):
                continue
            if name in RETIRED or not os.access(path, os.W_OK) or name.startswith("CrowPi_check_report"):
                os.chmod(path, WRITABLE)
                os.remove(path)
                log("Removed old file " + name)
            else:
                os.makedirs(MY_PROGRAMS, exist_ok=True)
                keep = os.path.join(MY_PROGRAMS, name)
                while os.path.exists(keep):
                    stem, extension = os.path.splitext(keep)
                    keep = stem + "_saved" + extension
                shutil.move(path, keep)
                log("Moved a student's file to " + keep)
    else:
        for name in RETIRED:                              # lessons no longer in the club
            path = os.path.join(target, name)
            if os.path.isfile(path):
                os.chmod(path, WRITABLE)
                os.remove(path)
                log("Removed old lesson " + name)
    pycache = os.path.join(target, "__pycache__")
    if os.path.isdir(pycache):
        shutil.rmtree(pycache, ignore_errors=True)
    protected = 0
    for name in os.listdir(target):
        path = os.path.join(target, name)
        if os.path.isfile(path):
            os.chmod(path, READ_ONLY | (RUNNABLE & ~stat.S_IWUSR if name.endswith(".desktop") else 0))
            protected += 1
    log("Locked %d lesson files (read-only - use Save As to change one)" % protected)

    ensure_my_programs(target)
    log("Students' folder: " + MY_PROGRAMS)

    icon = USER_ICON
    try:
        os.makedirs(os.path.dirname(USER_ICON), exist_ok=True)
        shutil.copyfile(os.path.join(target, "crowpi_icon.png"), USER_ICON)
    except OSError:
        icon = os.path.join(target, "crowpi_icon.png")
    command = 'python3 "' + os.path.join(target, "lessons_app.py") + '"'
    entry = desktop_entry(command, icon)
    for path in (USER_MENU_ENTRY, DESKTOP_ICON):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path):
                os.chmod(path, RUNNABLE)
            with open(path, "w", encoding="utf-8") as f:
                f.write(entry)
            os.chmod(path, RUNNABLE)
            log("App icon: " + path)
        except OSError as problem:
            log("Could not make " + path + ": " + str(problem))
    if set_autostart(autostart_on(), command, icon):
        log("Opens by itself when the kit starts: " + ("ON" if autostart_on() else "OFF"))

    try:
        plugin = user_plugin_path()
        os.makedirs(os.path.dirname(plugin), exist_ok=True)
        shutil.copyfile(os.path.join(target, "crowpi_thonny_plugin.py"), plugin)
        log("Thonny helper installed (lessons start by themselves)")
    except OSError as problem:
        log("Could not install the Thonny helper: " + str(problem))

    try:
        move_old_install(log)
        tidy_desktop(log)
    except OSError as problem:
        log("Could not tidy the Desktop: " + str(problem))
    try:
        lock_installed_games(games_locked(), target, log)
    except OSError as problem:
        log("Could not lock the installed games: " + str(problem))
    version = folder_version(target) or (0,)
    remove_old_versions(version, log)
    save_config(lesson_folder=target, installed_version=version_text(version))
    return target


def newest_source(folder):
    """A folder with a newer version than the installed one, or None.
    Looks at the folder the app was started from and at the apt package."""
    installed = folder_version(INSTALL_DIR)
    best, best_version = None, installed
    for source in (folder, SYSTEM_DIR):
        if not source or os.path.realpath(source) == os.path.realpath(INSTALL_DIR):
            continue
        version = folder_version(source)
        if version is not None and (best_version is None or version > best_version):
            best, best_version = source, version
    return best


def run_installed_copy(*flags):
    """Restart as the installed app (only once, so it can never loop)."""
    if os.environ.get("CROWPI_RESTARTED"):
        return
    app = os.path.join(INSTALL_DIR, "lessons_app.py")
    os.environ["CROWPI_RESTARTED"] = "1"
    os.execv(sys.executable, [sys.executable, app] + list(flags))


# ==================================================================
#  Only one copy of the app at a time
# ==================================================================
def claim_single_instance(raise_event):
    """Returns a socket if this is the only copy running. Otherwise asks the
    running copy to come to the front and returns None."""
    name = "\0crowpi-lessons-%d" % os.getuid()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(name)
    except OSError:
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(name)
            client.sendall(b"show")
            client.close()
        except OSError:
            pass
        return None
    server.listen(4)

    def serve():
        while True:
            try:
                connection, _ = server.accept()
                connection.close()
                raise_event.set()
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    return server


BUZZER_SETTING = os.path.join(HOME, ".crowpi_buzzer.json")   # read by crowpi_kit.py


def save_buzzer_setting(on):
    """The Buzzer switch: tell crowpi_kit.py if lessons may use the buzzer.
    (The Kit Check always uses it.)"""
    try:
        with open(BUZZER_SETTING, "w", encoding="utf-8") as f:
            json.dump({"buzzer": "on" if on else "off", "time": time.time()}, f)
    except OSError:
        pass


def thonny_command():
    program = shutil.which("thonny")
    if program:
        return [program]
    return [sys.executable, "-m", "thonny"]


def open_folder(folder):
    os.makedirs(folder, exist_ok=True)
    subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ==================================================================
#  The window
# ==================================================================
class LessonsApp:
    def __init__(self, root, folder, just_installed=False):
        import tkinter as tk
        self.tk = tk
        self.root = root
        self.folder = folder
        self.raise_event = threading.Event()
        self.lesson_buttons: dict = {}
        self.progress_time = 0.0
        self.games_locked = games_locked()                                  # set by the installer
        self.games_unlocked = not self.games_locked   # (locked again every time the app opens)
        self.buzzer_on = True                     # the Buzzer switch is ON every time the app opens
        save_buzzer_setting(True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.title(APP_NAME)
        root.configure(bg=BG)
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = min(screen_w - 24, 1000), min(screen_h - 76, 560)
        root.geometry("%dx%d+%d+%d" % (width, height, (screen_w - width) // 2, 8))
        root.minsize(640, 400)
        small = screen_h < 720
        self.font = ("DejaVu Sans", 10 if small else 12, "bold")
        self.title_font = ("DejaVu Sans", 18 if small else 22, "bold")
        self.pad = 3 if small else 5
        self.set_window_icon()
        self.root.after(500, self.check_raise)
        if folder:
            self.show_lessons()
            if just_installed:
                self.root.after(400, self.installed_message)
        else:
            self.show_finder()

    # ---------- helpers ----------
    def set_window_icon(self):
        for path in (os.path.join(self.folder or HERE, "crowpi_icon.png"), USER_ICON, SYSTEM_ICON):
            if os.path.exists(path):
                try:
                    self.icon_image = self.tk.PhotoImage(file=path)
                    self.root.iconphoto(True, self.icon_image)
                    return
                except self.tk.TclError:
                    pass

    def check_raise(self):
        if self.raise_event.is_set():
            self.raise_event.clear()
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.update_ticks()
        self.root.after(500, self.check_raise)

    def close(self):
        save_buzzer_setting(True)                 # app closed: the buzzer is back to normal
        self.root.destroy()

    def buzzer_switch(self, parent):
        """A switch you click: Buzzer ON (green, knob right) / OFF (grey, knob left)."""
        box = self.tk.Frame(parent, bg=BG)
        self.label(box, "Buzzer", 11, True, "white").pack(side="left", padx=(0, 6))
        width, height = 64, 28
        switch = self.tk.Canvas(box, width=width, height=height, bg=BG, highlightthickness=0,
                                cursor="hand2")
        switch.pack(side="left")

        def draw():
            switch.delete("all")
            color = COLORS[ANY] if self.buzzer_on else "#5A6378"
            r = height // 2
            switch.create_oval(0, 0, height, height, fill=color, outline=color)
            switch.create_oval(width - height, 0, width, height, fill=color, outline=color)
            switch.create_rectangle(r, 0, width - r, height, fill=color, outline=color)
            knob = width - height + 3 if self.buzzer_on else 3
            switch.create_oval(knob, 3, knob + height - 6, height - 3, fill="white", outline="white")
            switch.create_text(r + 2 if self.buzzer_on else width - r - 2, r,
                               text="ON" if self.buzzer_on else "OFF", fill="white",
                               font=("DejaVu Sans", 9, "bold"))

        def flip(_event=None):
            self.buzzer_on = not self.buzzer_on
            save_buzzer_setting(self.buzzer_on)
            draw()
            self.say("Buzzer ON." if self.buzzer_on else "Buzzer OFF - lessons and games are quiet.")

        switch.bind("<Button-1>", flip)
        draw()
        return box

    def clear(self):
        for child in self.root.winfo_children():
            child.destroy()

    def label(self, parent, text, size=10, bold=False, color="#C9D3E3", **options):
        return self.tk.Label(parent, text=text, fg=color, bg=options.pop("bg", BG),
                             font=("DejaVu Sans", size, "bold" if bold else "normal"), **options)

    def button(self, parent, text, command, color="#3A4560", active="#4B5878", size=10, **options):
        return self.tk.Button(parent, text=text, command=command, bg=color, fg="white",
                              activebackground=active, activeforeground="white", relief="flat",
                              bd=0, font=("DejaVu Sans", size, "bold"), cursor="hand2", **options)

    def say(self, text):
        if getattr(self, "status", None) is not None:
            self.status.config(text=text)

    # ---------- screen 1: the lessons ----------
    def show_lessons(self):
        tk = self.tk
        self.clear()
        self.lesson_buttons = {}
        ensure_my_programs(self.folder)
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=12, pady=(8, 2))
        title = self.label(header, APP_NAME, color="white")
        title.configure(font=self.title_font)
        title.pack(side="left")
        legend = tk.Frame(header, bg=BG)
        legend.pack(side="right")
        self.buzzer_switch(header).pack(side="right", padx=(0, 18))
        for mode, text in ((ANY, "any switches"), (BUTTON, "BUTTON MODE")):
            self.label(legend, " " + text + " ", 9, True, "white", bg=COLORS[mode]).pack(side="left", padx=3)
        self.label(self.root, "Click a lesson: it opens as an app with Learn, Try it, Code and Challenge. "
                              "Click Close in the lesson when you're done.").pack(anchor="w", padx=14)

        grid = tk.Frame(self.root, bg=BG)
        grid.pack(fill="both", expand=True, padx=10, pady=(6, 2))
        columns = 6
        for i, lesson in enumerate(LESSONS):
            self.lesson_button(grid, lesson).grid(row=i // columns, column=i % columns,
                                                  padx=self.pad, pady=self.pad, sticky="nsew")
        for c in range(columns):
            grid.columnconfigure(c, weight=1, uniform="lessons")
        for r in range((len(LESSONS) + columns - 1) // columns):
            grid.rowconfigure(r, weight=1, uniform="rows")

        self.label(self.root, "Bonus", 11, True, "white").pack(anchor="w", padx=14, pady=(4, 0))
        bonus = tk.Frame(self.root, bg=BG)
        bonus.pack(fill="x", padx=10)
        for i, lesson in enumerate(BONUS):
            self.lesson_button(bonus, lesson).grid(row=0, column=i, padx=self.pad,
                                                   pady=self.pad, sticky="nsew")
            bonus.columnconfigure(i, weight=1, uniform="bonus")
        games_text = "Games" if self.games_unlocked else "Games  (locked)"
        self.button(bonus, games_text, self.open_games, "#7B3FC4", "#8E52D8", self.font[1],
                    width=1, height=2).grid(row=0, column=len(BONUS), padx=self.pad,
                                            pady=self.pad, sticky="nsew")
        bonus.columnconfigure(len(BONUS), weight=1, uniform="bonus")

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=12, pady=(4, 8))
        # buttons first, so a long message can never push them off the window
        self.button(footer, "Shut down", self.shut_down, "#B23B3B", "#C94A4A", 9,
                    padx=8, pady=3).pack(side="left", padx=(0, 8))
        self.button(footer, "Photos", lambda: open_folder(PHOTOS), size=9,
                    padx=8, pady=3).pack(side="right", padx=4)
        self.button(footer, "My programs", lambda: open_folder(MY_PROGRAMS), size=9,
                    padx=8, pady=3).pack(side="right", padx=4)
        self.button(footer, "Lesson folder", lambda: open_folder(self.folder), size=9,
                    padx=8, pady=3).pack(side="right", padx=4)
        self.button(footer, "Clear ticks", self.clear_ticks, size=9, padx=8, pady=3).pack(side="right", padx=4)
        if os.path.realpath(self.folder) == os.path.realpath(INSTALL_DIR):
            on = autostart_on()
            self.button(footer, "Opens at start-up: " + ("ON" if on else "OFF"),
                        self.toggle_autostart, COLORS[ANY] if on else "#3A4560",
                        ACTIVE[ANY] if on else "#4B5878", 9, padx=8, pady=3).pack(side="right", padx=4)
        else:
            self.button(footer, "Install on this kit", self.install, COLORS[ANY], ACTIVE[ANY], 9,
                        padx=8, pady=3).pack(side="right", padx=4)
        self.status = self.label(footer, "", 10, True, "#FFD866", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.say("Version %s" % APP_VERSION)

    def clear_ticks(self):
        """Start the ticks again (e.g. a new student on this kit)."""
        for path in (PROGRESS, CODE_VALUES):
            try:
                os.remove(path)
            except OSError:
                pass
        self.progress_time = 0.0
        for b, text in list(self.lesson_buttons.values()):
            b.config(text=text)
        self.say("Ticks, Code tab values and missions cleared - every lesson starts fresh.")

    def toggle_autostart(self):
        set_autostart(not autostart_on())
        self.show_lessons()
        self.say("CrowPi Lessons will " + ("" if autostart_on() else "NOT ") +
                 "open by itself when the kit starts.")

    def lesson_button(self, parent, lesson):
        text, filename, mode = lesson[0], lesson[1], lesson[2]
        b = self.button(parent, self.with_tick(text, filename), lambda: self.start(lesson), COLORS[mode],
                        ACTIVE[mode], self.font[1], width=1, height=2, wraplength=140)
        self.lesson_buttons[filename] = (b, text)
        return b

    def with_tick(self, text, filename):
        """A tick on lessons whose challenges are all done."""
        return text + "  \u2713" if filename in lessons_done() else text

    def update_ticks(self):
        try:
            changed = os.path.getmtime(PROGRESS)
        except OSError:
            return
        if changed != self.progress_time:
            self.progress_time = changed
            for filename, (b, text) in list(self.lesson_buttons.items()):
                try:
                    b.config(text=self.with_tick(text, filename))
                except self.tk.TclError:
                    pass

    def start(self, lesson):
        text, filename, mode, parts = lesson
        path = os.path.join(self.folder, filename)
        if not os.path.exists(path):
            self.say("Can't find " + filename + " in " + self.folder)
            return
        if (mode != ANY or parts) and not self.ready_check(text.replace("\n", " - "), mode, parts):
            self.say("OK - nothing started.")
            return
        save_buzzer_setting(self.buzzer_on)       # the switch counts for every start
        try:
            with open(REQUEST, "w", encoding="utf-8") as f:
                json.dump({"file": path, "time": time.time()}, f)
        except OSError:
            pass
        try:
            subprocess.Popen(thonny_command() + [path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.say("Thonny didn't start. Open " + filename + " from the lesson folder.")
            return
        if autorun_ready():
            self.say("Starting " + filename + " in Thonny...")
        else:
            self.say("Opened " + filename + " in Thonny - click the green Run button.")

    def install(self):
        lines: list = []
        try:
            target = install_kit(self.folder, lines.append)
        except OSError as problem:
            self.message("Install didn't finish", str(problem))
            return
        self.folder = target
        self.show_lessons()
        self.installed_message()

    def installed_message(self):
        self.message("Installed version %s!" % APP_VERSION,
                     "CrowPi Lessons is the only icon on the Desktop, in the\n"
                     "Raspberry menu > Education, and opens by itself when\n"
                     "the kit starts (turn that off with the button at the bottom).\n"
                     "Old lessons and old versions were removed. Anything else\n"
                     "that was on the Desktop is in the folder Old_Desktop_Files.\n\n"
                     "Close Thonny once (File > Exit) so lessons can\n"
                     "start by themselves next time.")

    # ---------- screen 3: games (admin password) ----------
    def open_games(self):
        if not self.games_unlocked:
            if not self.ask_password():
                return
            self.games_unlocked = True
        self.show_games()

    def ask_password(self):
        """Ask for the admin password. True if it is right."""
        window = self.popup("Admin password")
        self.label(window, "Games are locked", 16, True, "white").pack(padx=24, pady=(14, 4))
        self.label(window, "Type the admin password:", 12).pack(padx=24)
        entry = self.tk.Entry(window, show="*", font=("DejaVu Sans", 14), width=16)
        entry.pack(padx=24, pady=8)
        note = self.label(window, "", 11, True, "#FF8A80")
        note.pack()
        answer = {"ok": False}

        def check(_event=None):
            if entry.get().strip().lower() == "reset":          # "reset" = clear the high scores
                entry.delete(0, "end")
                if reset_high_scores(window):
                    note.config(text="High scores reset!", fg="#7CFFB0")
                return
            typed = entry.get().encode("utf-8")
            if hashlib.sha256(typed).hexdigest() == GAMES_PASSWORD_SHA256:
                answer["ok"] = True
                window.destroy()
            else:
                note.config(text="Wrong password")
                entry.delete(0, "end")

        row = self.tk.Frame(window, bg=BG)
        row.pack(pady=(6, 14))
        self.button(row, "Unlock", check, COLORS[ANY], ACTIVE[ANY], 13,
                    padx=16, pady=6).pack(side="left", padx=8)
        self.button(row, "Cancel", window.destroy, size=13, padx=16, pady=6).pack(side="left", padx=8)
        entry.bind("<Return>", check)
        self.place(window)
        entry.focus_set()
        self.root.wait_window(window)
        return answer["ok"]

    def show_games(self):
        tk = self.tk
        self.clear()
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=12, pady=(8, 2))
        title = self.label(header, "Games", color="white")
        title.configure(font=self.title_font)
        title.pack(side="left")
        self.button(header, "Back to lessons", self.show_lessons, size=10, padx=10,
                    pady=4).pack(side="right", padx=4)
        if self.games_locked:
            self.button(header, "Lock games", self.lock_games, "#7B3FC4", "#8E52D8", 10, padx=10,
                        pady=4).pack(side="right", padx=4)
        self.buzzer_switch(header).pack(side="right", padx=(0, 18))
        self.label(self.root, "Click a game: it opens in its own window. "
                              "Click Close in the game when you're done.").pack(anchor="w", padx=14)
        grid = tk.Frame(self.root, bg=BG)
        grid.pack(fill="both", expand=True, padx=10, pady=8)
        columns = 3
        for i, game in enumerate(GAMES):
            self.lesson_button(grid, game).grid(row=i // columns, column=i % columns,
                                                padx=self.pad * 2, pady=self.pad * 2, sticky="nsew")
        for c in range(columns):
            grid.columnconfigure(c, weight=1, uniform="games")
        for r in range((len(GAMES) + columns - 1) // columns):
            grid.rowconfigure(r, weight=1, uniform="games")
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=12, pady=(4, 8))
        self.status = self.label(footer, "", 10, True, "#FFD866", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.say("Games are unlocked until you click 'Lock games' or close the app." if self.games_locked
                 else "Games are open on this kit (no password - set by the installer).")

    def shut_down(self):
        from tkinter import messagebox
        if not messagebox.askyesno("Shut down", "Shut down the CrowPi now?\nWait until the green light "
                                   "stops blinking before you switch it off.", parent=self.root):
            return
        self.say("Shutting down...")
        self.root.update()
        try:
            if subprocess.call(["systemctl", "poweroff"], timeout=20) != 0:
                subprocess.call(["sudo", "-n", "shutdown", "-h", "now"], timeout=20)
        except (OSError, subprocess.SubprocessError):
            self.say("Couldn't shut down - use the Raspberry menu > Shutdown.")

    def lock_games(self):
        self.games_unlocked = False
        self.show_lessons()
        self.say("Games locked.")

    # ---------- screen 2: lessons not found ----------
    def show_finder(self):
        tk = self.tk
        self.clear()
        self.status = None
        box = tk.Frame(self.root, bg=BG)
        box.pack(fill="both", expand=True, padx=28, pady=20)
        title = self.label(box, "Where are the lessons?", color="white")
        title.configure(font=self.title_font)
        title.pack(anchor="w")
        if in_archive_temp(HERE):
            why = ("You opened this straight from inside the zip file. The archive program\n"
                   "only took this one file out, into a temporary folder:\n    " + HERE + "\n"
                   "so the lesson files aren't next to it.")
        else:
            why = "The lesson files aren't in this folder:\n    " + HERE
        self.label(box, why, 11, justify="left").pack(anchor="w", pady=(10, 8))
        self.label(box, "Fix: right-click CrowPi_Club.zip > Extract Here. Then open the\n"
                        "CrowPi_Club folder and double-click CrowPi Lessons.",
                   11, True, "#FFD866", justify="left").pack(anchor="w", pady=(0, 14))
        zips = find_zip_files()
        if zips:
            self.button(box, "Unzip " + os.path.basename(zips[0]) + " for me",
                        lambda: self.unzip(zips[0]), COLORS[ANY], ACTIVE[ANY], 12,
                        padx=14, pady=8).pack(anchor="w", pady=4)
            self.label(box, "Found: " + zips[0], 9).pack(anchor="w")
        self.button(box, "Choose the CrowPi_Club folder...", self.choose_folder, COLORS[MOTOR],
                    ACTIVE[MOTOR], 12, padx=14, pady=8).pack(anchor="w", pady=(12, 4))
        self.button(box, "Close", self.root.destroy, size=12, padx=14,
                    pady=8).pack(anchor="w", pady=4)

    def unzip(self, zip_path):
        try:
            folder = unzip_lessons(zip_path)
        except (OSError, ValueError, zipfile.BadZipFile) as problem:
            self.message("Couldn't unzip", str(problem))
            return
        self.folder = folder
        self.install()

    def choose_folder(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(parent=self.root, initialdir=DESKTOP,
                                         title="Choose the CrowPi_Club folder")
        if not folder:
            return
        if not has_lessons(folder) and has_lessons(os.path.join(folder, "CrowPi_Club")):
            folder = os.path.join(folder, "CrowPi_Club")
        if has_lessons(folder):
            self.folder = os.path.realpath(folder)
            save_config(lesson_folder=self.folder)
            self.show_lessons()
        else:
            self.message("Not that one", "That folder doesn't have the CrowPi lessons in it.\n"
                                         "Pick the CrowPi_Club folder (it has buzzer.py inside).")

    # ---------- small windows ----------
    def popup(self, title):
        window = self.tk.Toplevel(self.root, bg=BG)
        window.title(title)
        window.transient(self.root)
        return window

    def place(self, window):
        window.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_height()) // 3
        window.geometry("+%d+%d" % (max(0, x), max(0, y)))
        try:
            window.grab_set()
        except self.tk.TclError:
            pass

    def message(self, title, text):
        window = self.popup(title)
        self.label(window, title, 16, True, "white").pack(padx=20, pady=(14, 6))
        self.label(window, text, 12, justify="left").pack(padx=20, pady=4)
        self.button(window, "OK", window.destroy, COLORS[ANY], ACTIVE[ANY], 13,
                    padx=20, pady=6).pack(pady=(10, 14))
        self.place(window)
        self.root.wait_window(window)

    def ready_check(self, title, mode, parts):
        """A big 'are you ready?' window. Returns True if the student clicks Start."""
        window = self.popup("Get ready")
        self.label(window, title, 16, True, "white").pack(padx=20, pady=(14, 6))
        if mode != ANY:
            self.label(window, "Switches: " + MODE_TEXT[mode], 12, True, "white", bg=COLORS[mode],
                       justify="left", padx=12, pady=8).pack(padx=20, pady=4, fill="x")
        if parts:
            self.label(window, "Plug in: " + parts, 12, True, BG, bg="#FFD866", wraplength=420,
                       justify="left", padx=12, pady=8).pack(padx=20, pady=4, fill="x")
        answer = {"go": False}

        def go():
            answer["go"] = True
            window.destroy()

        row = self.tk.Frame(window, bg=BG)
        row.pack(pady=(10, 14))
        self.button(row, "Ready - Start!", go, COLORS[ANY], ACTIVE[ANY], 13,
                    padx=16, pady=6).pack(side="left", padx=8)
        self.button(row, "Cancel", window.destroy, size=13, padx=16, pady=6).pack(side="left", padx=8)
        self.place(window)
        self.root.wait_window(window)
        return answer["go"]


def main():
    if "--help" in sys.argv:
        print("CrowPi Lessons. Options: --install (install from the lesson folder), "
              "--lock-games / --no-lock-games (games behind the admin password or not), "
              "--find (print the lesson folder)")
        return 0
    folder = find_lesson_folder()
    if "--find" in sys.argv:
        print(folder or "not found")
        return 0 if folder else 1
    if "--lock-games" in sys.argv or "--no-lock-games" in sys.argv:
        save_config(games_locked="--lock-games" in sys.argv)      # (the installer asks)
    if "--install" in sys.argv:
        if not folder:
            print("Can't find the lesson files. Extract CrowPi_Club.zip first "
                  "(right-click > Extract Here), then run this from the CrowPi_Club folder.")
            return 1
        install_kit(folder)
        return 0

    # Started from a newer folder (USB stick, extracted zip) or a newer apt package?
    # Install it by itself, then restart as the installed copy in the home folder.
    if folder and not os.environ.get("CROWPI_RESTARTED"):
        source = newest_source(folder)
        try:
            if source:
                install_kit(source, log=lambda text: None)
                run_installed_copy("--just-installed")
            elif (has_lessons(INSTALL_DIR)
                  and os.path.realpath(folder) != os.path.realpath(INSTALL_DIR)):
                run_installed_copy()                  # same or older version: use the installed one
        except OSError as problem:
            print("Couldn't install:", problem)

    raise_event = threading.Event()
    instance = claim_single_instance(raise_event)
    if instance is None:
        return 0                                  # already open - it comes to the front
    import tkinter as tk
    root = tk.Tk(className=WINDOW_CLASS)
    if has_lessons(INSTALL_DIR) and os.environ.get("CROWPI_RESTARTED"):
        folder = INSTALL_DIR
    if games_locked() and has_lessons(INSTALL_DIR):              # (also locks games installed later)
        threading.Thread(target=lock_installed_games, args=(True, INSTALL_DIR, lambda _t: None),
                         daemon=True).start()
    app = LessonsApp(root, folder, "--just-installed" in sys.argv)
    app.raise_event = raise_event
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
