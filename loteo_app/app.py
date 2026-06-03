"""NV2 Loteo Tintorería v4"""
import io, sys, os, json, threading
from datetime import datetime
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

# ── VERSION FINGERPRINT (diagnóstico) ─────────────────────────────────────
import inspect
from engine import loteo as _loteo_mod
_LOTEO_FILE = inspect.getfile(_loteo_mod)
_HAS_PREFILTER = hasattr(_loteo_mod, '__file__') and 'lnks_sin_dispon' in open(_loteo_mod.__file__).read()
# ──────────────────────────────────────────────────────────────────────────

from engine.loader import load_inputs
from engine.loteo  import run_loteo, build_reports, quality_to_beam, DESCARTE_MSGS
from engine.disponibilidad import load_disponibilidad
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
    "tbl_version": 0,
    "_uploader_last_hash": None,
    # Modo restricción de tejido
    "modo_restriccion": False,
    "dispon_index": None,
    "inv_file_name": None,
}.items():
    if k not in st.session_state: st.session_state[k]=v

def _bump(): st.session_state["tbl_version"] += 1

def fmt(v):
    try: return f"{int(round(float(v))):,}"
    except: return "0"

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
    if t is None:
        df = factory()
    else:
        df = t.copy()   # always work on a copy, never mutate session_state in-place
    if factory == empty_cap:
        df = _fix_bool_cols(df)
    return df

# ── Profile helpers ────────────────────────────────────────────────────────
def df2j(df): return [] if (df is None or df.empty) else df.where(pd.notna(df),None).to_dict("records")
def _fix_numeric_cols(df, cols):
    """Convierte columnas a float — reemplaza None/NaN con pd.NA para NumberColumn."""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

# Columnas numéricas por tipo de tabla
_NUM_COLS = {
    "ra":   ["ANCHO_1","ANCHO_2","CAPACIDAD_PRIORIDAD_1","CAPACIDAD_PRIORIDAD_2","CAPACIDAD_PRIORIDAD_3"],
    "ras":  ["LIMITE_ANCHO","PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"],
    "rc":   ["PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3"],
    "rf":   ["PRIORIDAD_1","PRIORIDAD_2","PRIORIDAD_3","PRIORIDAD_4"],
    "cap":  ["MINIMO","MAXIMO","CAPACIDAD","MIN_DIFF","MAX_DIFF","MAX_WIDTHS","MAX_SKU",
             "MAX_SALTO_RANGO","SPLIT_MIN_LBS","OVERSHOOT_TOL_PCT","UNDERSHOOT_TOL_PCT"],
}

def j2df(r, f):
    df = pd.DataFrame(r) if r else f()
    if f == empty_cap:
        df = _fix_bool_cols(df)
        df = _fix_numeric_cols(df, _NUM_COLS["cap"])
    elif f == empty_ra:
        df = _fix_numeric_cols(df, _NUM_COLS["ra"])
    elif f == empty_ras:
        df = _fix_numeric_cols(df, _NUM_COLS["ras"])
    elif f == empty_rc:
        df = _fix_numeric_cols(df, _NUM_COLS["rc"])
    elif f == empty_rf:
        df = _fix_numeric_cols(df, _NUM_COLS["rf"])
    return df

def build_profile(overrides):
    """Guarda SOLO configuración — nunca el archivo de datos."""
    p={"overrides":overrides,"created":datetime.now().isoformat(),"notes":"",
       "tables":{
           "capacidades":          df2j(get_tbl("tbl_capacidades",empty_cap)),
           "reglas_anchos":        df2j(get_tbl("tbl_reglas_anchos",empty_ra)),
           "restricciones_ancho":  df2j(get_tbl("tbl_restricciones_ancho",empty_ras)),
           "restricciones_color":  df2j(get_tbl("tbl_restricciones_color",empty_rc)),
           "restricciones_familia":df2j(get_tbl("tbl_restricciones_familia",empty_rf)),
           "combinaciones":        df2j(get_tbl("tbl_combinaciones",empty_comb)),
       }}
    return p

def apply_profile(profile):
    """Carga SOLO configuración — no toca el archivo de datos cargado."""
    t=profile.get("tables",{})
    st.session_state.tbl_capacidades          =j2df(t.get("capacidades"),empty_cap)
    st.session_state.tbl_reglas_anchos        =j2df(t.get("reglas_anchos"),empty_ra)
    st.session_state.tbl_restricciones_ancho  =j2df(t.get("restricciones_ancho"),empty_ras)
    st.session_state.tbl_restricciones_color  =j2df(t.get("restricciones_color"),empty_rc)
    st.session_state.tbl_restricciones_familia=j2df(t.get("restricciones_familia"),empty_rf)
    st.session_state.tbl_combinaciones        =j2df(t.get("combinaciones"),empty_comb)
    st.session_state.cfg=profile.get("overrides",{})
    st.session_state.cap_applied=False
    _bump()   # fuerza recreación de todos los data_editors

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
            # Enforce correct dtypes to avoid NumberColumn type errors
            if factory == empty_ra:  df = _fix_numeric_cols(df, _NUM_COLS["ra"])
            elif factory == empty_ras: df = _fix_numeric_cols(df, _NUM_COLS["ras"])
            elif factory == empty_rc:  df = _fix_numeric_cols(df, _NUM_COLS["rc"])
            elif factory == empty_rf:  df = _fix_numeric_cols(df, _NUM_COLS["rf"])
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
    _bump()   # fuerza recreación de todos los data_editors

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
        # Hojas de restricción de tejido (solo modo restricción)
        df_tej = result.get("detalle_tejido", pd.DataFrame())
        df_stk = result.get("stock_tejido",   pd.DataFrame())
        if not df_tej.empty: df_tej.to_excel(w,index=False,sheet_name="DETALLE_TEJIDO")
        if not df_stk.empty: df_stk.to_excel(w,index=False,sheet_name="STOCK_TEJIDO")
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("🧶 NV2 Loteo")
    # ── Diagnóstico de versión ──────────────────────────────────────────
    if _HAS_PREFILTER:
        st.success("✅ v5 activa — pre-filtro tejido OK")
    else:
        st.error(f"❌ loteo.py SIN pre-filtro — archivo: {_LOTEO_FILE}")
    # ───────────────────────────────────────────────────────────────────
    st.divider()

    # ── Modo de loteo ──────────────────────────────────────────────────────
    st.subheader("🔀 Modo de loteo")
    modo_sel = st.radio(
        "Selecciona el modo:",
        options=["🟢 Modo Libre", "🔵 Modo Restricción de Tejido"],
        index=1 if st.session_state.modo_restriccion else 0,
        help=(
            "**Modo Libre:** loteo sin restricción de disponibilidad de tela (comportamiento original).\n\n"
            "**Modo Restricción:** solo forma lotes con tela disponible (inventario en mano + "
            "producción planeada por día). Requiere cargar el archivo ANALISIS_INV."
        ),
        key="modo_radio",
    )
    nuevo_modo = (modo_sel == "🔵 Modo Restricción de Tejido")
    if nuevo_modo != st.session_state.modo_restriccion:
        st.session_state.modo_restriccion = nuevo_modo
        st.session_state.dispon_index = None   # reset al cambiar de modo

    # Uploader de ANALISIS_INV (solo en modo restricción)
    if st.session_state.modo_restriccion:
        st.markdown(
            '<div class="info-note">📋 Sube el archivo <strong>ANALISIS_INV</strong> (hoja Export) '
            'con el inventario y plan de tejido de los próximos 10 días.</div>',
            unsafe_allow_html=True,
        )
        inv_up = st.file_uploader(
            "ANALISIS_INV (.xlsx)",
            type=["xlsx","xlsm"],
            key="inv_uploader",
        )
        if inv_up is not None:
            try:
                dispon = load_disponibilidad(inv_up.read())
                st.session_state.dispon_index = dispon
                st.session_state.inv_file_name = inv_up.name
                st.success(f"✅ {inv_up.name} — {len(dispon.stock):,} registros de disponibilidad cargados.")
            except Exception as e:
                st.error(f"Error leyendo ANALISIS_INV: {e}")
                st.session_state.dispon_index = None

        if st.session_state.dispon_index is not None and st.session_state.inv_file_name:
            st.caption(f"📂 Activo: {st.session_state.inv_file_name}")
        elif st.session_state.dispon_index is None:
            st.markdown(
                '<div class="warn-note">⚠️ Sin ANALISIS_INV cargado. El loteo en modo restricción '
                'no podrá ejecutarse.</div>',
                unsafe_allow_html=True,
            )

    st.divider()
    # Profiles
    st.subheader("💾 Perfiles")
    profiles=st.session_state.profiles

    # Importar JSON PRIMERO para que el perfil quede disponible
    # en el selectbox de la misma ejecución
    json_up=st.file_uploader("Importar JSON",type=["json"],key="json_upload")
    if json_up:
        try:
            loaded=json.load(json_up)
            nm=json_up.name.replace(".json","")
            if nm not in profiles:   # solo importar si es nuevo
                profiles[nm]=loaded
                st.session_state.profiles=profiles
                st.rerun()           # rerun para que aparezca en el selector
            else:
                st.caption(f"'{nm}' ya existe en sesión.")
        except Exception as e: st.error(str(e))

    pnames=list(profiles.keys())
    if pnames:
        sel=st.selectbox("Cargar perfil",["— seleccionar —"]+pnames,key="sel_profile")
        if sel!="— seleccionar —" and st.button("📥 Aplicar",use_container_width=True):
            apply_profile(profiles[sel]); st.rerun()
    else:
        st.caption("Sin perfiles guardados. Importa un JSON o guarda uno desde los ajustes.")

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
        st.divider()
        lookahead  =st.checkbox("LOOKAHEAD_VENCIDOS",
                                value=bool(int(cv("LOOKAHEAD_VENCIDOS",1))),
                                help="Antes de confirmar un lote que contiene VENCIDOS, verifica que "
                                     "las LBS vencidas restantes en el grupo puedan formar al menos "
                                     "otro lote válido. Evita dejar VENCIDOS huérfanos.")
        st.divider()
        simples    =st.checkbox("PREFERIR_LOTES_SIMPLES",
                                value=bool(int(cv("PREFERIR_LOTES_SIMPLES",0))),
                                help="Penaliza lotes con muchos anchos o LNKs. "
                                     "El algoritmo prefiere lotes simples sin prohibir los complejos.")
        if simples:
            pen_ancho  =st.slider("Penalización por ancho extra",
                                   min_value=0.1, max_value=5.0,
                                   value=float(cv("PENALIZACION_ANCHO_EXTRA",1.5)),
                                   step=0.1,
                                   help="Cuánto se penaliza cada ancho adicional más allá del primero")
            pen_lnk    =st.slider("Penalización por LNK extra",
                                   min_value=0.1, max_value=5.0,
                                   value=float(cv("PENALIZACION_LNK_EXTRA",0.8)),
                                   step=0.1,
                                   help="Cuánto se penaliza cada LNK adicional más allá del primero")
        else:
            pen_ancho = float(cv("PENALIZACION_ANCHO_EXTRA", 1.5))
            pen_lnk   = float(cv("PENALIZACION_LNK_EXTRA",   0.8))

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
        "LOOKAHEAD_VENCIDOS":int(lookahead),
        "PREFERIR_LOTES_SIMPLES":int(simples),
        "PENALIZACION_ANCHO_EXTRA":pen_ancho,
        "PENALIZACION_LNK_EXTRA":pen_lnk,
        "AGRUPAR_POR_TONO":int(agrupar_tono),"APPLY_RULES_BLEACH":int(apply_bleach),
        "RULE_ORDER":rule_order,"PRIORITY_ORDER":priority_order,
    }

    st.divider()
    # ── Calidad del Loteo ──────────────────────────────────────────────────
    st.subheader("🎯 Calidad del Loteo")
    _p_ql  = st.session_state.params or {}
    _cfg_ql= st.session_state.cfg or {}
    _cv_ql = lambda k,d: _cfg_ql.get(k, _p_ql.get(k,d))
    quality_level = st.slider(
        "Nivel de calidad", min_value=1, max_value=10,
        value=int(_cv_ql("QUALITY_LEVEL", 5)),
        key="quality_slider",
        help="1 = Muy rápido · 10 = Óptimo (más lento)",
    )
    st.session_state["_quality_level"] = quality_level
    _beam_sb = quality_to_beam(quality_level)
    st.caption(f"BEAM_WIDTH: **{_beam_sb}** · {'🟢 Rápido' if quality_level<=3 else '🟡 Balanceado' if quality_level<=6 else '🔴 Óptimo'}")
    if st.session_state.df_data is not None:
        _df_est = st.session_state.df_data
        _grp_est = _df_est.groupby(["TELA.CUERPO","MIX"]).ngroups
        _est_sec = _grp_est * _beam_sb * 0.075
        st.caption(f"Grupos: {_grp_est:,} · Estimado: ~{_est_sec/60:.1f} min")

    st.divider()
    # ── Guardar Perfil ─────────────────────────────────────────────────────
    st.subheader("💾 Guardar Perfil")
    _all_ov_sb = {**adv_overrides, "QUALITY_LEVEL": quality_level}
    _pname_sb  = st.text_input("Nombre", placeholder="ej. Semana23_DYE", key="pname")
    _notes_sb  = st.text_input("Notas",  placeholder="Descripción",       key="pnotes")
    if st.button("💾 Guardar en sesión", use_container_width=True, key="btn_guardar_perfil"):
        _nm = _pname_sb.strip()
        if _nm:
            _pr = build_profile(_all_ov_sb); _pr["notes"] = _notes_sb
            if _nm in profiles:
                _old_p = profiles[_nm].get("overrides",{})
                _diff  = {k:{"antes":_old_p.get(k),"ahora":_all_ov_sb.get(k)}
                          for k in set(_old_p)|set(_all_ov_sb)
                          if _old_p.get(k) != _all_ov_sb.get(k)}
                _pr["diff_vs_anterior"] = _diff
            profiles[_nm] = _pr
            st.session_state.profiles = profiles
            st.success(f"'{_nm}' guardado")
        else:
            st.warning("Ingresa un nombre")
    _pr_json_sb = json.dumps(build_profile(_all_ov_sb), indent=2, ensure_ascii=False)
    st.download_button("📤 Exportar JSON", data=_pr_json_sb,
                       file_name=(_pname_sb.strip() or "perfil")+".json",
                       mime="application/json", use_container_width=True,
                       key="btn_export_json")

# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════
st.markdown("## 🧶 NV2 Loteo Tintorería")
st.caption("Optimización · Lotes · Asignación de Pedidos")

# ── Sección 1: Carga ──────────────────────────────────────────────────────
st.markdown("### 📁 Sección 1 — Carga de Archivo")
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
    import hashlib
    fb = uploaded.read()
    # Identificamos el archivo por hash MD5 del contenido.
    # Recargamos SIEMPRE que el contenido cambie, sin importar nombre ni tamaño.
    # No usamos raw_file_bytes para comparar porque ese puede venir de un perfil.
    _content_hash = hashlib.md5(fb).hexdigest()
    _uploader_hash = st.session_state.get("_uploader_last_hash", None)

    if _content_hash != _uploader_hash:
        st.session_state["_uploader_last_hash"] = _content_hash
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
st.markdown("### 📋 Sección 2 — Capacidad y Validación")
st.markdown('<div class="info-note">✏️ Edita la tabla y presiona <b>Aplicar cambios de capacidad</b>. '
            'Los parámetros por fila sobreescriben los globales para ese tamaño de lote.</div>',
            unsafe_allow_html=True)

tbl_cap=get_tbl("tbl_capacidades",empty_cap)

# Toggle vista básica / avanzada
_vista_avanzada = st.checkbox("Mostrar parámetros avanzados por categoría",
                               value=False, key="cap_vista_avanzada")

# Columnas básicas siempre visibles
_basic_cols = ["CATEGORIA","MINIMO","MAXIMO","CAPACIDAD","MIX"]
# Columnas avanzadas solo en vista avanzada
_adv_cols   = ["MIN_DIFF","MAX_DIFF","MAX_WIDTHS","MAX_SKU","WIDTHS_TARGET_ORDER",
               "OVERSHOOT","UNDERSHOOT","PERMITIR_RANGO_SUPERIOR","MAX_SALTO_RANGO",
               "SCRAP_REMAINDER","APPLY_RULES_BLEACH","SPLIT_MIN_LBS",
               "OVERSHOOT_TOL_PCT","UNDERSHOOT_TOL_PCT"]
_show_cols  = _basic_cols + (_adv_cols if _vista_avanzada else [])
_tbl_show   = tbl_cap[[c for c in _show_cols if c in tbl_cap.columns]].copy()

# Height: 7 rows basic, 10 rows advanced
h = 310 if not _vista_avanzada else max(415, min(60+35*max(len(tbl_cap),1),600))

_col_cfg_basic = {
    "CATEGORIA": st.column_config.TextColumn("Categoría", width="small"),
    "MINIMO":    st.column_config.NumberColumn("Mín LBS",  format="%d"),
    "MAXIMO":    st.column_config.NumberColumn("Máx LBS",  format="%d"),
    "CAPACIDAD": st.column_config.NumberColumn("Capacidad Total", format="%d"),
    "MIX":       st.column_config.SelectboxColumn("MIX", options=["DYE","BLEACH"]),
}
_col_cfg_adv = {
    "MIN_DIFF":   st.column_config.NumberColumn("Min Diff", format="%.1f"),
    "MAX_DIFF":   st.column_config.NumberColumn("Max Diff", format="%.1f"),
    "MAX_WIDTHS": st.column_config.NumberColumn("Max Anchos", format="%d"),
    "MAX_SKU":    st.column_config.NumberColumn("Max SKU", format="%d"),
    "WIDTHS_TARGET_ORDER": st.column_config.TextColumn("Orden Anchos"),
    "OVERSHOOT":  st.column_config.CheckboxColumn("Overshoot"),
    "UNDERSHOOT": st.column_config.CheckboxColumn("Undershoot"),
    "PERMITIR_RANGO_SUPERIOR": st.column_config.CheckboxColumn("Rango Sup."),
    "MAX_SALTO_RANGO": st.column_config.NumberColumn("Max Salto", format="%d"),
    "SCRAP_REMAINDER":  st.column_config.CheckboxColumn("Scrap"),
    "APPLY_RULES_BLEACH": st.column_config.CheckboxColumn("Bleach"),
    "SPLIT_MIN_LBS":    st.column_config.NumberColumn("Split Min", format="%d"),
    "OVERSHOOT_TOL_PCT": st.column_config.NumberColumn("Tol Over%", format="%.1f%%"),
    "UNDERSHOOT_TOL_PCT":st.column_config.NumberColumn("Tol Under%",format="%.1f%%"),
}
_col_cfg = {**_col_cfg_basic, **(_col_cfg_adv if _vista_avanzada else {})}

cap_ed=st.data_editor(
    _tbl_show, num_rows="dynamic", use_container_width=True, height=h,
    key=f'editor_cap_{st.session_state["tbl_version"]}{"_adv" if _vista_avanzada else "_bas"}',
    column_config=_col_cfg,
)
# Merge edited basic cols back into full table
if not _vista_avanzada:
    _full = tbl_cap.copy()
    for c in _basic_cols:
        if c in cap_ed.columns and c in _full.columns:
            # align by position (same rows)
            _full = _full.reset_index(drop=True)
            cap_ed = cap_ed.reset_index(drop=True)
            _full[c] = cap_ed[c]
    # Handle added/deleted rows: if cap_ed has more rows, append with defaults
    if len(cap_ed) != len(_full):
        cap_ed_full = cap_ed.copy()
        for c in _adv_cols:
            if c not in cap_ed_full.columns:
                cap_ed_full[c] = CAP_DEFAULTS.get(c, None)
        _full = cap_ed_full
    cap_ed = _full

b_col,s_col=st.columns([1,3])
with b_col:
    if st.button("✅ Aplicar cambios de capacidad",type="primary",use_container_width=True):
        # FIX: guardamos cap_ed (ediciones del usuario en pantalla)
        # pero si hay un perfil recién cargado (tbl_version cambió),
        # cap_ed ya contiene los datos del perfil correctamente.
        # Guardamos siempre lo que el editor muestra.
        st.session_state.tbl_capacidades = cap_ed
        st.session_state.cap_applied = True
        # NO bumpeamos version aquí — solo guardamos y confirmamos
        st.rerun()
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


# _ql ya viene del sidebar (quality_slider en sidebar)
_ql=st.session_state.get("_quality_level", int((st.session_state.cfg or {}).get("QUALITY_LEVEL",5)))
section3_overrides={"QUALITY_LEVEL":_ql}

# ── Sección 3 (anteriormente aquí, movida al sidebar) ─────────────────────

# ── Sección 4: Reglas ──────────────────────────────────────────────────────
with st.expander("🔗  Sección 4 — Reglas de Combinación y Restricciones", expanded=False):
    rt1,rt2,rt3,rt4,rt5=st.tabs([
        "🔗 Anchos Combinados","📐 Restricciones Ancho",
        "🎨 Restricciones Color","👨‍👩‍👧 Restricciones Familia","⚖️ Combinaciones Prioridad"])

    with rt1:
        st.markdown('<div class="info-note">Pares de anchos que pueden combinarse + prioridades de tamaño. Vacía = libre.</div>',unsafe_allow_html=True)
        ra_ed=st.data_editor(get_tbl("tbl_reglas_anchos",empty_ra),num_rows="dynamic",
                             use_container_width=True,key=f'editor_ra_{st.session_state["tbl_version"]}',
                             column_config={
                                 "ANCHO_1":st.column_config.NumberColumn("Ancho 1",format="%.1f"),
                                 "ANCHO_2":st.column_config.NumberColumn("Ancho 2",format="%.1f"),
                                 "CAPACIDAD_PRIORIDAD_1":st.column_config.NumberColumn("Prioridad 1 (LBS)",format="%d"),
                                 "CAPACIDAD_PRIORIDAD_2":st.column_config.NumberColumn("Prioridad 2 (LBS)",format="%d"),
                                 "CAPACIDAD_PRIORIDAD_3":st.column_config.NumberColumn("Prioridad 3 (LBS)",format="%d"),
                             })
        if st.button("💾 Guardar",key="save_ra"): st.session_state.tbl_reglas_anchos=ra_ed; st.rerun()

    with rt2:
        st.markdown('<div class="info-note">Si el STYLE tiene un ancho ≤ LIMITE_ANCHO, prioriza los tamaños indicados. '
                    'Reemplaza la antigua regla ANCHO18.</div>',unsafe_allow_html=True)
        ras_ed=st.data_editor(get_tbl("tbl_restricciones_ancho",empty_ras),num_rows="dynamic",
                              use_container_width=True,key=f'editor_ras_{st.session_state["tbl_version"]}',
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
        if st.button("💾 Guardar",key="save_ras"): st.session_state.tbl_restricciones_ancho=ras_ed; st.rerun()

    with rt3:
        rc_ed=st.data_editor(get_tbl("tbl_restricciones_color",empty_rc),num_rows="dynamic",
                             use_container_width=True,key=f'editor_rc_{st.session_state["tbl_version"]}',
                             column_config={
                                 "COLOR_R":st.column_config.TextColumn("COLOR_R",width="medium"),
                                 "PRIORIDAD_1":st.column_config.NumberColumn("Prioridad 1 (LBS)",format="%d"),
                                 "PRIORIDAD_2":st.column_config.NumberColumn("Prioridad 2 (LBS)",format="%d"),
                                 "PRIORIDAD_3":st.column_config.NumberColumn("Prioridad 3 (LBS)",format="%d"),
                             })
        if st.button("💾 Guardar",key="save_rc"): st.session_state.tbl_restricciones_color=rc_ed; st.rerun()

    with rt4:
        rf_ed=st.data_editor(get_tbl("tbl_restricciones_familia",empty_rf),num_rows="dynamic",
                             use_container_width=True,key=f'editor_rf_{st.session_state["tbl_version"]}',
                             column_config={
                                 "FAMILIA":st.column_config.TextColumn("FAMILIA",width="medium"),
                                 "PRIORIDAD_1":st.column_config.NumberColumn("Prioridad 1 (LBS)",format="%d"),
                                 "PRIORIDAD_2":st.column_config.NumberColumn("Prioridad 2 (LBS)",format="%d"),
                                 "PRIORIDAD_3":st.column_config.NumberColumn("Prioridad 3 (LBS)",format="%d"),
                                 "PRIORIDAD_4":st.column_config.NumberColumn("Prioridad 4 (LBS)",format="%d"),
                             })
        if st.button("💾 Guardar",key="save_rf"): st.session_state.tbl_restricciones_familia=rf_ed; st.rerun()

    with rt5:
        st.markdown('<div class="info-note">Tabla vacía = NO se mezclan bloques de prioridad. '
                    'Agrega pares que SÍ pueden coexistir.</div>',unsafe_allow_html=True)
        BLOQUES=["VENCIDOS","AHEAD","AHEAD2","OTROS"]
        comb_ed=st.data_editor(get_tbl("tbl_combinaciones",empty_comb),num_rows="dynamic",
                               use_container_width=True,key=f'editor_comb_{st.session_state["tbl_version"]}',
                               column_config={
                                   "PRIORIDAD_1":st.column_config.SelectboxColumn("Bloque 1",options=BLOQUES),
                                   "PRIORIDAD_2":st.column_config.SelectboxColumn("Bloque 2",options=BLOQUES),
                               })
        if st.button("💾 Guardar",key="save_comb"): st.session_state.tbl_combinaciones=comb_ed; st.rerun()

# (Perfil guardado desde el sidebar)



# (seccion_solver)
st.markdown("### ⚗️ Sección 5 — Loteo")
# ── Botón principal ────────────────────────────────────────────────────────
#  st.divider()
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
  _run_start = datetime.now()

  with st.spinner("Preparando parámetros…"):
      try:
          df_data2,df_cap2,params2,_=load_inputs(io.BytesIO(st.session_state.raw_file_bytes),
                                                  param_overrides=all_overrides)
          # Parámetros que el loader no conoce → aplicar directamente sobre params2
          params2["QUALITY_LEVEL"]          = int(all_overrides.get("QUALITY_LEVEL", 5))
          params2["BEAM_WIDTH"]             = quality_to_beam(params2["QUALITY_LEVEL"])
          params2["LOOKAHEAD_VENCIDOS"]     = int(all_overrides.get("LOOKAHEAD_VENCIDOS", 1))
          params2["PREFERIR_LOTES_SIMPLES"] = int(all_overrides.get("PREFERIR_LOTES_SIMPLES", 0))
          params2["PENALIZACION_ANCHO_EXTRA"]= float(all_overrides.get("PENALIZACION_ANCHO_EXTRA", 1.5))
          params2["PENALIZACION_LNK_EXTRA"]  = float(all_overrides.get("PENALIZACION_LNK_EXTRA", 0.8))
          params2["OVERSHOOT_ENABLE"]        = int(all_overrides.get("OVERSHOOT_ENABLE", 1))
          params2["UNDERSHOOT_ENABLE"]       = int(all_overrides.get("UNDERSHOOT_ENABLE", 1))
          params2["OVERSHOOT_TOL_PCT_SMALL"] = float(all_overrides.get("OVERSHOOT_TOL_PCT_SMALL", 0.05))
          params2["OVERSHOOT_TOL_PCT_LARGE"] = float(all_overrides.get("OVERSHOOT_TOL_PCT_LARGE", 0.02))
          params2["OVERSHOOT_SMALL_THRESHOLD"]= float(all_overrides.get("OVERSHOOT_SMALL_THRESHOLD", 5000))

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

  _lbs_plan_total = float(df_data2["TOTAL"].sum()) if not df_data2.empty else 0.0

  def cb(pct,msg,stats):
      prog.progress(min(pct,0.99),text=msg)
      lbs_asig  = float(stats.get('lbs', 0))
      pct_asig  = (lbs_asig / _lbs_plan_total * 100) if _lbs_plan_total > 0 else 0.0
      _elapsed  = (datetime.now() - _run_start).seconds
      _mins, _secs = divmod(_elapsed, 60)
      stat.markdown(
          f"**Grupo** {stats['grupo']}/{stats['total']} · "
          f"**Lotes:** {stats['lotes']:,} · "
          f"**LBS Plan:** {fmt(_lbs_plan_total)} · "
          f"**LBS Asignadas:** {fmt(lbs_asig)} · "
          f"**% Asignado:** {pct_asig:.1f}% · "
          f"**Tiempo:** {_mins:02d}:{_secs:02d}"
      )

  try:
      # Validar modo restricción antes de correr
      _dispon = None
      if st.session_state.modo_restriccion:
          if st.session_state.dispon_index is None:
              st.error("⚠️ Modo Restricción activo pero no hay ANALISIS_INV cargado. "
                       "Sube el archivo en el sidebar antes de ejecutar.")
              st.session_state.running = False
              st.stop()
          _dispon = st.session_state.dispon_index

      df_det,df_res,df_exc,df_par,cancelled,df_tej,df_stock=run_loteo(
          df_data2,df_cap2,params2,progress_callback=cb,
          cancel_flag=st.session_state.cancel_flag,
          dispon_index=_dispon)
      _elapsed_total = (datetime.now() - _run_start).seconds
      _tm, _ts = divmod(_elapsed_total, 60)
      if cancelled:
          prog.progress(1.0,text=f"⏹ Cancelado — resultados parciales ({_tm:02d}:{_ts:02d})")
      else:
          prog.progress(1.0,text=f"✅ Loteo completado en {_tm:02d}:{_ts:02d}")
      stat.empty()
  except Exception as e:
      st.error(f"Error en loteo: {e}"); st.session_state.running=False; st.stop()

  result={"ts":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          "label":datetime.now().strftime("%H:%M:%S"),
          "comentario":st.session_state.get("run_comment",""),
          "quality_used":_ql,
          "tiempo_seg": (datetime.now()-_run_start).seconds,
          "modo": "Restricción" if st.session_state.modo_restriccion else "Libre",
          "detalle":df_det,"resumen":df_res,"excedentes":df_exc,
          "params_out":df_par,
          "detalle_tejido":df_tej,"stock_tejido":df_stock,
          "reports":build_reports(df_data2,df_cap2,df_det,df_res),
          "cancelled":cancelled}
  st.session_state.last_result=result
  h=st.session_state.run_history; h.append(result)
  if len(h)>5: h.pop(0)
  st.session_state.run_history=h
  st.session_state.running=False
  st.rerun()

# ── Resultados (fuera del expander, nivel raíz) ────────────────────────────
if st.session_state.last_result is None: st.stop()

st.divider()
st.markdown("### 📊 Resultados")

# ── Selector de corrida ────────────────────────────────────────────────────
_hist = st.session_state.run_history
if len(_hist) > 1:
    _opts = []
    for i, r in enumerate(_hist):
        _t = r.get("tiempo_seg",0); _rm,_rs = divmod(int(_t),60)
        _lbl = (f"#{i+1} · {r['label']} · Calidad {r.get('quality_used','?')} · "
                f"{_rm:02d}:{_rs:02d}"
                + (f" · 💬 {r['comentario']}" if r.get("comentario") else ""))
        _opts.append(_lbl)
    _sel_idx = st.selectbox(
        "Ver corrida", range(len(_opts)),
        format_func=lambda i: _opts[i],
        index=len(_hist)-1,   # última por default
        key="corrida_sel"
    )
    res = _hist[_sel_idx]
else:
    res = st.session_state.last_result

df_det=res["detalle"]; df_res=res["resumen"]
df_exc=res["excedentes"]; reports=res["reports"]
lnk_df=reports.get("LNK_COMPLETITUD",pd.DataFrame())

if res.get("cancelled"):
    st.warning("⚠️ Corrida cancelada — resultados parciales")

_modo_badge = res.get("modo","Libre")
_badge_color = "#3b82f6" if _modo_badge == "Restricción" else "#16a34a"
st.markdown(
    f'<span style="background:{_badge_color};color:#fff;padding:3px 10px;border-radius:12px;'
    f'font-size:.82rem;font-weight:600;">Modo: {_modo_badge}</span>',
    unsafe_allow_html=True,
)

_comment=res.get("comentario","")
_ql_used=res.get("quality_used","?")
_t_seg=res.get("tiempo_seg",0)
_tm,_ts=divmod(int(_t_seg),60)
st.caption(f"Corrida: {res['ts']}  ·  Calidad: {_ql_used}  ·  Tiempo: {_tm:02d}:{_ts:02d}  {'·  💬 '+_comment if _comment else ''}")

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

tab_g,tab_d,tab_r,tab_l,tab_c,tab_e,tab_t=st.tabs([
    "📊 Gráficas","📋 Detalle Lotes","📄 Resumen",
    "🔍 Decision Log","🔁 Comparar Corridas","⚠️ Excedentes",
    "🧵 Disponibilidad Tejido"])

with tab_g:
    cap_df  = reports.get("CAPACIDAD_X_CATEG", pd.DataFrame())
    prio_df = reports.get("PRIORIDAD_VS_ASIG", pd.DataFrame())

    # ── Gráficas existentes ────────────────────────────────────────────────
    c1,c2=st.columns(2)
    with c1: st.plotly_chart(chart_capacidad_barras(cap_df), use_container_width=True)
    with c2: st.plotly_chart(chart_bloques_donut(prio_df),   use_container_width=True)
    c3,c4=st.columns(2)
    with c3: st.plotly_chart(chart_heatmap_capacidad(cap_df),  use_container_width=True)
    with c4: st.plotly_chart(chart_completitud_lnk(lnk_df),    use_container_width=True)

    st.divider()

    # ── Donuts: Anchos y LNKs ──────────────────────────────────────────────
    st.markdown("#### 🍩 Distribución de Lotes")

    if df_res.empty:
        st.info("Sin datos de lotes.")
    else:
        _f1,_f2,_f3 = st.columns(3)
        _mix_opts = ["Todos"] + sorted(df_res["MIX"].unique().tolist())
        _cat_opts = ["Todas"] + sorted(df_res["CATEGORIA"].dropna().unique().tolist())
        _blq_opts = ["Todos"] + (sorted(df_res["BLOQUE_DOMINANTE"].dropna().unique().tolist()) if "BLOQUE_DOMINANTE" in df_res.columns else [])
        _f_mix = _f1.selectbox("MIX",       _mix_opts, key="dg_mix")
        _f_cat = _f2.selectbox("Categoría", _cat_opts, key="dg_cat")
        _f_blq = _f3.selectbox("Bloque",    ["Todos"]+_blq_opts, key="dg_blq")

        # Filter once — pass as frozen tuple to cached functions
        _df_f = df_res.copy()
        if _f_mix != "Todos":  _df_f = _df_f[_df_f["MIX"]==_f_mix]
        if _f_cat != "Todas":  _df_f = _df_f[_df_f["CATEGORIA"]==_f_cat]
        if _f_blq != "Todos" and "BLOQUE_DOMINANTE" in _df_f.columns:
            _df_f = _df_f[_df_f["BLOQUE_DOMINANTE"]==_f_blq]

        d_left, d_right = st.columns(2)

        with d_left:
            st.markdown("**Por cantidad de anchos**")
            if _df_f.empty:
                st.info("Sin datos.")
            else:
                import plotly.graph_objects as go
                _anc = (_df_f.groupby("ANCHOS_UNICOS")
                        .agg(Lotes=("LOTE_ID","nunique"), LBS=("LBS_TOTAL","sum"))
                        .reset_index().sort_values("ANCHOS_UNICOS"))
                _colors = ["#9FE1CB","#1D9E75","#0F6E56","#04342C","#B5D4F4","#378ADD"]
                _fig1 = go.Figure(go.Pie(
                    labels=[f"{int(r.ANCHOS_UNICOS)} ancho{'s' if r.ANCHOS_UNICOS>1 else ''}" for _,r in _anc.iterrows()],
                    values=_anc["Lotes"], hole=0.55,
                    marker_colors=_colors[:len(_anc)], textinfo="label+percent",
                    hovertemplate="<b>%{label}</b><br>Lotes: %{value:,}<br>%{percent}<extra></extra>",
                ))
                _fig1.update_layout(height=300, margin=dict(t=10,b=10,l=10,r=10), showlegend=False)
                st.plotly_chart(_fig1, use_container_width=True)
                _ad = _anc.copy()
                _ad["ANCHOS_UNICOS"] = _ad["ANCHOS_UNICOS"].astype(int)
                _ad["% Lotes"] = (_ad["Lotes"]/_ad["Lotes"].sum()*100).round(1)
                _ad["LBS"] = _ad["LBS"].apply(fmt)
                st.dataframe(_ad.rename(columns={"ANCHOS_UNICOS":"N° Anchos"})[["N° Anchos","Lotes","% Lotes","LBS"]],
                             use_container_width=True, hide_index=True, height=180)

        with d_right:
            st.markdown("**Por cantidad de LNKs por lote**")
            if _df_f.empty or "SKU_DISTINTOS" not in _df_f.columns:
                st.info("Sin datos.")
            else:
                def _bin(n):
                    n=int(n)
                    return f"{n} LNK{'s' if n>1 else ''}" if n<=4 else "5+ LNKs"
                _df_f2 = _df_f.copy()
                _df_f2["_BIN"] = _df_f2["SKU_DISTINTOS"].apply(_bin)
                _lnk = _df_f2.groupby("_BIN").agg(Lotes=("LOTE_ID","nunique"),LBS=("LBS_TOTAL","sum")).reset_index()
                _ord = ["1 LNK","2 LNKs","3 LNKs","4 LNKs","5+ LNKs"]
                _lnk["_o"] = _lnk["_BIN"].apply(lambda x: _ord.index(x) if x in _ord else 99)
                _lnk = _lnk.sort_values("_o").drop(columns=["_o"])
                _colors2 = ["#B5D4F4","#378ADD","#185FA5","#0C447C","#042C53"]
                _fig2 = go.Figure(go.Pie(
                    labels=_lnk["_BIN"], values=_lnk["Lotes"], hole=0.55,
                    marker_colors=_colors2[:len(_lnk)], textinfo="label+percent",
                    hovertemplate="<b>%{label}</b><br>Lotes: %{value:,}<br>%{percent}<extra></extra>",
                ))
                _fig2.update_layout(height=300, margin=dict(t=10,b=10,l=10,r=10), showlegend=False)
                st.plotly_chart(_fig2, use_container_width=True)
                _ld = _lnk.copy()
                _ld["% Lotes"] = (_ld["Lotes"]/_ld["Lotes"].sum()*100).round(1)
                _ld["LBS"] = _ld["LBS"].apply(fmt)
                st.dataframe(_ld.rename(columns={"_BIN":"LNKs por lote"})[["LNKs por lote","Lotes","% Lotes","LBS"]],
                             use_container_width=True, hide_index=True, height=180)

    st.caption("💡 Las tablas resumen detalladas están en la pestaña **📄 Resumen**.")

with tab_d:
    if df_det.empty: st.info("Sin lotes.")
    else:
        d1,d2,d3=st.columns(3)
        fm=d1.multiselect("MIX",    sorted(df_det["MIX"].unique()),key="dm")
        fr=d2.multiselect("Regla",  sorted(df_det["APLICA_REGLA"].unique()),key="dr")
        fb=d3.multiselect("Bloque", sorted(df_det["BLOQUE"].unique()),key="db")
        filt=df_det.copy()
        if fm: filt=filt[filt["MIX"].isin(fm)]
        if fr: filt=filt[filt["APLICA_REGLA"].isin(fr)]
        if fb: filt=filt[filt["BLOQUE"].isin(fb)]
        MAX_ROWS=2000
        if len(filt)>MAX_ROWS:
            st.caption(f"⚠️ Mostrando primeras {MAX_ROWS:,} de {len(filt):,} filas. Usa filtros para reducir.")
            filt=filt.head(MAX_ROWS)
        st.dataframe(filt,use_container_width=True,height=420)
        st.caption(f"{len(filt):,} filas visibles")

with tab_r:
    if df_res.empty: st.info("Sin resumen.")
    else:
        st.dataframe(df_res,use_container_width=True,height=350)
        st.divider()
        st.markdown("#### 📋 Tablas Resumen")
        ta,tb,tc,td,te=st.tabs(["Por Prioridad","Por N° Anchos","Anchos × Categoría","Por Categoría","Resumen Dinámico"])

        with ta:
            prio_df2=reports.get("PRIORIDAD_VS_ASIG",pd.DataFrame())
            if prio_df2.empty: st.info("Sin datos.")
            else:
                t1=prio_df2.copy()
                if not df_res.empty and "BLOQUE_DOMINANTE" in df_res.columns:
                    lb2=(df_res.groupby(["BLOQUE_DOMINANTE","MIX"]).agg(LOTES=("LOTE_ID","nunique")).reset_index())
                    lb2.columns=["BLOQUE","MIX","LOTES"]
                    t1=t1.merge(lb2,on=["BLOQUE","MIX"],how="left").fillna({"LOTES":0})
                else:
                    t1["LOTES"]=0
                t1["LOTES"]=t1["LOTES"].astype(int)
                t1["% ASIGNADO"]=(t1["LBS_ASIGNADAS"]/t1["LBS_BASE"].replace(0,pd.NA)*100).fillna(0).round(1)
                t1=t1.rename(columns={"BLOQUE":"Prioridad","LBS_BASE":"LBS Planeadas","LBS_ASIGNADAS":"LBS Asignadas","LBS_SIN_ASIGNAR":"LBS Sin Asignar"})
                cols=[c for c in ["Prioridad","MIX","LBS Planeadas","LBS Asignadas","LBS Sin Asignar","LOTES","% ASIGNADO"] if c in t1.columns]
                st.dataframe(t1[cols],use_container_width=True,hide_index=True)

        with tb:
            if df_res.empty: st.info("Sin datos.")
            else:
                t2=(df_res.groupby("ANCHOS_UNICOS").agg(LBS=("LBS_TOTAL","sum"),Lotes=("LOTE_ID","nunique")).reset_index().sort_values("ANCHOS_UNICOS"))
                t2.columns=["N° Anchos","LBS Asignadas","Lotes"]
                tot=t2["LBS Asignadas"].sum()
                t2["% del Total"]=(t2["LBS Asignadas"]/tot*100).round(1) if tot>0 else 0
                st.dataframe(t2,use_container_width=True,hide_index=True)

        with tc:
            if df_res.empty: st.info("Sin datos.")
            else:
                t3=(df_res.groupby(["ANCHOS_UNICOS","CATEGORIA"]).agg(LBS=("LBS_TOTAL","sum"),Lotes=("LOTE_ID","nunique"),MinLBS=("LBS_TOTAL","min"),MaxLBS=("LBS_TOTAL","max")).reset_index().sort_values(["ANCHOS_UNICOS","CATEGORIA"]))
                t3.columns=["N° Anchos","Categoría","LBS Asignadas","Lotes","Min LBS Lote","Max LBS Lote"]
                st.dataframe(t3,use_container_width=True,hide_index=True)

        with td:
            cap_df2=reports.get("CAPACIDAD_X_CATEG",pd.DataFrame())
            if cap_df2.empty: st.info("Sin datos.")
            else:
                t4=cap_df2.copy()
                if not df_res.empty:
                    lc=df_res.groupby("CATEGORIA").agg(LOTES=("LOTE_ID","nunique")).reset_index()
                    t4=t4.merge(lc,on="CATEGORIA",how="left").fillna({"LOTES":0})
                    t4["LOTES"]=t4["LOTES"].astype(int)
                disp={"CATEGORIA":"Categoría","MINIMO":"Mín LBS","MAXIMO":"Máx LBS","MIX":"MIX","LBS_ASIGNADAS":"LBS Asignadas","LOTES":"Lotes","CAPACIDAD":"Capacidad","PCT_OCUPACION":"% Ocupación"}
                t4=t4.rename(columns=disp)
                st.dataframe(t4[[v for v in disp.values() if v in t4.columns]],use_container_width=True,hide_index=True)

        with te:
            st.markdown("**Resumen dinámico** — agrupa por los campos que elijas.")
            if df_det.empty: st.info("Sin datos.")
            else:
                RES_OPTS=[c for c in ["TELA.CUERPO","STYLE","COLOR","COLOR_R","FAMILIA","TONO","MIX","BLOQUE","APLICA_REGLA","CATEGORIA","PRIORIDAD","ANCHOS_LOTE","LNK"] if c in df_det.columns]
                METRIC_OPTS={k:v for k,v in {"LBS Asignadas":"LBS_ASIGNADAS","Docenas":"DOCENAS"}.items() if v in df_det.columns}
                re1,re2,re3=st.columns([3,2,1])
                rg_sel=re1.multiselect("Agrupar por",RES_OPTS,default=["MIX","BLOQUE"] if "BLOQUE" in df_det.columns else RES_OPTS[:1],key="rg_sel")
                rm_sel=re2.selectbox("Métrica",list(METRIC_OPTS.keys()),key="rm_sel")
                rn_top=re3.number_input("Top N",5,500,50,key="rn_top")
                if rg_sel and rm_sel:
                    mc=METRIC_OPTS[rm_sel]
                    try:
                        rt=df_det.groupby(rg_sel,as_index=False)[mc].sum()
                        rt=rt.sort_values(mc,ascending=False).head(rn_top)
                        total_m=df_det[mc].sum()
                        rt["% del Total"]=(rt[mc]/total_m*100).round(1) if total_m>0 else 0
                        rt=rt.rename(columns={mc:rm_sel})
                        st.dataframe(rt,use_container_width=True,hide_index=True,height=min(60+35*len(rt),380))
                        st.caption(f"Total {rm_sel}: {total_m:,.1f} · {len(rt)} grupos")
                    except Exception as e:
                        st.error(f"Error: {e}")


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
            _t=r.get("tiempo_seg",0); _rm,_rs=divmod(int(_t),60)
            rows.append({
                "#":i+1, "Hora":r["label"],
                "Tiempo":f"{_rm:02d}:{_rs:02d}",
                "Calidad":r.get("quality_used","?"),
                "Comentario":r.get("comentario","—"),
                "Lotes":len(s),
                "LBS Asignadas":fmt(d["LBS_ASIGNADAS"].sum() if not d.empty else 0),
                "LBS Excedentes":fmt(exc["LBS_RESTANTES"].sum() if not exc.empty else 0),
                "Cap. Perdida":fmt(s["CAPACIDAD_PERDIDA"].sum() if not s.empty else 0),
                "LNKs %":f"{(lc['ESTADO'].isin(['COMPLETO','COMPLETO (SCRAP)']).sum()/len(lc)*100) if not lc.empty else 0:.1f}%",
                "Cancelada":"Sí" if r.get("cancelled") else "No",
                        "Modo": r.get("modo","Libre"),
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

with tab_t:
    df_tej = res.get("detalle_tejido", pd.DataFrame())
    df_stk = res.get("stock_tejido",   pd.DataFrame())

    if res.get("modo","Libre") == "Libre":
        st.info("ℹ️ Este resultado se generó en **Modo Libre**. "
                "Activa el **Modo Restricción de Tejido** en el sidebar y sube el "
                "ANALISIS_INV para ver el plan de disponibilidad de tejido.")
    elif df_tej.empty:
        st.info("Sin movimientos de tejido registrados.")
    else:
        # ── Métricas rápidas ──────────────────────────────────────────────
        modo_lote = res.get("modo","Libre")
        st.markdown(f"**Modo:** {modo_lote}")

        lbs_inv  = df_tej[df_tej["FUENTE"]=="INV MANO"]["LBS_ASIGNADAS"].sum() if not df_tej.empty else 0
        lbs_dias = df_tej[df_tej["FUENTE"]!="INV MANO"]["LBS_ASIGNADAS"].sum() if not df_tej.empty else 0
        dia_max_global = int(df_tej["DIA_MAX_LOTE"].max()) if "DIA_MAX_LOTE" in df_tej.columns and not df_tej.empty else 0
        label_dia = "INV MANO (hoy)" if dia_max_global == 0 else f"DIA {dia_max_global}"

        mc1,mc2,mc3 = st.columns(3)
        mc1.metric("LBS de Inventario en Mano", f"{lbs_inv:,.0f}")
        mc2.metric("LBS de Días Futuros",        f"{lbs_dias:,.0f}")
        mc3.metric("Día más tardío del plan",     label_dia)

        # ── Sub-tabs ──────────────────────────────────────────────────────
        st1, st2 = st.tabs(["📦 Detalle por lote/componente", "📊 Stock de tejido"])

        with st1:
            st.caption("Una fila por cada componente de tejido asignado a un lote.")

            # ── Resumen de lotes por día ──────────────────────────────────
            if "DIA_LOTE" in df_tej.columns:
                st.markdown("**📅 Resumen de lotes por día de disponibilidad**")
                _dia_ord = {"INV MANO": 0}
                for _i in range(1, 11): _dia_ord[f"DIA {_i}"] = _i

                _resumen_dia = (
                    df_tej.groupby("DIA_LOTE")["LOTE_ID"]
                    .nunique()
                    .reset_index()
                    .rename(columns={"LOTE_ID": "Lotes", "DIA_LOTE": "Día"})
                )
                _lbs_dia = (
                    df_tej.groupby("DIA_LOTE")["LBS_ASIGNADAS"]
                    .sum()
                    .reset_index()
                    .rename(columns={"LBS_ASIGNADAS": "LBS Tejido", "DIA_LOTE": "Día"})
                )
                _resumen_dia = _resumen_dia.merge(_lbs_dia, on="Día")
                _resumen_dia["_ord"] = _resumen_dia["Día"].map(lambda x: _dia_ord.get(x, 99))
                _resumen_dia = _resumen_dia.sort_values("_ord").drop(columns=["_ord"]).reset_index(drop=True)
                _resumen_dia["LBS Tejido"] = _resumen_dia["LBS Tejido"].apply(lambda x: f"{x:,.0f}")
                _total_lotes = df_tej["LOTE_ID"].nunique()
                _resumen_dia.loc[len(_resumen_dia)] = ["TOTAL", _total_lotes, f"{df_tej['LBS_ASIGNADAS'].sum():,.0f}"]
                st.dataframe(_resumen_dia, use_container_width=True, hide_index=True, height=min(60+35*len(_resumen_dia), 320))
                st.divider()

            # ── Filtros ───────────────────────────────────────────────────
            fc1,fc2,fc3,fc4 = st.columns(4)
            _lotes_f  = fc1.multiselect("LOTE_ID",  sorted(df_tej["LOTE_ID"].unique()),  key="tf_lote")
            _estilos_f= fc2.multiselect("ESTILO C", sorted(df_tej["ESTILO C"].unique()), key="tf_estilo")
            _fuentes_f= fc3.multiselect("FUENTE",   sorted(df_tej["FUENTE"].unique()),   key="tf_fuente")
            _dias_f   = fc4.multiselect("DÍA LOTE",
                sorted(df_tej["DIA_LOTE"].unique(),
                       key=lambda x: _dia_ord.get(x, 99)) if "DIA_LOTE" in df_tej.columns else [],
                key="tf_dia")

            df_tej_f = df_tej.copy()
            if _lotes_f:   df_tej_f = df_tej_f[df_tej_f["LOTE_ID"].isin(_lotes_f)]
            if _estilos_f: df_tej_f = df_tej_f[df_tej_f["ESTILO C"].isin(_estilos_f)]
            if _fuentes_f: df_tej_f = df_tej_f[df_tej_f["FUENTE"].isin(_fuentes_f)]
            if _dias_f and "DIA_LOTE" in df_tej_f.columns:
                df_tej_f = df_tej_f[df_tej_f["DIA_LOTE"].isin(_dias_f)]

            # Columna LBS_TOTAL_LOTE: suma de todas las LBS asignadas del lote
            lbs_por_lote = df_tej_f.groupby("LOTE_ID")["LBS_ASIGNADAS"].sum().rename("LBS_TOTAL_LOTE")
            df_tej_f = df_tej_f.merge(lbs_por_lote, on="LOTE_ID", how="left")
            # Moverla justo después de LBS_ASIGNADAS
            cols = list(df_tej_f.columns)
            if "LBS_TOTAL_LOTE" in cols and "LBS_ASIGNADAS" in cols:
                cols.remove("LBS_TOTAL_LOTE")
                cols.insert(cols.index("LBS_ASIGNADAS") + 1, "LBS_TOTAL_LOTE")
                df_tej_f = df_tej_f[cols]

            st.caption(f"{len(df_tej_f):,} filas · {df_tej_f['LOTE_ID'].nunique():,} lotes")
            st.dataframe(df_tej_f, use_container_width=True, height=420)

        with st2:
            st.caption(
                "Inventario inicial vs. asignado vs. remanente por (ESTILO C, DG, LOTE FACE). "
                "Columnas INI/ASIG/REM para cada fuente."
            )
            # Mostrar solo columnas resumen por defecto; usuario puede ver todo
            cols_res = ["ESTILO C","DG","LOTE FACE","TOTAL_INICIAL","TOTAL_ASIGNADO","TOTAL_REMANENTE"]
            cols_show = [c for c in cols_res if c in df_stk.columns]
            expand = st.toggle("Ver todas las columnas (por fuente)", value=False, key="stk_expand")
            df_show = df_stk if expand else df_stk[cols_show]
            # Highlight filas con asignación > 0
            st.dataframe(
                df_show.style.apply(
                    lambda r: ["background-color: #eff6ff" if r.get("TOTAL_ASIGNADO",0)>0 else "" for _ in r],
                    axis=1
                ) if "TOTAL_ASIGNADO" in df_show.columns else df_show,
                use_container_width=True,
                height=500,
            )

st.divider()
ts=res["ts"].replace(":","").replace(" ","_").replace("-","")
st.download_button("⬇  Descargar Excel completo",data=export_excel(res),
                   file_name=f"RESULTADOS_LOTES_{ts}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   use_container_width=True)

with st.expander("📋  Sección prueba", expanded=True):
    st.markdown('<div class="info-note">✏️ Edita la tabla y presiona <b>Aplicar cambios de capacidad</b>. '
                'Los parámetros por fila sobreescriben los globales para ese tamaño de lote.</div>',
                unsafe_allow_html=True)
