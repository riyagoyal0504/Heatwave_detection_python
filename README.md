# Climate Intelligence for Heatwave Monitoring, Prediction and Early Warning

A website that **predicts the heat wave for today, tomorrow and the day after
tomorrow** for 7 Indian cities, learning from each city's own weather history
(1990 to 2022). Data lives in **SQLite and is handled with plain SQL (no pandas)**.
Pick a day to open its own page with a written summary, charts, a city comparison
and a downloadable report. An **Arduino alert station (Tinkercad)** is driven from
the Python backend and is not shown on the website.

```
heatwave_project/
├── app.py                        the website (Streamlit)
├── heatwave/
│   ├── db.py                     SQLite schema, CSV loader, every SQL query
│   ├── model.py                  the prediction (records mode + history mode)
│   ├── report.py                 written summaries and the downloadable HTML report
│   ├── alerts.py                 IMD rules, alert levels, advice
│   └── station.py                backend link to the Arduino station
├── arduino/heatwave_station.ino  sketch for the Tinkercad / Arduino Uno station
├── station/                      hand-over folder for the Arduino messages
├── data/                         the CSV files (7 cities + station coordinates)
├── requirements.txt
└── README.md
```
`heatwave.db` is created automatically on the first run (about 2 seconds).

---

## 1. Setup and run

Python 3.10+ recommended.

**macOS / Linux**
```bash
cd heatwave_project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
**Windows (Command Prompt)**
```bat
cd heatwave_project
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
Open <http://localhost:8501>. Stop with `Ctrl + C`. To rebuild the database, delete
`heatwave.db` and restart.

## 2. The website

**Sidebar**
* **City**.
* **Prediction for:** *Overview (all 3 days)*, *Today*, *Tomorrow* or *Day after tomorrow*.
* **Auto-refresh** and **Refresh every (seconds)** (1 to 60). Each refresh recomputes the
  prediction, re-reads the live station reading (if any), moves on when the date changes at
  midnight, and shows "last updated". The weather data itself is historical, so on its own
  nothing changes between refreshes; it matters when a live reading is being fed in (section 6).
* **Advanced: predict from another date.** Off by default (the site predicts from today).
  Use it to try a heat-season date, see section 3.

**Overview page:** an outlook sentence for the 3 days, three cards (expected maximum, likely
range, **chance of heat wave**, status), **one chart of the next 3 days only** (nothing from past
years is drawn), a table of all cities, early-warning advice, a CSV of all cities and a
**printable report** for the 3 days.

**Day page (Today / Tomorrow / Day after tomorrow):** the status banner, a **written summary**
in plain language, a **gauge** of the chance of a heat wave, a **chart** of that one day (likely
range, expected maximum, normal, heat-wave and severe levels), all cities on that day, what to do,
a **report** for that day, and an explanation of how reliable the prediction is.

**Report:** one self-contained HTML file (summary table, a chart and written explanation per day,
advice, method and limitations). Open it and use *Print, Save as PDF* for a PDF copy.

**History** (kept): heat-wave days per year, a table of the most recent heat-wave episodes
(runs of at least 2 consecutive days at the heat-wave level) and a CSV download.

## 3. How the prediction works

The weather data ends in July 2022 (September for Bhubaneswar and Rourkela), so the site chooses
between two modes automatically and **tells you which one it used**:

1. **Records mode** (the date is inside the data, in March to June, with records for the last
   few days). Hot spells persist, so for each city the model learns from its **earlier years
   only** how the next 1 to 6 days depend on how far the latest days ran above normal (numpy
   least squares). The scatter of the fit turns the prediction into a chance:
   `departure(L days later) = c0 + c1 * a1 + c2 * a3`, `predicted max = normal + departure`,
   `chance = P(max >= heat-wave level)`. The training table is built in SQL
   (`LEAD` / `AVG ... OVER` window functions, `db.training_rows`).
2. **History mode** (anything else, including today's real date). With no recent records the
   prediction is the **typical maximum for the date**, and the chance is **how often that time of
   year (same date, plus or minus 3 days) reached the heat-wave level in earlier years**,
   counted directly in SQL. It has **no skill beyond the normal**; it cannot tell whether this
   week is hotter than usual.

Only earlier years are ever used (a prediction for a past date never sees its own future).

**Records mode, tested on held-out seasons 2008 to 2021** (six cities, trained on earlier years only):

| | Today | Tomorrow | Day after |
|---|---|---|---|
| Average temperature error | 1.1 °C | 1.4 °C | 1.6 °C |
| Error using just the normal | 1.8 °C | 1.8 °C | 1.8 °C |
| Heat-wave days caught (chance >= 15 %) | 69 % | 40 % | 22 % |
| Share of "possible" days that were real | 36 % | 28 % | 25 % |

The stated chances were reliable: a 5 to 10 % chance came true 5 to 7 % of the time, 10 to 20 %
came true 10 to 16 %. Of 64 "likely / severe" predictions for the same day (2000 to 2022), 41
(64 %) did reach the heat-wave level that day.

**History mode, same held-out test:** the average stated chance was 2.6 % while 3.2 % of days
really reached the heat-wave level, so it leans slightly low (recent years are hotter).

**Be honest in your report:** these are simple statistical models, not weather forecasts. They
are good for temperature, weaker at catching heat-wave days two and three days ahead, and tend
to under-predict extremes. Heat waves are rare, so **today's real date will almost always say
"no heat wave expected"** (it is the off-season in October, and history mode never exceeds a few
percent even in May). To see the model react, use *Advanced: predict from another date*:

| City | Date | What the site predicts for that day | What actually happened |
|---|---|---|---|
| Bhubaneswar | 18 Jun 2005 | **severe heat wave likely** (97 %) | 45.8 °C |
| Bhubaneswar | 7 Jun 2012 | **severe heat wave likely** (89 %) | 45.6 °C |
| Delhi | 5 Jun 2017 | **heat wave likely** (73 %) | 47.1 °C |
| Lucknow | 24 Jun 2009 | **heat wave likely** (78 %) | 44.5 °C |
| Delhi | 17 May 2017 | heat wave **possible** all 3 days (26 / 21 / 18 %) | - |

(These were picked because the model flagged them; it also issues "likely" for days that turned
out not to be, for example Delhi 4 Apr 2016, so show a couple of each.)

## 4. The database (SQL)

| Object | Purpose |
|---|---|
| `cities` | city, station, IMD station type and threshold, coordinates |
| `daily_obs` | one row per city per day: tavg, tmin, tmax, prcp |
| `normals` | average tmax for each calendar day (plus or minus 7 days, all years), per city |
| `load_log` | rows read / loaded / rejected per CSV |
| view `v_raw_status` | observations + normals + IMD thresholds + same-day level |

SQL features used: a recursive CTE and circular day-of-year join (normals), window functions
(`LEAD`, `AVG ... OVER`, `ROW_NUMBER`) for the training table and the 2-day confirmation, a
"gaps and islands" query for heat-wave episodes, and `COUNT(DISTINCT ...)` for "in X of N years".

## 5. IMD alert rules

Based on the **daily maximum**. Station thresholds: plains 40 °C (Delhi, Lucknow, Bangalore,
Rourkela), coastal 37 °C (Mumbai, Chennai, Bhubaneswar; an assumption, editable in `db.py`).
**Heat Wave:** threshold reached and departure from normal >= 4.5 °C. **Severe:** departure
>= 6.5 °C. **Absolute:** >= 45 °C heat wave, >= 47 °C severe.
Status on the site: chance < 15 % none, 15 to 50 % possible, >= 50 % likely; severe if the chance
of the severe level is >= 50 %.

## 6. The hardware station (Tinkercad / Arduino), backend only

The website never mentions it. The station has three jobs:

* **Sense:** a DHT22 reads local temperature and humidity every 2 s, judges it with the IMD
  levels, and prints `millis,temperature,humidity` once per reading.
* **Warn:** the backend sends the 3-day prediction as one serial line,
  `F,<level today>,<level tomorrow>,<level day after>,<t0>,<t1>,<t2>` (example `F,1,2,2,44,46,47`).
  The LCD alternates between the local reading and the forecast, the LEDs show the **worst** of the
  local and forecast level (green normal, yellow heat wave, red severe), the buzzer sounds **only on
  severe**, and a pushbutton on a hardware interrupt mutes it.
* **Report back:** paste the Serial Monitor text into `station/from_arduino.txt`; the website
  then uses the highest reading as a floor for **today's** predicted maximum (a day's maximum
  cannot be lower than a temperature already measured) and shows "Live reading from the local
  sensor station" on the page. This only happens when predicting from today's real date, and is
  what the refresh interval re-checks.

**How the backend reaches the Arduino.** Every time the prediction changes, `heatwave/station.py`
writes the line to `station/to_arduino.txt`.
* **Tinkercad** cannot open a COM port, so while the simulation runs, copy that line into the
  Serial Monitor's send box. (If your Tinkercad has no send box, set `PRELOAD_FORECAST` to 1 in
  the sketch to start with a sample forecast.)
* **A real Arduino** receives it automatically over USB. Set the port before starting:
  `export HEATWAVE_SERIAL_PORT=/dev/cu.usbmodem1101` (macOS/Linux) or
  `set HEATWAVE_SERIAL_PORT=COM3` (Windows). `HEATWAVE_SERIAL_BAUD` is optional (default 9600).

### Parts
Arduino Uno, DHT22, LCD 16x2, green / yellow / red LEDs, piezo buzzer, 1 pushbutton, resistors:
3 x 220 Ω (LEDs), 1 x 220 Ω (LCD backlight), 1 x 10 kΩ (DHT22 pull-up, only for a 4-pin DHT22).

### Wiring

| Part / pin | Connects to | Resistor |
|---|---|---|
| DHT22 VCC (+) | 5V | |
| DHT22 DATA (out) | **D7** | 10 kΩ from DATA to 5V (4-pin DHT22 only) |
| DHT22 GND (−) | GND | |
| LCD VSS (GND), RW, K (backlight −) | GND | |
| LCD VDD (VCC) | 5V | |
| LCD VO (contrast) | GND (or wiper of a 10 kΩ pot between 5V and GND) | |
| LCD A (backlight +) | 5V | 220 Ω in series |
| LCD RS / E | **D12** / **D11** | |
| LCD D4 / D5 / D6 / D7 | **D10** / **D9** / **D8** / **D13** | |
| LCD D0 to D3 | not connected | |
| Green LED anode | **D3** | 220 Ω in series |
| Yellow LED anode | **D4** | 220 Ω in series |
| Red LED anode | **D5** | 220 Ω in series |
| All LED cathodes | GND | |
| Piezo + / − | **D6** / GND | optional 100 Ω in series |
| Pushbutton | **D2** (INT0) and GND | none (`INPUT_PULLUP`) |

Tinkercad pin labels vary slightly; match by function.

### Build steps
1. New Circuit: Arduino Uno + small breadboard; 5V to the `+` rail, GND to the `−` rail.
2. Wire the LCD, LEDs (with resistors), buzzer, button and DHT22 as in the table.
3. **Code**, *Text*, paste `arduino/heatwave_station.ino`, **Start Simulation**.
4. Drag the DHT22 temperature slider up: LCD, LEDs and buzzer react (local alert).
5. Open the Serial Monitor and send a line such as `F,1,2,2,44,46,47`: the LCD now alternates with
   a forecast page, and a level of 2 lights the red LED and sounds the buzzer.
6. Press the button to mute the buzzer. Copy the Serial Monitor text into
   `station/from_arduino.txt` to feed the live reading back to the website.

If Tinkercad has no DHT22 or rejects `<DHT.h>`, set `USE_DHT22` to 0 and use a **TMP36** (A0) plus
a **potentiometer** (A1 as humidity). **The sketch was type-checked and its message parser and
alert logic tested on a PC, but it has not been compiled with the AVR toolchain or run in Tinkercad;
build it there first and fix anything it reports.**

### How the I/O path works
* **Sensor:** the DHT22 sends temperature and humidity on one wire (D7) as a 40-bit pulse train that
  the DHT library decodes; it can only be read about every 2 s, so the sketch samples with a
  `millis()` timer instead of `delay()`.
* **Interrupt:** the button is on D2 (INT0). `attachInterrupt()` jumps to `onMutePressed()` the
  instant the pin falls; it only sets a `volatile` flag, and `loop()` then silences the buzzer.
* **Serial:** UART at 9600 baud. Arduino to PC: CSV lines. PC to Arduino: the `F,...` line.

## 7. Data notes and limitations

* Source: the CSV files supplied (daily max/min/average temperature, rainfall). **There is no
  humidity in the data**, so no humidity or heat index is shown.
* **The records end in 2022**, so for today's date there are no recent records (history mode).
* Normals come from this dataset (1990 to 2022), not IMD's official normals; the recent warm years
  push them up, so alerts are somewhat fewer than IMD's. They include every year, so there is a
  small leak of later years into the normals themselves (the predictions otherwise use earlier
  years only).
* **Rourkela** has only about 14 months of data: no normal and no prediction.
* **Jodhpur** is not included: the file supplied is byte-for-byte identical to the Bangalore file.
  Two labels in the coordinates file were swapped and are corrected by the loader.
* Station types are an assumption. Bangalore never reaches 40 °C, so it never reaches the
  heat-wave level.
* 4 to 16 % of days have no max temperature; records mode then uses the last record with a
  longer lead, and gives up after a gap of 4 days or more.
