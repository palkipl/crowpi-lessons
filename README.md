# CrowPi Lessons

22 days of hands-on lessons for the **Raspberry Pi 5 CrowPi kit** (board v3.2): sensors, screens, RFID cards, IR remote, camera, a button piano, games and a finale show.
Every lesson opens as one app with **Learn · Try it · Code · Challenge** tabs. Students never type code: on the Code tab they only change a few highlighted values.

## Install on a kit (one line)

Open the terminal on the CrowPi and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/palkipl/crowpi-lessons/main/install_crowpi.py -o ~/install_crowpi.py && python3 ~/install_crowpi.py --add --lock-games
```

* `--lock-games` - the installed games (Minecraft, Doom, ...) and the club games ask for the admin password.
  Use `--no-lock-games` to leave them open.
* Remove everything again: `python3 ~/install_crowpi.py --remove`
* Prefer a window? Run `python3 ~/install_crowpi.py` (or open it in Thonny and click Run).

After installing, close Thonny once (File > Exit) so lessons start by themselves.

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

* `install_crowpi.py` - the installer (add / remove / lock games)
* `CrowPi_Club/` - the lessons and the CrowPi Lessons app (see `CrowPi_Club/TEACHER_GUIDE.txt`)
* `docs/CrowPi_Club_Plan.pptx` - the club slides, one section per day
