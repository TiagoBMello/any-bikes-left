"""
Diagnostico de qualidade de dados dos CSVs mensais de disponibilidade de
bicicletas (Dublin Bikes / estilo GBFS station_status + station_information).

Este script APENAS LE data/raw/*.csv e ESCREVE um relatorio em
reports/data_quality.md. Nao grava nada em data/processed/ nem altera os
arquivos de origem.

Criterios de negocio aplicados (definidos com o usuario antes de codar):

  1. Gap jan/fev: quantificar e documentar o buraco de leituras entre os
     arquivos de janeiro e fevereiro. Sem imputacao, sem interpolacao.
  2. stations: a tabela dimensao de estacoes e derivada dos proprios dados
     de leitura (moda de name/lat/lon/capacity por station_id).
     data/raw/stations.csv serve so como referencia externa para
     reconciliacao -- divergencias sao reportadas, nao corrigidas.
  3. capacity: o esperado e num_bikes_available + num_docks_available <=
     capacity. Reportamos o percentual de violacoes de "maior que", sem
     remover nenhuma linha.
  4. Duplicidade: deduplicar por (station_id, last_reported), mantendo a
     PRIMEIRA ocorrencia (arquivos sao processados em ordem cronologica
     jan->jun). Isso tambem resolve overlap de timestamps entre arquivos.
  5. Timezone: checar se last_reported esta em hora local irlandesa ou em
     UTC observando o comportamento na virada do horario de verao europeu
     (ultimo domingo de marco de 2026): hora local "sobe" de 01:00 direto
     pra 02:00 (spring forward), o que apareceria como um buraco de ~60min
     comecando as 01:00 -- se os dados forem UTC, esse buraco nao existe.
  6. Booleans: as colunas is_installed/is_renting/is_returning sao lidas
     como texto puro (nao convertidas pelo pandas), e reportamos quais
     variantes de string aparecem nos 6 arquivos.
"""

import calendar                          # usado para achar o ultimo domingo de um mes (virada de DST)
import datetime as dt                    # tipos de data/hora usados na checagem de timezone
from collections import Counter, defaultdict  # estruturas de contagem usadas nos acumuladores
from pathlib import Path                 # manipulacao de caminhos de arquivo independente de SO

import pandas as pd                      # leitura em chunks e manipulacao tabular

# --------------------------------------------------------------------------
# Configuracao
# --------------------------------------------------------------------------

RAW_DIR = Path("data/raw")                                   # pasta com os 6 CSVs mensais + stations.csv
STATIONS_REF_PATH = RAW_DIR / "stations.csv"                 # cadastro de referencia (110 estacoes)
MONTHLY_FILES = sorted(RAW_DIR.glob("bikes_2026_*.csv"))      # os 6 arquivos mensais, em ordem cronologica pelo nome
REPORT_PATH = Path("reports/data_quality.md")                 # arquivo de saida do relatorio

CHUNKSIZE = 200_000                       # linhas lidas do disco por vez (evita carregar os ~3.4M de linhas inteiras)
EXPECTED_INTERVAL_MIN = 5                 # cadencia esperada de leitura, em minutos
CRITICAL_COLUMNS = ["system_id", "station_id", "last_reported"]  # colunas que nunca podem ser nulas
BOOLEAN_COLUMNS = ["is_installed", "is_renting", "is_returning"]  # colunas booleanas suspeitas de ter variantes de string
LAT_LON_TOLERANCE = 1e-3                  # tolerancia (graus) pra considerar lat/lon "iguais" na reconciliacao (~100m)

# dtype fixo por coluna: evita que o pandas infira tipos errados (ex.: ler
# "true"/"false" como bool nativo, o que esconderia variantes de string)
DTYPE_OVERRIDES = {
    "system_id": "string",
    "station_id": "string",          # mantido como string p/ nao perder comparabilidade com stations.csv
    "num_bikes_available": "Int64",  # inteiro anulavel (aceita NaN sem virar float)
    "num_docks_available": "Int64",
    "is_installed": "string",        # string crua, NAO bool: e o que o check de booleans precisa inspecionar
    "is_renting": "string",
    "is_returning": "string",
    "name": "string",
    "short_name": "string",
    "address": "string",
    "lat": "float64",
    "lon": "float64",
    "region_id": "string",
    "capacity": "Int64",
}


# --------------------------------------------------------------------------
# Leitura em chunks
# --------------------------------------------------------------------------

def iter_file_chunks(path: Path, chunksize: int = CHUNKSIZE):
    """
    Recebe: o caminho de um CSV mensal e o tamanho do chunk (numero de
    linhas por leitura).
    Devolve: um gerador de DataFrames, cada um com ate `chunksize` linhas
    do arquivo, com os tipos de coluna forcados por DTYPE_OVERRIDES e
    `last_reported` ja convertida para datetime. O uso de chunksize garante
    memoria limitada independente do tamanho do arquivo (aqui, ~3.4M linhas
    no total).
    """
    reader = pd.read_csv(
        path,
        dtype=DTYPE_OVERRIDES,            # forca os tipos declarados acima
        parse_dates=["last_reported"],    # converte a coluna de timestamp pra datetime64 na leitura
        chunksize=chunksize,               # devolve um iterador de DataFrames em vez do arquivo inteiro
    )
    for chunk in reader:                   # cada iteracao le o proximo bloco de `chunksize` linhas do disco
        yield chunk                        # entrega o chunk pra quem chamou; os anteriores podem ser liberados da memoria


# --------------------------------------------------------------------------
# Checagem 0: schema (colunas identicas nos 6 arquivos)
# --------------------------------------------------------------------------

def check_schema_consistency(paths: list[Path]) -> list[dict]:
    """
    Recebe: lista de Paths dos arquivos mensais.
    Devolve: lista de dicts (um por arquivo) com as colunas encontradas e
    se elas batem, em nome E ordem, com o primeiro arquivo da lista (usado
    como referencia). Le so o cabecalho (nrows=0) de cada arquivo, entao
    nao precisa de chunksize aqui.
    """
    results = []                                       # acumula um dict de resultado por arquivo
    reference_columns = None                            # colunas do primeiro arquivo, usadas como "gabarito"
    for path in paths:                                   # percorre os arquivos na ordem em que foram passados
        header_df = pd.read_csv(path, nrows=0)           # le so a linha de cabecalho, sem nenhum dado
        columns = list(header_df.columns)                # lista de nomes de coluna, na ordem do arquivo
        if reference_columns is None:                    # primeira iteracao: define a referencia
            reference_columns = columns
        results.append({
            "file": path.name,
            "columns": columns,
            "matches_reference": columns == reference_columns,  # True so se nomes E ordem forem identicos ao 1o arquivo
        })
    return results


# --------------------------------------------------------------------------
# Estado global (acumuladores atualizados a cada chunk)
# --------------------------------------------------------------------------

def init_state() -> dict:
    """
    Recebe: nada.
    Devolve: dict com todos os acumuladores usados durante a passagem pelos
    chunks. Criado uma unica vez, antes do loop principal, e atualizado
    in-place por cada uma das funcoes update_*.
    """
    return {
        "rows_raw_per_file": Counter(),        # nº de linhas lidas (antes de dedup), por arquivo
        "rows_dedup_per_file": Counter(),      # nº de linhas restantes (apos dedup), por arquivo
        "duplicates_per_file": Counter(),      # nº de linhas descartadas pela dedup, por arquivo
        "null_counts": defaultdict(Counter),   # null_counts[coluna][arquivo] = nº de nulos naquela coluna/arquivo
        "min_ts": None,                        # menor last_reported visto em todo o dataset (dado bruto)
        "max_ts": None,                        # maior last_reported visto em todo o dataset (dado bruto)
        "ts_range_per_file": {},               # arquivo -> (min_ts, max_ts) daquele arquivo especifico
        "boolean_variants": defaultdict(Counter),  # boolean_variants[coluna][valor_bruto] = contagem
        "seen_keys": set(),                    # chaves (station_id, last_reported_ns) ja vistas -- usado na dedup global
        "capacity_checked": 0,                 # nº de linhas deduplicadas com capacity+bikes+docks nao nulos, avaliadas
        "capacity_violations": 0,              # dessas, quantas tem bikes+docks > capacity
        "station_name": defaultdict(Counter),  # station_name[station_id][nome] = contagem (insumo pra moda)
        "station_lat": defaultdict(Counter),   # idem, latitude
        "station_lon": defaultdict(Counter),   # idem, longitude
        "station_capacity": defaultdict(Counter),  # idem, capacity
        "station_reading_count": Counter(),    # nº de leituras (deduplicadas) por station_id
        "last_seen_ts": {},                    # carry-over entre chunks/arquivos: station_id -> ultimo last_reported processado
        "gap_records": [],                     # lista de dicts, um por buraco > EXPECTED_INTERVAL_MIN encontrado
        "delta_histogram": Counter(),          # histograma de deltas (minutos, arredondados) entre leituras consecutivas
    }


# --------------------------------------------------------------------------
# Checagens por chunk (chamadas em sequencia dentro do loop principal)
# --------------------------------------------------------------------------

def update_raw_counts_and_nulls(chunk: pd.DataFrame, file_name: str, state: dict) -> None:
    """
    Recebe: um chunk CRU (antes da dedup), o nome do arquivo de origem e o
    dict de estado global.
    Devolve: None -- atualiza state["rows_raw_per_file"], state["null_counts"],
    state["min_ts"]/state["max_ts"] e state["ts_range_per_file"] in-place.
    """
    state["rows_raw_per_file"][file_name] += len(chunk)         # soma o nº de linhas deste chunk ao total do arquivo
    for col in CRITICAL_COLUMNS:                                 # para cada coluna que nunca deveria ter nulo
        state["null_counts"][col][file_name] += int(chunk[col].isna().sum())  # soma os nulos deste chunk
    chunk_min = chunk["last_reported"].min()                     # menor timestamp deste chunk
    chunk_max = chunk["last_reported"].max()                     # maior timestamp deste chunk
    if state["min_ts"] is None or chunk_min < state["min_ts"]:   # se ainda nao ha minimo global, ou este chunk trouxe algo menor
        state["min_ts"] = chunk_min                               # atualiza o minimo global
    if state["max_ts"] is None or chunk_max > state["max_ts"]:   # idem para o maximo
        state["max_ts"] = chunk_max
    prev_min, prev_max = state["ts_range_per_file"].get(file_name, (chunk_min, chunk_max))  # range ja visto deste arquivo (ou o do proprio chunk, na 1a vez)
    state["ts_range_per_file"][file_name] = (min(prev_min, chunk_min), max(prev_max, chunk_max))  # amplia o range do arquivo


def collect_boolean_variants(chunk: pd.DataFrame, state: dict) -> None:
    """
    Recebe: um chunk CRU e o dict de estado.
    Devolve: None -- atualiza state["boolean_variants"] in-place com a
    contagem de cada valor textual bruto encontrado nas 3 colunas booleanas
    (inclusive nulos, rotulados como "<NULL>").
    """
    for col in BOOLEAN_COLUMNS:                                   # para cada uma das colunas booleanas
        value_counts = chunk[col].value_counts(dropna=False)      # conta ocorrencias de cada valor bruto, incluindo NaN
        for raw_value, count in value_counts.items():             # percorre cada (valor, contagem) deste chunk
            key = "<NULL>" if pd.isna(raw_value) else str(raw_value)  # normaliza NaN pra uma chave textual legivel
            state["boolean_variants"][col][key] += int(count)      # soma a contagem deste chunk ao total da variante


def dedupe_chunk(chunk: pd.DataFrame, file_name: str, state: dict) -> pd.DataFrame:
    """
    Recebe: um chunk CRU, na ordem cronologica original do arquivo (cada
    arquivo mensal ja vem ordenado por last_reported), o nome do arquivo e
    o dict de estado (contem state["seen_keys"], o set global de chaves ja
    processadas em chunks/arquivos anteriores).
    Devolve: um novo DataFrame contendo so as linhas cuja chave
    (station_id, last_reported) ainda nao havia aparecido -- ou seja,
    mantem a PRIMEIRA ocorrencia de cada chave, respeitando a ordem em que
    os arquivos sao processados (jan -> jun). Isso remove duplicatas dentro
    do mesmo arquivo e tambem overlaps de timestamp entre arquivos.
    """
    ts_ns = chunk["last_reported"].values.astype("int64")        # timestamp como inteiro (ns); mais leve que Timestamp p/ guardar ~3.4M chaves num set
    keys = list(zip(chunk["station_id"], ts_ns))                  # chave composta por linha: (station_id, timestamp em ns)
    is_new = [key not in state["seen_keys"] for key in keys]      # True para chaves ainda nao vistas em nenhum chunk/arquivo anterior
    n_duplicates = len(chunk) - sum(is_new)                        # quantas linhas deste chunk sao duplicatas
    state["duplicates_per_file"][file_name] += n_duplicates        # acumula duplicatas descartadas deste arquivo
    for key, new in zip(keys, is_new):                              # percorre as chaves na ordem original do chunk
        if new:
            state["seen_keys"].add(key)                             # registra a chave como vista, pros proximos chunks/arquivos
    deduped = chunk[is_new].reset_index(drop=True)                  # filtra so as linhas novas e reindexa de 0
    state["rows_dedup_per_file"][file_name] += len(deduped)          # soma linhas que sobreviveram a dedup, deste arquivo
    return deduped                                                   # devolve o chunk deduplicado, pronto pras proximas checagens


def update_capacity_check(chunk: pd.DataFrame, state: dict) -> None:
    """
    Recebe: um chunk JA DEDUPLICADO e o dict de estado.
    Devolve: None -- atualiza state["capacity_checked"] e
    state["capacity_violations"] in-place. So considera linhas onde
    capacity, num_bikes_available e num_docks_available nao sao nulos
    simultaneamente.
    """
    has_all = (                                                     # mascara booleana: linhas com os 3 valores presentes
        chunk["capacity"].notna()
        & chunk["num_bikes_available"].notna()
        & chunk["num_docks_available"].notna()
    )
    evaluable = chunk.loc[has_all]                                   # subconjunto avaliavel (sem nulos nos 3 campos)
    total_available = evaluable["num_bikes_available"] + evaluable["num_docks_available"]  # soma bikes+docks por linha
    violations = (total_available > evaluable["capacity"]).sum()      # nº de linhas onde a soma excede a capacidade declarada
    state["capacity_checked"] += len(evaluable)                       # soma ao total de linhas avaliadas nesta checagem
    state["capacity_violations"] += int(violations)                    # soma ao total de violacoes encontradas


def update_station_dimension(chunk: pd.DataFrame, state: dict) -> None:
    """
    Recebe: um chunk JA DEDUPLICADO e o dict de estado.
    Devolve: None -- para cada station_id presente no chunk, atualiza os
    Counters de name/lat/lon/capacity (usados depois para calcular a moda
    de cada estacao) e o total de leituras por estacao.
    """
    grouped = chunk.groupby("station_id", observed=True)              # agrupa as linhas do chunk por estacao
    for station_id, group in grouped:                                  # percorre cada grupo (uma estacao) presente neste chunk
        state["station_name"][station_id].update(group["name"].dropna())         # soma ocorrencias de cada nome visto
        state["station_lat"][station_id].update(group["lat"].dropna())           # soma ocorrencias de cada latitude vista
        state["station_lon"][station_id].update(group["lon"].dropna())           # soma ocorrencias de cada longitude vista
        state["station_capacity"][station_id].update(group["capacity"].dropna()) # soma ocorrencias de cada capacity vista
        state["station_reading_count"][station_id] += len(group)                  # soma nº de leituras desta estacao neste chunk


def update_cadence_gaps(chunk: pd.DataFrame, state: dict) -> None:
    """
    Recebe: um chunk JA DEDUPLICADO, preservando a ordem cronologica
    original do arquivo, e o dict de estado (contem state["last_seen_ts"],
    o ultimo timestamp visto de cada estacao, carregado entre
    chunks/arquivos).
    Devolve: None -- atualiza state["gap_records"], state["delta_histogram"]
    e state["last_seen_ts"] in-place.
    """
    ordered = chunk.sort_values(["station_id", "last_reported"])       # ordena por estacao e depois por tempo, dentro do chunk
    prev_in_chunk = ordered.groupby("station_id")["last_reported"].shift(1)  # timestamp anterior da mesma estacao, dentro do chunk (NaT na 1a linha de cada estacao)
    carried_prev = pd.to_datetime(ordered["station_id"].map(state["last_seen_ts"]))  # timestamp anterior vindo de chunks/arquivos ja processados (map devolve NaN pra estacao nova; to_datetime normaliza pra NaT)
    prev_ts = prev_in_chunk.fillna(carried_prev)                        # usa o valor do proprio chunk quando existe; senao usa o carry-over
    has_prev = prev_ts.notna()                                          # linhas em que da pra calcular delta (nao e a 1a leitura da estacao em todo o dataset)
    delta_minutes = (ordered["last_reported"] - prev_ts).dt.total_seconds() / 60  # diferenca em minutos entre leituras consecutivas

    for station_id, prev, curr, delta in zip(                           # percorre linha a linha so as que tem delta valido
        ordered.loc[has_prev, "station_id"],
        prev_ts[has_prev],
        ordered.loc[has_prev, "last_reported"],
        delta_minutes[has_prev],
    ):
        rounded_delta = int(round(delta / EXPECTED_INTERVAL_MIN) * EXPECTED_INTERVAL_MIN)  # arredonda pro multiplo de 5min mais proximo (histograma)
        state["delta_histogram"][rounded_delta] += 1                     # soma 1 ao histograma de deltas observados
        if delta > EXPECTED_INTERVAL_MIN:                                 # delta maior que os 5min esperados = leitura(s) faltando no meio
            missing_periods = round(delta / EXPECTED_INTERVAL_MIN) - 1    # nº de leituras de 5min que deveriam existir no intervalo e nao existem
            state["gap_records"].append({
                "station_id": station_id,
                "gap_start": prev,                                        # ultima leitura antes do buraco
                "gap_end": curr,                                          # primeira leitura depois do buraco
                "missing_periods": missing_periods,                       # quantas leituras de 5min "sumiram" nesse intervalo
            })

    latest_per_station = ordered.groupby("station_id")["last_reported"].max()  # ultimo timestamp visto por estacao, neste chunk
    for station_id, ts in latest_per_station.items():                     # percorre cada estacao atualizada neste chunk
        state["last_seen_ts"][station_id] = ts                             # atualiza o carry-over pro proximo chunk/arquivo


# --------------------------------------------------------------------------
# Orquestracao do passe unico pelos dados
# --------------------------------------------------------------------------

def run_pipeline(paths: list[Path]) -> dict:
    """
    Recebe: lista de Paths dos arquivos mensais, em ordem cronologica.
    Devolve: o dict de estado totalmente populado, apos uma unica passagem
    por todos os chunks de todos os arquivos, aplicando as checagens na
    ordem correta (bruto -> dedup -> derivadas do dedup).
    """
    state = init_state()                                    # cria os acumuladores zerados
    for path in paths:                                        # processa os arquivos na ordem jan -> jun
        file_name = path.name                                  # nome do arquivo, usado como rotulo nas contagens por arquivo
        for chunk in iter_file_chunks(path):                    # le o arquivo em blocos de CHUNKSIZE linhas
            update_raw_counts_and_nulls(chunk, file_name, state)  # contagens de linhas e nulos, sobre o chunk cru
            collect_boolean_variants(chunk, state)                # variantes textuais das colunas booleanas, sobre o chunk cru
            deduped = dedupe_chunk(chunk, file_name, state)        # remove duplicatas (station_id, last_reported); devolve so linhas novas
            update_capacity_check(deduped, state)                   # violacoes de capacity, sobre o chunk ja deduplicado
            update_station_dimension(deduped, state)                # acumula name/lat/lon/capacity por estacao, sobre o deduplicado
            update_cadence_gaps(deduped, state)                      # detecta buracos na cadencia de 5min, sobre o deduplicado
    return state                                              # devolve o estado final, pronto para as funcoes de fechamento/relatorio


# --------------------------------------------------------------------------
# Fechamento: tabela dimensao de estacoes + reconciliacao com stations.csv
# --------------------------------------------------------------------------

def build_station_dimension(state: dict) -> pd.DataFrame:
    """
    Recebe: o dict de estado (usa station_name/lat/lon/capacity e
    station_reading_count, ja populados por update_station_dimension).
    Devolve: um DataFrame com uma linha por station_id, contendo o valor
    mais frequente (moda) de name/lat/lon/capacity e quantos valores
    distintos foram vistos em cada campo -- mais de 1 valor distinto indica
    que o campo nao foi estavel ao longo do tempo pra aquela estacao.
    """
    rows = []                                                    # acumula um dict por estacao antes de virar DataFrame
    for station_id, n_readings in state["station_reading_count"].items():  # percorre cada station_id visto nas leituras
        name_counter = state["station_name"][station_id]          # contagem de cada nome visto para esta estacao
        lat_counter = state["station_lat"][station_id]             # contagem de cada latitude vista
        lon_counter = state["station_lon"][station_id]             # contagem de cada longitude vista
        cap_counter = state["station_capacity"][station_id]        # contagem de cada capacity vista
        rows.append({
            "station_id": station_id,
            "name_mode": name_counter.most_common(1)[0][0] if name_counter else None,   # valor mais frequente de name
            "n_distinct_name": len(name_counter),                    # quantos nomes diferentes essa estacao teve
            "lat_mode": lat_counter.most_common(1)[0][0] if lat_counter else None,
            "n_distinct_lat": len(lat_counter),
            "lon_mode": lon_counter.most_common(1)[0][0] if lon_counter else None,
            "n_distinct_lon": len(lon_counter),
            "capacity_mode": cap_counter.most_common(1)[0][0] if cap_counter else None,
            "n_distinct_capacity": len(cap_counter),
            "n_readings": n_readings,                                # total de leituras desta estacao, ja deduplicadas
        })
    return pd.DataFrame(rows).sort_values("station_id").reset_index(drop=True)  # tabela final, ordenada por station_id


def reconcile_with_reference(derived_df: pd.DataFrame, ref_path: Path, tolerance: float = LAT_LON_TOLERANCE) -> dict:
    """
    Recebe: o DataFrame derivado dos dados de leitura (saida de
    build_station_dimension), o caminho do stations.csv de referencia e uma
    tolerancia em graus pra considerar lat/lon "iguais" (default 1e-3 grau,
    ~100m).
    Devolve: dict com as divergencias encontradas entre os dados derivados
    e o cadastro de referencia: estacoes so nas leituras, estacoes so na
    referencia, e estacoes presentes nos dois lados com name/lat/lon
    divergentes alem da tolerancia.
    """
    ref = pd.read_csv(ref_path, dtype={"Number": "string"})          # le o cadastro de referencia (110 linhas, cabe inteiro em memoria)
    ref = ref.rename(columns={                                        # renomeia colunas do arquivo pro vocabulario usado aqui
        "Number": "station_id", "Name": "name_ref", "Latitude": "lat_ref", "Longitude": "lon_ref",
    })

    derived_ids = set(derived_df["station_id"])                        # station_id vistos nas leituras (dado bruto)
    ref_ids = set(ref["station_id"])                                    # station_id existentes no cadastro de referencia

    only_in_readings = sorted(derived_ids - ref_ids)                    # apareceram nas leituras mas nao estao cadastrados
    only_in_reference = sorted(ref_ids - derived_ids)                   # estao cadastrados mas nunca apareceram nas leituras

    merged = derived_df.merge(ref[["station_id", "name_ref", "lat_ref", "lon_ref"]], on="station_id", how="inner")  # so as estacoes presentes nos dois lados
    name_mismatch = merged[merged["name_mode"].str.upper() != merged["name_ref"].str.upper()]   # nomes diferentes (case-insensitive)
    lat_mismatch = merged[(merged["lat_mode"] - merged["lat_ref"]).abs() > tolerance]            # latitude fora da tolerancia
    lon_mismatch = merged[(merged["lon_mode"] - merged["lon_ref"]).abs() > tolerance]            # longitude fora da tolerancia

    return {
        "only_in_readings": only_in_readings,                                            # lista de station_id
        "only_in_reference": only_in_reference,                                          # lista de station_id
        "name_mismatch": name_mismatch[["station_id", "name_mode", "name_ref"]],          # DataFrame de divergencias de nome
        "lat_mismatch": lat_mismatch[["station_id", "lat_mode", "lat_ref"]],              # DataFrame de divergencias de latitude
        "lon_mismatch": lon_mismatch[["station_id", "lon_mode", "lon_ref"]],              # DataFrame de divergencias de longitude
    }


# --------------------------------------------------------------------------
# Fechamento: checagem de timezone via virada de horario de verao (DST)
# --------------------------------------------------------------------------

def find_last_sunday(year: int, month: int) -> dt.date:
    """
    Recebe: ano e mes.
    Devolve: a data (datetime.date) do ultimo domingo daquele mes -- que e
    quando a Irlanda/Europa muda o horario de verao (adianta em marco,
    atrasa em outubro).
    """
    last_day = calendar.monthrange(year, month)[1]           # nº de dias no mes (ex.: 31 para marco)
    for day in range(last_day, 0, -1):                         # percorre os dias do mes de tras pra frente
        candidate = dt.date(year, month, day)                   # monta a data candidata
        if candidate.weekday() == 6:                             # weekday() == 6 corresponde a domingo
            return candidate                                      # primeiro domingo encontrado de tras pra frente = o ultimo do mes
    raise ValueError("nenhum domingo encontrado no mes")          # nunca deve ocorrer (todo mes tem >=1 domingo)


def check_timezone_dst(state: dict, year: int = 2026) -> dict:
    """
    Recebe: o dict de estado (usa state["gap_records"], ja populado por
    update_cadence_gaps) e o ano de referencia.
    Devolve: dict com a data da virada de horario de verao, os gap_records
    que caem dentro dessa janela (evidencia bruta) e uma conclusao textual:
    um buraco de leitura comecando as 01:00 do ultimo domingo de marco (a
    hora "some" localmente, pulando de 01:00 pra 02:00) indica que
    last_reported esta em hora LOCAL irlandesa; a ausencia desse buraco
    indica UTC (ou que o dataset nao modela DST).
    """
    dst_date = find_last_sunday(year, 3)                                 # ultimo domingo de marco do ano em questao
    window_start = dt.datetime.combine(dst_date, dt.time(0, 50))         # inicio da janela de observacao (um pouco antes da 1h)
    window_end = dt.datetime.combine(dst_date, dt.time(2, 10))           # fim da janela de observacao (um pouco depois das 2h)
    matches = [                                                           # gaps cujo inicio cai dentro dessa janela, ignorando o ruido rotineiro de 1 leitura faltando (ver secao 6: 10min em vez de 5min e o padrao normal, nao evidencia de DST)
        g for g in state["gap_records"]
        if window_start <= g["gap_start"] <= window_end and g["missing_periods"] >= 2
    ]
    is_local_time_signal = any(g["missing_periods"] >= 6 for g in matches)  # >=30min faltando nesse horario e indicio forte de "spring forward" local
    conclusion = (
        "Buraco de leitura encontrado coincidindo com a troca de horario de verao "
        "(spring forward) -- indicio de que last_reported esta em hora LOCAL irlandesa."
        if is_local_time_signal else
        "Nenhum buraco de leitura na janela da troca de horario de verao -- "
        "indicio de que last_reported esta em UTC (ou o dataset nao modela DST)."
    )
    return {"dst_date": dst_date, "matches": matches, "conclusion": conclusion}


# --------------------------------------------------------------------------
# Renderizacao do relatorio em markdown
# --------------------------------------------------------------------------

def df_to_markdown_table(df: pd.DataFrame, max_rows: int | None = None) -> str:
    """
    Recebe: um DataFrame e, opcionalmente, um numero maximo de linhas a
    exibir.
    Devolve: uma string com o DataFrame formatado como tabela markdown,
    montada manualmente (o projeto nao tem a lib `tabulate`, entao
    DataFrame.to_markdown() nao esta disponivel).
    """
    if max_rows is not None:                                              # se foi pedido um limite de linhas
        df = df.head(max_rows)                                             # corta a tabela nas primeiras `max_rows`
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"          # linha de cabecalho com os nomes das colunas
    separator = "| " + " | ".join("---" for _ in df.columns) + " |"        # linha separadora exigida pelo formato markdown
    body_lines = [                                                          # uma linha markdown por linha do DataFrame
        "| " + " | ".join(str(v) for v in row) + " |"
        for row in df.itertuples(index=False)
    ]
    return "\n".join([header, separator, *body_lines])                      # junta cabecalho + separador + corpo num texto so


def render_markdown_report(
    schema_results: list[dict],
    state: dict,
    station_dim: pd.DataFrame,
    reconciliation: dict,
    dst_result: dict,
) -> str:
    """
    Recebe: os resultados de todas as checagens (schema, o estado
    acumulado do pipeline, a tabela dimensao de estacoes derivada, o dict
    de reconciliacao com stations.csv e o dict da checagem de timezone).
    Devolve: uma string unica em markdown, pronta para ser escrita em
    reports/data_quality.md.
    """
    lines: list[str] = []                                                  # lista de linhas de texto que vira o relatorio final
    lines.append("# Relatorio de qualidade de dados -- bikes_2026_01..06.csv")
    lines.append(f"\nGerado em {dt.datetime.now():%Y-%m-%d %H:%M}. Este relatorio e so leitura/diagnostico; "
                 "nenhum arquivo em data/processed/ foi criado ou alterado.\n")

    # -- 0. Schema -----------------------------------------------------
    lines.append("## 0. Consistencia de schema entre arquivos")
    all_match = all(r["matches_reference"] for r in schema_results)         # True se TODOS os arquivos baterem com o 1o
    lines.append(f"\nTodos os 6 arquivos tem as mesmas colunas, na mesma ordem: **{all_match}**.\n")
    schema_df = pd.DataFrame([                                              # tabela auxiliar so com file + match, pro relatorio
        {"file": r["file"], "matches_reference": r["matches_reference"]} for r in schema_results
    ])
    lines.append(df_to_markdown_table(schema_df))

    # -- 1. Volume, range de datas e o gap jan/fev ----------------------
    lines.append("\n\n## 1. Volume por arquivo e gap temporal jan/fev")
    lines.append(
        "\nCriterio: o gap entre janeiro e fevereiro e apenas quantificado e documentado aqui -- "
        "**sem imputacao nem interpolacao**. Janeiro deve ser tratado como mes PARCIAL (dias 1 a 24) "
        "nas analises seguintes.\n"
    )
    volume_rows = []                                                        # monta a tabela de volume por arquivo
    for path in MONTHLY_FILES:
        fname = path.name
        raw = state["rows_raw_per_file"][fname]                              # linhas lidas do arquivo, antes de dedup
        dedup = state["rows_dedup_per_file"][fname]                          # linhas restantes, apos dedup
        dup = state["duplicates_per_file"][fname]                            # linhas descartadas na dedup
        ts_min, ts_max = state["ts_range_per_file"][fname]                   # menor/maior timestamp visto neste arquivo
        volume_rows.append({
            "file": fname, "rows_raw": raw, "rows_dedup": dedup, "duplicates_removed": dup,
            "min_last_reported": ts_min, "max_last_reported": ts_max,
        })
    volume_df = pd.DataFrame(volume_rows)
    lines.append(df_to_markdown_table(volume_df))

    jan_max = state["ts_range_per_file"]["bikes_2026_01.csv"][1]             # ultimo timestamp visto em janeiro
    feb_min = state["ts_range_per_file"]["bikes_2026_02.csv"][0]             # primeiro timestamp visto em fevereiro
    gap_span = feb_min - jan_max                                              # duracao do buraco entre os dois arquivos
    lines.append(
        f"\n**Gap jan/fev**: ultima leitura em janeiro em `{jan_max}`, primeira leitura em fevereiro em "
        f"`{feb_min}` -- um buraco de **{gap_span}** sem nenhuma leitura registrada, em nenhuma estacao.\n"
    )

    # -- 2. Completude (nulos em colunas criticas) -----------------------
    lines.append("\n## 2. Completude -- nulos em colunas criticas")
    lines.append("\nColunas que nunca deveriam ter nulo, por serem chave/identificacao de cada leitura:\n")
    null_rows = [                                                            # uma linha por (coluna, arquivo)
        {"column": col, "file": fname, "null_count": count}
        for col, per_file in state["null_counts"].items()
        for fname, count in per_file.items()
    ]
    null_df = pd.DataFrame(null_rows)
    total_nulls = null_df["null_count"].sum() if not null_df.empty else 0     # soma geral de nulos encontrados
    lines.append(f"\nTotal de nulos encontrados nas colunas criticas (todas os arquivos, todas as colunas): **{total_nulls}**.\n")
    lines.append(df_to_markdown_table(null_df))

    # -- 3. Duplicidade ----------------------------------------------------
    lines.append("\n\n## 3. Duplicidade -- chave (station_id, last_reported)")
    total_raw = sum(state["rows_raw_per_file"].values())                       # total de linhas lidas, somando os 6 arquivos
    total_dup = sum(state["duplicates_per_file"].values())                     # total de duplicatas descartadas
    dup_pct = (total_dup / total_raw * 100) if total_raw else 0.0              # percentual de duplicatas sobre o total bruto
    lines.append(
        f"\nCriterio: deduplicar por `(station_id, last_reported)`, mantendo a **primeira** ocorrencia "
        f"(arquivos processados em ordem jan->jun). Isso tambem resolve overlap de timestamp entre arquivos.\n\n"
        f"Total de linhas lidas: **{total_raw}**. Total de duplicatas descartadas: **{total_dup}** "
        f"(**{dup_pct:.3f}%**). Linhas restantes apos dedup: **{total_raw - total_dup}**.\n"
    )

    # -- 4. Capacity --------------------------------------------------------
    lines.append("\n## 4. Violacoes de capacity")
    checked = state["capacity_checked"]                                        # nº de linhas avaliadas (com os 3 campos presentes)
    violations = state["capacity_violations"]                                   # nº de linhas com bikes+docks > capacity
    viol_pct = (violations / checked * 100) if checked else 0.0                 # percentual de violacoes sobre as linhas avaliadas
    lines.append(
        f"\nCriterio: esperado `num_bikes_available + num_docks_available <= capacity`. Apenas reportando; "
        f"nenhuma linha foi removida nesta etapa.\n\n"
        f"Linhas avaliadas (capacity/bikes/docks nao nulos, apos dedup): **{checked}**. "
        f"Violacoes (`>` capacity): **{violations}** (**{viol_pct:.4f}%**).\n"
    )

    # -- 5. Booleans ----------------------------------------------------------
    lines.append("\n## 5. Variantes de string nas colunas booleanas")
    lines.append("\nValores brutos encontrados (somando os 6 arquivos), por coluna:\n")
    bool_rows = [                                                                # uma linha por (coluna, variante, contagem)
        {"column": col, "raw_value": value, "count": count}
        for col, counter in state["boolean_variants"].items()
        for value, count in counter.most_common()
    ]
    lines.append(df_to_markdown_table(pd.DataFrame(bool_rows)))

    # -- 6. Cadencia e maiores buracos de leitura ------------------------------
    lines.append("\n\n## 6. Cadencia de leitura (esperada: 5 em 5 minutos)")
    total_deltas = sum(state["delta_histogram"].values())                        # total de deltas calculados (pares de leituras consecutivas)
    hist_rows = [                                                                 # tabela do histograma de deltas, ordenada por frequencia
        {"delta_minutes": delta, "count": count, "pct": f"{count / total_deltas * 100:.3f}%"}
        for delta, count in state["delta_histogram"].most_common(15)
    ]
    lines.append("\nDistribuicao dos 15 deltas mais comuns entre leituras consecutivas da mesma estacao:\n")
    lines.append(df_to_markdown_table(pd.DataFrame(hist_rows)))

    trivial_gaps = sum(1 for g in state["gap_records"] if g["missing_periods"] == 1)   # buracos de so 1 leitura faltando (a maioria e so o padrao de 10min em vez de 5min, visivel no histograma acima)
    real_gaps = sum(1 for g in state["gap_records"] if g["missing_periods"] >= 2)      # buracos com 2+ leituras faltando -- evidencia mais forte de perda real de dado
    lines.append(
        f"\n\nTotal de buracos (delta > {EXPECTED_INTERVAL_MIN}min) encontrados: **{len(state['gap_records'])}**, sendo "
        f"**{trivial_gaps}** de apenas 1 leitura faltando (o padrao rotineiro de 10min visto acima, nao necessariamente "
        f"perda de dado) e **{real_gaps}** com 2 ou mais leituras faltando seguidas (evidencia mais forte de buraco real).\n"
    )
    top_gaps = sorted(state["gap_records"], key=lambda g: g["missing_periods"], reverse=True)[:15]  # 15 maiores buracos, por leituras faltantes
    lines.append("\nOs 15 maiores buracos (por leituras faltantes):\n")
    lines.append(df_to_markdown_table(pd.DataFrame(top_gaps)))

    # -- 7. Timezone / DST ------------------------------------------------------
    lines.append("\n\n## 7. Timezone -- hora local irlandesa vs. UTC")
    lines.append(
        f"\nCriterio: observar o comportamento de `last_reported` na virada do horario de verao europeu "
        f"(**{dst_result['dst_date']}**, ultimo domingo de marco/2026 -- relogios adiantam de 01:00 pra 02:00 "
        "na hora local, entao a hora local 01:00-01:59 simplesmente nao existe nesse dia).\n\n"
        f"Buracos de leitura encontrados na janela 00:50-02:10 desse dia: **{len(dst_result['matches'])}**.\n\n"
        f"**Conclusao**: {dst_result['conclusion']}\n"
    )
    if dst_result["matches"]:                                                     # se achou algum buraco na janela, mostra a evidencia
        lines.append(df_to_markdown_table(pd.DataFrame(dst_result["matches"])))

    # -- 8. Dimensao de estacoes derivada dos dados ------------------------------
    lines.append("\n\n## 8. Tabela dimensao de estacoes (derivada das leituras)")
    lines.append(
        "\nCriterio: `name`/`lat`/`lon`/`capacity` por estacao sao a MODA (valor mais frequente) observada "
        "nas leituras, nao um valor fixo de cadastro. `n_distinct_*` > 1 indica que o campo variou ao longo "
        "do periodo pra aquela estacao (potencial instabilidade de dado, nao necessariamente erro).\n"
    )
    unstable = station_dim[
        (station_dim["n_distinct_name"] > 1)
        | (station_dim["n_distinct_lat"] > 1)
        | (station_dim["n_distinct_lon"] > 1)
        | (station_dim["n_distinct_capacity"] > 1)
    ]
    lines.append(f"\nTotal de estacoes derivadas dos dados: **{len(station_dim)}**. "
                 f"Estacoes com pelo menos 1 campo instavel ao longo do tempo: **{len(unstable)}**.\n")
    if len(unstable):
        lines.append(df_to_markdown_table(unstable))

    # -- 9. Reconciliacao com stations.csv ----------------------------------------
    lines.append("\n\n## 9. Reconciliacao com stations.csv (referencia)")
    lines.append(
        "\nCriterio: stations.csv NAO e usado pra corrigir a dimensao derivada -- so pra apontar divergencias.\n\n"
        f"Estacoes so nas leituras (nao cadastradas em stations.csv): **{reconciliation['only_in_readings']}**\n\n"
        f"Estacoes so no cadastro (nunca apareceram nas leituras): **{reconciliation['only_in_reference']}**\n"
    )
    lines.append(f"\nDivergencias de nome (case-insensitive): **{len(reconciliation['name_mismatch'])}**\n")
    if len(reconciliation["name_mismatch"]):
        lines.append(df_to_markdown_table(reconciliation["name_mismatch"]))
    lines.append(f"\n\nDivergencias de latitude (> {LAT_LON_TOLERANCE} grau): **{len(reconciliation['lat_mismatch'])}**\n")
    if len(reconciliation["lat_mismatch"]):
        lines.append(df_to_markdown_table(reconciliation["lat_mismatch"]))
    lines.append(f"\n\nDivergencias de longitude (> {LAT_LON_TOLERANCE} grau): **{len(reconciliation['lon_mismatch'])}**\n")
    if len(reconciliation["lon_mismatch"]):
        lines.append(df_to_markdown_table(reconciliation["lon_mismatch"]))

    return "\n".join(lines)                                                        # concatena todas as secoes num unico texto markdown


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    """
    Recebe: nada (usa as constantes de modulo MONTHLY_FILES/STATIONS_REF_PATH/REPORT_PATH).
    Devolve: None. Roda o pipeline completo e escreve reports/data_quality.md.
    Efeito colateral (unico): cria/sobrescreve o arquivo de relatorio.
    """
    schema_results = check_schema_consistency(MONTHLY_FILES)          # checagem 0: schema identico entre arquivos
    state = run_pipeline(MONTHLY_FILES)                                 # passe unico por todos os chunks, populando o estado
    station_dim = build_station_dimension(state)                        # tabela dimensao de estacoes, derivada das leituras
    reconciliation = reconcile_with_reference(station_dim, STATIONS_REF_PATH)  # divergencias contra stations.csv
    dst_result = check_timezone_dst(state)                              # conclusao sobre timezone via virada de DST

    report = render_markdown_report(schema_results, state, station_dim, reconciliation, dst_result)  # monta o texto final
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)                # garante que reports/ existe (sem apagar nada nela)
    REPORT_PATH.write_text(report, encoding="utf-8")                     # escreve o relatorio (unico arquivo gerado por este script)
    print(f"Relatorio escrito em {REPORT_PATH}")                          # feedback no terminal ao final da execucao


if __name__ == "__main__":                                                # so roda o pipeline se o arquivo for executado diretamente
    main()
