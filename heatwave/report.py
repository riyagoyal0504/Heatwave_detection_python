"""Plain-language summaries and the downloadable report.

The report is one self-contained HTML file (no internet or extra libraries
needed). Open it in a browser; use Print > Save as PDF for a PDF copy.
The chart in it is drawn as inline SVG, so it prints sharply.
"""
from html import escape

from . import alerts, model

STATUS_MEANING = {
    model.NONE: "Under a 15 % chance, so no heat wave is expected.",
    model.POSSIBLE_S: "A 15 to 50 % chance, so a heat wave is possible. Stay alert.",
    model.LIKELY_S: "At least a 50 % chance of reaching the heat-wave level.",
    model.SEVERE_S: "At least a 50 % chance of reaching the severe heat-wave level.",
}

METHOD_NOTES = [
    "Source: daily weather records (maximum temperature) for each city, 1990 to 2022, stored in "
    "an SQLite database.",
    "Heat-wave levels follow the India Meteorological Department (IMD): the station threshold "
    "(plains 40 °C, coastal 37 °C), a rise of at least 4.5 °C above the normal maximum (severe: "
    "6.5 °C), or 45 °C (severe: 47 °C) regardless. Normals are computed from this data, not taken "
    "from IMD, so results can differ from IMD bulletins.",
    "This is a statistical prediction, not a weather forecast. It does not know about fronts, "
    "cloud, wind or monsoon onset, and it tends to under-predict extreme days.",
    "Held-out tests (2008 to 2021, six cities): with recent records the average error was "
    "1.1 / 1.4 / 1.6 °C for today / tomorrow / day after, against 1.8 °C using just the normal; "
    "about 69 % / 40 % / 22 % of heat-wave days were caught. Without recent records the "
    "prediction is the typical value for the date and has no skill beyond that; it also "
    "leans slightly low because recent years are hotter (2008 to 2021: average stated chance "
    "2.6 % against 3.2 % of days that really reached the heat-wave level).",
]


def pct(p):
    if p < 0.01:
        return "<1 %"
    if p > 0.99:
        return ">99 %"
    return f"{p * 100:.0f} %"


# ---- written summary -----------------------------------------------------------------
def describe_day(city_name, d, res):
    """A short list of plain-language sentences describing one day's prediction."""
    records = res["mode"] == "records"
    dep = d["pred"] - d["normal"]
    if records:
        first = (f"{city_name} is expected to reach a maximum of about {d['pred']:.1f} °C on "
                 f"{d['date']:%A %d %B}, most likely between {d['low']:.0f} and {d['high']:.0f} °C.")
    else:
        first = (f"A typical maximum for {d['date']:%d %B} in {city_name} is about "
                 f"{d['pred']:.1f} °C; 80 % of past days fell between {d['low']:.0f} and "
                 f"{d['high']:.0f} °C.")
    lines = [first]
    if records:
        lines.append(f"That is {abs(dep):.1f} °C {'above' if dep >= 0 else 'below'} the normal "
                     f"maximum for this date ({d['normal']:.1f} °C).")
    lines.append(f"A heat wave is declared at {d['hw']:.1f} °C or more (severe heat wave: "
                 f"{d['sev']:.1f} °C). The chance of reaching the heat-wave level is "
                 f"{pct(d['p_heat'])} and of reaching the severe level {pct(d['p_sev'])}.")
    lines.append(f"Outlook: {model.STATUS_TEXT[d['status']].capitalize()}. "
                 f"{STATUS_MEANING[d['status']]}")
    h = d["history"]
    if h["years"] < 5:
        pass                                    # too little earlier history to compare with
    elif h["years_hw"]:
        sev_part = (f" and the severe level in {h['years_sev']}" if h["years_sev"] else "")
        lines.append(f"For comparison, around this date the heat-wave level was reached in "
                     f"{h['years_hw']} of {h['years']} years ({h['first_year']} to "
                     f"{h['last_year']}){sev_part}.")
    else:
        lines.append(f"For comparison, around this date the heat-wave level was never reached "
                     f"in the {h['years']} years on record ({h['first_year']} to {h['last_year']}).")
    if d.get("live_floor") is not None:
        lines.append(f"The local sensor station has already measured {d['live_floor']:.1f} °C "
                     "today, so today's maximum cannot be lower than that.")
    return lines


# ---- inline SVG chart -----------------------------------------------------------------
def _ticks(lo, hi):
    step = 1 if hi - lo <= 8 else 2 if hi - lo <= 16 else 5
    t = int(lo // step * step)
    out = []
    while t <= hi:
        if t >= lo:
            out.append(t)
        t += step
    return out


def range_chart_svg(d, width=640, height=170):
    """One day on a temperature line: likely range, expected value, normal, levels."""
    pad = 28
    lo = min(d["low"], d["normal"], d["hw"]) - 2
    hi = max(d["high"], d["sev"]) + 2

    def x(v):
        return pad + (v - lo) / (hi - lo) * (width - 2 * pad)

    axis_y = 112
    amber, red = alerts.LEVEL_COLORS[1], alerts.LEVEL_COLORS[2]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
             f'width="100%" role="img" aria-label="Predicted maximum temperature range">',
             f'<rect width="{width}" height="{height}" fill="white"/>',
             f'<line x1="{pad}" y1="{axis_y}" x2="{width - pad}" y2="{axis_y}" '
             f'stroke="#888" stroke-width="1.5"/>']
    for t in _ticks(lo, hi):
        parts.append(f'<line x1="{x(t):.1f}" y1="{axis_y}" x2="{x(t):.1f}" y2="{axis_y + 5}" stroke="#888"/>')
        parts.append(f'<text x="{x(t):.1f}" y="{axis_y + 19}" font-size="11" fill="#555" '
                     f'text-anchor="middle">{t}</text>')
    parts.append(f'<text x="{width - pad}" y="{axis_y + 36}" font-size="11" fill="#555" '
                 f'text-anchor="end">Maximum temperature (°C)</text>')
    # heat-wave and severe levels
    for value, color, label, ytxt in ((d["hw"], amber, f"Heat wave {d['hw']:.1f}", 14),
                                      (d["sev"], red, f"Severe {d['sev']:.1f}", 30)):
        parts.append(f'<line x1="{x(value):.1f}" y1="{ytxt + 4}" x2="{x(value):.1f}" y2="{axis_y}" '
                     f'stroke="{color}" stroke-width="2" stroke-dasharray="6 4"/>')
        parts.append(f'<text x="{x(value):.1f}" y="{ytxt}" font-size="12" font-weight="bold" '
                     f'fill="{color}" text-anchor="middle">{escape(label)}</text>')
    # likely range
    parts.append(f'<rect x="{x(d["low"]):.1f}" y="62" width="{x(d["high"]) - x(d["low"]):.1f}" '
                 f'height="30" rx="6" fill="#f4c7c3" stroke="#d62828" stroke-width="1"/>')
    # normal (diamond) and expected value (dot)
    nx = x(d["normal"])
    parts.append(f'<polygon points="{nx:.1f},68 {nx + 7:.1f},77 {nx:.1f},86 {nx - 7:.1f},77" fill="#777"/>')
    parts.append(f'<circle cx="{x(d["pred"]):.1f}" cy="77" r="9" fill="#d62828" stroke="white" stroke-width="2"/>')
    parts.append(f'<text x="{x(d["pred"]):.1f}" y="56" font-size="12" font-weight="bold" fill="#d62828" '
                 f'text-anchor="middle">{d["pred"]:.1f} °C</text>')
    parts.append(f'<text x="{pad}" y="{height - 4}" font-size="11" fill="#555">'
                 f'Red dot = expected maximum, pink bar = likely range (80 %), grey diamond = '
                 f'normal ({d["normal"]:.1f} °C)</text>')
    parts.append("</svg>")
    return "".join(parts)


def chance_bar_svg(p, color, label, width=320):
    fill = max(0.0, min(1.0, p)) * (width - 2)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 34" width="{width}" '
            f'role="img" aria-label="{escape(label)}">'
            f'<rect x="1" y="14" width="{width - 2}" height="16" rx="8" fill="#eee" stroke="#ccc"/>'
            f'<rect x="1" y="14" width="{fill:.1f}" height="16" rx="8" fill="{color}"/>'
            f'<text x="1" y="10" font-size="12" fill="#333">{escape(label)}: {escape(pct(p))}</text>'
            f'</svg>')


# ---- the report ------------------------------------------------------------------------
_CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#222;max-width:860px;
margin:24px auto;padding:0 18px;line-height:1.45}
h1{margin-bottom:2px} h2{margin-top:28px;border-bottom:2px solid #eee;padding-bottom:4px}
.meta{color:#666;margin-top:0}
.banner{color:#fff;padding:14px 20px;border-radius:10px;font-size:1.25rem;font-weight:700;margin:14px 0}
table{border-collapse:collapse;width:100%;margin:8px 0 4px} th,td{border:1px solid #ddd;padding:7px 10px;text-align:left}
th{background:#f6f6f6}
.pill{color:#fff;border-radius:6px;padding:2px 8px;font-weight:700;font-size:.85rem;white-space:nowrap}
.day{page-break-inside:avoid}
ul{margin-top:4px} .foot{color:#777;font-size:.85rem;margin-top:30px;border-top:1px solid #eee;padding-top:8px}
@media print{body{margin:0}}
"""


def build_report(city, as_of, res, days, focus, generated):
    """The report as one HTML string.

    focus = 0, 1 or 2 for a single day's report, or None for the full 3-day report.
    """
    chosen = [days[focus]] if focus is not None else list(days)
    title_day = chosen[0]["label"] if focus is not None else "next 3 days"
    text, head_status = model.headline(chosen)
    worst_level = max(d["level"] for d in chosen)
    h = []
    h.append(f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             f"<title>Heat-wave report: {escape(city['name'])}, {escape(title_day)}</title>"
             f"<style>{_CSS}</style></head><body>")
    h.append("<h1>Heat-wave prediction report</h1>")
    h.append(f"<p class='meta'><b>{escape(city['name'])}</b> ({escape(city['station_name'])} station, "
             f"{escape(city['station_type'])}, IMD threshold {city['threshold']:.0f} °C) · "
             f"predicting from {as_of:%A %d %B %Y} · report generated {generated:%d %b %Y %H:%M}</p>")
    h.append(f"<div class='banner' style='background:{model.STATUS_COLOR[head_status]}'>"
             f"{escape(text)}</div>")

    h.append("<h2>Summary</h2><table><tr><th>Day</th><th>Date</th><th>Expected max</th>"
             "<th>Likely range</th><th>Chance of heat wave</th><th>Outlook</th></tr>")
    for d in chosen:
        h.append(f"<tr><td>{escape(d['label'])}</td><td>{d['date']:%a %d %b}</td>"
                 f"<td>{d['pred']:.1f} °C</td><td>{d['low']:.0f} to {d['high']:.0f} °C</td>"
                 f"<td>{escape(pct(d['p_heat']))}</td>"
                 f"<td><span class='pill' style='background:{model.STATUS_COLOR[d['status']]}'>"
                 f"{escape(model.STATUS_TEXT[d['status']])}</span></td></tr>")
    h.append("</table>")

    for d in chosen:
        h.append(f"<div class='day'><h2>{escape(d['label'])}: {d['date']:%A %d %B %Y}</h2>")
        for line in describe_day(city["name"], d, res):
            h.append(f"<p>{escape(line)}</p>")
        h.append(range_chart_svg(d))
        h.append(chance_bar_svg(d["p_heat"], model.STATUS_COLOR[d["status"]], "Chance of heat wave"))
        h.append("</div>")

    h.append(f"<h2>What to do ({escape(alerts.LEVEL_NAMES[worst_level])})</h2><ul>")
    for a in alerts.ADVICE[worst_level]:
        h.append(f"<li>{escape(a)}</li>")
    h.append("</ul>")
    h.append("<h2>How this was produced</h2>")
    h.append(f"<p>{escape(res['basis'])}</p><ul>")
    for note in METHOD_NOTES:
        h.append(f"<li>{escape(note)}</li>")
    h.append("</ul>")
    h.append("<p class='foot'>General guidance in the spirit of NDMA/IMD heat advisories. This "
             "report is a student project and is not an official warning; always follow your local "
             "authority's advice.</p></body></html>")
    return "".join(h)
