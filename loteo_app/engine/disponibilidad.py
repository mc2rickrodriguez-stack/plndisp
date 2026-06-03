"""
Motor de disponibilidad de tejido v1
=====================================
Lee el archivo ANALISIS_INV (hoja Export) y construye un índice que el motor
de loteo consulta antes de asignar cada LNK a un lote.

Conceptos clave
---------------
* (ESTILO C, DG, LOTE FACE)  →  clave de disponibilidad
* INV EN MANO                →  fuente "INV MANO" (hoy, día 0)
* DIA 1 … DIA 10             →  producción diaria planeada (mañana en adelante)
* LOTE FACE                  →  identifica el lote de hilo — dentro de un LNK
                                 todos los componentes deben usar el mismo LOTE FACE
* Consumo acumulado          →  se descuenta en tiempo real durante el loteo

Estructura interna
------------------
DisponibilidadIndex.stock   dict  (estilo, dg, lote_face) → {
    "INV MANO": lbs_float,          # disponible hoy
    "DIA 1" … "DIA 10": lbs_float,  # producción ese día
    "_used": {"INV MANO": 0.0, "DIA 1": 0.0, …},  # consumido
}

Interfaz pública
----------------
load_disponibilidad(file_or_bytes)  →  DisponibilidadIndex
DisponibilidadIndex.check_lnk(...)  →  LnkDisponibilidad | None
DisponibilidadIndex.consume(...)    →  None
DisponibilidadIndex.build_reports() →  (df_detalle_tejido, df_stock_report)
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── Constantes ────────────────────────────────────────────────────────────────
DIA_COLS = [f"DIA {i}" for i in range(1, 11)]
FUENTE_INV  = "INV MANO"         # inventario en mano (día 0)
FUENTES_ORD = [FUENTE_INV] + DIA_COLS   # orden de consumo preferido

# Urgencia para elegir el LOTE FACE con menor día máximo
BLOQUE_URGENCIA = {"VENCIDOS": 0, "AHEAD": 1, "AHEAD2": 2, "OTROS": 3}

# ── Helpers internos ──────────────────────────────────────────────────────────

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
    """INV MANO → -1 (hoy), DIA N → N-1 (0-based)."""
    if fuente == FUENTE_INV:
        return -1
    try:
        return int(fuente.split()[-1]) - 1   # "DIA 3" → 2
    except Exception:
        return 999


# ── Resultado de check_lnk ────────────────────────────────────────────────────

@dataclass
class ComponenteFuente:
    """Detalle de cómo se cubre un componente de un LNK."""
    componente: str       # "CUERPO", "MANGAS", "RIB", "POCKET"
    estilo: str
    dg: float
    lote_face: str
    lbs_necesarias: float
    fuentes: List[Tuple[str, float]]  # [("INV MANO", 300), ("DIA 1", 200)]

    @property
    def dia_maximo(self) -> int:
        """Día más tardío requerido para este componente (0=hoy, 1=mañana…)."""
        max_d = -1
        for fuente, lbs in self.fuentes:
            if lbs > 1e-9:
                d = _day_index(fuente)
                if d > max_d:
                    max_d = d
        return max_d   # -1 si solo INV MANO


@dataclass
class LnkDisponibilidad:
    """Resultado de check_lnk: plan de cobertura para un LNK con un LOTE FACE dado."""
    lnk: str
    lote_face: str
    componentes: List[ComponenteFuente]
    viable: bool = True

    @property
    def dia_maximo_lote(self) -> int:
        """Día más tardío entre todos los componentes del LNK."""
        return max((c.dia_maximo for c in self.componentes), default=-1)

    @property
    def fuente_resumen(self) -> str:
        """Texto para el reporte: 'INV MANO', 'DIA 3', 'INV MANO + DIA 2', etc."""
        fuentes_usadas: set = set()
        for comp in self.componentes:
            for fuente, lbs in comp.fuentes:
                if lbs > 1e-9:
                    fuentes_usadas.add(fuente)
        if not fuentes_usadas:
            return "—"
        ordered = sorted(fuentes_usadas, key=_day_index)
        return " + ".join(ordered)


# ── Índice principal ──────────────────────────────────────────────────────────

class DisponibilidadIndex:
    """
    Índice O(1) de disponibilidad de tejido.

    stock[(estilo, dg, lote_face)] = {
        "INV MANO": float,
        "DIA 1": float, …, "DIA 10": float,
        "_used": {"INV MANO": 0.0, …}
    }
    """

    def __init__(self, df_raw: pd.DataFrame):
        self.stock: Dict[tuple, dict] = {}
        self._raw = df_raw.copy()          # guardamos para el reporte de stock
        self._consumos: List[dict] = []    # log de cada consumo
        self._build(df_raw)

    # ── Construcción ──────────────────────────────────────────────────────────

    def _build(self, df: pd.DataFrame) -> None:
        """Normaliza y construye el índice desde el DataFrame raw."""
        df = df.copy()
        df["ESTILO C"]   = df["ESTILO C"].apply(_norm)
        df["LOTE FACE"]  = df["LOTE FACE"].apply(_norm)
        df["DG"]         = df["DG"].apply(_flt)

        # Aseguramos que todas las columnas DIA existan (pueden no estar en archivos pequeños)
        for d in DIA_COLS:
            if d not in df.columns:
                df[d] = 0.0
            df[d] = df[d].apply(_flt)

        df["INV EN MANO"] = df["INV EN MANO"].apply(_flt)

        # Agrupar por (ESTILO C, DG, LOTE FACE) sumando sub-filas de LOTE_H
        agg = {c: "sum" for c in ["INV EN MANO"] + DIA_COLS}
        grouped = df.groupby(["ESTILO C", "DG", "LOTE FACE"], as_index=False).agg(agg)

        for _, row in grouped.iterrows():
            key = (_norm(row["ESTILO C"]), float(row["DG"]), _norm(row["LOTE FACE"]))
            entry = {
                FUENTE_INV: float(row["INV EN MANO"]),
                "_used": {FUENTE_INV: 0.0},
            }
            for d in DIA_COLS:
                entry[d]         = float(row[d])
                entry["_used"][d] = 0.0
            self.stock[key] = entry

    # ── Disponible neto ───────────────────────────────────────────────────────

    def disponible(self, estilo: str, dg: float, lote_face: str, fuente: str) -> float:
        key = (estilo, dg, lote_face)
        if key not in self.stock:
            return 0.0
        e = self.stock[key]
        return max(0.0, e.get(fuente, 0.0) - e["_used"].get(fuente, 0.0))

    def disponible_total(self, estilo: str, dg: float, lote_face: str) -> float:
        """Total disponible a través de todas las fuentes (hoy + todos los días)."""
        return sum(
            self.disponible(estilo, dg, lote_face, f)
            for f in FUENTES_ORD
        )

    # ── Check de un componente ────────────────────────────────────────────────

    def _check_componente(
        self,
        comp_name: str,
        estilo: str,
        dg: float,
        lote_face: str,
        lbs_necesarias: float,
        tentativo: Optional[Dict] = None,
    ) -> Optional[ComponenteFuente]:
        """
        Verifica si hay suficiente disponibilidad para un componente.
        tentativo: dict de consumos previos *aún no confirmados* de este lote
                   (para no doble-contar dentro del mismo lote al evaluar candidatos).
        Returns ComponenteFuente si viable, None si no hay suficiente.
        """
        key = (estilo, dg, lote_face)
        if key not in self.stock:
            return None

        if lbs_necesarias <= 1e-9:
            return ComponenteFuente(comp_name, estilo, dg, lote_face, 0.0, [])

        restante = lbs_necesarias
        fuentes_usadas: List[Tuple[str, float]] = []

        tent = tentativo or {}   # consumos temporales del lote actual

        for fuente in FUENTES_ORD:
            disp = max(
                0.0,
                self.disponible(estilo, dg, lote_face, fuente)
                - tent.get((estilo, dg, lote_face, fuente), 0.0)
            )
            if disp <= 1e-9:
                continue
            tomar = min(restante, disp)
            fuentes_usadas.append((fuente, tomar))
            restante -= tomar
            if restante <= 1e-9:
                break

        if restante > 1e-9:
            return None   # no hay suficiente

        return ComponenteFuente(comp_name, estilo, dg, lote_face, lbs_necesarias, fuentes_usadas)

    # ── Check de un LNK completo ──────────────────────────────────────────────

    def check_lnk(
        self,
        lnk_row: pd.Series,
        lote_face: str,
        tentativo: Optional[Dict] = None,
    ) -> Optional[LnkDisponibilidad]:
        """
        Verifica si un LNK puede cubrirse con el LOTE FACE dado.

        Componentes activos = aquellos con TELA != '-' y LBS > 0.
        Todos deben cubrirse con el mismo LOTE FACE.

        tentativo: {(estilo,dg,lote_face,fuente): lbs_reservadas_en_este_lote}
        """
        COMPONENTES = [
            ("CUERPO",  "TELA.CUERPO",  "DG.CUERPO",  "LB.CUERPO"),
            ("MANGAS",  "TELA.MANGAS",  "DG.MANGAS",  "LB.MANGAS"),
            ("RIB",     "TELA.RIB",     "DG.RIB",     "LB.RIB"),
            ("POCKET",  "TELA.POCKET",  "DG.POCKET",  "LB.POCKET"),
        ]

        componentes_ok: List[ComponenteFuente] = []

        for comp_name, tcol, dgcol, lbscol in COMPONENTES:
            tela = _norm(lnk_row.get(tcol, ""))
            if not tela or tela == "-":
                continue
            try:
                dg  = float(lnk_row.get(dgcol, 0))
                lbs = float(lnk_row.get(lbscol, 0))
            except Exception:
                dg = lbs = 0.0
            if dg == 0.0 or lbs <= 0:
                continue

            comp = self._check_componente(
                comp_name, tela, dg, lote_face, lbs, tentativo
            )
            if comp is None:
                return None   # este componente no tiene disponibilidad suficiente
            componentes_ok.append(comp)

        if not componentes_ok:
            return None   # ningún componente activo encontrado

        return LnkDisponibilidad(
            lnk=_norm(lnk_row.get("LNK", "")),
            lote_face=lote_face,
            componentes=componentes_ok,
            viable=True,
        )

    # ── Selección de LOTE FACE óptimo para un LNK ────────────────────────────

    def elegir_lote_face(
        self,
        lnk_row: pd.Series,
        bloque: str,
        tentativo: Optional[Dict] = None,
    ) -> Optional[LnkDisponibilidad]:
        """
        Evalúa todos los LOTE FACE que pueden cubrir el LNK y elige el
        que minimiza el día máximo de entrega.
        Para igual día máximo, prefiere el que tiene más INV MANO.
        """
        # Recopilar LOTE FACE candidatos para el componente principal (CUERPO)
        tela_cuerpo = _norm(lnk_row.get("TELA.CUERPO", ""))
        try:
            dg_cuerpo = float(lnk_row.get("DG.CUERPO", 0))
        except Exception:
            dg_cuerpo = 0.0

        if not tela_cuerpo or tela_cuerpo == "-" or dg_cuerpo == 0.0:
            return None

        # Todos los LOTE FACE conocidos para este estilo+DG
        candidatos_lf = [
            lf
            for (est, dg, lf) in self.stock.keys()
            if est == tela_cuerpo and abs(dg - dg_cuerpo) < 1e-9
        ]

        if not candidatos_lf:
            return None

        mejor: Optional[LnkDisponibilidad] = None
        mejor_dia  = 9999
        mejor_inv  = -1.0

        for lf in candidatos_lf:
            resultado = self.check_lnk(lnk_row, lf, tentativo)
            if resultado is None:
                continue
            dia = resultado.dia_maximo_lote
            inv = self.disponible(tela_cuerpo, dg_cuerpo, lf, FUENTE_INV)

            # Criterio: mínimo día máximo; en empate, más inventario en mano
            if dia < mejor_dia or (dia == mejor_dia and inv > mejor_inv):
                mejor      = resultado
                mejor_dia  = dia
                mejor_inv  = inv

        return mejor

    # ── Elegir LOTE FACE para un lote completo (múltiples LNKs) ─────────────

    def elegir_lote_face_lote(
        self,
        lnk_rows: List[pd.Series],
        bloque: str,
        tentativo: Optional[Dict] = None,
    ) -> Optional[Tuple[str, Dict[str, LnkDisponibilidad]]]:
        """
        Para un lote con múltiples LNKs, elige el LOTE FACE que minimiza
        el día máximo global del lote completo.

        Returns (lote_face, {lnk: LnkDisponibilidad}) o None si ninguno viable.
        """
        if not lnk_rows:
            return None

        # Candidatos: LOTE FACE del primer LNK (restringe el espacio)
        tela_ref = _norm(lnk_rows[0].get("TELA.CUERPO", ""))
        try:
            dg_ref = float(lnk_rows[0].get("DG.CUERPO", 0))
        except Exception:
            dg_ref = 0.0

        candidatos_lf = list({
            lf
            for (est, dg, lf) in self.stock.keys()
            if est == tela_ref and abs(dg - dg_ref) < 1e-9
        })

        if not candidatos_lf:
            return None

        mejor_lf    = None
        mejor_dia   = 9999
        mejor_inv   = -1.0
        mejor_map   = {}

        for lf in candidatos_lf:
            # Acumulamos consumos temporales para no doble-contar dentro del lote
            tent_lf: Dict = dict(tentativo) if tentativo else {}
            plan_lf: Dict[str, LnkDisponibilidad] = {}
            viable = True

            for lnk_row in lnk_rows:
                res = self.check_lnk(lnk_row, lf, tent_lf)
                if res is None:
                    viable = False
                    break
                lnk_id = _norm(lnk_row.get("LNK", ""))
                plan_lf[lnk_id] = res
                # Acumular reservas tentativas
                for comp in res.componentes:
                    for fuente, lbs in comp.fuentes:
                        k = (comp.estilo, comp.dg, lf, fuente)
                        tent_lf[k] = tent_lf.get(k, 0.0) + lbs

            if not viable:
                continue

            dia_max = max(r.dia_maximo_lote for r in plan_lf.values()) if plan_lf else 9999
            inv_ref = self.disponible(tela_ref, dg_ref, lf, FUENTE_INV)

            if dia_max < mejor_dia or (dia_max == mejor_dia and inv_ref > mejor_inv):
                mejor_lf  = lf
                mejor_dia  = dia_max
                mejor_inv  = inv_ref
                mejor_map  = plan_lf

        if mejor_lf is None:
            return None

        return mejor_lf, mejor_map

    # ── Confirmar consumo ─────────────────────────────────────────────────────

    def consume(
        self,
        lnk_disp: LnkDisponibilidad,
        lote_id: str,
    ) -> None:
        """Descuenta el consumo planificado del stock real."""
        for comp in lnk_disp.componentes:
            for fuente, lbs in comp.fuentes:
                if lbs <= 1e-9:
                    continue
                key = (comp.estilo, comp.dg, lnk_disp.lote_face)
                if key in self.stock:
                    self.stock[key]["_used"][fuente] = (
                        self.stock[key]["_used"].get(fuente, 0.0) + lbs
                    )
                # Log para el reporte
                self._consumos.append({
                    "LOTE_ID":    lote_id,
                    "LNK":        lnk_disp.lnk,
                    "COMPONENTE": comp.componente,
                    "ESTILO C":   comp.estilo,
                    "DG":         comp.dg,
                    "LOTE FACE":  lnk_disp.lote_face,
                    "FUENTE":     fuente,
                    "LBS_ASIGNADAS": lbs,
                })

    # ── Verificación rápida: ¿tiene LNK alguna disponibilidad? ───────────────

    def lnk_tiene_disponibilidad(self, lnk_row: pd.Series) -> bool:
        """
        True si el LNK puede cubrirse con al menos un LOTE FACE.
        Se usa como pre-filtro rápido antes de intentar_lote_para_rango.
        """
        return self.elegir_lote_face(lnk_row, bloque="OTROS") is not None

    # ── Reportes ──────────────────────────────────────────────────────────────

    def build_reports(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Returns:
            df_detalle_tejido  - una fila por (LOTE_ID, LNK, COMPONENTE, FUENTE)
            df_stock_report    - una fila por (ESTILO C, DG, LOTE FACE) con
                                 INV INICIAL, ASIGNADO, REMANENTE por fuente
        """
        # ── Detalle de tejido (reporte de asignación por fuente) ─────────────
        df_det = pd.DataFrame(self._consumos) if self._consumos else pd.DataFrame(columns=[
            "LOTE_ID","LNK","COMPONENTE","ESTILO C","DG","LOTE FACE","FUENTE","LBS_ASIGNADAS"
        ])

        # Añadir columna DIA_MAXIMO_LOTE (máximo DIA entre todos los componentes del lote)
        if len(df_det) > 0:
            def _fuente_a_dia(f):
                return 0 if f == FUENTE_INV else _day_index(f) + 1  # 1-based

            df_det["DIA_NUM"] = df_det["FUENTE"].apply(_fuente_a_dia)
            lote_dia_max = df_det.groupby("LOTE_ID")["DIA_NUM"].max().rename("DIA_MAX_LOTE")
            df_det = df_det.merge(lote_dia_max, on="LOTE_ID", how="left")
            df_det["DIA_LOTE"] = df_det["DIA_MAX_LOTE"].apply(
                lambda d: "INV MANO" if d == 0 else f"DIA {int(d)}"
            )
            df_det = df_det.drop(columns=["DIA_NUM","DIA_MAX_LOTE"])

        # ── Reporte de stock (inicial / asignado / remanente) ────────────────
        rows_stock = []
        for (estilo, dg, lf), entry in self.stock.items():
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


# ── Función de carga pública ──────────────────────────────────────────────────

def load_disponibilidad(file_or_bytes) -> DisponibilidadIndex:
    """
    Lee el archivo ANALISIS_INV (hoja 'Export') y devuelve un DisponibilidadIndex.
    file_or_bytes: ruta de archivo o bytes (para Streamlit file_uploader).
    """
    if hasattr(file_or_bytes, "read") or isinstance(file_or_bytes, (str,)):
        df = pd.read_excel(file_or_bytes, sheet_name="Export", engine="openpyxl")
    else:
        import io
        df = pd.read_excel(io.BytesIO(file_or_bytes), sheet_name="Export", engine="openpyxl")

    required = {"ESTILO C", "DG", "LOTE FACE", "INV EN MANO"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(f"ANALISIS_INV le faltan columnas: {missing}")

    return DisponibilidadIndex(df)
