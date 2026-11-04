# CrowPi Lessons

22 days of hands-on lessons for the **Raspberry Pi 5 CrowPi kit** (board v3.2): sensors, screens, RFID cards, IR remote, camera, a button piano, games and a finale show.
Every lesson opens as one app with **Learn · Try it · Code · Challenge** tabs. Students never type code: on the Code tab they only change a few highlighted values.

## Install on a kit - no terminal

Download **[CrowPi_Setup.py](https://raw.githubusercontent.com/palkipl/crowpi-lessons/main/CrowPi_Setup.py)** (right-click > Save link as), open it in Thonny and click **Run**.
A window asks: **ADD or REMOVE**, **lock the games** (Minecraft, Doom and the club games) with the admin password, and **kiosk mode** (only CrowPi Lessons on the screen). Click Start, then restart the kit.

## Install on a kit (one line in the terminal)

Open the terminal on the CrowPi and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/palkipl/crowpi-lessons/main/install_crowpi.py -o ~/install_crowpi.py && python3 ~/install_crowpi.py --add --lock-games
```

* `--lock-games` - the installed games (Minecraft, Doom, ...) and the club games ask for the admin password.
  Use `--no-lock-games` to leave them open.
* Remove everything again: `python3 ~/install_crowpi.py --remove`
* Prefer a window? Run `python3 ~/install_crowpi.py` (or open it in Thonny and click Run).

After installing, close Thonny once (File > Exit) so lessons start by themselves.

## Kiosk mode (on by default)

After a restart the kit shows **only CrowPi Lessons**, filling the screen - the top bar stays (clock, Wi-Fi, volume) but without the Raspberry menu; no desktop icons. Lessons open full screen from the app and **Close lesson** brings the app back.
The small **Teacher** button (admin password) has Kit Check, the folders, a terminal, *Normal desktop until restart* and *Kiosk mode OFF*.
Install without kiosk mode: add `--no-kiosk`.

## What it changes on the kit

* Lessons in `~/CrowPi_Club` (read-only), students' work in `~/My_CrowPi_Programs`, photos in `~/Pictures/CrowPi_Photos`.
* The Desktop shows only **CrowPi Lessons**; everything that was there moves to `~/Old_Desktop_Files` (nothing is deleted, and Remove puts it back).
* CrowPi Lessons opens at start-up and is in Raspberry menu > Education.
* No accounts, no tracking, nothing is sent anywhere. After installing, no internet is needed.

## Offline

Download this repository as a zip (green **Code** button > Download ZIP), put it on a USB stick next to `install_crowpi.py`, and run:

```bash
python3 /media/$USER/*/install_crowpi.py --add --lock-games
```

## Files

* `CrowPi_Setup.py` - double-click setup: always downloads the newest installer
* `install_crowpi.py` - the installer (add / remove / lock games)
* `CrowPi_Club/` - the lessons and the CrowPi Lessons app (see `CrowPi_Club/TEACHER_GUIDE.txt`)
* `docs/CrowPi_Club_Plan.pptx` - the club slides, one section per day
