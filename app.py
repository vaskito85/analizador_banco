import streamlit as st
import pandas as pd

# --- CONFIG ---
CONCEPTOS_A_COMPARAR = [
    "IVA - Alicuota No Alcanzado",
    "Impuesto Ley 25.413 Ali Gral s/Debitos",
    "Percep Ing Brutos No incl en padron PBA",
    "Com. mantenimiento cuenta",
    "Impuesto Ley 25.413 Ali Gral s/Creditos",
    "Comision por Transferencia B. INTERNET COM.",
    "Suscripcion al Periodico Accion",
    "Contracargos a comercios First Data MASTER CONTRACARGO"
]
CONCEPTO_ESPECIAL = "Debito Automatico Directo FEDERACION PATRO"

# --- HELPERS ---
def find_fecha_column(df):
    for col in df.columns:
        if 'fecha' in str(col).lower() or 'date' in str(col).lower():
            return col
    return None

def analyze_data(df, concepto_col, debito_col):
    total_impuestos = 0.0
    total_especial = 0.0
    detalles_especial = pd.DataFrame(columns=['Fecha','Concepto','Débito'])

    if concepto_col not in df.columns or debito_col not in df.columns:
        st.error(f"El archivo debe contener las columnas '{concepto_col}' y '{debito_col}'.")
        return total_impuestos, total_especial, detalles_especial

    fecha_col = find_fecha_column(df)

    for _, row in df.iterrows():
        concepto_row = str(row.get(concepto_col, '')).strip()
        # normales
        if any(concepto_row.startswith(c) for c in CONCEPTOS_A_COMPARAR):
            try:
                total_impuestos += float(str(row.get(debito_col, '')).replace(',', ''))
            except:
                pass
        # especial
        if concepto_row.startswith(CONCEPTO_ESPECIAL):
            try:
                val = float(str(row.get(debito_col, '')).replace(',', ''))
                total_especial += val
                detalles_especial = pd.concat([detalles_especial, pd.DataFrame([{
                    'Fecha': row.get(fecha_col, '') if fecha_col else '',
                    'Concepto': concepto_row,
                    'Débito': row.get(debito_col, '')
                }])], ignore_index=True)
            except:
                pass

    return total_impuestos, total_especial, detalles_especial

def summarize_per_concept(df, concepto_col, debito_col):
    suma_por_concepto = {}
    for concepto in CONCEPTOS_A_COMPARAR:
        mask = df[concepto_col].astype(str).apply(lambda x: x.startswith(concepto))
        suma_por_concepto[concepto] = pd.to_numeric(df[mask][debito_col], errors='coerce').sum()

    summary = pd.DataFrame(list(suma_por_concepto.items()), columns=['Concepto','Total Débito'])
    total_general = summary['Total Débito'].sum()
    summary = pd.concat([summary, pd.DataFrame([['TOTAL GENERAL', total_general]], columns=['Concepto','Total Débito'])], ignore_index=True)

    mask_especial = df[concepto_col].astype(str).apply(lambda x: x.startswith(CONCEPTO_ESPECIAL))
    total_especial = pd.to_numeric(df[mask_especial][debito_col], errors='coerce').sum()
    summary = pd.concat([summary, pd.DataFrame([[CONCEPTO_ESPECIAL, total_especial]], columns=['Concepto','Total Débito'])], ignore_index=True)

    return summary

# --- STREAMLIT UI ---
st.set_page_config(page_title="Analizador Bancario", layout="wide")
st.title("📊 Analizador de Conceptos Bancarios")

# Selección del banco
banco = st.radio(
    "Seleccioná el banco del archivo que vas a analizar:",
    ("Banco Credicoop", "Banco Galicia")
)

# Asignar nombres de columnas según el banco
if banco == "Banco Credicoop":
    concepto_col = "Concepto"
    debito_col = "Débito"
else:  # Banco Galicia
    concepto_col = "Descripción"
    debito_col = "Debitos"

st.write(f"👉 Se analizarán las columnas: **{concepto_col}** y **{debito_col}**")

# Subida del archivo
uploaded_file = st.file_uploader("Elegir archivo Excel o CSV", type=["xlsx", "xls", "csv"])
if uploaded_file:
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)

        st.success(f"Archivo cargado: {uploaded_file.name}")
        st.dataframe(df.head())

        total_impuestos, total_especial, detalles_especial = analyze_data(df, concepto_col, debito_col)
        summary = summarize_per_concept(df, concepto_col, debito_col)

        st.markdown("### Resultados generales")
        st.write(f"**Suma total de impuestos (conceptos normales):** {total_impuestos:,.2f}")
        st.write(f"**Suma total del concepto especial (‘{CONCEPTO_ESPECIAL}’):** {total_especial:,.2f}")

        st.markdown("### Resumen por concepto")
        st.dataframe(summary)

        if not detalles_especial.empty:
            st.markdown(f"### Detalle del concepto especial: {CONCEPTO_ESPECIAL}")
            st.dataframe(detalles_especial[['Fecha','Concepto','Débito']])
        else:
            st.info(f"No se encontraron registros del concepto especial '{CONCEPTO_ESPECIAL}'.")
    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")
