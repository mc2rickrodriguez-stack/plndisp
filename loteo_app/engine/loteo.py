"""
Motor de loteo v4:
- Parámetros por categoría (desde tabla CAPACIDADES_TINTO)
- Overshoot/undershoot con tolerancias reales configurables
- Score unificado con slider de calidad (QUALITY_LEVEL 1-10 → BEAM_WIDTH)
- WIDTHS_TARGET_ORDER como peso en scoring, no como filtro multiplicador
- PERMITIR_RANGO_SUPERIOR reemplaza UPGRADE_CATEGORIA, con MAX_SALTO_RANGO
- RESTRICCIONES_ANCHO absorbe lógica ANCHO18 completamente
- Pre-cómputo de anchos y arrays NumPy para velocidad
- Decision Log con RAZON_DESCARTE (código + español)
- Soporte cancelación via cancel_flag
"""
import pandas as pd
import numpy as np
import re
from engine.utils import (
    norm_str, up, prioridad_bloque, can_mix_blocks,
    valid_width_group, get_row_widths, choose_take,
    choose_take_humano, build_ranges, rango_param,
)
from engine.disponibilidad import (
    DisponibilidadIndex, LnkDisponibilidad, FUENTE_INV, DIA_COLS,
)

# ── QUALITY_LEVEL → BEAM_WIDTH mapping ──────────────────────────────────────
def quality_to_beam(level: int) -> int:
    """1=fastest … 10=best quality"""
    table = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5, 7:7, 8:10, 9:15, 10:20}
    return table.get(max(1,min(10,int(level))), 3)

# ── Priority helpers ─────────────────────────────────────────────────────────
def order_priorities(pris, params):
    pris = [float(x) for x in pris if x is not None]
    po_text = norm_str(params.get("PRIORITY_ORDER",""))
    if po_text:
        plan = [p.strip() for p in po_text.split(">") if p.strip()]
        rank = {}
        for i,v in enumerate(plan):
            if re.match(r"^\d+(\.\d+)?$",v): rank[float(v)]=i
        return sorted(pris, key=lambda x:(rank.get(float(x),10_000),float(x)))
    return sorted(pris)

def order_by_priorities(base_ranges, prioridades):
    used=set(); out=[]
    for cap in prioridades:
        for r in base_ranges:
            if abs(float(r["MAXIMO"])-float(cap))<1e-6 and id(r) not in used:
                out.append(r); used.add(id(r))
    for r in base_ranges:
        if id(r) not in used: out.append(r)
    return out

# ── Rule selection ───────────────────────────────────────────────────────────
def reorder_ranges_for_seed(ranges_mix, mixv, work, seed_idx, params, width_cache):
    base = list(ranges_mix)
    rule_info = {
        "regla_aplicada": "NONE",
        "prioridades": [],
        "match_combo": False,
        "limite_ancho_style": None,
        "origen_prioridad": "MIX",
        "combo_target_width": None,
    }

    apply_bleach = int(params.get("APPLY_RULES_BLEACH",0))==1
    if up(mixv) not in ("DYE",) and not apply_bleach:
        return base, rule_info

    fam     = up(work.at[seed_idx,"FAMILIA"])   if "FAMILIA"  in work.columns else ""
    color_r = up(work.at[seed_idx,"COLOR_R"])   if "COLOR_R"  in work.columns else ""
    style   = up(work.at[seed_idx,"STYLE"])     if "STYLE"    in work.columns else ""

    seed_ws = width_cache.get(seed_idx, [])
    ancho_c = seed_ws[0] if len(seed_ws)>0 else 0.0
    ancho_m = seed_ws[1] if len(seed_ws)>1 else (seed_ws[0] if len(seed_ws)>0 else 0.0)

    restr_fam   = params.get("RESTRICCIONES_FAMILIA",{})
    restr_color = params.get("RESTRICCIONES_COLOR",{})
    restr_ancho = params.get("RESTRICCIONES_ANCHO",{})
    reglas_combo= params.get("REGLAS_ANCHOS_COMBINADOS",[])
    rule_order  = [x.strip().upper() for x in norm_str(params.get("RULE_ORDER","")).split(">") if x.strip()] or \
                  ["RESTRICCION_ANCHO","COMBO_ANCHOS","COLOR_R","FAMILIA","DEFAULT"]
    # FIX: ANCHO18 es alias de RESTRICCION_ANCHO (compatibilidad con configs anteriores)
    rule_order  = ["RESTRICCION_ANCHO" if t=="ANCHO18" else t for t in rule_order]

    def ancho_leq(lim):
        vals=[v for v in [ancho_c,ancho_m] if v>0]
        return bool(vals) and min(vals)<=float(lim)

    def try_restriccion_ancho():
        if style in restr_ancho:
            lim = restr_ancho[style].get("limite",None)
            pris = order_priorities(restr_ancho[style].get("prioridades",[]),params)
            if lim is not None and ancho_leq(lim) and pris:
                rule_info.update({"regla_aplicada":"RESTRICCION_ANCHO",
                                  "prioridades":list(pris),
                                  "limite_ancho_style":lim,
                                  "origen_prioridad":"STYLE"})
                return order_by_priorities(base,pris)

    def try_combo():
        for regla in reglas_combo:
            a1,a2 = regla["a1"],regla["a2"]
            pris = order_priorities(regla["prioridades"],params)
            seed_matches = any(abs(w-a1)<1e-6 or abs(w-a2)<1e-6 for w in seed_ws if w>0)
            if not seed_matches: continue
            objetivo = a2 if any(abs(w-a1)<1e-6 for w in seed_ws if w>0) else a1
            existe = any(
                float(work.at[idx,"LBS_RESTANTES"])>0 and
                any(abs(w-objetivo)<1e-6 for w in width_cache.get(idx,[]) if w>0)
                for idx in work.index if idx!=seed_idx
            )
            if existe and pris:
                rule_info.update({"regla_aplicada":"COMBO_ANCHOS","prioridades":list(pris),
                                  "match_combo":True,"origen_prioridad":"COMBO",
                                  "combo_target_width":float(objetivo)})
                return order_by_priorities(base,pris)

    def try_color_r():
        if color_r in restr_color and restr_color[color_r]:
            p=float(restr_color[color_r])
            rule_info.update({"regla_aplicada":"COLOR_R","prioridades":[p],"origen_prioridad":"COLOR"})
            return order_by_priorities(base,[p])

    def try_familia():
        if fam in restr_fam and restr_fam[fam]:
            pris=order_priorities(restr_fam[fam],params)
            rule_info.update({"regla_aplicada":"FAMILIA","prioridades":list(pris),"origen_prioridad":"FAMILIA"})
            return order_by_priorities(base,pris)

    for token in rule_order:
        out=None
        if   token in ("RESTRICCION_ANCHO","ANCHO18"): out=try_restriccion_ancho()
        elif token=="COMBO_ANCHOS":  out=try_combo()
        elif token=="COLOR_R":       out=try_color_r()
        elif token=="FAMILIA":       out=try_familia()
        elif token=="DEFAULT":       out=base
        if out is not None: return out,rule_info
    return base,rule_info

# ── Range matching ───────────────────────────────────────────────────────────
def ranges_matching_priority(pri, ranges_try, allow_nearest_higher=True):
    pri=float(pri)
    exact=[r for r in ranges_try if abs(float(r["MAXIMO"])-pri)<=1e-6]
    if exact: return exact
    if not allow_nearest_higher: return []
    higher=sorted([r for r in ranges_try if float(r["MAXIMO"])>=pri-1e-6],
                  key=lambda r:(float(r["MAXIMO"])-pri,-float(r["MAXIMO"])))
    return [higher[0]] if higher else []

# ── Score ─────────────────────────────────────────────────────────────────────
DESCARTE_MSGS = {
    "CAP_AGOTADA":        "Capacidad del rango agotada para este período",
    "LBS_INSUFICIENTES":  "LBS insuficientes para el mínimo del rango",
    "ANCHO_INCOMPATIBLE": "Diferencia de anchos fuera del rango permitido",
    "BLOQUE_INCOMPATIBLE":"Mezcla de prioridades no permitida",
    "MAX_SKU_EXCEDIDO":   "Se superaría el límite de SKUs por lote",
    "SPLIT_MIN":          "Residuo menor al mínimo de split permitido",
    "MAX_WIDTHS":         "Se superaría el máximo de anchos distintos",
    "RANGO_SUPERIOR":     "Rango superior no permitido o salto excesivo",
    "RESCUE":             "Lote de rescate — LNK no pudo entrar en loteo normal",
}

def score_lote(lote_dict, widths_set, params, rango):
    if lote_dict is None: return -1e30

    ql = int(params.get("QUALITY_LEVEL",5))
    W_FILL      = 3.0 + ql*0.4
    W_CAP_LOSS  = 1.0 + ql*0.2
    W_WIDTH     = 1.0 + (10-ql)*0.3

    total  = float(lote_dict.get("TOTAL_LOTE",0.0))
    maximo = float(lote_dict.get("MAXIMO",1.0))
    fill   = total/maximo if maximo>1e-9 else 0.0
    cap_loss_norm = (maximo-total)/maximo if maximo>1e-9 else 0.0

    order_text = rango_param(rango,"WIDTHS_TARGET_ORDER",params,"2>3>1")
    targets=[int(x) for x in str(order_text).split(">") if x.strip().isdigit()]
    nw=len(widths_set)
    try:    rank=targets.index(nw)
    except: rank=len(targets)+abs(nw-(targets[-1] if targets else 2))
    width_score = -float(rank)/max(len(targets),1)

    score = W_FILL*fill - W_CAP_LOSS*cap_loss_norm + W_WIDTH*width_score

    # ── PREFERIR_LOTES_SIMPLES ────────────────────────────────────────────
    # Cuando está activo, penaliza cada ancho y LNK adicional más allá del
    # primero. El algoritmo sigue pudiendo formar lotes complejos si no hay
    # otra opción, pero los prefiere solo como último recurso.
    if int(params.get("PREFERIR_LOTES_SIMPLES", 0)) == 1:
        n_lnks = int(lote_dict.get("N_LNKS", nw))  # passed from caller
        pen_anchos = float(params.get("PENALIZACION_ANCHO_EXTRA", 1.5))
        pen_lnks   = float(params.get("PENALIZACION_LNK_EXTRA",   0.8))
        # Penalización acumulativa: 0 para el primero, pen * (n-1) para el resto
        anchos_extra = max(0, nw - 1)
        lnks_extra   = max(0, n_lnks - 1)
        score -= pen_anchos * anchos_extra
        score -= pen_lnks   * lnks_extra

    return score

# ── Core lote builder ─────────────────────────────────────────────────────────
def intentar_lote_para_rango(work, seed_idx, rango, capacity_used, params,
                              rule_info, width_cache,
                              require_two_widths=False, split_min_lbs=None):
    """
    Build a lote starting from seed_idx for the given rango.
    Returns lote dict or None. Also returns list of reject reasons for log.
    """
    rid         = rango["RANGO_ID"]
    cap_left    = max(0.0, float(rango["CAPACIDAD"]) - float(capacity_used.get(rid,0.0)))
    if cap_left<=0: return None, ["CAP_AGOTADA"]

    max_allowed = min(float(rango["MAXIMO"]), cap_left)
    if float(work.at[seed_idx,"LBS_RESTANTES"])<=0: return None,["LBS_INSUFICIENTES"]

    # Per-rango parameters (fall back to global)
    min_diff   = float(rango_param(rango,"MIN_DIFF",   params, 0.0))
    max_diff   = float(rango_param(rango,"MAX_DIFF",   params, 999.0))
    max_widths = int(  rango_param(rango,"MAX_WIDTHS", params, 6))
    max_sku    = int(  rango_param(rango,"MAX_SKU",    params, 10))
    split_min  = float(split_min_lbs if split_min_lbs is not None
                       else rango_param(rango,"SPLIT_MIN_LBS",params,100.0))
    allowed_pairs = params.get("MIX_ALLOWED",set())
    allow_scrap   = int(rango_param(rango,"SCRAP_REMAINDER",params,1))==1

    lote_rows=[]; lote_lbs=0.0; lote_lnks=set()
    lote_blocks=[]; lote_widths=[]
    rejects=[]

    seed_tono = up(work.at[seed_idx,"TONO"]) if "TONO" in work.columns and pd.notna(work.at[seed_idx,"TONO"]) else ""

    def can_add(idx, lbs_to_add):
        if lbs_to_add<=0: return False,""
        if "TONO" in work.columns:
            rt = up(work.at[idx,"TONO"]) if pd.notna(work.at[idx,"TONO"]) else ""
            if rt!=seed_tono: return False,"TONO_DISTINTO"
        if len(lote_lnks|{work.at[idx,"LNK"]})>max_sku: return False,"MAX_SKU_EXCEDIDO"
        b=work.at[idx,"BLOQUE"]
        for eb in lote_blocks:
            if not can_mix_blocks(eb,b,allowed_pairs): return False,"BLOQUE_INCOMPATIBLE"
        wc = lote_widths + width_cache.get(idx,[])
        if not valid_width_group(wc,min_diff,max_diff,max_widths): return False,"ANCHO_INCOMPATIBLE"
        if lote_lbs+lbs_to_add>max_allowed+1e-9: return False,"CAPACIDAD_LOTE"
        return True,""

    seed_rest = float(work.at[seed_idx,"LBS_RESTANTES"])
    seed_total_orig = float(work.at[seed_idx,"TOTAL"]) if "TOTAL" in work.columns else seed_rest
    # FIX: if this is the last remaining portion (≤ original order), ignore SPLIT_MIN
    # SPLIT_MIN exists to avoid tiny future residues, not to block the final piece
    is_last_portion = (seed_rest <= seed_total_orig + 1e-6)
    effective_split = 0.0 if is_last_portion else split_min
    remaining = max_allowed
    use_human = int(rango_param(rango,"OVERSHOOT",params,0))==1

    if use_human:
        take,oe,us = choose_take_humano(seed_rest,remaining,work.loc[seed_idx],params,"")
    else:
        take = choose_take(seed_rest,remaining,effective_split,allow_scrap_residue=allow_scrap)
        oe=us=0.0

    ok,reason = can_add(seed_idx,take)
    if take<=0 or not ok:
        return None,[reason or "LBS_INSUFICIENTES"]

    lote_rows.append((seed_idx,take,oe,us))
    lote_lbs+=take
    lote_lnks.add(work.at[seed_idx,"LNK"])
    lote_blocks.append(work.at[seed_idx,"BLOQUE"])
    lote_widths+=width_cache.get(seed_idx,[])

    combo_target=rule_info.get("combo_target_width") if rule_info else None

    # Pre-filter candidate indices (only those with LBS > 0, not seed)
    seed_set = {seed_idx}
    _preferir_simples = int(params.get("PREFERIR_LOTES_SIMPLES", 0)) == 1
    _pen_ancho = float(params.get("PENALIZACION_ANCHO_EXTRA", 1.5))
    _pen_lnk   = float(params.get("PENALIZACION_LNK_EXTRA",   0.8))

    while True:
        remaining=max_allowed-lote_lbs
        if remaining<=1e-6: break
        best=None; best_take=0.0; best_score=-1e30
        lote_set=set(i for i,*_ in lote_rows)
        widths_now=set(float(w) for w in lote_widths if w and not pd.isna(w) and float(w)!=0.0)
        lnks_now=len(lote_lnks)

        for idx in work.index:
            if idx in lote_set: continue
            rest=float(work.at[idx,"LBS_RESTANTES"])
            if rest<=0: continue
            orig=float(work.at[idx,"TOTAL"]) if "TOTAL" in work.columns else rest
            eff_split=0.0 if rest<=orig+1e-6 else split_min
            tk=choose_take(rest,remaining,eff_split,allow_scrap_residue=allow_scrap)
            if tk<=0: continue
            ok,_=can_add(idx,tk)
            if not ok: continue

            # Base score: prefer items that fill more
            widths_add=set(float(w) for w in width_cache.get(idx,[]) if w and float(w)!=0.0)
            combo_bonus=1e-3 if (combo_target and any(abs(w-combo_target)<1e-6 for w in widths_add)) else 0
            sc=lote_lbs+tk+combo_bonus

            # PREFERIR_LOTES_SIMPLES: penalize adding new widths or new LNKs
            if _preferir_simples:
                new_widths = widths_add - widths_now
                adds_new_width = len(new_widths) > 0
                is_new_lnk = work.at[idx,"LNK"] not in lote_lnks
                sc -= _pen_ancho * (1 if adds_new_width else 0)
                sc -= _pen_lnk   * (1 if is_new_lnk else 0)

            if sc>best_score: best_score=sc; best=idx; best_take=tk

        if best is None: break
        lote_rows.append((best,best_take,0.0,0.0))
        lote_lbs+=best_take
        lote_lnks.add(work.at[best,"LNK"])
        lote_blocks.append(work.at[best,"BLOQUE"])
        lote_widths+=width_cache.get(best,[])

    if lote_lbs+1e-9<float(rango["MINIMO"]):
        return None,["LBS_INSUFICIENTES"]

    final_widths=sorted(set(float(w) for w in lote_widths if w and not pd.isna(w) and float(w)!=0.0))
    if require_two_widths and len(final_widths)<2:
        return None,["COMBO_ANCHOS_SIN_PAR"]

    return {
        "RANGO_ID":  rango["RANGO_ID"],
        "CATEGORIA": rango["CATEGORIA"],
        "MIX":       rango["MIX"],
        "MINIMO":    float(rango["MINIMO"]),
        "MAXIMO":    float(rango["MAXIMO"]),
        "TOTAL_LOTE":float(lote_lbs),
        "ROWS":      lote_rows,
        "REQUIERE_2_ANCHOS": bool(require_two_widths),
        "FINAL_WIDTHS": final_widths,
    }, []

# ── Upgrade rango (PERMITIR_RANGO_SUPERIOR) ──────────────────────────────────
def get_allowed_ranges(rango_target, all_ranges_mix, params):
    """Return rango_target + allowed superior ranges based on MAX_SALTO_RANGO."""
    permitir = int(rango_param(rango_target,"PERMITIR_RANGO_SUPERIOR",params,0))==1
    if not permitir: return [rango_target]
    max_salto = int(rango_param(rango_target,"MAX_SALTO_RANGO",params,1))
    # find position of this rango in sorted list (ascending by MAXIMO)
    sorted_r = sorted(all_ranges_mix, key=lambda r: float(r["MAXIMO"]))
    try: pos=next(i for i,r in enumerate(sorted_r) if r["RANGO_ID"]==rango_target["RANGO_ID"])
    except StopIteration: return [rango_target]
    allowed=[rango_target]
    for step in range(1,max_salto+1):
        ni=pos+step
        if ni<len(sorted_r): allowed.append(sorted_r[ni])
    return allowed

# ── Look-ahead para VENCIDOS ──────────────────────────────────────────────
def _lookahead_vencidos_ok(work, lote_rows, ranges_mix, capacity_used_snap, params, width_cache):
    """
    Después de tentativamente formar un lote que contiene al menos un VENCIDO,
    verifica que las LBS vencidas que NO entraron al lote puedan formar
    al menos otro lote válido (llegando al mínimo del rango más pequeño con capacidad).

    Opción B: aplica cuando el lote contiene VENCIDOS, sin importar si el seed
    es VENCIDO o AHEAD (cubre lotes mixtos).

    Retorna True si el look-ahead pasa (el lote es seguro confirmar).
    Retorna False si confirmar este lote dejaría VENCIDOS huérfanos.
    """
    # ¿El lote propuesto contiene algún VENCIDO?
    lote_indices = {idx for idx, *_ in lote_rows}
    lote_has_vencido = any(
        work.at[idx, "BLOQUE"] == "VENCIDOS" for idx in lote_indices
        if idx in work.index
    )
    if not lote_has_vencido:
        return True  # no aplica, dejar pasar

    # Calcular LBS vencidas que quedarían FUERA del lote
    lbs_asig_por_idx = {idx: lbs for idx, lbs, *_ in lote_rows}
    lbs_vencidos_restantes = 0.0
    for idx in work.index:
        if work.at[idx, "BLOQUE"] != "VENCIDOS":
            continue
        lbs_rest = float(work.at[idx, "LBS_RESTANTES"])
        if lbs_rest <= 0:
            continue
        asig = lbs_asig_por_idx.get(idx, 0.0)
        sobra = max(0.0, lbs_rest - asig)
        lbs_vencidos_restantes += sobra

    # Si no quedan LBS vencidas fuera del lote, look-ahead pasa trivialmente
    if lbs_vencidos_restantes <= 1e-6:
        return True

    # Mínimo del rango más pequeño con capacidad disponible
    min_rango = None
    for r in sorted(ranges_mix, key=lambda x: float(x["MINIMO"])):
        rid = r["RANGO_ID"]
        cap_left = float(r["CAPACIDAD"]) - float(capacity_used_snap.get(rid, 0.0))
        if cap_left > 1e-6:
            min_rango = float(r["MINIMO"])
            break

    if min_rango is None:
        return True  # sin capacidad disponible de todos modos, no bloqueamos

    # ¿Las LBS vencidas restantes alcanzan el mínimo?
    return lbs_vencidos_restantes >= min_rango - 1e-6


# ── Main run_loteo ────────────────────────────────────────────────────────────
def run_loteo(df_data, df_cap, params,
              progress_callback=None, cancel_flag=None,
              dispon_index: "DisponibilidadIndex | None" = None):
    """
    progress_callback(pct, msg, stats) where stats is a dict.
    cancel_flag: a list [False] — set [True] from outside to cancel.
    dispon_index: DisponibilidadIndex para modo restricción de tejido.
                  None = modo libre (comportamiento original).
    Returns (df_detalle, df_resumen, df_excedentes, df_params, cancelled:bool,
             df_detalle_tejido, df_stock_report)
    Los dos últimos son DataFrames vacíos en modo libre.
    """
    modo_restriccion = dispon_index is not None

    ranges = build_ranges(df_cap)
    capacity_used = {r["RANGO_ID"]:0.0 for r in ranges}

    data = df_data.copy()
    data["BLOQUE"]       = data["PRIORIDAD"].apply(prioridad_bloque)
    data["LBS_RESTANTES"]= data["TOTAL"].astype(float)
    data["LBS_SCRAP"]    = 0.0

    # ── Pre-compute width cache (major speedup) ──────────────────────────
    width_cache = {}
    for idx in data.index:
        ws=[]
        for c in ["ANCHO.F.C","ANCHO.F.M"]:
            if c in data.columns:
                v=data.at[idx,c]
                if pd.notna(v) and float(v)!=0.0: ws.append(float(v))
        width_cache[idx]=ws

    # ── Pre-filtro de disponibilidad (modo restricción) ──────────────────
    # Filtramos por LNK completo: si CUALQUIER fila del LNK no tiene
    # disponibilidad, TODAS las filas de ese LNK van a excedentes.
    # Usar la primera fila del LNK para evaluar (mismos componentes en todas).
    if modo_restriccion:
        lnks_sin_dispon: set = set()
        # Evaluar un LNK una sola vez (la primera fila que aparezca)
        lnks_vistos: set = set()
        for idx in data.index:
            lnk_id = data.at[idx, "LNK"]
            if lnk_id in lnks_vistos:
                continue
            lnks_vistos.add(lnk_id)
            if not dispon_index.lnk_tiene_disponibilidad(data.loc[idx]):
                lnks_sin_dispon.add(lnk_id)

        sin_dispon = data[data["LNK"].isin(lnks_sin_dispon)].index
        data_sin_dispon = data.loc[list(sin_dispon)].copy()
        data_con_dispon = data.drop(index=list(sin_dispon)).copy()
    else:
        data_sin_dispon = pd.DataFrame(columns=data.columns)
        data_con_dispon = data.copy()

    # Trabajamos sobre data_con_dispon para el loop principal
    data = data_con_dispon

    # Reasignamos el width_cache para que solo contenga índices válidos
    width_cache = {idx: width_cache[idx] for idx in data.index if idx in width_cache}

    detalle=[]; resumen=[]; lote_id_global=1
    block_order=["VENCIDOS","AHEAD","AHEAD2","OTROS"]

    group_keys=["TELA.CUERPO","MIX"]
    if "TONO" in data.columns: group_keys.insert(1,"TONO")
    else:                       group_keys.insert(1,"COLOR")

    groups=list(data.groupby(group_keys).groups.items())
    total_groups=len(groups)
    lotes_formados=0
    lbs_procesadas=0.0

    quality_level = int(params.get("QUALITY_LEVEL",5))
    beam_w = quality_to_beam(quality_level)
    cancelled=False

    for g_num,(keys,grp_idx) in enumerate(groups):
        if cancel_flag and cancel_flag[0]:
            cancelled=True; break

        if progress_callback:
            pct=g_num/total_groups
            progress_callback(pct,
                f"Grupo {g_num+1}/{total_groups} · Tela: {keys[0]}",
                {"lotes":lotes_formados,"lbs":lbs_procesadas,"grupo":g_num+1,"total":total_groups})

        work=data.loc[grp_idx].copy()
        mixv=keys[-1]
        if "TONO" in data.columns: tela,tono=keys[0],keys[1]
        else:                       tela,tono=keys[0],keys[1]  # color as tono key

        ranges_mix=[r for r in ranges if r["MIX"]==mixv]
        blocked=set()

        while True:
            if cancel_flag and cancel_flag[0]: cancelled=True; break
            work["LBS_RESTANTES"]=pd.to_numeric(work["LBS_RESTANTES"],errors="coerce").fillna(0.0)
            if (work["LBS_RESTANTES"]>0).sum()==0: break
            made_any=False

            for b in block_order:
                if b in blocked: continue
                cand=work[(work["BLOQUE"]==b)&(work["LBS_RESTANTES"]>0)]
                if len(cand)==0: blocked.add(b); continue

                top_seeds=cand.sort_values("LBS_RESTANTES",ascending=False).head(beam_w).index.tolist()
                best_lote=None; best_pack=None; best_score=-1e30

                for seed_idx in top_seeds:
                    # ── Modo restricción: verificar disponibilidad del seed ──
                    if modo_restriccion:
                        lnk_disp_seed = dispon_index.elegir_lote_face(
                            work.loc[seed_idx], bloque=b
                        )
                        if lnk_disp_seed is None:
                            continue   # sin tejido disponible para este seed
                    else:
                        lnk_disp_seed = None
                    ranges_try,rule_info=reorder_ranges_for_seed(
                        ranges_mix,mixv,work,seed_idx,params,width_cache)

                    pri_list=order_priorities(rule_info.get("prioridades",[]),params)
                    pri_iter=pri_list if pri_list else [None]

                    lote=None; prioridad_obj=None

                    for pri in pri_iter:
                        candidate_ranges = ranges_matching_priority(pri,ranges_try) if pri is not None else ranges_try
                        for r in candidate_ranges:
                            if capacity_used[r["RANGO_ID"]]>=r["CAPACIDAD"]-1e-6: continue
                            # try with require_two if COMBO_ANCHOS
                            req2=(rule_info.get("regla_aplicada")=="COMBO_ANCHOS")
                            split_min=float(rango_param(r,"SPLIT_MIN_LBS",params,100.0))
                            intento,_=intentar_lote_para_rango(
                                work,seed_idx,r,capacity_used,params,rule_info,width_cache,
                                require_two_widths=req2,split_min_lbs=split_min)
                            if intento is None and req2:
                                intento,_=intentar_lote_para_rango(
                                    work,seed_idx,r,capacity_used,params,rule_info,width_cache,
                                    require_two_widths=False,split_min_lbs=split_min)
                            if intento is not None:
                                lote=intento; prioridad_obj=float(pri) if pri else None
                                break
                        if lote: break

                    # Fallback: try all ranges without priority filter
                    if lote is None:
                        for r in ranges_try:
                            if capacity_used[r["RANGO_ID"]]>=r["CAPACIDAD"]-1e-6: continue
                            # check PERMITIR_RANGO_SUPERIOR
                            allowed_r=get_allowed_ranges(r,ranges_mix,params)
                            for ar in allowed_r:
                                if capacity_used[ar["RANGO_ID"]]>=ar["CAPACIDAD"]-1e-6: continue
                                split_min=float(rango_param(ar,"SPLIT_MIN_LBS",params,100.0))
                                intento,_=intentar_lote_para_rango(
                                    work,seed_idx,ar,capacity_used,params,rule_info,width_cache,
                                    split_min_lbs=split_min)
                                if intento:
                                    lote=intento; break
                            if lote: break

                    if lote is not None:
                        # ── Look-ahead VENCIDOS ────────────────────────────
                        if int(params.get("LOOKAHEAD_VENCIDOS", 1)) == 1:
                            la_ok = _lookahead_vencidos_ok(
                                work, lote["ROWS"], ranges_mix,
                                capacity_used, params, width_cache)
                            if not la_ok:
                                lote = None

                    # ── Modo restricción: votar por el LOTE FACE que minimice el
                    #    día máximo global del lote completo ──────────────────
                    lote_face_elegido = None
                    plan_tejido_lote: dict = {}   # {lnk: LnkDisponibilidad}

                    if lote is not None and modo_restriccion:
                        # Construir filas con LBS proporcionales al lote.
                        # Un LNK puede tener LB.CUERPO = 26,400 (total del plan)
                        # pero en este lote solo se le asignan 3,300 LBS.
                        # Escalamos LB.* por el ratio (lbs_asig / TOTAL) para que
                        # check_lnk reserve solo lo que este lote necesita.
                        lnk_rows_del_lote = []
                        seen_lnks: set = set()
                        for idx, lbs_asig, *_ in lote["ROWS"]:
                            lnk_id = work.at[idx, "LNK"]
                            if lnk_id in seen_lnks:
                                continue   # mismo LNK en dos filas (split prioridad)
                            seen_lnks.add(lnk_id)
                            row = work.loc[idx].copy()
                            total_lnk = float(row.get("TOTAL", 0)) or 1.0
                            ratio = min(1.0, float(lbs_asig) / total_lnk)
                            for lbs_col in ["LB.CUERPO", "LB.MANGAS", "LB.RIB", "LB.POCKET"]:
                                if lbs_col in row.index:
                                    try:
                                        row[lbs_col] = float(row[lbs_col]) * ratio
                                    except Exception:
                                        pass
                            lnk_rows_del_lote.append(row)

                        resultado_lf = dispon_index.elegir_lote_face_lote(
                            lnk_rows_del_lote, bloque=b
                        )
                        if resultado_lf is None:
                            lote = None   # ningún LOTE FACE puede cubrir el lote completo
                        else:
                            lote_face_elegido, plan_tejido_lote = resultado_lf

                    if lote is not None:
                        wset=set(lote["FINAL_WIDTHS"])
                        n_lnks=len({work.at[idx,"LNK"] for idx,*_ in lote["ROWS"]})
                        sc=score_lote({"TOTAL_LOTE":lote["TOTAL_LOTE"],"MAXIMO":lote["MAXIMO"],
                                        "N_LNKS":n_lnks},
                                      wset,params,r if 'r' in dir() else ranges_mix[0])
                        if sc>best_score:
                            best_score=sc; best_lote=lote
                            best_pack=(lote,rule_info,prioridad_obj,best_score,
                                       lote_face_elegido,plan_tejido_lote)

                if best_lote is None: blocked.add(b); continue

                lote,rule_info,prioridad_obj,best_score,lote_face_elegido,plan_tejido_lote=best_pack
                lote_id=f"L{lote_id_global:06d}"; lote_id_global+=1
                anchos_lote=lote["FINAL_WIDTHS"]
                anchos_lote_str=str(anchos_lote)

                regla_final=rule_info.get("regla_aplicada","NONE")
                req2_flag=False
                if regla_final=="COMBO_ANCHOS":
                    req2_flag=bool(lote.get("REQUIERE_2_ANCHOS",False)) and len(anchos_lote)>=2
                    if not req2_flag: regla_final="COMBO_ANCHOS_FALLBACK"

                split_min_used=float(rango_param(
                    next((r for r in ranges if r["RANGO_ID"]==lote["RANGO_ID"]),ranges[0]),
                    "SPLIT_MIN_LBS",params,100.0))

                # Determinar día máximo del lote (para reportes)
                dia_max_lote_num = 0
                if modo_restriccion and plan_tejido_lote:
                    dia_max_lote_num = max(
                        (v.dia_maximo_lote for v in plan_tejido_lote.values()),
                        default=-1
                    )
                    dia_max_lote_num = max(0, dia_max_lote_num + 1)  # 1-based; 0=hoy

                for idx,lbs_asig,oe,us in lote["ROWS"]:
                    lnk_id = work.at[idx,"LNK"]
                    detalle.append({
                        "LOTE_ID":lote_id,"ANCHOS_LOTE":anchos_lote_str,
                        "CATEGORIA":lote["CATEGORIA"],"MIX":lote["MIX"],
                        "TELA.CUERPO":tela,"COLOR":work.at[idx,"COLOR"],
                        "TONO":work.at[idx,"TONO"] if "TONO" in work.columns else "",
                        "LNK":lnk_id,"PRIORIDAD":work.at[idx,"PRIORIDAD"],
                        "BLOQUE":work.at[idx,"BLOQUE"],
                        "ANCHO.F.C":float(work.at[idx,"ANCHO.F.C"]),
                        "ANCHO.F.M":float(work.at[idx,"ANCHO.F.M"]),
                        "CONSUMO_C":float(work.at[idx,"CONSUMO_C"]),
                        "FAMILIA":work.at[idx,"FAMILIA"],"COLOR_R":work.at[idx,"COLOR_R"],
                        "STYLE":work.at[idx,"STYLE"],
                        "LBS_ASIGNADAS":float(lbs_asig),
                        "LBS_EXTRA_SOBRE_ORDEN":float(max(0.0,oe)),
                        "APLICA_REGLA":regla_final,
                        "PRIORIDAD_USADA":float(lote["MAXIMO"]),
                        "PRIORIDAD_OBJETIVO":prioridad_obj,
                        "ORIGEN_PRIORIDAD":rule_info.get("origen_prioridad","MIX"),
                        "PERMITIR_RANGO_SUPERIOR":int(rango_param(
                            next((r for r in ranges if r["RANGO_ID"]==lote["RANGO_ID"]),ranges[0]),
                            "PERMITIR_RANGO_SUPERIOR",params,0)),
                        "SPLIT_MIN_USADO":split_min_used,
                        "DECISION_SCORE":float(best_score),
                        # Campos de restricción de tejido
                        "LOTE_FACE":lote_face_elegido or "",
                        "DIA_MAX_LOTE": dia_max_lote_num if modo_restriccion else None,
                    })
                    prev=float(work.at[idx,"LBS_RESTANTES"])
                    work.at[idx,"LBS_RESTANTES"]=max(0.0,prev-float(lbs_asig))
                    if int(rango_param(next((r for r in ranges if r["RANGO_ID"]==lote["RANGO_ID"]),ranges[0]),
                                       "SCRAP_REMAINDER",params,1))==1:
                        rem=float(work.at[idx,"LBS_RESTANTES"])
                        if rem>1e-9 and rem+1e-9<split_min_used:
                            work.at[idx,"LBS_SCRAP"]+=rem
                            work.at[idx,"LBS_RESTANTES"]=0.0

                # ── Confirmar consumo de tejido UNA VEZ por LNK único ──────
                # Un LNK puede aparecer múltiples veces en ROWS (splits de
                # prioridad). consume() debe llamarse una sola vez por LNK
                # para no doble-descontar el stock ni duplicar el reporte.
                if modo_restriccion and plan_tejido_lote:
                    lnks_consumidos: set = set()
                    for idx, *_ in lote["ROWS"]:
                        lnk_id = work.at[idx, "LNK"]
                        if lnk_id not in lnks_consumidos and lnk_id in plan_tejido_lote:
                            dispon_index.consume(plan_tejido_lote[lnk_id], lote_id)
                            lnks_consumidos.add(lnk_id)

                det_lote=[d for d in detalle if d["LOTE_ID"]==lote_id]
                bloques=[d["BLOQUE"] for d in det_lote]
                resumen.append({
                    "LOTE_ID":lote_id,"ANCHOS_LOTE":anchos_lote_str,
                    "CATEGORIA":lote["CATEGORIA"],"MIX":lote["MIX"],
                    "TELA.CUERPO":tela,"COLOR/TONO_KEY":tono,
                    "LBS_TOTAL":float(lote["TOTAL_LOTE"]),
                    "MIN_RANGO":float(lote["MINIMO"]),"MAX_RANGO":float(lote["MAXIMO"]),
                    "CAPACIDAD_PERDIDA":float(lote["MAXIMO"]-lote["TOTAL_LOTE"]),
                    "SKU_DISTINTOS":len({d["LNK"] for d in det_lote}),
                    "ANCHOS_UNICOS":len(anchos_lote),
                    "BLOQUE_DOMINANTE":max(set(bloques),key=bloques.count) if bloques else "",
                    "REGLA_DOMINANTE":regla_final,
                    "PRIORIDAD_FINAL":float(lote["MAXIMO"]),
                    "PRIORIDAD_OBJETIVO":prioridad_obj,
                    "QUALITY_LEVEL":quality_level,
                    "BEAM_WIDTH_USADO":beam_w,
                    # Campos de restricción de tejido
                    "LOTE_FACE":lote_face_elegido or "",
                    "DIA_MAX_LOTE": dia_max_lote_num if modo_restriccion else None,
                })
                capacity_used[lote["RANGO_ID"]]+=float(lote["TOTAL_LOTE"])
                lotes_formados+=1
                lbs_procesadas+=float(lote["TOTAL_LOTE"])
                blocked=set(); made_any=True; break

            if not made_any: break

        # ── FIX 3: Rescue pass — mono-SKU lots for stranded LNKs ────────────
        # After main loop, any row still with LBS_RESTANTES > 0 tries a
        # single-SKU lote in ANY available range, ignoring SPLIT_MIN entirely.
        work["LBS_RESTANTES"]=pd.to_numeric(work["LBS_RESTANTES"],errors="coerce").fillna(0.0)
        stranded=work[work["LBS_RESTANTES"]>1e-9].sort_values("LBS_RESTANTES",ascending=False)
        for seed_idx in stranded.index:
            if cancel_flag and cancel_flag[0]: break
            if float(work.at[seed_idx,"LBS_RESTANTES"])<=1e-9: continue
            for r in sorted(ranges_mix, key=lambda x: -float(x["MAXIMO"])):
                if capacity_used[r["RANGO_ID"]]>=r["CAPACIDAD"]-1e-6: continue
                intento,_=intentar_lote_para_rango(
                    work,seed_idx,r,capacity_used,params,
                    {"regla_aplicada":"NONE","prioridades":[],"match_combo":False,
                     "limite_ancho_style":None,"origen_prioridad":"RESCUE","combo_target_width":None},
                    width_cache, require_two_widths=False, split_min_lbs=0.0)
                if intento is not None:
                    lote_id=f"L{lote_id_global:06d}"; lote_id_global+=1
                    anchos_lote=intento["FINAL_WIDTHS"]; anchos_lote_str=str(anchos_lote)
                    for idx,lbs_asig,oe,us in intento["ROWS"]:
                        detalle.append({
                            "LOTE_ID":lote_id,"ANCHOS_LOTE":anchos_lote_str,
                            "CATEGORIA":intento["CATEGORIA"],"MIX":intento["MIX"],
                            "TELA.CUERPO":tela,"COLOR":work.at[idx,"COLOR"],
                            "TONO":work.at[idx,"TONO"] if "TONO" in work.columns else "",
                            "LNK":work.at[idx,"LNK"],"PRIORIDAD":work.at[idx,"PRIORIDAD"],
                            "BLOQUE":work.at[idx,"BLOQUE"],
                            "ANCHO.F.C":float(work.at[idx,"ANCHO.F.C"]),
                            "ANCHO.F.M":float(work.at[idx,"ANCHO.F.M"]),
                            "CONSUMO_C":float(work.at[idx,"CONSUMO_C"]),
                            "FAMILIA":work.at[idx,"FAMILIA"],"COLOR_R":work.at[idx,"COLOR_R"],
                            "STYLE":work.at[idx,"STYLE"],
                            "LBS_ASIGNADAS":float(lbs_asig),
                            "LBS_EXTRA_SOBRE_ORDEN":0.0,
                            "APLICA_REGLA":"RESCUE",
                            "PRIORIDAD_USADA":float(intento["MAXIMO"]),
                            "PRIORIDAD_OBJETIVO":None,
                            "ORIGEN_PRIORIDAD":"RESCUE",
                            "PERMITIR_RANGO_SUPERIOR":0,
                            "SPLIT_MIN_USADO":0.0,
                            "DECISION_SCORE":0.0,
                        })
                        work.at[idx,"LBS_RESTANTES"]=max(0.0,float(work.at[idx,"LBS_RESTANTES"])-float(lbs_asig))
                    det_lote=[d for d in detalle if d["LOTE_ID"]==lote_id]
                    bloques=[d["BLOQUE"] for d in det_lote]
                    resumen.append({
                        "LOTE_ID":lote_id,"ANCHOS_LOTE":anchos_lote_str,
                        "CATEGORIA":intento["CATEGORIA"],"MIX":intento["MIX"],
                        "TELA.CUERPO":tela,"COLOR/TONO_KEY":tono,
                        "LBS_TOTAL":float(intento["TOTAL_LOTE"]),
                        "MIN_RANGO":float(intento["MINIMO"]),"MAX_RANGO":float(intento["MAXIMO"]),
                        "CAPACIDAD_PERDIDA":float(intento["MAXIMO"]-intento["TOTAL_LOTE"]),
                        "SKU_DISTINTOS":len({d["LNK"] for d in det_lote}),
                        "ANCHOS_UNICOS":len(anchos_lote),
                        "BLOQUE_DOMINANTE":max(set(bloques),key=bloques.count) if bloques else "",
                        "REGLA_DOMINANTE":"RESCUE",
                        "PRIORIDAD_FINAL":float(intento["MAXIMO"]),
                        "PRIORIDAD_OBJETIVO":None,
                        "QUALITY_LEVEL":quality_level,
                        "BEAM_WIDTH_USADO":beam_w,
                    })
                    capacity_used[intento["RANGO_ID"]]+=float(intento["TOTAL_LOTE"])
                    lotes_formados+=1; lbs_procesadas+=float(intento["TOTAL_LOTE"])
                    break  # found a range for this seed

        data.loc[work.index,"LBS_RESTANTES"]=work["LBS_RESTANTES"]
        data.loc[work.index,"LBS_SCRAP"]=work["LBS_SCRAP"]

    # Excedentes
    ec=["LNK","TELA.CUERPO","COLOR","MIX","PRIORIDAD","BLOQUE",
        "ANCHO.F.C","ANCHO.F.M","TOTAL","LBS_RESTANTES","LBS_SCRAP"]
    if "TONO" in data.columns: ec.insert(3,"TONO")
    exced_loteados = data[data["LBS_RESTANTES"]>1e-9][[c for c in ec if c in data.columns]].copy()

    # En modo restricción, agregar los LNKs que quedaron excluidos por falta de disponibilidad
    if modo_restriccion and len(data_sin_dispon) > 0:
        data_sin_dispon_ec = data_sin_dispon[[c for c in ec if c in data_sin_dispon.columns]].copy()
        exced = pd.concat([exced_loteados, data_sin_dispon_ec], ignore_index=True)
    else:
        exced = exced_loteados

    df_det=pd.DataFrame(detalle)
    if len(df_det)>0:
        df_det["DOCENAS"]=np.where(df_det["CONSUMO_C"]>0,
                                   df_det["LBS_ASIGNADAS"]/df_det["CONSUMO_C"],np.nan)
    df_res=pd.DataFrame(resumen)

    df_par=pd.DataFrame([
        ["QUALITY_LEVEL",quality_level],["BEAM_WIDTH",beam_w],
        ["MODO_RESTRICCION_TEJIDO", 1 if modo_restriccion else 0],
        ["LOOKAHEAD_VENCIDOS",params.get("LOOKAHEAD_VENCIDOS",1)],
        ["PREFERIR_LOTES_SIMPLES",params.get("PREFERIR_LOTES_SIMPLES",0)],
        ["PENALIZACION_ANCHO_EXTRA",params.get("PENALIZACION_ANCHO_EXTRA",1.5)],
        ["PENALIZACION_LNK_EXTRA",params.get("PENALIZACION_LNK_EXTRA",0.8)],
        ["OVERSHOOT_ENABLE",params.get("OVERSHOOT_ENABLE",1)],
        ["UNDERSHOOT_ENABLE",params.get("UNDERSHOOT_ENABLE",1)],
        ["OVERSHOOT_TOL_PCT_SMALL",params.get("OVERSHOOT_TOL_PCT_SMALL",0.05)],
        ["OVERSHOOT_TOL_PCT_LARGE",params.get("OVERSHOOT_TOL_PCT_LARGE",0.02)],
        ["RULE_ORDER",params.get("RULE_ORDER","")],
        ["PRIORITY_ORDER",params.get("PRIORITY_ORDER","")],
        ["APPLY_RULES_BLEACH",params.get("APPLY_RULES_BLEACH",0)],
        ["OVERSHOOT_SMALL_THRESHOLD",params.get("OVERSHOOT_SMALL_THRESHOLD",5000)],
        ["AGRUPAR_POR_TONO",params.get("AGRUPAR_POR_TONO",1)],
    ],columns=["PARAMETRO","VALOR"])

    # Reportes de tejido (vacíos en modo libre)
    if modo_restriccion:
        df_detalle_tejido, df_stock_report = dispon_index.build_reports()
    else:
        df_detalle_tejido = pd.DataFrame()
        df_stock_report   = pd.DataFrame()

    return df_det, df_res, exced, df_par, cancelled, df_detalle_tejido, df_stock_report


# ── Reports ───────────────────────────────────────────────────────────────────
def build_reports(df_data, df_cap, df_detalle, df_resumen):
    from engine.utils import prioridad_bloque
    df_cap_s=df_cap[["CATEGORIA","MIX","MINIMO","MAXIMO","CAPACIDAD"]].copy()
    df_cat_asig=(df_detalle.groupby(["CATEGORIA","MIX"],as_index=False)["LBS_ASIGNADAS"].sum()
                 if len(df_detalle)>0 else pd.DataFrame({"CATEGORIA":[],"MIX":[],"LBS_ASIGNADAS":[]}))
    df_cap_cap=(df_cap_s.merge(df_cat_asig,on=["CATEGORIA","MIX"],how="left")
                .fillna({"LBS_ASIGNADAS":0.0}))
    df_cap_cap["DIFERENCIA"]=df_cap_cap["LBS_ASIGNADAS"]-df_cap_cap["CAPACIDAD"]
    df_cap_cap["PCT_OCUPACION"]=(df_cap_cap["LBS_ASIGNADAS"]/
                                  df_cap_cap["CAPACIDAD"].replace(0,np.nan)*100).fillna(0).round(1)
    df_cap_cap=df_cap_cap.sort_values(["MIX","CATEGORIA"])

    base=df_data.copy(); base["BLOQUE"]=base["PRIORIDAD"].apply(prioridad_bloque)
    df_prio_base=base.groupby(["MIX","BLOQUE"],as_index=False)["TOTAL"].sum().rename(columns={"TOTAL":"LBS_BASE"})
    df_prio_asig=(df_detalle.groupby(["MIX","BLOQUE"],as_index=False)["LBS_ASIGNADAS"].sum()
                  if len(df_detalle)>0 else pd.DataFrame({"MIX":[],"BLOQUE":[],"LBS_ASIGNADAS":[]}))
    df_pva=(df_prio_base.merge(df_prio_asig,on=["MIX","BLOQUE"],how="left")
            .fillna({"LBS_ASIGNADAS":0.0}))
    df_pva["LBS_SIN_ASIGNAR"]=df_pva["LBS_BASE"]-df_pva["LBS_ASIGNADAS"]
    ob=["VENCIDOS","AHEAD","AHEAD2","OTROS"]
    df_pva["ORD"]=df_pva["BLOQUE"].apply(lambda x: ob.index(x) if x in ob else 99)
    df_pva=df_pva.sort_values(["MIX","ORD"]).drop(columns=["ORD"])

    df_lnk_base=df_data.groupby(["MIX","LNK"],as_index=False)["TOTAL"].sum().rename(columns={"TOTAL":"LBS_BASE"})
    df_lnk_scrap=(df_data.groupby(["MIX","LNK"],as_index=False)["LBS_SCRAP"].sum()
                  if "LBS_SCRAP" in df_data.columns
                  else pd.DataFrame({"MIX":[],"LNK":[],"LBS_SCRAP":[]}))
    df_lnk_asig=(df_detalle.groupby(["MIX","LNK"],as_index=False)["LBS_ASIGNADAS"].sum()
                 if len(df_detalle)>0 else pd.DataFrame({"MIX":[],"LNK":[],"LBS_ASIGNADAS":[]}))
    df_lnk=(df_lnk_base.merge(df_lnk_asig,on=["MIX","LNK"],how="left")
            .merge(df_lnk_scrap,on=["MIX","LNK"],how="left")
            .fillna({"LBS_ASIGNADAS":0.0,"LBS_SCRAP":0.0}))
    df_lnk["BALANCE"]=df_lnk["LBS_BASE"]-df_lnk["LBS_ASIGNADAS"]-df_lnk["LBS_SCRAP"]
    df_lnk["ESTADO"]=np.where(df_lnk["BALANCE"].abs()<=1e-6,
                              np.where(df_lnk["LBS_SCRAP"]>1e-6,"COMPLETO (SCRAP)","COMPLETO"),
                              "INCOMPLETO")
    df_lnk=df_lnk.sort_values(["MIX","ESTADO","BALANCE"],ascending=[True,True,False])

    # Decision log with Spanish descriptions
    if len(df_detalle)>0:
        log_cols=["LOTE_ID","MIX","TELA.CUERPO","COLOR","FAMILIA","STYLE","COLOR_R",
                  "CATEGORIA","PRIORIDAD","BLOQUE","APLICA_REGLA","PRIORIDAD_USADA",
                  "PRIORIDAD_OBJETIVO","LBS_ASIGNADAS","LBS_EXTRA_SOBRE_ORDEN",
                  "ANCHOS_LOTE","DECISION_SCORE","LNK"]
        if "TONO" in df_detalle.columns: log_cols.insert(4,"TONO")
        decision_log=df_detalle[[c for c in log_cols if c in df_detalle.columns]].copy()
        decision_log=decision_log.sort_values(["LOTE_ID","LNK"])
    else:
        decision_log=pd.DataFrame()

    return {
        "CAPACIDAD_X_CATEG":  df_cap_cap,
        "PRIORIDAD_VS_ASIG":  df_pva,
        "LNK_COMPLETITUD":    df_lnk,
        "DECISION_LOG":       decision_log,
    }
