import pandas as pd
import re


def norm_str(x):
    if pd.isna(x): return ""
    return str(x).strip()

def up(x): return norm_str(x).upper()

def clean_cols(cols):
    out = []
    for c in cols:
        c = "" if c is None else str(c)
        c = c.replace("\n"," ").replace("\r"," ")
        c = re.sub(r"\s+"," ",c).strip()
        out.append(c)
    return out

def prioridad_bloque(prio_text):
    p = (prio_text or "").upper()
    if "PAST DUE" in p or "DUE" in p or "VENC" in p: return "VENCIDOS"
    if "AHEAD2" in p: return "AHEAD2"
    if "AHEAD"  in p: return "AHEAD"
    return "OTROS"

def can_mix_blocks(b1, b2, allowed_pairs):
    if b1 == b2: return True
    return (b1,b2) in allowed_pairs

def valid_width_group(widths, min_diff, max_diff, max_widths):
    w = [float(x) for x in widths if x is not None and not pd.isna(x) and float(x)!=0.0]
    uw = sorted(set(w))
    if len(uw) <= 1: return True
    if len(uw) > int(max_widths): return False
    for i in range(len(uw)):
        for j in range(i+1,len(uw)):
            d = abs(uw[j]-uw[i])
            if d < min_diff or d > max_diff: return False
    return True

def get_row_widths(work, idx):
    widths = []
    for c in ["ANCHO.F.C","ANCHO.F.M"]:
        if c in work.columns:
            v = work.at[idx,c]
            if pd.notna(v) and float(v)!=0.0:
                widths.append(float(v))
    return widths

def choose_take(rest, remaining, split_min_lbs, allow_scrap_residue=False):
    try: split_min_lbs = float(split_min_lbs)
    except: split_min_lbs = 0.0
    if rest<=0 or remaining<=0: return 0.0
    if rest <= remaining+1e-9: return float(rest)
    take = float(remaining)
    if take+1e-9 < split_min_lbs: return 0.0
    residue = float(rest)-take
    if residue>1e-9 and residue+1e-9 < split_min_lbs:
        if not allow_scrap_residue: return 0.0
        return take
    return take

# ── Overshoot/undershoot con tolerancias reales ───────────────────────────
# Tolerancia % configurable por rango de LBS de la orden
# Parámetros en params:
#   OVERSHOOT_TOL_PCT_SMALL  (órdenes <= OVERSHOOT_SMALL_THRESHOLD)
#   OVERSHOOT_TOL_PCT_LARGE  (órdenes >  OVERSHOOT_SMALL_THRESHOLD)
#   OVERSHOOT_SMALL_THRESHOLD
def get_tol_pct(total_lbs, params):
    thr = float(params.get("OVERSHOOT_SMALL_THRESHOLD", 5000))
    if total_lbs <= thr:
        return float(params.get("OVERSHOOT_TOL_PCT_SMALL", 0.05))  # 5% default
    return float(params.get("OVERSHOOT_TOL_PCT_LARGE", 0.02))       # 2% default

def choose_take_humano(rest, remaining, row, params, block_name):
    if remaining<=0 or rest<0: return 0.0,0.0,0.0
    total = float(row.get("TOTAL",0.0))
    tol_pct = get_tol_pct(total, params)
    allow_over  = int(params.get("OVERSHOOT_ENABLE",0))==1
    allow_under = int(params.get("UNDERSHOOT_ENABLE",0))==1
    take = min(rest, remaining)
    over_extra=0.0; under_saved=0.0
    if allow_over:
        cap_room = remaining-take
        if cap_room>1e-9 and rest<=take+1e-9:
            extra = min(cap_room, tol_pct*total)
            if extra>0: take+=extra; over_extra=extra
    if allow_under and take>remaining+1e-9:
        reducible = min(take-remaining, tol_pct*total)
        if reducible>0: take-=reducible; under_saved=reducible
    take = min(take, remaining)
    if take<=1e-9: return 0.0,0.0,0.0
    return float(take),float(over_extra),float(under_saved)

def build_ranges(df_cap):
    """
    df_cap may now have per-row params:
    MIN_DIFF, MAX_DIFF, MAX_WIDTHS, MAX_SKU, WIDTHS_TARGET_ORDER,
    OVERSHOOT, UNDERSHOOT, UPGRADE_CATEGORIA, SCRAP_REMAINDER,
    PERMITIR_RANGO_SUPERIOR, MAX_SALTO_RANGO, APPLY_RULES_BLEACH,
    SPLIT_MIN_LBS
    All are optional; fall back to global params when missing.
    """
    BOOL_COLS  = ["OVERSHOOT","UNDERSHOOT","UPGRADE_CATEGORIA",
                  "SCRAP_REMAINDER","PERMITIR_RANGO_SUPERIOR","APPLY_RULES_BLEACH"]
    FLOAT_COLS = ["MIN_DIFF","MAX_DIFF","SPLIT_MIN_LBS",
                  "OVERSHOOT_TOL_PCT","UNDERSHOOT_TOL_PCT"]
    INT_COLS   = ["MAX_WIDTHS","MAX_SKU","MAX_SALTO_RANGO"]

    ranges = []
    for _, r in df_cap.iterrows():
        entry = {
            "CATEGORIA": norm_str(r["CATEGORIA"]),
            "MINIMO":    float(r["MINIMO"]),
            "MAXIMO":    float(r["MAXIMO"]),
            "CAPACIDAD": float(r["CAPACIDAD"]),
            "MIX":       up(r["MIX"]),
            "RANGO_ID":  f"CAP_{norm_str(r['CATEGORIA'])}_{up(r['MIX'])}_{float(r['MAXIMO']):.0f}",
            # per-row params (None = use global)
            "p_MIN_DIFF":    None,
            "p_MAX_DIFF":    None,
            "p_MAX_WIDTHS":  None,
            "p_MAX_SKU":     None,
            "p_WIDTHS_TARGET_ORDER": None,
            "p_OVERSHOOT":   None,
            "p_UNDERSHOOT":  None,
            "p_UPGRADE_CATEGORIA":     None,
            "p_SCRAP_REMAINDER":       None,
            "p_PERMITIR_RANGO_SUPERIOR": None,
            "p_MAX_SALTO_RANGO":        None,
            "p_APPLY_RULES_BLEACH":     None,
            "p_SPLIT_MIN_LBS":          None,
            "p_OVERSHOOT_TOL_PCT":      None,
            "p_UNDERSHOOT_TOL_PCT":     None,
        }
        for col in BOOL_COLS:
            if col in r.index and pd.notna(r[col]):
                v = str(r[col]).strip().upper()
                entry[f"p_{col}"] = 1 if v in ("1","TRUE","YES","SI","SÍ","X") else 0
        for col in FLOAT_COLS:
            if col in r.index and pd.notna(r[col]):
                try: entry[f"p_{col}"] = float(r[col])
                except: pass
        for col in INT_COLS:
            if col in r.index and pd.notna(r[col]):
                try: entry[f"p_{col}"] = int(float(r[col]))
                except: pass
        if "WIDTHS_TARGET_ORDER" in r.index and pd.notna(r.get("WIDTHS_TARGET_ORDER")):
            entry["p_WIDTHS_TARGET_ORDER"] = str(r["WIDTHS_TARGET_ORDER"]).strip()

        ranges.append(entry)
    return sorted(ranges, key=lambda x: x["MAXIMO"], reverse=True)


def rango_param(rango, key, params, default=None):
    """Get a parameter: per-rango first, then global params, then default."""
    pkey = f"p_{key}"
    v = rango.get(pkey)
    if v is not None: return v
    if key in params: return params[key]
    return default
