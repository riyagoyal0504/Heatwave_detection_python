"""Heat-wave prediction for today, tomorrow and the day after tomorrow.

Two ways of predicting, chosen automatically
--------------------------------------------
1. RECORDS mode - used when the database holds temperature records for the last
   few days before the chosen date, and that date is in the heat season
   (1 March to 30 June). Hot spells persist, so for each city we learn from its
   OWN EARLIER YEARS how the next 1 to 6 days depend on how far the latest days
   ran above normal (ordinary least squares, numpy):

       departure(L days later) = c0 + c1 * a1 + c2 * a3          (L = 1 .. 6)

       departure = daily max temperature minus the normal max for that date
       a1        = departure of the latest recorded day
       a3        = average departure of that day and the 2 days before it

   predicted max = normal(target date) + predicted departure. The scatter of the
   fit (sigma) turns it into a chance of reaching the heat-wave level.
   It is trained only on years BEFORE the chosen date, so it never sees the future.

2. HISTORY mode - used otherwise (for example for today's real date, because the
   weather data ends in 2022). With no recent records the prediction is the
   typical (normal) maximum for the date, and the chance of a heat wave is how
   often that time of year (same date +/- 3 days) reached the heat-wave level in
   every year on record, counted directly with SQL.

Neither is a weather model. They know nothing about fronts, cloud, wind or
monsoon onset. Tested on held-out seasons 2008-2021 (six cities) the RECORDS
chances were well calibrated; temperature error beat "just use the normal" at
every lead, but catching heat-wave days 2 and 3 days ahead is much weaker than
for today. HISTORY mode has no skill beyond the normal by construction.
"""
import math
import os
from datetime import date, timedelta

import numpy as np

from . import alerts, db

MAX_LEAD = 6                # models for 1..6 days ahead (covers short data gaps)
MAX_GAP_DAYS = 3            # use records up to 3 days old (lead grows with the gap)
MIN_TRAIN_ROWS = 400        # need roughly 4+ earlier seasons of data
MIN_HISTORY_DAYS = 100      # minimum days of history around a date (history mode)
CONFIRM_DAYS = 2            # IMD: criteria on 2 consecutive days
POSSIBLE = 0.15             # chance at/above which a heat wave is "possible"
LIKELY = 0.50               # chance at/above which it is "likely"
RANGE_Z = 1.28              # +/- 1.28 sigma = an 80 % range
DAY_NAMES = ["Today", "Tomorrow", "Day after tomorrow"]

# Outlook status for one day
NONE, POSSIBLE_S, LIKELY_S, SEVERE_S = "none", "possible", "likely", "severe"
STATUS_TEXT = {NONE: "NO HEAT WAVE EXPECTED", POSSIBLE_S: "HEAT WAVE POSSIBLE",
               LIKELY_S: "HEAT WAVE LIKELY", SEVERE_S: "SEVERE HEAT WAVE LIKELY"}
STATUS_COLOR = {NONE: "#2e9e4f", POSSIBLE_S: "#b8860b", LIKELY_S: "#e67e22", SEVERE_S: "#d62828"}

_model_cache = {}
_pred_cache = {}


def _db_stamp():
    try:
        return os.path.getmtime(db.DB_PATH)
    except OSError:
        return 0


# ---- training (records mode) ------------------------------------------------------
def get_model(conn, city_id, before_year):
    """Fitted coefficients per lead, or None if there is too little earlier data."""
    key = (city_id, before_year, _db_stamp())
    if key not in _model_cache:
        _model_cache[key] = _train(db.training_rows(conn, city_id, before_year))
    return _model_cache[key]


def _train(rows):
    coefs = {}
    n_years = len({r["year"] for r in rows})
    for lead in range(1, MAX_LEAD + 1):
        pts = [(r["a1"], r["a3"], r[f"y{lead}"]) for r in rows
               if r[f"y{lead}"] is not None and r["a3"] is not None]
        if len(pts) < MIN_TRAIN_ROWS:
            return None
        x = np.array([[1.0, a1, a3] for a1, a3, _ in pts])
        y = np.array([p[2] for p in pts])
        coef = np.linalg.lstsq(x, y, rcond=None)[0]
        sigma = float(np.std(y - x @ coef, ddof=3))
        coefs[lead] = {"coef": [float(c) for c in coef], "sigma": sigma, "n": len(pts),
                       "years": n_years}
    return coefs


# ---- helpers -----------------------------------------------------------------------
def chance_at_least(level_temp, mean, sigma):
    """P(X >= level_temp) for X ~ Normal(mean, sigma)."""
    return 0.5 * math.erfc((level_temp - mean) / (sigma * math.sqrt(2.0)))


def status_for(p_heat, p_severe):
    """Outlook status and the level (0/1/2) sent to the alert station."""
    if p_severe >= LIKELY:
        return SEVERE_S, alerts.SEVERE
    if p_heat >= LIKELY:
        return LIKELY_S, alerts.HEAT_WAVE
    if p_heat >= POSSIBLE:
        return POSSIBLE_S, alerts.HEAT_WAVE
    return NONE, alerts.NORMAL


def _quantile(sorted_values, q):
    """Linear-interpolated quantile of an already sorted list."""
    if not sorted_values:
        return None
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _in_heat_season(d):
    return db.SEASON_START <= (d.month, d.day) <= db.SEASON_END


def _departures(rows):
    """{date: departure} for days with a recorded max temperature."""
    return {date.fromisoformat(r["obs_date"]): r["departure"]
            for r in rows if r["departure"] is not None}


def _features(dep, anchor):
    """(a1, a3) for the day `anchor`, using only days up to and including it."""
    a1 = dep[anchor]
    window = [dep[anchor - timedelta(days=k)] for k in range(3)
              if (anchor - timedelta(days=k)) in dep]
    return a1, sum(window) / len(window)


# ---- prediction --------------------------------------------------------------------
def predict_city(conn, city, as_of):
    """Predict `as_of` (today), the next day and the day after for one city.

    Only records BEFORE `as_of` are used. Returns a dict:
        ok, reason (if not ok), mode ("records" / "history"), basis (plain-language
        sentence), data_end (last record date), days (three dicts).
    """
    key = (city["city_id"], as_of, _db_stamp())
    if key in _pred_cache:
        return _pred_cache[key]
    _pred_cache[key] = result = _predict(conn, city, as_of)
    return result


def _predict(conn, city, as_of):
    cid, threshold = city["city_id"], city["threshold"]
    targets = [as_of + timedelta(days=k) for k in range(3)]
    normals = db.get_normals(conn, cid, targets)
    if any(normals[d] is None for d in targets):
        return {"ok": False, "reason": f"{city['name']} has too little weather history "
                                       "(about 14 months) to work out its normal temperature, "
                                       "so no prediction can be made."}

    # Records just before `as_of`, if the database has any
    yesterday = as_of - timedelta(days=1)
    rows = db.season_status(conn, cid, as_of - timedelta(days=14), yesterday, CONFIRM_DAYS)
    dep = _departures(rows)
    anchor = next((yesterday - timedelta(days=b) for b in range(0, MAX_GAP_DAYS + 1)
                   if (yesterday - timedelta(days=b)) in dep), None)
    coefs = None
    if anchor is not None and _in_heat_season(anchor):
        coefs = get_model(conn, cid, as_of.year)          # earlier years only
    records_mode = coefs is not None

    if records_mode:
        gap = (yesterday - anchor).days
        a1, a3 = _features(dep, anchor)

    days = []
    for k, target in enumerate(targets):
        normal = normals[target]
        hw, sev = alerts.thresholds(threshold, normal)
        hist = db.date_history(conn, cid, target.timetuple().tm_yday, hw, sev,
                               before_year=as_of.year)          # earlier years only
        if not records_mode and hist["n"] < MIN_HISTORY_DAYS:
            return {"ok": False, "reason": "There is not enough earlier history around this "
                                           "date to make a prediction."}
        if records_mode:
            c0, c1, c2 = coefs[gap + 1 + k]["coef"]
            sigma = coefs[gap + 1 + k]["sigma"]
            pred = normal + c0 + c1 * a1 + c2 * a3
            low, high = pred - RANGE_Z * sigma, pred + RANGE_Z * sigma
            p_heat = chance_at_least(hw, pred, sigma)
            p_sev = chance_at_least(sev, pred, sigma)
        else:
            pred = normal
            low, high = _quantile(hist["values"], 0.10), _quantile(hist["values"], 0.90)
            p_heat = hist["hw_days"] / hist["n"]
            p_sev = hist["sev_days"] / hist["n"]
        status, level = status_for(p_heat, p_sev)
        days.append({"label": DAY_NAMES[k], "date": target, "pred": pred, "low": low,
                     "high": high, "normal": normal, "hw": hw, "sev": sev,
                     "p_heat": p_heat, "p_sev": p_sev, "status": status, "level": level,
                     "history": {k2: v for k2, v in hist.items() if k2 != "values"},
                     "live_floor": None})

    last_record = conn.execute("SELECT MAX(obs_date) FROM daily_obs WHERE city_id = ? "
                               "AND tmax IS NOT NULL", (cid,)).fetchone()[0]
    h0 = days[0]["history"]
    history_text = (f"how hot this time of year has been in {h0['years']} years of history "
                    f"({h0['first_year']} to {h0['last_year']})")
    if records_mode:
        basis = (f"Uses the latest records (up to {anchor:%d %b %Y}) and the pattern learned "
                 f"from {coefs[1]['years']} earlier seasons of this city.")
    elif anchor is None:
        basis = (f"Based on {history_text}. There are no records for the last few days (the "
                 f"weather data ends on {date.fromisoformat(last_record):%d %b %Y}), so recent "
                 "weather is not used.")
    elif not _in_heat_season(anchor):
        basis = (f"Based on {history_text}. Recent records exist, but the pattern model only "
                 "covers the heat season (1 March to 30 June), so they are not used.")
    else:
        basis = (f"Based on {history_text}. There are too few earlier seasons before "
                 f"{as_of.year} to learn the recent-records pattern from.")
    return {"ok": True, "mode": "records" if records_mode else "history", "basis": basis,
            "data_end": last_record, "days": days}


def apply_live(days, live_max):
    """Fold a live station reading into TODAY's prediction (a floor, never a cut).

    The day's maximum can never be lower than a temperature already measured.
    """
    if live_max is None:
        return days
    d = dict(days[0])
    d["pred"] = max(d["pred"], live_max)
    d["low"] = max(d["low"], live_max)
    d["high"] = max(d["high"], live_max)
    if live_max >= d["hw"]:
        d["p_heat"] = 1.0
    if live_max >= d["sev"]:
        d["p_sev"] = 1.0
    d["status"], d["level"] = status_for(d["p_heat"], d["p_sev"])
    d["live_floor"] = live_max
    return [d] + list(days[1:])


def headline(days):
    """One plain sentence and a status for the whole 3-day outlook."""
    worst = max(days, key=lambda d: (d["level"], d["p_heat"]))
    flagged = [d["label"].lower() for d in days if d["status"] != NONE]
    if worst["status"] == SEVERE_S:
        sev = [d["label"].lower() for d in days if d["status"] == SEVERE_S]
        return f"Severe heat wave likely: {', '.join(sev)}", SEVERE_S
    if worst["status"] == LIKELY_S:
        likely = [d["label"].lower() for d in days if d["status"] in (LIKELY_S, SEVERE_S)]
        return f"Heat wave likely: {', '.join(likely)}", LIKELY_S
    if flagged:
        return f"Heat wave possible: {', '.join(flagged)}", POSSIBLE_S
    return "No heat wave expected today, tomorrow or the day after", NONE
