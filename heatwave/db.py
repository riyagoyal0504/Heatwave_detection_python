"""SQLite database: schema, loading the CSV files, and every SQL query.

All data handling goes through SQL (sqlite3 + csv from the standard library).
No pandas is used anywhere in this project.

Tables
------
cities       one row per city (station name, IMD station type, coordinates)
daily_obs    one row per city per day: tavg, tmin, tmax, prcp
normals      long-term average tmax for every calendar day (1..366), per city
load_log     what happened while loading each CSV (rows read / kept / rejected)

View
----
v_raw_status joins observations with normals and applies the IMD rules in SQL,
             giving each day its thresholds and its single-day alert level.

Run  `python -m heatwave.db`  to (re)build the database and print a summary.
"""
import csv
import os
import re
import sqlite3
import sys
from datetime import date, timedelta

from . import alerts

SCHEMA_VERSION = 1
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DB_PATH = os.path.join(PROJECT_DIR, "heatwave.db")

# ---- the cities (one CSV each) -------------------------------------------------
# (city, csv file, station name, IMD station type)
# NOTE: the station type is an assumption: Mumbai, Chennai and Bhubaneswar are
# treated as coastal (37 C threshold), the rest as plains (40 C). Change here
# and rebuild the database if your faculty prefers another classification.
CITY_SOURCES = [
    ("Delhi",       "Delhi_NCR_1990_2022_Safdarjung.csv",     "Safdarjung",     "Plains"),
    ("Lucknow",     "Lucknow_1990_2022.csv",                  "Lucknow",        "Plains"),
    ("Mumbai",      "Mumbai_1990_2022_Santacruz.csv",         "Santacruz",      "Coastal"),
    ("Chennai",     "Chennai_1990_2022_Madras.csv",           "Madras",         "Coastal"),
    ("Bangalore",   "Bangalore_1990_2022_BangaloreCity.csv",  "Bangalore City", "Plains"),
    ("Bhubaneswar", "weather_Bhubhneshwar_1990_2022.csv",     "Bhubaneswar",    "Coastal"),
    ("Rourkela",    "weather_Rourkela_2021_2022.csv",         "Rourkela",       "Plains"),
]

# Station coordinates file. Two labels in it are wrong, so they are corrected
# here: the row called "Bangalore" has Jodhpur's coordinates (26.3 N, 73.0 E)
# and the row called "Rajastan" has Bangalore's (12.97 N, 77.58 E). The
# Bhubaneswar spelling is also normalised.
GEO_FILE = "Station_GeoLocation_Longitute_Latitude_Elevation_EPSG_4326.csv"
GEO_NAME_FIX = {"Bangalore": "Jodhpur", "Rajastan": "Bangalore",
                "Bubhneshwar": "Bhubaneswar"}

# Plausible physical limits; anything outside is treated as a bad value (NULL)
TEMP_LIMITS = (-10.0, 55.0)
PRCP_LIMITS = (0.0, 1500.0)

# Normals: average tmax of the same calendar day +/- 7 days, over all years
NORMAL_HALF_WINDOW = 7
MIN_NORMAL_OBS = 150          # about 10 years x 15 days; below this: no normal

# The heat season: the prediction model is trained on these days only
SEASON_START = (3, 1)         # 1 March
SEASON_END = (6, 30)          # 30 June

# "How hot has this date been?" looks at the same date +/- 3 days in every year
HISTORY_HALF_WINDOW = 3

SCHEMA = """
CREATE TABLE cities (
    city_id      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    station_name TEXT NOT NULL,
    station_type TEXT NOT NULL CHECK (station_type IN ('Plains', 'Coastal', 'Hilly')),
    threshold    REAL NOT NULL,
    latitude     REAL,
    longitude    REAL,
    elevation_m  REAL,
    source_file  TEXT NOT NULL
);

CREATE TABLE daily_obs (
    city_id  INTEGER NOT NULL REFERENCES cities(city_id),
    obs_date TEXT    NOT NULL,            -- ISO date, YYYY-MM-DD
    year     INTEGER NOT NULL,
    doy      INTEGER NOT NULL,            -- day of year, 1..366
    tavg REAL, tmin REAL, tmax REAL, prcp REAL,
    PRIMARY KEY (city_id, obs_date)
) WITHOUT ROWID;

CREATE INDEX idx_obs_city_year ON daily_obs (city_id, year);

CREATE TABLE normals (
    city_id     INTEGER NOT NULL REFERENCES cities(city_id),
    doy         INTEGER NOT NULL,
    normal_tmax REAL,                     -- NULL when there is too little history
    n_obs       INTEGER NOT NULL,
    PRIMARY KEY (city_id, doy)
) WITHOUT ROWID;

CREATE TABLE load_log (
    source_file TEXT, rows_read INTEGER, rows_loaded INTEGER,
    bad_dates INTEGER, bad_values INTEGER, duplicates INTEGER
);
"""

# IMD rules in SQL. The numbers come from alerts.py so they exist only once.
VIEW_SQL = f"""
CREATE VIEW v_raw_status AS
WITH b AS (
    SELECT o.city_id, o.obs_date, o.year, o.doy, o.tavg, o.tmin, o.tmax, o.prcp,
           n.normal_tmax,
           CASE WHEN n.normal_tmax IS NULL THEN {alerts.ABSOLUTE_HEAT_WAVE}
                ELSE MIN(MAX(c.threshold, n.normal_tmax + {alerts.DEPARTURE_HEAT_WAVE}),
                         {alerts.ABSOLUTE_HEAT_WAVE}) END AS hw_thr,
           CASE WHEN n.normal_tmax IS NULL THEN {alerts.ABSOLUTE_SEVERE}
                ELSE MIN(MAX(c.threshold, n.normal_tmax + {alerts.DEPARTURE_SEVERE}),
                         {alerts.ABSOLUTE_SEVERE}) END AS sev_thr
    FROM daily_obs o
    JOIN cities c ON c.city_id = o.city_id
    LEFT JOIN normals n ON n.city_id = o.city_id AND n.doy = o.doy
)
SELECT city_id, obs_date, year, doy, tavg, tmin, tmax, prcp, normal_tmax,
       tmax - normal_tmax AS departure,
       hw_thr, sev_thr,
       CASE WHEN tmax IS NULL THEN NULL
            WHEN tmax >= sev_thr THEN 2
            WHEN tmax >= hw_thr  THEN 1
            ELSE 0 END AS raw_level
FROM b;
"""

NORMALS_SQL = """
WITH per_doy AS (
    SELECT city_id, doy, SUM(tmax) AS s, COUNT(tmax) AS n
    FROM daily_obs GROUP BY city_id, doy
),
days(doy) AS (SELECT 1 UNION ALL SELECT doy + 1 FROM days WHERE doy < 366)
INSERT INTO normals (city_id, doy, normal_tmax, n_obs)
SELECT c.city_id, days.doy,
       CASE WHEN SUM(p.n) >= :min_obs THEN SUM(p.s) / SUM(p.n) END,
       COALESCE(SUM(p.n), 0)
FROM cities c
CROSS JOIN days
LEFT JOIN per_doy p
       ON p.city_id = c.city_id
      AND MIN(ABS(p.doy - days.doy), 366 - ABS(p.doy - days.doy)) <= :half
GROUP BY c.city_id, days.doy
"""


# =============================================================================
# Building the database from the CSV files
# =============================================================================
def _parse_date(text):
    """ISO (2022-04-01) or day-first (01-04-2022) -> ISO string, else None."""
    text = text.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
        if re.fullmatch(r"\d{2}-\d{2}-\d{4}", text):
            d, m, y = (int(p) for p in text.split("-"))
            return date(y, m, d).isoformat()
    except ValueError:
        pass
    return None


def _number(text, limits):
    """Blank / non-numeric -> (None, False). Out of range -> (None, True)."""
    text = (text or "").strip()
    if text == "":
        return None, False
    try:
        value = float(text)
    except ValueError:
        return None, True
    if value != value or not (limits[0] <= value <= limits[1]):
        return None, True
    return value, False


def _read_geo(data_dir):
    """{city name: (lat, lon, elevation)} from the coordinates file, labels fixed."""
    geo = {}
    path = os.path.join(data_dir, GEO_FILE)
    if not os.path.exists(path):
        return geo
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = GEO_NAME_FIX.get(row["Location_Name"].strip(), row["Location_Name"].strip())
            elev = row.get("Elevation", "").strip()
            geo[name] = (float(row["Latitude"]), float(row["longitude"]),
                         float(elev) if elev else None)
    return geo


def build_database(db_path=DB_PATH, data_dir=DATA_DIR):
    """Create the database from the CSV files. Returns a list of load_log rows."""
    tmp_path = db_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    conn = sqlite3.connect(tmp_path)
    conn.executescript(SCHEMA)
    geo = _read_geo(data_dir)
    log = []

    for city_id, (name, filename, station, station_type) in enumerate(CITY_SOURCES, start=1):
        path = os.path.join(data_dir, filename)
        if not os.path.exists(path):
            print(f"WARNING: {filename} not found, skipping {name}", file=sys.stderr)
            continue
        lat, lon, elev = geo.get(name, (None, None, None))
        conn.execute("INSERT INTO cities VALUES (?,?,?,?,?,?,?,?,?)",
                     (city_id, name, station, station_type,
                      alerts.STATION_THRESHOLDS[station_type], lat, lon, elev, filename))

        read = loaded = bad_dates = bad_values = dups = 0
        batch = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                read += 1
                iso = _parse_date(row.get("time", ""))
                if iso is None:
                    bad_dates += 1
                    continue
                values = []
                for col, limits in (("tavg", TEMP_LIMITS), ("tmin", TEMP_LIMITS),
                                    ("tmax", TEMP_LIMITS), ("prcp", PRCP_LIMITS)):
                    v, bad = _number(row.get(col), limits)
                    bad_values += bad
                    values.append(v)
                d = date.fromisoformat(iso)
                batch.append((city_id, iso, d.year, d.timetuple().tm_yday, *values))
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO daily_obs VALUES (?,?,?,?,?,?,?,?)", batch)
        loaded = conn.total_changes - before
        dups = len(batch) - loaded
        entry = (filename, read, loaded, bad_dates, bad_values, dups)
        conn.execute("INSERT INTO load_log VALUES (?,?,?,?,?,?)", entry)
        log.append(entry)

    conn.execute(NORMALS_SQL, {"min_obs": MIN_NORMAL_OBS, "half": NORMAL_HALF_WINDOW})
    conn.executescript(VIEW_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    conn.close()
    os.replace(tmp_path, db_path)
    return log


def ensure_database(db_path=DB_PATH, data_dir=DATA_DIR):
    """Build the database on first run (or when the schema version changed)."""
    rebuild = not os.path.exists(db_path)
    if not rebuild:
        conn = sqlite3.connect(db_path)
        rebuild = conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        conn.close()
    if rebuild:
        build_database(db_path, data_dir)
    return db_path


def connect(db_path=DB_PATH):
    """Open a connection whose rows behave like dictionaries."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# Queries used by the dashboard
# =============================================================================
def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_cities(conn):
    return _rows(conn, "SELECT * FROM cities ORDER BY name")




def _confirmed_cte(confirm_days):
    """SQL that adds the CONFIRMED level to each day.

    A day's confirmed level is the lowest single-day level over the last N
    days (IMD: criteria must hold on consecutive days). If any of those N days
    has no tmax record, the level is NULL (not enough data to confirm).
    """
    n = int(confirm_days)                       # forced to int: safe to put in SQL
    return f"""
    conf AS (
        SELECT *,
               CASE WHEN COUNT(raw_level) OVER w = {n}
                    THEN MIN(raw_level) OVER w END AS level
        FROM v_raw_status
        WHERE city_id = :city AND obs_date BETWEEN :warm AND :end
        WINDOW w AS (ORDER BY obs_date ROWS BETWEEN {n - 1} PRECEDING AND CURRENT ROW)
    )"""


def season_status(conn, city_id, start, end, confirm_days):
    """Every day from `start` to `end` with normals, thresholds and levels."""
    warm = start - timedelta(days=10)           # so the first days have a full window
    sql = ("WITH " + _confirmed_cte(confirm_days) +
           " SELECT * FROM conf WHERE obs_date >= :start ORDER BY obs_date")
    return _rows(conn, sql, {"city": city_id, "warm": warm.isoformat(),
                             "end": end.isoformat(), "start": start.isoformat()})




def training_rows(conn, city_id, before_year):
    """Training table for the prediction model, built entirely in SQL.

    One row per past in-season day (years BEFORE `before_year` only, so the
    model never sees the future):
      a1      how far the day's max ran above/below normal (departure)
      a3      average departure over that day and the 2 days before it
      y1..y6  the departure 1 to 6 days LATER (what the model learns to predict)
    """
    leads = ", ".join(f"LEAD(departure, {k}) OVER w AS y{k}" for k in range(1, 7))
    sm, sd = SEASON_START
    em, ed = SEASON_END
    sql = f"""
        WITH f AS (
            SELECT obs_date, year, departure AS a1,
                   AVG(departure) OVER w3 AS a3, {leads}
            FROM v_raw_status WHERE city_id = :city
            WINDOW w  AS (ORDER BY obs_date),
                   w3 AS (ORDER BY obs_date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
        )
        SELECT * FROM f
        WHERE year < :before AND a1 IS NOT NULL
          AND substr(obs_date, 6) BETWEEN :s AND :e"""
    return _rows(conn, sql, {"city": city_id, "before": before_year,
                             "s": f"{sm:02d}-{sd:02d}", "e": f"{em:02d}-{ed:02d}"})


def date_history(conn, city_id, doy, hw, sev, before_year=None, half=HISTORY_HALF_WINDOW):
    """How hot has this time of year been? (all years on record, +/- `half` days)

    Returns the sorted list of daily maxima in that window, how many days and
    how many distinct years reached the heat-wave / severe level, and the span
    of years. Everything is selected with SQL. With `before_year`, only EARLIER
    years are used, so a prediction for a past date never counts its own future.
    """
    where = """city_id = :c AND tmax IS NOT NULL
               AND (:before IS NULL OR year < :before)
               AND MIN(ABS(doy - :doy), 366 - ABS(doy - :doy)) <= :half"""
    p = {"c": city_id, "doy": doy, "half": half, "hw": hw, "sev": sev, "before": before_year}
    values = [r[0] for r in conn.execute(
        f"SELECT tmax FROM daily_obs WHERE {where} ORDER BY tmax", p)]
    row = conn.execute(f"""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(tmax >= :hw), 0)  AS hw_days,
               COALESCE(SUM(tmax >= :sev), 0) AS sev_days,
               COUNT(DISTINCT year) AS years,
               COUNT(DISTINCT CASE WHEN tmax >= :hw  THEN year END) AS years_hw,
               COUNT(DISTINCT CASE WHEN tmax >= :sev THEN year END) AS years_sev,
               MIN(year) AS first_year, MAX(year) AS last_year
        FROM daily_obs WHERE {where}""", p).fetchone()
    out = dict(row)
    out["values"] = values
    return out


def past_episodes(conn, city_id, min_days, limit=15):
    """The most recent heat-wave episodes on record for a city.

    An episode is a run of at least `min_days` consecutive days that each
    reached the heat-wave level (IMD: criteria on consecutive days). Classic
    SQL 'gaps and islands': the date minus the row number is constant inside
    a run of consecutive days, so grouping on it finds each run.
    """
    sql = """
        WITH hot AS (
            SELECT obs_date, tmax, raw_level, normal_tmax,
                   julianday(obs_date) - ROW_NUMBER() OVER (ORDER BY obs_date) AS grp
            FROM v_raw_status WHERE city_id = :city AND raw_level >= 1
        )
        SELECT MIN(obs_date) AS start_date, MAX(obs_date) AS end_date,
               COUNT(*) AS days, MAX(tmax) AS peak_tmax,
               ROUND(MAX(tmax - normal_tmax), 1) AS peak_departure,
               MAX(raw_level) AS worst_level
        FROM hot GROUP BY grp HAVING COUNT(*) >= :min_days
        ORDER BY start_date DESC LIMIT :limit"""
    return _rows(conn, sql, {"city": city_id, "min_days": int(min_days), "limit": limit})



def get_normals(conn, city_id, dates):
    """{date: normal tmax or None} for arbitrary dates (used for the forecast days)."""
    table = {r["doy"]: r["normal_tmax"] for r in conn.execute(
        "SELECT doy, normal_tmax FROM normals WHERE city_id = ?", (city_id,))}
    return {d: table.get(d.timetuple().tm_yday) for d in dates}


def yearly_heat_days(conn, city_id):
    """Per year: days meeting the heat-wave / severe criteria (single-day basis)."""
    return _rows(conn, """
        SELECT year,
               COUNT(raw_level)          AS days_with_data,
               SUM(raw_level >= 1)       AS heat_days,
               SUM(raw_level = 2)        AS severe_days
        FROM v_raw_status WHERE city_id = ?
        GROUP BY year ORDER BY year""", (city_id,))


def data_quality(conn):
    """One row per city: coverage and missing-value percentages."""
    return _rows(conn, """
        SELECT c.name AS City, c.station_name AS Station, c.station_type AS Type,
               MIN(o.obs_date) AS First_day, MAX(o.obs_date) AS Last_day,
               COUNT(*) AS Days,
               ROUND(100.0 * SUM(o.tmax IS NULL) / COUNT(*), 1) AS Missing_tmax_pct,
               ROUND(100.0 * SUM(o.tmin IS NULL) / COUNT(*), 1) AS Missing_tmin_pct,
               (SELECT COUNT(*) FROM normals n
                 WHERE n.city_id = c.city_id AND n.normal_tmax IS NOT NULL) > 0
                   AS Has_normal
        FROM cities c
        JOIN daily_obs o ON o.city_id = c.city_id
        GROUP BY c.city_id ORDER BY c.name""")


if __name__ == "__main__":
    print("Building", DB_PATH)
    for entry in build_database():
        print("  %-42s read=%d loaded=%d bad_dates=%d bad_values=%d duplicates=%d" % entry)
    with connect() as c:
        for row in data_quality(c):
            print(" ", row)
