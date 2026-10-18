"""IMD heatwave rules, alert levels and early-warning advice.

IMD (India Meteorological Department) heatwave criteria used here
------------------------------------------------------------------
Based on the DAILY MAXIMUM temperature (tmax) of a station:
    Station threshold:  Plains >= 40 C   Coastal >= 37 C   Hilly >= 30 C
    Heat Wave        :  threshold reached AND departure from normal >= 4.5 C
    Severe Heat Wave :  threshold reached AND departure from normal >= 6.5 C
    Absolute rule    :  tmax >= 45 C is a Heat Wave, tmax >= 47 C is Severe
    Duration         :  IMD declares a heat wave when the criteria hold for at
                        least 2 consecutive days (adjustable in the sidebar).

The "normal" is the long-term average maximum for that calendar day. Here it
is computed from the dataset itself (see db.py).
"""

# ---- alert levels -------------------------------------------------------------
NORMAL, HEAT_WAVE, SEVERE = 0, 1, 2
LEVEL_NAMES = {NORMAL: "Normal", HEAT_WAVE: "Heat Wave", SEVERE: "Severe Heat Wave"}
LEVEL_COLORS = {NORMAL: "#2e9e4f", HEAT_WAVE: "#e6a100", SEVERE: "#d62828"}
NO_DATA_COLOR = "#8a8f98"

# ---- IMD numbers (also used by the SQL view in db.py, so defined once) --------
STATION_THRESHOLDS = {"Plains": 40.0, "Coastal": 37.0, "Hilly": 30.0}
DEPARTURE_HEAT_WAVE = 4.5
DEPARTURE_SEVERE = 6.5
ABSOLUTE_HEAT_WAVE = 45.0
ABSOLUTE_SEVERE = 47.0


def thresholds(station_threshold, normal_max):
    """Return (heat_wave_temp, severe_temp): the tmax at which each level starts.

    Combines the station threshold, the departure rule and the absolute
    45 / 47 C rule. If no normal is available (too little history) only the
    absolute rule can be applied.
    """
    if normal_max is None:
        return ABSOLUTE_HEAT_WAVE, ABSOLUTE_SEVERE
    hw = min(max(station_threshold, normal_max + DEPARTURE_HEAT_WAVE), ABSOLUTE_HEAT_WAVE)
    sev = min(max(station_threshold, normal_max + DEPARTURE_SEVERE), ABSOLUTE_SEVERE)
    return hw, sev


def classify(tmax, hw, sev):
    """0 normal, 1 heat wave, 2 severe for a single day (None if no tmax)."""
    if tmax is None:
        return None
    if tmax >= sev:
        return SEVERE
    if tmax >= hw:
        return HEAT_WAVE
    return NORMAL


def event_label(prev_level, level):
    """Text for an alert-log row, from the previous and new confirmed level."""
    if prev_level is None:
        return "Alert active at start of replay"
    return "Escalated" if level > prev_level else "Eased"


# ---- early-warning advice ------------------------------------------------------
# General public-health guidance in the spirit of NDMA / IMD heat advisories.
ADVICE = {
    NORMAL: [
        "No heat alert. Carry on as usual.",
        "Keep drinking water regularly, even if you are not thirsty.",
        "Keep monitoring: the trend can change within days.",
    ],
    HEAT_WAVE: [
        "Avoid going out in the sun between about 12:00 and 16:00.",
        "Drink water, ORS, lassi, buttermilk or lemon water often.",
        "Wear light, loose, light-coloured cotton clothes; cover head with a cloth or hat.",
        "Reschedule heavy outdoor work to early morning or evening.",
        "Watch for dizziness, headache, cramps or nausea; rest in shade and cool down.",
    ],
    SEVERE: [
        "Stay indoors in the coolest place available; go out only if essential.",
        "Stop outdoor labour and sports; open cooling centres / shade shelters.",
        "Check on elderly people, infants, pregnant women and people with illness.",
        "Never leave children or pets in a parked vehicle.",
        "Heatstroke signs (very high body temperature, confusion, no sweating, "
        "unconsciousness) are an emergency: cool the person and call 108 / 112.",
    ],
}
