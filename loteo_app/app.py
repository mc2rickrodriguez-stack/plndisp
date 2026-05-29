"""NV2 Loteo Tintorería v4"""
import io, sys, os, json, base64, threading
from datetime import datetime
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
from engine.loader import load_inputs
from engine.loteo  import run_loteo, build_reports, quality_to_beam, DESCARTE_MSGS
from ui.charts     import (chart_capacidad_barras, chart_bloques_donut,
                            chart_heatmap_capacidad, chart_completitud_lnk)

st.set_page_config(page_title="NV2 Loteo", page_icon="🧶", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""<style>
.info-note{background:#eff6ff;border-left:3px solid #3b82f6;padding:8px 14px;
           border-radius:4px;font-size:.88rem;color:#1e40af;margin-bottom:10px;}
.warn-note{background:#fff7ed;border-left:3px solid #f97316;padding:8px 14px;
           border-radius:4px;font-size:.88rem;color:#9a3412;margin-bottom:10px;}
</style>""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────
for k,v in {
    "run_history":[],"last_result":None,
    "df_data":None,"df_cap":None,"params":None,
    "raw_file_bytes":None,"raw_file_name":None,
    "cap_applied":False,
    "tbl_capacidades":None,"tbl_reglas_anchos":None,
    "tbl_restricciones_ancho":None,"tbl_restricciones_color":None,
    "tbl_restricciones_familia":None,"tbl_combinaciones":None,
    "profiles":{},"cfg":{},
    "cancel_flag":[False],
    "running":False,
}.items():
    if k not in st.session_state: st.session_state[k]=v

def fmt(v): return f"{v:,.0f}"

# ── Table factories ────────────────────────────────────────────────────────
CAP_COLS=["CATEGORIA","MINIMO","MAXIMO","CAPACIDAD","MIX",
          "MIN_DIFF","MAX_DIFF","MAX_WIDTHS","MAX_SKU","WIDTHS_TARGET_ORDER",
          "OVERSHOOT","UNDERSHOOT","PERMITIR_RANGO_SUPERIOR","MAX_SALTO_RANGO",
          "SCRAP_REMAINDER","APPLY_RULES_BLEACH","SPLIT_MIN_LBS",
          "OVERSHOOT_TOL_PCT","UNDERSHOOT_TOL_PCT"]

BOOL_CAP_COLS=["OVERSHOOT","UNDERSHOOT","PERMITIR_RANGO_SUPERIOR",
               "SCRAP_REMAINDER","APPLY_RULES_BLEACH"]

def _to_bool(v):
    if v is None: return False
    try:
        if pd.isna(v): return False
    except: pass
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return bool(v)
    return str(v).strip().upper() in ("1","TRUE","YES","SI","SÍ","X")

def _fix_bool_cols(df):
    """Force bool columns to dtype bool so st.data_editor renders checkboxes."""
    df = df.copy()
    for col in BOOL_CAP_COLS:
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].apply(_to_bool).astype(bool)
    return df

def empty_cap():
    df = pd.DataFrame(columns=CAP_COLS)
    for col in BOOL_CAP_COLS:
        df[col] = pd.array([], dtype=bool)
    return df
def empty_ra():   return pd.DataFrame(columns=["ANCHO_1","ANCHO_2","CAPACIDAD_PRIORIDAD_1","CAPACIDAD_PRIORIDAD_2","CAPACIDAD_PRIORIDAD_3"])
def empty_ras():  return pd.DataFrame(columns=["STYLE","LIMITE_ANCHO","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"])
def empty_rc():   return pd.DataFrame(columns=["COLOR_R","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"])
def empty_rf():   return pd.DataFrame(columns=["FAMILIA","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3","PRIORIDAD_4"])
def empty_comb(): return pd.DataFrame(columns=["PRIORIDAD_1","PRIORIDAD_2"])

def get_tbl(key, factory):
    t = st.session_state[key]
    df = t if t is not None else factory()
    if factory == empty_cap:
        df = _fix_bool_cols(df)
        # Save back so future calls don't re-process
        st.session_state[key] = df
    return df

# ── Profile helpers ────────────────────────────────────────────────────────
def df2j(df): return [] if (df is None or df.empty) else df.where(pd.notna(df),None).to_dict("records")
def j2df(r,f):
    df= pd.DataFrame(r) if r else f()
    if f==empty_cap: df=_fix_bool_cols(df)
    return df

def build_profile(overrides):
    p={"overrides":overrides,"created":datetime.now().isoformat(),"notes":"",
       "tables":{
           "capacidades":          df2j(get_tbl("tbl_capacidades",empty_cap)),
           "reglas_anchos":        df2j(get_tbl("tbl_reglas_anchos",empty_ra)),
           "restricciones_ancho":  df2j(get_tbl("tbl_restricciones_ancho",empty_ras)),
           "restricciones_color":  df2j(get_tbl("tbl_restricciones_color",empty_rc)),
           "restricciones_familia":df2j(get_tbl("tbl_restricciones_familia",empty_rf)),
           "combinaciones":        df2j(get_tbl("tbl_combinaciones",empty_comb)),
       }}
    if st.session_state.raw_file_bytes:
        p["file_b64"] =base64.b64encode(st.session_state.raw_file_bytes).decode()
        p["file_name"]=st.session_state.raw_file_name or "archivo.xlsx"
    return p

def apply_profile(profile):
    t=profile.get("tables",{})
    st.session_state.tbl_capacidades          =j2df(t.get("capacidades"),empty_cap)
    st.session_state.tbl_reglas_anchos        =j2df(t.get("reglas_anchos"),empty_ra)
    st.session_state.tbl_restricciones_ancho  =j2df(t.get("restricciones_ancho"),empty_ras)
    st.session_state.tbl_restricciones_color  =j2df(t.get("restricciones_color"),empty_rc)
    st.session_state.tbl_restricciones_familia=j2df(t.get("restricciones_familia"),empty_rf)
    st.session_state.tbl_combinaciones        =j2df(t.get("combinaciones"),empty_comb)
    st.session_state.cfg=profile.get("overrides",{})
    st.session_state.cap_applied=False
    if "file_b64" in profile:
        raw=base64.b64decode(profile["file_b64"])
        st.session_state.raw_file_bytes=raw
        st.session_state.raw_file_name=profile.get("file_name","archivo.xlsx")
        try:
            df_data,df_cap,params,_=load_inputs(io.BytesIO(raw))
            st.session_state.df_data=df_data; st.session_state.df_cap=df_cap
            st.session_state.params=params
        except: pass

CAP_DEFAULTS = {
    "MIN_DIFF": 0.0, "MAX_DIFF": 999.0, "MAX_WIDTHS": 3,
    "MAX_SKU": 8, "WIDTHS_TARGET_ORDER": "2>3>1",
    "OVERSHOOT": False, "UNDERSHOOT": False,
    "PERMITIR_RANGO_SUPERIOR": False, "MAX_SALTO_RANGO": 1,
    "SCRAP_REMAINDER": True, "APPLY_RULES_BLEACH": False,
    "SPLIT_MIN_LBS": 100, "OVERSHOOT_TOL_PCT": 5.0,
    "UNDERSHOOT_TOL_PCT": 2.0,
}

def load_tables_from_excel(raw):
    xls = pd.ExcelFile(io.BytesIO(raw), engine="openpyxl")

    def sr(sheet, factory):
        if sheet in xls.sheet_names:
            df = pd.read_excel(io.BytesIO(raw), sheet_name=sheet, engine="openpyxl")
            df.columns = [str(c).strip() for c in df.columns]
            return df
        return factory()

    # Load capacidades, add any missing columns with proper defaults
    cap_raw = sr("CAPACIDADES_TINTO", empty_cap)
    for col in CAP_COLS:
        if col not in cap_raw.columns:
            cap_raw[col] = CAP_DEFAULTS.get(col, None)
    cap_raw = cap_raw[CAP_COLS]          # enforce column order
    cap_raw = _fix_bool_cols(cap_raw)    # normalize all bool cols to Python bool

    st.session_state.tbl_capacidades          = cap_raw
    st.session_state.tbl_reglas_anchos        = sr("REGLAS_ANCHOS_COMBINADOS", empty_ra)
    st.session_state.tbl_restricciones_ancho  = sr("RESTRICCIONES_ANCHO",      empty_ras)
    st.session_state.tbl_restricciones_color  = sr("RESTRICCIONES_COLOR",      empty_rc)
    st.session_state.tbl_restricciones_familia= sr("RESTRICCIONES_FAMILIA",    empty_rf)
    st.session_state.tbl_combinaciones        = sr("COMBINACIONES_PRIORIDAD",   empty_comb)
    st.session_state.cap_applied = False

# ── Param rebuild from UI tables ───────────────────────────────────────────
def rebuild_params(params2):
    def rfam(df):
        out={}
        if df is None or df.empty: return out
        pc=[c for c in df.columns if c.upper().startswith("PRIORIDAD")]
        for _,r in df.iterrows():
            f=str(r.get("FAMILIA","")).strip().upper()
            if not f: continue
            caps=[float(r[c]) for c in pc if pd.notna(r.get(c))]
            if caps: out[f]=caps
        return out
    def rcol(df):
        out={}
        if df is None or df.empty: return out
        for _,r in df.iterrows():
            c=str(r.get("COLOR_R","")).strip().upper()
            v=r.get("PRIORIDAD_1",None)
            out[c]=float(v) if pd.notna(v) else None
        return out
    def ranch(df):
        out={}
        if df is None or df.empty: return out
        pc=[c for c in df.columns if c.upper().startswith("PRIORIDAD")]
        for _,r in df.iterrows():
            s=str(r.get("STYLE","")).strip().upper()
            if not s: continue
            lim=r.get("LIMITE_ANCHO",None)
            try: lim=float(lim) if pd.notna(lim) else None
            except: lim=None
            caps=[]
            for c in pc:
                v=r.get(c,None)
                if pd.notna(v):
                    try: caps.append(float(v))
                    except: pass
            out[s]={"limite":lim,"prioridades":caps}
        return out
    def rra(df):
        out=[]
        if df is None or df.empty: return out
        pc=[c for c in df.columns if c.upper().startswith("CAPACIDAD_PRIORIDAD")]
        for _,r in df.iterrows():
            try: a1=float(r.get("ANCHO_1",0)); a2=float(r.get("ANCHO_2",0))
            except: continue
            caps=[float(r[c]) for c in pc if pd.notna(r.get(c))]
            if caps: out.append({"a1":a1,"a2":a2,"prioridades":caps})
        return out
    def rcomb(df):
        pairs=set()
        if df is None or df.empty: return pairs
        for _,r in df.iterrows():
            a=str(r.get("PRIORIDAD_1","")).strip(); b=str(r.get("PRIORIDAD_2","")).strip()
            if a and b: pairs.add((a,b)); pairs.add((b,a))
        return pairs

    params2["RESTRICCIONES_FAMILIA"]   =rfam(st.session_state.tbl_restricciones_familia)
    params2["RESTRICCIONES_COLOR"]     =rcol(st.session_state.tbl_restricciones_color)
    params2["RESTRICCIONES_ANCHO"]     =ranch(st.session_state.tbl_restricciones_ancho)
    params2["REGLAS_ANCHOS_COMBINADOS"]=rra(st.session_state.tbl_reglas_anchos)
    c=rcomb(st.session_state.tbl_combinaciones)
    if c: params2["MIX_ALLOWED"]=c
    return params2

def export_excel(result):
    buf=io.BytesIO()
    with pd.ExcelWriter(buf,engine="openpyxl") as w:
        result["detalle"].to_excel(w,index=False,sheet_name="DETALLE_LOTES")
        result["resumen"].to_excel(w,index=False,sheet_name="RESUMEN_LOTES")
        result["excedentes"].to_excel(w,index=False,sheet_name="EXCEDENTES")
        result["params_out"].to_excel(w,index=False,sheet_name="PARAMETROS")
        for k,df in result["reports"].items():
            if not df.empty: df.to_excel(w,index=False,sheet_name=k[:31])
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("🧶 NV2 Loteo")
    st.divider()
    # Profiles
    st.subheader("💾 Perfiles")
    profiles=st.session_state.profiles
    pnames=list(profiles.keys())
    if pnames:
        sel=st.selectbox("Cargar perfil",["— seleccionar —"]+pnames,key="sel_profile")
        if sel!="— seleccionar —" and st.button("📥 Aplicar",use_container_width=True):
            apply_profile(profiles[sel]); st.rerun()
    json_up=st.file_uploader("Importar JSON",type=["json"],key="json_upload")
    if json_up:
        try:
            loaded=json.load(json_up); nm=json_up.name.replace(".json","")
            profiles[nm]=loaded; st.session_state.profiles=profiles
            st.success(f"'{nm}' importado")
        except Exception as e: st.error(str(e))

    st.divider()
    p=st.session_state.params or {}
    cfg=st.session_state.cfg or {}
    def cv(k,d): return cfg.get(k,p.get(k,d))

    st.subheader("⚙️ Ajustes avanzados")
    with st.expander("Overshoot / Undershoot"):
        overshoot  =st.checkbox("OVERSHOOT_ENABLE",  value=bool(int(cv("OVERSHOOT_ENABLE",1))))
        undershoot =st.checkbox("UNDERSHOOT_ENABLE", value=bool(int(cv("UNDERSHOOT_ENABLE",1))))
        tol_small  =st.number_input("Tolerancia % órdenes pequeñas",
                                    value=float(cv("OVERSHOOT_TOL_PCT_SMALL",5)),
                                    min_value=0.0,max_value=50.0,step=0.5,
                                    help="% de tolerancia para órdenes ≤ umbral")
        tol_large  =st.number_input("Tolerancia % órdenes grandes",
                                    value=float(cv("OVERSHOOT_TOL_PCT_LARGE",2)),
                                    min_value=0.0,max_value=50.0,step=0.5)
        tol_thr    =st.number_input("Umbral pequeña/grande (LBS)",
                                    value=float(cv("OVERSHOOT_SMALL_THRESHOLD",5000)),step=500.0)

    with st.expander("Agrupamiento"):
        agrupar_tono=st.checkbox("Agrupar por TONO",
                                 value=bool(int(cv("AGRUPAR_POR_TONO",1))),
                                 help="Si se desactiva, grupos por TELA+COLOR+MIX en lugar de TELA+TONO+MIX")
        apply_bleach=st.checkbox("APPLY_RULES_BLEACH",value=bool(int(cv("APPLY_RULES_BLEACH",0))))

    with st.expander("Reglas de orden"):
        rule_order    =st.text_input("RULE_ORDER",
                                     value=str(cv("RULE_ORDER","RESTRICCION_ANCHO>COMBO_ANCHOS>COLOR_R>FAMILIA>DEFAULT")))
        priority_order=st.text_input("PRIORITY_ORDER",value=str(cv("PRIORITY_ORDER","")))

    adv_overrides={
        "OVERSHOOT_ENABLE":int(overshoot),"UNDERSHOOT_ENABLE":int(undershoot),
        "OVERSHOOT_TOL_PCT_SMALL":tol_small/100,"OVERSHOOT_TOL_PCT_LARGE":tol_large/100,
        "OVERSHOOT_SMALL_THRESHOLD":tol_thr,
        "AGRUPAR_POR_TONO":int(agrupar_tono),"APPLY_RULES_BLEACH":int(apply_bleach),
        "RULE_ORDER":rule_order,"PRIORITY_ORDER":priority_order,
    }

# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════
st.markdown("## 🧶 NV2 Loteo Tintorería")
st.caption("Optimización · Lotes · Asignación de Pedidos")

# ── Sección 1: Carga ──────────────────────────────────────────────────────
with st.expander("📁  Sección 1 — Carga de Archivo", expanded=True):
    u_col,i_col=st.columns([2,1])
    with u_col:
        uploaded=st.file_uploader("Excel (.xlsx/.xlsm)",type=["xlsx","xlsm"],
                                  key="file_upload",label_visibility="collapsed")
    with i_col:
        if st.session_state.df_data is not None:
            df_data=st.session_state.df_data
            st.success(f"✅ **{st.session_state.raw_file_name}**")
            st.caption(f"{len(df_data):,} filas · {fmt(df_data['TOTAL'].sum())} LBS")
        else: st.info("Sin archivo")

    if uploaded:
        fb=uploaded.read()
        if fb!=st.session_state.raw_file_bytes:
            with st.spinner("Leyendo…"):
                try:
                    df_data,df_cap,params,hdr=load_inputs(io.BytesIO(fb))
                    st.session_state.df_data=df_data; st.session_state.df_cap=df_cap
                    st.session_state.params=params; st.session_state.raw_file_bytes=fb
                    st.session_state.raw_file_name=uploaded.name
                    load_tables_from_excel(fb); st.rerun()
                except Exception as e: st.error(str(e))

    if st.session_state.df_data is not None:
        with st.expander("🔍 Vista previa DATA"):
            df_data=st.session_state.df_data
            f1,f2=st.columns([3,1])
            mix_sel=f1.selectbox("MIX",["Todos"]+sorted(df_data["MIX"].unique().tolist()),
                                 key="prev_mix",label_visibility="collapsed")
            n_rows=f2.number_input("Filas",5,500,50,key="prev_n",label_visibility="collapsed")
            prev=df_data if mix_sel=="Todos" else df_data[df_data["MIX"]==mix_sel]
            st.dataframe(prev.head(n_rows),use_container_width=True,height=220)

        with st.expander("📊 Resumen dinámico de DATA"):
            df_data=st.session_state.df_data
            # Available grouping columns
            GROUP_OPTS=[c for c in ["TELA.CUERPO","STYLE","COLOR","COLOR_R","FAMILIA","TONO",
                                     "MIX","PRIORIDAD","ANCHO.F.C","ANCHO.F.M","LNK"]
                        if c in df_data.columns]
            # Metric options
            METRIC_OPTS={"LBS (TOTAL)":"TOTAL","Docenas (CONSUMO_C)":"CONSUMO_C"}
            valid_metrics={k:v for k,v in METRIC_OPTS.items() if v in df_data.columns}

            rc1,rc2,rc3=st.columns([3,3,2])
            with rc1:
                group_sel=st.multiselect(
                    "Agrupar por",GROUP_OPTS,
                    default=[GROUP_OPTS[0]] if GROUP_OPTS else [],
                    key="rsm_group",
                    help="Selecciona uno o más campos para agrupar"
                )
            with rc2:
                metric_sel=st.selectbox(
                    "Métrica",list(valid_metrics.keys()),key="rsm_metric"
                )
            with rc3:
                top_n=st.number_input("Top N filas",min_value=5,max_value=500,value=50,step=5,key="rsm_n")

            if group_sel and metric_sel:
                metric_col=valid_metrics[metric_sel]
                try:
                    rsm=df_data.groupby(group_sel,as_index=False)[metric_col].sum()
                    rsm=rsm.sort_values(metric_col,ascending=False).head(top_n)
                    rsm[metric_col]=rsm[metric_col].round(1)
                    rsm.columns=group_sel+[metric_sel]
                    # % del total
                    total_metric=df_data[metric_col].sum()
                    rsm["% del Total"]=(rsm[metric_sel]/total_metric*100).round(1) if total_metric>0 else 0
                    st.dataframe(rsm,use_container_width=True,height=min(60+35*len(rsm),400),
                                 hide_index=True)
                    st.caption(f"Total {metric_sel}: {df_data[metric_col].sum():,.1f} · Mostrando top {len(rsm)} de {len(df_data.groupby(group_sel))} grupos")
                except Exception as e:
                    st.error(f"Error al agrupar: {e}")
            else:
                st.info("Selecciona al menos un campo para agrupar.")

# ── Sección 2: Capacidad ──────────────────────────────────────────────────
with st.expander("📋  Sección 2 — Capacidad y Validación", expanded=True):
    st.markdown('<div class="info-note">✏️ Edita la tabla y presiona <b>Aplicar cambios de capacidad</b>. '
                'Los parámetros por fila sobreescriben los globales para ese tamaño de lote.</div>',
                unsafe_allow_html=True)

    tbl_cap=get_tbl("tbl_capacidades",empty_cap)
    h=min(60+35*max(len(tbl_cap),1),500)

    # CSS to freeze first 3 columns in data_editor
    st.markdown("""
    <style>
    div[data-testid="stDataEditor"] table thead tr th:nth-child(-n+4),
    div[data-testid="stDataEditor"] table tbody tr td:nth-child(-n+4) {
        position: sticky !important;
        left: 0;
        background: #f8fafc;
        z-index: 2;
        border-right: 2px solid #cbd5e1;
    }
    div[data-testid="stDataEditor"] table thead tr th:nth-child(2),
    div[data-testid="stDataEditor"] table tbody tr td:nth-child(2) { left: 60px !important; }
    div[data-testid="stDataEditor"] table thead tr th:nth-child(3),
    div[data-testid="stDataEditor"] table tbody tr td:nth-child(3) { left: 130px !important; }
    div[data-testid="stDataEditor"] table thead tr th:nth-child(4),
    div[data-testid="stDataEditor"] table tbody tr td:nth-child(4) { left: 210px !important; }
    </style>
    """, unsafe_allow_html=True)

    cap_ed=st.data_editor(
        tbl_cap, num_rows="dynamic", use_container_width=True, height=h, key="editor_cap",
        column_config={
            "CATEGORIA":  st.column_config.TextColumn("Categoría",width="small"),
            "MINIMO":     st.column_config.NumberColumn("Mín LBS",format="%d"),
            "MAXIMO":     st.column_config.NumberColumn("Máx LBS",format="%d"),
            "CAPACIDAD":  st.column_config.NumberColumn("Capacidad Total",format="%d"),
            "MIX":        st.column_config.SelectboxColumn("MIX",options=["DYE","BLEACH"]),
            "MIN_DIFF":   st.column_config.NumberColumn("Min Diff Anchos",format="%.1f",help="Diferencia mínima entre anchos"),
            "MAX_DIFF":   st.column_config.NumberColumn("Max Diff Anchos",format="%.1f",help="Diferencia máxima entre anchos"),
            "MAX_WIDTHS": st.column_config.NumberColumn("Max Anchos",format="%d",help="Máximo de anchos distintos por lote"),
            "MAX_SKU":    st.column_config.NumberColumn("Max SKU",format="%d",help="Máximo de SKUs por lote"),
            "WIDTHS_TARGET_ORDER": st.column_config.TextColumn("Orden Anchos",help="Ej: 2>3>1"),
            "OVERSHOOT":  st.column_config.CheckboxColumn("Overshoot",help="Permitir exceder la orden"),
            "UNDERSHOOT": st.column_config.CheckboxColumn("Undershoot",help="Permitir quedarse bajo la orden"),
            "PERMITIR_RANGO_SUPERIOR": st.column_config.CheckboxColumn("Rango Superior",help="Permitir colocar en rango mayor"),
            "MAX_SALTO_RANGO": st.column_config.NumberColumn("Max Salto",format="%d",help="Cuántos rangos arriba puede subir"),
            "SCRAP_REMAINDER":  st.column_config.CheckboxColumn("Scrap Residuo",help="Descartar residuos menores al mínimo de split"),
            "APPLY_RULES_BLEACH": st.column_config.CheckboxColumn("Reglas Bleach",help="Aplicar reglas de restricción también a BLEACH"),
            "SPLIT_MIN_LBS":    st.column_config.NumberColumn("Split Min LBS",format="%d"),
            "OVERSHOOT_TOL_PCT":  st.column_config.NumberColumn("Tol Over %",format="%.1f%%",help="% tolerancia overshoot para este rango"),
            "UNDERSHOOT_TOL_PCT": st.column_config.NumberColumn("Tol Under %",format="%.1f%%"),
        },
    )

    b_col,s_col=st.columns([1,3])
    with b_col:
        if st.button("✅ Aplicar cambios de capacidad",type="primary",use_container_width=True):
            st.session_state.tbl_capacidades=cap_ed; st.session_state.cap_applied=True
            st.success("Capacidades actualizadas")
    with s_col:
        if st.session_state.cap_applied:
            cap_ok=get_tbl("tbl_capacidades",empty_cap)
            if not cap_ok.empty:
                tot=pd.to_numeric(cap_ok["CAPACIDAD"],errors="coerce").sum()
                inv=cap_ok[["MINIMO","MAXIMO","CAPACIDAD"]].isna().any(axis=1).sum()
                st.caption(f"{'⚠️' if inv else '✅'} {len(cap_ok)} categorías · "
                           f"Capacidad total: **{fmt(tot)} LBS**"
                           f"{' · ⚠️ Filas incompletas: '+str(inv) if inv else ''}")
        else:
            st.caption("ℹ️ Edita y presiona Aplicar para habilitar el loteo.")

    # Pre-check de factibilidad
    if st.session_state.cap_applied and st.session_state.df_data is not None:
        df_data=st.session_state.df_data
        cap_ok=get_tbl("tbl_capacidades",empty_cap)
        if not cap_ok.empty:
            st.markdown("**Pre-check de factibilidad:**")
            for mix_v in df_data["MIX"].unique():
                lbs_disp=df_data[df_data["MIX"]==mix_v]["TOTAL"].sum()
                cap_rows=cap_ok[cap_ok["MIX"]==mix_v]
                lbs_cap=pd.to_numeric(cap_rows["CAPACIDAD"],errors="coerce").sum() if not cap_rows.empty else 0
                gap=lbs_cap-lbs_disp
                if gap>=0:
                    st.caption(f"✅ {mix_v}: {fmt(lbs_disp)} LBS disponibles | {fmt(lbs_cap)} LBS capacidad → Capacidad suficiente (+{fmt(gap)})")
                else:
                    st.caption(f"⚠️ {mix_v}: {fmt(lbs_disp)} LBS disponibles | {fmt(lbs_cap)} LBS capacidad → {fmt(-gap)} LBS sin capacidad")

# ── Sección 3: Calidad ────────────────────────────────────────────────────
with st.expander("🎯  Sección 3 — Calidad del Loteo", expanded=True):
    p=st.session_state.params or {}
    cfg=st.session_state.cfg or {}
    def cv(k,d): return cfg.get(k,p.get(k,d))

    ql_col,info_col=st.columns([2,1])
    with ql_col:
        quality_level=st.slider(
            "Calidad del loteo",min_value=1,max_value=10,
            value=int(cv("QUALITY_LEVEL",5)),
            key="quality_slider",
            help="1 = Muy rápido (menos exhaustivo) · 10 = Óptimo (más lento)",
        )
        # Always persist to session_state so it's available even when collapsed
        st.session_state["_quality_level"]=quality_level
        beam=quality_to_beam(quality_level)
        st.caption(f"BEAM_WIDTH interno: **{beam}** · "
                   f"{'🟢 Rápido' if quality_level<=3 else '🟡 Balanceado' if quality_level<=6 else '🔴 Lento/Óptimo'}")
    with info_col:
        if st.session_state.df_data is not None:
            df_data=st.session_state.df_data
            groups_est=df_data.groupby(["TELA.CUERPO","MIX"]).ngroups
            est_sec=groups_est*beam*0.075
            st.metric("Grupos estimados",f"{groups_est:,}")
            st.caption(f"⏱ Tiempo estimado: ~{est_sec/60:.1f} min")

# Read quality from session_state (safe even if expander was collapsed)
_ql=st.session_state.get("_quality_level", int((st.session_state.cfg or {}).get("QUALITY_LEVEL",5)))
section3_overrides={"QUALITY_LEVEL":_ql}

# ── Sección 4: Reglas ──────────────────────────────────────────────────────
with st.expander("🔗  Sección 4 — Reglas de Combinación y Restricciones", expanded=False):
    rt1,rt2,rt3,rt4,rt5=st.tabs([
        "🔗 Anchos Combinados","📐 Restricciones Ancho",
        "🎨 Restricciones Color","👨‍👩‍👧 Restricciones Familia","⚖️ Combinaciones Prioridad"])

    with rt1:
        st.markdown('<div class="info-note">Pares de anchos que pueden combinarse + prioridades de tamaño. Vacía = libre.</div>',unsafe_allow_html=True)
        ra_ed=st.data_editor(get_tbl("tbl_reglas_anchos",empty_ra),num_rows="dynamic",
                             use_container_width=True,key="editor_ra",
                             column_config={
                                 "ANCHO_1":st.column_config.NumberColumn("Ancho 1",format="%.1f"),
                                 "ANCHO_2":st.column_config.NumberColumn("Ancho 2",format="%.1f"),
                                 "CAPACIDAD_PRIORIDAD_1":st.column_config.NumberColumn("Prioridad 1 (LBS)",format="%d"),
                                 "CAPACIDAD_PRIORIDAD_2":st.column_config.NumberColumn("Prioridad 2 (LBS)",format="%d"),
                                 "CAPACIDAD_PRIORIDAD_3":st.column_config.NumberColumn("Prioridad 3 (LBS)",format="%d"),
                             })
        if st.button("💾 Guardar",key="save_ra"): st.session_state.tbl_reglas_anchos=ra_ed; st.success("Guardado")

    with rt2:
        st.markdown('<div class="info-note">Si el STYLE tiene un ancho ≤ LIMITE_ANCHO, prioriza los tamaños indicados. '
                    'Reemplaza la antigua regla ANCHO18.</div>',unsafe_allow_html=True)
        ras_ed=st.data_editor(get_tbl("tbl_restricciones_ancho",empty_ras),num_rows="dynamic",
                              use_container_width=True,key="editor_ras",
                              column_config={
                                  "STYLE":st.column_config.TextColumn("STYLE",width="medium"),
                                  "LIMITE_ANCHO":st.column_config.NumberColumn("Límite Ancho",format="%.1f"),
                                  "PRIORIDAD_1":st.column_config.NumberColumn("Prioridad 1 (LBS)",format="%d"),
                                  "PRIORIDAD_2":st.column_config.NumberColumn("Prioridad 2 (LBS)",format="%d"),
                                  "PRIORIDAD_3":st.column_config.NumberColumn("Prioridad 3 (LBS)",format="%d"),
                              })
        srch=st.text_input("🔍 Buscar STYLE",key="srch_style")
        if srch and not ras_ed.empty:
            f=ras_ed[ras_ed["STYLE"].astype(str).str.upper().str.contains(srch.upper(),na=False)]
            st.dataframe(f,use_container_width=True) if not f.empty else st.caption("Sin resultados")
        if st.button("💾 Guardar",key="save_ras"): st.session_state.tbl_restricciones_ancho=ras_ed; st.success("Guardado")

    with rt3:
        rc_ed=st.data_editor(get_tbl("tbl_restricciones_color",empty_rc),num_rows="dynamic",
                             use_container_width=True,key="editor_rc",
                             column_config={
                                 "COLOR_R":st.column_config.TextColumn("COLOR_R",width="medium"),
                                 "PRIORIDAD_1":st.column_config.NumberColumn("Prioridad 1 (LBS)",format="%d"),
                                 "PRIORIDAD_2":st.column_config.NumberColumn("Prioridad 2 (LBS)",format="%d"),
                                 "PRIORIDAD_3":st.column_config.NumberColumn("Prioridad 3 (LBS)",format="%d"),
                             })
        if st.button("💾 Guardar",key="save_rc"): st.session_state.tbl_restricciones_color=rc_ed; st.success("Guardado")

    with rt4:
        rf_ed=st.data_editor(get_tbl("tbl_restricciones_familia",empty_rf),num_rows="dynamic",
                             use_container_width=True,key="editor_rf",
                             column_config={
                                 "FAMILIA":st.column_config.TextColumn("FAMILIA",width="medium"),
                                 "PRIORIDAD_1":st.column_config.NumberColumn("Prioridad 1 (LBS)",format="%d"),
                                 "PRIORIDAD_2":st.column_config.NumberColumn("Prioridad 2 (LBS)",format="%d"),
                                 "PRIORIDAD_3":st.column_config.NumberColumn("Prioridad 3 (LBS)",format="%d"),
                                 "PRIORIDAD_4":st.column_config.NumberColumn("Prioridad 4 (LBS)",format="%d"),
                             })
        if st.button("💾 Guardar",key="save_rf"): st.session_state.tbl_restricciones_familia=rf_ed; st.success("Guardado")

    with rt5:
        st.markdown('<div class="info-note">Tabla vacía = NO se mezclan bloques de prioridad. '
                    'Agrega pares que SÍ pueden coexistir.</div>',unsafe_allow_html=True)
        BLOQUES=["VENCIDOS","AHEAD","AHEAD2","OTROS"]
        comb_ed=st.data_editor(get_tbl("tbl_combinaciones",empty_comb),num_rows="dynamic",
                               use_container_width=True,key="editor_comb",
                               column_config={
                                   "PRIORIDAD_1":st.column_config.SelectboxColumn("Bloque 1",options=BLOQUES),
                                   "PRIORIDAD_2":st.column_config.SelectboxColumn("Bloque 2",options=BLOQUES),
                               })
        if st.button("💾 Guardar",key="save_comb"): st.session_state.tbl_combinaciones=comb_ed; st.success("Guardado")

# ── Sección 5: Perfiles ────────────────────────────────────────────────────
with st.expander("💾  Sección 5 — Guardar Perfil", expanded=False):
    all_overrides={**adv_overrides,**section3_overrides}
    pc1,pc2,pc3,pc4=st.columns([2,1,1,1])
    pname=pc1.text_input("Nombre del perfil",placeholder="ej. Semana23_DYE",key="pname")
    notes=pc2.text_input("Notas",placeholder="Descripción opcional",key="pnotes")
    with pc3:
        if st.button("💾 En sesión",use_container_width=True):
            nm=pname.strip()
            if nm:
                pr=build_profile(all_overrides); pr["notes"]=notes
                # diff vs previous version of same profile
                if nm in profiles:
                    old=profiles[nm].get("overrides",{}); new=all_overrides
                    diff={k:{"antes":old.get(k),"ahora":new.get(k)} for k in set(old)|set(new) if old.get(k)!=new.get(k)}
                    pr["diff_vs_anterior"]=diff
                profiles[nm]=pr; st.session_state.profiles=profiles; st.success(f"'{nm}' guardado")
            else: st.warning("Ingresa un nombre")
    with pc4:
        pr_json=json.dumps(build_profile(all_overrides),indent=2,ensure_ascii=False)
        st.download_button("📤 Exportar JSON",data=pr_json,
                           file_name=(pname.strip() or "perfil")+".json",
                           mime="application/json",use_container_width=True)
    if profiles:
        st.caption(f"Perfiles en sesión: {', '.join(profiles.keys())}")

# ── Botón principal ────────────────────────────────────────────────────────
st.divider()
can_run=(st.session_state.df_data is not None and
         st.session_state.raw_file_bytes is not None and
         st.session_state.cap_applied)

if not can_run:
    tips=[]
    if st.session_state.df_data is None:   tips.append("📁 Sube un archivo Excel")
    if not st.session_state.cap_applied:   tips.append("📋 Aplica cambios de capacidad (Sección 2)")
    st.info("  ·  ".join(tips))

run_comment=st.text_input("💬 Comentario para esta corrida (opcional)",
                           placeholder="ej. Prueba calidad 10 con nuevas capacidades",
                           key="run_comment")

btn_col,cancel_col=st.columns([3,1])
with btn_col:
    run_btn=st.button("▶  Correr Loteo",type="primary",use_container_width=True,disabled=not can_run)
with cancel_col:
    if st.button("⏹ Cancelar",use_container_width=True,disabled=not st.session_state.running):
        st.session_state.cancel_flag[0]=True

# ── Ejecución ──────────────────────────────────────────────────────────────
if run_btn and can_run:
    st.session_state.cancel_flag=[False]
    st.session_state.running=True
    all_overrides={**adv_overrides,**section3_overrides}

    with st.spinner("Preparando parámetros…"):
        try:
            df_data2,df_cap2,params2,_=load_inputs(io.BytesIO(st.session_state.raw_file_bytes),
                                                    param_overrides=all_overrides)
            cap_ui=get_tbl("tbl_capacidades",empty_cap)
            if not cap_ui.empty:
                for c in ["MINIMO","MAXIMO","CAPACIDAD"]:
                    cap_ui[c]=pd.to_numeric(cap_ui[c],errors="coerce")
                df_cap2=cap_ui.dropna(subset=["MINIMO","MAXIMO","CAPACIDAD"]).copy()
            params2=rebuild_params(params2)
        except Exception as e:
            st.error(f"Error: {e}"); st.session_state.running=False; st.stop()

    prog=st.progress(0,text="Iniciando…")
    stat=st.empty()

    def cb(pct,msg,stats):
        prog.progress(min(pct,0.99),text=msg)
        stat.markdown(
            f"**Grupo** {stats['grupo']}/{stats['total']} · "
            f"**Lotes formados:** {stats['lotes']:,} · "
            f"**LBS procesadas:** {fmt(stats['lbs'])}"
        )

    try:
        df_det,df_res,df_exc,df_par,cancelled=run_loteo(
            df_data2,df_cap2,params2,progress_callback=cb,
            cancel_flag=st.session_state.cancel_flag)
        if cancelled:
            prog.progress(1.0,text="⏹ Cancelado — resultados parciales disponibles")
        else:
            prog.progress(1.0,text="✅ Loteo completado")
        stat.empty()
    except Exception as e:
        st.error(f"Error en loteo: {e}"); st.session_state.running=False; st.stop()

    result={"ts":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "label":datetime.now().strftime("%H:%M:%S"),
            "comentario":st.session_state.get("run_comment",""),
            "quality_used":_ql,
            "detalle":df_det,"resumen":df_res,"excedentes":df_exc,
            "params_out":df_par,
            "reports":build_reports(df_data2,df_cap2,df_det,df_res),
            "cancelled":cancelled}
    st.session_state.last_result=result
    h=st.session_state.run_history; h.append(result)
    if len(h)>5: h.pop(0)
    st.session_state.run_history=h
    st.session_state.running=False
    st.rerun()

# ── Resultados ─────────────────────────────────────────────────────────────
if st.session_state.last_result is None: st.stop()

res=st.session_state.last_result
df_det=res["detalle"]; df_res=res["resumen"]
df_exc=res["excedentes"]; reports=res["reports"]
lnk_df=reports.get("LNK_COMPLETITUD",pd.DataFrame())

if res.get("cancelled"):
    st.warning("⚠️ Corrida cancelada — resultados parciales")

st.divider()
st.markdown("### 📊 Resultados")
_comment=res.get("comentario","")
_ql_used=res.get("quality_used","?")
st.caption(f"Corrida: {res['ts']}  ·  Calidad: {_ql_used}  {'·  💬 '+_comment if _comment else ''}")

k1,k2,k3,k4,k5,k6,k7=st.columns(7)
_lbs_asig = df_det["LBS_ASIGNADAS"].sum() if not df_det.empty else 0
_lbs_plan = reports.get("PRIORIDAD_VS_ASIG", pd.DataFrame())
_lbs_plan_total = _lbs_plan["LBS_BASE"].sum() if not _lbs_plan.empty and "LBS_BASE" in _lbs_plan.columns else 0
_pct_plan = (_lbs_asig / _lbs_plan_total * 100) if _lbs_plan_total > 0 else 0
k1.metric("Lotes",          f"{len(df_res):,}")
k2.metric("LBS Planeadas",  fmt(_lbs_plan_total))
k3.metric("LBS Asignadas",  fmt(_lbs_asig))
k4.metric("% vs Plan",      f"{_pct_plan:.1f}%")
k5.metric("LBS Excedentes", fmt(df_exc["LBS_RESTANTES"].sum() if not df_exc.empty else 0))
k6.metric("LNKs completos",
          f"{(lnk_df['ESTADO'].isin(['COMPLETO','COMPLETO (SCRAP)']).sum()/len(lnk_df)*100) if not lnk_df.empty else 0:.1f}%")
k7.metric("Cap. perdida",   fmt(df_res["CAPACIDAD_PERDIDA"].sum() if not df_res.empty else 0))

tab_g,tab_d,tab_r,tab_l,tab_c,tab_e=st.tabs([
    "📊 Gráficas","📋 Detalle Lotes","📄 Resumen",
    "🔍 Decision Log","🔁 Comparar Corridas","⚠️ Excedentes"])

with tab_g:
    cap_df  = reports.get("CAPACIDAD_X_CATEG", pd.DataFrame())
    prio_df = reports.get("PRIORIDAD_VS_ASIG", pd.DataFrame())
    c1,c2=st.columns(2)
    with c1: st.plotly_chart(chart_capacidad_barras(cap_df), use_container_width=True)
    with c2: st.plotly_chart(chart_bloques_donut(prio_df),   use_container_width=True)
    c3,c4=st.columns(2)
    with c3: st.plotly_chart(chart_heatmap_capacidad(cap_df),  use_container_width=True)
    with c4: st.plotly_chart(chart_completitud_lnk(lnk_df),    use_container_width=True)

    st.divider()
    st.markdown("#### 📋 Tablas Resumen")
    ta,tb,tc,td,te = st.tabs(["Por Prioridad","Por N° Anchos","Anchos × Categoría","Por Categoría","Resumen Dinámico"])

    with ta:
        if df_det.empty or prio_df.empty:
            st.info("Sin datos.")
        else:
            t1 = prio_df.copy()
            # FIX: lotes come from RESUMEN (1 row per lote), grouped by BLOQUE+MIX
            if not df_res.empty and "BLOQUE_DOMINANTE" in df_res.columns:
                lotes_bloque = (df_res.groupby(["BLOQUE_DOMINANTE","MIX"])
                                .agg(LOTES=("LOTE_ID","nunique")).reset_index())
                lotes_bloque.columns = ["BLOQUE","MIX","LOTES"]
                t1 = t1.merge(lotes_bloque, on=["BLOQUE","MIX"], how="left").fillna({"LOTES":0})
            else:
                t1["LOTES"] = 0
            t1["LOTES"] = t1["LOTES"].astype(int)
            t1["% ASIGNADO"] = (t1["LBS_ASIGNADAS"] / t1["LBS_BASE"].replace(0,pd.NA) * 100).fillna(0).round(1)
            t1 = t1.rename(columns={"BLOQUE":"Prioridad","LBS_BASE":"LBS Planeadas",
                                     "LBS_ASIGNADAS":"LBS Asignadas","LBS_SIN_ASIGNAR":"LBS Sin Asignar"})
            cols = [c for c in ["Prioridad","MIX","LBS Planeadas","LBS Asignadas","LBS Sin Asignar","LOTES","% ASIGNADO"] if c in t1.columns]
            st.dataframe(t1[cols], use_container_width=True, hide_index=True)

    with tb:
        if df_res.empty:
            st.info("Sin datos.")
        else:
            t2 = (df_res.groupby("ANCHOS_UNICOS")
                  .agg(LBS_Asignadas=("LBS_TOTAL","sum"), Lotes=("LOTE_ID","nunique"))
                  .reset_index().sort_values("ANCHOS_UNICOS"))
            t2.columns = ["N° Anchos","LBS Asignadas","Lotes"]
            total = t2["LBS Asignadas"].sum()
            t2["% del Total"] = (t2["LBS Asignadas"] / total * 100).round(1) if total>0 else 0
            st.dataframe(t2, use_container_width=True, hide_index=True)

    with tc:
        if df_res.empty:
            st.info("Sin datos.")
        else:
            t3 = (df_res.groupby(["ANCHOS_UNICOS","CATEGORIA"])
                  .agg(LBS_Asignadas=("LBS_TOTAL","sum"),
                       Lotes=("LOTE_ID","nunique"),
                       Min_LBS_Lote=("LBS_TOTAL","min"),
                       Max_LBS_Lote=("LBS_TOTAL","max"))
                  .reset_index().sort_values(["ANCHOS_UNICOS","CATEGORIA"]))
            t3.columns = ["N° Anchos","Categoría","LBS Asignadas","Lotes","Min LBS Lote","Max LBS Lote"]
            st.dataframe(t3, use_container_width=True, hide_index=True)

    with td:
        if cap_df.empty:
            st.info("Sin datos.")
        else:
            t4 = cap_df.copy()
            if not df_res.empty:
                lotes_cat = (df_res.groupby("CATEGORIA")
                             .agg(LOTES=("LOTE_ID","nunique")).reset_index())
                t4 = t4.merge(lotes_cat, on="CATEGORIA", how="left").fillna({"LOTES":0})
                t4["LOTES"] = t4["LOTES"].astype(int)
            disp = {"CATEGORIA":"Categoría","MINIMO":"Mín LBS","MAXIMO":"Máx LBS","MIX":"MIX",
                    "LBS_ASIGNADAS":"LBS Asignadas","LOTES":"Lotes",
                    "CAPACIDAD":"Capacidad","PCT_OCUPACION":"% Ocupación"}
            t4 = t4.rename(columns=disp)
            st.dataframe(t4[[v for v in disp.values() if v in t4.columns]], use_container_width=True, hide_index=True)

    with te:
        st.markdown("**Resumen dinámico de resultados** — agrupa los lotes generados por los campos que elijas.")
        if df_det.empty:
            st.info("Sin datos de lotes generados.")
        else:
            RES_GROUP_OPTS = [c for c in ["TELA.CUERPO","STYLE","COLOR","COLOR_R","FAMILIA",
                                           "TONO","MIX","BLOQUE","APLICA_REGLA","CATEGORIA",
                                           "PRIORIDAD","ANCHOS_LOTE","LNK"]
                              if c in df_det.columns]
            RES_METRIC_OPTS = {"LBS Asignadas":"LBS_ASIGNADAS","Docenas":"DOCENAS"}
            valid_res_m = {k:v for k,v in RES_METRIC_OPTS.items() if v in df_det.columns}

            re1,re2,re3 = st.columns([3,2,1])
            rg_sel  = re1.multiselect("Agrupar por",RES_GROUP_OPTS,
                                       default=["MIX","BLOQUE"] if "BLOQUE" in df_det.columns else RES_GROUP_OPTS[:1],
                                       key="rg_sel")
            rm_sel  = re2.selectbox("Métrica",list(valid_res_m.keys()),key="rm_sel")
            rn_top  = re3.number_input("Top N",5,500,50,key="rn_top")

            if rg_sel and rm_sel:
                mc = valid_res_m[rm_sel]
                try:
                    rt = df_det.groupby(rg_sel,as_index=False)[mc].sum()
                    rt = rt.sort_values(mc,ascending=False).head(rn_top)
                    rt[mc] = rt[mc].round(1)
                    total_m = df_det[mc].sum()
                    rt["% del Total"] = (rt[mc]/total_m*100).round(1) if total_m>0 else 0
                    rt = rt.rename(columns={mc:rm_sel})
                    st.dataframe(rt, use_container_width=True, hide_index=True,
                                 height=min(60+35*len(rt),420))
                    st.caption(f"Total {rm_sel}: {total_m:,.1f} · {len(rt)} grupos")
                except Exception as e:
                    st.error(f"Error al agrupar: {e}")

with tab_d:
    if df_det.empty: st.info("Sin lotes.")
    else:
        d1,d2,d3=st.columns(3)
        fm=d1.multiselect("MIX",sorted(df_det["MIX"].unique()),key="dm")
        fr=d2.multiselect("Regla",sorted(df_det["APLICA_REGLA"].unique()),key="dr")
        fb=d3.multiselect("Bloque",sorted(df_det["BLOQUE"].unique()),key="db")
        filt=df_det.copy()
        if fm: filt=filt[filt["MIX"].isin(fm)]
        if fr: filt=filt[filt["APLICA_REGLA"].isin(fr)]
        if fb: filt=filt[filt["BLOQUE"].isin(fb)]
        st.dataframe(filt,use_container_width=True,height=420)
        st.caption(f"{len(filt):,} filas")

with tab_r:
    if df_res.empty: st.info("Sin resumen.")
    else:
        st.dataframe(df_res,use_container_width=True,height=380)
        st.subheader("Capacidad por Categoría")
        cap_df=reports.get("CAPACIDAD_X_CATEG",pd.DataFrame())
        st.dataframe(cap_df,use_container_width=True)

with tab_l:
    dlog=reports.get("DECISION_LOG",pd.DataFrame())
    if dlog.empty: st.info("Sin log.")
    else:
        l1,l2,l3=st.columns(3)
        lnk_s=l1.text_input("LNK contiene",key="ll")
        lr=l2.multiselect("Regla",sorted(dlog["APLICA_REGLA"].unique()) if "APLICA_REGLA" in dlog.columns else [],key="lr")
        lb=l3.multiselect("Bloque",sorted(dlog["BLOQUE"].unique()) if "BLOQUE" in dlog.columns else [],key="lb")
        lf=dlog.copy()
        if lnk_s: lf=lf[lf["LNK"].str.contains(lnk_s,case=False,na=False)]
        if lr: lf=lf[lf["APLICA_REGLA"].isin(lr)]
        if lb: lf=lf[lf["BLOQUE"].isin(lb)]
        st.dataframe(lf,use_container_width=True,height=440)
        with st.expander("📖 Leyenda de códigos de descarte"):
            for code,desc in DESCARTE_MSGS.items():
                st.caption(f"**{code}** — {desc}")
        st.caption(f"{len(lf):,} registros")

with tab_c:
    hist=st.session_state.run_history
    if len(hist)<2: st.info("Corre al menos 2 corridas.")
    else:
        rows=[]
        for i,r in enumerate(hist):
            d=r["detalle"]; s=r["resumen"]; exc=r["excedentes"]
            lc=r["reports"].get("LNK_COMPLETITUD",pd.DataFrame())
            rows.append({
                "#":i+1, "Hora":r["label"],
                "Calidad":r.get("quality_used","?"),
                "Comentario":r.get("comentario","—"),
                "Lotes":len(s),
                "LBS Asignadas":fmt(d["LBS_ASIGNADAS"].sum() if not d.empty else 0),
                "LBS Excedentes":fmt(exc["LBS_RESTANTES"].sum() if not exc.empty else 0),
                "Cap. Perdida":fmt(s["CAPACIDAD_PERDIDA"].sum() if not s.empty else 0),
                "LNKs %":f"{(lc['ESTADO'].isin(['COMPLETO','COMPLETO (SCRAP)']).sum()/len(lc)*100) if not lc.empty else 0:.1f}%",
                "Cancelada":"Sí" if r.get("cancelled") else "No",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.subheader("Descargar corridas")
        for i,r in enumerate(hist):
            lbl=f"⬇ #{i+1} {r['label']}" + (f" — {r['comentario']}" if r.get("comentario") else "")
            fn=f"RESULTADOS_LOTES_{r['ts'].replace(':','').replace(' ','_').replace('-','')}.xlsx"
            st.download_button(lbl, data=export_excel(r), file_name=fn,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key=f"dl_{i}")


with tab_e:
    if df_exc.empty: st.success("✅ Sin excedentes.")
    else:
        st.warning(f"⚠️ {len(df_exc):,} filas sin asignar")
        st.dataframe(df_exc,use_container_width=True,height=420)

st.divider()
ts=res["ts"].replace(":","").replace(" ","_").replace("-","")
st.download_button("⬇  Descargar Excel completo",data=export_excel(res),
                   file_name=f"RESULTADOS_LOTES_{ts}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   use_container_width=True)
