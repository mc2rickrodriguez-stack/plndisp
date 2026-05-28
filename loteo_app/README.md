# NV2 Loteo Tintorería – Streamlit App

Motor de loteo migrado desde Google Colab. Algoritmo 100% idéntico al original.

## Estructura

```
loteo_app/
├── app.py                  ← Entry point de Streamlit
├── requirements.txt
├── engine/
│   ├── utils.py            ← Funciones utilitarias puras
│   ├── loader.py           ← load_inputs() (sin google.colab)
│   └── loteo.py            ← run_loteo() + build_reports()
└── ui/
    └── charts.py           ← Gráficas Plotly
```

## Correr localmente

```bash
pip install -r requirements.txt
cd loteo_app
streamlit run app.py
```

## Deploy en Streamlit Cloud

1. Sube esta carpeta a un repositorio GitHub (público o privado).
2. En [share.streamlit.io](https://share.streamlit.io), crea una nueva app:
   - **Repository**: tu repo
   - **Branch**: main
   - **Main file path**: `loteo_app/app.py`
3. Streamlit Cloud leerá `requirements.txt` automáticamente.
4. Click **Deploy**.

## Funcionalidades

| Función | Descripción |
|---|---|
| Upload | Sube `.xlsx` / `.xlsm` directo desde el browser |
| Vista previa DATA | Filtrable por MIX, configurable en # filas |
| CONFIG editable | Todos los parámetros editables desde la UI sin tocar Excel |
| Loteo con progreso | Barra de progreso en tiempo real |
| Gráficas | Barras capacidad, donut prioridades, heatmap ocupación, completitud LNK |
| Decision Log | Filtros por LNK, Regla y Bloque |
| Comparar corridas | Side-by-side de hasta 5 corridas en la misma sesión |
| Exportar Excel | Descarga con timestamp, incluye todas las hojas del Colab original |

## Diferencias vs Colab

| Colab | Streamlit |
|---|---|
| `from google.colab import files` | `st.file_uploader` / `st.download_button` |
| `files.upload()` | Widget de upload en sidebar |
| `files.download()` | Botón de descarga con nombre `RESULTADOS_LOTES_YYYYMMDD_HHMMSS.xlsx` |
| Sin UI para CONFIG | Formulario editable en sidebar con todos los parámetros |
| Sin gráficas | 4 gráficas Plotly integradas |
| Sin historial | Guarda hasta 5 corridas para comparar en la sesión |
