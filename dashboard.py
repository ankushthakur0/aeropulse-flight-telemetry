"""
AeroPulse - live telemetry dashboard.

Run the simulator in one terminal:
    python3 telemetry_simulator.py
Run the dashboard in another:
    streamlit run dashboard.py
"""

import sqlite3

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DB_FILE = "aeropulse.db"
TEMP_ALERT_C = 110
VIBRATION_ALERT_G = 1.2

st.set_page_config(page_title="AeroPulse", page_icon="✈️", layout="wide")


@st.cache_resource
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)


def query(sql, params=()):
    try:
        return pd.read_sql_query(sql, get_conn(), params=params)
    except Exception:
        return pd.DataFrame()


def gauge(title, value, vmin, vmax, warn_at=None, unit=""):
    color = "#e5484d" if warn_at is not None and value >= warn_at else "#3b82f6"
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            number={"suffix": unit},
            title={"text": title},
            gauge={
                "axis": {"range": [vmin, vmax]},
                "bar": {"color": color},
                "threshold": {"line": {"color": "#e5484d", "width": 3}, "value": warn_at}
                if warn_at is not None
                else None,
            },
        )
    )
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=50, b=10))
    return fig


def line_chart(df, column, title, color, warn_at=None):
    fig = go.Figure(go.Scatter(x=df["ts"], y=df[column], mode="lines", line=dict(color=color, width=2)))
    if warn_at is not None:
        fig.add_hline(y=warn_at, line_dash="dash", line_color="#e5484d", annotation_text="limit")
    flagged = df[df["alert"].notna()]
    if not flagged.empty:
        fig.add_trace(
            go.Scatter(
                x=flagged["ts"],
                y=flagged[column],
                mode="markers",
                marker=dict(color="#e5484d", size=7),
                name="alert",
            )
        )
    fig.update_layout(title=title, height=260, margin=dict(l=10, r=10, t=40, b=10), showlegend=False)
    return fig


# --- Sidebar ------------------------------------------------------------------
st.sidebar.title("✈️ AeroPulse")
flights = query("SELECT flight_id, MAX(ts) AS last_ts FROM telemetry GROUP BY flight_id ORDER BY last_ts DESC")

if flights.empty:
    st.title("AeroPulse")
    st.warning("No data yet. Start the simulator: `python3 telemetry_simulator.py`")
    st.stop()

flight_id = st.sidebar.selectbox("Flight", flights["flight_id"].tolist(), index=0)
window = st.sidebar.slider("Readings shown", 30, 600, 180, step=30)
refresh = st.sidebar.slider("Refresh (seconds)", 1, 10, 1)
st.sidebar.caption("Newest flight is selected by default. Pick an older flight to review it.")


@st.fragment(run_every=refresh)
def live_view():
    df = query(
        "SELECT * FROM (SELECT * FROM telemetry WHERE flight_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
        (flight_id, window),
    )
    if df.empty:
        st.info("Waiting for readings...")
        return
    df["ts"] = pd.to_datetime(df["ts"])
    latest = df.iloc[-1]

    st.title(f"Flight {flight_id}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Phase", latest["phase"])
    c2.metric("Heading", f"{latest['heading_deg']:.0f}°")
    c3.metric("Readings in view", len(df))

    if latest["alert"]:
        st.error(f"ACTIVE ALERT: {latest['alert'].replace(',', ', ')}")
    else:
        st.success("All systems nominal")

    g1, g2, g3, g4 = st.columns(4)
    g1.plotly_chart(gauge("Airspeed", latest["airspeed_kts"], 0, 600, unit=" kts"))
    g2.plotly_chart(gauge("Engine temp", latest["engine_temp_c"], 60, 140, TEMP_ALERT_C, "°C"))
    g3.plotly_chart(gauge("Fuel", latest["fuel_pct"], 0, 100, unit="%"))
    g4.plotly_chart(gauge("Vibration", latest["vibration_g"], 0, 2, VIBRATION_ALERT_G, " g"))

    a, b = st.columns(2)
    a.plotly_chart(line_chart(df, "altitude_ft", "Altitude (ft)", "#3b82f6"))
    b.plotly_chart(line_chart(df, "airspeed_kts", "Airspeed (kts)", "#10b981"))
    c, d = st.columns(2)
    c.plotly_chart(line_chart(df, "engine_temp_c", "Engine temp (°C)", "#f59e0b", TEMP_ALERT_C))
    d.plotly_chart(line_chart(df, "vibration_g", "Vibration (g)", "#8b5cf6", VIBRATION_ALERT_G))

    st.subheader("Alert log")
    alerts = query(
        "SELECT ts, phase, alert, engine_temp_c, vibration_g FROM telemetry "
        "WHERE flight_id = ? AND alert IS NOT NULL ORDER BY id DESC LIMIT 20",
        (flight_id,),
    )
    if alerts.empty:
        st.caption("No alerts for this flight.")
    else:
        st.dataframe(alerts, hide_index=True)


live_view()
