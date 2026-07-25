"""Download hourly weather data for Dublin and save it as CSV."""

from datetime import datetime

import meteostat

# Met station 03969 is Dublin Airport. A real station ID is used
# instead of a Point because virtual points return no data here.
DUBLIN_AIRPORT = "03969"

# The period must match the range of the bike readings.
START = datetime(2026, 1, 1)
END = datetime(2026, 7, 1)


def download_hourly_weather(station_id, start, end):
    """Receive a station id and two dates, return a DataFrame of hourly weather."""
    time_series = meteostat.hourly(station_id, start, end)
    return time_series.fetch()


def main():
    weather = download_hourly_weather(DUBLIN_AIRPORT, START, END)
    weather.to_csv("data/raw/weather_dublin.csv")
    print("Rows:", len(weather))
    print("Columns:", list(weather.columns))
    print(weather.head())


if __name__ == "__main__":
    main()
