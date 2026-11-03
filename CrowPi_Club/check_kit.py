#!/usr/bin/env python3
# ==================================================================
#  CrowPi Club - KIT CHECK
#  Run this first on every kit (teacher). It checks the software, finds
#  every chip on the board, flashes/beeps the outputs, reads the sensors
#  and lists the cameras. Nothing needs to be installed.
#
#  It saves a report in the home folder:  CrowPi_check_report.txt
#  (and a copy in this folder when it can). If something is wrong,
#  send that one text file instead of photos.
# ==================================================================
import crowpi_kit                  # CrowPi Club setup - always keep this line first
import datetime
import glob
import grp
import importlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import time

lines: list = []
problems: list = []


def log(text=""):
    print(text)
    lines.append(str(text))


def result(check, ok, detail="", how_to_fix=""):
    log(("  [ OK ] " if ok else "  [FIX!] ") + check + ("  -  " + detail if detail else ""))
    if not ok:
        problems.append((check, how_to_fix or detail))


def section(title):
    log()
    log("--- " + title + " " + "-" * max(0, 50 - len(title)))


def read_file(file_path):
    try:
        with open(file_path, encoding="utf-8", errors="replace") as handle:
            return handle.read().replace("\x00", "").strip()
    except OSError:
        return ""


def run(command, timeout=15):
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return (out.stdout + out.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return "(could not run: %s)" % e


log("CROWPI KIT CHECK  -  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
log("Computer name: " + socket.gethostname())
log("Lesson folder: " + crowpi_kit.HERE)

# ------------------------------------------------------------------
section("1. Computer")
model = read_file("/proc/device-tree/model")
log("  Model:  " + (model or "unknown"))
os_name = ""
for line in read_file("/etc/os-release").splitlines():
    if line.startswith("PRETTY_NAME="):
        os_name = line.split("=", 1)[1].strip('"')
log("  System: " + os_name)
log("  Kernel: " + platform.release())
log("  Python: " + sys.version.split()[0] + "  (" + sys.executable + ")")
result("Raspberry Pi 5", "Raspberry Pi 5" in model, model,
       "These lessons are made for the Raspberry Pi 5 kit.")
try:
    user = os.environ.get("USER") or os.getlogin()
except OSError:
    user = "?"
group_names = set()
for gid in os.getgroups():
    try:
        group_names.add(grp.getgrgid(gid).gr_name)
    except KeyError:
        pass
for group in ("gpio", "i2c", "spi"):
    result("user '%s' may use %s" % (user, group.upper()), group in group_names or os.geteuid() == 0,
           "", "The user is not in the '%s' group." % group)

# ------------------------------------------------------------------
section("2. Interfaces")
result("I2C turned on (/dev/i2c-1)", os.path.exists("/dev/i2c-1"), "",
       "Raspberry menu > Preferences > Raspberry Pi Configuration > Interfaces > I2C ON, restart.")
result("SPI turned on (/dev/spidev0.0 and 0.1)",
       os.path.exists("/dev/spidev0.0") and os.path.exists("/dev/spidev0.1"), "",
       "Raspberry menu > Preferences > Raspberry Pi Configuration > Interfaces > SPI ON, restart.")
chips = sorted(glob.glob("/dev/gpiochip*"))
result("GPIO chips found", bool(chips), " ".join(chips))

# ------------------------------------------------------------------
section("3. Software (already on the kit - nothing to install)")
for module, why in [("gpiozero", "all lessons"), ("lgpio", "GPIO on the Pi 5"),
                    ("spidev", "RFID and LED matrix"), ("luma.led_matrix", "LED matrix"),
                    ("PIL", "LED matrix pictures")]:
    try:
        mod = importlib.import_module(module)
        version = getattr(mod, "__version__", "")
        result(module, True, version)
    except Exception as e:
        result(module, False, str(e), "Library '%s' is missing (needed for %s)." % (module, why))
smbus_ok = False
for module in ("smbus2", "smbus"):
    try:
        importlib.import_module(module)
        smbus_ok = True
        log("  [ OK ] " + module)
        break
    except Exception:
        pass
if not smbus_ok:
    result("smbus (I2C library)", False, "", "Install python3-smbus.")
for module in ("picamera2", "cv2"):
    try:
        importlib.import_module(module)
        log("  [info] %s available" % module)
    except Exception:
        log("  [info] %s not installed (only matters for the camera)" % module)
for program in ("rpicam-still", "libcamera-still", "fswebcam", "ffmpeg"):
    log("  [info] %-16s %s" % (program, "found" if shutil.which(program) else "-"))

# ------------------------------------------------------------------
section("4. GPIO (the Pi 5 pin library)")
factory = None
try:
    from gpiozero import Device
    if Device.pin_factory is None:
        try:
            Device.ensure_pin_factory()
        except AttributeError:                    # older gpiozero: any device starts it
            import gpiozero
            gpiozero.DigitalInputDevice(17).close()
    factory = Device.pin_factory
    name = type(factory).__name__
    result("gpiozero pin library", "LGPIO" in name.upper(), name,
           "gpiozero is not using lgpio. Run this from the lesson folder (it must start with import crowpi_kit).")
except Exception as e:
    result("gpiozero pin library", False, str(e),
           "GPIO does not start. Send this report to your teacher.")

# ------------------------------------------------------------------
section("5. Chips on the I2C bus")
expected = {0x21: "LCD screen (MCP23008)", 0x38: "temperature/humidity (DHT20)",
            0x5C: "light sensor (BH1750)", 0x70: "7-segment display (HT16K33)"}
found = []
try:
    bus = crowpi_kit.i2c_bus()
    for address in range(0x03, 0x78):
        try:
            bus.read_byte(address)
            found.append(address)
        except OSError:
            pass
    log("  Found: " + (" ".join("0x%02X" % a for a in found) or "nothing"))
    for address, name in expected.items():
        result("%s at 0x%02X" % (name, address), address in found, "",
               "Not answering. Check that the GPIO ribbon cable is pushed in on both ends.")
except Exception as e:
    result("I2C bus", False, str(e))

# ------------------------------------------------------------------
section("6. Outputs - watch and listen!")
lcd = None
try:
    lcd = crowpi_kit.LCD()
    lcd.show("CrowPi kit check", "LCD works!")
    result("LCD shows 'CrowPi kit check'", True, "look at the LCD",
           "")
except Exception as e:
    result("LCD", False, str(e))
try:
    seg = crowpi_kit.SevenSegment()
    seg.show("8888", colon=True)
    result("7-segment shows 88:88", True, "look at the red digits")
except Exception as e:
    result("7-segment", False, str(e))
try:
    matrix = crowpi_kit.open_matrix()
    crowpi_kit.draw_picture(matrix, crowpi_kit.SMILE)
    result("LED matrix shows a smiley (upright)", True, "if it's sideways, tell your teacher")
except Exception as e:
    result("LED matrix", False, str(e))
try:
    from gpiozero import Buzzer
    buzzer = Buzzer(18)
    buzzer.on()
    time.sleep(0.15)
    buzzer.off()
    buzzer.close()
    result("buzzer beeped once", True)
except Exception as e:
    result("buzzer", False, str(e))
try:
    from gpiozero import OutputDevice
    relay = OutputDevice(21)
    relay.on()
    time.sleep(0.4)
    relay.off()
    relay.close()
    result("relay clicked twice", True)
except Exception as e:
    result("relay", False, str(e))

# ------------------------------------------------------------------
section("7. Sensors")
try:
    temperature, humidity = crowpi_kit.DHT20().read()
    result("temperature/humidity", -10 < temperature < 50, "%.1f C, %.1f %%" % (temperature, humidity))
except Exception as e:
    result("temperature/humidity", False, str(e))
try:
    lux = crowpi_kit.LightSensor().read()
    result("light sensor", True, "%.1f lux" % lux)
except Exception as e:
    result("light sensor", False, str(e))
try:
    reader = crowpi_kit.RFID(reset_pin=25)
    result("RFID reader", True, "chip version 0x%02X" % reader.version)
    reader.close()
except Exception as e:
    result("RFID reader", False, str(e))


def level(gpio, pull_up):
    try:
        from gpiozero import DigitalInputDevice
        device = DigitalInputDevice(gpio, pull_up=pull_up)
        value = int(device.pin.state)
        device.close()
        return value
    except Exception as e:
        return "error: %s" % e


log("  Pin levels right now (1 = HIGH, 0 = LOW):")
log("    touch  GPIO17 = %s   (1 = being touched)" % level(17, False))
log("    IR     GPIO20 = %s   (1 = resting; receiver must be plugged in)" % level(20, True))
arrows = {}
for name, pin in (("UP", 26), ("DOWN", 13), ("LEFT", 25), ("RIGHT", 19)):
    arrows[name] = level(pin, True)
log("    arrow buttons (0 = pressed): " + "  ".join("%s=%s" % kv for kv in arrows.items()))
if all(v == 0 for v in arrows.values()):
    log("    All arrow buttons read 'pressed' - the switches are probably not in BUTTON MODE.")

# ------------------------------------------------------------------
section("8. Camera")
usb_found = False
other_video = []
for folder in sorted(glob.glob("/sys/class/video4linux/video*")):
    name = read_file(os.path.join(folder, "name"))
    driver = os.path.basename(os.path.realpath(os.path.join(folder, "device", "driver")))
    index = read_file(os.path.join(folder, "index"))
    if driver == "uvcvideo":
        usb_found = True
        log("  USB camera: /dev/%s  name=%s  index=%s" % (os.path.basename(folder), name, index))
    else:
        other_video.append("%s(%s)" % (os.path.basename(folder), driver))
log("  USB camera: " + ("yes" if usb_found else "no"))
log("  Other video devices: %d %s" % (len(other_video), " ".join(other_video[:6])))
csi_text = ""
for tool in ("rpicam-hello", "libcamera-hello"):
    if shutil.which(tool):
        csi_text = run([tool, "--list-cameras"])
        break
if csi_text:
    for line in csi_text.splitlines()[:6]:
        log("  " + line)
csi_found = "Available cameras" in csi_text
result("a camera was found", usb_found or csi_found,
       "USB" if usb_found else ("ribbon (CSI)" if csi_found else "none"),
       "No camera found. Check the camera cable (Lesson 16).")

# ------------------------------------------------------------------
section("9. Lesson files")
lesson_files = sorted(glob.glob(os.path.join(crowpi_kit.HERE, "*.py")))
writable = [os.path.basename(f) for f in lesson_files
            if os.access(f, os.W_OK) and os.path.basename(f) not in ("setup_kit.py",)]
log("  %d lesson files in %s" % (len(lesson_files), crowpi_kit.HERE))
result("lesson files protected (read-only)", not writable,
       "" if not writable else "%d files can still be saved over" % len(writable),
       "Double-click CrowPi Lessons in the extracted CrowPi_Club folder - it installs itself.")

# ------------------------------------------------------------------
section("10. CrowPi Lessons app")
thonny_program = shutil.which("thonny")
result("Thonny is installed", bool(thonny_program), thonny_program or "",
       "Thonny is missing - the lessons open in Thonny.")
config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
thonny_home = os.environ.get("THONNY_USER_DIR") or os.path.join(config_home, "Thonny")
helper_files = [
    os.path.join(thonny_home, "plugins", "lib", "python%d.%d" % sys.version_info[:2],
                 "site-packages", "thonnycontrib", "crowpi_autorun", "__init__.py"),
    "/usr/lib/python3/dist-packages/thonnycontrib/crowpi_autorun/__init__.py",   # apt package
]
helper_file = next((f for f in helper_files if os.path.exists(f)), "")
result("Thonny helper (lessons start by themselves)", bool(helper_file), helper_file,
       "Double-click CrowPi Lessons in the extracted folder, then close and reopen Thonny.")
shortcut = os.path.expanduser("~/Desktop/CrowPi_Lessons.desktop")
result("'CrowPi Lessons' icon on the Desktop", os.path.exists(shortcut), "",
       "Double-click CrowPi Lessons in the extracted folder - it makes the icon.")
autostart_file = os.path.join(config_home, "autostart", "crowpi-lessons.desktop")
autostart_text = read_file(autostart_file)
log("  [info] opens by itself at start-up: " +
    ("OFF" if "Hidden=true" in autostart_text else "ON" if autostart_text else "not set up"))

# ------------------------------------------------------------------
section("SUMMARY")
if problems:
    log("  %d thing(s) to fix:" % len(problems))
    for name, fix in problems:
        log("   - %s: %s" % (name, fix))
else:
    log("  Everything checked out!")
log()
log("Switches (turn them only when no lesson is running):")
log("  " + crowpi_kit.BUTTON_MODE)
log("     -> arrow buttons, button matrix, piano, Simon Says, the CrowPi Show, games")
log("  All other lessons work with the switches any way.")

report = "\n".join(lines) + "\n"
saved = []
home = os.path.expanduser("~")                    # (the Desktop only has CrowPi Lessons on it)
places = [(home, "CrowPi_check_report.txt")]
extra = os.environ.get("CROWPI_REPORT_DIR")             # (also save a copy here, e.g. a USB stick)
if not extra and crowpi_kit.HERE.startswith(("/media/", "/mnt/", "/run/media/")):
    extra = crowpi_kit.HERE                              # running straight from a USB stick
if extra:
    places.append((extra, "CrowPi_check_report_%s.txt" % socket.gethostname()))
for folder, name in places:
    try:
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        saved.append(path)
    except OSError:
        pass
log()
for path in saved:
    print("Report saved: " + path)
if lcd:
    try:
        lcd.show("Kit check done", "%d to fix" % len(problems) if problems else "All OK!")
    except Exception:
        pass
crowpi_kit.finish()
