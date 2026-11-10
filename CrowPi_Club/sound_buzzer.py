#!/usr/bin/env python3
# ==================================================================
#  CrowPi Club - PI SOUND THROUGH THE BUZZER
#
#  Started by the "Sound > buzzer" switch in CrowPi Lessons.
#  It listens to whatever the Pi is playing (YouTube, music, games...)
#  and copies the main note onto the buzzer (GPIO18) - loud parts
#  buzz, quiet parts are silent. A buzzer can only play one note at a
#  time, so music sounds like an old video game!
#
#  It stops when the switch is turned off, when a lesson starts (the
#  lesson needs the buzzer) or when CrowPi Lessons closes.
# ==================================================================
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import time

BUZZER_PIN = 18
RATE = 16000                 # samples per second we listen at
BLOCK = 1024                 # samples per look (= 64 ms)
LOWEST, HIGHEST = 120, 2500  # notes the buzzer can play well (Hz)
QUIET = 250                  # quieter than this = silence
PID_FILE = os.path.join(os.path.expanduser("~"), ".crowpi_sound_buzzer.pid")   # lessons stop us with it

# Ways to listen to what the Pi plays (the "monitor" of the speaker output)
RECORDERS = [
    ["parec", "-d", "@DEFAULT_MONITOR@", "--format=s16le", "--rate=%d" % RATE, "--channels=1",
     "--raw", "--latency-msec=40"],
    ["pw-record", "-P", "stream.capture.sink=true", "--rate", str(RATE), "--channels", "1",
     "--format", "s16", "-"],
]

import importlib

try:
    numpy = importlib.import_module("numpy")
except ImportError:          # (works without numpy too, a bit less exactly)
    numpy = None


def main_note(samples):
    """(frequency in Hz, loudness) of a block of samples."""
    if numpy is not None:
        data = numpy.frombuffer(samples, dtype="<i2").astype(numpy.float32)
        loud = float(numpy.sqrt(numpy.mean(data * data))) if len(data) else 0.0
        if loud < QUIET:
            return 0.0, loud
        spectrum = numpy.abs(numpy.fft.rfft(data * numpy.hanning(len(data))))
        freqs = numpy.fft.rfftfreq(len(data), 1.0 / RATE)
        band = (freqs >= LOWEST) & (freqs <= HIGHEST)
        if not band.any():
            return 0.0, loud
        peak = int(numpy.argmax(numpy.where(band, spectrum, 0)))
        return float(freqs[peak]), loud
    count = len(samples) // 2
    data = struct.unpack("<%dh" % count, samples[:count * 2])
    loud = math.sqrt(sum(x * x for x in data) / max(1, count))
    if loud < QUIET:
        return 0.0, loud
    crossings = sum(1 for a, b in zip(data, data[1:]) if (a < 0) != (b < 0))
    return crossings * RATE / (2.0 * max(1, count)), loud


def start_recorder():
    for command in RECORDERS:
        if not shutil.which(command[0]):
            continue
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            continue
        time.sleep(0.3)
        if process.poll() is None:
            return process
    return None


def main():
    recorder = start_recorder()
    if recorder is None:
        print("Can't listen to the Pi's sound (no parec or pw-record).")
        return 1
    from gpiozero import PWMOutputDevice
    buzzer = PWMOutputDevice(BUZZER_PIN, frequency=440, initial_value=0)
    running = {"on": True}

    def stop(*_args):
        running["on"] = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    playing = 0.0
    print("Pi sound -> buzzer: ON")
    try:
        while running["on"]:
            samples = recorder.stdout.read(BLOCK * 2) if recorder.stdout else b""
            if not samples:
                break
            note, loud = main_note(samples)
            if not note or not (LOWEST <= note <= HIGHEST):
                if buzzer.value:
                    buzzer.value = 0
                playing = 0.0
                continue
            if not playing or abs(note - playing) / playing > 0.03:        # a new note
                buzzer.frequency = round(note)
                playing = note
            buzzer.value = 0.5 if loud > QUIET * 4 else 0.25                # louder = stronger
    finally:
        try:
            buzzer.value = 0
            buzzer.close()
        except Exception:
            pass
        recorder.terminate()
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
    print("Pi sound -> buzzer: OFF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
