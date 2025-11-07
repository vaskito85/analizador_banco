import streamlit as st
import pandas as pd

# --- CONFIG GENERAL ---
# Conceptos especiales con variaciones
CONCEPTOS_ESPECIALES = {
    "AGUAS BONAERENSES": ["aguas bonaerenses", "aguasbonaerenses"],
    "CONSORCIO ABIERT": ["consorcio abiert"],
    "CAMUZZI": ["camuzzi"],
    "SAN CRISTOBAL": ["san cristobal"],
    "CABLEVISION": ["cablevision"],
    "EDES": ["edes"],
    "ARCA VEP": ["arca vep"],
    "BVNET": ["bvnet"],
    "Maria Luisa": ["maria luisa"]
}

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
def formato_argentino(valor):
    try:
        return format(valor, ',.2f').replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return valor

def find_fecha_column(df):
    for col in df.columns:
        if 'fecha' in str(col).lower() or 'date' in str(col).lower():
            return col
    return None

def analyze_data(df, col_concepto, col_debito):
    total_impuestos = 0.0
    fecha_col = find_fecha_column(df)

    detalles_especiales = pd.DataFrame(columns=['Fecha','Concepto','Débito','Grupo'])

    for _, row in df.iterrows():
        concepto_row = str(row.get(col_concepto, '')).strip()
        concepto_lower = concepto_row.lower()

        # Conceptos normales
        if any(concepto_row.startswith(c) for c in CONCEPTOS_A_COMPARAR):
            try:
                total_impuestos += float(str(row.get(col_debito, '')).replace(',', ''))
            except:
                pass

        # Conceptos especiales (busca si contiene alguna palabra clave)
        for grupo, keywords in CONCEPTOS_ESPECIALES.items():
            if any(k in concepto_lower for k in keywords):
                try:
                    val = float(str(row.get(col_debito, '')).replace(',', ''))
                    detalles_especiales = pd.concat([
                        detalles_especiales,
                        pd.DataFrame([{
                            'Fecha': row.get(fecha_col, '') if fecha_col else '',
                            'Concepto': concepto_row,
                            'Débito': formato_argentino(val),
                            'Grupo': grupo
                        }])
                    ], ignore_index=True)
                except:
                    pass

    return total_impuestos, detalles_especiales

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

    # Aplicar formato argentino
    summary['Total Débito'] = summary['Total Débito'].apply(formato_argentino)

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

        total_impuestos, detalles_especiales = analyze_data(df, col_concepto, col_debito)
        summary = summarize_per_concept(df, col_concepto, col_debito)

        st.markdown("### Resultados generales")
        st.write(f"**Suma total de impuestos (conceptos normales):** {formato_argentino(total_impuestos)}")

        st.markdown("### Resumen por concepto")
        st.dataframe(summary)

        # Mostrar conceptos especiales agrupados
        if not detalles_especiales.empty:
            st.markdown("### Detalle de conceptos especiales")
            for grupo in CONCEPTOS_ESPECIALES.keys():
                grupo_df = detalles_especiales[detalles_especiales['Grupo'] == grupo]
                if not grupo_df.empty:
                    # Calcular subtotal del grupo
                    subtotal = sum([float(str(x).replace('.','').replace(',','.')) for x in grupo_df['Débito']])
                    with st.expander(f"📌 {grupo} ({len(grupo_df)} registros) - Total: {formato_argentino(subtotal)}"):
                        st.dataframe(grupo_df[['Fecha','Concepto','Débito']])
        else:
            st.info("No se encontraron registros de conceptos especiales.")

    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")
