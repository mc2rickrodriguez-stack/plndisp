"""
Motor de disponibilidad de tejido v2
=====================================
Agrega soporte de DG alternos (DT → múltiples DG reales equivalentes).

Reglas de asignación con DG alterno
-------------------------------------
1. Jerarquía: primero DG exacto (DT == DG). Si no alcanza → alterno completo.
   No se mezcla titular con alterno en el mismo componente ni en el mismo lote.
2. Selección de alterno: el DG real que comparte el mismo (ESTILO C, DT).
   Se elige el que deje MENOR REMANENTE después de cubrir la necesidad del lote.
3. Consistencia dentro del lote: todos los LNKs que usen el mismo DG_TITULAR
   deben usar el mismo DG_USADO. Un lote no puede tener dos DG distintos
   para el mismo titular.
4. LOTE FACE consistente en todos los componentes de todos los LNKs del lote.
5. Un solo DG por componente — no se completa con dos DGs distintos.
6. La restricción aplica por lote — cada lote elige independientemente.

Nuevos campos en el log de consumo
------------------------------------
  DG_TITULAR  — DG que pidió el plan (= DG.* de DATA)
  DG_USADO    — DG real del stock que se asignó
  ES_ALTERNO  — True si DG_USADO ≠ DG_TITULAR
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ── Constantes ────────────────────────────────────────────────────────────────
DIA_COLS    = [f"DIA {i}" for i in range(1, 11)]
FUENTE_INV  = "INV MANO"
FUENTES_ORD = [FUENTE_INV] + DIA_COLS

# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return str(x).strip()

def _flt(x, default=0.0) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except Exception:
        return default

def _day_index(fuente: str) -> int:
    if fuente == FUENTE_INV:
        return -1
    try:
        return int(fuente.split()[-1]) - 1
    except Exception:
        return 999

# ── Dataclasses de resultado ──────────────────────────────────────────────────

@dataclass
class ComponenteFuente:
    componente:    str
    estilo:        str
    dg_titular:    float   # DG que pidió el plan
    dg_usado:      float   # DG real del stock (puede ser alterno)
    es_alterno:    bool
    lote_face:     str
    lbs_necesarias: float
    fuentes:       List[Tuple[str, float]]  # [(fuente, lbs), ...]

    @property
    def dia_maximo(self) -> int:
        max_d = -1
        for fuente, lbs in self.fuentes:
            if lbs > 1e-9:
                d = _day_index(fuente)
                if d > max_d:
                    max_d = d
        return max_d

@dataclass
class LnkDisponibilidad:
    lnk:        str
    lote_face:  str
    componentes: List[ComponenteFuente]
    viable:     bool = True

    @property
    def dia_maximo_lote(self) -> int:
        return max((c.dia_maximo for c in self.componentes), default=-1)

    @property
    def fuente_resumen(self) -> str:
        fuentes_usadas: set = set()
        for comp in self.componentes:
            for fuente, lbs in comp.fuentes:
                if lbs > 1e-9:
                    fuentes_usadas.add(fuente)
        if not fuentes_usadas:
            return "—"
        return " + ".join(sorted(fuentes_usadas, key=_day_index))

# ── Índice principal ──────────────────────────────────────────────────────────

class DisponibilidadIndex:
    """
    stock[(estilo, dg, lote_face)] = {
        "INV MANO": float, "DIA 1": float, ...,
        "_used": {"INV MANO": 0.0, ...}
    }

    _alternos[(estilo, dt)] = [dg1, dg2, ...]   ← DGs reales equivalentes al titular DT
    """

    def __init__(self, df_raw: pd.DataFrame):
        self.stock:     Dict[tuple, dict]        = {}
        self._alternos: Dict[tuple, List[float]] = {}
        self._consumos: List[dict]               = []
        self._build(df_raw)

    # ── Construcción ──────────────────────────────────────────────────────────

    def _build(self, df: pd.DataFrame) -> None:
        df = df.copy()
        df["ESTILO C"]  = df["ESTILO C"].apply(_norm)
        df["LOTE FACE"] = df["LOTE FACE"].apply(_norm)
        df["DG"]        = df["DG"].apply(_flt)

        # DT puede no existir en archivos viejos
        if "DT" in df.columns:
            df["DT"] = df["DT"].apply(_flt)
        else:
            df["DT"] = df["DG"]   # sin DT, asumimos DT == DG

        for d in DIA_COLS:
            if d not in df.columns:
                df[d] = 0.0
            df[d] = df[d].apply(_flt)
        df["INV EN MANO"] = df["INV EN MANO"].apply(_flt)

        # Construir mapa de alternos: (estilo, dt) → [dg1, dg2, ...]
        # Filtramos filas con ESTILO C vacío (totales del Excel)
        df_valid = df[df["ESTILO C"] != ""]
        alt_map: Dict[tuple, set] = {}
        for _, row in df_valid.iterrows():
            est = _norm(row["ESTILO C"])
            dt  = _flt(row["DT"])
            dg  = _flt(row["DG"])
            if est and dt > 0 and dg > 0:
                k = (est, dt)
                alt_map.setdefault(k, set()).add(dg)
        self._alternos = {k: sorted(v) for k, v in alt_map.items()}

        # Construir stock agrupando por (ESTILO C, DG, LOTE FACE)
        agg = {c: "sum" for c in ["INV EN MANO"] + DIA_COLS}
        grouped = df_valid.groupby(["ESTILO C", "DG", "LOTE FACE"], as_index=False).agg(agg)

        for _, row in grouped.iterrows():
            key = (_norm(row["ESTILO C"]), float(row["DG"]), _norm(row["LOTE FACE"]))
            entry = {FUENTE_INV: float(row["INV EN MANO"]), "_used": {FUENTE_INV: 0.0}}
            for d in DIA_COLS:
                entry[d]          = float(row[d])
                entry["_used"][d] = 0.0
            self.stock[key] = entry

    # ── Disponible neto ───────────────────────────────────────────────────────

    def disponible(self, estilo: str, dg: float, lote_face: str, fuente: str,
                   tent: Optional[Dict] = None) -> float:
        key = (estilo, dg, lote_face)
        if key not in self.stock:
            return 0.0
        e = self.stock[key]
        base = max(0.0, e.get(fuente, 0.0) - e["_used"].get(fuente, 0.0))
        reserva = (tent or {}).get((estilo, dg, lote_face, fuente), 0.0)
        return max(0.0, base - reserva)

    def disponible_total(self, estilo: str, dg: float, lote_face: str,
                         tent: Optional[Dict] = None) -> float:
        return sum(self.disponible(estilo, dg, lote_face, f, tent) for f in FUENTES_ORD)

    # ── DG alternos para un titular ───────────────────────────────────────────

    def dgs_para_titular(self, estilo: str, dt: float) -> List[float]:
        """Retorna todos los DGs reales (incluido el exacto si existe) para un DT."""
        return list(self._alternos.get((estilo, dt), []))

    # ── Check de un componente con soporte de alterno ─────────────────────────

    def _check_componente_con_dg(
        self,
        comp_name:     str,
        estilo:        str,
        dg_titular:    float,
        dg_a_usar:     float,   # DG real (titular o alterno)
        lote_face:     str,
        lbs_necesarias: float,
        tent:          Optional[Dict] = None,
    ) -> Optional[ComponenteFuente]:
        """
        Verifica si el DG específico (titular o alterno) puede cubrir
        las LBS necesarias. Retorna ComponenteFuente o None.
        """
        key = (estilo, dg_a_usar, lote_face)
        if key not in self.stock:
            return None
        if lbs_necesarias <= 1e-9:
            return ComponenteFuente(
                comp_name, estilo, dg_titular, dg_a_usar,
                dg_a_usar != dg_titular, lote_face, 0.0, []
            )

        restante = lbs_necesarias
        fuentes_usadas: List[Tuple[str, float]] = []

        for fuente in FUENTES_ORD:
            disp = self.disponible(estilo, dg_a_usar, lote_face, fuente, tent)
            if disp <= 1e-9:
                continue
            tomar = min(restante, disp)
            fuentes_usadas.append((fuente, tomar))
            restante -= tomar
            if restante <= 1e-9:
                break

        if restante > 1e-9:
            return None   # no hay suficiente con este DG

        return ComponenteFuente(
            comp_name, estilo, dg_titular, dg_a_usar,
            abs(dg_a_usar - dg_titular) > 1e-9,
            lote_face, lbs_necesarias, fuentes_usadas
        )

    def _elegir_dg_para_componente(
        self,
        comp_name:     str,
        estilo:        str,
        dg_titular:    float,
        lote_face:     str,
        lbs_necesarias: float,
        tent:          Optional[Dict] = None,
        dg_forzado:    Optional[float] = None,   # si el lote ya fijó un DG para este titular
    ) -> Optional[ComponenteFuente]:
        """
        Elige el mejor DG para cubrir un componente:
        1. Si dg_forzado está definido, solo intenta ese DG.
        2. Primero intenta DG exacto (titular).
        3. Si no alcanza, prueba cada alterno y elige el de menor remanente.
        Retorna ComponenteFuente o None si ninguno alcanza.
        """
        if dg_forzado is not None:
            # El lote ya decidió qué DG usar para este titular
            return self._check_componente_con_dg(
                comp_name, estilo, dg_titular, dg_forzado,
                lote_face, lbs_necesarias, tent
            )

        # Obtener todos los DGs candidatos para este titular
        candidatos = self.dgs_para_titular(estilo, dg_titular)
        if not candidatos:
            # Sin info de DT en ANALISIS_INV — intentar exacto
            candidatos = [dg_titular]

        # Separar exacto de alternos
        exacto   = dg_titular if dg_titular in candidatos else None
        alternos = [dg for dg in candidatos if abs(dg - dg_titular) > 1e-9]

        # 1. Intentar DG exacto
        if exacto is not None:
            comp = self._check_componente_con_dg(
                comp_name, estilo, dg_titular, exacto,
                lote_face, lbs_necesarias, tent
            )
            if comp is not None:
                return comp

        # 2. Intentar alternos — elegir el de menor remanente
        mejor_comp:     Optional[ComponenteFuente] = None
        mejor_remanente = float("inf")

        for dg_alt in alternos:
            comp = self._check_componente_con_dg(
                comp_name, estilo, dg_titular, dg_alt,
                lote_face, lbs_necesarias, tent
            )
            if comp is None:
                continue
            remanente = self.disponible_total(estilo, dg_alt, lote_face, tent) - lbs_necesarias
            if remanente < mejor_remanente:
                mejor_remanente = remanente
                mejor_comp      = comp

        return mejor_comp

    # ── Check de un LNK con soporte de alterno ────────────────────────────────

    def check_lnk(
        self,
        lnk_row:    pd.Series,
        lote_face:  str,
        tent:       Optional[Dict] = None,
        dg_map:     Optional[Dict[Tuple[str, float], float]] = None,
    ) -> Optional[LnkDisponibilidad]:
        """
        Verifica si un LNK puede cubrirse con el LOTE FACE dado.

        dg_map: {(estilo, dg_titular): dg_usado} — DGs ya fijados por el lote
                para este LOTE FACE. Garantiza consistencia entre LNKs.
        """
        COMPONENTES = [
            ("CUERPO", "TELA.CUERPO", "DG.CUERPO", "LB.CUERPO"),
            ("MANGAS", "TELA.MANGAS", "DG.MANGAS", "LB.MANGAS"),
            ("RIB",    "TELA.RIB",    "DG.RIB",    "LB.RIB"),
            ("POCKET", "TELA.POCKET", "DG.POCKET",  "LB.POCKET"),
        ]

        componentes_ok: List[ComponenteFuente] = []
        dg_map = dict(dg_map) if dg_map else {}

        for comp_name, tcol, dgcol, lbscol in COMPONENTES:
            tela = _norm(lnk_row.get(tcol, ""))
            if not tela or tela == "-":
                continue
            try:
                dg_tit = float(lnk_row.get(dgcol, 0))
                lbs    = float(lnk_row.get(lbscol, 0))
            except Exception:
                dg_tit = lbs = 0.0
            if dg_tit == 0.0 or lbs <= 0:
                continue

            # DG ya fijado por este lote para este titular?
            dg_forzado = dg_map.get((tela, dg_tit))

            comp = self._elegir_dg_para_componente(
                comp_name, tela, dg_tit, lote_face, lbs, tent, dg_forzado
            )
            if comp is None:
                return None   # componente sin disponibilidad

            # Fijar DG para este titular en el lote
            dg_map[(tela, dg_tit)] = comp.dg_usado
            componentes_ok.append(comp)

        if not componentes_ok:
            return None

        return LnkDisponibilidad(
            lnk=_norm(lnk_row.get("LNK", "")),
            lote_face=lote_face,
            componentes=componentes_ok,
        )

    # ── Selección de LOTE FACE para un LNK individual ────────────────────────

    def elegir_lote_face(
        self,
        lnk_row: pd.Series,
        bloque:  str,
        tent:    Optional[Dict] = None,
    ) -> Optional[LnkDisponibilidad]:
        """Evalúa todos los LOTE FACE candidatos y elige el de menor día máximo."""
        tela_cuerpo = _norm(lnk_row.get("TELA.CUERPO", ""))
        try:
            dg_tit_cuerpo = float(lnk_row.get("DG.CUERPO", 0))
        except Exception:
            dg_tit_cuerpo = 0.0

        if not tela_cuerpo or tela_cuerpo == "-" or dg_tit_cuerpo == 0.0:
            return None

        # Candidatos: todos los LOTE FACE que tienen stock para este estilo
        # (cualquier DG del mismo titular incluido)
        dgs_candidatos = self.dgs_para_titular(tela_cuerpo, dg_tit_cuerpo) or [dg_tit_cuerpo]
        candidatos_lf = list({
            lf
            for (est, dg, lf) in self.stock.keys()
            if est == tela_cuerpo and any(abs(dg - d) < 1e-9 for d in dgs_candidatos)
        })

        if not candidatos_lf:
            return None

        mejor: Optional[LnkDisponibilidad] = None
        mejor_dia = 9999
        mejor_inv = -1.0

        for lf in candidatos_lf:
            res = self.check_lnk(lnk_row, lf, tent)
            if res is None:
                continue
            dia = res.dia_maximo_lote
            inv = max(
                self.disponible(tela_cuerpo, dg, lf, FUENTE_INV, tent)
                for dg in dgs_candidatos
                if (tela_cuerpo, dg, lf) in self.stock
            ) if dgs_candidatos else 0.0

            if dia < mejor_dia or (dia == mejor_dia and inv > mejor_inv):
                mejor     = res
                mejor_dia = dia
                mejor_inv = inv

        return mejor

    def lnk_tiene_disponibilidad(self, lnk_row: pd.Series) -> bool:
        return self.elegir_lote_face(lnk_row, bloque="OTROS") is not None

    # ── Selección de LOTE FACE para un lote completo ─────────────────────────

    def elegir_lote_face_lote(
        self,
        lnk_rows: List[pd.Series],
        bloque:   str,
        tent:     Optional[Dict] = None,
    ) -> Optional[Tuple[str, Dict[str, LnkDisponibilidad]]]:
        """
        Elige el LOTE FACE que minimiza el día máximo global del lote.
        Garantiza que todos los LNKs usen el mismo DG para cada DG titular.
        """
        if not lnk_rows:
            return None

        tela_ref = _norm(lnk_rows[0].get("TELA.CUERPO", ""))
        try:
            dg_tit_ref = float(lnk_rows[0].get("DG.CUERPO", 0))
        except Exception:
            dg_tit_ref = 0.0

        dgs_ref = self.dgs_para_titular(tela_ref, dg_tit_ref) or [dg_tit_ref]
        candidatos_lf = list({
            lf
            for (est, dg, lf) in self.stock.keys()
            if est == tela_ref and any(abs(dg - d) < 1e-9 for d in dgs_ref)
        })

        if not candidatos_lf:
            return None

        mejor_lf  = None
        mejor_dia = 9999
        mejor_inv = -1.0
        mejor_map: Dict[str, LnkDisponibilidad] = {}

        for lf in candidatos_lf:
            tent_lf: Dict = dict(tent) if tent else {}
            # dg_map garantiza consistencia: mismo DG titular → mismo DG usado en todo el lote
            dg_map_lf: Dict[Tuple[str, float], float] = {}
            plan_lf:   Dict[str, LnkDisponibilidad]   = {}
            viable = True

            for lnk_row in lnk_rows:
                res = self.check_lnk(lnk_row, lf, tent_lf, dg_map_lf)
                if res is None:
                    viable = False
                    break
                lnk_id = _norm(lnk_row.get("LNK", ""))
                plan_lf[lnk_id] = res
                # Acumular reservas tentativas y fijar DG elegidos
                for comp in res.componentes:
                    for fuente, lbs in comp.fuentes:
                        k = (comp.estilo, comp.dg_usado, lf, fuente)
                        tent_lf[k] = tent_lf.get(k, 0.0) + lbs
                    # Propagar dg_map para siguientes LNKs
                    dg_map_lf[(comp.estilo, comp.dg_titular)] = comp.dg_usado

            if not viable:
                continue

            dia_max = max(r.dia_maximo_lote for r in plan_lf.values()) if plan_lf else 9999
            inv_ref = max(
                (self.disponible(tela_ref, dg, lf, FUENTE_INV, tent_lf)
                 for dg in dgs_ref if (tela_ref, dg, lf) in self.stock),
                default=0.0
            )

            if dia_max < mejor_dia or (dia_max == mejor_dia and inv_ref > mejor_inv):
                mejor_lf  = lf
                mejor_dia = dia_max
                mejor_inv = inv_ref
                mejor_map = plan_lf

        if mejor_lf is None:
            return None
        return mejor_lf, mejor_map

    # ── Confirmar consumo ─────────────────────────────────────────────────────

    def consume(self, lnk_disp: LnkDisponibilidad, lote_id: str) -> None:
        for comp in lnk_disp.componentes:
            for fuente, lbs in comp.fuentes:
                if lbs <= 1e-9:
                    continue
                key = (comp.estilo, comp.dg_usado, lnk_disp.lote_face)
                if key in self.stock:
                    self.stock[key]["_used"][fuente] = (
                        self.stock[key]["_used"].get(fuente, 0.0) + lbs
                    )
                self._consumos.append({
                    "LOTE_ID":      lote_id,
                    "LNK":          lnk_disp.lnk,
                    "COMPONENTE":   comp.componente,
                    "ESTILO C":     comp.estilo,
                    "DG_TITULAR":   comp.dg_titular,
                    "DG_USADO":     comp.dg_usado,
                    "ES_ALTERNO":   "Sí" if comp.es_alterno else "No",
                    "LOTE FACE":    lnk_disp.lote_face,
                    "FUENTE":       fuente,
                    "LBS_ASIGNADAS": lbs,
                })

    # ── Reportes ──────────────────────────────────────────────────────────────

    def build_reports(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        df_det = pd.DataFrame(self._consumos) if self._consumos else pd.DataFrame(columns=[
            "LOTE_ID","LNK","COMPONENTE","ESTILO C","DG_TITULAR","DG_USADO",
            "ES_ALTERNO","LOTE FACE","FUENTE","LBS_ASIGNADAS"
        ])

        if len(df_det) > 0:
            def _fuente_a_dia(f):
                return 0 if f == FUENTE_INV else _day_index(f) + 1
            df_det["DIA_NUM"] = df_det["FUENTE"].apply(_fuente_a_dia)
            lote_dia_max = df_det.groupby("LOTE_ID")["DIA_NUM"].max().rename("DIA_MAX_LOTE")
            df_det = df_det.merge(lote_dia_max, on="LOTE_ID", how="left")
            df_det["DIA_LOTE"] = df_det["DIA_MAX_LOTE"].apply(
                lambda d: "INV MANO" if d == 0 else f"DIA {int(d)}"
            )
            df_det = df_det.drop(columns=["DIA_NUM","DIA_MAX_LOTE"])

        rows_stock = []
        for (estilo, dg, lf), entry in self.stock.items():
            if not estilo:
                continue
            row_base = {"ESTILO C": estilo, "DG": dg, "LOTE FACE": lf}
            total_ini = 0.0; total_used = 0.0
            for fuente in FUENTES_ORD:
                ini  = entry.get(fuente, 0.0)
                used = entry["_used"].get(fuente, 0.0)
                row_base[f"INI_{fuente}"]  = ini
                row_base[f"ASIG_{fuente}"] = used
                row_base[f"REM_{fuente}"]  = max(0.0, ini - used)
                total_ini  += ini
                total_used += used
            row_base["TOTAL_INICIAL"]   = total_ini
            row_base["TOTAL_ASIGNADO"]  = total_used
            row_base["TOTAL_REMANENTE"] = max(0.0, total_ini - total_used)
            rows_stock.append(row_base)

        df_stock = pd.DataFrame(rows_stock).sort_values(
            ["ESTILO C","DG","LOTE FACE"]
        ).reset_index(drop=True)

        return df_det, df_stock

    def build_tejido_ocioso(self, df_plan: pd.DataFrame) -> pd.DataFrame:
        """Tejido en ANALISIS_INV sin demanda en el plan mensual."""
        demanda: set = set()
        for _, row in df_plan.iterrows():
            for tcol, dgcol in [
                ("TELA.CUERPO","DG.CUERPO"),("TELA.MANGAS","DG.MANGAS"),
                ("TELA.RIB","DG.RIB"),("TELA.POCKET","DG.POCKET"),
            ]:
                tela = _norm(row.get(tcol, ""))
                try:
                    dg = float(row.get(dgcol, 0))
                except Exception:
                    dg = 0.0
                if tela and tela != "-" and dg > 0:
                    # Marcar el titular y todos sus alternos como "con demanda"
                    demanda.add((tela, dg))
                    for dg_alt in self.dgs_para_titular(tela, dg):
                        demanda.add((tela, dg_alt))

        rows = []
        for (estilo, dg, lf), entry in self.stock.items():
            if not estilo:
                continue
            if (estilo, dg) in demanda:
                continue
            total_ini = sum(entry.get(f, 0.0) for f in FUENTES_ORD)
            if total_ini <= 0:
                continue
            row = {"ESTILO C": estilo, "DG": dg, "LOTE FACE": lf,
                   "INV MANO": entry.get(FUENTE_INV, 0.0)}
            for d in DIA_COLS:
                row[d] = entry.get(d, 0.0)
            row["LBS_TOTAL"] = total_ini
            rows.append(row)

        if not rows:
            return pd.DataFrame(columns=["ESTILO C","DG","LOTE FACE","INV MANO","LBS_TOTAL"])
        return pd.DataFrame(rows).sort_values("LBS_TOTAL", ascending=False).reset_index(drop=True)


# ── Carga pública ─────────────────────────────────────────────────────────────

def load_disponibilidad(file_or_bytes) -> DisponibilidadIndex:
    if hasattr(file_or_bytes, "read") or isinstance(file_or_bytes, str):
        df = pd.read_excel(file_or_bytes, sheet_name="Export", engine="openpyxl")
    else:
        import io
        df = pd.read_excel(io.BytesIO(file_or_bytes), sheet_name="Export", engine="openpyxl")

    required = {"ESTILO C", "DG", "LOTE FACE", "INV EN MANO"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(f"ANALISIS_INV le faltan columnas: {missing}")

    return DisponibilidadIndex(df)
