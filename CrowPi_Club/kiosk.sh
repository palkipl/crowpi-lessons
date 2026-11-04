#!/bin/bash
# ==================================================================
#  CrowPi Club - KIOSK MODE
#  The desktop session starts this instead of the normal desktop:
#  only CrowPi Lessons is on the screen (no taskbar, no desktop icons).
#  If the app is closed it opens again. The Teacher menu in the app
#  (admin password) can bring the normal desktop back.
#  Turn kiosk mode off: Teacher menu > Kiosk mode OFF, or run
#  install_crowpi.py again and choose No for kiosk mode.
# ==================================================================
APP="$(dirname "$(readlink -f "$0")")/lessons_app.py"
PAUSE="$HOME/.crowpi_kiosk_pause"
rm -f "$PAUSE"
cd "$HOME" || exit 1
command -v xset >/dev/null && [ -n "$DISPLAY" ] && { xset s off; xset -dpms; } 2>/dev/null

starts=0
since=$(date +%s)
while [ ! -f "$PAUSE" ]; do
    python3 "$APP" --kiosk-session
    now=$(date +%s)
    if [ $((now - since)) -lt 60 ]; then
        starts=$((starts + 1))
    else
        starts=1
        since=$now
    fi
    if [ "$starts" -ge 5 ]; then             # it keeps stopping: give the normal desktop back
        echo "CrowPi Lessons stopped 5 times in a minute - showing the normal desktop" >&2
        break
    fi
    sleep 1
done
rm -f "$PAUSE"

# the normal desktop for the rest of this session (until the next restart)
if [ -n "$WAYLAND_DISPLAY" ] && command -v wf-panel-pi >/dev/null; then
    wf-panel-pi &
elif command -v lxpanel >/dev/null; then
    lxpanel --profile LXDE-pi &
fi
command -v pcmanfm >/dev/null && pcmanfm --desktop --profile LXDE-pi &
wait
