# ==================================================================
#  CrowPi Club - Thonny helper (the CrowPi Lessons app installs this into Thonny)
#
#  When you click a lesson in the CrowPi Lessons app, the app leaves a
#  small note (the file ~/.crowpi_run_request). This helper, running
#  inside Thonny, sees the note, opens that lesson and presses Run.
#  If anything goes wrong it does nothing - you can still press Run.
# ==================================================================
import json
import os
import time

REQUEST = os.path.expanduser("~/.crowpi_run_request")


def _workbench():
    from thonny import get_workbench
    return get_workbench()


def _check_for_request():
    workbench = _workbench()
    try:
        if os.path.exists(REQUEST):
            with open(REQUEST, encoding="utf-8") as f:
                request = json.load(f)
            os.remove(REQUEST)
            path = os.path.realpath(request.get("file", ""))
            fresh = time.time() - float(request.get("time", 0)) < 60
            if fresh and os.path.isfile(path):
                workbench.get_editor_notebook().show_file(path)
                workbench.after(500, lambda: _run_when_ready(path, 0))
    except Exception:
        pass                                  # never get in Thonny's way
    workbench.after(400, _check_for_request)


def _run_when_ready(path, tries):
    workbench = _workbench()
    try:
        from thonny import get_runner
        runner = get_runner()
        editor = workbench.get_editor_notebook().get_current_editor()
        name = (editor.get_filename() if editor is not None else None) or ""
        showing = bool(name) and os.path.realpath(name) == path
        if runner is not None and showing:
            state = runner.get_state()
            if state == "waiting_toplevel_command":
                runner.cmd_run_current_script()          # press Run
                return
            if state in ("running", "waiting_debugger_command") and tries == 0:
                runner.cmd_stop_restart()                # stop the old program first
        elif not showing:
            workbench.get_editor_notebook().show_file(path)
    except Exception:
        return
    if tries < 80:                                       # keep trying for about 20 seconds
        workbench.after(250, lambda: _run_when_ready(path, tries + 1))


def load_plugin():
    _workbench().after(800, _check_for_request)
