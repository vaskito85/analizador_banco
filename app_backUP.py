import streamlit as st
import pandas as pd

# --- CONFIG GENERAL ---
CONCEPTO_ESPECIAL = "Debito Automatico Directo FEDERACION PATRO"

# --- STREAMLIT UI ---
st.set_page_config(page_title="Analizador Bancario", layout="wide")
st.title("📊 Analizador de Conceptos Bancarios")

# --- SELECCIÓN DE BANCO ---
banco = st.selectbox("Seleccioná el banco:", ["Banco Credicoop", "Banco Galicia"])

# Definir columnas y conceptos según banco
if banco == "Banco Credicoop":
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
    col_concepto = "Concepto"
    col_debito = "Débito"

elif banco == "Banco Galicia":
    CONCEPTOS_A_COMPARAR = [
        "Imp. Deb. Ley 25413 Gral.",
        "Imp. Cre. Ley 25413",
        "Iva"
    ]
    col_concepto = "Descripción"
    col_debito = "Débitos"

st.write(f"Configurado para analizar archivos de **{banco}** usando las columnas **{col_concepto}** y **{col_debito}**.")
st.write("Subí un archivo Excel o CSV con los datos bancarios para analizarlo.")

# --- FUNCIONES AUXILIARES ---
def find_fecha_column(df):
    for col in df.columns:
        if 'fecha' in str(col).lower() or 'date' in str(col).lower():
            return col
    return None

def analyze_data(df, col_concepto, col_debito):
    total_impuestos = 0.0
    total_especial = 0.0
    detalles_especial = pd.DataFrame(columns=['Fecha','Concepto','Débito'])

    fecha_col = find_fecha_column(df)

    for _, row in df.iterrows():
        concepto_row = str(row.get(col_concepto, '')).strip()

        # Conceptos normales
        if any(concepto_row.startswith(c) for c in CONCEPTOS_A_COMPARAR):
            try:
                total_impuestos += float(str(row.get(col_debito, '')).replace(',', ''))
            except:
                pass

        # Concepto especial
        if concepto_row.startswith(CONCEPTO_ESPECIAL):
            try:
                val = float(str(row.get(col_debito, '')).replace(',', ''))
                total_especial += val
                detalles_especial = pd.concat([
                    detalles_especial,
                    pd.DataFrame([{
                        'Fecha': row.get(fecha_col, '') if fecha_col else '',
                        'Concepto': concepto_row,
                        'Débito': row.get(col_debito, '')
                    }])
                ], ignore_index=True)
            except:
                pass

    return total_impuestos, total_especial, detalles_especial

def summarize_per_concept(df, col_concepto, col_debito):
    suma_por_concepto = {}
    for concepto in CONCEPTOS_A_COMPARAR:
        mask = df[col_concepto].astype(str).apply(lambda x: x.startswith(concepto))
        suma_por_concepto[concepto] = pd.to_numeric(df[mask][col_debito], errors='coerce').sum()

    summary = pd.DataFrame(list(suma_por_concepto.items()), columns=['Concepto','Total Débito'])
    total_general = summary['Total Débito'].sum()
    summary = pd.concat([
        summary,
        pd.DataFrame([['TOTAL GENERAL', total_general]], columns=['Concepto','Total Débito'])
    ], ignore_index=True)

    # Concepto especial
    mask_especial = df[col_concepto].astype(str).apply(lambda x: x.startswith(CONCEPTO_ESPECIAL))
    total_especial = pd.to_numeric(df[mask_especial][col_debito], errors='coerce').sum()
    if total_especial > 0:
        summary = pd.concat([
            summary,
            pd.DataFrame([[CONCEPTO_ESPECIAL, total_especial]], columns=['Concepto','Total Débito'])
        ], ignore_index=True)

    return summary

# --- CARGA DE ARCHIVO ---
uploaded_file = st.file_uploader("Elegir archivo", type=["xlsx", "xls", "csv"])
if uploaded_file:
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)

        st.success(f"Archivo cargado: {uploaded_file.name}")
        st.dataframe(df.head())

        total_impuestos, total_especial, detalles_especial = analyze_data(df, col_concepto, col_debito)
        summary = summarize_per_concept(df, col_concepto, col_debito)

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
