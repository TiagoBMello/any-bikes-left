"""
fase 2: monta data/dublinbikes.db a partir dos csvs de data/raw/
stations usa o valor mais recente por station_id, nao a moda (estacao 34 foi renomeada e teve capacity alterada)
"""

import sqlite3
from pathlib import Path

import pandas

RAW_DIRECTORY = Path("data/raw")
MONTHLY_CSV_FILES = sorted(RAW_DIRECTORY.glob("bikes_2026_*.csv"))
DATABASE_PATH = Path("data/dublinbikes.db")
CHUNK_SIZE = 200_000

# so le as colunas que a tabela usa, resto do csv fica de fora
COLUMN_TYPES = {
    "station_id": "string",
    "num_bikes_available": "Int64",
    "num_docks_available": "Int64",
    "is_installed": "string",
    "is_renting": "string",
    "is_returning": "string",
    "name": "string",
    "lat": "float64",
    "lon": "float64",
    "capacity": "Int64",
}
COLUMNS_TO_READ = list(COLUMN_TYPES.keys()) + ["last_reported"]


def convert_boolean_string_to_int(value):
    if value == "true":
        return 1
    return 0


def add_boolean_int_columns(chunk):
    chunk["is_installed"] = chunk["is_installed"].map(convert_boolean_string_to_int)
    chunk["is_renting"] = chunk["is_renting"].map(convert_boolean_string_to_int)
    chunk["is_returning"] = chunk["is_returning"].map(convert_boolean_string_to_int)
    return chunk


def add_timestamp_columns(chunk):
    chunk["timestamp_utc"] = chunk["last_reported"].dt.tz_localize("UTC")  # fase 1 confirmou que o dado ja vem em utc (sem buraco de dst)
    chunk["timestamp_local"] = chunk["timestamp_utc"].dt.tz_convert("Europe/Dublin")
    return chunk


def update_latest_station_info(chunk, latest_station_info):
    for row in chunk.itertuples():
        station_id = row.station_id
        is_new_station = station_id not in latest_station_info
        is_newer_reading = (
            not is_new_station
            and row.timestamp_utc > latest_station_info[station_id]["timestamp_utc"]
        )
        if is_new_station or is_newer_reading:
            latest_station_info[station_id] = {
                "timestamp_utc": row.timestamp_utc,
                "name": row.name,
                "lat": row.lat,
                "lon": row.lon,
                "capacity": row.capacity,
            }


def remove_duplicate_readings(chunk, seen_reading_keys):
    keep_row = []
    duplicate_count = 0
    for row in chunk.itertuples():
        key = (row.station_id, row.timestamp_utc)
        if key in seen_reading_keys:
            keep_row.append(False)
            duplicate_count += 1
        else:
            seen_reading_keys.add(key)
            keep_row.append(True)
    deduplicated_chunk = chunk[keep_row].reset_index(drop=True)
    return deduplicated_chunk, duplicate_count


def remove_capacity_violations(chunk):
    total_available = chunk["num_bikes_available"] + chunk["num_docks_available"]
    keep_row = total_available <= chunk["capacity"]
    violation_count = int((~keep_row).sum())
    valid_chunk = chunk[keep_row].reset_index(drop=True)
    return valid_chunk, violation_count


def create_stations_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stations (
            station_id TEXT PRIMARY KEY,
            name TEXT,
            lat REAL,
            lon REAL,
            capacity INTEGER
        )
        """
    )


def create_readings_table(connection):
    # fk so documental, nao aplicada: readings entra antes de stations existir
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS readings (
            station_id TEXT,
            timestamp_utc TEXT,
            timestamp_local TEXT,
            num_bikes_available INTEGER,
            num_docks_available INTEGER,
            is_installed INTEGER,
            is_renting INTEGER,
            is_returning INTEGER,
            FOREIGN KEY (station_id) REFERENCES stations (station_id)
        )
        """
    )


def create_readings_index(connection):
    # criado so no final, mais rapido que atualizar o indice a cada insert
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_readings_station_time "
        "ON readings (station_id, timestamp_utc)"
    )


def insert_readings_chunk(connection, chunk):
    rows_to_insert = []
    for row in chunk.itertuples():
        rows_to_insert.append((
            row.station_id,
            str(row.timestamp_utc),
            str(row.timestamp_local),
            int(row.num_bikes_available),  # Int64 do pandas vira numpy.int64, sqlite grava como blob se nao converter pra int puro
            int(row.num_docks_available),
            row.is_installed,
            row.is_renting,
            row.is_returning,
        ))
    connection.executemany(
        "INSERT INTO readings (station_id, timestamp_utc, timestamp_local, "
        "num_bikes_available, num_docks_available, is_installed, is_renting, is_returning) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows_to_insert,
    )


def insert_stations(connection, latest_station_info):
    rows_to_insert = []
    for station_id, info in latest_station_info.items():
        rows_to_insert.append((station_id, info["name"], info["lat"], info["lon"], int(info["capacity"])))  # mesmo problema de numpy.int64 -> blob, ver insert_readings_chunk
    connection.executemany(
        "INSERT INTO stations (station_id, name, lat, lon, capacity) VALUES (?, ?, ?, ?, ?)",
        rows_to_insert,
    )


def count_table_rows(connection, table_name):
    cursor = connection.execute(f"SELECT COUNT(*) FROM {table_name}")
    row_count = cursor.fetchone()[0]
    return row_count


def process_chunk(connection, chunk, latest_station_info, seen_reading_keys):
    chunk = add_boolean_int_columns(chunk)
    chunk = add_timestamp_columns(chunk)
    update_latest_station_info(chunk, latest_station_info)
    chunk, duplicate_count = remove_duplicate_readings(chunk, seen_reading_keys)
    chunk, violation_count = remove_capacity_violations(chunk)
    insert_readings_chunk(connection, chunk)
    return duplicate_count, violation_count


def print_summary(connection, total_duplicates_dropped, total_capacity_violations_dropped):
    stations_row_count = count_table_rows(connection, "stations")
    readings_row_count = count_table_rows(connection, "readings")
    print(f"stations table: {stations_row_count} rows")
    print(f"readings table: {readings_row_count} rows")
    print(f"duplicate readings dropped: {total_duplicates_dropped}")
    print(f"capacity violations dropped: {total_capacity_violations_dropped}")


def main():
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()  # recria do zero a cada run, senao reruns duplicam insert

    connection = sqlite3.connect(DATABASE_PATH)
    create_stations_table(connection)
    create_readings_table(connection)

    latest_station_info = {}
    seen_reading_keys = set()
    total_duplicates_dropped = 0
    total_capacity_violations_dropped = 0

    for path in MONTHLY_CSV_FILES:
        chunk_reader = pandas.read_csv(
            path,
            usecols=COLUMNS_TO_READ,
            dtype=COLUMN_TYPES,
            parse_dates=["last_reported"],
            chunksize=CHUNK_SIZE,
        )
        for chunk in chunk_reader:
            duplicate_count, violation_count = process_chunk(
                connection, chunk, latest_station_info, seen_reading_keys
            )
            total_duplicates_dropped += duplicate_count
            total_capacity_violations_dropped += violation_count

    insert_stations(connection, latest_station_info)
    create_readings_index(connection)
    connection.commit()

    print_summary(connection, total_duplicates_dropped, total_capacity_violations_dropped)
    connection.close()


if __name__ == "__main__":
    main()
