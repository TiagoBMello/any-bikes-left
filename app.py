"""app streamlit -- previsao de disponibilidade de bikes por estacao/hora"""

import joblib
import pandas as pd
import streamlit as st

MODEL_PATH = "models/model.pkl"
STATIONS_PATH = "data/stations.csv"

# rhum e wspd fixos: usuario nao sabe informar umidade/vento, e clima vale
# so 3% da importancia do modelo (permutation_importance, fase 8). valor =
# mediana do periodo de treino (jan-mai 2026)
RHUM_DEFAULT = 82.0
WSPD_DEFAULT = 18.0

DAY_NAMES = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


@st.cache_resource
def load_model():
    return joblib.load(MODEL_PATH)


@st.cache_data
def load_stations():
    return pd.read_csv(STATIONS_PATH)


def build_input_row(station, hour_of_day, day_of_week, temp, prcp):
    is_weekend = 1 if day_of_week >= 5 else 0
    return pd.DataFrame([{
        "hour_of_day": hour_of_day,
        "day_of_week": day_of_week,
        "is_weekend": is_weekend,
        "temp": temp,
        "prcp": prcp,
        "rhum": RHUM_DEFAULT,
        "wspd": WSPD_DEFAULT,
        "capacity": station["capacity"],
        "lat": station["lat"],
        "lon": station["lon"],
        "station_id": station["station_id"],
    }])


def risk_label(probability):
    if probability >= 0.5:
        return "risco alto"
    if probability >= 0.25:
        return "risco medio"
    return "risco baixo"


st.set_page_config(page_title="Any Bikes Left?", layout="centered")
st.title("Any Bikes Left?")
st.write("Preveja a chance de uma estacao do Dublin Bikes estar sem bicicletas disponiveis, numa hora especifica.")

stations = load_stations()
model = load_model()

station_name = st.selectbox("Estacao", sorted(stations["name"]))
day_of_week = st.selectbox("Dia da semana", options=range(7), format_func=lambda i: DAY_NAMES[i])
hour_of_day = st.slider("Hora do dia", 0, 23, 8)
temp = st.slider("Temperatura (C)", -5.0, 30.0, 8.3, step=0.5)
rain = st.checkbox("Vai chover?")
prcp = 0.5 if rain else 0.0

if st.button("Prever"):
    station = stations[stations["name"] == station_name].iloc[0]
    input_row = build_input_row(station, hour_of_day, day_of_week, temp, prcp)
    probability = model.predict_proba(input_row)[0, 1]

    st.metric("Probabilidade de estacao vazia", f"{probability * 100:.0f}%")
    st.write(f"**{risk_label(probability)}** de nao encontrar bike nessa estacao, nesse horario.")
