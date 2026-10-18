"""Heatwave Monitoring, Prediction and Early Warning - Streamlit website.

Run with:   streamlit run app.py

Data flow:  CSV files -> heatwave.db (SQLite, built on first run)
                      -> SQL queries (heatwave/db.py)
                      -> heatwave.model: prediction for today / tomorrow / day after
                      -> this file: overview page and one page per day
                      -> heatwave.report: written summary and downloadable report
                      -> heatwave.station: tells the alert station (backend only,
                         nothing about it is shown here)
"""
import csv
import io
from contextlib import closing
from datetime import date, datetime

import plotly.graph_objects as go
import streamlit as st

from heatwave import alerts, db, model, report, station

st.set_page_config(page_title="Heatwave Early Warning", page_icon="🌡️", layout="wide")

CONFIRM_DAYS = model.CONFIRM_DAYS
VIEWS = ["Overview (all 3 days)", "Today", "Tomorrow", "Day after tomorrow"]


@st.cache_resource(show_spinner="Building the SQLite database from the CSV files (first run only)...")
def init_database():
    return db.ensure_database()


init_database()

# =============================================================================
# Sidebar
# =============================================================================
st.sidebar.title("🌡️ Settings")

with closing(db.connect()) as _conn:
    cities = db.get_cities(_conn)
city_name = st.sidebar.selectbox("City", [c["name"] for c in cities])
city = next(c for c in cities if c["name"] == city_name)

view = st.sidebar.selectbox("Prediction for", VIEWS)

st.sidebar.divider()
auto_refresh = st.sidebar.checkbox("Auto-refresh", value=True)
refresh_s = st.sidebar.slider("Refresh every (seconds)", 1, 60, 5)

with st.sidebar.expander("Advanced: predict from another date"):
    use_custom_date = st.checkbox(
        "Use a different date", value=False,
        help="By default the site predicts from today's date. The weather data ends in 2022, so "
             "outside the heat season there is nothing to see; pick a date in March to June "
             "(1995 to 2022) to watch the model use real recent records.")
    custom_date = st.date_input("Date", value=date.today(),
                                min_value=date(1991, 1, 1), max_value=date(2035, 12, 31))


# =============================================================================
# Small helpers
# =============================================================================
def pct(p):
    return report.pct(p)


def banner_html(text, status):
    return (f"<div style='background:{model.STATUS_COLOR[status]};color:white;padding:16px 22px;"
            f"border-radius:12px;font-size:1.5rem;font-weight:700;margin-bottom:12px'>{text}</div>")


def day_card(d, mode):
    color = model.STATUS_COLOR[d["status"]]
    caption = ("likely range" if mode == "records" else "80 % of past days between")
    big = "Expected max" if mode == "records" else "Typical max"
    return (f"<div style='border:2px solid {color};border-radius:12px;padding:14px 16px'>"
            f"<div style='font-weight:700;font-size:1.1rem'>{d['label']}</div>"
            f"<div style='opacity:.7;font-size:.85rem'>{d['date']:%A %d %B}</div>"
            f"<div style='opacity:.7;font-size:.85rem;margin-top:6px'>{big}</div>"
            f"<div style='font-size:2.3rem;font-weight:700;line-height:1.1'>{d['pred']:.1f} °C</div>"
            f"<div style='opacity:.7;font-size:.85rem'>{caption} {d['low']:.0f} to {d['high']:.0f} °C</div>"
            f"<div style='margin-top:10px'>Chance of heat wave: <b>{pct(d['p_heat'])}</b></div>"
            f"<div style='opacity:.7;font-size:.85rem'>(heat wave starts at {d['hw']:.1f} °C)</div>"
            f"<div style='background:{color};color:white;border-radius:8px;padding:7px;"
            f"text-align:center;margin-top:10px;font-weight:700'>{model.STATUS_TEXT[d['status']]}</div>"
            f"</div>")


def advice_cards(current):
    html = ""
    for lvl in (alerts.NORMAL, alerts.HEAT_WAVE, alerts.SEVERE):
        color = alerts.LEVEL_COLORS[lvl]
        tint = f"{color}26" if lvl == current else "transparent"
        tag = " &nbsp;◀ for this outlook" if lvl == current else ""
        items = "".join(f"<li>{a}</li>" for a in alerts.ADVICE[lvl])
        html += (f"<div style='border-left:6px solid {color};background:{tint};"
                 f"padding:8px 14px;margin-bottom:10px;border-radius:4px'>"
                 f"<b style='color:{color}'>{alerts.LEVEL_NAMES[lvl]}</b>{tag}"
                 f"<ul style='margin:4px 0 0 0;padding-left:20px'>{items}</ul></div>")
    return html


def to_csv(rows, fields):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# ---- charts (all about the FUTURE: nothing from the past is drawn) -----------------------
def outlook_chart(days):
    """Expected maximum for the next 3 days, with the two IMD levels."""
    x = [f"{d['label']}<br>{d['date']:%d %b}" for d in days]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=[d["pred"] for d in days], name="Expected max temperature",
        mode="lines+markers", line=dict(color="#d62828", width=3),
        marker=dict(size=12, color="#d62828"),
        error_y=dict(type="data", visible=True, thickness=1.5,
                     array=[d["high"] - d["pred"] for d in days],
                     arrayminus=[d["pred"] - d["low"] for d in days])))
    fig.add_trace(go.Scatter(x=x, y=[d["hw"] for d in days], name="Heat wave level", mode="lines",
                             line=dict(color=alerts.LEVEL_COLORS[1], width=2, dash="dash")))
    fig.add_trace(go.Scatter(x=x, y=[d["sev"] for d in days], name="Severe level", mode="lines",
                             line=dict(color=alerts.LEVEL_COLORS[2], width=2, dash="dash")))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=10), template="plotly_white",
                      yaxis_title="Max temperature (°C)", hovermode="x unified",
                      legend=dict(orientation="h", y=1.14, x=0))
    return fig


def gauge_chart(d):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=round(d["p_heat"] * 100, 1),
        number={"suffix": " %"}, title={"text": "Chance of heat wave"},
        gauge={"axis": {"range": [0, 100]}, "bar": {"color": model.STATUS_COLOR[d["status"]]},
               "steps": [{"range": [0, 15], "color": "#e8f5e9"},
                         {"range": [15, 50], "color": "#fff3cd"},
                         {"range": [50, 100], "color": "#f8d7da"}]}))
    fig.update_layout(height=250, margin=dict(l=20, r=20, t=50, b=10))
    return fig


def range_chart(d):
    """One day on a temperature line: likely range, expected value, normal, levels."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[d["low"], d["high"], d["high"], d["low"], d["low"]], y=[0.3, 0.3, 0.7, 0.7, 0.3],
        fill="toself", mode="lines", name="Likely range (80 %)",
        line=dict(color="rgba(214,40,40,0.5)", width=1), fillcolor="rgba(214,40,40,0.18)"))
    fig.add_trace(go.Scatter(x=[d["hw"], d["hw"]], y=[0, 1], mode="lines",
                             name=f"Heat wave level ({d['hw']:.1f} °C)",
                             line=dict(color=alerts.LEVEL_COLORS[1], width=3, dash="dash")))
    fig.add_trace(go.Scatter(x=[d["sev"], d["sev"]], y=[0, 1], mode="lines",
                             name=f"Severe level ({d['sev']:.1f} °C)",
                             line=dict(color=alerts.LEVEL_COLORS[2], width=3, dash="dash")))
    fig.add_trace(go.Scatter(x=[d["normal"]], y=[0.5], mode="markers",
                             name=f"Normal for this date ({d['normal']:.1f} °C)",
                             marker=dict(size=14, symbol="diamond", color="#777777")))
    fig.add_trace(go.Scatter(x=[d["pred"]], y=[0.5], mode="markers",
                             name=f"Expected max ({d['pred']:.1f} °C)",
                             marker=dict(size=18, color="#d62828", line=dict(color="white", width=2))))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=20, b=10), template="plotly_white",
                      xaxis_title="Maximum temperature (°C)",
                      yaxis=dict(visible=False, range=[0, 1]),
                      legend=dict(orientation="h", y=-0.35, x=0))
    return fig


# ---- tables for the "all cities" board ------------------------------------------------------
def board_for_day(board, idx):
    rows = []
    for c, r in board:
        if r["ok"]:
            d = r["days"][idx]
            rows.append((d["p_heat"], {"City": c["name"], "Expected max (°C)": round(d["pred"], 1),
                                       "Heat wave starts (°C)": round(d["hw"], 1),
                                       "Chance of heat wave": pct(d["p_heat"]),
                                       "Outlook": model.STATUS_TEXT[d["status"]].capitalize()}))
        else:
            rows.append((-1, {"City": c["name"], "Expected max (°C)": None,
                              "Heat wave starts (°C)": None, "Chance of heat wave": "n/a",
                              "Outlook": "No prediction (too little history)"}))
    return [row for _, row in sorted(rows, key=lambda t: (-t[0], t[1]["City"]))]


def board_overview(board):
    rows = []
    for c, r in board:
        if r["ok"]:
            ds = r["days"]
            worst = max(ds, key=lambda d: (d["level"], d["p_heat"]))
            rows.append((max(d["p_heat"] for d in ds), {
                "City": c["name"], "Today": pct(ds[0]["p_heat"]), "Tomorrow": pct(ds[1]["p_heat"]),
                "Day after tomorrow": pct(ds[2]["p_heat"]),
                "Worst outlook": model.STATUS_TEXT[worst["status"]].capitalize()}))
        else:
            rows.append((-1, {"City": c["name"], "Today": "n/a", "Tomorrow": "n/a",
                              "Day after tomorrow": "n/a", "Worst outlook": "No prediction"}))
    return [row for _, row in sorted(rows, key=lambda t: (-t[0], t[1]["City"]))]


BOARD_FIELDS = ["City", "Day", "Date", "Expected max (°C)", "Heat wave starts (°C)",
                "Chance of heat wave (%)", "Chance of severe (%)", "Outlook"]


def board_csv(board):
    rows = []
    for c, r in board:
        if not r["ok"]:
            continue
        for d in r["days"]:
            rows.append({"City": c["name"], "Day": d["label"], "Date": d["date"].isoformat(),
                         "Expected max (°C)": round(d["pred"], 1),
                         "Heat wave starts (°C)": round(d["hw"], 1),
                         "Chance of heat wave (%)": round(d["p_heat"] * 100, 1),
                         "Chance of severe (%)": round(d["p_sev"] * 100, 1),
                         "Outlook": model.STATUS_TEXT[d["status"]]})
    return to_csv(rows, BOARD_FIELDS)


# =============================================================================
# The two kinds of page
# =============================================================================
def overview_page(as_of, res, days, board):
    text, head_status = model.headline(days)
    st.markdown(banner_html(text, head_status), unsafe_allow_html=True)
    for col, d in zip(st.columns(3), days):
        col.markdown(day_card(d, res["mode"]), unsafe_allow_html=True)
    st.caption("Choose Today, Tomorrow or Day after tomorrow under **Prediction for** (left) "
               "to open that day's full page, with a written summary, charts and a report.")

    st.subheader("The next 3 days")
    st.plotly_chart(outlook_chart(days), key="outlook_chart")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("All cities: chance of a heat wave")
        st.dataframe(board_overview(board), hide_index=True)
        st.download_button("⬇️ Download all cities (CSV)", board_csv(board),
                           f"heatwave_predictions_{as_of:%Y%m%d}.csv", "text/csv", key="dl_board")
    with right:
        st.subheader("Early warning")
        worst = max(d["level"] for d in days)
        st.caption("Advice for the worst level in the 3-day outlook:")
        st.markdown(advice_cards(worst), unsafe_allow_html=True)

    st.subheader("Report")
    st.caption("A printable report for all three days: summary table, a chart and a written "
               "explanation for each day, advice and how it was produced. Open the file and use "
               "Print > Save as PDF for a PDF copy.")
    st.download_button("📄 Download 3-day report (HTML)",
                       report.build_report(city, as_of, res, days, None, datetime.now()),
                       f"heatwave_report_{city['name']}_{as_of:%Y%m%d}.html", "text/html",
                       key="dl_report_all")


def day_page(idx, as_of, res, days, board):
    d = days[idx]
    st.markdown(f"## {d['label']}: {d['date']:%A %d %B %Y}")
    st.markdown(banner_html(model.STATUS_TEXT[d["status"]], d["status"]), unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.subheader("In words")
        for line in report.describe_day(city["name"], d, res):
            st.markdown(f"- {line}")
    with right:
        st.plotly_chart(gauge_chart(d), key="gauge_chart")

    st.subheader("In a chart")
    st.plotly_chart(range_chart(d), key="range_chart")
    st.caption("The red dot is the expected maximum and the pink band the likely range (80 %). "
               "A heat wave starts at the amber line and a severe heat wave at the red line.")

    left2, right2 = st.columns([3, 2])
    with left2:
        st.subheader("All cities on this day")
        st.dataframe(board_for_day(board, idx), hide_index=True)
    with right2:
        st.subheader("What to do")
        st.markdown(advice_cards(d["level"]), unsafe_allow_html=True)

    st.subheader("Report")
    st.caption("A printable report for this day only. Open the file and use Print > Save as PDF "
               "for a PDF copy.")
    st.download_button(f"📄 Download report for {d['label'].lower()} (HTML)",
                       report.build_report(city, as_of, res, days, idx, datetime.now()),
                       f"heatwave_report_{city['name']}_{d['label'].replace(' ', '_').lower()}_"
                       f"{as_of:%Y%m%d}.html", "text/html", key=f"dl_report_{idx}")

    with st.expander("How was this worked out, and how reliable is it?"):
        st.markdown(f"**Basis:** {res['basis']}")
        if res["mode"] == "records":
            st.markdown(
                "The model learned, from this city's earlier years, how the next 1 to 3 days "
                "depend on how far the latest days ran above the normal maximum. Held-out tests "
                "(2008 to 2021, six cities): average error 1.1 / 1.4 / 1.6 °C for today / "
                "tomorrow / day after, against 1.8 °C using just the normal. About 69 % / 40 % / "
                "22 % of heat-wave days were caught (chance of at least 15 %), and the stated "
                "chances were reliable: a 10 to 20 % chance came true about 10 to 16 % of the "
                "time. It is weaker at catching heat-wave days further ahead, and tends to "
                "under-predict extremes.")
        else:
            st.markdown(
                "With no recent records, the prediction is the typical (normal) maximum for the "
                "date, and the chance is how often that time of year reached the heat-wave level "
                "in past years. It cannot tell whether this particular week is hotter than usual, "
                "so it has no skill beyond the normal. It also leans slightly low because recent "
                "years are hotter: in tests on 2008 to 2021 the average stated chance was 2.6 % "
                "while 3.2 % of days really reached the heat-wave level. Open **Advanced: "
                "predict from another "
                "date** in the sidebar and pick a date in March to June between 1995 and 2022 to "
                "see the model that uses real recent records.")
        st.markdown("It is a simple statistical model, **not a weather forecast**: it knows "
                    "nothing about fronts, cloud, wind or monsoon onset.")


# =============================================================================
# The dashboard body (re-runs every N seconds when auto-refresh is on)
# =============================================================================
def dashboard():
    as_of = custom_date if use_custom_date else date.today()
    with closing(db.connect()) as conn:
        res = model.predict_city(conn, city, as_of)
        board = [(c, model.predict_city(conn, c, as_of)) for c in cities]

    refresh_note = f" · refreshes every {refresh_s} s" if auto_refresh else " · auto-refresh is off"
    st.caption(f"Predicting from **{as_of:%A %d %B %Y}** · last updated "
               f"{datetime.now():%H:%M:%S}{refresh_note}")
    if not res["ok"]:
        st.info(f"Prediction not available: {res['reason']}")
        return

    live = station.read_live() if as_of == date.today() else None
    days = model.apply_live(res["days"], live["max"] if live else None)
    station.publish([d["level"] for d in days], [d["pred"] for d in days])   # backend only

    if res["mode"] == "history":
        st.info(res["basis"])
    else:
        st.caption(res["basis"])
    if live:
        st.caption(f"Live reading from the local sensor station: {live['latest']:.1f} °C "
                   f"(highest so far {live['max']:.1f} °C).")

    if view == VIEWS[0]:
        overview_page(as_of, res, days, board)
    else:
        day_page(VIEWS.index(view) - 1, as_of, res, days, board)


st.title("Heatwave Monitoring, Prediction and Early Warning")
st.caption("Predicts the heat wave for today, tomorrow and the day after from each city's own "
           "weather history (1990 to 2022, stored in SQLite).")

if auto_refresh:
    st.fragment(run_every=refresh_s)(dashboard)()      # re-runs only this function
else:
    dashboard()


# =============================================================================
# Static sections (drawn once, not on every refresh)
# =============================================================================
with closing(db.connect()) as _conn:
    _yearly = db.yearly_heat_days(_conn, city["city_id"])
    _episodes = db.past_episodes(_conn, city["city_id"], CONFIRM_DAYS, limit=100000)
    _quality = db.data_quality(_conn)

with st.expander(f"History: {city['name']} heat-wave days per year and past episodes"):
    _years = [r["year"] for r in _yearly]
    _fig = go.Figure()
    _fig.add_trace(go.Bar(x=_years, y=[r["heat_days"] - r["severe_days"] for r in _yearly],
                          name="Heat wave days", marker_color=alerts.LEVEL_COLORS[1]))
    _fig.add_trace(go.Bar(x=_years, y=[r["severe_days"] for r in _yearly],
                          name="Severe heat wave days", marker_color=alerts.LEVEL_COLORS[2]))
    _fig.update_layout(barmode="stack", height=300, template="plotly_white",
                       margin=dict(l=10, r=10, t=20, b=10), yaxis_title="days",
                       legend=dict(orientation="h", y=1.15, x=0))
    st.plotly_chart(_fig, key="history_chart")
    st.caption("Days that reached the heat-wave level in each year (same-day criteria). 2022 "
               "ends in July/September, and Rourkela has only 2021 and 2022.")

    st.markdown(f"**Most recent heat-wave episodes** (at least {CONFIRM_DAYS} consecutive days at "
                "the heat-wave level)")
    _ep_rows = [{"Start": r["start_date"], "End": r["end_date"], "Days": r["days"],
                 "Peak max (°C)": r["peak_tmax"], "Peak above normal (°C)": r["peak_departure"],
                 "Worst level": alerts.LEVEL_NAMES[r["worst_level"]]} for r in _episodes]
    if _ep_rows:
        st.dataframe(_ep_rows[:15], hide_index=True)
    else:
        st.caption("No heat-wave episodes on record for this city.")
    st.download_button("⬇️ Download all past episodes (CSV)",
                       to_csv(_ep_rows, ["Start", "End", "Days", "Peak max (°C)",
                                         "Peak above normal (°C)", "Worst level"]),
                       f"past_heatwave_episodes_{city['name']}.csv", "text/csv", key="dl_episodes")

with st.expander("Data notes and limitations"):
    st.markdown(
        "- **Source:** daily weather records (maximum, minimum and average temperature, rainfall) "
        "in the CSV files, loaded into SQLite. The maximum is a true daily maximum, which is what "
        "the IMD rules use. **The records end in July 2022** (September for Bhubaneswar and "
        "Rourkela), so for today's date there are no recent records and the prediction rests on "
        "the history of that time of year.\n"
        "- **Normals** are computed in SQL as the average maximum of the same calendar day (±7 "
        "days) over 1990-2022. IMD uses its own official 30-year normals, so alerts here can "
        "differ from IMD bulletins; this period includes recent warm years, which tends to give "
        "fewer alerts.\n"
        "- **Prediction:** with recent records (March to June), a least-squares model learned from "
        "the city's earlier years; otherwise the history of the date. It uses no weather forecast, "
        "can miss sudden changes, and tends to under-predict extreme days.\n"
        "- **Rourkela** has only about 14 months of data: no normal and no prediction.\n"
        "- **Station type** (IMD threshold) is an assumption: Mumbai, Chennai and Bhubaneswar are "
        "treated as coastal (37 °C), the others as plains (40 °C). Bangalore never reaches 40 °C, "
        "so it never reaches the heat-wave level.\n"
        "- **Missing records:** 4 to 16 % of days have no maximum temperature. The model then uses "
        "the last record (up to 3 days old) with wider uncertainty.\n"
        "- **Jodhpur** is not included: the file supplied is an exact copy of the Bangalore "
        "file. Two labels in the coordinates file were also swapped and have been corrected.")
    st.dataframe(_quality, hide_index=True)
