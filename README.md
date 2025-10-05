# Analizador de Conceptos Bancarios (Streamlit)

Sube un Excel/CSV con columnas `Concepto` y `Débito`. 
La app suma ciertos conceptos (lista en el código) y trata un concepto especial "Debito Automatico Directo FEDERACION PATRO" por separado, mostrando detalle (Fecha, Concepto completo, Débito).

## Ejecutar localmente
1. `pip install -r requirements.txt`
2. `streamlit run app.py`

## Deploy en Streamlit Cloud
Subir repo a GitHub (ver pasos abajo) y crear una nueva app en Streamlit Cloud apuntando al `app.py`.

