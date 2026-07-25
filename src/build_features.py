"""
fase 3: monta data/processed/hourly_features.csv, uma linha por estacao-hora
agregacao em hora utc, nunca local: hora local em dublin some (marco) ou repete (outubro) por causa do horario de verao
avg_bikes fica no csv so como diagnostico, nunca como feature: vem da mesma coluna que gera o alvo (leakage)
"""

import sqlite3
from pathlib import Path

import pandas

DATABASE_PATH = Path("data/dublinbikes.db")
WEATHER_CSV_PATH = Path("data/raw/weather_dublin.csv")
OUTPUT_CSV_PATH = Path("data/processed/hourly_features.csv")
DUBLIN_TIMEZONE = "Europe/Dublin"
EMPTY_BIKES_THRESHOLD = 2          # define o que conta como estacao vazia
MINIMUM_READINGS_PER_HOUR = 3      # poucas leituras na hora, descarta (dado insuficiente)
WEATHER_COLUMNS_TO_KEEP = ["temp", "prcp", "rhum", "wspd"]


def read_readings(connection):
    query = "SELECT station_id, timestamp_utc, num_bikes_available, is_installed FROM readings"
    readings = pandas.read_sql_query(query, connection)
    readings["timestamp_utc"] = pandas.to_datetime(readings["timestamp_utc"], utc=True)
    return readings


def remove_uninstalled_readings(readings):
    keep_row = readings["is_installed"] == 1
    dropped_count = int((~keep_row).sum())
    installed_readings = readings[keep_row].reset_index(drop=True)
    return installed_readings, dropped_count


def add_hour_utc_column(readings):
    readings["hour_utc"] = readings["timestamp_utc"].dt.floor("h")
    return readings


def aggregate_readings_by_hour(readings):
    readings["is_empty_reading"] = (readings["num_bikes_available"] <= EMPTY_BIKES_THRESHOLD).astype(int)
    grouped = readings.groupby(["station_id", "hour_utc"])
    hourly = grouped.agg(
        readings_count=("num_bikes_available", "count"),
        empty_readings=("is_empty_reading", "sum"),
        avg_bikes=("num_bikes_available", "mean"),  # diagnostico, nunca feature (mesma coluna do alvo)
    )
    hourly = hourly.reset_index()
    hourly["is_empty"] = (hourly["empty_readings"] >= 1).astype(int)
    hourly["empty_share"] = hourly["empty_readings"] / hourly["readings_count"]  # cadencia e bimodal (10min/5min), share tira o vies de amostragem que is_empty tem
    hourly = hourly.drop(columns=["empty_readings"])
    return hourly


def remove_low_count_hours(hourly):
    keep_row = hourly["readings_count"] >= MINIMUM_READINGS_PER_HOUR
    dropped_count = int((~keep_row).sum())
    valid_hourly = hourly[keep_row].reset_index(drop=True)
    return valid_hourly, dropped_count


def read_weather(csv_path):
    weather = pandas.read_csv(csv_path, usecols=["time"] + WEATHER_COLUMNS_TO_KEEP)
    weather["hour_utc"] = pandas.to_datetime(weather["time"], utc=True)  # csv de clima ja vem por hora e em utc, nao precisa de floor
    weather = weather.drop(columns=["time"])
    return weather


def join_readings_with_weather(hourly, weather):
    rows_before_join = len(hourly)
    joined = hourly.merge(weather, on="hour_utc", how="inner")
    dropped_count = rows_before_join - len(joined)
    return joined, dropped_count


def add_local_time_features(joined):
    hour_local = joined["hour_utc"].dt.tz_convert(DUBLIN_TIMEZONE)
    joined["hour_of_day"] = hour_local.dt.hour
    joined["day_of_week"] = hour_local.dt.dayofweek
    joined["is_weekend"] = (hour_local.dt.dayofweek >= 5).astype(int)
    joined["month"] = hour_local.dt.month
    return joined


def read_stations(connection):
    query = "SELECT station_id, name, lat, lon, capacity FROM stations"
    stations = pandas.read_sql_query(query, connection)
    return stations


def add_station_info(joined, stations):
    with_station_info = joined.merge(stations, on="station_id", how="left")
    return with_station_info


def count_null_values(features):
    null_counts = features.isna().sum()
    return null_counts


def print_summary(features, installed_dropped, low_count_dropped, no_weather_dropped, null_counts):
    print(f"final rows: {len(features)}")
    print(f"is_empty rate: {features['is_empty'].mean():.4f}")
    print(f"dropped (is_installed == 0): {installed_dropped}")
    print(f"dropped (readings_count < {MINIMUM_READINGS_PER_HOUR}): {low_count_dropped}")
    print(f"dropped (no matching weather hour): {no_weather_dropped}")
    print("null values per column:")
    print(null_counts)


def main():
    connection = sqlite3.connect(DATABASE_PATH)

    readings = read_readings(connection)
    readings, installed_dropped = remove_uninstalled_readings(readings)
    readings = add_hour_utc_column(readings)
    hourly = aggregate_readings_by_hour(readings)
    hourly, low_count_dropped = remove_low_count_hours(hourly)

    weather = read_weather(WEATHER_CSV_PATH)
    joined, no_weather_dropped = join_readings_with_weather(hourly, weather)
    joined = add_local_time_features(joined)

    stations = read_stations(connection)
    features = add_station_info(joined, stations)
    connection.close()

    features.to_csv(OUTPUT_CSV_PATH, index=False)

    null_counts = count_null_values(features)
    print_summary(features, installed_dropped, low_count_dropped, no_weather_dropped, null_counts)


if __name__ == "__main__":
    main()
