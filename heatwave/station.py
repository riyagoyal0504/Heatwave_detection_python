"""Backend link to the Arduino alert station (built and simulated in Tinkercad).

This is NOT shown anywhere on the website. Whenever the dashboard has a new
3-day prediction, the backend turns it into one short serial command and

  1. writes it to  station/to_arduino.txt
       -> with Tinkercad, paste this line into the Serial Monitor's send box
          (Tinkercad cannot open a COM port on your PC);
  2. sends it straight over USB if a real board is connected and the
       environment variable HEATWAVE_SERIAL_PORT is set (needs pyserial):
           macOS/Linux:  export HEATWAVE_SERIAL_PORT=/dev/cu.usbmodem1101
           Windows:      set HEATWAVE_SERIAL_PORT=COM3
       HEATWAVE_SERIAL_BAUD is optional (default 9600, must match the sketch).

The station can also talk BACK: copy the Serial Monitor text (lines such as
12000,41.5,32.0) into  station/from_arduino.txt  and the backend uses the highest
reading as a floor for TODAY's predicted maximum (see read_live below).

Message format (one line, 9600 baud):   F,<lvl today>,<lvl tomorrow>,<lvl day after>,<t0>,<t1>,<t2>
    lvl = 0 normal, 1 heat wave possible/likely, 2 severe heat wave likely
    t   = predicted max temperature, rounded to whole degrees C
Example:  F,1,2,2,44,46,47
The sketch in arduino/heatwave_station.ino reads it and shows it on the LCD,
LEDs and buzzer.
"""
import os
import time

from .db import PROJECT_DIR

FEED_PATH = os.path.join(PROJECT_DIR, "station", "to_arduino.txt")

_last_message = None
_serial = None


def build_message(levels, temps):
    """Turn three levels and three temperatures into the serial command."""
    lv = ",".join(str(int(v)) for v in levels)
    tp = ",".join(str(int(round(t))) for t in temps)
    return f"F,{lv},{tp}"


def _send_serial(message):
    """Send over USB if a port is configured. Never raises."""
    global _serial
    port = os.environ.get("HEATWAVE_SERIAL_PORT")
    if not port:
        return
    try:
        if _serial is None:
            import serial                              # only needed for a real board
            _serial = serial.Serial(port, int(os.environ.get("HEATWAVE_SERIAL_BAUD", "9600")),
                                    timeout=1)
            time.sleep(2)                              # opening the port resets the Arduino
        _serial.write((message + "\n").encode("ascii"))
    except Exception:
        _serial = None                                 # try again on the next change


def publish(levels, temps):
    """Send the prediction to the station, but only when it has changed."""
    global _last_message
    message = build_message(levels, temps)
    if message == _last_message:
        return message
    _last_message = message
    try:
        os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
        with open(FEED_PATH, "w") as f:
            f.write(message + "\n")
    except OSError:
        pass                                           # e.g. read-only folder: skip quietly
    _send_serial(message)
    return message


# ---- live readings coming FROM the station (optional, also backend only) ------------
LIVE_PATH = os.path.join(PROJECT_DIR, "station", "from_arduino.txt")


def read_live():
    """Latest and highest temperature in station/from_arduino.txt, or None.

    The station prints  millis,temperature,humidity  once per reading. Copy the
    Serial Monitor text into that file (Tinkercad cannot write files itself),
    or let any program append the lines when a real board is connected. Header
    lines and anything that is not a valid reading are ignored. The website
    uses this only to make sure TODAY's predicted maximum is never lower than a
    temperature that has already been measured.
    """
    import re
    try:
        with open(LIVE_PATH) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    temps = []
    for line in lines:
        m = re.match(r"^\s*\d+\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*$", line)
        if m and -40.0 < float(m.group(1)) < 80.0 and 0.0 <= float(m.group(2)) <= 100.0:
            temps.append(float(m.group(1)))
    if not temps:
        return None
    return {"latest": temps[-1], "max": max(temps), "n": len(temps),
            "updated": os.path.getmtime(LIVE_PATH)}
