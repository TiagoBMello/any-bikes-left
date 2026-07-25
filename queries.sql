-- =============================================================
-- any-bikes-left
-- Exploratory SQL queries on the Dublinbikes database
--
-- Database: data/dublinbikes.db
-- Tables:   stations (dimension, 115 rows)
--           readings (fact, ~3.4M rows)
--
-- A reading is considered "empty" when the station has
-- 2 bikes or fewer available.
-- All time-based queries use timestamp_local (Europe/Dublin),
-- never timestamp_utc, so that hours match real commuting times.
-- =============================================================


-- -------------------------------------------------------------
-- 1. How many readings per month?
--
-- Purpose: check data completeness. January and February are
-- expected to be partial due to a known 11-day collection gap
-- (2026-01-24 to 2026-02-05).
-- -------------------------------------------------------------
SELECT
    strftime('%Y-%m', timestamp_local) AS month,
    COUNT(*) AS total_readings
FROM readings
GROUP BY month
ORDER BY month;


-- -------------------------------------------------------------
-- 2. Which are the 10 largest stations by capacity?
--
-- Purpose: get a feel for the network. No JOIN needed here,
-- since all the information lives in the dimension table.
-- -------------------------------------------------------------
SELECT
    name AS station_name,
    capacity,
    lat,
    lon
FROM stations
ORDER BY capacity DESC
LIMIT 10;


-- -------------------------------------------------------------
-- 3. What is the average number of available bikes per hour?
--
-- Purpose: find the daily rhythm of the network. strftime('%H')
-- extracts the hour as a two-character string ('00' to '23').
-- ROUND keeps the output readable.
-- -------------------------------------------------------------
SELECT
    strftime('%H', timestamp_local) AS hour_of_day,
    ROUND(AVG(num_bikes_available), 2) AS avg_bikes_available,
    COUNT(*) AS total_readings
FROM readings
GROUP BY hour_of_day
ORDER BY hour_of_day;


-- -------------------------------------------------------------
-- 4. Which 10 stations were empty most often?
--
-- Purpose: identify the problem stations in absolute terms.
-- SUM(CASE WHEN ... THEN 1 ELSE 0 END) counts only the rows
-- that match the condition, while COUNT(*) counts every row.
-- -------------------------------------------------------------
SELECT
    s.name AS station_name,
    s.capacity,
    SUM(CASE WHEN r.num_bikes_available <= 2 THEN 1 ELSE 0 END) AS empty_readings,
    COUNT(*) AS total_readings
FROM readings AS r
JOIN stations AS s ON s.station_id = r.station_id
GROUP BY s.station_id, s.name, s.capacity
ORDER BY empty_readings DESC
LIMIT 10;


-- -------------------------------------------------------------
-- 5. What share of readings is empty, per station?
--
-- Purpose: absolute counts favour stations with more readings,
-- so this normalises by total readings.
--
-- Two details worth remembering:
--   - 100.0 (not 100) forces decimal division; with an integer
--     SQLite would truncate the result to 0.
--   - HAVING filters groups after aggregation, while WHERE
--     filters rows before it. COUNT(*) only exists after the
--     GROUP BY, so the filter has to be HAVING.
-- -------------------------------------------------------------
SELECT
    s.name AS station_name,
    s.capacity,
    COUNT(*) AS total_readings,
    SUM(CASE WHEN r.num_bikes_available <= 2 THEN 1 ELSE 0 END) AS empty_readings,
    ROUND(
        100.0 * SUM(CASE WHEN r.num_bikes_available <= 2 THEN 1 ELSE 0 END) / COUNT(*),
        1
    ) AS empty_percent
FROM readings AS r
JOIN stations AS s ON s.station_id = r.station_id
GROUP BY s.station_id, s.name, s.capacity
HAVING COUNT(*) > 10000
ORDER BY empty_percent DESC;


-- -------------------------------------------------------------
-- 6. How does emptiness change by hour, weekday vs weekend?
--
-- Purpose: this is the commuting pattern, and the strongest
-- candidate for a chart in the README.
--
-- strftime('%w') returns the weekday as a string, where
-- '0' is Sunday and '6' is Saturday.
--
-- Percentages are used instead of counts because there are
-- five weekdays for every two weekend days, which would make
-- raw counts impossible to compare.
-- -------------------------------------------------------------
SELECT
    strftime('%H', timestamp_local) AS hour_of_day,
    CASE
        WHEN strftime('%w', timestamp_local) IN ('0', '6') THEN 'weekend'
        ELSE 'weekday'
    END AS day_type,
    COUNT(*) AS total_readings,
    ROUND(
        100.0 * SUM(CASE WHEN num_bikes_available <= 2 THEN 1 ELSE 0 END) / COUNT(*),
        1
    ) AS empty_percent
FROM readings
GROUP BY hour_of_day, day_type
ORDER BY day_type, hour_of_day;
