#!/usr/bin/env python3
# ==================================================================
#  crowpi_kit.py  -  CrowPi Club helper file (Raspberry Pi 5 version)
#
#  Every lesson starts with the line:   import crowpi_kit
#
#  That one line:
#   1. Moves Python to your home folder. The Pi 5 GPIO library (lgpio)
#      has to make a small hidden file in a folder it can write to.
#      A USB stick can't hold that file, so lessons used to crash there.
#   2. Hides a harmless "servo jitter" warning.
#   3. Shows friendly messages when something is wrong.
#   3b. Keeps the buzzer quiet if the Buzzer switch in the app is OFF.
#   4. Gives the lessons ready-made helpers for the parts that need a
#      lot of code: LCD, 7-segment display, LED matrix, temperature
#      sensor, light sensor, RFID reader and IR remote.
#
#  Pins and addresses come from the CrowPi Quick Start Guide and from
#  Elecrow's official Raspberry Pi 5 examples (github.com/Elecrow-RD/CrowPi_RPI5).
#
#  DO NOT CHANGE OR SAVE OVER THIS FILE. Every lesson needs it.
# ==================================================================
import os
import sys
import time
import warnings
import threading
from collections import deque

# ---------- 1. Run from the home folder ----------
HERE = os.path.dirname(os.path.abspath(__file__))     # the lesson folder
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _program_file():
    """The lesson file that is running (worked out before we change folder)."""
    path = getattr(sys.modules.get("__main__"), "__file__", None) or (sys.argv[0] if sys.argv else "")
    return os.path.realpath(path) if path else ""


# The Buzzer switch in the CrowPi Lessons app saves ON or OFF here.
# (The app switches it back ON when it opens and when it closes.)
BUZZER_SETTING = os.path.join(os.path.expanduser("~"), ".crowpi_buzzer.json")


def _buzzer_switched_off():
    if os.path.basename(_program_file()) == "check_kit.py":
        return False                      # the kit check always tests the buzzer
    try:
        import json
        with open(BUZZER_SETTING, encoding="utf-8") as f:
            return json.load(f).get("buzzer") == "off"
    except (OSError, ValueError, AttributeError):
        return False


BUZZER_OFF = _buzzer_switched_off()
try:
    os.chdir(os.path.expanduser("~"))
except OSError:
    os.chdir("/tmp")

# ---------- 2. Hide harmless warnings ----------
warnings.filterwarnings("ignore", module="gpiozero")      # e.g. "servo jitter" notes
warnings.filterwarnings("ignore", message=".*jitter.*")
warnings.filterwarnings("ignore", message=".*[Nn]o echo received.*")
warnings.filterwarnings("ignore", message=".*Falling back.*")


# ---------- 2b. Buzzer OFF: a quiet stand-in for the buzzer ----------
class QuietBuzzer:
    """Used instead of the buzzer on GPIO18 when the Buzzer switch is OFF.
    It does everything the buzzer does (and takes just as long) - silently."""
    def __init__(self, *_args, **kwargs):
        self.value = 0
        self.frequency = kwargs.get("frequency", 100)

    def on(self):
        self.value = 1

    def off(self):
        self.value = 0

    def toggle(self):
        self.value = 0 if self.value else 1

    @property
    def is_active(self):
        return bool(self.value)

    def beep(self, on_time=1, off_time=1, n=None, background=True):
        if not background:                   # the real one would wait, so wait too
            if n is None:
                while True:
                    time.sleep(1)
            time.sleep((on_time + off_time) * n)

    def blink(self, on_time=1, off_time=1, n=None, background=True, **_kwargs):
        self.beep(on_time, off_time, n, background)

    def close(self):
        self.value = 0


def _quiet_buzzer_pin(pin):
    return str(pin).upper() in ("18", "GPIO18", "BCM18", "BOARD12")


if BUZZER_OFF:
    import gpiozero
    _real_buzzer, _real_pwm = gpiozero.Buzzer, gpiozero.PWMOutputDevice

    def _buzzer(pin, *args, **kwargs):
        return QuietBuzzer(**kwargs) if _quiet_buzzer_pin(pin) else _real_buzzer(pin, *args, **kwargs)

    def _pwm_output(pin, *args, **kwargs):
        return QuietBuzzer(**kwargs) if _quiet_buzzer_pin(pin) else _real_pwm(pin, *args, **kwargs)

    setattr(gpiozero, "Buzzer", _buzzer)
    setattr(gpiozero, "PWMOutputDevice", _pwm_output)


# ---------- 3. Friendly error messages ----------
class KitProblem(RuntimeError):
    """A problem with the kit (a part not found, a switch, a cable...)."""


def _running_in_terminal():
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def finish(message="Done!"):
    """Call at the end of a lesson. Keeps a terminal window open so you can read it."""
    print()
    print(message)
    if _running_in_terminal():
        try:
            input("Press Enter to close this window.")
        except (EOFError, KeyboardInterrupt):
            pass


def _friendly_excepthook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        print("\nStopped.")
        return
    if issubclass(exc_type, KitProblem):
        print()
        print("=" * 56)
        print("PROBLEM:", exc)
        print("=" * 56)
    else:
        sys.__excepthook__(exc_type, exc, tb)
        print()
        print("Something went wrong. Take a photo of this message for your teacher.")
    if _running_in_terminal():
        try:
            input("Press Enter to close this window.")
        except (EOFError, KeyboardInterrupt):
            pass


sys.excepthook = _friendly_excepthook


def open_lesson(name):
    """Open a lesson app (Learn / Try it / Challenge) - see crowpi_app.py."""
    for folder in (os.path.expanduser("~/CrowPi_Club"), "/usr/share/crowpi-lessons"):
        if not os.path.exists(os.path.join(HERE, "crowpi_app.py")) and folder not in sys.path:
            sys.path.append(folder)          # (a copy saved somewhere else still finds the app)
    from crowpi_app import run
    run(name)


def banner(title, switches="No switches needed.", parts=None, window=True):
    """Print the lesson title and what the kit needs. (window: kept for older lessons)"""
    print("=" * 56)
    print(" " + title)
    print("=" * 56)
    print("Switches:", switches)
    if parts:
        print("Parts:   ", parts)
    if BUZZER_OFF:
        print("Buzzer:   OFF (the Buzzer switch in the CrowPi Lessons app) - quiet")
    print("To stop: click Close in the lesson window (or the red Stop button in Thonny).")
    print("-" * 56)
    del window


MOTOR_MODE = ("MOTOR MODE - right switch block (UX5): all 8 UP (ON). "
              "Left switch block (UX1): all 8 DOWN (OFF).")
BUTTON_MODE = ("BUTTON MODE - left switch block (UX1): all 8 UP (ON). "
               "Right switch block (UX5): all 8 DOWN (OFF).")
SWITCH_TIP = "Flip switches only when no program is running."


# ==================================================================
#  I2C bus (LCD, segment display, temperature and light sensors)
# ==================================================================
_bus = None


def i2c_bus():
    """Open I2C bus 1 (the GPIO header bus). Works with smbus2 or smbus."""
    global _bus
    if _bus is None:
        import importlib
        smbus_class = None
        for module_name in ("smbus2", "smbus"):
            try:
                smbus_class = importlib.import_module(module_name).SMBus
                break
            except ImportError:
                pass
        if smbus_class is None:
            raise KitProblem("The I2C library (smbus) is missing on this kit. "
                             "Ask your teacher to run check_kit.py.")
        try:
            _bus = smbus_class(1)
        except (FileNotFoundError, PermissionError, OSError):
            raise KitProblem("I2C is turned off. Turn it on: Raspberry menu > Preferences > "
                             "Raspberry Pi Configuration > Interfaces > I2C = ON, then restart.")
    return _bus


def i2c_present(address):
    """True if a chip answers at this I2C address."""
    try:
        i2c_bus().read_byte(address)
        return True
    except OSError:
        return False


# ==================================================================
#  LCD 16x2  (HD44780 driven by an MCP23008 chip at I2C 0x21)
#  MCP23008 pins: GP1=RS, GP2=E, GP3-GP6=D4-D7, GP7=backlight (HIGH = on)
#  Same wiring as Elecrow's Pi 5 lcd.py and the Adafruit LCD backpack.
# ==================================================================
class LCD:
    """The blue 16x2 text screen.

    lcd = LCD()
    lcd.show("Line one", "Line two")
    lcd.clear()
    """
    COLS = 16
    ROWS = 2
    _RS = 0x02
    _EN = 0x04
    _LIGHT = 0x80
    _IODIR = 0x00
    _GPPU = 0x06
    _GPIO = 0x09

    def __init__(self, address=0x21, backlight=True):
        self.address = address
        self.bus = i2c_bus()
        self._light = self._LIGHT if backlight else 0
        try:
            self.bus.write_byte_data(address, self._IODIR, 0x01)   # GP1-GP7 = outputs
            self.bus.write_byte_data(address, self._GPPU, 0x00)    # no pull-ups
            self._out(0)
        except OSError:
            raise KitProblem("The LCD did not answer (I2C address 0x21). "
                             "Check that the kit is fully powered and the GPIO ribbon cable is pushed in.")
        time.sleep(0.05)
        # Standard 4-bit start-up (the same commands as the Adafruit/Elecrow driver)
        for command in (0x33, 0x32, 0x0C, 0x28, 0x06):
            self._write(command)
            time.sleep(0.005)
        self.clear()

    # --- low level ---
    def _out(self, value):
        self.bus.write_byte_data(self.address, self._GPIO, (value & 0x7F) | self._light)

    def _nibble(self, nibble, char_mode):
        data = ((nibble & 0x0F) << 3) | (self._RS if char_mode else 0)
        self._out(data)              # put the 4 bits on D4-D7, E low
        self._out(data | self._EN)   # E high
        self._out(data)              # E low: the LCD reads the 4 bits now

    def _write(self, value, char_mode=False):
        self._nibble(value >> 4, char_mode)
        self._nibble(value & 0x0F, char_mode)

    # --- for lessons ---
    def clear(self):
        """Erase the screen."""
        self._write(0x01)
        time.sleep(0.003)

    def set_cursor(self, column, row):
        """Move the cursor. column 0-15, row 0-1."""
        column = max(0, min(self.COLS - 1, int(column)))
        row = max(0, min(self.ROWS - 1, int(row)))
        self._write(0x80 | (column + (0x00, 0x40)[row]))

    def write(self, text):
        """Write text where the cursor is."""
        for ch in str(text):
            if ch == "°":          # degree sign
                code = 0xDF
            else:
                code = ord(ch)
                if code > 0x7F:
                    code = ord("?")
            self._write(code, char_mode=True)

    def show(self, line1="", line2=""):
        """Show two lines of text (16 letters each). No flicker."""
        self.set_cursor(0, 0)
        self.write(str(line1)[:self.COLS].ljust(self.COLS))
        self.set_cursor(0, 1)
        self.write(str(line2)[:self.COLS].ljust(self.COLS))

    def message(self, text):
        """Show text. Put \\n where line 2 should start."""
        lines = (str(text).split("\n") + ["", ""])[:2]
        self.show(lines[0], lines[1])

    def backlight(self, on=True):
        """Turn the screen light on (True) or off (False)."""
        self._light = self._LIGHT if on else 0
        self._out(0)

    def cursor(self, show=True, blink=False):
        """Show or hide the cursor."""
        self._write(0x0C | (0x02 if show else 0) | (0x01 if blink else 0))


# ==================================================================
#  4-digit 7-segment display  (HT16K33 chip at I2C 0x70)
#  Display memory: digit 1 = byte 0, digit 2 = byte 2, colon = byte 4,
#  digit 3 = byte 6, digit 4 = byte 8 (same as Adafruit's SevenSegment).
# ==================================================================
class SevenSegment:
    """The red 4-digit display.

    seg = SevenSegment()
    seg.show_number(42)
    seg.show("COOL")
    seg.show_time(12, 30)
    """
    FONT = {
        " ": 0x00, "-": 0x40, "_": 0x08,
        "0": 0x3F, "1": 0x06, "2": 0x5B, "3": 0x4F, "4": 0x66,
        "5": 0x6D, "6": 0x7D, "7": 0x07, "8": 0x7F, "9": 0x6F,
        "A": 0x77, "B": 0x7C, "C": 0x39, "D": 0x5E, "E": 0x79, "F": 0x71,
        "G": 0x3D, "H": 0x76, "I": 0x06, "J": 0x1E, "L": 0x38, "N": 0x54,
        "O": 0x3F, "P": 0x73, "R": 0x50, "S": 0x6D, "T": 0x78, "U": 0x3E,
        "Y": 0x6E, "Z": 0x5B,
    }
    POSITIONS = (0, 2, 6, 8)

    def __init__(self, address=0x70, brightness=15):
        self.address = address
        self.bus = i2c_bus()
        self.buffer = [0] * 16
        try:
            self.bus.write_byte(address, 0x21)                       # oscillator on
            self.bus.write_byte(address, 0x81)                       # display on, no blink
            self.bus.write_byte(address, 0xE0 | (brightness & 0x0F))  # brightness 0-15
        except OSError:
            raise KitProblem("The 7-segment display did not answer (I2C address 0x70). "
                             "Check the power and the GPIO ribbon cable.")
        self.clear()

    def update(self):
        """Send the picture to the display."""
        for i, value in enumerate(self.buffer):
            self.bus.write_byte_data(self.address, i, value)

    def clear(self):
        """Turn every segment off."""
        self.buffer = [0] * 16
        self.update()

    def set_colon(self, on=True):
        if on:
            self.buffer[4] |= 0x02
        else:
            self.buffer[4] &= 0xFD

    def show(self, text, colon=False):
        """Show up to 4 letters/numbers. A '.' lights the dot after the letter before it."""
        cells: list = []
        for ch in str(text).upper():
            if ch == "." and cells:
                cells[-1] |= 0x80
            else:
                cells.append(self.FONT.get(ch, 0x00))
        cells = (cells + [0, 0, 0, 0])[:4]
        self.buffer = [0] * 16
        for position, value in zip(self.POSITIONS, cells):
            self.buffer[position] = value
        self.set_colon(colon)
        self.update()

    def show_number(self, number):
        """Show a whole number from -999 to 9999 (lined up on the right)."""
        number = int(round(number))
        number = max(-999, min(9999, number))
        self.show(str(number).rjust(4))

    def show_time(self, hours, minutes, colon=True):
        """Show a clock time like 12:30."""
        self.show("%02d%02d" % (hours % 100, minutes % 100), colon=colon)

    def brightness(self, level):
        """0 (dim) to 15 (bright)."""
        self.bus.write_byte(self.address, 0xE0 | (int(level) & 0x0F))


# ==================================================================
#  Temperature and humidity sensor  (DHT20 at I2C 0x38)
#  Same commands as Elecrow's Pi 5 example (DFRobot_DHT20) plus a CRC check.
# ==================================================================
def _crc8(data):
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class DHT20:
    """Temperature and humidity sensor.

    sensor = DHT20()
    temperature, humidity = sensor.read()
    """
    def __init__(self, address=0x38):
        self.address = address
        self.bus = i2c_bus()
        time.sleep(0.1)
        try:
            self.bus.read_i2c_block_data(address, 0x71, 1)
        except OSError:
            raise KitProblem("The temperature sensor (DHT20, I2C address 0x38) did not answer.")

    def read(self):
        """Return (temperature in C, humidity in %)."""
        reading = None
        for _ in range(3):
            self.bus.write_i2c_block_data(self.address, 0xAC, [0x33, 0x00])
            time.sleep(0.1)
            d = self.bus.read_i2c_block_data(self.address, 0x71, 7)
            if d[0] & 0x80:            # still measuring
                time.sleep(0.05)
                continue
            humidity = ((d[1] << 12) | (d[2] << 4) | (d[3] >> 4)) * 100.0 / 1048576
            temperature = (((d[3] & 0x0F) << 16) | (d[4] << 8) | d[5]) * 200.0 / 1048576 - 50
            reading = (round(temperature, 1), round(humidity, 1))
            if _crc8(d[:6]) == d[6]:
                return reading
            time.sleep(0.05)
        if reading is None:
            raise KitProblem("The temperature sensor stayed busy. Try again.")
        return reading


# ==================================================================
#  Light sensor  (BH1750 at I2C 0x5C)
# ==================================================================
class LightSensor:
    """Light level in lux (0 = dark, 300 = classroom, 10000+ = sunlight).

    light = LightSensor()
    print(light.read())
    """
    def __init__(self, address=0x5C):
        self.address = address
        self.bus = i2c_bus()
        try:
            self.bus.write_byte(address, 0x01)   # power on
            self.bus.write_byte(address, 0x10)   # measure all the time, 1 lux steps
        except OSError:
            raise KitProblem("The light sensor (BH1750, I2C address 0x5C) did not answer.")
        time.sleep(0.2)

    def read(self):
        data = self.bus.read_i2c_block_data(self.address, 0x10, 2)
        return round(((data[0] << 8) | data[1]) / 1.2, 1)


# ==================================================================
#  8x8 LED matrix  (MAX7219 on SPI, chip select CE1 = pin 26)
#  block_orientation=90 is Elecrow's setting for the CrowPi board.
# ==================================================================
def open_matrix(brightness=4):
    """Open the red 8x8 LED matrix. brightness 0-15."""
    try:
        from luma.core.interface.serial import spi, noop
        from luma.led_matrix.device import max7219
    except ImportError:
        raise KitProblem("The LED matrix library (luma.led_matrix) is missing on this kit.")
    try:
        serial = spi(port=0, device=1, gpio=noop())
    except Exception:
        raise KitProblem("SPI is turned off. Turn it on: Raspberry menu > Preferences > "
                         "Raspberry Pi Configuration > Interfaces > SPI = ON, then restart.")
    device = max7219(serial, cascaded=1, block_orientation=90, rotate=0)
    device.contrast(max(0, min(15, int(brightness))) * 16)
    return device


def draw_picture(device, rows):
    """Draw an 8x8 picture. rows = 8 strings of 8 letters: X = light on, . = off."""
    from luma.core.render import canvas
    with canvas(device) as draw:
        for y, row in enumerate(rows[:8]):
            for x, ch in enumerate(str(row)[:8]):
                if ch not in " .0_-":
                    draw.point((x, y), fill="white")


def scroll_text(device, text, speed=0.06):
    """Scroll a message across the LED matrix (smaller speed = faster)."""
    from luma.core.legacy import show_message
    from luma.core.legacy.font import proportional, CP437_FONT
    show_message(device, str(text), fill="white", font=proportional(CP437_FONT),
                 scroll_delay=speed)


HEART = [".XX..XX.",
         "XXXXXXXX",
         "XXXXXXXX",
         "XXXXXXXX",
         ".XXXXXX.",
         "..XXXX..",
         "...XX...",
         "........"]

SMILE = ["..XXXX..",
         ".X....X.",
         "X.X..X.X",
         "X......X",
         "X.X..X.X",
         "X..XX..X",
         ".X....X.",
         "..XXXX.."]

ARROWS = {
    "UP":    ["...XX...", "..XXXX..", ".XXXXXX.", "XXXXXXXX",
              "...XX...", "...XX...", "...XX...", "...XX..."],
    "DOWN":  ["...XX...", "...XX...", "...XX...", "...XX...",
              "XXXXXXXX", ".XXXXXX.", "..XXXX..", "...XX..."],
    "LEFT":  ["...X....", "..XX....", ".XXX....", "XXXXXXXX",
              "XXXXXXXX", ".XXX....", "..XX....", "...X...."],
    "RIGHT": ["....X...", "....XX..", "....XXX.", "XXXXXXXX",
              "XXXXXXXX", "....XXX.", "....XX..", "....X..."],
}


# ==================================================================
#  RFID / NFC reader  (MFRC522 on SPI, chip select CE0 = pin 24)
#  Reads and writes the blue tag and white card (MIFARE Classic 1K).
#  Text goes in blocks 8, 9, 10 with the standard key - the same as
#  Elecrow's Read.py/Write.py, so cards work with both.
# ==================================================================
class RFID:
    """RFID reader.

    reader = RFID()
    card_id, text = reader.read_text()
    reader.write_text("Hello")
    """
    _IDLE, _AUTHENT, _TRANSCEIVE, _RESET, _CALCCRC = 0x00, 0x0E, 0x0C, 0x0F, 0x03
    _OK, _NOTAG, _ERR = 0, 1, 2
    _CommandReg, _CommIEnReg, _CommIrqReg, _DivIrqReg = 0x01, 0x02, 0x04, 0x05
    _ErrorReg, _Status2Reg, _FIFODataReg, _FIFOLevelReg = 0x06, 0x08, 0x09, 0x0A
    _ControlReg, _BitFramingReg, _ModeReg, _TxControlReg = 0x0C, 0x0D, 0x11, 0x14
    _TxAutoReg, _CRCResultRegM, _CRCResultRegL = 0x15, 0x21, 0x22
    _TModeReg, _TPrescalerReg, _TReloadRegH, _TReloadRegL = 0x2A, 0x2B, 0x2C, 0x2D
    _VersionReg = 0x37
    KEY = [0xFF] * 6
    TEXT_BLOCKS = (8, 9, 10)
    TRAILER_BLOCK = 11

    def __init__(self, reset_pin=25):
        # The reader's RESET line is GPIO25 (same as Elecrow's code). First we only
        # pull it up gently, which is safe even if someone presses the LEFT button
        # (it shares GPIO25). If the reader doesn't answer, we drive it HIGH.
        self._reset_device = None
        if reset_pin is not None:
            try:
                from gpiozero import DigitalInputDevice
                self._reset_device = DigitalInputDevice(reset_pin, pull_up=True)
            except Exception:
                self._reset_device = None    # pin busy or not needed - that's OK
        try:
            import spidev
        except ImportError:
            raise KitProblem("The SPI library (spidev) is missing on this kit.")
        self.spi = spidev.SpiDev()
        try:
            self.spi.open(0, 0)
        except (FileNotFoundError, PermissionError, OSError):
            raise KitProblem("SPI is turned off. Turn it on: Raspberry menu > Preferences > "
                             "Raspberry Pi Configuration > Interfaces > SPI = ON, then restart.")
        self.spi.max_speed_hz = 1000000
        self.spi.mode = 0
        time.sleep(0.05)
        self._init_chip()
        version = self._read(self._VersionReg)
        if version in (0x00, 0xFF) and reset_pin is not None:
            try:
                from gpiozero import OutputDevice
                if self._reset_device is not None:
                    self._reset_device.close()
                self._reset_device = OutputDevice(reset_pin, initial_value=True)
                time.sleep(0.05)
                self._init_chip()
                version = self._read(self._VersionReg)
            except Exception:
                pass
        if version in (0x00, 0xFF):
            raise KitProblem("The RFID reader did not answer. Check the power and the GPIO ribbon cable.")
        self.version = version

    # --- registers ---
    def _write(self, reg, value):
        self.spi.xfer2([(reg << 1) & 0x7E, value & 0xFF])

    def _read(self, reg):
        return self.spi.xfer2([((reg << 1) & 0x7E) | 0x80, 0])[1]

    def _set_bits(self, reg, mask):
        self._write(reg, self._read(reg) | mask)

    def _clear_bits(self, reg, mask):
        self._write(reg, self._read(reg) & (~mask & 0xFF))

    def _init_chip(self):
        self._write(self._CommandReg, self._RESET)
        time.sleep(0.05)
        self._write(self._TModeReg, 0x8D)
        self._write(self._TPrescalerReg, 0x3E)
        self._write(self._TReloadRegL, 30)
        self._write(self._TReloadRegH, 0)
        self._write(self._TxAutoReg, 0x40)
        self._write(self._ModeReg, 0x3D)
        if (self._read(self._TxControlReg) & 0x03) != 0x03:
            self._set_bits(self._TxControlReg, 0x03)       # antenna on

    def _to_card(self, command, data):
        if command == self._AUTHENT:
            irq_en, wait_irq = 0x12, 0x10
        else:
            irq_en, wait_irq = 0x77, 0x30
        self._write(self._CommIEnReg, irq_en | 0x80)
        self._clear_bits(self._CommIrqReg, 0x80)
        self._set_bits(self._FIFOLevelReg, 0x80)            # empty the FIFO
        self._write(self._CommandReg, self._IDLE)
        for byte in data:
            self._write(self._FIFODataReg, byte)
        self._write(self._CommandReg, command)
        if command == self._TRANSCEIVE:
            self._set_bits(self._BitFramingReg, 0x80)       # start sending
        n = 0
        for _ in range(2000):
            n = self._read(self._CommIrqReg)
            if n & 0x01 or n & wait_irq:
                break
        else:
            self._clear_bits(self._BitFramingReg, 0x80)
            return self._ERR, [], 0
        self._clear_bits(self._BitFramingReg, 0x80)
        if self._read(self._ErrorReg) & 0x1B:
            return self._ERR, [], 0
        status = self._NOTAG if (n & irq_en & 0x01) else self._OK
        back, bits = [], 0
        if command == self._TRANSCEIVE:
            level = self._read(self._FIFOLevelReg)
            last_bits = self._read(self._ControlReg) & 0x07
            bits = (level - 1) * 8 + last_bits if last_bits else level * 8
            level = max(1, min(18, level))
            back = [self._read(self._FIFODataReg) for _ in range(level)]
        return status, back, bits

    def _crc(self, data):
        self._clear_bits(self._DivIrqReg, 0x04)
        self._set_bits(self._FIFOLevelReg, 0x80)
        for byte in data:
            self._write(self._FIFODataReg, byte)
        self._write(self._CommandReg, self._CALCCRC)
        for _ in range(255):
            if self._read(self._DivIrqReg) & 0x04:
                break
        return [self._read(self._CRCResultRegL), self._read(self._CRCResultRegM)]

    def _request(self):
        self._write(self._BitFramingReg, 0x07)
        status, _, bits = self._to_card(self._TRANSCEIVE, [0x26])
        return status == self._OK and bits == 0x10

    def _anticoll(self):
        self._write(self._BitFramingReg, 0x00)
        status, back, _ = self._to_card(self._TRANSCEIVE, [0x93, 0x20])
        if status != self._OK or len(back) < 5:
            return None
        check = 0
        for byte in back[:4]:
            check ^= byte
        if check != back[4]:
            return None
        return back[:5]

    def _select(self, uid):
        data = [0x93, 0x70] + list(uid[:5])
        data += self._crc(data)
        status, _, bits = self._to_card(self._TRANSCEIVE, data)
        return status == self._OK and bits == 0x18

    def _auth(self, block, uid):
        data = [0x60, block] + self.KEY + list(uid[:4])
        status, _, _ = self._to_card(self._AUTHENT, data)
        return status == self._OK and bool(self._read(self._Status2Reg) & 0x08)

    def _stop_crypto(self):
        self._clear_bits(self._Status2Reg, 0x08)

    def _read_block(self, block):
        data = [0x30, block]
        data += self._crc(data)
        status, back, _ = self._to_card(self._TRANSCEIVE, data)
        if status == self._OK and len(back) >= 16:
            return back[:16]
        return None

    def _write_block(self, block, sixteen_bytes):
        data = [0xA0, block]
        data += self._crc(data)
        status, back, bits = self._to_card(self._TRANSCEIVE, data)
        if status != self._OK or bits != 4 or not back or (back[0] & 0x0F) != 0x0A:
            return False
        data = list(sixteen_bytes[:16])
        data += self._crc(data)
        status, back, bits = self._to_card(self._TRANSCEIVE, data)
        return status == self._OK and bits == 4 and bool(back) and (back[0] & 0x0F) == 0x0A

    @staticmethod
    def _uid_number(uid):
        number = 0
        for byte in uid[:5]:
            number = number * 256 + byte
        return number

    # --- for lessons ---
    def read_id_now(self):
        """Card number if a card is on the reader right now, else None."""
        if not self._request():
            return None
        uid = self._anticoll()
        return self._uid_number(uid) if uid else None

    def wait_for_card(self, timeout=None):
        """Wait for a card. Returns its number (or None after timeout seconds)."""
        start = time.time()
        while timeout is None or time.time() - start < timeout:
            card = self.read_id_now()
            if card is not None:
                return card
            time.sleep(0.05)
        return None

    def wait_for_removal(self):
        """Wait until the card is taken away."""
        misses = 0
        while misses < 3:
            misses = misses + 1 if self.read_id_now() is None else 0
            time.sleep(0.1)

    def read_text(self, timeout=None):
        """Wait for a card and read the text saved on it. Returns (card_number, text)."""
        start = time.time()
        while timeout is None or time.time() - start < timeout:
            if self._request():
                uid = self._anticoll()
                if uid:
                    card = self._uid_number(uid)
                    text = None
                    if self._select(uid) and self._auth(self.TRAILER_BLOCK, uid):
                        data = []
                        for block in self.TEXT_BLOCKS:
                            chunk = self._read_block(block)
                            if chunk:
                                data += chunk
                        text = "".join(chr(b) for b in data if 32 <= b < 127).strip()
                    self._stop_crypto()
                    return card, text
            time.sleep(0.05)
        return None, None

    def write_text(self, text, timeout=None):
        """Wait for a card and save text on it (up to 48 letters). Returns the card number."""
        payload = str(text)[:48].ljust(48).encode("ascii", "replace")
        start = time.time()
        while timeout is None or time.time() - start < timeout:
            if self._request():
                uid = self._anticoll()
                if uid:
                    card = self._uid_number(uid)
                    ok = False
                    if self._select(uid) and self._auth(self.TRAILER_BLOCK, uid):
                        ok = True
                        for i, block in enumerate(self.TEXT_BLOCKS):
                            ok = self._write_block(block, payload[i * 16:(i + 1) * 16]) and ok
                    self._stop_crypto()
                    if ok:
                        return card
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.spi.close()
        except Exception:
            pass
        if self._reset_device is not None:
            self._reset_device.close()


# ==================================================================
#  IR remote  (IR receiver plugged into the "IR" socket, GPIO20)
#  NEC remote codes from Elecrow's IR.py. The edge times come from the
#  Linux kernel, so decoding is reliable on the Pi 5.
# ==================================================================
IR_KEYS = {
    0x45: "CH-", 0x46: "CH", 0x47: "CH+",
    0x44: "PREV", 0x40: "NEXT", 0x43: "PLAY/PAUSE",
    0x07: "VOL-", 0x15: "VOL+", 0x09: "EQ",
    0x16: "0", 0x19: "100+", 0x0D: "200+",
    0x0C: "1", 0x18: "2", 0x5E: "3",
    0x08: "4", 0x1C: "5", 0x5A: "6",
    0x42: "7", 0x52: "8", 0x4A: "9",
}


def decode_nec(edges):
    """edges = list of (seconds, level) where level is the pin level AFTER the edge.
    Returns the command byte, "REPEAT", or None."""
    pulses = []                       # (level, length in microseconds)
    for (t0, level), (t1, _) in zip(edges, edges[1:]):
        pulses.append((level, (t1 - t0) * 1e6))
    i = 0
    while i + 1 < len(pulses):
        level, length = pulses[i]
        next_level, next_length = pulses[i + 1]
        if level == 0 and 6000 < length < 12000 and next_level == 1:
            if 3000 < next_length < 6000:            # start of a new key
                bits: list = []
                j = i + 2
                while len(bits) < 32 and j + 1 < len(pulses):
                    mark, space = pulses[j][1], pulses[j + 1][1]
                    if not (200 < mark < 1000):
                        break
                    if 200 < space < 1000:
                        bits.append(0)
                    elif 1100 < space < 2400:
                        bits.append(1)
                    else:
                        break
                    j += 2
                if len(bits) == 32:
                    data = [sum(bits[k * 8 + b] << b for b in range(8)) for k in range(4)]
                    if data[2] ^ data[3] == 0xFF:
                        return data[2]
                return None
            if 1700 < next_length < 2900:            # key held down
                return "REPEAT"
        i += 1
    return None


class IRRemote:
    """The small IR remote control.

    remote = IRRemote()
    key = remote.wait_for_key()     # "1", "VOL+", "PLAY/PAUSE", ...
    """
    def __init__(self, pin=20):
        from gpiozero import Device
        if Device.pin_factory is None:
            try:
                Device.ensure_pin_factory()
            except AttributeError:            # older gpiozero: any device starts it
                from gpiozero import DigitalInputDevice
                DigitalInputDevice(pin).close()
        self._edges: deque = deque(maxlen=400)
        self._lock = threading.Lock()
        self._last_edge = 0.0
        self.last_key = None
        factory = Device.pin_factory
        if factory is None:
            raise KitProblem("GPIO did not start. Run check_kit.py.")
        self._pin = factory.pin(pin)
        self._pin.function = "input"
        self._pin.pull = "up"
        self._pin.edges = "both"
        self._pin.when_changed = self._changed

    def _changed(self, ticks, level):
        with self._lock:
            self._edges.append((ticks, 1 if level else 0))
            self._last_edge = time.monotonic()

    def read_key(self):
        """A key name if one was pressed since the last check, else None."""
        with self._lock:
            quiet = time.monotonic() - self._last_edge > 0.03
            if not quiet or len(self._edges) < 3:
                return None
            edges = list(self._edges)
            self._edges.clear()
        result = decode_nec(edges)
        if result == "REPEAT":
            return None
        if result is None:
            return None
        self.last_key = IR_KEYS.get(result, "0x%02X" % result)
        return self.last_key

    def wait_for_key(self, timeout=None):
        """Wait for a key press. Returns the key name (or None after timeout seconds)."""
        start = time.time()
        while timeout is None or time.time() - start < timeout:
            key = self.read_key()
            if key:
                return key
            time.sleep(0.02)
        return None

    def close(self):
        try:
            self._pin.when_changed = None
            self._pin.close()
        except Exception:
            pass


# ==================================================================
#  4x4 button matrix  (16 buttons - needs BUTTON MODE switches)
#  rows = GPIO27, 22, 5, 6   columns = GPIO25, 26, 19, 13
#  Button numbers 1-16 are the same as Elecrow's button_matrix.py.
# ==================================================================
class ButtonMatrix:
    """The grid of 16 buttons.

    keys = ButtonMatrix()
    number = keys.wait_for_press()   # 1 to 16
    keys.wait_for_release()
    """
    ROW_PINS = (27, 22, 5, 6)
    COLUMN_PINS = (25, 26, 19, 13)

    def __init__(self):
        from gpiozero import DigitalInputDevice, OutputDevice
        self.rows = [DigitalInputDevice(pin, pull_up=True) for pin in self.ROW_PINS]
        self.columns = [OutputDevice(pin, initial_value=True) for pin in self.COLUMN_PINS]

    def read(self):
        """The number (1-16) of the button pressed right now, or None."""
        for c, column in enumerate(self.columns):
            column.off()                    # turn this column on (LOW)
            for r, row in enumerate(self.rows):
                if row.is_active:           # this row went LOW: that button is pressed
                    column.on()
                    return r * 4 + c + 1
            column.on()
        return None

    def wait_for_press(self):
        """Wait until a button is pressed. Returns its number (1-16)."""
        while True:
            number = self.read()
            if number:
                return number
            time.sleep(0.02)

    def wait_for_release(self):
        """Wait until no button is pressed."""
        while self.read():
            time.sleep(0.02)

    @staticmethod
    def row_and_column(number):
        """Button 1-16 -> (row 0-3, column 0-3). Button 1 is row 0, column 0."""
        return divmod(number - 1, 4)

    def close(self):
        for device in self.rows + self.columns:
            device.close()


# ==================================================================
#  Camera  (finds a USB camera or a ribbon/CSI camera by itself)
# ==================================================================
PHOTO_FOLDER = os.path.expanduser("~/Pictures/CrowPi_Photos")


def find_usb_camera():
    """The /dev/video... name of a USB camera, or None."""
    import glob
    for folder in sorted(glob.glob("/sys/class/video4linux/video*")):
        driver = os.path.realpath(os.path.join(folder, "device", "driver"))
        if not driver.endswith("uvcvideo"):
            continue
        try:
            with open(os.path.join(folder, "index"), encoding="utf-8") as f:
                if f.read().strip() != "0":
                    continue
        except OSError:
            pass
        return "/dev/" + os.path.basename(folder)
    return None


_ribbon_tool = ""                     # remembered after the first search


def find_ribbon_camera_tool():
    """The photo program for a ribbon (CSI) camera if one is connected, else None."""
    global _ribbon_tool
    if _ribbon_tool:
        return _ribbon_tool
    import shutil
    import subprocess
    for still, hello in [("rpicam-still", "rpicam-hello"), ("libcamera-still", "libcamera-hello")]:
        if shutil.which(still) and shutil.which(hello):
            try:
                result = subprocess.run([hello, "--list-cameras"], capture_output=True,
                                        text=True, timeout=15)
                if "Available cameras" in (result.stdout + result.stderr):
                    _ribbon_tool = still
                    return still
            except (OSError, subprocess.TimeoutExpired):
                pass
    return None


def take_photo(filename=None, preview=False):
    """Take one photo and return its file name.
    Photos are saved in the folder Pictures/CrowPi_Photos."""
    import shutil
    import subprocess
    import datetime
    os.makedirs(PHOTO_FOLDER, exist_ok=True)
    if filename is None:
        filename = datetime.datetime.now().strftime("photo_%Y-%m-%d_%H-%M-%S.jpg")
    path = filename if os.path.isabs(filename) else os.path.join(PHOTO_FOLDER, filename)

    device = find_usb_camera()
    if device:
        try:
            import cv2
            camera = cv2.VideoCapture(device)
            frame = None
            for _ in range(8):                  # let the camera adjust its light first
                ok, picture = camera.read()
                if ok:
                    frame = picture
            camera.release()
            if frame is not None and cv2.imwrite(path, frame):
                return path
        except ImportError:
            pass
        if shutil.which("fswebcam"):
            subprocess.run(["fswebcam", "-d", device, "-r", "1280x720", "--no-banner",
                            "-S", "10", path], capture_output=True, timeout=30)
        elif shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "v4l2", "-i", device,
                            "-frames:v", "1", path], capture_output=True, timeout=30)
        if os.path.exists(path):
            return path
        raise KitProblem("The USB camera was found but no photo was taken.")

    tool = find_ribbon_camera_tool()
    if tool:
        command = [tool, "-t", "3000" if preview else "1000", "-o", path]
        if not preview:
            command.append("-n")                # no preview window
        subprocess.run(command, capture_output=True, timeout=40)
        if os.path.exists(path):
            return path
        raise KitProblem("The camera was found but no photo was taken.")

    raise KitProblem("No camera found. Run check_kit.py - it lists the cameras this kit "
                     "can see. If none is listed, check the camera cable.")


if __name__ == "__main__":
    print("crowpi_kit.py is the helper file for the CrowPi Club lessons.")
    print("Open a lesson file instead (for example buzzer.py).")
    finish()
