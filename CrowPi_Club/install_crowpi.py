#!/usr/bin/env python3
# ==================================================================
#  CrowPi Club - INSTALLER  (for the teacher / school IT admin)
#
#  Adds or removes the CrowPi Lessons app on one Raspberry Pi kit.
#  No terminal needed: open this file in Thonny and click Run
#  (or right-click > Open With > Python 3).
#
#  ADD     downloads the lessons from GitHub (or uses a
#          CrowPi_Club.zip next to this file / in Downloads / on a USB
#          stick), installs the lessons, makes CrowPi Lessons the only
#          icon on the Desktop and opens it at every start-up.
#          LOCK GAMES = the games that came on the kit (Minecraft, Doom and
#          the rest of the Games menu) and the club games ask for the admin
#          password before they start.
#  REMOVE  takes everything away again and puts the Desktop back.
#          Students' own programs and photos are kept.
#
#  Many kits at once (over SSH), no window:
#      python3 install_crowpi.py --add --lock-games
#      python3 install_crowpi.py --add --no-lock-games --url https://.../CrowPi_Club.zip
#  or straight from GitHub on a fresh kit (one line):
#      curl -fsSL https://raw.githubusercontent.com/palkipl/crowpi-lessons/main/install_crowpi.py \
#           -o ~/install_crowpi.py && python3 ~/install_crowpi.py --add --lock-games
#      python3 install_crowpi.py --remove
# ==================================================================
import glob
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile

# Where the lessons come from: the GitHub repository (it has to be public).
# The link can also be changed in the installer window, or with --url.
GITHUB_REPO = "palkipl/crowpi-lessons"
DOWNLOAD_URL = "https://github.com/%s/archive/refs/heads/main.zip" % GITHUB_REPO

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.join(HOME, "Desktop")
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config"))
DATA_HOME = os.environ.get("XDG_DATA_HOME", os.path.join(HOME, ".local", "share"))
INSTALL_DIR = os.path.join(HOME, "CrowPi_Club")
APP_ID = "crowpi-lessons"
OLD_DESKTOP = os.path.join(HOME, "Old_Desktop_Files")
SYSTEM_DIR = "/usr/share/crowpi-lessons"          # the .deb package, if it was used
KEEP = [os.path.join(HOME, "My_CrowPi_Programs"), os.path.join(HOME, "Pictures", "CrowPi_Photos")]

BG, PANEL, TEXT, WHITE = "#1B2230", "#262F42", "#C9D3E3", "#FFFFFF"
GREEN, RED, BLUE, YELLOW = "#2E9E5B", "#D64545", "#2F6FD0", "#FFD866"


# ==================================================================
#  Getting the lessons
# ==================================================================
def download(url, log, progress):
    """Download the zip to a temporary file. Returns its path."""
    log("Downloading " + url)
    target = os.path.join(tempfile.mkdtemp(prefix="crowpi_"), "CrowPi_Club.zip")
    request = urllib.request.Request(url, headers={"User-Agent": "CrowPi-Installer"})
    with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total:
                progress(done / total)
    log("Downloaded %d KB" % (done // 1024))
    return target


def local_zips():
    """CrowPi_Club zip files next to this installer, in Downloads or on a USB stick."""
    places = [HERE, os.path.join(HOME, "Downloads"), HOME, DESKTOP, OLD_DESKTOP]
    places += glob.glob("/media/*/*") + glob.glob("/media/*/*/*")
    found = []
    for place in places:
        for path in glob.glob(os.path.join(place, "CrowPi_Club*.zip")):
            if path not in found:
                found.append(path)
    return sorted(found, key=os.path.getmtime, reverse=True)


def unpack(zip_path, log):
    """Check the zip and unpack only the lesson files. Returns the folder with lessons_app.py."""
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError("That download isn't a zip file - check the website link.")
    folder = tempfile.mkdtemp(prefix="crowpi_files_")
    with zipfile.ZipFile(zip_path) as archive:
        apps = [n for n in archive.namelist() if n.endswith("lessons_app.py")]
        if not apps:
            raise RuntimeError("That zip file has no CrowPi lessons in it.")
        prefix = sorted(apps, key=len)[0][:-len("lessons_app.py")]
        count = 0
        for name in archive.namelist():
            relative = name[len(prefix):] if name.startswith(prefix) else ""
            if (not relative or "/" in relative or "\\" in relative or relative.startswith(".")
                    or name.endswith("/")):
                continue                              # only the lesson files themselves
            with archive.open(name) as source, open(os.path.join(folder, relative), "wb") as out:
                shutil.copyfileobj(source, out)
            count += 1
    log("Unpacked %d lesson files" % count)
    return folder


def version_of(folder):
    try:
        with open(os.path.join(folder, "lessons_app.py"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("APP_VERSION"):
                    return line.split('"')[1]
    except (OSError, IndexError):
        pass
    return "?"


# ==================================================================
#  Running programs
# ==================================================================
def stop_lessons_app(log):
    """Close CrowPi Lessons if it's open, so the new version can start fresh."""
    me = os.getpid()
    for proc in glob.glob("/proc/[0-9]*"):
        try:
            pid = int(os.path.basename(proc))
            with open(os.path.join(proc, "cmdline"), "rb") as f:
                command = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except (OSError, ValueError):
            continue
        if pid != me and "lessons_app.py" in command and "install_crowpi" not in command:
            try:
                os.kill(pid, signal.SIGTERM)
                log("Closed the CrowPi Lessons app that was open")
            except OSError:
                pass


def start_lessons_app():
    app = os.path.join(INSTALL_DIR, "lessons_app.py")
    if os.path.exists(app) and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        subprocess.Popen([sys.executable, app, "--just-installed"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, cwd=HOME)


# ==================================================================
#  ADD
# ==================================================================
def add(url, lock_games, log, progress=lambda _f: None):
    zip_path = None
    links = [url] if url else []
    if url.startswith("https://"):
        links.append("http://" + url[len("https://"):])      # (in case the website has no https)
    for link in links:
        try:
            zip_path = download(link, log, progress)
            break
        except Exception as problem:
            log("The download didn't work: %s" % problem)
    if zip_path is None:
        zips = local_zips()
        if not zips:
            raise RuntimeError("No lessons to install. Check the website link, or put CrowPi_Club.zip "
                               "next to this installer (or on a USB stick) and try again.")
        zip_path = zips[0]
        log("Using " + zip_path)
    progress(1.0)
    files = unpack(zip_path, log)
    log("Installing version %s ..." % version_of(files))
    stop_lessons_app(log)
    flag = "--lock-games" if lock_games else "--no-lock-games"
    result = subprocess.run([sys.executable, os.path.join(files, "lessons_app.py"), "--install", flag, files],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                            cwd=HOME, env=dict(os.environ, CROWPI_RESTARTED="1"))
    for line in result.stdout.splitlines():
        log("  " + line)
    shutil.rmtree(files, ignore_errors=True)
    if result.returncode != 0 or not os.path.exists(os.path.join(INSTALL_DIR, "lessons_app.py")):
        raise RuntimeError("The install didn't finish - see the messages above.")
    log("Games: " + ("LOCKED with the admin password (installed games and club games)" if lock_games
                     else "open (no password)"))
    log("DONE! CrowPi Lessons %s is installed." % version_of(INSTALL_DIR))
    log("Close Thonny once (File > Exit) so lessons can start by themselves.")
    start_lessons_app()


# ==================================================================
#  REMOVE
# ==================================================================
def remove_path(path, log):
    if os.path.isdir(path) and not os.path.islink(path):
        for folder, _dirs, files in os.walk(path):
            try:
                os.chmod(folder, 0o700)
                for name in files:
                    os.chmod(os.path.join(folder, name), 0o600)
            except OSError:
                pass
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        os.remove(path)
    else:
        return
    log("Removed " + path.replace(HOME, "~"))


def show_desktop_icons():
    """Put the Rubbish Bin, drives and Documents icons back on the Desktop."""
    for path in glob.glob(os.path.join(CONFIG_HOME, "pcmanfm", "*", "desktop-items-*.conf")):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for key in ("show_trash", "show_mounts", "show_documents"):
                text = text.replace(key + "=0", key + "=1")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
    if shutil.which("pcmanfm"):
        subprocess.Popen(["pcmanfm", "--reconfigure"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


def remove(log, progress=lambda _f: None):
    stop_lessons_app(log)
    paths = [INSTALL_DIR, os.path.join(DESKTOP, "CrowPi_Club"),
             os.path.join(DESKTOP, "CrowPi_Lessons.desktop"),
             os.path.join(DATA_HOME, "applications", APP_ID + ".desktop"),
             os.path.join(CONFIG_HOME, "autostart", APP_ID + ".desktop"),
             os.path.join(DATA_HOME, "icons", "hicolor", "128x128", "apps", APP_ID + ".png"),
             os.path.join(CONFIG_HOME, APP_ID + ".json")]
    thonny = os.environ.get("THONNY_USER_DIR") or os.path.join(CONFIG_HOME, "Thonny")
    paths += glob.glob(os.path.join(thonny, "plugins", "lib", "*", "site-packages", "thonnycontrib",
                                    "crowpi_autorun"))
    paths += [p for p in glob.glob(os.path.join(HOME, ".crowpi_*")) if os.path.isfile(p)]
    for entry in glob.glob(os.path.join(DATA_HOME, "applications", "*.desktop")):   # the game locks
        try:
            with open(entry, encoding="utf-8", errors="replace") as f:
                if "X-CrowPi-Locked=true" in f.read():
                    paths.append(entry)
        except OSError:
            pass
    for i, path in enumerate(paths):
        remove_path(path, log)
        progress((i + 1) / len(paths))
    if os.path.isdir(OLD_DESKTOP):                     # put the old Desktop back
        os.makedirs(DESKTOP, exist_ok=True)
        for name in sorted(os.listdir(OLD_DESKTOP)):
            target = os.path.join(DESKTOP, name)
            while os.path.exists(target):
                stem, extension = os.path.splitext(target)
                target = stem + "_restored" + extension
            shutil.move(os.path.join(OLD_DESKTOP, name), target)
        try:
            os.rmdir(OLD_DESKTOP)
        except OSError:
            pass
        log("Put the old Desktop files back")
    show_desktop_icons()
    if os.path.isdir(SYSTEM_DIR):
        log("Removing the crowpi-lessons package (it asks for the admin password)...")
        command = ["pkexec", "apt-get", "remove", "-y", APP_ID] if shutil.which("pkexec") else \
            ["sudo", "-n", "apt-get", "remove", "-y", APP_ID]
        try:
            ok = subprocess.call(command) == 0
        except OSError:
            ok = False
        if not ok:
            log("Couldn't remove the package. In a terminal type:  sudo apt remove crowpi-lessons")
    kept = [p.replace(HOME, "~") for p in KEEP if os.path.isdir(p)]
    if kept:
        log("Kept the students' work: " + ", ".join(kept) + " (delete them yourself if you want)")
    log("DONE! CrowPi Lessons is removed from this kit.")


# ==================================================================
#  THE WINDOW
# ==================================================================
class Installer:
    def __init__(self, root):
        import tkinter as tk
        self.tk, self.root = tk, root
        root.title("CrowPi Lessons - Installer")
        root.configure(bg=BG)
        root.geometry("720x560")
        root.minsize(600, 480)
        def font(size, bold=True):
            return ("DejaVu Sans", size, "bold" if bold else "normal")
        tk.Label(root, text="CrowPi Lessons - Installer", bg=BG, fg=WHITE, font=font(20)).pack(
            anchor="w", padx=18, pady=(14, 2))
        tk.Label(root, text="For the teacher or school IT. Students never need this.", bg=BG, fg=TEXT,
                 font=font(11, False)).pack(anchor="w", padx=18)
        box = tk.Frame(root, bg=PANEL, padx=14, pady=10)
        box.pack(fill="x", padx=18, pady=10)
        self.mode = tk.StringVar(value="add")
        tk.Label(box, text="1.  What do you want to do?", bg=PANEL, fg=YELLOW, font=font(12)).grid(
            row=0, column=0, sticky="w", columnspan=2)
        for i, (value, text) in enumerate((("add", "ADD (install or update) CrowPi Lessons on this kit"),
                                           ("remove", "REMOVE CrowPi Lessons from this kit"))):
            tk.Radiobutton(box, text=text, value=value, variable=self.mode, command=self.changed, bg=PANEL,
                           fg=WHITE, selectcolor=BG, activebackground=PANEL, activeforeground=WHITE,
                           font=font(12, False)).grid(row=1 + i, column=0, sticky="w", columnspan=2, padx=12)
        self.lock = tk.StringVar(value="yes")
        self.lock_label = tk.Label(box, text="2.  Lock the games with the admin password?  (Minecraft, Doom ...)",
                                   bg=PANEL, fg=YELLOW, font=font(12))
        self.lock_label.grid(row=3, column=0, sticky="w", columnspan=2, pady=(10, 0))
        self.lock_buttons = []
        for i, (value, text) in enumerate((("yes", "Yes - Minecraft, Doom and the club games ask for the password"),
                                           ("no", "No - games are open to everyone"))):
            b = tk.Radiobutton(box, text=text, value=value, variable=self.lock, bg=PANEL, fg=WHITE,
                               selectcolor=BG, activebackground=PANEL, activeforeground=WHITE,
                               font=font(12, False))
            b.grid(row=4 + i, column=0, sticky="w", columnspan=2, padx=12)
            self.lock_buttons.append(b)
        self.url_label = tk.Label(box, text="3.  Download from (website link):", bg=PANEL, fg=YELLOW,
                                  font=font(12))
        self.url_label.grid(row=6, column=0, sticky="w", columnspan=2, pady=(10, 2))
        self.url = tk.Entry(box, font=font(11, False), width=56)
        self.url.insert(0, DOWNLOAD_URL)
        self.url.grid(row=7, column=0, sticky="we", padx=12)
        self.url_note = tk.Label(box, text="(empty or offline: uses a CrowPi_Club.zip next to this file, "
                                           "in Downloads or on a USB stick)", bg=PANEL, fg=TEXT,
                                 font=font(9, False))
        self.url_note.grid(row=8, column=0, sticky="w", padx=12)
        row = tk.Frame(root, bg=BG)
        row.pack(fill="x", padx=18)
        self.go = tk.Button(row, text="Start", command=self.start, bg=GREEN, fg=WHITE, activebackground=GREEN,
                            relief="flat", font=font(14), padx=20, pady=6, cursor="hand2")
        self.go.pack(side="left")
        self.bar = tk.Canvas(row, height=16, bg=PANEL, highlightthickness=0)
        self.bar.pack(side="left", fill="x", expand=True, padx=12)
        self.output = tk.Text(root, height=10, bg="#0E121A", fg="#9CDB7A", font=("DejaVu Sans Mono", 10),
                              relief="flat", state="disabled")
        self.output.pack(fill="both", expand=True, padx=18, pady=(10, 14))
        self.lines: list = []
        self.done = None
        self.fraction = 0.0
        self.root.after(100, self.poll)

    def changed(self):
        adding = self.mode.get() == "add"
        for button in self.lock_buttons:
            button.config(state="normal" if adding else "disabled")
        self.url.config(state="normal" if adding else "disabled")
        self.go.config(text="Start" if adding else "Remove", bg=GREEN if adding else RED)

    def log(self, line):
        self.lines.append(line)
        print(line)

    def progress(self, fraction):
        self.fraction = fraction

    def start(self):
        from tkinter import messagebox
        mode = self.mode.get()
        if mode == "remove" and not messagebox.askyesno(
                "Remove CrowPi Lessons", "Remove CrowPi Lessons and all the lesson files from this kit?\n"
                "(Students' programs and photos are kept.)", parent=self.root):
            return
        self.go.config(state="disabled")
        self.fraction = 0.0
        url, lock = self.url.get().strip(), self.lock.get() == "yes"

        def work():
            try:
                if mode == "add":
                    add(url, lock, self.log, self.progress)
                else:
                    remove(self.log, self.progress)
                self.done = True
            except Exception as problem:
                self.log("PROBLEM: %s" % problem)
                self.done = False
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if self.lines:
            self.output.config(state="normal")
            while self.lines:
                self.output.insert("end", self.lines.pop(0) + "\n")
            self.output.see("end")
            self.output.config(state="disabled")
        self.bar.delete("all")
        width = self.bar.winfo_width()
        self.bar.create_rectangle(0, 0, width * getattr(self, "fraction", 0), 16, fill=GREEN, outline="")
        if self.done is not None:
            from tkinter import messagebox
            ok, self.done = self.done, None
            self.go.config(state="normal")
            if ok:
                messagebox.showinfo("CrowPi Lessons", "All done!", parent=self.root)
            else:
                messagebox.showerror("CrowPi Lessons", "That didn't work - see the messages.", parent=self.root)
        self.root.after(100, self.poll)


def main():
    args = sys.argv[1:]
    if "--add" in args or "--remove" in args:              # no window (e.g. over SSH)
        try:
            if "--remove" in args:
                remove(print)
            else:
                url = args[args.index("--url") + 1] if "--url" in args else DOWNLOAD_URL
                add(url, "--no-lock-games" not in args, print)
            return 0
        except Exception as problem:
            print("PROBLEM:", problem)
            return 1
    import tkinter as tk
    root = tk.Tk(className="CrowPiInstaller")
    Installer(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
