"""
Phase 2: build data/dublinbikes.db from the 6 monthly CSVs in data/raw/.

This script reads the raw readings in chunks, cleans them, and writes two
tables:
  - stations: one row per station_id, using the MOST RECENT name/lat/lon/
    capacity seen for that station (some stations were renamed or resized
    during the period, see reports/data_quality.md from phase 1).
  - readings: one row per (station_id, timestamp_utc), with booleans
    converted to 0/1 and both a UTC and a Europe/Dublin timestamp.

Cleaning rules applied while loading:
  - duplicate (station_id, timestamp_utc) rows are dropped, keeping the
    first occurrence.
  - rows where num_bikes_available + num_docks_available > capacity are
    dropped (same rule phase 1 only reported on; here we actually remove
    them, since this is the data going into the database).
"""

import sqlite3
from pathlib import Path

import pandas

RAW_DIRECTORY = Path("data/raw")
MONTHLY_CSV_FILES = sorted(RAW_DIRECTORY.glob("bikes_2026_*.csv"))
DATABASE_PATH = Path("data/dublinbikes.db")
CHUNK_SIZE = 200_000

# Columns we actually need from the raw CSVs, with the type each one
# should be read as. Passing this list to usecols keeps every chunk
# smaller by skipping columns the database schema does not need
# (system_id, short_name, address, region_id).
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
    """
    Receives one value from an is_installed/is_renting/is_returning cell
    (the string "true" or "false"). Returns 1 for "true" and 0 for
    anything else.
    """
    if value == "true":
        return 1
    return 0


def add_boolean_int_columns(chunk):
    """
    Receives a chunk where is_installed/is_renting/is_returning are still
    the raw "true"/"false" strings. Returns the same chunk with those
    three columns replaced by integers 0/1.
    """
    chunk["is_installed"] = chunk["is_installed"].map(convert_boolean_string_to_int)
    chunk["is_renting"] = chunk["is_renting"].map(convert_boolean_string_to_int)
    chunk["is_returning"] = chunk["is_returning"].map(convert_boolean_string_to_int)
    return chunk


def add_timestamp_columns(chunk):
    """
    Receives a chunk where last_reported is a naive datetime column (phase
    1 found no DST gap in the data, so it is already UTC). Returns the
    chunk with two new columns: timestamp_utc (timezone-aware, UTC) and
    timestamp_local (timezone-aware, Europe/Dublin).
    """
    chunk["timestamp_utc"] = chunk["last_reported"].dt.tz_localize("UTC")
    chunk["timestamp_local"] = chunk["timestamp_utc"].dt.tz_convert("Europe/Dublin")
    return chunk


def update_latest_station_info(chunk, latest_station_info):
    """
    Receives a cleaned chunk (with timestamp_utc already added) and the
    dictionary that tracks the most recent station info seen so far
    (station_id -> dict of timestamp_utc/name/lat/lon/capacity). Returns
    nothing; updates latest_station_info in place, one station_id at a
    time, keeping only the row with the largest timestamp_utc.
    """
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
    """
    Receives a chunk of readings and the set of (station_id,
    timestamp_utc) keys already seen in earlier chunks. Returns a tuple:
    the chunk with duplicate rows removed (keeping the first occurrence),
    and the number of duplicate rows that were dropped.
    """
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
    """
    Receives a chunk of readings. Returns a tuple: the chunk with rows
    removed where num_bikes_available + num_docks_available is greater
    than capacity, and the number of rows that were dropped.
    """
    total_available = chunk["num_bikes_available"] + chunk["num_docks_available"]
    keep_row = total_available <= chunk["capacity"]
    violation_count = int((~keep_row).sum())
    valid_chunk = chunk[keep_row].reset_index(drop=True)
    return valid_chunk, violation_count


def create_stations_table(connection):
    """
    Receives an open sqlite3 connection. Creates the stations table
    (dimension table, one row per station) if it does not exist yet.
    Returns nothing.
    """
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
    """
    Receives an open sqlite3 connection. Creates the readings table (fact
    table, one row per station reading) if it does not exist yet. The
    FOREIGN KEY documents the relationship to stations; it is not enforced
    at runtime here because readings are inserted before the stations
    table is filled (see build_database for the reason).
    Returns nothing.
    """
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
    """
    Receives an open sqlite3 connection. Creates an index on
    (station_id, timestamp_utc), the pair every time series query filters
    on. Built after all rows are inserted, since that is faster than
    updating the index on every single insert. Returns nothing.
    """
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_readings_station_time "
        "ON readings (station_id, timestamp_utc)"
    )


def insert_readings_chunk(connection, chunk):
    """
    Receives an open sqlite3 connection and a cleaned chunk of readings.
    Inserts every row into the readings table. Returns nothing.
    """
    rows_to_insert = []
    for row in chunk.itertuples():
        rows_to_insert.append((
            row.station_id,
            str(row.timestamp_utc),
            str(row.timestamp_local),
            int(row.num_bikes_available),  # pandas' Int64 dtype yields numpy.int64, which sqlite3 stores as a raw BLOB instead of an INTEGER unless converted to a plain Python int first
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
    """
    Receives an open sqlite3 connection and the dictionary of most recent
    station info (station_id -> name/lat/lon/capacity). Inserts one row
    per station into the stations table. Returns nothing.
    """
    rows_to_insert = []
    for station_id, info in latest_station_info.items():
        rows_to_insert.append((station_id, info["name"], info["lat"], info["lon"], int(info["capacity"])))  # same numpy.int64 -> BLOB issue as num_bikes_available, see insert_readings_chunk
    connection.executemany(
        "INSERT INTO stations (station_id, name, lat, lon, capacity) VALUES (?, ?, ?, ?, ?)",
        rows_to_insert,
    )


def count_table_rows(connection, table_name):
    """
    Receives an open sqlite3 connection and a table name. Returns the
    number of rows currently in that table.
    """
    cursor = connection.execute(f"SELECT COUNT(*) FROM {table_name}")
    row_count = cursor.fetchone()[0]
    return row_count


def process_chunk(connection, chunk, latest_station_info, seen_reading_keys):
    """
    Receives an open connection, one raw chunk of readings, the running
    dictionary of latest station info, and the running set of seen
    (station_id, timestamp_utc) keys. Cleans the chunk, updates the
    station info dictionary, and inserts the surviving readings. Returns
    a tuple: (duplicate_rows_dropped, capacity_violation_rows_dropped).
    """
    chunk = add_boolean_int_columns(chunk)
    chunk = add_timestamp_columns(chunk)
    update_latest_station_info(chunk, latest_station_info)
    chunk, duplicate_count = remove_duplicate_readings(chunk, seen_reading_keys)
    chunk, violation_count = remove_capacity_violations(chunk)
    insert_readings_chunk(connection, chunk)
    return duplicate_count, violation_count


def print_summary(connection, total_duplicates_dropped, total_capacity_violations_dropped):
    """
    Receives an open connection and the two running drop counters. Prints
    the final row counts for both tables and how many rows each cleaning
    rule dropped. Returns nothing.
    """
    stations_row_count = count_table_rows(connection, "stations")
    readings_row_count = count_table_rows(connection, "readings")
    print(f"stations table: {stations_row_count} rows")
    print(f"readings table: {readings_row_count} rows")
    print(f"duplicate readings dropped: {total_duplicates_dropped}")
    print(f"capacity violations dropped: {total_capacity_violations_dropped}")


def main():
    """
    Receives nothing. Runs the full phase 2 pipeline: reads the 6 monthly
    CSVs in chunks, builds the stations and readings tables in
    data/dublinbikes.db, and prints a summary. Returns nothing.
    """
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()  # rebuild from scratch every run, so reruns never double-insert

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
