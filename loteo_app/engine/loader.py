import pandas as pd
import re
from engine.utils import norm_str, up, clean_cols


def find_header_row(xlsm_path, sheet_name, required_cols, search_rows=80):
    preview = pd.read_excel(xlsm_path, sheet_name=sheet_name, engine="openpyxl", header=None, nrows=search_rows)
    req = set(required_cols)
    for r in range(search_rows):
        row_vals = [norm_str(v) for v in preview.iloc[r].tolist()]
        row_set = set([v for v in row_vals if v])
        if req.issubset(row_set):
            return r
    return 0


def read_sheet_autoheader(xlsm_path, sheet_name, required_cols=None, default_header=0):
    hdr = find_header_row(xlsm_path, sheet_name, required_cols) if required_cols else default_header
    df = pd.read_excel(xlsm_path, sheet_name=sheet_name, engine="openpyxl", header=hdr)
    df.columns = clean_cols(df.columns)
    return df, hdr


def load_inputs(xlsm_path, param_overrides=None):
    """
    Load all inputs from Excel. param_overrides is a dict of CONFIG overrides
    set from the Streamlit UI (keys uppercase, values as-is).
    """
    required_sheets = ["DATA", "CAPACIDADES_TINTO", "CONFIG", "COMBINACIONES_PRIORIDAD"]
    xls = pd.ExcelFile(xlsm_path, engine="openpyxl")
    missing = [s for s in required_sheets if s not in xls.sheet_names]
    if missing:
        raise ValueError(f"Faltan hojas: {missing}. Deben existir: {required_sheets}")

    req_cols = ["LNK", "TELA.CUERPO", "COLOR", "PRIORIDAD", "ANCHO.F.C", "ANCHO.F.M", "TOTAL", "MIX", "CONSUMO_C"]
    df_data, hdr_row = read_sheet_autoheader(xlsm_path, "DATA", required_cols=req_cols, default_header=0)
    df_cap = pd.read_excel(xlsm_path, sheet_name="CAPACIDADES_TINTO", engine="openpyxl")
    df_cfg = pd.read_excel(xlsm_path, sheet_name="CONFIG", engine="openpyxl")
    df_mix = pd.read_excel(xlsm_path, sheet_name="COMBINACIONES_PRIORIDAD", engine="openpyxl")

    def read_optional(sheet):
        if sheet in xls.sheet_names:
            dfx = pd.read_excel(xlsm_path, sheet_name=sheet, engine="openpyxl")
            dfx.columns = clean_cols(dfx.columns)
            return dfx
        return pd.DataFrame()

    df_fam = read_optional("RESTRICCIONES_FAMILIA")
    df_color = read_optional("RESTRICCIONES_COLOR")
    df_ancho = read_optional("RESTRICCIONES_ANCHO")
    df_comb = read_optional("REGLAS_ANCHOS_COMBINADOS")

    df_cap.columns = clean_cols(df_cap.columns)
    df_cfg.columns = clean_cols(df_cfg.columns)
    df_mix.columns = clean_cols(df_mix.columns)

    miss = [c for c in req_cols if c not in df_data.columns]
    if miss:
        raise ValueError(f"DATA: faltan columnas {miss}. Header detectado fila {hdr_row + 1}.")

    for c in ["ANCHO.F.C", "ANCHO.F.M", "TOTAL", "CONSUMO_C"]:
        df_data[c] = pd.to_numeric(df_data[c], errors="coerce").fillna(0.0)
    for c in ["LNK", "TELA.CUERPO", "COLOR", "PRIORIDAD", "MIX"]:
        df_data[c] = df_data[c].apply(norm_str)
    df_data["MIX"] = df_data["MIX"].apply(up)

    if "FAMILIA" not in df_data.columns:
        df_data["FAMILIA"] = ""
    else:
        df_data["FAMILIA"] = df_data["FAMILIA"].apply(up)
    if "COLOR_R" not in df_data.columns:
        df_data["COLOR_R"] = ""
    else:
        df_data["COLOR_R"] = df_data["COLOR_R"].apply(up)
    if "STYLE" not in df_data.columns:
        df_data["STYLE"] = ""
    else:
        df_data["STYLE"] = df_data["STYLE"].apply(up)
    if "TONO" in df_data.columns:
        df_data["TONO"] = df_data["TONO"].apply(up)

    cap_cols = ["CATEGORIA", "MINIMO", "MAXIMO", "CAPACIDAD", "MIX"]
    miss = [c for c in cap_cols if c not in df_cap.columns]
    if miss:
        raise ValueError(f"CAPACIDADES_TINTO: faltan columnas {miss}")
    df_cap["CATEGORIA"] = df_cap["CATEGORIA"].apply(norm_str)
    df_cap["MIX"] = df_cap["MIX"].apply(up)
    for c in ["MINIMO", "MAXIMO", "CAPACIDAD"]:
        df_cap[c] = pd.to_numeric(df_cap[c], errors="coerce")
    if df_cap[["MINIMO", "MAXIMO", "CAPACIDAD"]].isna().any().any():
        raise ValueError("CAPACIDADES_TINTO: MINIMO/MAXIMO/CAPACIDAD inválidos.")

    # Build cfg dict from Excel, then apply UI overrides
    cfg = {}
    for _, r in df_cfg.iterrows():
        k = norm_str(r["PARAMETRO"]).upper()
        if k:
            cfg[k] = r["VALOR"]

    if param_overrides:
        for k, v in param_overrides.items():
            cfg[k.upper()] = v

    def cfg_int(name, default=0):
        v = cfg.get(str(name).strip().upper(), default)
        try:
            return int(float(str(v).strip()))
        except:
            return int(default)

    def cfg_float(name, default=0.0):
        v = cfg.get(str(name).strip().upper(), default)
        try:
            return float(str(v).strip())
        except:
            return float(default)

    def cfg_bool(name, default=0):
        v = str(cfg.get(str(name).strip().upper(), default)).strip().upper()
        return 1 if v in ("1", "TRUE", "YES", "SI", "SÍ") else 0

    def cfg_list(name, sep=",", default_list=()):
        raw = str(cfg.get(str(name).strip().upper(), "")).strip()
        if not raw:
            return list(default_list)
        return [x.strip().upper() for x in str(raw).split(sep) if x.strip()]

    MIN_DIFF = float(cfg_float("MIN_DIFF", 0.0))
    MAX_DIFF = float(cfg_float("MAX_DIFF", 6.0))
    MAX_WIDTHS = int(cfg_int("MAX_WIDTHS", 6))
    MAX_SKU = int(cfg_int("MAX_SKU", 6))
    RULE_ORDER = norm_str(cfg.get("RULE_ORDER", "ANCHO18>COMBO_ANCHOS>COLOR_R>FAMILIA>DEFAULT"))
    PRIORITY_ORDER = norm_str(cfg.get("PRIORITY_ORDER", ""))
    APPLY_RULES_BLEACH = cfg_int("APPLY_RULES_BLEACH", 0)
    SPLIT_MIN_LBS_ANCHO18 = float(cfg_float("SPLIT_MIN_LBS_ANCHO18", 250.0))
    SPLIT_MIN_LBS_DEFAULT_CFG = float(cfg_float("SPLIT_MIN_LBS_DEFAULT", 100.0))
    SCRAP_REMAINDER_BELOW_SPLIT_MIN = cfg_int("SCRAP_REMAINDER_BELOW_SPLIT_MIN", 1)
    ANCHO18_ALLOW_SPILLOVER_2600 = cfg_int("ANCHO18_ALLOW_SPILLOVER_2600", 0)

    def parse_float_set(x, default={2200.0, 1100.0}):
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return set(default)
        parts = re.split(r"[;,\s]+", str(x).strip())
        vals = []
        for p in parts:
            if not p:
                continue
            try:
                vals.append(float(p))
            except:
                pass
        return set(vals) if vals else set(default)

    ANCHO18_ALLOWED_MAX_DYE = parse_float_set(cfg.get("ANCHO18_ALLOWED_MAX_DYE", "2200,1100"))
    UPGRADE_CATEGORIA = cfg_int("UPGRADE_CATEGORIA", 1)
    TRY_ALL_PRIORITIES = cfg_int("TRY_ALL_PRIORITIES", 1)

    miss = [c for c in ["PRIORIDAD_1", "PRIORIDAD_2"] if c not in df_mix.columns]
    if miss:
        raise ValueError(f"COMBINACIONES_PRIORIDAD: faltan columnas {miss}")
    allowed_pairs = set()
    for _, r in df_mix.iterrows():
        a = norm_str(r["PRIORIDAD_1"])
        b = norm_str(r["PRIORIDAD_2"])
        if a and b:
            allowed_pairs.add((a, b))
            allowed_pairs.add((b, a))

    restr_fam = {}
    if not df_fam.empty and "FAMILIA" in df_fam.columns:
        pcols = [c for c in df_fam.columns if c.upper().startswith("PRIORIDAD")]
        for _, r in df_fam.iterrows():
            f = up(r.get("FAMILIA", ""))
            if not f:
                continue
            caps = []
            for pc in pcols:
                v = r.get(pc, None)
                if v is not None and not pd.isna(v):
                    caps.append(float(v))
            if caps:
                restr_fam[f] = caps

    restr_color = {}
    if not df_color.empty and "COLOR_R" in df_color.columns:
        if "PRIORIDAD_1" in df_color.columns:
            for _, r in df_color.iterrows():
                c = up(r.get("COLOR_R", ""))
                v = r.get("PRIORIDAD_1", None)
                restr_color[c] = float(v) if v is not None and not pd.isna(v) else None

    restr_ancho = {}
    if not df_ancho.empty and "STYLE" in df_ancho.columns:
        pcols = [c for c in df_ancho.columns if c.upper().startswith("PRIORIDAD")]
        for _, r in df_ancho.iterrows():
            style = up(r.get("STYLE", ""))
            if not style:
                continue
            lim = r.get("LIMITE_ANCHO", None)
            try:
                lim = float(lim) if lim is not None and not pd.isna(lim) else None
            except:
                lim = None
            caps = []
            for pc in pcols:
                v = r.get(pc, None)
                if v is not None and not pd.isna(v):
                    try:
                        caps.append(float(v))
                    except:
                        pass
            restr_ancho[style] = {"limite": lim, "prioridades": caps}

    reglas_combinacion = []
    if not df_comb.empty:
        pcols = [c for c in df_comb.columns if c.upper().startswith("CAPACIDAD_PRIORIDAD")]
        for _, r in df_comb.iterrows():
            a1 = r.get("ANCHO_1", None)
            a2 = r.get("ANCHO_2", None)
            if a1 is None or a2 is None or pd.isna(a1) or pd.isna(a2):
                continue
            try:
                a1f = float(a1)
                a2f = float(a2)
            except:
                continue
            caps = []
            for pc in pcols:
                v = r.get(pc, None)
                if v is not None and not pd.isna(v):
                    try:
                        caps.append(float(v))
                    except:
                        pass
            if caps:
                reglas_combinacion.append({"a1": a1f, "a2": a2f, "prioridades": caps})

    BEAM_WIDTH = max(1, cfg_int("BEAM_WIDTH", 3))
    W_FILL = cfg_float("W_FILL", 5.0)
    W_CAP_LOSS = cfg_float("W_CAP_LOSS", 3.0)
    WIDTH_PREF_LIST = [int(x) for x in cfg_list("WIDTH_PREF_LIST", ",", ("2", "3", "1", "4", "5", "6")) if x.isdigit()]
    W_WIDTH_PREF = cfg_float("W_WIDTH_PREF", 2.0)
    W_1100_WIDTHS_STRICT = cfg_float("W_1100_WIDTHS_STRICT", 10.0)
    OVERSHOOT_ENABLE = cfg_bool("OVERSHOOT_ENABLE", 1)
    UNDERSHOOT_ENABLE = cfg_bool("UNDERSHOOT_ENABLE", 1)
    WIDTHS_TARGET_ORDER = norm_str(cfg.get("WIDTHS_TARGET_ORDER", "2>3>4"))
    REQUIRE_WIDTHS_STRICT = cfg_bool("REQUIRE_WIDTHS_STRICT", 1)

    def parse_allowed_by_mix(prefix, mix):
        key = f"{prefix}_{str(mix).strip().upper()}"
        raw = cfg.get(key, None)
        if raw is None or str(raw).strip() == "":
            return set()
        parts = re.split(r"[;,\s]+", str(raw).strip())
        vals = []
        for p in parts:
            if not p:
                continue
            try:
                vals.append(float(p))
            except:
                pass
        return set(vals)

    ALLOWED_MAXIMO_FOR_3_WIDTHS = {
        "DYE": parse_allowed_by_mix("ALLOWED_MAXIMO_FOR_3_WIDTHS", "DYE"),
        "BLEACH": parse_allowed_by_mix("ALLOWED_MAXIMO_FOR_3_WIDTHS", "BLEACH"),
    }
    ALLOWED_MAXIMO_FOR_4_WIDTHS = {
        "DYE": parse_allowed_by_mix("ALLOWED_MAXIMO_FOR_4_WIDTHS", "DYE"),
        "BLEACH": parse_allowed_by_mix("ALLOWED_MAXIMO_FOR_4_WIDTHS", "BLEACH"),
    }

    params = {
        "MIN_DIFF": MIN_DIFF,
        "MAX_DIFF": MAX_DIFF,
        "MAX_WIDTHS": MAX_WIDTHS,
        "MAX_SKU": MAX_SKU,
        "MIX_ALLOWED": allowed_pairs,
        "RESTRICCIONES_FAMILIA": restr_fam,
        "RESTRICCIONES_COLOR": restr_color,
        "RESTRICCIONES_ANCHO": restr_ancho,
        "REGLAS_ANCHOS_COMBINADOS": reglas_combinacion,
        "RULE_ORDER": RULE_ORDER,
        "PRIORITY_ORDER": PRIORITY_ORDER,
        "APPLY_RULES_BLEACH": APPLY_RULES_BLEACH,
        "OVERRIDE_BY_PRIORITY": 1,
        "TRY_ALL_PRIORITIES": TRY_ALL_PRIORITIES,
        "UPGRADE_CATEGORIA": UPGRADE_CATEGORIA,
        "SPLIT_MIN_LBS_DEFAULT": SPLIT_MIN_LBS_DEFAULT_CFG,
        "SPLIT_MIN_LBS_ANCHO18": SPLIT_MIN_LBS_ANCHO18,
        "SCRAP_REMAINDER_BELOW_SPLIT_MIN": SCRAP_REMAINDER_BELOW_SPLIT_MIN,
        "ANCHO18_ALLOW_SPILLOVER_2600": ANCHO18_ALLOW_SPILLOVER_2600,
        "ANCHO18_ALLOWED_MAX_DYE": ANCHO18_ALLOWED_MAX_DYE,
        "BEAM_WIDTH": BEAM_WIDTH,
        "W_FILL": W_FILL,
        "W_CAP_LOSS": W_CAP_LOSS,
        "WIDTH_PREF_LIST": WIDTH_PREF_LIST,
        "W_WIDTH_PREF": W_WIDTH_PREF,
        "W_1100_WIDTHS_STRICT": W_1100_WIDTHS_STRICT,
        "OVERSHOOT_ENABLE": OVERSHOOT_ENABLE,
        "UNDERSHOOT_ENABLE": UNDERSHOOT_ENABLE,
        "WIDTHS_TARGET_ORDER": WIDTHS_TARGET_ORDER,
        "REQUIRE_WIDTHS_STRICT": REQUIRE_WIDTHS_STRICT,
        "ALLOWED_MAXIMO_FOR_3_WIDTHS": ALLOWED_MAXIMO_FOR_3_WIDTHS,
        "ALLOWED_MAXIMO_FOR_4_WIDTHS": ALLOWED_MAXIMO_FOR_4_WIDTHS,
        # raw cfg for UI display
        "_raw_cfg": dict(cfg),
    }
    return df_data, df_cap, params, hdr_row
