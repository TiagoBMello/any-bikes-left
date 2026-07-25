"""baixa clima horario de dublin e salva em csv"""

from datetime import datetime

import meteostat

# estacao real em vez de point, point nao devolve dado aqui
DUBLIN_AIRPORT = "03969"

# periodo bate com o range das leituras de bike
START = datetime(2026, 1, 1)
END = datetime(2026, 7, 1)


def download_hourly_weather(station_id, start, end):
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
