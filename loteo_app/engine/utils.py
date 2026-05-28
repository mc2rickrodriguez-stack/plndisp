import pandas as pd
import re


def norm_str(x):
    if pd.isna(x):
        return ""
    return str(x).strip()


def up(x):
    return norm_str(x).upper()


def clean_cols(cols):
    out = []
    for c in cols:
        c = "" if c is None else str(c)
        c = c.replace("\n", " ").replace("\r", " ")
        c = re.sub(r"\s+", " ", c).strip()
        out.append(c)
    return out


def prioridad_bloque(prio_text: str) -> str:
    p = (prio_text or "").upper()
    if "PAST DUE" in p or "DUE" in p or "VENC" in p:
        return "VENCIDOS"
    if "AHEAD2" in p:
        return "AHEAD2"
    if "AHEAD" in p:
        return "AHEAD"
    return "OTROS"


def can_mix_blocks(b1, b2, allowed_pairs):
    if b1 == b2:
        return True
    return (b1, b2) in allowed_pairs


def valid_width_group(widths, min_diff, max_diff, max_widths):
    w = [float(x) for x in widths if x is not None and not pd.isna(x) and float(x) != 0.0]
    uw = sorted(set(w))
    if len(uw) <= 1:
        return True
    if len(uw) > int(max_widths):
        return False
    for i in range(len(uw)):
        for j in range(i + 1, len(uw)):
            d = abs(uw[j] - uw[i])
            if d < min_diff or d > max_diff:
                return False
    return True


def get_row_widths(work, idx):
    widths = []
    for c in ["ANCHO.F.C", "ANCHO.F.M"]:
        if c in work.columns:
            v = work.at[idx, c]
            if pd.notna(v) and float(v) != 0.0:
                widths.append(float(v))
    return widths


def choose_take(rest, remaining, split_min_lbs, allow_scrap_residue=False):
    try:
        split_min_lbs = float(split_min_lbs)
    except:
        split_min_lbs = 0.0
    if rest <= 0 or remaining <= 0:
        return 0.0
    if rest <= remaining + 1e-9:
        return float(rest)
    take = float(remaining)
    if take + 1e-9 < split_min_lbs:
        return 0.0
    residue = float(rest) - take
    if residue > 1e-9 and residue + 1e-9 < split_min_lbs:
        if not allow_scrap_residue:
            return 0.0
        return take
    return take


def get_percent_tolerance(row):
    total = float(row.get("TOTAL", 0.0))
    if total <= 5000:
        return 0.00
    elif total <= 10000:
        return 0.00
    elif total <= 30000:
        return 0.00
    else:
        return 0.0


def choose_take_humano(rest, remaining, row, params, block_name):
    if remaining <= 0 or rest < 0:
        return 0.0, 0.0, 0.0
    tol_pct = get_percent_tolerance(row)
    total = float(row.get("TOTAL", 0.0))
    allow_over = int(params.get("OVERSHOOT_ENABLE", 0)) == 1
    allow_under = int(params.get("UNDERSHOOT_ENABLE", 0)) == 1
    take = min(rest, remaining)
    over_extra = 0.0
    under_saved = 0.0
    if allow_over:
        cap_room = remaining - take
        if cap_room > 1e-9 and rest <= take + 1e-9:
            max_extra = tol_pct * total
            extra = min(cap_room, max_extra)
            if extra > 0:
                take += extra
                over_extra = extra
    if allow_under and take > remaining + 1e-9:
        reducible = min(take - remaining, tol_pct * total)
        if reducible > 0:
            take -= reducible
            under_saved = reducible
    take = min(take, remaining)
    if take <= 1e-9:
        return 0.0, 0.0, 0.0
    return float(take), float(over_extra), float(under_saved)


def build_ranges(df_cap):
    ranges = []
    for _, r in df_cap.iterrows():
        ranges.append({
            "CATEGORIA": norm_str(r["CATEGORIA"]),
            "MINIMO": float(r["MINIMO"]),
            "MAXIMO": float(r["MAXIMO"]),
            "CAPACIDAD": float(r["CAPACIDAD"]),
            "MIX": up(r["MIX"]),
            "RANGO_ID": f"CAP_{norm_str(r['CATEGORIA'])}_{up(r['MIX'])}_{float(r['MAXIMO']):.0f}"
        })
    return sorted(ranges, key=lambda x: x["MAXIMO"], reverse=True)
