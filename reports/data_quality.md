# Relatorio de qualidade de dados -- bikes_2026_01..06.csv

Gerado em 2026-07-25 21:03. Este relatorio e so leitura/diagnostico; nenhum arquivo em data/processed/ foi criado ou alterado.

## 0. Consistencia de schema entre arquivos

Todos os 6 arquivos tem as mesmas colunas, na mesma ordem: **True**.

| file | matches_reference |
| --- | --- |
| bikes_2026_01.csv | True |
| bikes_2026_02.csv | True |
| bikes_2026_03.csv | True |
| bikes_2026_04.csv | True |
| bikes_2026_05.csv | True |
| bikes_2026_06.csv | True |


## 1. Volume por arquivo e gap temporal jan/fev

Criterio: o gap entre janeiro e fevereiro e apenas quantificado e documentado aqui -- **sem imputacao nem interpolacao**. Janeiro deve ser tratado como mes PARCIAL (dias 1 a 24) nas analises seguintes.

| file | rows_raw | rows_dedup | duplicates_removed | min_last_reported | max_last_reported |
| --- | --- | --- | --- | --- | --- |
| bikes_2026_01.csv | 470766 | 470766 | 0 | 2026-01-01 00:05:00 | 2026-01-24 23:55:00 |
| bikes_2026_02.csv | 454623 | 454623 | 0 | 2026-02-05 16:55:00 | 2026-02-28 23:55:00 |
| bikes_2026_03.csv | 625668 | 625668 | 0 | 2026-03-01 00:05:00 | 2026-03-31 23:55:00 |
| bikes_2026_04.csv | 617218 | 617218 | 0 | 2026-04-01 00:05:00 | 2026-04-30 23:55:00 |
| bikes_2026_05.csv | 637304 | 637304 | 0 | 2026-05-01 00:05:00 | 2026-05-31 23:55:00 |
| bikes_2026_06.csv | 606511 | 606511 | 0 | 2026-06-01 00:05:00 | 2026-06-30 23:55:00 |

**Gap jan/fev**: ultima leitura em janeiro em `2026-01-24 23:55:00`, primeira leitura em fevereiro em `2026-02-05 16:55:00` -- um buraco de **11 days 17:00:00** sem nenhuma leitura registrada, em nenhuma estacao.


## 2. Completude -- nulos em colunas criticas

Colunas que nunca deveriam ter nulo, por serem chave/identificacao de cada leitura:


Total de nulos encontrados nas colunas criticas (todas os arquivos, todas as colunas): **0**.

| column | file | null_count |
| --- | --- | --- |
| system_id | bikes_2026_01.csv | 0 |
| system_id | bikes_2026_02.csv | 0 |
| system_id | bikes_2026_03.csv | 0 |
| system_id | bikes_2026_04.csv | 0 |
| system_id | bikes_2026_05.csv | 0 |
| system_id | bikes_2026_06.csv | 0 |
| station_id | bikes_2026_01.csv | 0 |
| station_id | bikes_2026_02.csv | 0 |
| station_id | bikes_2026_03.csv | 0 |
| station_id | bikes_2026_04.csv | 0 |
| station_id | bikes_2026_05.csv | 0 |
| station_id | bikes_2026_06.csv | 0 |
| last_reported | bikes_2026_01.csv | 0 |
| last_reported | bikes_2026_02.csv | 0 |
| last_reported | bikes_2026_03.csv | 0 |
| last_reported | bikes_2026_04.csv | 0 |
| last_reported | bikes_2026_05.csv | 0 |
| last_reported | bikes_2026_06.csv | 0 |


## 3. Duplicidade -- chave (station_id, last_reported)

Criterio: deduplicar por `(station_id, last_reported)`, mantendo a **primeira** ocorrencia (arquivos processados em ordem jan->jun). Isso tambem resolve overlap de timestamp entre arquivos.

Total de linhas lidas: **3412090**. Total de duplicatas descartadas: **0** (**0.000%**). Linhas restantes apos dedup: **3412090**.


## 4. Violacoes de capacity

Criterio: esperado `num_bikes_available + num_docks_available <= capacity`. Apenas reportando; nenhuma linha foi removida nesta etapa.

Linhas avaliadas (capacity/bikes/docks nao nulos, apos dedup): **3412090**. Violacoes (`>` capacity): **102** (**0.0030%**).


## 5. Variantes de string nas colunas booleanas

Valores brutos encontrados (somando os 6 arquivos), por coluna:

| column | raw_value | count |
| --- | --- | --- |
| is_installed | true | 3408656 |
| is_installed | false | 3434 |
| is_renting | true | 3408656 |
| is_renting | false | 3434 |
| is_returning | true | 3408656 |
| is_returning | false | 3434 |


## 6. Cadencia de leitura (esperada: 5 em 5 minutos)

Distribuicao dos 15 deltas mais comuns entre leituras consecutivas da mesma estacao:

| delta_minutes | count | pct |
| --- | --- | --- |
| 10 | 2115249 | 61.995% |
| 5 | 1275441 | 37.381% |
| 15 | 19915 | 0.584% |
| 20 | 320 | 0.009% |
| 25 | 121 | 0.004% |
| 45 | 99 | 0.003% |
| 30 | 81 | 0.002% |
| 50 | 75 | 0.002% |
| 80 | 70 | 0.002% |
| 505 | 59 | 0.002% |
| 75 | 59 | 0.002% |
| 16870 | 58 | 0.002% |
| 485 | 50 | 0.001% |
| 60 | 44 | 0.001% |
| 55 | 42 | 0.001% |


Total de buracos (delta > 5min) encontrados: **2136534**, sendo **2115249** de apenas 1 leitura faltando (o padrao rotineiro de 10min visto acima, nao necessariamente perda de dado) e **21285** com 2 ou mais leituras faltando seguidas (evidencia mais forte de buraco real).


Os 15 maiores buracos (por leituras faltantes):

| station_id | gap_start | gap_end | missing_periods |
| --- | --- | --- | --- |
| 34 | 2026-01-13 10:55:00 | 2026-02-12 14:05:00 | 8677 |
| 105 | 2026-01-24 23:45:00 | 2026-02-05 17:05:00 | 3375 |
| 113 | 2026-01-24 23:45:00 | 2026-02-05 17:05:00 | 3375 |
| 25 | 2026-01-24 23:45:00 | 2026-02-05 17:05:00 | 3375 |
| 65 | 2026-01-24 23:45:00 | 2026-02-05 17:05:00 | 3375 |
| 100 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 102 | 2026-01-24 23:45:00 | 2026-02-05 17:00:00 | 3374 |
| 115 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 15 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 22 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 33 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 36 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 5 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 56 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |
| 6 | 2026-01-24 23:50:00 | 2026-02-05 17:05:00 | 3374 |


## 7. Timezone -- hora local irlandesa vs. UTC

Criterio: observar o comportamento de `last_reported` na virada do horario de verao europeu (**2026-03-29**, ultimo domingo de marco/2026 -- relogios adiantam de 01:00 pra 02:00 na hora local, entao a hora local 01:00-01:59 simplesmente nao existe nesse dia).

Buracos de leitura encontrados na janela 00:50-02:10 desse dia: **9**.

**Conclusao**: Nenhum buraco de leitura na janela da troca de horario de verao -- indicio de que last_reported esta em UTC (ou o dataset nao modela DST).

| station_id | gap_start | gap_end | missing_periods |
| --- | --- | --- | --- |
| 107 | 2026-03-29 02:10:00 | 2026-03-29 02:25:00 | 2 |
| 109 | 2026-03-29 01:30:00 | 2026-03-29 01:45:00 | 2 |
| 110 | 2026-03-29 01:40:00 | 2026-03-29 01:55:00 | 2 |
| 111 | 2026-03-29 00:50:00 | 2026-03-29 01:05:00 | 2 |
| 15 | 2026-03-29 01:50:00 | 2026-03-29 02:05:00 | 2 |
| 44 | 2026-03-29 01:10:00 | 2026-03-29 01:25:00 | 2 |
| 45 | 2026-03-29 01:00:00 | 2026-03-29 01:15:00 | 2 |
| 56 | 2026-03-29 01:30:00 | 2026-03-29 01:45:00 | 2 |
| 62 | 2026-03-29 00:50:00 | 2026-03-29 01:05:00 | 2 |


## 8. Tabela dimensao de estacoes (derivada das leituras)

Criterio: `name`/`lat`/`lon`/`capacity` por estacao sao a MODA (valor mais frequente) observada nas leituras, nao um valor fixo de cadastro. `n_distinct_*` > 1 indica que o campo variou ao longo do periodo pra aquela estacao (potencial instabilidade de dado, nao necessariamente erro).


Total de estacoes derivadas dos dados: **115**. Estacoes com pelo menos 1 campo instavel ao longo do tempo: **17**.

| station_id | name_mode | n_distinct_name | lat_mode | n_distinct_lat | lon_mode | n_distinct_lon | capacity_mode | n_distinct_capacity | n_readings |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 18 | GRANTHAM STREET | 1 | 53.334126 | 2 | -6.2656674 | 2 | 30 | 1 | 31933 |
| 19 | HERBERT PLACE | 1 | 53.3347 | 2 | -6.245139 | 2 | 30 | 1 | 33074 |
| 20 | JAMES STREET EAST | 1 | 53.336487 | 2 | -6.248174 | 2 | 30 | 1 | 28750 |
| 21 | LEINSTER STREET SOUTH | 1 | 53.342144 | 2 | -6.2541847 | 2 | 30 | 1 | 31565 |
| 23 | CUSTOM HOUSE | 1 | 53.34828 | 2 | -6.254358 | 2 | 30 | 1 | 31927 |
| 24 | CATHAL BRUGHA STREET | 1 | 53.352215 | 2 | -6.260328 | 2 | 20 | 1 | 31621 |
| 26 | MERRION SQUARE WEST | 1 | 53.34007 | 2 | -6.251716 | 2 | 20 | 1 | 29386 |
| 27 | MOLESWORTH STREET | 1 | 53.341236 | 2 | -6.2575936 | 2 | 20 | 1 | 30847 |
| 29 | ORMOND QUAY UPPER | 1 | 53.346107 | 2 | -6.268345 | 2 | 29 | 1 | 31980 |
| 34 | LENNOX STREET | 2 | 53.331383 | 2 | -6.265023 | 2 | 40 | 2 | 29250 |
| 36 | ST. STEPHEN'S GREEN EAST | 1 | 53.337635 | 2 | -6.256218 | 2 | 40 | 1 | 29649 |
| 38 | TALBOT STREET | 1 | 53.350998 | 2 | -6.252661 | 2 | 40 | 1 | 33149 |
| 41 | HARCOURT TERRACE | 1 | 53.332417 | 2 | -6.2578697 | 2 | 20 | 1 | 27987 |
| 44 | UPPER SHERRARD STREET | 1 | 53.358593 | 2 | -6.2602983 | 2 | 30 | 1 | 28432 |
| 5 | CHARLEMONT PLACE | 1 | 53.330704 | 2 | -6.260004 | 2 | 40 | 1 | 35948 |
| 56 | MOUNT STREET LOWER | 1 | 53.337986 | 2 | -6.241539 | 2 | 40 | 1 | 30544 |
| 7 | HIGH STREET | 1 | 53.34342 | 2 | -6.2744136 | 2 | 29 | 1 | 27619 |


## 9. Reconciliacao com stations.csv (referencia)

Criterio: stations.csv NAO e usado pra corrigir a dimensao derivada -- so pra apontar divergencias.

Estacoes so nas leituras (nao cadastradas em stations.csv): **['1', '14', '20', '35', '60', '70']**

Estacoes so no cadastro (nunca apareceram nas leituras): **['81']**


Divergencias de nome (case-insensitive): **1**

| station_id | name_mode | name_ref |
| --- | --- | --- |
| 34 | LENNOX STREET | PORTOBELLO HARBOUR |


Divergencias de latitude (> 0.001 grau): **1**

| station_id | lat_mode | lat_ref |
| --- | --- | --- |
| 34 | 53.331383 | 53.330362 |


Divergencias de longitude (> 0.001 grau): **0**
