#!/usr/bin/env python3
# ==================================================================
#  CrowPi Club - APP LOCK
#
#  When the installer locks the apps, every app in the Raspberry menu
#  (browser, terminal, files, settings, Minecraft, ...) opens through
#  this little window first: it asks for the admin password.
#  Right password = the app starts. Wrong or Cancel = nothing happens.
#  CrowPi Lessons and Shutdown are never locked.
#
#  The installer (install_crowpi.py) turns the lock on and off.
# ==================================================================
import hashlib
import os
import sys

# Only the fingerprint (SHA-256) of the admin password is kept here.
PASSWORD_SHA256 = "fcd4e25bb2a919b986574a4432b396deccf6f119f1919efb4ce6e86e1227e9ac"


def ask(name):
    """True if the admin password was typed."""
    import tkinter as tk
    root = tk.Tk(className="CrowPiGameLock")
    root.title("Locked")
    root.configure(bg="#1B2230")
    root.resizable(False, False)
    answer = {"ok": False}
    tk.Label(root, text="%s is locked" % name, bg="#1B2230", fg="white",
             font=("DejaVu Sans", 16, "bold")).pack(padx=28, pady=(16, 4))
    tk.Label(root, text="Ask your teacher - type the admin password to open it:", bg="#1B2230", fg="#C9D3E3",
             font=("DejaVu Sans", 12)).pack(padx=28)
    entry = tk.Entry(root, show="*", font=("DejaVu Sans", 14), width=16)
    entry.pack(padx=28, pady=8)
    note = tk.Label(root, text="", bg="#1B2230", fg="#FF8A80", font=("DejaVu Sans", 11, "bold"))
    note.pack()

    def check(_event=None):
        if hashlib.sha256(entry.get().encode("utf-8")).hexdigest() == PASSWORD_SHA256:
            answer["ok"] = True
            root.destroy()
        else:
            note.config(text="Wrong password")
            entry.delete(0, "end")

    keypad = tk.Frame(root, bg="#1B2230")          # tap the numbers (touchscreen)
    keypad.pack()
    for i, key in enumerate(["1", "2", "3", "4", "5", "6", "7", "8", "9", "\u232B", "0", "C"]):
        def press(key=key):
            if key == "\u232B":
                entry.delete(len(entry.get()) - 1, "end")
            elif key == "C":
                entry.delete(0, "end")
            else:
                entry.insert("end", key)
        tk.Button(keypad, text=key, command=press, bg="#2A3347", fg="white", activebackground="#3A4560",
                  activeforeground="white", relief="flat", font=("DejaVu Sans", 14, "bold"), width=3,
                  cursor="hand2").grid(row=i // 3, column=i % 3, padx=3, pady=3)

    row = tk.Frame(root, bg="#1B2230")
    row.pack(pady=(6, 16))
    for text, command, color in (("Open", check, "#2E9E5B"), ("Cancel", root.destroy, "#313C54")):
        tk.Button(row, text=text, command=command, bg=color, fg="white", activebackground=color,
                  activeforeground="white", relief="flat", font=("DejaVu Sans", 12, "bold"), padx=16,
                  pady=5, cursor="hand2").pack(side="left", padx=8)
    entry.bind("<Return>", check)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry("+%d+%d" % (max(0, x), max(0, y)))
    entry.focus_force()
    root.mainloop()
    return answer["ok"]


def main():
    args = sys.argv[1:]
    name = "This app"
    if len(args) >= 2 and args[0] == "--name":
        name, args = args[1], args[2:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("Usage: game_lock.py [--name NAME] -- COMMAND ...")
        return 1
    if not ask(name):
        return 0
    try:
        os.execvp(args[0], args)                     # start the app (this program becomes the app)
    except OSError as problem:
        print("Couldn't start %s: %s" % (name, problem))
    return 1


if __name__ == "__main__":
    sys.exit(main())
