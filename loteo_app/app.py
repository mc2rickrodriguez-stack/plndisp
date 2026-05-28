"""
NV2 Loteo Tintorería – Streamlit App
Migrado desde Google Colab. Motor idéntico, UI nueva.
"""

import io
import sys
import os
from datetime import datetime

import pandas as pd
import streamlit as st

# ── path setup so engine/ and ui/ are importable ──────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from engine.loader import load_inputs
from engine.loteo import run_loteo, build_reports
from ui.charts import (
    chart_capacidad_barras,
    chart_bloques_donut,
    chart_heatmap_capacidad,
    chart_completitud_lnk,
)

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NV2 Loteo Tintorería",
    page_icon="🧶",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state init ─────────────────────────────────────────────────────
if "run_history" not in st.session_state:
    st.session_state.run_history = []   # list of result dicts (max 5)
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "df_data" not in st.session_state:
    st.session_state.df_data = None
if "df_cap" not in st.session_state:
    st.session_state.df_cap = None
if "params" not in st.session_state:
    st.session_state.params = None


# ── Helpers ────────────────────────────────────────────────────────────────

def fmt_lbs(v):
    return f"{v:,.0f}"


def export_excel(result: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        result["detalle"].to_excel(writer, index=False, sheet_name="DETALLE_LOTES")
        result["resumen"].to_excel(writer, index=False, sheet_name="RESUMEN_LOTES")
        result["excedentes"].to_excel(writer, index=False, sheet_name="EXCEDENTES")
        result["params_out"].to_excel(writer, index=False, sheet_name="PARAMETROS")
        for key, df in result["reports"].items():
            if not df.empty:
                sheet_name = key[:31]
                df.to_excel(writer, index=False, sheet_name=sheet_name)
    return buf.getvalue()


# ── Sidebar ────────────────────────────────────────────────────────────────
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
        with st.spinner("Leyendo archivo…"):
            try:
                df_data, df_cap, params, hdr_row = load_inputs(uploaded)
                st.session_state.df_data = df_data
                st.session_state.df_cap = df_cap
                st.session_state.params = params
                st.success(f"✅ {len(df_data):,} filas cargadas")
            except Exception as e:
                st.error(f"Error al leer el archivo:\n{e}")
                st.stop()

    st.divider()

    # ── 2. CONFIG editable ─────────────────────────────────────────────────
    if st.session_state.params is not None:
        p = st.session_state.params
        st.subheader("⚙️ Parámetros CONFIG")

        with st.expander("Anchos & SKU", expanded=True):
            min_diff = st.number_input("MIN_DIFF", value=float(p["MIN_DIFF"]), step=0.5, format="%.1f")
            max_diff = st.number_input("MAX_DIFF", value=float(p["MAX_DIFF"]), step=0.5, format="%.1f")
            max_widths = st.number_input("MAX_WIDTHS", value=int(p["MAX_WIDTHS"]), min_value=1, max_value=10, step=1)
            max_sku = st.number_input("MAX_SKU", value=int(p["MAX_SKU"]), min_value=1, max_value=20, step=1)
            widths_target = st.text_input("WIDTHS_TARGET_ORDER", value=str(p.get("WIDTHS_TARGET_ORDER", "2>3>4")))
            req_strict = st.checkbox("REQUIRE_WIDTHS_STRICT", value=bool(int(p.get("REQUIRE_WIDTHS_STRICT", 1))))

        with st.expander("Splits & Scrap"):
            split_default = st.number_input("SPLIT_MIN_LBS_DEFAULT", value=float(p.get("SPLIT_MIN_LBS_DEFAULT", 100.0)), step=10.0)
            split_ancho18 = st.number_input("SPLIT_MIN_LBS_ANCHO18", value=float(p.get("SPLIT_MIN_LBS_ANCHO18", 250.0)), step=10.0)
            scrap_rem = st.checkbox("SCRAP_REMAINDER_BELOW_SPLIT_MIN", value=bool(int(p.get("SCRAP_REMAINDER_BELOW_SPLIT_MIN", 1))))

        with st.expander("Scoring & Beam"):
            beam_w = st.number_input("BEAM_WIDTH", value=int(p.get("BEAM_WIDTH", 3)), min_value=1, max_value=10)
            w_fill = st.number_input("W_FILL", value=float(p.get("W_FILL", 5.0)), step=0.5)
            w_cap_loss = st.number_input("W_CAP_LOSS", value=float(p.get("W_CAP_LOSS", 3.0)), step=0.5)
            w_width_pref = st.number_input("W_WIDTH_PREF", value=float(p.get("W_WIDTH_PREF", 2.0)), step=0.5)
            w_1100 = st.number_input("W_1100_WIDTHS_STRICT", value=float(p.get("W_1100_WIDTHS_STRICT", 10.0)), step=1.0)
            pref_list_str = st.text_input("WIDTH_PREF_LIST", value=",".join(str(x) for x in p.get("WIDTH_PREF_LIST", [2, 3, 1, 4, 5, 6])))

        with st.expander("Reglas & Flags"):
            overshoot = st.checkbox("OVERSHOOT_ENABLE", value=bool(int(p.get("OVERSHOOT_ENABLE", 1))))
            undershoot = st.checkbox("UNDERSHOOT_ENABLE", value=bool(int(p.get("UNDERSHOOT_ENABLE", 1))))
            upgrade_cat = st.checkbox("UPGRADE_CATEGORIA", value=bool(int(p.get("UPGRADE_CATEGORIA", 1))))
            try_all = st.checkbox("TRY_ALL_PRIORITIES", value=bool(int(p.get("TRY_ALL_PRIORITIES", 1))))
            apply_bleach = st.checkbox("APPLY_RULES_BLEACH", value=bool(int(p.get("APPLY_RULES_BLEACH", 0))))
            ancho18_spillover = st.checkbox("ANCHO18_ALLOW_SPILLOVER_2600", value=bool(int(p.get("ANCHO18_ALLOW_SPILLOVER_2600", 0))))
            rule_order = st.text_input("RULE_ORDER", value=str(p.get("RULE_ORDER", "ANCHO18>COMBO_ANCHOS>COLOR_R>FAMILIA>DEFAULT")))

        # Build overrides dict
        overrides = {
            "MIN_DIFF": min_diff,
            "MAX_DIFF": max_diff,
            "MAX_WIDTHS": max_widths,
            "MAX_SKU": max_sku,
            "WIDTHS_TARGET_ORDER": widths_target,
            "REQUIRE_WIDTHS_STRICT": int(req_strict),
            "SPLIT_MIN_LBS_DEFAULT": split_default,
            "SPLIT_MIN_LBS_ANCHO18": split_ancho18,
            "SCRAP_REMAINDER_BELOW_SPLIT_MIN": int(scrap_rem),
            "BEAM_WIDTH": beam_w,
            "W_FILL": w_fill,
            "W_CAP_LOSS": w_cap_loss,
            "W_WIDTH_PREF": w_width_pref,
            "W_1100_WIDTHS_STRICT": w_1100,
            "WIDTH_PREF_LIST": pref_list_str,
            "OVERSHOOT_ENABLE": int(overshoot),
            "UNDERSHOOT_ENABLE": int(undershoot),
            "UPGRADE_CATEGORIA": int(upgrade_cat),
            "TRY_ALL_PRIORITIES": int(try_all),
            "APPLY_RULES_BLEACH": int(apply_bleach),
            "ANCHO18_ALLOW_SPILLOVER_2600": int(ancho18_spillover),
            "RULE_ORDER": rule_order,
        }

        st.divider()
        run_btn = st.button("▶ Correr Loteo", type="primary", use_container_width=True)
    else:
        run_btn = False
        overrides = {}


# ── Main area ──────────────────────────────────────────────────────────────
st.title("🧶 NV2 Loteo Tintorería")

if st.session_state.df_data is None:
    st.info("👈 Sube un archivo Excel en el panel izquierdo para comenzar.")
    st.stop()

df_data = st.session_state.df_data
df_cap = st.session_state.df_cap

# ── Vista previa DATA ──────────────────────────────────────────────────────
with st.expander("🔍 Vista previa de DATA", expanded=False):
    col_filter, col_n = st.columns([3, 1])
    with col_filter:
        mix_opts = ["Todos"] + sorted(df_data["MIX"].unique().tolist())
        mix_sel = st.selectbox("Filtrar por MIX", mix_opts, key="prev_mix")
    with col_n:
        n_rows = st.number_input("Filas a mostrar", min_value=5, max_value=500, value=50, step=10, key="prev_n")

    preview_df = df_data if mix_sel == "Todos" else df_data[df_data["MIX"] == mix_sel]
    st.dataframe(preview_df.head(n_rows), use_container_width=True, height=280)
    st.caption(f"Total: {len(df_data):,} filas | LBS totales: {fmt_lbs(df_data['TOTAL'].sum())}")

# ── Run loteo ──────────────────────────────────────────────────────────────
if run_btn:
    uploaded_file = st.session_state.get("file_upload")
    with st.spinner("Recargando parámetros con overrides de UI…"):
        try:
            # Reload with overrides
            st.session_state["file_upload"].seek(0)
            df_data2, df_cap2, params2, _ = load_inputs(st.session_state["file_upload"], param_overrides=overrides)
        except Exception as e:
            st.error(f"Error al aplicar parámetros: {e}")
            st.stop()

    progress_bar = st.progress(0, text="Iniciando loteo…")
    status_text = st.empty()

    def progress_cb(pct, msg):
        progress_bar.progress(pct, text=msg)
        status_text.text(msg)

    try:
        df_det, df_res, df_exc, df_par = run_loteo(df_data2, df_cap2, params2, progress_callback=progress_cb)
        reports = build_reports(df_data2, df_cap2, df_det, df_res)
        progress_bar.progress(1.0, text="¡Loteo completado!")
        status_text.empty()
    except Exception as e:
        st.error(f"Error en el loteo: {e}")
        st.stop()

    result = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "label": datetime.now().strftime("%H:%M:%S"),
        "detalle": df_det,
        "resumen": df_res,
        "excedentes": df_exc,
        "params_out": df_par,
        "reports": reports,
        "df_data_input": df_data2,
        "df_cap_input": df_cap2,
    }
    st.session_state.last_result = result

    # Keep history (max 5)
    hist = st.session_state.run_history
    hist.append(result)
    if len(hist) > 5:
        hist.pop(0)
    st.session_state.run_history = hist

    st.rerun()


# ── Results display ────────────────────────────────────────────────────────
if st.session_state.last_result is None:
    st.caption("Sube un archivo y presiona **▶ Correr Loteo** para ver resultados.")
    st.stop()

res = st.session_state.last_result
df_det = res["detalle"]
df_res = res["resumen"]
df_exc = res["excedentes"]
reports = res["reports"]

# ── KPI strip ──────────────────────────────────────────────────────────────
total_lotes = len(df_res)
total_lbs_asig = df_det["LBS_ASIGNADAS"].sum() if not df_det.empty else 0
total_lbs_exc = df_exc["LBS_RESTANTES"].sum() if not df_exc.empty else 0
lnk_df = reports.get("LNK_COMPLETITUD", pd.DataFrame())
completitud_pct = (
    (lnk_df["ESTADO"].isin(["COMPLETO", "COMPLETO (SCRAP)"]).sum() / len(lnk_df) * 100)
    if not lnk_df.empty else 0
)
cap_perdida = df_res["CAPACIDAD_PERDIDA"].sum() if not df_res.empty else 0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Lotes generados", f"{total_lotes:,}")
k2.metric("LBS asignadas", fmt_lbs(total_lbs_asig))
k3.metric("LBS excedentes", fmt_lbs(total_lbs_exc))
k4.metric("LNKs completos", f"{completitud_pct:.1f}%")
k5.metric("Capacidad perdida", fmt_lbs(cap_perdida))

st.caption(f"Corrida: {res['ts']}")
st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────
tab_graficas, tab_detalle, tab_resumen, tab_log, tab_comparar, tab_excedentes = st.tabs([
    "📊 Gráficas",
    "📋 Detalle Lotes",
    "📄 Resumen",
    "🔍 Decision Log",
    "🔁 Comparar Corridas",
    "⚠️ Excedentes",
])

# ── Tab: Gráficas ──────────────────────────────────────────────────────────
with tab_graficas:
    cap_df = reports.get("CAPACIDAD_X_CATEG", pd.DataFrame())
    prio_df = reports.get("PRIORIDAD_VS_ASIG", pd.DataFrame())

    col_l, col_r = st.columns(2)
    with col_l:
        st.plotly_chart(chart_capacidad_barras(cap_df), use_container_width=True)
    with col_r:
        st.plotly_chart(chart_bloques_donut(prio_df), use_container_width=True)

    col_l2, col_r2 = st.columns(2)
    with col_l2:
        st.plotly_chart(chart_heatmap_capacidad(cap_df), use_container_width=True)
    with col_r2:
        st.plotly_chart(chart_completitud_lnk(lnk_df), use_container_width=True)

# ── Tab: Detalle ───────────────────────────────────────────────────────────
with tab_detalle:
    if df_det.empty:
        st.info("Sin lotes generados.")
    else:
        dc1, dc2, dc3 = st.columns(3)
        with dc1:
            det_mix = st.multiselect("MIX", sorted(df_det["MIX"].unique()), key="det_mix")
        with dc2:
            det_regla = st.multiselect("Regla aplicada", sorted(df_det["APLICA_REGLA"].unique()), key="det_regla")
        with dc3:
            det_bloque = st.multiselect("Bloque", sorted(df_det["BLOQUE"].unique()), key="det_bloque")

        filtered = df_det.copy()
        if det_mix:
            filtered = filtered[filtered["MIX"].isin(det_mix)]
        if det_regla:
            filtered = filtered[filtered["APLICA_REGLA"].isin(det_regla)]
        if det_bloque:
            filtered = filtered[filtered["BLOQUE"].isin(det_bloque)]

        st.dataframe(filtered, use_container_width=True, height=420)
        st.caption(f"{len(filtered):,} filas mostradas")

# ── Tab: Resumen ───────────────────────────────────────────────────────────
with tab_resumen:
    if df_res.empty:
        st.info("Sin resumen.")
    else:
        st.dataframe(df_res, use_container_width=True, height=460)

        # Tabla de capacidad por categoría
        st.subheader("Capacidad por Categoría")
        st.dataframe(cap_df, use_container_width=True, height=260)

# ── Tab: Decision Log ──────────────────────────────────────────────────────
with tab_log:
    dlog = reports.get("DECISION_LOG", pd.DataFrame())
    if dlog.empty:
        st.info("Sin log de decisiones.")
    else:
        lc1, lc2, lc3 = st.columns(3)
        with lc1:
            log_lnk = st.text_input("Filtrar LNK (contiene)", key="log_lnk")
        with lc2:
            log_regla = st.multiselect("Regla", sorted(dlog["APLICA_REGLA"].unique()) if "APLICA_REGLA" in dlog.columns else [], key="log_regla")
        with lc3:
            log_bloque = st.multiselect("Bloque", sorted(dlog["BLOQUE"].unique()) if "BLOQUE" in dlog.columns else [], key="log_bloque")

        log_filtered = dlog.copy()
        if log_lnk:
            log_filtered = log_filtered[log_filtered["LNK"].str.contains(log_lnk, case=False, na=False)]
        if log_regla:
            log_filtered = log_filtered[log_filtered["APLICA_REGLA"].isin(log_regla)]
        if log_bloque:
            log_filtered = log_filtered[log_filtered["BLOQUE"].isin(log_bloque)]

        st.dataframe(log_filtered, use_container_width=True, height=460)
        st.caption(f"{len(log_filtered):,} registros")

# ── Tab: Comparar Corridas ─────────────────────────────────────────────────
with tab_comparar:
    hist = st.session_state.run_history
    if len(hist) < 2:
        st.info("Corre al menos **2 corridas** para comparar.")
    else:
        comp_rows = []
        for i, r in enumerate(hist):
            d = r["detalle"]
            s = r["resumen"]
            exc = r["excedentes"]
            lnk_c = r["reports"].get("LNK_COMPLETITUD", pd.DataFrame())
            comp_rows.append({
                "Corrida": f"#{i + 1} – {r['label']}",
                "Lotes": len(s),
                "LBS Asignadas": fmt_lbs(d["LBS_ASIGNADAS"].sum() if not d.empty else 0),
                "LBS Excedentes": fmt_lbs(exc["LBS_RESTANTES"].sum() if not exc.empty else 0),
                "Cap. Perdida": fmt_lbs(s["CAPACIDAD_PERDIDA"].sum() if not s.empty else 0),
                "LNKs Completos %": f"{(lnk_c['ESTADO'].isin(['COMPLETO', 'COMPLETO (SCRAP)']).sum() / len(lnk_c) * 100) if not lnk_c.empty else 0:.1f}%",
            })
        st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

        # Per-corrida download buttons
        st.subheader("Descargar corridas individuales")
        for i, r in enumerate(hist):
            xlsx_bytes = export_excel(r)
            fname = f"RESULTADOS_LOTES_{r['ts'].replace(':', '').replace(' ', '_').replace('-', '')}.xlsx"
            st.download_button(
                label=f"⬇ Corrida #{i + 1} – {r['label']}",
                data=xlsx_bytes,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_hist_{i}",
            )

# ── Tab: Excedentes ────────────────────────────────────────────────────────
with tab_excedentes:
    if df_exc.empty:
        st.success("✅ Sin excedentes — todos los lotes fueron asignados.")
    else:
        st.warning(f"⚠️ {len(df_exc):,} filas con saldo sin asignar.")
        st.dataframe(df_exc, use_container_width=True, height=420)

# ── Global download ────────────────────────────────────────────────────────
st.divider()
xlsx_bytes = export_excel(res)
ts_clean = res["ts"].replace(":", "").replace(" ", "_").replace("-", "")
st.download_button(
    label="⬇ Descargar Excel completo (última corrida)",
    data=xlsx_bytes,
    file_name=f"RESULTADOS_LOTES_{ts_clean}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)
