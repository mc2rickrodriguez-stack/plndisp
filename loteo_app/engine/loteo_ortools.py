"""
Motor OR-Tools CP-SAT v1
Reemplaza el loop greedy de loteo.py con optimización exacta por grupo.

Estrategia:
- Por cada grupo (TELA+TONO+MIX), se construye un modelo CP-SAT.
- Variables: x[i,j] = fracción de LNK i asignada al lote j (continua escalada a enteros).
- Restricciones: capacidad por lote, compatibilidad de anchos, prioridades, SKU máx.
- Objetivo: maximizar LBS asignadas + penalizar capacidad perdida.
- Fallback: si el grupo es demasiado grande (>MAX_ITEMS_ORTOOLS), usa el greedy original.
"""
import pandas as pd
import numpy as np
import math
import time
from ortools.sat.python import cp_model

from engine.utils import (
    norm_str, up, prioridad_bloque, can_mix_blocks,
    valid_width_group, get_row_widths, build_ranges, rango_param,
)
from engine.loteo import (
    reorder_ranges_for_seed, quality_to_beam,
    DESCARTE_MSGS, score_lote, get_allowed_ranges,
)

# Groups larger than this fall back to greedy
MAX_ITEMS_ORTOOLS = 200
# OR-Tools time limit per group (seconds)
TIME_LIMIT_PER_GROUP = 30.0
# Scale factor: convert LBS floats to integers for CP-SAT (1 unit = 0.1 LBS)
LBS_SCALE = 10


def _widths_compatible(ws1, ws2, min_diff, max_diff, max_widths):
    """Check if merging two width lists stays valid."""
    combined = ws1 + ws2
    return valid_width_group(combined, min_diff, max_diff, max_widths)


def _build_width_cache(work):
    wc = {}
    for idx in work.index:
        ws = []
        for c in ['ANCHO.F.C', 'ANCHO.F.M']:
            if c in work.columns:
                v = work.at[idx, c]
                if pd.notna(v) and float(v) != 0.0:
                    ws.append(float(v))
        wc[idx] = ws
    return wc


def _precompute_compat_matrix(items, width_cache, min_diff, max_diff, max_widths):
    """
    For each pair (i,j), can they be in the same lot?
    Returns dict (i,j)->bool (symmetric).
    """
    n = len(items)
    compat = {}
    for a in range(n):
        for b in range(a, n):
            ia, ib = items[a], items[b]
            ws = width_cache.get(ia, []) + width_cache.get(ib, [])
            ok = valid_width_group(ws, min_diff, max_diff, max_widths)
            compat[(a, b)] = ok
            compat[(b, a)] = ok
    return compat


def solve_group_ortools(work, ranges_mix, params, width_cache,
                         lote_id_start, capacity_used,
                         rule_info_by_seed, cancel_flag=None,
                         time_limit=TIME_LIMIT_PER_GROUP):
    """
    Solve one TELA+TONO+MIX group with OR-Tools CP-SAT.
    Returns (detalle_rows, resumen_rows, updated_capacity_used, next_lote_id).
    """
    work = work.copy()
    work['LBS_RESTANTES'] = pd.to_numeric(work['LBS_RESTANTES'], errors='coerce').fillna(0.0)
    items = [idx for idx in work.index if work.at[idx, 'LBS_RESTANTES'] > 0]
    if not items:
        return [], [], capacity_used, lote_id_start

    allowed_pairs = params.get('MIX_ALLOWED', set())
    max_sku = int(params.get('MAX_SKU', 8))

    # Per-group params (use first available rango as reference for defaults)
    ref_rango = ranges_mix[0] if ranges_mix else {}
    min_diff  = float(rango_param(ref_rango, 'MIN_DIFF',  params, 0.0))
    max_diff  = float(rango_param(ref_rango, 'MAX_DIFF',  params, 999.0))
    max_widths= int(  rango_param(ref_rango, 'MAX_WIDTHS',params, 6))

    n = len(items)
    # Estimate max lotes needed (total LBS / smallest rango min)
    total_lbs = sum(float(work.at[i, 'LBS_RESTANTES']) for i in items)
    min_rango  = min((float(r['MINIMO']) for r in ranges_mix), default=100.0)
    max_lotes  = min(int(math.ceil(total_lbs / max(min_rango, 1))) + 5, n)

    # Scale LBS to integers
    def s(v): return max(1, int(round(float(v) * LBS_SCALE)))

    lbs_i     = [s(work.at[i, 'LBS_RESTANTES']) for i in items]
    bloque_i  = [work.at[i, 'BLOQUE'] for i in items]
    lnk_i     = [work.at[i, 'LNK'] for i in items]

    # Build rango list with scaled capacities
    avail_rangos = []
    for r in ranges_mix:
        rid = r['RANGO_ID']
        cap_left = max(0.0, float(r['CAPACIDAD']) - float(capacity_used.get(rid, 0.0)))
        if cap_left <= 0: continue
        avail_rangos.append({
            'rango': r,
            'min_s': s(r['MINIMO']),
            'max_s': s(min(r['MAXIMO'], cap_left)),
            'cap_left': cap_left,
        })
    if not avail_rangos:
        return [], [], capacity_used, lote_id_start

    # Precompute width compatibility matrix
    compat = _precompute_compat_matrix(items, width_cache, min_diff, max_diff, max_widths)

    # ── Build CP-SAT model ────────────────────────────────────────────────
    model = cp_model.CpModel()

    # x[i][j] = scaled LBS of item i assigned to lot j
    J = max_lotes
    x = [[model.NewIntVar(0, lbs_i[i], f'x_{i}_{j}') for j in range(J)]
         for i in range(n)]

    # y[j] = 1 if lot j is used
    y = [model.NewBoolVar(f'y_{j}') for j in range(J)]

    # r[j] = rango index assigned to lot j
    # We'll try all rangos for each lot via auxiliary bool
    lot_rango = [[model.NewBoolVar(f'lr_{j}_{k}') for k in range(len(avail_rangos))]
                  for j in range(J)]
    for j in range(J):
        # Each active lot uses exactly one rango
        model.Add(sum(lot_rango[j]) == y[j])

    # lot_total[j] = total LBS in lot j
    lot_total = [model.NewIntVar(0, s(6_000_000), f'lt_{j}') for j in range(J)]
    for j in range(J):
        model.Add(lot_total[j] == sum(x[i][j] for i in range(n)))

    # Capacity: lot_total[j] in [min, max] of its rango
    for j in range(J):
        for k, ar in enumerate(avail_rangos):
            # If lot j uses rango k: min_s <= lot_total[j] <= max_s
            model.Add(lot_total[j] >= ar['min_s']).OnlyEnforceIf(lot_rango[j][k])
            model.Add(lot_total[j] <= ar['max_s']).OnlyEnforceIf(lot_rango[j][k])
        # If lot not used: lot_total == 0
        model.Add(lot_total[j] == 0).OnlyEnforceIf(y[j].Not())

    # Each item fully assigned (split allowed across lots)
    for i in range(n):
        model.Add(sum(x[i][j] for j in range(J)) <= lbs_i[i])

    # x[i][j] > 0 only if lot j is active
    for i in range(n):
        for j in range(J):
            model.Add(x[i][j] <= lbs_i[i] * y[j])

    # Width compatibility: if two items i1,i2 both in lot j and not compatible -> can't coexist
    # Use auxiliary b_in[i][j] = 1 if x[i][j] > 0
    b_in = [[model.NewBoolVar(f'b_{i}_{j}') for j in range(J)] for i in range(n)]
    for i in range(n):
        for j in range(J):
            model.Add(x[i][j] > 0).OnlyEnforceIf(b_in[i][j])
            model.Add(x[i][j] == 0).OnlyEnforceIf(b_in[i][j].Not())

    # Width incompatibility constraints
    for a in range(n):
        for b in range(a+1, n):
            if not compat.get((a,b), True):
                for j in range(J):
                    model.AddBoolAnd([b_in[a][j].Not(), b_in[b][j].Not()]).OnlyEnforceIf(
                        [b_in[a][j], b_in[b][j]])
                    # Simpler: at most one of them in same lot
                    model.Add(b_in[a][j] + b_in[b][j] <= 1)

    # Block mixing constraints
    for a in range(n):
        for b in range(a+1, n):
            if not can_mix_blocks(bloque_i[a], bloque_i[b], allowed_pairs):
                for j in range(J):
                    model.Add(b_in[a][j] + b_in[b][j] <= 1)

    # Max SKU per lot
    for j in range(J):
        # Count distinct LNKs in lot j (approximation: count items, since LNK may repeat)
        lnk_set = {}
        for i, lnk in enumerate(lnk_i):
            if lnk not in lnk_set:
                lnk_set[lnk] = []
            lnk_set[lnk].append(i)
        # For each unique LNK, has_lnk[lnk][j] = 1 if any item with that LNK is in lot j
        has_lnk = {}
        for lnk, idxs in lnk_set.items():
            hl = model.NewBoolVar(f'hl_{lnk}_{j}')
            model.AddMaxEquality(hl, [b_in[i][j] for i in idxs])
            has_lnk[lnk] = hl
        model.Add(sum(has_lnk.values()) <= max_sku)

    # Symmetry breaking: lot j can only be used if lot j-1 is used
    for j in range(1, J):
        model.Add(y[j] <= y[j-1])

    # ── Objective ─────────────────────────────────────────────────────────
    # Maximize total LBS assigned
    total_assigned = sum(x[i][j] for i in range(n) for j in range(J))

    # Penalize capacity waste per lot
    cap_waste = []
    for j in range(J):
        for k, ar in enumerate(avail_rangos):
            waste = model.NewIntVar(0, ar['max_s'], f'w_{j}_{k}')
            model.Add(waste == ar['max_s'] - lot_total[j]).OnlyEnforceIf(lot_rango[j][k])
            model.Add(waste == 0).OnlyEnforceIf(lot_rango[j][k].Not())
            cap_waste.append(waste)

    # Weighted objective: 10*assigned - 1*waste
    model.Maximize(10 * total_assigned - sum(cap_waste))

    # ── Solve ──────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 4
    solver.parameters.log_search_progress = False

    status = solver.Solve(model)

    detalle_rows = []
    resumen_rows = []
    lote_id = lote_id_start

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for j in range(J):
            if not solver.Value(y[j]):
                continue

            # Find which rango this lot uses
            rango_used = None
            for k, ar in enumerate(avail_rangos):
                if solver.Value(lot_rango[j][k]):
                    rango_used = ar
                    break
            if rango_used is None:
                continue

            lot_items = []
            for i in range(n):
                lbs_raw = solver.Value(x[i][j])
                if lbs_raw <= 0:
                    continue
                lbs_real = lbs_raw / LBS_SCALE
                lot_items.append((items[i], lbs_real))

            if not lot_items:
                continue

            # Build width list for this lot
            lote_widths = []
            for idx, _ in lot_items:
                lote_widths += width_cache.get(idx, [])
            anchos_lote = sorted(set(float(w) for w in lote_widths if w and float(w)!=0.0))
            total_lote  = sum(lbs for _, lbs in lot_items)
            rid = rango_used['rango']['RANGO_ID']
            lote_label = f"L{lote_id:06d}"
            lote_id += 1

            bloques = [work.at[idx, 'BLOQUE'] for idx, _ in lot_items]
            bloque_dom = max(set(bloques), key=bloques.count) if bloques else ''

            for idx, lbs_asig in lot_items:
                detalle_rows.append({
                    'LOTE_ID': lote_label,
                    'ANCHOS_LOTE': str(anchos_lote),
                    'CATEGORIA': rango_used['rango']['CATEGORIA'],
                    'MIX': rango_used['rango']['MIX'],
                    'TELA.CUERPO': work.at[idx, 'TELA.CUERPO'],
                    'COLOR': work.at[idx, 'COLOR'],
                    'TONO': work.at[idx, 'TONO'] if 'TONO' in work.columns else '',
                    'LNK': work.at[idx, 'LNK'],
                    'PRIORIDAD': work.at[idx, 'PRIORIDAD'],
                    'BLOQUE': work.at[idx, 'BLOQUE'],
                    'ANCHO.F.C': float(work.at[idx, 'ANCHO.F.C']),
                    'ANCHO.F.M': float(work.at[idx, 'ANCHO.F.M']),
                    'CONSUMO_C': float(work.at[idx, 'CONSUMO_C']),
                    'FAMILIA': work.at[idx, 'FAMILIA'],
                    'COLOR_R': work.at[idx, 'COLOR_R'],
                    'STYLE': work.at[idx, 'STYLE'],
                    'LBS_ASIGNADAS': float(lbs_asig),
                    'LBS_EXTRA_SOBRE_ORDEN': 0.0,
                    'APLICA_REGLA': 'ORTOOLS',
                    'PRIORIDAD_USADA': float(rango_used['rango']['MAXIMO']),
                    'PRIORIDAD_OBJETIVO': None,
                    'ORIGEN_PRIORIDAD': 'ORTOOLS',
                    'PERMITIR_RANGO_SUPERIOR': 0,
                    'SPLIT_MIN_USADO': 0.0,
                    'DECISION_SCORE': float(solver.ObjectiveValue()),
                })
                # Update remaining
                work.at[idx, 'LBS_RESTANTES'] = max(
                    0.0, float(work.at[idx, 'LBS_RESTANTES']) - float(lbs_asig))

            resumen_rows.append({
                'LOTE_ID': lote_label,
                'ANCHOS_LOTE': str(anchos_lote),
                'CATEGORIA': rango_used['rango']['CATEGORIA'],
                'MIX': rango_used['rango']['MIX'],
                'TELA.CUERPO': work.at[lot_items[0][0], 'TELA.CUERPO'],
                'COLOR/TONO_KEY': '',
                'LBS_TOTAL': float(total_lote),
                'MIN_RANGO': float(rango_used['rango']['MINIMO']),
                'MAX_RANGO': float(rango_used['rango']['MAXIMO']),
                'CAPACIDAD_PERDIDA': float(rango_used['rango']['MAXIMO']) - float(total_lote),
                'SKU_DISTINTOS': len({work.at[idx,'LNK'] for idx,_ in lot_items}),
                'ANCHOS_UNICOS': len(anchos_lote),
                'BLOQUE_DOMINANTE': bloque_dom,
                'REGLA_DOMINANTE': 'ORTOOLS',
                'PRIORIDAD_FINAL': float(rango_used['rango']['MAXIMO']),
                'PRIORIDAD_OBJETIVO': None,
                'QUALITY_LEVEL': params.get('QUALITY_LEVEL', 5),
                'BEAM_WIDTH_USADO': 0,
            })
            capacity_used[rid] = capacity_used.get(rid, 0.0) + float(total_lote)

    return detalle_rows, resumen_rows, capacity_used, lote_id


def run_loteo_ortools(df_data, df_cap, params,
                      progress_callback=None, cancel_flag=None):
    """
    Main entry point — same signature as run_loteo() in loteo.py.
    Uses OR-Tools for groups <= MAX_ITEMS_ORTOOLS, greedy fallback for larger ones.
    Returns (df_detalle, df_resumen, df_excedentes, df_params, cancelled).
    """
    from engine.loteo import run_loteo as run_greedy

    ranges = build_ranges(df_cap)
    capacity_used = {r['RANGO_ID']: 0.0 for r in ranges}

    data = df_data.copy()
    data['BLOQUE']        = data['PRIORIDAD'].apply(prioridad_bloque)
    data['LBS_RESTANTES'] = data['TOTAL'].astype(float)
    data['LBS_SCRAP']     = 0.0

    # Width cache
    width_cache = {}
    for idx in data.index:
        ws = []
        for c in ['ANCHO.F.C', 'ANCHO.F.M']:
            if c in data.columns:
                v = data.at[idx, c]
                if pd.notna(v) and float(v) != 0.0:
                    ws.append(float(v))
        width_cache[idx] = ws

    group_keys = ['TELA.CUERPO', 'MIX']
    if 'TONO' in data.columns:
        group_keys.insert(1, 'TONO')
    else:
        group_keys.insert(1, 'COLOR')

    groups      = list(data.groupby(group_keys).groups.items())
    total_groups= len(groups)
    detalle     = []
    resumen     = []
    lote_id     = 1
    lbs_proc    = 0.0
    cancelled   = False
    greedy_groups = 0
    ortools_groups = 0

    quality_level = int(params.get('QUALITY_LEVEL', 5))
    # Time limit scales with quality: quality 5 = 10s, quality 10 = 60s
    time_limit = 5.0 + quality_level * 5.5

    for g_num, (keys, grp_idx) in enumerate(groups):
        if cancel_flag and cancel_flag[0]:
            cancelled = True
            break

        work = data.loc[grp_idx].copy()
        mixv = keys[-1]
        tela = keys[0]
        tono = keys[1] if len(keys) > 2 else keys[1]

        ranges_mix = [r for r in ranges if r['MIX'] == mixv]
        n_items    = (work['LBS_RESTANTES'] > 0).sum()

        if progress_callback:
            engine = 'OR-Tools' if n_items <= MAX_ITEMS_ORTOOLS else 'Greedy'
            progress_callback(
                g_num / total_groups,
                f"[{engine}] Grupo {g_num+1}/{total_groups} · {tela}",
                {'lotes': len(resumen), 'lbs': lbs_proc,
                 'grupo': g_num+1, 'total': total_groups}
            )

        if n_items <= MAX_ITEMS_ORTOOLS:
            # ── OR-Tools path ──────────────────────────────────────────
            ortools_groups += 1
            det_rows, res_rows, capacity_used, lote_id = solve_group_ortools(
                work, ranges_mix, params, width_cache,
                lote_id, capacity_used, {},
                cancel_flag=cancel_flag, time_limit=time_limit,
            )
            detalle.extend(det_rows)
            resumen.extend(res_rows)
            lbs_proc += sum(r['LBS_TOTAL'] for r in res_rows)

            # Update data LBS_RESTANTES
            assigned = {}
            for row in det_rows:
                lnk = row['LNK']
                assigned[lnk] = assigned.get(lnk, 0.0) + row['LBS_ASIGNADAS']
            for idx in work.index:
                lnk = data.at[idx, 'LNK']
                if lnk in assigned:
                    data.at[idx, 'LBS_RESTANTES'] = max(
                        0.0, float(data.at[idx, 'LBS_RESTANTES']) - assigned[lnk])
        else:
            # ── Greedy fallback for large groups ───────────────────────
            greedy_groups += 1
            # Run greedy on just this group's data
            work_sub = data.loc[grp_idx].copy()
            cap_sub  = pd.DataFrame(ranges_mix)
            if cap_sub.empty:
                continue
            # Adjust capacities by what's already used
            cap_sub_adj = []
            for r in ranges_mix:
                r2 = dict(r)
                used = capacity_used.get(r['RANGO_ID'], 0.0)
                r2['CAPACIDAD'] = max(0.0, float(r['CAPACIDAD']) - used)
                cap_sub_adj.append(r2)
            cap_sub = pd.DataFrame(cap_sub_adj)

            df_det_g, df_res_g, _, _, _ = run_greedy(
                work_sub, cap_sub, params,
                progress_callback=None, cancel_flag=cancel_flag)

            if not df_det_g.empty:
                # Offset lote IDs
                offset = lote_id - 1
                df_det_g['LOTE_ID'] = df_det_g['LOTE_ID'].apply(
                    lambda x: f"L{int(x[1:])+offset:06d}")
                df_res_g['LOTE_ID'] = df_res_g['LOTE_ID'].apply(
                    lambda x: f"L{int(x[1:])+offset:06d}")
                lote_id += len(df_res_g)

                detalle.extend(df_det_g.to_dict('records'))
                resumen.extend(df_res_g.to_dict('records'))
                lbs_proc += df_det_g['LBS_ASIGNADAS'].sum()

                # Update capacity_used
                for _, row in df_res_g.iterrows():
                    for r in ranges_mix:
                        if r['CATEGORIA'] == row.get('CATEGORIA') and r['MIX'] == row.get('MIX'):
                            capacity_used[r['RANGO_ID']] = capacity_used.get(r['RANGO_ID'], 0.0) + float(row['LBS_TOTAL'])

                # Update data LBS_RESTANTES
                assigned = {}
                for _, row in df_det_g.iterrows():
                    lnk = row['LNK']
                    assigned[lnk] = assigned.get(lnk, 0.0) + row['LBS_ASIGNADAS']
                for idx in grp_idx:
                    lnk = data.at[idx, 'LNK']
                    if lnk in assigned:
                        data.at[idx, 'LBS_RESTANTES'] = max(
                            0.0, float(data.at[idx, 'LBS_RESTANTES']) - assigned[lnk])

    # Excedentes
    ec = ['LNK','TELA.CUERPO','COLOR','MIX','PRIORIDAD','BLOQUE',
          'ANCHO.F.C','ANCHO.F.M','TOTAL','LBS_RESTANTES','LBS_SCRAP']
    if 'TONO' in data.columns: ec.insert(3, 'TONO')
    exced = data[data['LBS_RESTANTES'] > 1e-9][[c for c in ec if c in data.columns]].copy()

    df_det = pd.DataFrame(detalle)
    if len(df_det) > 0 and 'CONSUMO_C' in df_det.columns:
        df_det['DOCENAS'] = np.where(
            df_det['CONSUMO_C'] > 0,
            df_det['LBS_ASIGNADAS'] / df_det['CONSUMO_C'], np.nan)
    df_res = pd.DataFrame(resumen)

    df_par = pd.DataFrame([
        ['ENGINE', 'OR-Tools CP-SAT'],
        ['QUALITY_LEVEL', quality_level],
        ['TIME_LIMIT_PER_GROUP', time_limit],
        ['MAX_ITEMS_ORTOOLS', MAX_ITEMS_ORTOOLS],
        ['ORTOOLS_GROUPS', ortools_groups],
        ['GREEDY_GROUPS', greedy_groups],
        ['TOTAL_GROUPS', total_groups],
    ], columns=['PARAMETRO', 'VALOR'])

    return df_det, df_res, exced, df_par, cancelled
