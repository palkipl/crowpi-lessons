#!/usr/bin/env python3
# ==================================================================
#  CrowPi Club - GAME LOCK
#
#  When the installer locks the games, the games that came on the kit
#  (Minecraft, Doom and the other games in the Raspberry menu) open
#  through this little window first: it asks for the admin password.
#  Right password = the game starts. Wrong or Cancel = nothing happens.
#
#  The installer (install_crowpi.py) turns the lock on and off.
# ==================================================================
import hashlib
import os
import sys

# Only the fingerprint (SHA-256) of the admin password is kept here.
PASSWORD_SHA256 = "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"


def ask(name):
    """True if the admin password was typed."""
    import tkinter as tk
    root = tk.Tk(className="CrowPiGameLock")
    root.title("Game locked")
    root.configure(bg="#1B2230")
    root.resizable(False, False)
    answer = {"ok": False}
    tk.Label(root, text="%s is locked" % name, bg="#1B2230", fg="white",
             font=("DejaVu Sans", 16, "bold")).pack(padx=28, pady=(16, 4))
    tk.Label(root, text="Type the admin password to play:", bg="#1B2230", fg="#C9D3E3",
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

    row = tk.Frame(root, bg="#1B2230")
    row.pack(pady=(6, 16))
    for text, command, color in (("Play", check, "#2E9E5B"), ("Cancel", root.destroy, "#313C54")):
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
    name = "This game"
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
        os.execvp(args[0], args)                     # start the game (this program becomes the game)
    except OSError as problem:
        print("Couldn't start %s: %s" % (name, problem))
    return 1


if __name__ == "__main__":
    sys.exit(main())
