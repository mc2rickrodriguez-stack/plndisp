"""
NV2 Loteo Tintorería – Streamlit App v3
Diseño: secciones colapsables en área principal, sidebar solo para perfiles y ajustes rápidos.
"""
import io, sys, os, json, base64
from datetime import datetime
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
from engine.loader import load_inputs
from engine.loteo import run_loteo, build_reports
from ui.charts import (
    chart_capacidad_barras, chart_bloques_donut,
    chart_heatmap_capacidad, chart_completitud_lnk,
)

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NV2 Loteo Tintorería",
    page_icon="🧶",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.section-header {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 12px 18px;
    margin-bottom: 8px;
    font-weight: 600;
    color: #1e293b;
}
.kpi-box {
    background: #f1f5f9;
    border-radius: 8px;
    padding: 12px;
    text-align: center;
}
div[data-testid="stDataEditor"] { border-radius: 6px; }
.stButton > button[kind="primary"] {
    background: #2563eb;
    border: none;
    border-radius: 6px;
}
.info-note {
    background: #eff6ff;
    border-left: 3px solid #3b82f6;
    padding: 8px 14px;
    border-radius: 4px;
    font-size: 0.88rem;
    color: #1e40af;
    margin-bottom: 10px;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────
DEFAULTS = {
    "run_history": [], "last_result": None,
    "df_data": None, "df_cap": None, "params": None,
    "raw_file_bytes": None, "raw_file_name": None,
    "cap_applied": False,          # did user click Apply on capacidades?
    "tbl_capacidades": None,
    "tbl_reglas_anchos": None,
    "tbl_restricciones_ancho": None,
    "tbl_restricciones_color": None,
    "tbl_restricciones_familia": None,
    "tbl_combinaciones": None,
    "profiles": {}, "active_profile": None,
    # sidebar config values stored so they survive rerun
    "cfg": {},
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Empty-table factories ──────────────────────────────────────────────────
def empty_cap():  return pd.DataFrame(columns=["CATEGORIA","MINIMO","MAXIMO","CAPACIDAD","MIX"])
def empty_ra():   return pd.DataFrame(columns=["ANCHO_1","ANCHO_2","CAPACIDAD_PRIORIDAD_1","CAPACIDAD_PRIORIDAD_2","CAPACIDAD_PRIORIDAD_3"])
def empty_ras():  return pd.DataFrame(columns=["STYLE","LIMITE_ANCHO","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"])
def empty_rc():   return pd.DataFrame(columns=["COLOR_R","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"])
def empty_rf():   return pd.DataFrame(columns=["FAMILIA","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3","PRIORIDAD_4"])
def empty_comb(): return pd.DataFrame(columns=["PRIORIDAD_1","PRIORIDAD_2"])

def get_tbl(key, factory):
    t = st.session_state[key]
    return t if t is not None else factory()

def fmt_lbs(v): return f"{v:,.0f}"

def df_to_json(df):
    if df is None or df.empty: return []
    return df.where(pd.notna(df), None).to_dict(orient="records")

def json_to_df(records, factory):
    return pd.DataFrame(records) if records else factory()

# ── Excel export ───────────────────────────────────────────────────────────
def export_excel(result):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        result["detalle"].to_excel(w, index=False, sheet_name="DETALLE_LOTES")
        result["resumen"].to_excel(w, index=False, sheet_name="RESUMEN_LOTES")
        result["excedentes"].to_excel(w, index=False, sheet_name="EXCEDENTES")
        result["params_out"].to_excel(w, index=False, sheet_name="PARAMETROS")
        for key, df in result["reports"].items():
            if not df.empty: df.to_excel(w, index=False, sheet_name=key[:31])
    return buf.getvalue()

# ── Profile helpers ────────────────────────────────────────────────────────
def build_profile(overrides):
    p = {"overrides": overrides, "tables": {
        "capacidades":          df_to_json(get_tbl("tbl_capacidades",empty_cap)),
        "reglas_anchos":        df_to_json(get_tbl("tbl_reglas_anchos",empty_ra)),
        "restricciones_ancho":  df_to_json(get_tbl("tbl_restricciones_ancho",empty_ras)),
        "restricciones_color":  df_to_json(get_tbl("tbl_restricciones_color",empty_rc)),
        "restricciones_familia":df_to_json(get_tbl("tbl_restricciones_familia",empty_rf)),
        "combinaciones":        df_to_json(get_tbl("tbl_combinaciones",empty_comb)),
    }}
    if st.session_state.raw_file_bytes:
        p["file_b64"]  = base64.b64encode(st.session_state.raw_file_bytes).decode()
        p["file_name"] = st.session_state.raw_file_name or "archivo.xlsx"
    return p

def apply_profile(profile):
    t = profile.get("tables", {})
    st.session_state.tbl_capacidades          = json_to_df(t.get("capacidades"), empty_cap)
    st.session_state.tbl_reglas_anchos        = json_to_df(t.get("reglas_anchos"), empty_ra)
    st.session_state.tbl_restricciones_ancho  = json_to_df(t.get("restricciones_ancho"), empty_ras)
    st.session_state.tbl_restricciones_color  = json_to_df(t.get("restricciones_color"), empty_rc)
    st.session_state.tbl_restricciones_familia= json_to_df(t.get("restricciones_familia"), empty_rf)
    st.session_state.tbl_combinaciones        = json_to_df(t.get("combinaciones"), empty_comb)
    st.session_state.cfg = profile.get("overrides", {})
    st.session_state.cap_applied = False
    if "file_b64" in profile:
        raw = base64.b64decode(profile["file_b64"])
        st.session_state.raw_file_bytes = raw
        st.session_state.raw_file_name  = profile.get("file_name","archivo.xlsx")
        try:
            df_data, df_cap, params, _ = load_inputs(io.BytesIO(raw))
            st.session_state.df_data = df_data
            st.session_state.df_cap  = df_cap
            st.session_state.params  = params
        except Exception: pass

def load_tables_from_excel(raw_bytes):
    xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine="openpyxl")
    def sr(sheet, factory):
        if sheet in xls.sheet_names:
            df = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=sheet, engine="openpyxl")
            df.columns = [str(c).strip() for c in df.columns]
            return df
        return factory()
    st.session_state.tbl_capacidades          = sr("CAPACIDADES_TINTO",       empty_cap)
    st.session_state.tbl_reglas_anchos        = sr("REGLAS_ANCHOS_COMBINADOS", empty_ra)
    st.session_state.tbl_restricciones_ancho  = sr("RESTRICCIONES_ANCHO",      empty_ras)
    st.session_state.tbl_restricciones_color  = sr("RESTRICCIONES_COLOR",      empty_rc)
    st.session_state.tbl_restricciones_familia= sr("RESTRICCIONES_FAMILIA",    empty_rf)
    st.session_state.tbl_combinaciones        = sr("COMBINACIONES_PRIORIDAD",   empty_comb)
    st.session_state.cap_applied = False

# ── Param rebuild helpers ──────────────────────────────────────────────────
def rebuild_all_params(params2):
    def rebuild_familia(df):
        out = {}
        if df is None or df.empty: return out
        pcols = [c for c in df.columns if c.upper().startswith("PRIORIDAD")]
        for _, r in df.iterrows():
            f = str(r.get("FAMILIA","")).strip().upper()
            if not f: continue
            caps = [float(r[pc]) for pc in pcols if pd.notna(r.get(pc))]
            if caps: out[f] = caps
        return out
    def rebuild_color(df):
        out = {}
        if df is None or df.empty: return out
        for _, r in df.iterrows():
            c = str(r.get("COLOR_R","")).strip().upper()
            v = r.get("PRIORIDAD_1", None)
            out[c] = float(v) if pd.notna(v) else None
        return out
    def rebuild_ancho(df):
        out = {}
        if df is None or df.empty: return out
        pcols = [c for c in df.columns if c.upper().startswith("PRIORIDAD")]
        for _, r in df.iterrows():
            style = str(r.get("STYLE","")).strip().upper()
            if not style: continue
            lim = r.get("LIMITE_ANCHO", None)
            try: lim = float(lim) if pd.notna(lim) else None
            except: lim = None
            caps = []
            for pc in pcols:
                v = r.get(pc, None)
                if pd.notna(v):
                    try: caps.append(float(v))
                    except: pass
            out[style] = {"limite": lim, "prioridades": caps}
        return out
    def rebuild_reglas(df):
        out = []
        if df is None or df.empty: return out
        pcols = [c for c in df.columns if c.upper().startswith("CAPACIDAD_PRIORIDAD")]
        for _, r in df.iterrows():
            try: a1 = float(r.get("ANCHO_1",0)); a2 = float(r.get("ANCHO_2",0))
            except: continue
            caps = [float(r[pc]) for pc in pcols if pd.notna(r.get(pc))]
            if caps: out.append({"a1": a1, "a2": a2, "prioridades": caps})
        return out
    def rebuild_comb(df):
        pairs = set()
        if df is None or df.empty: return pairs
        for _, r in df.iterrows():
            a = str(r.get("PRIORIDAD_1","")).strip()
            b = str(r.get("PRIORIDAD_2","")).strip()
            if a and b: pairs.add((a,b)); pairs.add((b,a))
        return pairs

    params2["RESTRICCIONES_FAMILIA"]   = rebuild_familia(st.session_state.tbl_restricciones_familia)
    params2["RESTRICCIONES_COLOR"]     = rebuild_color(st.session_state.tbl_restricciones_color)
    params2["RESTRICCIONES_ANCHO"]     = rebuild_ancho(st.session_state.tbl_restricciones_ancho)
    params2["REGLAS_ANCHOS_COMBINADOS"]= rebuild_reglas(st.session_state.tbl_reglas_anchos)
    comb = rebuild_comb(st.session_state.tbl_combinaciones)
    if comb: params2["MIX_ALLOWED"] = comb
    return params2

# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR  — perfiles + ajustes avanzados
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("🧶 NV2 Loteo")
    st.caption("Tintorería · Planificación de lotes")
    st.divider()

    # Perfiles ─────────────────────────────────────────────────────────────
    st.subheader("💾 Perfiles")
    profiles = st.session_state.profiles
    pnames   = list(profiles.keys())
    if pnames:
        sel = st.selectbox("Cargar perfil", ["— seleccionar —"] + pnames, key="sel_profile")
        if sel != "— seleccionar —" and st.button("📥 Aplicar", use_container_width=True):
            apply_profile(profiles[sel])
            st.success(f"Perfil '{sel}' aplicado")
            st.rerun()

    json_up = st.file_uploader("Importar JSON", type=["json"], key="json_upload")
    if json_up:
        try:
            loaded = json.load(json_up)
            nm = json_up.name.replace(".json","")
            profiles[nm] = loaded
            st.session_state.profiles = profiles
            st.success(f"'{nm}' importado")
        except Exception as e:
            st.error(str(e))

    st.divider()

    # Ajustes avanzados (scoring, splits, flags) ──────────────────────────
    p   = st.session_state.params or {}
    cfg = st.session_state.cfg    or {}
    def cv(k, d): return cfg.get(k, p.get(k, d))

    st.subheader("⚙️ Ajustes avanzados")

    with st.expander("Scoring & Beam"):
        beam_w       = st.number_input("BEAM_WIDTH",          value=int(cv("BEAM_WIDTH",3)),   min_value=1, step=1)
        w_fill       = st.number_input("W_FILL",              value=float(cv("W_FILL",5.0)),   step=0.5)
        w_cap_loss   = st.number_input("W_CAP_LOSS",          value=float(cv("W_CAP_LOSS",2.0)),step=0.5)
        w_width_pref = st.number_input("W_WIDTH_PREF",        value=float(cv("W_WIDTH_PREF",2.0)),step=0.5)
        w_1100       = st.number_input("W_1100_WIDTHS_STRICT",value=float(cv("W_1100_WIDTHS_STRICT",10.0)),step=1.0)
        pref_list_str= st.text_input("WIDTH_PREF_LIST",       value=",".join(str(x) for x in cv("WIDTH_PREF_LIST",[2,3,1,4,5,6])))

    with st.expander("Splits & Scrap"):
        split_default= st.number_input("SPLIT_MIN_LBS_DEFAULT",value=float(cv("SPLIT_MIN_LBS_DEFAULT",100)),step=10.0)
        split_ancho18= st.number_input("SPLIT_MIN_LBS_ANCHO18",value=float(cv("SPLIT_MIN_LBS_ANCHO18",200)),step=10.0)
        scrap_rem    = st.checkbox("SCRAP_REMAINDER_BELOW_SPLIT_MIN", value=bool(int(cv("SCRAP_REMAINDER_BELOW_SPLIT_MIN",1))))

    with st.expander("Flags"):
        overshoot    = st.checkbox("OVERSHOOT_ENABLE",             value=bool(int(cv("OVERSHOOT_ENABLE",1))))
        undershoot   = st.checkbox("UNDERSHOOT_ENABLE",            value=bool(int(cv("UNDERSHOOT_ENABLE",1))))
        upgrade_cat  = st.checkbox("UPGRADE_CATEGORIA",            value=bool(int(cv("UPGRADE_CATEGORIA",1))))
        try_all      = st.checkbox("TRY_ALL_PRIORITIES",           value=bool(int(cv("TRY_ALL_PRIORITIES",1))))
        apply_bleach = st.checkbox("APPLY_RULES_BLEACH",           value=bool(int(cv("APPLY_RULES_BLEACH",0))))
        ancho18_spill= st.checkbox("ANCHO18_ALLOW_SPILLOVER_2600", value=bool(int(cv("ANCHO18_ALLOW_SPILLOVER_2600",0))))

    with st.expander("Reglas de orden"):
        rule_order     = st.text_input("RULE_ORDER",     value=str(cv("RULE_ORDER","ANCHO18>COMBO_ANCHOS>COLOR_R>FAMILIA>DEFAULT")))
        priority_order = st.text_input("PRIORITY_ORDER", value=str(cv("PRIORITY_ORDER","")))
        ancho18_max    = st.text_input("ANCHO18_ALLOWED_MAX_DYE",
                                       value=",".join(str(int(x)) for x in cv("ANCHO18_ALLOWED_MAX_DYE",{2200,1100})))

    # collect overrides (section-1 values are read from main area widgets below)
    adv_overrides = {
        "BEAM_WIDTH": beam_w, "W_FILL": w_fill, "W_CAP_LOSS": w_cap_loss,
        "W_WIDTH_PREF": w_width_pref, "W_1100_WIDTHS_STRICT": w_1100,
        "WIDTH_PREF_LIST": pref_list_str,
        "SPLIT_MIN_LBS_DEFAULT": split_default, "SPLIT_MIN_LBS_ANCHO18": split_ancho18,
        "SCRAP_REMAINDER_BELOW_SPLIT_MIN": int(scrap_rem),
        "OVERSHOOT_ENABLE": int(overshoot), "UNDERSHOOT_ENABLE": int(undershoot),
        "UPGRADE_CATEGORIA": int(upgrade_cat), "TRY_ALL_PRIORITIES": int(try_all),
        "APPLY_RULES_BLEACH": int(apply_bleach), "ANCHO18_ALLOW_SPILLOVER_2600": int(ancho18_spill),
        "RULE_ORDER": rule_order, "PRIORITY_ORDER": priority_order,
        "ANCHO18_ALLOWED_MAX_DYE": ancho18_max,
    }

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN — Título
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("## 🧶 NV2 Loteo Tintorería")
st.caption("Optimización · Lotes · Asignación de Pedidos")

# ══════════════════════════════════════════════════════════════════════════════
#  SECCIÓN 1 — Carga de Archivo
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("📁  Sección 1 — Carga de Archivo", expanded=True):
    up_col, info_col = st.columns([2, 1])
    with up_col:
        uploaded = st.file_uploader(
            "Sube tu archivo Excel (.xlsx / .xlsm)",
            type=["xlsx","xlsm"], key="file_upload",
            label_visibility="collapsed",
        )
    with info_col:
        if st.session_state.df_data is not None:
            df_data = st.session_state.df_data
            st.success(f"✅ **{st.session_state.raw_file_name}**")
            st.caption(f"{len(df_data):,} filas · LBS: {fmt_lbs(df_data['TOTAL'].sum())}")
        else:
            st.info("Sin archivo cargado")

    if uploaded is not None:
        file_bytes = uploaded.read()
        if file_bytes != st.session_state.raw_file_bytes:
            with st.spinner("Leyendo…"):
                try:
                    df_data, df_cap, params, hdr = load_inputs(io.BytesIO(file_bytes))
                    st.session_state.df_data = df_data
                    st.session_state.df_cap  = df_cap
                    st.session_state.params  = params
                    st.session_state.raw_file_bytes = file_bytes
                    st.session_state.raw_file_name  = uploaded.name
                    load_tables_from_excel(file_bytes)
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

    # DATA preview
    if st.session_state.df_data is not None:
        with st.expander("🔍 Vista previa de DATA"):
            df_data = st.session_state.df_data
            mix_opts = ["Todos"] + sorted(df_data["MIX"].unique().tolist())
            fc1, fc2 = st.columns([3,1])
            mix_sel = fc1.selectbox("MIX", mix_opts, key="prev_mix", label_visibility="collapsed")
            n_rows  = fc2.number_input("Filas", 5, 500, 50, key="prev_n", label_visibility="collapsed")
            prev_df = df_data if mix_sel=="Todos" else df_data[df_data["MIX"]==mix_sel]
            st.dataframe(prev_df.head(n_rows), use_container_width=True, height=220)

# ══════════════════════════════════════════════════════════════════════════════
#  SECCIÓN 2 — Capacidad y Validación
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("📋  Sección 2 — Capacidad y Validación", expanded=True):
    st.markdown(
        '<div class="info-note">✏️ Edita la tabla y presiona <b>Aplicar cambios de capacidad</b> para confirmar.</div>',
        unsafe_allow_html=True,
    )

    tbl_cap = get_tbl("tbl_capacidades", empty_cap)

    cap_edited = st.data_editor(
        tbl_cap,
        num_rows="dynamic",
        use_container_width=True,
        height=min(60 + 35 * max(len(tbl_cap), 1), 400),
        key="editor_cap",
        column_config={
            "CATEGORIA": st.column_config.TextColumn("Categoría", width="small"),
            "MINIMO":    st.column_config.NumberColumn("Mínimo (LBS)", min_value=0, format="%d"),
            "MAXIMO":    st.column_config.NumberColumn("Máximo (LBS)", min_value=0, format="%d"),
            "CAPACIDAD": st.column_config.NumberColumn("Capacidad Total (LBS)", min_value=0, format="%d"),
            "MIX":       st.column_config.SelectboxColumn("MIX", options=["DYE","BLEACH"]),
        },
    )

    btn_col, stat_col = st.columns([1,3])
    with btn_col:
        if st.button("✅ Aplicar cambios de capacidad", type="primary", use_container_width=True):
            st.session_state.tbl_capacidades = cap_edited
            st.session_state.cap_applied = True
            st.success("Capacidades actualizadas")
    with stat_col:
        if st.session_state.cap_applied:
            cap_ok = get_tbl("tbl_capacidades", empty_cap)
            if not cap_ok.empty:
                total_c = pd.to_numeric(cap_ok["CAPACIDAD"], errors="coerce").sum()
                n_invalidas = cap_ok[["MINIMO","MAXIMO","CAPACIDAD"]].isna().any(axis=1).sum()
                st.caption(
                    f"{'⚠️' if n_invalidas else '✅'} "
                    f"{len(cap_ok)} categorías · "
                    f"Capacidad total: **{fmt_lbs(total_c)} LBS** · "
                    f"{'Filas incompletas: ' + str(n_invalidas) if n_invalidas else 'Sin errores'}"
                )
        else:
            st.caption("ℹ️ Edita la tabla y presiona Aplicar para habilitar el loteo.")

# ══════════════════════════════════════════════════════════════════════════════
#  SECCIÓN 3 — Parámetros de Anchos y SKU
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("⚙️  Sección 3 — Parámetros de Loteo", expanded=False):
    p   = st.session_state.params or {}
    cfg = st.session_state.cfg    or {}
    def cv(k, d): return cfg.get(k, p.get(k, d))

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        min_diff   = st.number_input("MIN_DIFF",   value=float(cv("MIN_DIFF",1)),   step=0.5, format="%.1f", help="Diferencia mínima entre anchos")
        max_diff   = st.number_input("MAX_DIFF",   value=float(cv("MAX_DIFF",6)),   step=0.5, format="%.1f", help="Diferencia máxima entre anchos")
    with col2:
        max_widths = st.number_input("MAX_WIDTHS", value=int(cv("MAX_WIDTHS",3)),   min_value=1, step=1, help="Máximo de anchos distintos por lote")
        max_sku    = st.number_input("MAX_SKU",    value=int(cv("MAX_SKU",8)),      min_value=1, step=1, help="Máximo de SKUs por lote")
    with col3:
        widths_target = st.text_input("WIDTHS_TARGET_ORDER", value=str(cv("WIDTHS_TARGET_ORDER","2>3>1")),
                                      help="Orden de preferencia de número de anchos. Ej: 2>3>1")
        req_strict    = st.checkbox("REQUIRE_WIDTHS_STRICT", value=bool(int(cv("REQUIRE_WIDTHS_STRICT",1))),
                                    help="Obligar el orden de anchos estrictamente")
    with col4:
        st.markdown("**Resumen parámetros activos**")
        st.caption(f"Anchos: {min_diff}–{max_diff} | Max anchos: {max_widths} | Max SKU: {max_sku}")
        st.caption(f"Target order: {widths_target} | Strict: {req_strict}")

    section3_overrides = {
        "MIN_DIFF": min_diff, "MAX_DIFF": max_diff,
        "MAX_WIDTHS": max_widths, "MAX_SKU": max_sku,
        "WIDTHS_TARGET_ORDER": widths_target,
        "REQUIRE_WIDTHS_STRICT": int(req_strict),
    }

# ══════════════════════════════════════════════════════════════════════════════
#  SECCIÓN 4 — Reglas de Combinación y Restricciones
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("🔗  Sección 4 — Reglas de Combinación y Restricciones", expanded=False):
    rt1, rt2, rt3, rt4, rt5 = st.tabs([
        "🔗 Anchos Combinados",
        "📐 Restricciones Ancho",
        "🎨 Restricciones Color",
        "👨‍👩‍👧 Restricciones Familia",
        "⚖️ Combinaciones Prioridad",
    ])

    with rt1:
        st.markdown('<div class="info-note">Define qué pares de anchos pueden ir juntos y en qué tamaños (prioridades). '
                    'Tabla vacía = combinaciones libres.</div>', unsafe_allow_html=True)
        ra_ed = st.data_editor(
            get_tbl("tbl_reglas_anchos", empty_ra), num_rows="dynamic",
            use_container_width=True, key="editor_ra",
            column_config={
                "ANCHO_1":               st.column_config.NumberColumn("Ancho 1",       format="%.1f"),
                "ANCHO_2":               st.column_config.NumberColumn("Ancho 2",       format="%.1f"),
                "CAPACIDAD_PRIORIDAD_1": st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
                "CAPACIDAD_PRIORIDAD_2": st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
                "CAPACIDAD_PRIORIDAD_3": st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
            },
        )
        if st.button("💾 Guardar Anchos Combinados", key="save_ra"):
            st.session_state.tbl_reglas_anchos = ra_ed
            st.success("Guardado")

    with rt2:
        st.markdown('<div class="info-note">Si un lote contiene ese STYLE y tiene un ancho ≤ LIMITE_ANCHO, '
                    'prioriza las capacidades indicadas (de mayor a menor).</div>', unsafe_allow_html=True)
        ras_ed = st.data_editor(
            get_tbl("tbl_restricciones_ancho", empty_ras), num_rows="dynamic",
            use_container_width=True, key="editor_ras",
            column_config={
                "STYLE":        st.column_config.TextColumn("STYLE", width="medium"),
                "LIMITE_ANCHO": st.column_config.NumberColumn("Límite Ancho", format="%.1f"),
                "PRIORIDAD_1":  st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
                "PRIORIDAD_2":  st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
                "PRIORIDAD_3":  st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
            },
        )
        s_search = st.text_input("🔍 Buscar STYLE", key="style_search")
        if s_search and not ras_ed.empty:
            found = ras_ed[ras_ed["STYLE"].astype(str).str.upper().str.contains(s_search.upper(), na=False)]
            if not found.empty: st.dataframe(found, use_container_width=True)
            else: st.caption("Sin coincidencias")
        if st.button("💾 Guardar Restricciones Ancho", key="save_ras"):
            st.session_state.tbl_restricciones_ancho = ras_ed
            st.success("Guardado")

    with rt3:
        st.markdown('<div class="info-note">Según la columna COLOR_R en DATA, prioriza los tamaños de lote '
                    'en el orden indicado.</div>', unsafe_allow_html=True)
        rc_ed = st.data_editor(
            get_tbl("tbl_restricciones_color", empty_rc), num_rows="dynamic",
            use_container_width=True, key="editor_rc",
            column_config={
                "COLOR_R":     st.column_config.TextColumn("COLOR_R", width="medium"),
                "PRIORIDAD_1": st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
                "PRIORIDAD_2": st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
                "PRIORIDAD_3": st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
            },
        )
        if st.button("💾 Guardar Restricciones Color", key="save_rc"):
            st.session_state.tbl_restricciones_color = rc_ed
            st.success("Guardado")

    with rt4:
        st.markdown('<div class="info-note">Familias listadas aquí respetan el orden de prioridades. '
                    'Familias no listadas → loteo libre.</div>', unsafe_allow_html=True)
        rf_ed = st.data_editor(
            get_tbl("tbl_restricciones_familia", empty_rf), num_rows="dynamic",
            use_container_width=True, key="editor_rf",
            column_config={
                "FAMILIA":     st.column_config.TextColumn("FAMILIA", width="medium"),
                "PRIORIDAD_1": st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
                "PRIORIDAD_2": st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
                "PRIORIDAD_3": st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
                "PRIORIDAD_4": st.column_config.NumberColumn("Prioridad 4 (LBS)", format="%d"),
            },
        )
        if st.button("💾 Guardar Restricciones Familia", key="save_rf"):
            st.session_state.tbl_restricciones_familia = rf_ed
            st.success("Guardado")

    with rt5:
        st.markdown('<div class="info-note">Por defecto los bloques de prioridad NO se mezclan. '
                    'Agrega aquí los pares que SÍ pueden coexistir en un mismo lote. '
                    'Tabla vacía = sin mezcla.</div>', unsafe_allow_html=True)
        BLOQUES = ["VENCIDOS","AHEAD","AHEAD2","OTROS"]
        comb_ed = st.data_editor(
            get_tbl("tbl_combinaciones", empty_comb), num_rows="dynamic",
            use_container_width=True, key="editor_comb",
            column_config={
                "PRIORIDAD_1": st.column_config.SelectboxColumn("Bloque 1", options=BLOQUES),
                "PRIORIDAD_2": st.column_config.SelectboxColumn("Bloque 2", options=BLOQUES),
            },
        )
        if st.button("💾 Guardar Combinaciones Prioridad", key="save_comb"):
            st.session_state.tbl_combinaciones = comb_ed
            st.success("Guardado")

# ══════════════════════════════════════════════════════════════════════════════
#  SECCIÓN 5 — Guardar Perfil
# ══════════════════════════════════════════════════════════════════════════════
with st.expander("💾  Sección 5 — Guardar Perfil de Configuración", expanded=False):
    all_overrides = {**adv_overrides, **section3_overrides}
    pc1, pc2, pc3 = st.columns([2,1,1])
    with pc1:
        pname_input = st.text_input("Nombre del perfil", placeholder="ej. Semana23_DYE", key="pname_input")
    with pc2:
        if st.button("💾 Guardar en sesión", use_container_width=True):
            nm = pname_input.strip()
            if nm:
                profiles[nm] = build_profile(all_overrides)
                st.session_state.profiles = profiles
                st.success(f"'{nm}' guardado")
            else:
                st.warning("Ingresa un nombre")
    with pc3:
        profile_json = json.dumps(build_profile(all_overrides), indent=2, ensure_ascii=False)
        fn = (pname_input.strip() or "perfil") + ".json"
        st.download_button("📤 Exportar JSON", data=profile_json,
                           file_name=fn, mime="application/json", use_container_width=True)

    if profiles:
        st.caption(f"Perfiles en sesión: {', '.join(profiles.keys())}")

# ══════════════════════════════════════════════════════════════════════════════
#  BOTÓN PRINCIPAL — Correr Loteo
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
can_run = (
    st.session_state.df_data is not None and
    st.session_state.raw_file_bytes is not None and
    st.session_state.cap_applied
)

if not can_run:
    reasons = []
    if st.session_state.df_data is None:     reasons.append("📁 Sube un archivo Excel")
    if not st.session_state.cap_applied:     reasons.append("📋 Aplica cambios de capacidad en Sección 2")
    st.info("  ·  ".join(reasons) if reasons else "Listo para correr")

run_btn = st.button(
    "▶  Correr Loteo",
    type="primary",
    use_container_width=True,
    disabled=not can_run,
)

# ══════════════════════════════════════════════════════════════════════════════
#  EJECUCIÓN
# ══════════════════════════════════════════════════════════════════════════════
if run_btn and can_run:
    all_overrides = {**adv_overrides, **section3_overrides}
    with st.spinner("Preparando parámetros…"):
        try:
            df_data2, df_cap2, params2, _ = load_inputs(
                io.BytesIO(st.session_state.raw_file_bytes),
                param_overrides=all_overrides,
            )
            # Override capacidades con tabla editada en UI
            cap_ui = get_tbl("tbl_capacidades", empty_cap)
            if not cap_ui.empty:
                for c in ["MINIMO","MAXIMO","CAPACIDAD"]:
                    cap_ui[c] = pd.to_numeric(cap_ui[c], errors="coerce")
                df_cap2 = cap_ui.dropna(subset=["MINIMO","MAXIMO","CAPACIDAD"]).copy()
            params2 = rebuild_all_params(params2)
        except Exception as e:
            st.error(f"Error al preparar: {e}")
            st.stop()

    prog = st.progress(0, text="Iniciando loteo…")
    def cb(pct, msg): prog.progress(min(pct, 0.99), text=msg)

    try:
        df_det, df_res, df_exc, df_par = run_loteo(df_data2, df_cap2, params2, progress_callback=cb)
        reports = build_reports(df_data2, df_cap2, df_det, df_res)
        prog.progress(1.0, text="¡Loteo completado!")
    except Exception as e:
        st.error(f"Error en loteo: {e}")
        st.stop()

    result = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "label": datetime.now().strftime("%H:%M:%S"),
        "detalle": df_det, "resumen": df_res,
        "excedentes": df_exc, "params_out": df_par,
        "reports": reports,
    }
    st.session_state.last_result = result
    hist = st.session_state.run_history
    hist.append(result); 
    if len(hist) > 5: hist.pop(0)
    st.session_state.run_history = hist
    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
#  RESULTADOS
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.last_result is None:
    st.stop()

res     = st.session_state.last_result
df_det  = res["detalle"]
df_res  = res["resumen"]
df_exc  = res["excedentes"]
reports = res["reports"]
lnk_df  = reports.get("LNK_COMPLETITUD", pd.DataFrame())

# KPIs
st.divider()
st.markdown("### 📊 Resultados de la Corrida")
st.caption(f"Corrida: {res['ts']}")

total_lotes    = len(df_res)
total_lbs_asig = df_det["LBS_ASIGNADAS"].sum() if not df_det.empty else 0
total_lbs_exc  = df_exc["LBS_RESTANTES"].sum() if not df_exc.empty else 0
completitud_pct= (lnk_df["ESTADO"].isin(["COMPLETO","COMPLETO (SCRAP)"]).sum()/len(lnk_df)*100) if not lnk_df.empty else 0
cap_perdida    = df_res["CAPACIDAD_PERDIDA"].sum() if not df_res.empty else 0

k1,k2,k3,k4,k5 = st.columns(5)
k1.metric("Lotes generados",  f"{total_lotes:,}")
k2.metric("LBS asignadas",    fmt_lbs(total_lbs_asig))
k3.metric("LBS excedentes",   fmt_lbs(total_lbs_exc))
k4.metric("LNKs completos",   f"{completitud_pct:.1f}%")
k5.metric("Capacidad perdida",fmt_lbs(cap_perdida))

# Tabs de resultados
tab_g, tab_d, tab_r, tab_l, tab_c, tab_e = st.tabs([
    "📊 Gráficas", "📋 Detalle Lotes", "📄 Resumen",
    "🔍 Decision Log", "🔁 Comparar Corridas", "⚠️ Excedentes",
])

with tab_g:
    cap_df  = reports.get("CAPACIDAD_X_CATEG", pd.DataFrame())
    prio_df = reports.get("PRIORIDAD_VS_ASIG", pd.DataFrame())
    c1,c2 = st.columns(2)
    with c1: st.plotly_chart(chart_capacidad_barras(cap_df), use_container_width=True)
    with c2: st.plotly_chart(chart_bloques_donut(prio_df),   use_container_width=True)
    c3,c4 = st.columns(2)
    with c3: st.plotly_chart(chart_heatmap_capacidad(cap_df),  use_container_width=True)
    with c4: st.plotly_chart(chart_completitud_lnk(lnk_df),    use_container_width=True)

with tab_d:
    if df_det.empty:
        st.info("Sin lotes generados.")
    else:
        dc1,dc2,dc3 = st.columns(3)
        det_mix    = dc1.multiselect("MIX",    sorted(df_det["MIX"].unique()), key="det_mix")
        det_regla  = dc2.multiselect("Regla",  sorted(df_det["APLICA_REGLA"].unique()), key="det_regla")
        det_bloque = dc3.multiselect("Bloque", sorted(df_det["BLOQUE"].unique()), key="det_bloque")
        filt = df_det.copy()
        if det_mix:    filt = filt[filt["MIX"].isin(det_mix)]
        if det_regla:  filt = filt[filt["APLICA_REGLA"].isin(det_regla)]
        if det_bloque: filt = filt[filt["BLOQUE"].isin(det_bloque)]
        st.dataframe(filt, use_container_width=True, height=420)
        st.caption(f"{len(filt):,} filas")

with tab_r:
    if df_res.empty: st.info("Sin resumen.")
    else:
        st.dataframe(df_res, use_container_width=True, height=420)
        st.subheader("Capacidad por Categoría")
        st.dataframe(reports.get("CAPACIDAD_X_CATEG", pd.DataFrame()), use_container_width=True)

with tab_l:
    dlog = reports.get("DECISION_LOG", pd.DataFrame())
    if dlog.empty: st.info("Sin log.")
    else:
        lc1,lc2,lc3 = st.columns(3)
        log_lnk    = lc1.text_input("LNK contiene", key="log_lnk")
        log_regla  = lc2.multiselect("Regla",  sorted(dlog["APLICA_REGLA"].unique()) if "APLICA_REGLA" in dlog.columns else [], key="log_regla")
        log_bloque = lc3.multiselect("Bloque", sorted(dlog["BLOQUE"].unique()) if "BLOQUE" in dlog.columns else [], key="log_bloque")
        lf = dlog.copy()
        if log_lnk:    lf = lf[lf["LNK"].str.contains(log_lnk, case=False, na=False)]
        if log_regla:  lf = lf[lf["APLICA_REGLA"].isin(log_regla)]
        if log_bloque: lf = lf[lf["BLOQUE"].isin(log_bloque)]
        st.dataframe(lf, use_container_width=True, height=440)
        st.caption(f"{len(lf):,} registros")

with tab_c:
    hist = st.session_state.run_history
    if len(hist) < 2: st.info("Corre al menos 2 corridas para comparar.")
    else:
        rows = []
        for i, r in enumerate(hist):
            d = r["detalle"]; s = r["resumen"]; exc = r["excedentes"]
            lnk_c = r["reports"].get("LNK_COMPLETITUD", pd.DataFrame())
            rows.append({
                "Corrida": f"#{i+1} – {r['label']}",
                "Lotes": len(s),
                "LBS Asignadas": fmt_lbs(d["LBS_ASIGNADAS"].sum() if not d.empty else 0),
                "LBS Excedentes": fmt_lbs(exc["LBS_RESTANTES"].sum() if not exc.empty else 0),
                "Cap. Perdida": fmt_lbs(s["CAPACIDAD_PERDIDA"].sum() if not s.empty else 0),
                "LNKs Completos %": f"{(lnk_c['ESTADO'].isin(['COMPLETO','COMPLETO (SCRAP)']).sum()/len(lnk_c)*100) if not lnk_c.empty else 0:.1f}%",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.subheader("Descargar corridas")
        for i, r in enumerate(hist):
            fn = f"RESULTADOS_LOTES_{r['ts'].replace(':','').replace(' ','_').replace('-','')}.xlsx"
            st.download_button(f"⬇ Corrida #{i+1} – {r['label']}", data=export_excel(r),
                               file_name=fn, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"dl_hist_{i}")

with tab_e:
    if df_exc.empty: st.success("✅ Sin excedentes.")
    else:
        st.warning(f"⚠️ {len(df_exc):,} filas sin asignar.")
        st.dataframe(df_exc, use_container_width=True, height=420)

# Descarga global
st.divider()
ts_clean = res["ts"].replace(":","").replace(" ","_").replace("-","")
st.download_button(
    "⬇  Descargar Excel completo (última corrida)",
    data=export_excel(res),
    file_name=f"RESULTADOS_LOTES_{ts_clean}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)
