"""
NV2 Loteo Tintorería – Streamlit App v2
Migrado desde Google Colab. Motor idéntico, UI nueva.
"""

import io, sys, os, json, base64
from datetime import datetime
from copy import deepcopy

import pandas as pd
import numpy as np
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
    initial_sidebar_state="expanded",
)

# ── Session state init ─────────────────────────────────────────────────────
DEFAULTS = {
    "run_history": [],
    "last_result": None,
    "df_data": None,
    "df_cap": None,
    "params": None,
    "raw_file_bytes": None,
    "raw_file_name": None,
    # editable tables (DataFrames)
    "tbl_capacidades": None,
    "tbl_reglas_anchos": None,
    "tbl_restricciones_ancho": None,
    "tbl_restricciones_color": None,
    "tbl_restricciones_familia": None,
    "tbl_combinaciones": None,
    # profiles
    "profiles": {},          # name -> profile dict
    "active_profile": None,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Helpers ────────────────────────────────────────────────────────────────
def fmt_lbs(v):
    return f"{v:,.0f}"


def empty_capacidades():
    return pd.DataFrame(columns=["CATEGORIA","MINIMO","MAXIMO","CAPACIDAD","MIX"])


def empty_reglas_anchos():
    return pd.DataFrame(columns=["ANCHO_1","ANCHO_2","CAPACIDAD_PRIORIDAD_1","CAPACIDAD_PRIORIDAD_2","CAPACIDAD_PRIORIDAD_3"])


def empty_restricciones_ancho():
    return pd.DataFrame(columns=["STYLE","LIMITE_ANCHO","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"])


def empty_restricciones_color():
    return pd.DataFrame(columns=["COLOR_R","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"])


def empty_restricciones_familia():
    return pd.DataFrame(columns=["FAMILIA","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3","PRIORIDAD_4"])


def empty_combinaciones():
    return pd.DataFrame(columns=["PRIORIDAD_1","PRIORIDAD_2"])


def df_to_json_safe(df):
    if df is None or df.empty:
        return []
    return df.where(pd.notna(df), None).to_dict(orient="records")


def json_safe_to_df(records, empty_fn):
    if not records:
        return empty_fn()
    return pd.DataFrame(records)


def export_excel(result: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        result["detalle"].to_excel(writer, index=False, sheet_name="DETALLE_LOTES")
        result["resumen"].to_excel(writer, index=False, sheet_name="RESUMEN_LOTES")
        result["excedentes"].to_excel(writer, index=False, sheet_name="EXCEDENTES")
        result["params_out"].to_excel(writer, index=False, sheet_name="PARAMETROS")
        for key, df in result["reports"].items():
            if not df.empty:
                df.to_excel(writer, index=False, sheet_name=key[:31])
    return buf.getvalue()


def build_profile_dict(overrides, cfg_tables):
    """Serialize current config to a JSON-safe dict."""
    profile = {"overrides": overrides, "tables": {}}
    for k, df in cfg_tables.items():
        profile["tables"][k] = df_to_json_safe(df)
    # also store raw file bytes as base64 if available
    if st.session_state.raw_file_bytes:
        profile["file_b64"] = base64.b64encode(st.session_state.raw_file_bytes).decode()
        profile["file_name"] = st.session_state.raw_file_name
    return profile


def apply_profile(profile: dict):
    """Load a profile dict back into session state."""
    st.session_state.tbl_capacidades = json_safe_to_df(profile["tables"].get("capacidades"), empty_capacidades)
    st.session_state.tbl_reglas_anchos = json_safe_to_df(profile["tables"].get("reglas_anchos"), empty_reglas_anchos)
    st.session_state.tbl_restricciones_ancho = json_safe_to_df(profile["tables"].get("restricciones_ancho"), empty_restricciones_ancho)
    st.session_state.tbl_restricciones_color = json_safe_to_df(profile["tables"].get("restricciones_color"), empty_restricciones_color)
    st.session_state.tbl_restricciones_familia = json_safe_to_df(profile["tables"].get("restricciones_familia"), empty_restricciones_familia)
    st.session_state.tbl_combinaciones = json_safe_to_df(profile["tables"].get("combinaciones"), empty_combinaciones)
    # restore file if embedded
    if "file_b64" in profile:
        raw = base64.b64decode(profile["file_b64"])
        st.session_state.raw_file_bytes = raw
        st.session_state.raw_file_name = profile.get("file_name","archivo.xlsx")
        # reload data
        try:
            df_data, df_cap, params, _ = load_inputs(io.BytesIO(raw))
            st.session_state.df_data = df_data
            st.session_state.df_cap = df_cap
            st.session_state.params = params
        except Exception:
            pass
    return profile.get("overrides", {})


def load_tables_from_excel(xlsm_path):
    """Extract editable tables from the uploaded Excel."""
    xls = pd.ExcelFile(xlsm_path, engine="openpyxl")

    def safe_read(sheet, empty_fn):
        if sheet in xls.sheet_names:
            df = pd.read_excel(xlsm_path, sheet_name=sheet, engine="openpyxl")
            df.columns = [str(c).strip() for c in df.columns]
            return df
        return empty_fn()

    st.session_state.tbl_capacidades      = safe_read("CAPACIDADES_TINTO",       empty_capacidades)
    st.session_state.tbl_reglas_anchos    = safe_read("REGLAS_ANCHOS_COMBINADOS", empty_reglas_anchos)
    st.session_state.tbl_restricciones_ancho   = safe_read("RESTRICCIONES_ANCHO",  empty_restricciones_ancho)
    st.session_state.tbl_restricciones_color   = safe_read("RESTRICCIONES_COLOR",  empty_restricciones_color)
    st.session_state.tbl_restricciones_familia = safe_read("RESTRICCIONES_FAMILIA",empty_restricciones_familia)
    st.session_state.tbl_combinaciones    = safe_read("COMBINACIONES_PRIORIDAD",   empty_combinaciones)


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("🧶 NV2 Loteo")
    st.caption("Tintorería · Planificación de lotes")
    st.divider()

    # ── 1. Carga de archivo ────────────────────────────────────────────────
    st.subheader("📁 Archivo")
    uploaded = st.file_uploader(
        "Sube tu archivo Excel (.xlsx / .xlsm)",
        type=["xlsx", "xlsm"],
        key="file_upload",
    )

    if uploaded is not None:
        file_bytes = uploaded.read()
        if file_bytes != st.session_state.raw_file_bytes:
            with st.spinner("Leyendo archivo…"):
                try:
                    buf = io.BytesIO(file_bytes)
                    df_data, df_cap, params, hdr_row = load_inputs(buf)
                    st.session_state.df_data = df_data
                    st.session_state.df_cap  = df_cap
                    st.session_state.params  = params
                    st.session_state.raw_file_bytes = file_bytes
                    st.session_state.raw_file_name  = uploaded.name
                    load_tables_from_excel(io.BytesIO(file_bytes))
                    st.success(f"✅ {len(df_data):,} filas · header fila {hdr_row+1}")
                except Exception as e:
                    st.error(f"Error: {e}")
                    st.stop()

    st.divider()

    # ── 2. Perfiles ────────────────────────────────────────────────────────
    st.subheader("💾 Perfiles de Configuración")

    profiles = st.session_state.profiles
    profile_names = list(profiles.keys())

    # Cargar perfil de sesión
    if profile_names:
        sel_profile = st.selectbox("Cargar perfil de sesión", ["— seleccionar —"] + profile_names, key="sel_profile")
        if sel_profile != "— seleccionar —" and st.button("📥 Aplicar perfil", use_container_width=True):
            apply_profile(profiles[sel_profile])
            st.success(f"Perfil '{sel_profile}' aplicado")
            st.rerun()

    # Importar JSON
    json_upload = st.file_uploader("Importar perfil (.json)", type=["json"], key="json_upload")
    if json_upload is not None:
        try:
            loaded = json.load(json_upload)
            pname = json_upload.name.replace(".json","")
            profiles[pname] = loaded
            st.session_state.profiles = profiles
            st.success(f"Perfil '{pname}' importado")
        except Exception as e:
            st.error(f"Error al importar: {e}")

    st.divider()

    # ── 3. CONFIG editable ─────────────────────────────────────────────────
    p = st.session_state.params or {}

    if p:
        st.subheader("⚙️ Parámetros CONFIG")

        with st.expander("Anchos & SKU", expanded=True):
            min_diff      = st.number_input("MIN_DIFF",   value=float(p.get("MIN_DIFF",1)),   step=0.5, format="%.1f")
            max_diff      = st.number_input("MAX_DIFF",   value=float(p.get("MAX_DIFF",6)),   step=0.5, format="%.1f")
            max_widths    = st.number_input("MAX_WIDTHS", value=int(p.get("MAX_WIDTHS",3)),   min_value=1, step=1)
            max_sku       = st.number_input("MAX_SKU",    value=int(p.get("MAX_SKU",8)),      min_value=1, step=1)
            widths_target = st.text_input("WIDTHS_TARGET_ORDER", value=str(p.get("WIDTHS_TARGET_ORDER","2>3>1")))
            req_strict    = st.checkbox("REQUIRE_WIDTHS_STRICT", value=bool(int(p.get("REQUIRE_WIDTHS_STRICT",1))))

        with st.expander("Splits & Scrap"):
            split_default = st.number_input("SPLIT_MIN_LBS_DEFAULT", value=float(p.get("SPLIT_MIN_LBS_DEFAULT",100)), step=10.0)
            split_ancho18 = st.number_input("SPLIT_MIN_LBS_ANCHO18", value=float(p.get("SPLIT_MIN_LBS_ANCHO18",200)), step=10.0)
            scrap_rem     = st.checkbox("SCRAP_REMAINDER_BELOW_SPLIT_MIN", value=bool(int(p.get("SCRAP_REMAINDER_BELOW_SPLIT_MIN",1))))

        with st.expander("Scoring & Beam"):
            beam_w       = st.number_input("BEAM_WIDTH",        value=int(p.get("BEAM_WIDTH",3)),   min_value=1, step=1)
            w_fill       = st.number_input("W_FILL",            value=float(p.get("W_FILL",5.0)),   step=0.5)
            w_cap_loss   = st.number_input("W_CAP_LOSS",        value=float(p.get("W_CAP_LOSS",2.0)),step=0.5)
            w_width_pref = st.number_input("W_WIDTH_PREF",      value=float(p.get("W_WIDTH_PREF",2.0)),step=0.5)
            w_1100       = st.number_input("W_1100_WIDTHS_STRICT",value=float(p.get("W_1100_WIDTHS_STRICT",10.0)),step=1.0)
            pref_list_str= st.text_input("WIDTH_PREF_LIST",     value=",".join(str(x) for x in p.get("WIDTH_PREF_LIST",[2,3,1,4,5,6])))

        with st.expander("Reglas & Flags"):
            overshoot      = st.checkbox("OVERSHOOT_ENABLE",              value=bool(int(p.get("OVERSHOOT_ENABLE",1))))
            undershoot     = st.checkbox("UNDERSHOOT_ENABLE",             value=bool(int(p.get("UNDERSHOOT_ENABLE",1))))
            upgrade_cat    = st.checkbox("UPGRADE_CATEGORIA",             value=bool(int(p.get("UPGRADE_CATEGORIA",1))))
            try_all        = st.checkbox("TRY_ALL_PRIORITIES",            value=bool(int(p.get("TRY_ALL_PRIORITIES",1))))
            apply_bleach   = st.checkbox("APPLY_RULES_BLEACH",            value=bool(int(p.get("APPLY_RULES_BLEACH",0))))
            ancho18_spill  = st.checkbox("ANCHO18_ALLOW_SPILLOVER_2600",  value=bool(int(p.get("ANCHO18_ALLOW_SPILLOVER_2600",0))))
            rule_order     = st.text_input("RULE_ORDER", value=str(p.get("RULE_ORDER","ANCHO18>COMBO_ANCHOS>COLOR_R>FAMILIA>DEFAULT")))
            priority_order = st.text_input("PRIORITY_ORDER", value=str(p.get("PRIORITY_ORDER","")))
            ancho18_max_dye= st.text_input("ANCHO18_ALLOWED_MAX_DYE",
                                           value=",".join(str(int(x)) for x in p.get("ANCHO18_ALLOWED_MAX_DYE",{2200,1100})))

        overrides = {
            "MIN_DIFF": min_diff, "MAX_DIFF": max_diff,
            "MAX_WIDTHS": max_widths, "MAX_SKU": max_sku,
            "WIDTHS_TARGET_ORDER": widths_target,
            "REQUIRE_WIDTHS_STRICT": int(req_strict),
            "SPLIT_MIN_LBS_DEFAULT": split_default,
            "SPLIT_MIN_LBS_ANCHO18": split_ancho18,
            "SCRAP_REMAINDER_BELOW_SPLIT_MIN": int(scrap_rem),
            "BEAM_WIDTH": beam_w, "W_FILL": w_fill, "W_CAP_LOSS": w_cap_loss,
            "W_WIDTH_PREF": w_width_pref, "W_1100_WIDTHS_STRICT": w_1100,
            "WIDTH_PREF_LIST": pref_list_str,
            "OVERSHOOT_ENABLE": int(overshoot), "UNDERSHOOT_ENABLE": int(undershoot),
            "UPGRADE_CATEGORIA": int(upgrade_cat), "TRY_ALL_PRIORITIES": int(try_all),
            "APPLY_RULES_BLEACH": int(apply_bleach),
            "ANCHO18_ALLOW_SPILLOVER_2600": int(ancho18_spill),
            "RULE_ORDER": rule_order, "PRIORITY_ORDER": priority_order,
            "ANCHO18_ALLOWED_MAX_DYE": ancho18_max_dye,
        }

        st.divider()

        # ── Guardar perfil ─────────────────────────────────────────────────
        st.subheader("💾 Guardar perfil actual")
        new_profile_name = st.text_input("Nombre del perfil", placeholder="ej. Semana23_DYE", key="new_profile_name")
        col_save, col_export = st.columns(2)

        cfg_tables = {
            "capacidades": st.session_state.tbl_capacidades or empty_capacidades(),
            "reglas_anchos": st.session_state.tbl_reglas_anchos or empty_reglas_anchos(),
            "restricciones_ancho": st.session_state.tbl_restricciones_ancho or empty_restricciones_ancho(),
            "restricciones_color": st.session_state.tbl_restricciones_color or empty_restricciones_color(),
            "restricciones_familia": st.session_state.tbl_restricciones_familia or empty_restricciones_familia(),
            "combinaciones": st.session_state.tbl_combinaciones or empty_combinaciones(),
        }

        with col_save:
            if st.button("💾 En sesión", use_container_width=True):
                if new_profile_name.strip():
                    profiles[new_profile_name.strip()] = build_profile_dict(overrides, cfg_tables)
                    st.session_state.profiles = profiles
                    st.success("Guardado")
                else:
                    st.warning("Ingresa un nombre")

        with col_export:
            profile_json = json.dumps(build_profile_dict(overrides, cfg_tables), indent=2, ensure_ascii=False)
            fn = (new_profile_name.strip() or "perfil") + ".json"
            st.download_button("📤 JSON", data=profile_json, file_name=fn,
                               mime="application/json", use_container_width=True)

        st.divider()
        run_btn = st.button("▶ Correr Loteo", type="primary", use_container_width=True)
    else:
        overrides = {}
        run_btn = False
        cfg_tables = {}


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN AREA
# ══════════════════════════════════════════════════════════════════════════════
st.title("🧶 NV2 Loteo Tintorería")

if st.session_state.df_data is None:
    st.info("👈 Sube un archivo Excel en el panel izquierdo para comenzar.")
    st.stop()

df_data = st.session_state.df_data
df_cap  = st.session_state.df_cap

# ── Vista previa DATA ──────────────────────────────────────────────────────
with st.expander("🔍 Vista previa de DATA", expanded=False):
    col_f, col_n = st.columns([3,1])
    with col_f:
        mix_opts = ["Todos"] + sorted(df_data["MIX"].unique().tolist())
        mix_sel = st.selectbox("Filtrar por MIX", mix_opts, key="prev_mix")
    with col_n:
        n_rows = st.number_input("Filas", min_value=5, max_value=500, value=50, step=10, key="prev_n")
    prev_df = df_data if mix_sel=="Todos" else df_data[df_data["MIX"]==mix_sel]
    st.dataframe(prev_df.head(n_rows), use_container_width=True, height=260)
    st.caption(f"Total: {len(df_data):,} filas | LBS: {fmt_lbs(df_data['TOTAL'].sum())}")

# ══════════════════════════════════════════════════════════════════════════════
#  PESTAÑA CONFIGURACIÓN DE TABLAS
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("🗂️ Configuración de Tablas")

tc1, tc2, tc3, tc4, tc5, tc6 = st.tabs([
    "📦 Capacidades Tinto",
    "🔗 Reglas Anchos Combinados",
    "📐 Restricciones Ancho",
    "🎨 Restricciones Color",
    "👨‍👩‍👧 Restricciones Familia",
    "⚖️ Combinaciones Prioridad",
])

# ── Tab: CAPACIDADES_TINTO ─────────────────────────────────────────────────
with tc1:
    st.markdown("**Categorías de capacidad** — define rangos, capacidad total y MIX. "
                "Edita directamente en la tabla o agrega/elimina filas.")

    if st.session_state.tbl_capacidades is None:
        st.session_state.tbl_capacidades = empty_capacidades()

    cap_edited = st.data_editor(
        st.session_state.tbl_capacidades,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_cap",
        column_config={
            "CATEGORIA":  st.column_config.TextColumn("Categoría", width="small"),
            "MINIMO":     st.column_config.NumberColumn("Mínimo LBS", min_value=0, format="%d"),
            "MAXIMO":     st.column_config.NumberColumn("Máximo LBS", min_value=0, format="%d"),
            "CAPACIDAD":  st.column_config.NumberColumn("Capacidad Total LBS", min_value=0, format="%d"),
            "MIX":        st.column_config.SelectboxColumn("MIX", options=["DYE","BLEACH"]),
        },
    )
    st.session_state.tbl_capacidades = cap_edited

    # Resumen visual
    if not cap_edited.empty:
        total_cap = pd.to_numeric(cap_edited["CAPACIDAD"], errors="coerce").sum()
        st.caption(f"📊 {len(cap_edited)} categorías | Capacidad total: {fmt_lbs(total_cap)} LBS")

# ── Tab: REGLAS_ANCHOS_COMBINADOS ──────────────────────────────────────────
with tc2:
    st.markdown("**Combinaciones de anchos permitidas** — define qué pares de anchos pueden ir juntos "
                "y en qué tamaños (prioridades de capacidad). Si la tabla está vacía, las combinaciones son libres.")

    if st.session_state.tbl_reglas_anchos is None:
        st.session_state.tbl_reglas_anchos = empty_reglas_anchos()

    ra_edited = st.data_editor(
        st.session_state.tbl_reglas_anchos,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_ra",
        column_config={
            "ANCHO_1":               st.column_config.NumberColumn("Ancho 1", format="%.1f"),
            "ANCHO_2":               st.column_config.NumberColumn("Ancho 2", format="%.1f"),
            "CAPACIDAD_PRIORIDAD_1": st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
            "CAPACIDAD_PRIORIDAD_2": st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
            "CAPACIDAD_PRIORIDAD_3": st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
        },
    )
    st.session_state.tbl_reglas_anchos = ra_edited

    if ra_edited.empty:
        st.info("ℹ️ Tabla vacía → combinaciones de anchos libres (sin restricción de pares).")

# ── Tab: RESTRICCIONES_ANCHO ───────────────────────────────────────────────
with tc3:
    st.markdown("**Restricciones por STYLE y ancho límite** — si un lote contiene ese STYLE "
                "y tiene un ancho ≤ LIMITE_ANCHO, prioriza las capacidades indicadas.")

    if st.session_state.tbl_restricciones_ancho is None:
        st.session_state.tbl_restricciones_ancho = empty_restricciones_ancho()

    ras_edited = st.data_editor(
        st.session_state.tbl_restricciones_ancho,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_ras",
        column_config={
            "STYLE":        st.column_config.TextColumn("STYLE", width="medium"),
            "LIMITE_ANCHO": st.column_config.NumberColumn("Límite Ancho", format="%.1f"),
            "PRIORIDAD_1":  st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
            "PRIORIDAD_2":  st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
            "PRIORIDAD_3":  st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
        },
    )
    st.session_state.tbl_restricciones_ancho = ras_edited

    # Quick lookup
    if not ras_edited.empty:
        style_search = st.text_input("🔍 Buscar STYLE", key="style_search")
        if style_search:
            found = ras_edited[ras_edited["STYLE"].str.upper().str.contains(style_search.upper(), na=False)]
            if not found.empty:
                st.dataframe(found, use_container_width=True)
            else:
                st.caption("Sin coincidencias.")

# ── Tab: RESTRICCIONES_COLOR ───────────────────────────────────────────────
with tc4:
    st.markdown("**Restricciones por COLOR_R** — según la categoría de color en DATA, "
                "prioriza los tamaños de lote en el orden indicado.")

    if st.session_state.tbl_restricciones_color is None:
        st.session_state.tbl_restricciones_color = empty_restricciones_color()

    rc_edited = st.data_editor(
        st.session_state.tbl_restricciones_color,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_rc",
        column_config={
            "COLOR_R":     st.column_config.TextColumn("COLOR_R", width="medium"),
            "PRIORIDAD_1": st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
            "PRIORIDAD_2": st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
            "PRIORIDAD_3": st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
        },
    )
    st.session_state.tbl_restricciones_color = rc_edited

# ── Tab: RESTRICCIONES_FAMILIA ─────────────────────────────────────────────
with tc5:
    st.markdown("**Restricciones por FAMILIA** — si la familia del SKU aparece en esta tabla, "
                "se respeta el orden de prioridades. Si no está, el loteo es libre.")

    if st.session_state.tbl_restricciones_familia is None:
        st.session_state.tbl_restricciones_familia = empty_restricciones_familia()

    rf_edited = st.data_editor(
        st.session_state.tbl_restricciones_familia,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_rf",
        column_config={
            "FAMILIA":     st.column_config.TextColumn("FAMILIA", width="medium"),
            "PRIORIDAD_1": st.column_config.NumberColumn("Prioridad 1 (LBS)", format="%d"),
            "PRIORIDAD_2": st.column_config.NumberColumn("Prioridad 2 (LBS)", format="%d"),
            "PRIORIDAD_3": st.column_config.NumberColumn("Prioridad 3 (LBS)", format="%d"),
            "PRIORIDAD_4": st.column_config.NumberColumn("Prioridad 4 (LBS)", format="%d"),
        },
    )
    st.session_state.tbl_restricciones_familia = rf_edited

# ── Tab: COMBINACIONES_PRIORIDAD ───────────────────────────────────────────
with tc6:
    st.markdown("**Mezcla de bloques de prioridad** — por defecto NO se mezclan prioridades distintas. "
                "Agrega aquí los pares que SÍ pueden coexistir en un mismo lote.")

    if st.session_state.tbl_combinaciones is None:
        st.session_state.tbl_combinaciones = empty_combinaciones()

    BLOQUES = ["VENCIDOS","AHEAD","AHEAD2","OTROS"]
    comb_edited = st.data_editor(
        st.session_state.tbl_combinaciones,
        num_rows="dynamic",
        use_container_width=True,
        key="editor_comb",
        column_config={
            "PRIORIDAD_1": st.column_config.SelectboxColumn("Bloque 1", options=BLOQUES),
            "PRIORIDAD_2": st.column_config.SelectboxColumn("Bloque 2", options=BLOQUES),
        },
    )
    st.session_state.tbl_combinaciones = comb_edited

    if comb_edited.empty:
        st.warning("⚠️ Tabla vacía → bloques de prioridad NO se mezclan (cada lote solo tiene un bloque).")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
#  CORRER LOTEO
# ══════════════════════════════════════════════════════════════════════════════
if run_btn:
    with st.spinner("Aplicando configuración…"):
        try:
            buf = io.BytesIO(st.session_state.raw_file_bytes)
            df_data2, df_cap2, params2, _ = load_inputs(buf, param_overrides=overrides)

            # Override tables with UI-edited versions
            cap_ui = st.session_state.tbl_capacidades
            if cap_ui is not None and not cap_ui.empty:
                for c in ["MINIMO","MAXIMO","CAPACIDAD"]:
                    cap_ui[c] = pd.to_numeric(cap_ui[c], errors="coerce")
                df_cap2 = cap_ui.dropna(subset=["MINIMO","MAXIMO","CAPACIDAD"]).copy()

            # Rebuild MIX_ALLOWED from UI table
            comb_ui = st.session_state.tbl_combinaciones
            if comb_ui is not None and not comb_ui.empty:
                allowed_pairs = set()
                for _, r in comb_ui.iterrows():
                    a = str(r.get("PRIORIDAD_1","")).strip()
                    b = str(r.get("PRIORIDAD_2","")).strip()
                    if a and b:
                        allowed_pairs.add((a,b)); allowed_pairs.add((b,a))
                params2["MIX_ALLOWED"] = allowed_pairs

            # Rebuild RESTRICCIONES from UI tables
            def rebuild_restricciones_familia(df_rf):
                out = {}
                if df_rf is None or df_rf.empty: return out
                pcols = [c for c in df_rf.columns if c.upper().startswith("PRIORIDAD")]
                for _, r in df_rf.iterrows():
                    f = str(r.get("FAMILIA","")).strip().upper()
                    if not f: continue
                    caps = [float(r[pc]) for pc in pcols if pd.notna(r.get(pc))]
                    if caps: out[f] = caps
                return out

            def rebuild_restricciones_color(df_rc):
                out = {}
                if df_rc is None or df_rc.empty: return out
                for _, r in df_rc.iterrows():
                    c = str(r.get("COLOR_R","")).strip().upper()
                    v = r.get("PRIORIDAD_1", None)
                    out[c] = float(v) if pd.notna(v) else None
                return out

            def rebuild_restricciones_ancho(df_ras):
                out = {}
                if df_ras is None or df_ras.empty: return out
                pcols = [c for c in df_ras.columns if c.upper().startswith("PRIORIDAD")]
                for _, r in df_ras.iterrows():
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

            def rebuild_reglas_anchos(df_ra):
                out = []
                if df_ra is None or df_ra.empty: return out
                pcols = [c for c in df_ra.columns if c.upper().startswith("CAPACIDAD_PRIORIDAD")]
                for _, r in df_ra.iterrows():
                    try:
                        a1 = float(r.get("ANCHO_1",0)); a2 = float(r.get("ANCHO_2",0))
                    except: continue
                    caps = [float(r[pc]) for pc in pcols if pd.notna(r.get(pc))]
                    if caps: out.append({"a1": a1, "a2": a2, "prioridades": caps})
                return out

            params2["RESTRICCIONES_FAMILIA"]    = rebuild_restricciones_familia(st.session_state.tbl_restricciones_familia)
            params2["RESTRICCIONES_COLOR"]       = rebuild_restricciones_color(st.session_state.tbl_restricciones_color)
            params2["RESTRICCIONES_ANCHO"]       = rebuild_restricciones_ancho(st.session_state.tbl_restricciones_ancho)
            params2["REGLAS_ANCHOS_COMBINADOS"]  = rebuild_reglas_anchos(st.session_state.tbl_reglas_anchos)

        except Exception as e:
            st.error(f"Error al preparar parámetros: {e}")
            st.stop()

    progress_bar = st.progress(0, text="Iniciando loteo…")

    def progress_cb(pct, msg):
        progress_bar.progress(min(pct, 0.99), text=msg)

    try:
        df_det, df_res, df_exc, df_par = run_loteo(df_data2, df_cap2, params2, progress_callback=progress_cb)
        reports = build_reports(df_data2, df_cap2, df_det, df_res)
        progress_bar.progress(1.0, text="¡Loteo completado!")
    except Exception as e:
        st.error(f"Error en el loteo: {e}")
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
    hist.append(result)
    if len(hist) > 5: hist.pop(0)
    st.session_state.run_history = hist
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  RESULTADOS
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.last_result is None:
    st.caption("Sube un archivo y presiona **▶ Correr Loteo** para ver resultados.")
    st.stop()

res     = st.session_state.last_result
df_det  = res["detalle"]
df_res  = res["resumen"]
df_exc  = res["excedentes"]
reports = res["reports"]

# KPIs
total_lotes    = len(df_res)
total_lbs_asig = df_det["LBS_ASIGNADAS"].sum() if not df_det.empty else 0
total_lbs_exc  = df_exc["LBS_RESTANTES"].sum() if not df_exc.empty else 0
lnk_df         = reports.get("LNK_COMPLETITUD", pd.DataFrame())
completitud_pct= (lnk_df["ESTADO"].isin(["COMPLETO","COMPLETO (SCRAP)"]).sum()/len(lnk_df)*100) if not lnk_df.empty else 0
cap_perdida    = df_res["CAPACIDAD_PERDIDA"].sum() if not df_res.empty else 0

k1,k2,k3,k4,k5 = st.columns(5)
k1.metric("Lotes generados",  f"{total_lotes:,}")
k2.metric("LBS asignadas",    fmt_lbs(total_lbs_asig))
k3.metric("LBS excedentes",   fmt_lbs(total_lbs_exc))
k4.metric("LNKs completos",   f"{completitud_pct:.1f}%")
k5.metric("Capacidad perdida",fmt_lbs(cap_perdida))
st.caption(f"Corrida: {res['ts']}")
st.divider()

# Tabs resultados
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
        filtered = df_det.copy()
        if det_mix:    filtered = filtered[filtered["MIX"].isin(det_mix)]
        if det_regla:  filtered = filtered[filtered["APLICA_REGLA"].isin(det_regla)]
        if det_bloque: filtered = filtered[filtered["BLOQUE"].isin(det_bloque)]
        st.dataframe(filtered, use_container_width=True, height=420)
        st.caption(f"{len(filtered):,} filas")

with tab_r:
    if df_res.empty:
        st.info("Sin resumen.")
    else:
        st.dataframe(df_res, use_container_width=True, height=420)
        st.subheader("Capacidad por Categoría")
        st.dataframe(reports.get("CAPACIDAD_X_CATEG", pd.DataFrame()), use_container_width=True)

with tab_l:
    dlog = reports.get("DECISION_LOG", pd.DataFrame())
    if dlog.empty:
        st.info("Sin log.")
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
    if len(hist) < 2:
        st.info("Corre al menos **2 corridas** para comparar.")
    else:
        rows = []
        for i, r in enumerate(hist):
            d   = r["detalle"];  s = r["resumen"];  exc = r["excedentes"]
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
            xlsx = export_excel(r)
            fn   = f"RESULTADOS_LOTES_{r['ts'].replace(':','').replace(' ','_').replace('-','')}.xlsx"
            st.download_button(f"⬇ Corrida #{i+1} – {r['label']}", data=xlsx,
                               file_name=fn, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"dl_hist_{i}")

with tab_e:
    if df_exc.empty:
        st.success("✅ Sin excedentes.")
    else:
        st.warning(f"⚠️ {len(df_exc):,} filas sin asignar.")
        st.dataframe(df_exc, use_container_width=True, height=420)

# Global download
st.divider()
xlsx_bytes = export_excel(res)
ts_clean   = res["ts"].replace(":","").replace(" ","_").replace("-","")
st.download_button(
    "⬇ Descargar Excel completo (última corrida)",
    data=xlsx_bytes,
    file_name=f"RESULTADOS_LOTES_{ts_clean}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)
