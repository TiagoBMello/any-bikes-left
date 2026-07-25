-- estacao vazia = 2 bikes ou menos disponiveis
-- usa sempre timestamp_local (nunca utc), bate com hora real de deslocamento

-- 1. leituras por mes
-- jan/fev sao parciais por causa do buraco de 11 dias na coleta (2026-01-24 a 2026-02-05)
SELECT
    strftime('%Y-%m', timestamp_local) AS month,
    COUNT(*) AS total_readings
FROM readings
GROUP BY month
ORDER BY month;


-- 2. as 10 maiores estacoes por capacidade
-- nao precisa de join, toda info esta na dimensao
SELECT
    name AS station_name,
    capacity,
    lat,
    lon
FROM stations
ORDER BY capacity DESC
LIMIT 10;


-- 3. media de bikes disponiveis por hora
-- strftime('%H') devolve string de 2 caracteres ('00' a '23')
SELECT
    strftime('%H', timestamp_local) AS hour_of_day,
    ROUND(AVG(num_bikes_available), 2) AS avg_bikes_available,
    COUNT(*) AS total_readings
FROM readings
GROUP BY hour_of_day
ORDER BY hour_of_day;


-- 4. as 10 estacoes mais vazias, em termos absolutos
-- sum(case when...) conta so as linhas que batem a condicao, count(*) conta todas
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


-- 5. percentual de leituras vazias, por estacao
-- 100.0 (nao 100) forca divisao decimal, com inteiro o sqlite trunca pra 0
-- having filtra depois do group by, where filtra antes; count(*) so existe apos o group by
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


-- 6. vazio por hora, dia de semana vs fim de semana
-- strftime('%w') devolve dia da semana como string, '0' domingo, '6' sabado
-- usa percentual em vez de contagem porque tem 5 dias de semana pra 2 de fim de semana
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
