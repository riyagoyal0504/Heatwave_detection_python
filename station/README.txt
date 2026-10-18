This folder is the hand-over point between the Python backend and the Arduino
alert station (built and simulated in Tinkercad). Nothing here is mentioned on
the website.

PYTHON -> ARDUINO   to_arduino.txt   (written by the dashboard)
  One line, rewritten whenever the 3-day prediction changes, e.g.   F,1,2,2,44,46,47
  F , level today , level tomorrow , level day after , temp today , temp tomorrow , temp day after
  level: 0 normal, 1 heat wave possible/likely, 2 severe heat wave likely
  Tinkercad cannot open a COM port on your PC, so copy this line into the Serial
  Monitor's send box while the simulation runs. With a real Arduino it is sent
  over USB automatically when HEATWAVE_SERIAL_PORT is set (see heatwave/station.py).

ARDUINO -> PYTHON   from_arduino.txt   (you create it; optional)
  Paste the Serial Monitor output into this file (lines like 12000,41.5,32.0).
  The website then knows the station's live reading: today's predicted maximum can
  never be lower than a temperature already measured. It is only used when the
  site is predicting from today's real date, and the website re-reads it on every
  refresh.

Neither file exists until you create / run something.
