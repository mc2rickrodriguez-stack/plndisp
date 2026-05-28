import pandas as pd
import numpy as np
import re
from engine.utils import (
    norm_str, up, prioridad_bloque, can_mix_blocks,
    valid_width_group, get_row_widths, choose_take,
    choose_take_humano, build_ranges
)


# ─── Priority helpers ────────────────────────────────────────────────────────

def order_priorities(pris, params):
    pris = [float(x) for x in pris if x is not None]
    po_text = norm_str(params.get("PRIORITY_ORDER", ""))
    if po_text:
        plan = [p.strip() for p in po_text.split(">") if p.strip()]
        rank = {}
        for i, v in enumerate(plan):
            if re.match(r"^\d+(\.\d+)?$", v):
                rank[float(v)] = i
        return sorted(pris, key=lambda x: (rank.get(float(x), 10_000), float(x)))
    return sorted(pris)


def order_by_priorities(base_ranges, prioridades):
    used = set()
    out = []
    for cap in prioridades:
        match = [r for r in base_ranges if abs(float(r["MAXIMO"]) - float(cap)) < 1e-6]
        for r in match:
            if id(r) not in used:
                out.append(r)
                used.add(id(r))
    for r in base_ranges:
        if id(r) not in used:
            out.append(r)
    return out


# ─── Rule reordering ─────────────────────────────────────────────────────────

def reorder_ranges_for_seed(ranges_mix, mixv, work, seed_idx, params):
    base = list(ranges_mix)
    rule_info = {
        "regla_aplicada": "NONE",
        "prioridades": [],
        "match_combo": False,
        "limite_ancho_style": None,
        "origen_prioridad": "MIX",
        "combo_target_width": None,
    }
    if up(mixv) not in ("DYE",) and int(params.get("APPLY_RULES_BLEACH", 0)) != 1:
        return base, rule_info

    fam = up(work.at[seed_idx, "FAMILIA"]) if "FAMILIA" in work.columns else ""
    color_r = up(work.at[seed_idx, "COLOR_R"]) if "COLOR_R" in work.columns else ""
    style = up(work.at[seed_idx, "STYLE"]) if "STYLE" in work.columns else ""

    def f2(x):
        try:
            return float(x)
        except:
            return 0.0

    ancho_c = f2(work.at[seed_idx, "ANCHO.F.C"]) if "ANCHO.F.C" in work.columns else 0.0
    ancho_m = f2(work.at[seed_idx, "ANCHO.F.M"]) if "ANCHO.F.M" in work.columns else 0.0

    restr_fam = params.get("RESTRICCIONES_FAMILIA", {})
    restr_color = params.get("RESTRICCIONES_COLOR", {})
    restr_ancho = params.get("RESTRICCIONES_ANCHO", {})
    reglas_combo = params.get("REGLAS_ANCHOS_COMBINADOS", [])
    rule_order_cfg = norm_str(params.get("RULE_ORDER", ""))
    rule_order = [x.strip().upper() for x in rule_order_cfg.split(">") if x.strip()] or \
                 ["ANCHO18", "COMBO_ANCHOS", "COLOR_R", "FAMILIA", "DEFAULT"]

    def ancho_activo_leq_lim(ac, am, lim):
        vals = []
        try:
            if ac is not None and not pd.isna(ac) and float(ac) > 0:
                vals.append(float(ac))
        except:
            pass
        try:
            if am is not None and not pd.isna(am) and float(am) > 0:
                vals.append(float(am))
        except:
            pass
        return (len(vals) > 0) and (min(vals) <= float(lim))

    def try_ancho18():
        if style in restr_ancho:
            lim = restr_ancho[style].get("limite", None)
            prioridades = order_priorities(restr_ancho[style].get("prioridades", []), params)
            if lim is not None and ancho_activo_leq_lim(ancho_c, ancho_m, lim) and len(prioridades) > 0:
                rule_info.update({
                    "regla_aplicada": "ANCHO18",
                    "prioridades": list(prioridades),
                    "limite_ancho_style": lim,
                    "origen_prioridad": "STYLE",
                })
                return order_by_priorities(base, prioridades)

    def try_combo():
        for regla in reglas_combo:
            a1, a2 = regla["a1"], regla["a2"]
            prioridades = order_priorities(regla["prioridades"], params)
            seed_matches = (abs(ancho_c - a1) < 1e-6 or abs(ancho_m - a1) < 1e-6 or
                            abs(ancho_c - a2) < 1e-6 or abs(ancho_m - a2) < 1e-6)
            if not seed_matches:
                continue
            objetivo = a2 if (abs(ancho_c - a1) < 1e-6 or abs(ancho_m - a1) < 1e-6) else a1
            existe_otro = False
            for idx in work.index:
                if idx == seed_idx:
                    continue
                if float(work.at[idx, "LBS_RESTANTES"]) <= 0:
                    continue
                ac = f2(work.at[idx, "ANCHO.F.C"])
                am = f2(work.at[idx, "ANCHO.F.M"])
                if abs(ac - objetivo) < 1e-6 or abs(am - objetivo) < 1e-6:
                    existe_otro = True
                    break
            if existe_otro and len(prioridades) > 0:
                rule_info.update({
                    "regla_aplicada": "COMBO_ANCHOS",
                    "prioridades": list(prioridades),
                    "match_combo": True,
                    "origen_prioridad": "COMBO",
                    "combo_target_width": float(objetivo),
                })
                return order_by_priorities(base, prioridades)

    def try_color_r():
        if color_r in restr_color and restr_color[color_r]:
            p = float(restr_color[color_r])
            rule_info.update({
                "regla_aplicada": "COLOR_R",
                "prioridades": [p],
                "origen_prioridad": "COLOR"
            })
            return order_by_priorities(base, [p])

    def try_familia():
        if fam in restr_fam and len(restr_fam[fam]) > 0:
            prioridades = order_priorities(restr_fam[fam], params)
            rule_info.update({
                "regla_aplicada": "FAMILIA",
                "prioridades": list(prioridades),
                "origen_prioridad": "FAMILIA"
            })
            return order_by_priorities(base, prioridades)

    for token in rule_order:
        out = None
        if token == "ANCHO18":
            out = try_ancho18()
        elif token == "COMBO_ANCHOS":
            out = try_combo()
        elif token == "COLOR_R":
            out = try_color_r()
        elif token == "FAMILIA":
            out = try_familia()
        elif token == "DEFAULT":
            out = base
        if out is not None:
            return out, rule_info
    return base, rule_info


def ranges_matching_priority(pri, ranges_try, tol=1e-6, allow_nearest_higher=True):
    pri = float(pri)
    exact = [r for r in ranges_try if abs(float(r["MAXIMO"]) - pri) <= tol]
    if exact:
        return exact
    if not allow_nearest_higher:
        return []
    higher = [r for r in ranges_try if float(r["MAXIMO"]) >= pri - tol]
    if higher:
        higher = sorted(higher, key=lambda r: (float(r["MAXIMO"]) - pri, -float(r["MAXIMO"])))
        return [higher[0]]
    return []


def score_lote(lote_dict, resumen_rows, params):
    if lote_dict is None:
        return -1e30
    W_FILL = params.get("W_FILL", 5.0)
    W_CAP_LOSS = params.get("W_CAP_LOSS", 3.0)
    W_WIDTH_PREF = params.get("W_WIDTH_PREF", 2.0)
    W_1100_STRICT = params.get("W_1100_WIDTHS_STRICT", 10.0)
    pref_list = params.get("WIDTH_PREF_LIST", [2, 3, 1, 4, 5, 6])

    total = float(lote_dict.get("TOTAL_LOTE", 0.0))
    maximo = float(lote_dict.get("MAXIMO", 1.0))
    fill = total / maximo if maximo > 1e-9 else 0.0
    cap_loss = (maximo - total)

    anchos = set()
    for r in resumen_rows:
        for w in r.get("ANCHOS_ROW", []):
            if w is not None:
                anchos.add(float(w))
    widths_unique = len(anchos)

    try:
        rank = pref_list.index(widths_unique)
    except ValueError:
        rank = len(pref_list) + abs(widths_unique - pref_list[-1])
    width_pref_score = -float(rank)

    score = (W_FILL * fill) + (-W_CAP_LOSS * cap_loss) + (W_WIDTH_PREF * width_pref_score)
    if abs(maximo - 1100.0) < 1e-6:
        score -= W_1100_STRICT * max(0, widths_unique - 1)
    return score


def filter_ranges_for_width_target(ranges_try, mixv, width_target, params):
    mixu = str(mixv).strip().upper()
    allowed = None
    if width_target == 3:
        allowed_map = params.get("ALLOWED_MAXIMO_FOR_3_WIDTHS", {})
        allowed = allowed_map.get(mixu, set())
    elif width_target == 4:
        allowed_map = params.get("ALLOWED_MAXIMO_FOR_4_WIDTHS", {})
        allowed = allowed_map.get(mixu, set())
    if allowed and len(allowed) > 0:
        return [r for r in ranges_try if float(r["MAXIMO"]) in allowed]
    return list(ranges_try)


def intentar_lote_para_rango(work, seed_idx, rango, capacity_used, params, rule_info,
                              require_two_widths=False, split_min_lbs=None,
                              min_unique_widths=None, max_unique_widths=None):
    min_diff = params["MIN_DIFF"]
    max_diff = params["MAX_DIFF"]
    max_widths = params["MAX_WIDTHS"]
    max_sku = params["MAX_SKU"]
    allowed_pairs = params["MIX_ALLOWED"]

    rid = rango["RANGO_ID"]
    cap_total = float(rango["CAPACIDAD"])
    cap_used = float(capacity_used.get(rid, 0.0))
    cap_left_global = max(0.0, cap_total - cap_used)
    if cap_left_global <= 0:
        return None

    max_allowed = min(float(rango["MAXIMO"]), cap_left_global)
    if float(work.at[seed_idx, "LBS_RESTANTES"]) <= 0:
        return None

    try:
        split_min_lbs = float(split_min_lbs if split_min_lbs is not None else params.get("SPLIT_MIN_LBS_DEFAULT", 100.0))
    except:
        split_min_lbs = float(params.get("SPLIT_MIN_LBS_DEFAULT", 100.0))
    allow_scrap_residue = int(params.get("SCRAP_REMAINDER_BELOW_SPLIT_MIN", 1)) == 1

    lote_rows = []
    lote_lbs = 0.0
    lote_lnks = set()
    lote_blocks = []
    lote_widths = []

    def can_add_row(idx, lbs_to_add):
        if lbs_to_add <= 0:
            return False
        if "TONO" in work.columns:
            seed_tono = up(work.at[seed_idx, "TONO"]) if not pd.isna(work.at[seed_idx, "TONO"]) else ""
            row_tono = up(work.at[idx, "TONO"]) if not pd.isna(work.at[idx, "TONO"]) else ""
            if seed_tono != row_tono:
                return False
        lnk = work.at[idx, "LNK"]
        new_lnks = set(lote_lnks)
        new_lnks.add(lnk)
        if len(new_lnks) > max_sku:
            return False
        b = work.at[idx, "BLOQUE"]
        for existing_b in lote_blocks:
            if not can_mix_blocks(existing_b, b, allowed_pairs):
                return False
        widths_candidate = list(lote_widths) + get_row_widths(work, idx)
        if not valid_width_group(widths_candidate, min_diff, max_diff, max_widths):
            return False
        if max_unique_widths is not None:
            uwc = sorted(set([float(w) for w in widths_candidate if w is not None and not pd.isna(w) and float(w) != 0.0]))
            if len(uwc) > int(max_unique_widths):
                return False
        if lote_lbs + lbs_to_add > max_allowed + 1e-9:
            return False
        return True

    seed_rest = float(work.at[seed_idx, "LBS_RESTANTES"])
    remaining = max_allowed - lote_lbs
    block_name = work.at[seed_idx, "BLOQUE"]
    use_human = int(params.get("OVERSHOOT_ENABLE", 0)) == 1

    if use_human:
        take, over_extra, under_saved = choose_take_humano(seed_rest, remaining, work.loc[seed_idx], params, block_name)
    else:
        take = choose_take(seed_rest, remaining, split_min_lbs, allow_scrap_residue=allow_scrap_residue)
        over_extra = 0.0
        under_saved = 0.0

    if take <= 0 or not can_add_row(seed_idx, take):
        return None

    lote_rows.append((seed_idx, take, over_extra, under_saved))
    lote_lbs += take
    lote_lnks.add(work.at[seed_idx, "LNK"])
    lote_blocks.append(work.at[seed_idx, "BLOQUE"])
    lote_widths += get_row_widths(work, seed_idx)

    combo_target = rule_info.get("combo_target_width", None) if rule_info else None

    while True:
        remaining = max_allowed - lote_lbs
        if remaining <= 1e-6:
            break
        best = None
        best_take = 0.0
        best_score = -1e30

        for idx in work.index:
            rest = float(work.at[idx, "LBS_RESTANTES"])
            if rest <= 0:
                continue
            if any(i == idx for i, *_ in lote_rows):
                continue
            take = choose_take(rest, remaining, split_min_lbs, allow_scrap_residue=allow_scrap_residue)
            if take <= 0:
                continue
            if not can_add_row(idx, take):
                continue

            new_total = lote_lbs + take
            widths_now = set([float(w) for w in lote_widths if w is not None and not pd.isna(w) and float(w) != 0.0])
            widths_add = set([float(w) for w in get_row_widths(work, idx) if w is not None and not pd.isna(w) and float(w) != 0.0])
            new_widths = widths_now.union(widths_add)
            adds_new_width = 1 if len(new_widths) > len(widths_now) else 0

            has_target = 0
            if combo_target is not None:
                for w in widths_add:
                    if abs(float(w) - float(combo_target)) < 1e-6:
                        has_target = 1
                        break

            score = new_total + has_target * 1e-3 + adds_new_width * 1e-4
            if score > best_score:
                best_score = score
                best = idx
                best_take = take

        if best is None:
            break

        lote_rows.append((best, best_take, 0.0, 0.0))
        lote_lbs += best_take
        lote_lnks.add(work.at[best, "LNK"])
        lote_blocks.append(work.at[best, "BLOQUE"])
        lote_widths += get_row_widths(work, best)

    if lote_lbs + 1e-9 < float(rango["MINIMO"]):
        return None

    if min_unique_widths is not None:
        min_required = int(min_unique_widths)
    elif require_two_widths:
        min_required = 2
    else:
        min_required = None

    if min_required is not None:
        uw = sorted(set([float(w) for w in lote_widths if w is not None and not pd.isna(w) and float(w) != 0.0]))
        if len(uw) < int(min_required):
            return None

    if max_unique_widths is not None:
        uw = sorted(set([float(w) for w in lote_widths if w is not None and not pd.isna(w) and float(w) != 0.0]))
        if len(uw) > int(max_unique_widths):
            return None

    return {
        "RANGO_ID": rango["RANGO_ID"],
        "CATEGORIA": rango["CATEGORIA"],
        "MIX": rango["MIX"],
        "MINIMO": float(rango["MINIMO"]),
        "MAXIMO": float(rango["MAXIMO"]),
        "TOTAL_LOTE": float(lote_lbs),
        "ROWS": lote_rows,
        "REQUIERE_2_ANCHOS": bool(require_two_widths),
    }


# ─── Main loteo ──────────────────────────────────────────────────────────────

def run_loteo(df_data, df_cap, params, progress_callback=None):
    ranges = build_ranges(df_cap)
    capacity_used = {r["RANGO_ID"]: 0.0 for r in ranges}

    data = df_data.copy()
    data["BLOQUE"] = data["PRIORIDAD"].apply(prioridad_bloque)
    data["LBS_RESTANTES"] = data["TOTAL"].astype(float)
    data["LBS_SCRAP"] = 0.0

    detalle = []
    resumen = []
    lote_id_global = 1
    block_order = ["VENCIDOS", "AHEAD", "AHEAD2", "OTROS"]

    group_keys = ["TELA.CUERPO", "MIX"]
    if "TONO" in data.columns:
        group_keys.insert(1, "TONO")
    else:
        group_keys.insert(1, "COLOR")

    groups = list(data.groupby(group_keys).groups.items())
    total_groups = len(groups)

    for g_num, (keys, grp_idx) in enumerate(groups):
        if progress_callback:
            progress_callback(g_num / total_groups, f"Procesando grupo {g_num + 1}/{total_groups}…")

        work = data.loc[grp_idx].copy()
        if "TONO" in data.columns:
            tela, tono, mixv = keys[0], keys[1], keys[2]
        else:
            tela, color, mixv = keys[0], keys[1], keys[2]
            tono = color

        ranges_mix = [r for r in ranges if r["MIX"] == mixv]
        blocked = set()

        while True:
            work["LBS_RESTANTES"] = pd.to_numeric(work["LBS_RESTANTES"], errors="coerce").fillna(0.0)
            if (work["LBS_RESTANTES"] > 0).sum() == 0:
                break
            made_any = False

            for b in block_order:
                if b in blocked:
                    continue
                cand = work[(work["BLOQUE"] == b) & (work["LBS_RESTANTES"] > 0)]
                if len(cand) == 0:
                    blocked.add(b)
                    continue

                beam_w = int(params.get("BEAM_WIDTH", 3))
                top_seeds = cand.sort_values("LBS_RESTANTES", ascending=False).head(beam_w).index.tolist()

                best_lote = None
                best_pack = None
                best_score = -1e30

                for seed_idx in top_seeds:
                    ranges_try, rule_info = reorder_ranges_for_seed(ranges_mix, mixv, work, seed_idx, params)

                    if rule_info.get("regla_aplicada") == "ANCHO18" and up(mixv) == "DYE":
                        allowed = set(params.get("ANCHO18_ALLOWED_MAX_DYE", {2200.0, 1100.0}))
                        if int(params.get("ANCHO18_ALLOW_SPILLOVER_2600", 0)) == 1:
                            allowed.add(2600.0)
                        ranges_try = [r for r in ranges_try if float(r["MAXIMO"]) in allowed]

                    lote = None
                    prioridad_obj = None

                    order_text = norm_str(params.get("WIDTHS_TARGET_ORDER", "2>3>4"))
                    targets = [int(x) for x in order_text.split(">") if x.strip().isdigit()]
                    req_strict = int(params.get("REQUIRE_WIDTHS_STRICT", 1)) == 1

                    pri_list = order_priorities(rule_info.get("prioridades", []), params)
                    use_upgrades = (len(pri_list) > 0 and int(params.get("UPGRADE_CATEGORIA", 0)) == 1)
                    pri_iter = pri_list if (use_upgrades and int(params.get("TRY_ALL_PRIORITIES", 1)) == 1) else [None]

                    for target in targets:
                        candidate_ranges_all = filter_ranges_for_width_target(ranges_try, mixv, target, params)
                        found = False
                        for pri in pri_iter:
                            candidate_ranges = candidate_ranges_all
                            if pri is not None:
                                candidate_ranges = ranges_matching_priority(pri, candidate_ranges_all, allow_nearest_higher=True)
                            for r in candidate_ranges:
                                if capacity_used[r["RANGO_ID"]] >= r["CAPACIDAD"] - 1e-6:
                                    continue
                                split_min = params.get("SPLIT_MIN_LBS_ANCHO18", 250) if rule_info.get("regla_aplicada") == "ANCHO18" else float(params.get("SPLIT_MIN_LBS_DEFAULT", 100.0))
                                intento = intentar_lote_para_rango(
                                    work, seed_idx, r, capacity_used, params, rule_info,
                                    require_two_widths=(rule_info.get("regla_aplicada") == "COMBO_ANCHOS"),
                                    split_min_lbs=split_min,
                                    min_unique_widths=target,
                                    max_unique_widths=(target if req_strict else None)
                                )
                                if intento is not None:
                                    lote = intento
                                    prioridad_obj = float(pri) if pri is not None else None
                                    found = True
                                    break
                            if found:
                                break
                        if lote is not None:
                            break

                    if lote is None:
                        if use_upgrades:
                            for pri in pri_iter:
                                if pri is None:
                                    continue
                                candidate_ranges = ranges_matching_priority(pri, ranges_try, allow_nearest_higher=True)
                                for r in candidate_ranges:
                                    if capacity_used[r["RANGO_ID"]] >= r["CAPACIDAD"] - 1e-6:
                                        continue
                                    split_min = params.get("SPLIT_MIN_LBS_ANCHO18", 250) if rule_info.get("regla_aplicada") == "ANCHO18" else float(params.get("SPLIT_MIN_LBS_DEFAULT", 100.0))
                                    if rule_info.get("regla_aplicada") == "COMBO_ANCHOS":
                                        intento = intentar_lote_para_rango(work, seed_idx, r, capacity_used, params, rule_info, require_two_widths=True, split_min_lbs=split_min)
                                        if intento is None:
                                            intento = intentar_lote_para_rango(work, seed_idx, r, capacity_used, params, rule_info, require_two_widths=False, split_min_lbs=split_min)
                                    else:
                                        intento = intentar_lote_para_rango(work, seed_idx, r, capacity_used, params, rule_info, require_two_widths=False, split_min_lbs=split_min)
                                    if intento is not None:
                                        lote = intento
                                        prioridad_obj = float(pri)
                                        break
                                if lote is not None:
                                    break

                        if lote is None:
                            for r in ranges_try:
                                if capacity_used[r["RANGO_ID"]] >= r["CAPACIDAD"] - 1e-6:
                                    continue
                                split_min = params.get("SPLIT_MIN_LBS_ANCHO18", 250) if rule_info.get("regla_aplicada") == "ANCHO18" else float(params.get("SPLIT_MIN_LBS_DEFAULT", 100.0))
                                if rule_info.get("regla_aplicada") == "COMBO_ANCHOS":
                                    intento = intentar_lote_para_rango(work, seed_idx, r, capacity_used, params, rule_info, require_two_widths=True, split_min_lbs=split_min)
                                    if intento is None:
                                        intento = intentar_lote_para_rango(work, seed_idx, r, capacity_used, params, rule_info, require_two_widths=False, split_min_lbs=split_min)
                                else:
                                    intento = intentar_lote_para_rango(work, seed_idx, r, capacity_used, params, rule_info, require_two_widths=False, split_min_lbs=split_min)
                                if intento is not None:
                                    lote = intento
                                    break

                    if lote is not None:
                        resumen_rows_sc = []
                        for idx, lbs_asig, over_extra, under_saved in lote["ROWS"]:
                            resumen_rows_sc.append({"LNK": work.at[idx, "LNK"], "ANCHOS_ROW": get_row_widths(work, idx)})
                        lote_for_score = {"MAXIMO": float(lote["MAXIMO"]), "TOTAL_LOTE": float(lote["TOTAL_LOTE"])}
                        sc = score_lote(lote_for_score, resumen_rows_sc, params)
                        if sc > best_score:
                            best_score = sc
                            best_lote = lote
                            best_pack = (lote, rule_info, prioridad_obj, best_score)

                if best_lote is None:
                    blocked.add(b)
                    continue

                lote, rule_info, prioridad_obj, best_score = best_pack
                split_min = params.get("SPLIT_MIN_LBS_ANCHO18", 250) if rule_info.get("regla_aplicada") == "ANCHO18" else float(params.get("SPLIT_MIN_LBS_DEFAULT", 100.0))

                lote_id = f"L{lote_id_global:06d}"
                lote_id_global += 1

                lote_widths_list = []
                for idx, _lbs, *_ in lote["ROWS"]:
                    lote_widths_list += get_row_widths(work, idx)
                anchos_lote = sorted(set([float(w) for w in lote_widths_list if w is not None and not pd.isna(w) and float(w) != 0.0]))
                anchos_lote_str = str(anchos_lote)

                prioridad_final = float(lote["MAXIMO"])
                regla_aplicada_final = rule_info.get("regla_aplicada", "NONE")
                requiere_2_anchos_flag = False
                if regla_aplicada_final == "COMBO_ANCHOS":
                    requiere_2_anchos_flag = bool(lote.get("REQUIERE_2_ANCHOS", False)) and (len(anchos_lote) >= 2)
                    if not requiere_2_anchos_flag:
                        regla_aplicada_final = "COMBO_ANCHOS_FALLBACK"

                for idx, lbs_asig, over_extra, under_saved in lote["ROWS"]:
                    detalle.append({
                        "LOTE_ID": lote_id,
                        "ANCHOS_LOTE": anchos_lote_str,
                        "CATEGORIA": lote["CATEGORIA"],
                        "MIX": lote["MIX"],
                        "TELA.CUERPO": tela,
                        "COLOR": work.at[idx, "COLOR"],
                        "TONO": work.at[idx, "TONO"] if "TONO" in work.columns else "",
                        "LNK_PRIORIDAD": f"{work.at[idx, 'LNK']}|{work.at[idx, 'PRIORIDAD']}",
                        "LNK": work.at[idx, "LNK"],
                        "PRIORIDAD": work.at[idx, "PRIORIDAD"],
                        "BLOQUE": work.at[idx, "BLOQUE"],
                        "ANCHO.F.C": float(work.at[idx, "ANCHO.F.C"]),
                        "ANCHO.F.M": float(work.at[idx, "ANCHO.F.M"]),
                        "CONSUMO_C": float(work.at[idx, "CONSUMO_C"]),
                        "FAMILIA": work.at[idx, "FAMILIA"],
                        "COLOR_R": work.at[idx, "COLOR_R"],
                        "STYLE": work.at[idx, "STYLE"],
                        "LBS_ASIGNADAS": float(lbs_asig),
                        "LBS_EXTRA_SOBRE_ORDEN": float(max(0.0, over_extra)),
                        "APLICA_REGLA": regla_aplicada_final,
                        "PRIORIDAD_USADA": prioridad_final,
                        "PRIORIDAD_OBJETIVO": prioridad_obj,
                        "ORIGEN_PRIORIDAD": rule_info.get("origen_prioridad", "MIX"),
                        "MATCH_ANCHO": bool(rule_info.get("match_combo", False)),
                        "LIMITE_ANCHO_STYLE": rule_info.get("limite_ancho_style", None),
                        "UPGRADE_CATEGORIA": int(params.get("UPGRADE_CATEGORIA", 0)),
                        "SPLIT_MIN_USADO": float(params.get("SPLIT_MIN_LBS_ANCHO18", 250)) if rule_info.get("regla_aplicada") == "ANCHO18" else float(params.get("SPLIT_MIN_LBS_DEFAULT", 100.0)),
                        "REQUIERE_2_ANCHOS": bool(requiere_2_anchos_flag),
                        "DECISION_SCORE": float(best_score)
                    })

                    prev_rest = float(work.at[idx, "LBS_RESTANTES"])
                    work.at[idx, "LBS_RESTANTES"] = max(0.0, prev_rest - float(lbs_asig))

                    if int(params.get("SCRAP_REMAINDER_BELOW_SPLIT_MIN", 1)) == 1:
                        rem = float(work.at[idx, "LBS_RESTANTES"])
                        if rem > 1e-9 and rem + 1e-9 < float(split_min):
                            work.at[idx, "LBS_SCRAP"] = float(work.at[idx, "LBS_SCRAP"]) + rem
                            work.at[idx, "LBS_RESTANTES"] = 0.0

                det_lote = [d for d in detalle if d["LOTE_ID"] == lote_id]
                bloques = [d["BLOQUE"] for d in det_lote]
                bloque_dom = max(set(bloques), key=bloques.count) if bloques else ""

                resumen.append({
                    "LOTE_ID": lote_id,
                    "ANCHOS_LOTE": anchos_lote_str,
                    "CATEGORIA": lote["CATEGORIA"],
                    "MIX": lote["MIX"],
                    "TELA.CUERPO": tela,
                    "COLOR/TONO_KEY": tono,
                    "LBS_TOTAL": float(lote["TOTAL_LOTE"]),
                    "MIN_RANGO": float(lote["MINIMO"]),
                    "MAX_RANGO": float(lote["MAXIMO"]),
                    "CAPACIDAD_PERDIDA": float(lote["MAXIMO"] - lote["TOTAL_LOTE"]),
                    "SKU_DISTINTOS": len({d["LNK"] for d in det_lote}),
                    "ANCHOS_UNICOS": len(anchos_lote),
                    "BLOQUE_DOMINANTE": bloque_dom,
                    "REGLA_DOMINANTE": regla_aplicada_final,
                    "PRIORIDAD_FINAL": prioridad_final,
                    "PRIORIDAD_OBJETIVO": prioridad_obj,
                    "COMBO_ANCHOS": (regla_aplicada_final == "COMBO_ANCHOS"),
                    "STYLE_CRITICO": rule_info.get("regla_aplicada") == "ANCHO18",
                    "CANT_REGLAS_APLICADAS": 0 if rule_info.get("regla_aplicada") == "NONE" else 1,
                    "UPGRADE_CATEGORIA": int(params.get("UPGRADE_CATEGORIA", 0))
                })

                capacity_used[lote["RANGO_ID"]] += float(lote["TOTAL_LOTE"])
                blocked = set()
                made_any = True
                break

            if not made_any:
                break

        data.loc[work.index, "LBS_RESTANTES"] = work["LBS_RESTANTES"]
        data.loc[work.index, "LBS_SCRAP"] = work["LBS_SCRAP"]

    exced_cols = ["LNK", "TELA.CUERPO", "COLOR", "MIX", "PRIORIDAD", "BLOQUE", "ANCHO.F.C", "ANCHO.F.M", "TOTAL", "LBS_RESTANTES", "LBS_SCRAP"]
    if "TONO" in data.columns:
        exced_cols.insert(3, "TONO")
    exced = data[data["LBS_RESTANTES"] > 1e-9].copy()
    exced = exced[[c for c in exced_cols if c in exced.columns]]

    df_detalle = pd.DataFrame(detalle)
    if len(df_detalle) > 0:
        df_detalle["DOCENAS"] = np.where(df_detalle["CONSUMO_C"] > 0, df_detalle["LBS_ASIGNADAS"] / df_detalle["CONSUMO_C"], np.nan)
    df_resumen = pd.DataFrame(resumen)

    df_param_out = pd.DataFrame([
        ["MIN_DIFF", params["MIN_DIFF"]],
        ["MAX_DIFF", params["MAX_DIFF"]],
        ["MAX_WIDTHS", params["MAX_WIDTHS"]],
        ["MAX_SKU", params["MAX_SKU"]],
        ["SPLIT_MIN_LBS_DEFAULT", params.get("SPLIT_MIN_LBS_DEFAULT", 100.0)],
        ["SPLIT_MIN_LBS_ANCHO18", params.get("SPLIT_MIN_LBS_ANCHO18", 250)],
        ["RULE_ORDER", params.get("RULE_ORDER", "")],
        ["PRIORITY_ORDER", params.get("PRIORITY_ORDER", "")],
        ["APPLY_RULES_BLEACH", params.get("APPLY_RULES_BLEACH", 0)],
        ["TRY_ALL_PRIORITIES", params.get("TRY_ALL_PRIORITIES", 1)],
        ["UPGRADE_CATEGORIA", params.get("UPGRADE_CATEGORIA", 0)],
        ["ANCHO18_ALLOW_SPILLOVER_2600", params.get("ANCHO18_ALLOW_SPILLOVER_2600", 0)],
        ["ANCHO18_ALLOWED_MAX_DYE", ",".join(sorted(str(int(x)) for x in params.get("ANCHO18_ALLOWED_MAX_DYE", {2200.0, 1100.0})))],
        ["SCRAP_REMAINDER_BELOW_SPLIT_MIN", params.get("SCRAP_REMAINDER_BELOW_SPLIT_MIN", 1)],
        ["OVERSHOOT_ENABLE", params.get("OVERSHOOT_ENABLE", 1)],
        ["UNDERSHOOT_ENABLE", params.get("UNDERSHOOT_ENABLE", 1)],
        ["BEAM_WIDTH", params.get("BEAM_WIDTH", 3)],
        ["W_FILL", params.get("W_FILL", 5.0)],
        ["W_CAP_LOSS", params.get("W_CAP_LOSS", 3.0)],
        ["WIDTH_PREF_LIST", ",".join(str(x) for x in params.get("WIDTH_PREF_LIST", [2, 3, 1, 4, 5, 6]))],
        ["W_WIDTH_PREF", params.get("W_WIDTH_PREF", 2.0)],
        ["W_1100_WIDTHS_STRICT", params.get("W_1100_WIDTHS_STRICT", 10.0)],
        ["WIDTHS_TARGET_ORDER", params.get("WIDTHS_TARGET_ORDER", "2>3>4")],
        ["REQUIRE_WIDTHS_STRICT", params.get("REQUIRE_WIDTHS_STRICT", 1)],
        ["ALLOWED_MAXIMO_FOR_3_WIDTHS_DYE", ",".join(str(int(x)) for x in params.get("ALLOWED_MAXIMO_FOR_3_WIDTHS", {}).get("DYE", set()))],
        ["ALLOWED_MAXIMO_FOR_4_WIDTHS_DYE", ",".join(str(int(x)) for x in params.get("ALLOWED_MAXIMO_FOR_4_WIDTHS", {}).get("DYE", set()))],
        ["ALLOWED_MAXIMO_FOR_3_WIDTHS_BLEACH", ",".join(str(int(x)) for x in params.get("ALLOWED_MAXIMO_FOR_3_WIDTHS", {}).get("BLEACH", set()))],
        ["ALLOWED_MAXIMO_FOR_4_WIDTHS_BLEACH", ",".join(str(int(x)) for x in params.get("ALLOWED_MAXIMO_FOR_4_WIDTHS", {}).get("BLEACH", set()))],
    ], columns=["PARAMETRO", "VALOR"])

    return df_detalle, df_resumen, exced, df_param_out


# ─── Reports ─────────────────────────────────────────────────────────────────

def build_reports(df_data, df_cap, df_detalle, df_resumen):
    df_cap_simple = df_cap[["CATEGORIA", "MIX", "MINIMO", "MAXIMO", "CAPACIDAD"]].copy()
    if len(df_detalle) > 0:
        df_cat_asig = df_detalle.groupby(["CATEGORIA", "MIX"], as_index=False)["LBS_ASIGNADAS"].sum()
    else:
        df_cat_asig = pd.DataFrame({"CATEGORIA": [], "MIX": [], "LBS_ASIGNADAS": []})

    df_cap_cap = (df_cap_simple.merge(df_cat_asig, on=["CATEGORIA", "MIX"], how="left").fillna({"LBS_ASIGNADAS": 0.0}))
    df_cap_cap["DIFERENCIA"] = df_cap_cap["LBS_ASIGNADAS"] - df_cap_cap["CAPACIDAD"]
    df_cap_cap = df_cap_cap.sort_values(["MIX", "CATEGORIA"])

    df_base_blocks = df_data.copy()
    df_base_blocks["BLOQUE"] = df_base_blocks["PRIORIDAD"].apply(prioridad_bloque)
    df_prio_base = (df_base_blocks.groupby(["MIX", "BLOQUE"], as_index=False)["TOTAL"].sum().rename(columns={"TOTAL": "LBS_BASE"}))

    if len(df_detalle) > 0:
        df_prio_asig = df_detalle.groupby(["MIX", "BLOQUE"], as_index=False)["LBS_ASIGNADAS"].sum()
    else:
        df_prio_asig = pd.DataFrame({"MIX": [], "BLOQUE": [], "LBS_ASIGNADAS": []})

    df_prio_vs_asig = (df_prio_base.merge(df_prio_asig, on=["MIX", "BLOQUE"], how="left").fillna({"LBS_ASIGNADAS": 0.0}))
    df_prio_vs_asig["LBS_SIN_ASIGNAR"] = df_prio_vs_asig["LBS_BASE"] - df_prio_vs_asig["LBS_ASIGNADAS"]
    order_blocks = ["VENCIDOS", "AHEAD", "AHEAD2", "OTROS"]
    df_prio_vs_asig["ORD"] = df_prio_vs_asig["BLOQUE"].apply(lambda x: order_blocks.index(x) if x in order_blocks else 999)
    df_prio_vs_asig = df_prio_vs_asig.sort_values(["MIX", "ORD"]).drop(columns=["ORD"])

    df_lnk_base = (df_data.groupby(["MIX", "LNK"], as_index=False)["TOTAL"].sum().rename(columns={"TOTAL": "LBS_BASE"}))
    if "LBS_SCRAP" in df_data.columns:
        df_lnk_scrap = df_data.groupby(["MIX", "LNK"], as_index=False)["LBS_SCRAP"].sum()
    else:
        df_lnk_scrap = pd.DataFrame({"MIX": [], "LNK": [], "LBS_SCRAP": []})

    if len(df_detalle) > 0:
        df_lnk_asig = df_detalle.groupby(["MIX", "LNK"], as_index=False)["LBS_ASIGNADAS"].sum()
    else:
        df_lnk_asig = pd.DataFrame({"MIX": [], "LNK": [], "LBS_ASIGNADAS": []})

    df_lnk_comp = (df_lnk_base.merge(df_lnk_asig, on=["MIX", "LNK"], how="left").merge(df_lnk_scrap, on=["MIX", "LNK"], how="left").fillna({"LBS_ASIGNADAS": 0.0, "LBS_SCRAP": 0.0}))
    df_lnk_comp["BALANCE"] = df_lnk_comp["LBS_BASE"] - df_lnk_comp["LBS_ASIGNADAS"] - df_lnk_comp["LBS_SCRAP"]
    df_lnk_comp["ESTADO"] = np.where(df_lnk_comp["BALANCE"].abs() <= 1e-6,
                                     np.where(df_lnk_comp["LBS_SCRAP"] > 1e-6, "COMPLETO (SCRAP)", "COMPLETO"),
                                     "INCOMPLETO")
    df_lnk_comp = df_lnk_comp.sort_values(["MIX", "ESTADO", "BALANCE"], ascending=[True, True, False])

    def resumen_por_lote(df_det, filtro):
        if len(df_det) == 0:
            return pd.DataFrame()
        sub = df_det.query(filtro).copy()
        if len(sub) == 0:
            return pd.DataFrame()
        agg_dict = {
            "ANCHOS_LOTE": "first", "MIX": "first", "TELA.CUERPO": "first",
            "COLOR": "first", "FAMILIA": "first", "STYLE": "first",
            "COLOR_R": "first", "PRIORIDAD_USADA": "first",
            "PRIORIDAD_OBJETIVO": "first", "UPGRADE_CATEGORIA": "first",
            "LBS_ASIGNADAS": "sum"
        }
        if "TONO" in df_det.columns:
            agg_dict["TONO"] = "first"
        return sub.groupby("LOTE_ID", as_index=False).agg(agg_dict)

    rep_ancho18 = resumen_por_lote(df_detalle, "APLICA_REGLA == 'ANCHO18'")
    rep_combo = resumen_por_lote(df_detalle, "APLICA_REGLA == 'COMBO_ANCHOS'")
    rep_color = resumen_por_lote(df_detalle, "APLICA_REGLA == 'COLOR_R'")
    rep_fam = resumen_por_lote(df_detalle, "APLICA_REGLA == 'FAMILIA'")

    if len(df_resumen) > 0:
        rep_cols = ["LOTE_ID", "ANCHOS_LOTE", "MIX", "REGLA_DOMINANTE", "PRIORIDAD_FINAL", "PRIORIDAD_OBJETIVO",
                    "COMBO_ANCHOS", "STYLE_CRITICO", "CANT_REGLAS_APLICADAS", "ANCHOS_UNICOS", "LBS_TOTAL",
                    "CAPACIDAD_PERDIDA", "UPGRADE_CATEGORIA"]
        rep_maestro = df_resumen[[c for c in rep_cols if c in df_resumen.columns]].copy()
    else:
        rep_maestro = pd.DataFrame()

    if len(df_detalle) > 0:
        overs = df_detalle.groupby(["MIX", "LNK"], as_index=False).agg({"LBS_EXTRA_SOBRE_ORDEN": "sum", "LBS_ASIGNADAS": "sum"})
        log_cols = ["LOTE_ID", "MIX", "TELA.CUERPO", "COLOR", "FAMILIA", "STYLE", "COLOR_R",
                    "CATEGORIA", "PRIORIDAD", "BLOQUE", "APLICA_REGLA", "PRIORIDAD_USADA",
                    "PRIORIDAD_OBJETIVO", "LBS_ASIGNADAS", "LBS_EXTRA_SOBRE_ORDEN",
                    "ANCHOS_LOTE", "DECISION_SCORE", "LNK"]
        if "TONO" in df_detalle.columns:
            log_cols.insert(4, "TONO")
        decision_log = df_detalle[[c for c in log_cols if c in df_detalle.columns]].copy().sort_values(["LOTE_ID", "LNK"])
    else:
        overs = pd.DataFrame({"MIX": [], "LNK": [], "LBS_EXTRA_SOBRE_ORDEN": [], "LBS_ASIGNADAS": []})
        decision_log = pd.DataFrame()

    return {
        "CAPACIDAD_X_CATEG": df_cap_cap,
        "PRIORIDAD_VS_ASIG": df_prio_vs_asig,
        "LNK_COMPLETITUD": df_lnk_comp,
        "REGLA_STYLE_ANCHO18": rep_ancho18,
        "REGLA_COMBINACION_ANCHOS": rep_combo,
        "REGLA_COLOR_R": rep_color,
        "REGLA_FAMILIA": rep_fam,
        "REPORTE_REGLAS_MIX": rep_maestro,
        "OVERSHOOT_SUMMARY": overs,
        "DECISION_LOG": decision_log,
    }
