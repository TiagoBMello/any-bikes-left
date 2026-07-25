"""
fase 1: diagnostico de qualidade dos csvs mensais de bikes, so leitura
escreve relatorio em reports/data_quality.md, nao mexe em data/processed/
dimensao de estacoes aqui usa moda (so diagnostico); build_database usa o valor mais recente pra tabela real
"""

import calendar
import datetime as dt
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/raw")
STATIONS_REF_PATH = RAW_DIR / "stations.csv"
MONTHLY_FILES = sorted(RAW_DIR.glob("bikes_2026_*.csv"))
REPORT_PATH = Path("reports/data_quality.md")

CHUNKSIZE = 200_000                       # evita carregar as ~3.4m linhas inteiras na memoria
EXPECTED_INTERVAL_MIN = 5
CRITICAL_COLUMNS = ["system_id", "station_id", "last_reported"]
BOOLEAN_COLUMNS = ["is_installed", "is_renting", "is_returning"]
LAT_LON_TOLERANCE = 1e-3                  # ~100m, tolerancia razoavel pra erro de gps/arredondamento

# dtype fixo evita que o pandas infira "true"/"false" como bool nativo, o que esconderia variantes de string
DTYPE_OVERRIDES = {
    "system_id": "string",
    "station_id": "string",          # string, nao int, pra comparar igual com stations.csv
    "num_bikes_available": "Int64",  # nullable, aceita nan sem virar float
    "num_docks_available": "Int64",
    "is_installed": "string",        # cru, nao bool: e o que o check de booleans precisa inspecionar
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


def iter_file_chunks(path: Path, chunksize: int = CHUNKSIZE):
    reader = pd.read_csv(
        path,
        dtype=DTYPE_OVERRIDES,
        parse_dates=["last_reported"],
        chunksize=chunksize,
    )
    for chunk in reader:
        yield chunk


def check_schema_consistency(paths: list[Path]) -> list[dict]:
    results = []
    reference_columns = None
    for path in paths:
        header_df = pd.read_csv(path, nrows=0)
        columns = list(header_df.columns)
        if reference_columns is None:
            reference_columns = columns
        results.append({
            "file": path.name,
            "columns": columns,
            "matches_reference": columns == reference_columns,
        })
    return results


def init_state() -> dict:
    return {
        "rows_raw_per_file": Counter(),
        "rows_dedup_per_file": Counter(),
        "duplicates_per_file": Counter(),
        "null_counts": defaultdict(Counter),
        "min_ts": None,
        "max_ts": None,
        "ts_range_per_file": {},
        "boolean_variants": defaultdict(Counter),
        "seen_keys": set(),
        "capacity_checked": 0,
        "capacity_violations": 0,
        "station_name": defaultdict(Counter),
        "station_lat": defaultdict(Counter),
        "station_lon": defaultdict(Counter),
        "station_capacity": defaultdict(Counter),
        "station_reading_count": Counter(),
        "last_seen_ts": {},
        "gap_records": [],
        "delta_histogram": Counter(),
    }


def update_raw_counts_and_nulls(chunk: pd.DataFrame, file_name: str, state: dict) -> None:
    state["rows_raw_per_file"][file_name] += len(chunk)
    for col in CRITICAL_COLUMNS:
        state["null_counts"][col][file_name] += int(chunk[col].isna().sum())
    chunk_min = chunk["last_reported"].min()
    chunk_max = chunk["last_reported"].max()
    if state["min_ts"] is None or chunk_min < state["min_ts"]:
        state["min_ts"] = chunk_min
    if state["max_ts"] is None or chunk_max > state["max_ts"]:
        state["max_ts"] = chunk_max
    prev_min, prev_max = state["ts_range_per_file"].get(file_name, (chunk_min, chunk_max))
    state["ts_range_per_file"][file_name] = (min(prev_min, chunk_min), max(prev_max, chunk_max))


def collect_boolean_variants(chunk: pd.DataFrame, state: dict) -> None:
    for col in BOOLEAN_COLUMNS:
        value_counts = chunk[col].value_counts(dropna=False)
        for raw_value, count in value_counts.items():
            key = "<NULL>" if pd.isna(raw_value) else str(raw_value)
            state["boolean_variants"][col][key] += int(count)


def dedupe_chunk(chunk: pd.DataFrame, file_name: str, state: dict) -> pd.DataFrame:
    ts_ns = chunk["last_reported"].values.astype("int64")  # int em vez de timestamp, mais leve pra guardar ~3.4m chaves num set
    keys = list(zip(chunk["station_id"], ts_ns))
    is_new = [key not in state["seen_keys"] for key in keys]
    n_duplicates = len(chunk) - sum(is_new)
    state["duplicates_per_file"][file_name] += n_duplicates
    for key, new in zip(keys, is_new):
        if new:
            state["seen_keys"].add(key)
    deduped = chunk[is_new].reset_index(drop=True)
    state["rows_dedup_per_file"][file_name] += len(deduped)
    return deduped


def update_capacity_check(chunk: pd.DataFrame, state: dict) -> None:
    has_all = (
        chunk["capacity"].notna()
        & chunk["num_bikes_available"].notna()
        & chunk["num_docks_available"].notna()
    )
    evaluable = chunk.loc[has_all]
    total_available = evaluable["num_bikes_available"] + evaluable["num_docks_available"]
    violations = (total_available > evaluable["capacity"]).sum()
    state["capacity_checked"] += len(evaluable)
    state["capacity_violations"] += int(violations)


def update_station_dimension(chunk: pd.DataFrame, state: dict) -> None:
    grouped = chunk.groupby("station_id", observed=True)
    for station_id, group in grouped:
        state["station_name"][station_id].update(group["name"].dropna())
        state["station_lat"][station_id].update(group["lat"].dropna())
        state["station_lon"][station_id].update(group["lon"].dropna())
        state["station_capacity"][station_id].update(group["capacity"].dropna())
        state["station_reading_count"][station_id] += len(group)


def update_cadence_gaps(chunk: pd.DataFrame, state: dict) -> None:
    ordered = chunk.sort_values(["station_id", "last_reported"])
    prev_in_chunk = ordered.groupby("station_id")["last_reported"].shift(1)
    carried_prev = pd.to_datetime(ordered["station_id"].map(state["last_seen_ts"]))  # map devolve nan pra estacao nova, to_datetime normaliza pra nat
    prev_ts = prev_in_chunk.fillna(carried_prev)
    has_prev = prev_ts.notna()
    delta_minutes = (ordered["last_reported"] - prev_ts).dt.total_seconds() / 60

    for station_id, prev, curr, delta in zip(
        ordered.loc[has_prev, "station_id"],
        prev_ts[has_prev],
        ordered.loc[has_prev, "last_reported"],
        delta_minutes[has_prev],
    ):
        rounded_delta = int(round(delta / EXPECTED_INTERVAL_MIN) * EXPECTED_INTERVAL_MIN)
        state["delta_histogram"][rounded_delta] += 1
        if delta > EXPECTED_INTERVAL_MIN:
            missing_periods = round(delta / EXPECTED_INTERVAL_MIN) - 1
            state["gap_records"].append({
                "station_id": station_id,
                "gap_start": prev,
                "gap_end": curr,
                "missing_periods": missing_periods,
            })

    latest_per_station = ordered.groupby("station_id")["last_reported"].max()
    for station_id, ts in latest_per_station.items():
        state["last_seen_ts"][station_id] = ts


def run_pipeline(paths: list[Path]) -> dict:
    state = init_state()
    for path in paths:
        file_name = path.name
        for chunk in iter_file_chunks(path):
            update_raw_counts_and_nulls(chunk, file_name, state)
            collect_boolean_variants(chunk, state)
            deduped = dedupe_chunk(chunk, file_name, state)  # dedup vem antes das outras checagens, senao duplicata conta 2x
            update_capacity_check(deduped, state)
            update_station_dimension(deduped, state)
            update_cadence_gaps(deduped, state)
    return state


def build_station_dimension(state: dict) -> pd.DataFrame:
    rows = []
    for station_id, n_readings in state["station_reading_count"].items():
        name_counter = state["station_name"][station_id]
        lat_counter = state["station_lat"][station_id]
        lon_counter = state["station_lon"][station_id]
        cap_counter = state["station_capacity"][station_id]
        rows.append({
            "station_id": station_id,
            "name_mode": name_counter.most_common(1)[0][0] if name_counter else None,
            "n_distinct_name": len(name_counter),
            "lat_mode": lat_counter.most_common(1)[0][0] if lat_counter else None,
            "n_distinct_lat": len(lat_counter),
            "lon_mode": lon_counter.most_common(1)[0][0] if lon_counter else None,
            "n_distinct_lon": len(lon_counter),
            "capacity_mode": cap_counter.most_common(1)[0][0] if cap_counter else None,
            "n_distinct_capacity": len(cap_counter),
            "n_readings": n_readings,
        })
    return pd.DataFrame(rows).sort_values("station_id").reset_index(drop=True)


def reconcile_with_reference(derived_df: pd.DataFrame, ref_path: Path, tolerance: float = LAT_LON_TOLERANCE) -> dict:
    ref = pd.read_csv(ref_path, dtype={"Number": "string"})
    ref = ref.rename(columns={
        "Number": "station_id", "Name": "name_ref", "Latitude": "lat_ref", "Longitude": "lon_ref",
    })

    derived_ids = set(derived_df["station_id"])
    ref_ids = set(ref["station_id"])

    only_in_readings = sorted(derived_ids - ref_ids)
    only_in_reference = sorted(ref_ids - derived_ids)

    merged = derived_df.merge(ref[["station_id", "name_ref", "lat_ref", "lon_ref"]], on="station_id", how="inner")
    name_mismatch = merged[merged["name_mode"].str.upper() != merged["name_ref"].str.upper()]
    lat_mismatch = merged[(merged["lat_mode"] - merged["lat_ref"]).abs() > tolerance]
    lon_mismatch = merged[(merged["lon_mode"] - merged["lon_ref"]).abs() > tolerance]

    return {
        "only_in_readings": only_in_readings,
        "only_in_reference": only_in_reference,
        "name_mismatch": name_mismatch[["station_id", "name_mode", "name_ref"]],
        "lat_mismatch": lat_mismatch[["station_id", "lat_mode", "lat_ref"]],
        "lon_mismatch": lon_mismatch[["station_id", "lon_mode", "lon_ref"]],
    }


def find_last_sunday(year: int, month: int) -> dt.date:
    """ultimo domingo do mes, quando irlanda/europa muda o horario de verao"""
    last_day = calendar.monthrange(year, month)[1]
    for day in range(last_day, 0, -1):
        candidate = dt.date(year, month, day)
        if candidate.weekday() == 6:  # weekday() 6 e domingo
            return candidate
    raise ValueError("nenhum domingo encontrado no mes")  # nunca deveria disparar, todo mes tem domingo


def check_timezone_dst(state: dict, year: int = 2026) -> dict:
    dst_date = find_last_sunday(year, 3)
    window_start = dt.datetime.combine(dst_date, dt.time(0, 50))
    window_end = dt.datetime.combine(dst_date, dt.time(2, 10))
    matches = [
        g for g in state["gap_records"]
        if window_start <= g["gap_start"] <= window_end and g["missing_periods"] >= 2  # ignora o ruido do padrao normal de 10min, so conta buraco real
    ]
    is_local_time_signal = any(g["missing_periods"] >= 6 for g in matches)  # spring forward: 01h vira 02h na hora local, buraco de 30min+ aqui indica hora local, nao utc
    conclusion = (
        "Buraco de leitura encontrado coincidindo com a troca de horario de verao "
        "(spring forward) -- indicio de que last_reported esta em hora LOCAL irlandesa."
        if is_local_time_signal else
        "Nenhum buraco de leitura na janela da troca de horario de verao -- "
        "indicio de que last_reported esta em UTC (ou o dataset nao modela DST)."
    )
    return {"dst_date": dst_date, "matches": matches, "conclusion": conclusion}


def df_to_markdown_table(df: pd.DataFrame, max_rows: int | None = None) -> str:
    """monta tabela markdown na mao, o projeto nao tem a lib tabulate"""
    if max_rows is not None:
        df = df.head(max_rows)
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    separator = "| " + " | ".join("---" for _ in df.columns) + " |"
    body_lines = [
        "| " + " | ".join(str(v) for v in row) + " |"
        for row in df.itertuples(index=False)
    ]
    return "\n".join([header, separator, *body_lines])


def render_markdown_report(
    schema_results: list[dict],
    state: dict,
    station_dim: pd.DataFrame,
    reconciliation: dict,
    dst_result: dict,
) -> str:
    lines: list[str] = []
    lines.append("# qualidade de dados -- bikes 2026")
    lines.append(f"\ngerado em {dt.datetime.now():%Y-%m-%d %H:%M}\n")

    lines.append("## 0. schema")
    all_match = all(r["matches_reference"] for r in schema_results)
    lines.append(f"\nTodos os 6 arquivos tem as mesmas colunas, na mesma ordem: **{all_match}**.\n")
    schema_df = pd.DataFrame([
        {"file": r["file"], "matches_reference": r["matches_reference"]} for r in schema_results
    ])
    lines.append(df_to_markdown_table(schema_df))

    lines.append("\n\n## 1. volume por arquivo e gap jan/fev")
    volume_rows = []
    for path in MONTHLY_FILES:
        fname = path.name
        raw = state["rows_raw_per_file"][fname]
        dedup = state["rows_dedup_per_file"][fname]
        dup = state["duplicates_per_file"][fname]
        ts_min, ts_max = state["ts_range_per_file"][fname]
        volume_rows.append({
            "file": fname, "rows_raw": raw, "rows_dedup": dedup, "duplicates_removed": dup,
            "min_last_reported": ts_min, "max_last_reported": ts_max,
        })
    volume_df = pd.DataFrame(volume_rows)
    lines.append("\n")
    lines.append(df_to_markdown_table(volume_df))

    jan_max = state["ts_range_per_file"]["bikes_2026_01.csv"][1]
    feb_min = state["ts_range_per_file"]["bikes_2026_02.csv"][0]
    gap_span = feb_min - jan_max
    lines.append(
        f"\n**gap jan/fev**: ultima leitura em `{jan_max}`, primeira de fevereiro em `{feb_min}` -- "
        f"buraco de **{gap_span}**, sem imputacao (janeiro conta como mes parcial).\n"
    )

    lines.append("\n## 2. completude -- nulos em colunas criticas")
    null_rows = [
        {"column": col, "file": fname, "null_count": count}
        for col, per_file in state["null_counts"].items()
        for fname, count in per_file.items()
    ]
    null_df = pd.DataFrame(null_rows)
    total_nulls = null_df["null_count"].sum() if not null_df.empty else 0
    lines.append(f"\nTotal de nulos: **{total_nulls}**.\n")
    lines.append(df_to_markdown_table(null_df))

    lines.append("\n\n## 3. duplicidade -- chave (station_id, last_reported)")
    total_raw = sum(state["rows_raw_per_file"].values())
    total_dup = sum(state["duplicates_per_file"].values())
    dup_pct = (total_dup / total_raw * 100) if total_raw else 0.0
    lines.append(
        f"\nTotal lido: **{total_raw}**. Duplicatas descartadas: **{total_dup}** (**{dup_pct:.3f}%**). "
        f"Restante: **{total_raw - total_dup}**.\n"
    )

    lines.append("\n## 4. violacoes de capacity")
    checked = state["capacity_checked"]
    violations = state["capacity_violations"]
    viol_pct = (violations / checked * 100) if checked else 0.0
    lines.append(
        f"\nLinhas avaliadas: **{checked}**. Violacoes (`bikes+docks > capacity`): **{violations}** (**{viol_pct:.4f}%**).\n"
    )

    lines.append("\n## 5. variantes de string nas colunas booleanas")
    bool_rows = [
        {"column": col, "raw_value": value, "count": count}
        for col, counter in state["boolean_variants"].items()
        for value, count in counter.most_common()
    ]
    lines.append("\n")
    lines.append(df_to_markdown_table(pd.DataFrame(bool_rows)))

    lines.append("\n\n## 6. cadencia de leitura")
    total_deltas = sum(state["delta_histogram"].values())
    hist_rows = [
        {"delta_minutes": delta, "count": count, "pct": f"{count / total_deltas * 100:.3f}%"}
        for delta, count in state["delta_histogram"].most_common(15)
    ]
    lines.append("\n")
    lines.append(df_to_markdown_table(pd.DataFrame(hist_rows)))

    trivial_gaps = sum(1 for g in state["gap_records"] if g["missing_periods"] == 1)
    real_gaps = sum(1 for g in state["gap_records"] if g["missing_periods"] >= 2)
    lines.append(
        f"\n\nBuracos (delta > {EXPECTED_INTERVAL_MIN}min): **{len(state['gap_records'])}**, sendo "
        f"**{trivial_gaps}** de 1 leitura faltando (padrao rotineiro de 10min) e "
        f"**{real_gaps}** com 2+ leituras faltando (buraco real).\n"
    )
    top_gaps = sorted(state["gap_records"], key=lambda g: g["missing_periods"], reverse=True)[:15]
    lines.append("\nOs 15 maiores:\n")
    lines.append(df_to_markdown_table(pd.DataFrame(top_gaps)))

    lines.append("\n\n## 7. timezone -- local vs utc")
    lines.append(
        f"\nVirada de horario de verao em **{dst_result['dst_date']}**. "
        f"Buracos na janela 00:50-02:10: **{len(dst_result['matches'])}**.\n\n"
        f"**Conclusao**: {dst_result['conclusion']}\n"
    )
    if dst_result["matches"]:
        lines.append(df_to_markdown_table(pd.DataFrame(dst_result["matches"])))

    lines.append("\n\n## 8. dimensao de estacoes (derivada das leituras)")
    unstable = station_dim[
        (station_dim["n_distinct_name"] > 1)
        | (station_dim["n_distinct_lat"] > 1)
        | (station_dim["n_distinct_lon"] > 1)
        | (station_dim["n_distinct_capacity"] > 1)
    ]
    lines.append(f"\nEstacoes: **{len(station_dim)}**. Com algum campo instavel ao longo do tempo: **{len(unstable)}**.\n")
    if len(unstable):
        lines.append(df_to_markdown_table(unstable))

    lines.append("\n\n## 9. reconciliacao com stations.csv")
    lines.append(
        f"\nSo nas leituras: **{reconciliation['only_in_readings']}**\n\n"
        f"So no cadastro: **{reconciliation['only_in_reference']}**\n"
    )
    lines.append(f"\nDivergencias de nome: **{len(reconciliation['name_mismatch'])}**\n")
    if len(reconciliation["name_mismatch"]):
        lines.append(df_to_markdown_table(reconciliation["name_mismatch"]))
    lines.append(f"\n\nDivergencias de latitude (> {LAT_LON_TOLERANCE} grau): **{len(reconciliation['lat_mismatch'])}**\n")
    if len(reconciliation["lat_mismatch"]):
        lines.append(df_to_markdown_table(reconciliation["lat_mismatch"]))
    lines.append(f"\n\nDivergencias de longitude (> {LAT_LON_TOLERANCE} grau): **{len(reconciliation['lon_mismatch'])}**\n")
    if len(reconciliation["lon_mismatch"]):
        lines.append(df_to_markdown_table(reconciliation["lon_mismatch"]))

    return "\n".join(lines)


def main() -> None:
    schema_results = check_schema_consistency(MONTHLY_FILES)
    state = run_pipeline(MONTHLY_FILES)
    station_dim = build_station_dimension(state)
    reconciliation = reconcile_with_reference(station_dim, STATIONS_REF_PATH)
    dst_result = check_timezone_dst(state)

    report = render_markdown_report(schema_results, state, station_dim, reconciliation, dst_result)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Relatorio escrito em {REPORT_PATH}")


if __name__ == "__main__":
    main()
