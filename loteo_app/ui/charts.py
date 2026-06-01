import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st


BLOCK_COLORS = {
    "VENCIDOS": "#ef4444",
    "AHEAD": "#f97316",
    "AHEAD2": "#eab308",
    "OTROS": "#6b7280",
}

MIX_COLORS = {
    "DYE": "#3b82f6",
    "BLEACH": "#10b981",
}


@st.cache_data(show_spinner=False)
def chart_capacidad_barras(df_cap_cap: pd.DataFrame) -> go.Figure:
    """LBS asignadas vs capacidad por categoría/MIX."""
    if df_cap_cap.empty:
        return go.Figure()

    fig = go.Figure()
    for mix_val, grp in df_cap_cap.groupby("MIX"):
        color = MIX_COLORS.get(mix_val, "#8b5cf6")
        label = f"{mix_val} – Capacidad"
        fig.add_trace(go.Bar(
            name=label,
            x=grp["CATEGORIA"],
            y=grp["CAPACIDAD"],
            marker_color=color,
            opacity=0.35,
            legendgroup=mix_val,
        ))
        fig.add_trace(go.Bar(
            name=f"{mix_val} – Asignado",
            x=grp["CATEGORIA"],
            y=grp["LBS_ASIGNADAS"],
            marker_color=color,
            legendgroup=mix_val,
        ))

    fig.update_layout(
        barmode="overlay",
        title="LBS Asignadas vs Capacidad por Categoría",
        xaxis_title="Categoría",
        yaxis_title="LBS",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=420,
        margin=dict(t=60, b=40),
    )
    return fig


@st.cache_data(show_spinner=False)
def chart_bloques_donut(df_prio_vs_asig: pd.DataFrame) -> go.Figure:
    """Distribución LBS por bloque de prioridad."""
    if df_prio_vs_asig.empty:
        return go.Figure()

    agg = df_prio_vs_asig.groupby("BLOQUE")["LBS_ASIGNADAS"].sum().reset_index()
    order = ["VENCIDOS", "AHEAD", "AHEAD2", "OTROS"]
    agg["ORD"] = agg["BLOQUE"].apply(lambda x: order.index(x) if x in order else 99)
    agg = agg.sort_values("ORD")

    colors = [BLOCK_COLORS.get(b, "#9ca3af") for b in agg["BLOQUE"]]

    fig = go.Figure(go.Pie(
        labels=agg["BLOQUE"],
        values=agg["LBS_ASIGNADAS"],
        hole=0.55,
        marker_colors=colors,
        textinfo="label+percent",
        hovertemplate="%{label}: %{value:,.0f} LBS<extra></extra>",
    ))
    fig.update_layout(
        title="Distribución LBS por Bloque de Prioridad",
        height=380,
        margin=dict(t=60, b=20, l=20, r=20),
        showlegend=False,
    )
    return fig


@st.cache_data(show_spinner=False)
def chart_heatmap_capacidad(df_cap_cap: pd.DataFrame) -> go.Figure:
    """Ocupación % de capacidad DYE vs BLEACH."""
    if df_cap_cap.empty:
        return go.Figure()

    df = df_cap_cap.copy()
    df["PCT_OCUP"] = (df["LBS_ASIGNADAS"] / df["CAPACIDAD"].replace(0, pd.NA) * 100).fillna(0).clip(0, 150)

    pivot = df.pivot_table(index="MIX", columns="CATEGORIA", values="PCT_OCUP", aggfunc="mean").fillna(0)

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=list(pivot.columns),
        y=list(pivot.index),
        colorscale=[
            [0.0, "#dbeafe"],
            [0.5, "#3b82f6"],
            [0.8, "#f97316"],
            [1.0, "#ef4444"],
        ],
        zmin=0,
        zmax=120,
        text=[[f"{v:.1f}%" for v in row] for row in pivot.values],
        texttemplate="%{text}",
        hovertemplate="MIX: %{y}<br>Cat: %{x}<br>Ocupación: %{z:.1f}%<extra></extra>",
        colorbar=dict(title="% Ocup.", ticksuffix="%"),
    ))
    fig.update_layout(
        title="Heatmap de Ocupación de Capacidad (%)",
        height=280,
        margin=dict(t=60, b=40),
        xaxis_title="Categoría",
        yaxis_title="MIX",
    )
    return fig


@st.cache_data(show_spinner=False)
def chart_completitud_lnk(df_lnk_comp: pd.DataFrame) -> go.Figure:
    """Barras apiladas de completitud por LNK."""
    if df_lnk_comp.empty:
        return go.Figure()

    estado_counts = df_lnk_comp["ESTADO"].value_counts().reset_index()
    estado_counts.columns = ["ESTADO", "COUNT"]

    color_map = {
        "COMPLETO": "#10b981",
        "COMPLETO (SCRAP)": "#f59e0b",
        "INCOMPLETO": "#ef4444",
    }
    colors = [color_map.get(e, "#6b7280") for e in estado_counts["ESTADO"]]

    fig = go.Figure(go.Bar(
        x=estado_counts["ESTADO"],
        y=estado_counts["COUNT"],
        marker_color=colors,
        text=estado_counts["COUNT"],
        textposition="outside",
    ))
    fig.update_layout(
        title="Completitud de LNKs",
        yaxis_title="Cantidad de LNKs",
        height=320,
        margin=dict(t=60, b=40),
    )
    return fig
