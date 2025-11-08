import streamlit as st
import pandas as pd

# --- CONFIG GENERAL ---
CONCEPTOS_ESPECIALES = {
    "AGUAS BONAERENSES": ["aguas bonaerenses", "aguasbonaerenses"],
    "CONSORCIO ABIERT": ["consorcio abiert", "debito directo consorcio abiert-000"],
    "CAMUZZI": ["camuzzi"],
    "SAN CRISTOBAL": ["san cristobal", "debito directo san cristobal sg-040"],
    "CABLEVISION": ["cablevision"],
    "EDES": ["edes"],
    "ARCA VEP": ["arca vep"],
    "BVNET": ["bvnet"],
    "Maria Luisa": ["maria luisa"],
    "SODAGO": ["sodago"],
    "PAGO AUTOMATICO SERVICIOS": ["pago automatico servicios"]
}

# --- STREAMLIT UI ---
st.set_page_config(page_title="Analizador Bancario", layout="wide")
st.title("📊 Analizador de Conceptos Bancarios")

# --- SELECCIÓN DE BANCO ---
banco = st.selectbox("Seleccioná el banco:", ["Banco Credicoop", "Banco Galicia", "Banco Roela"])

# --- CONFIGURACIÓN POR BANCO ---
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
    invertir_signo = False

elif banco == "Banco Galicia":
    CONCEPTOS_A_COMPARAR = [
        "Imp. Deb. Ley 25413 Gral.",
        "Imp. Cre. Ley 25413",
        "Iva"
    ]
    col_concepto = "Descripción"
    col_debito = "Débitos"
    invertir_signo = False

elif banco == "Banco Roela":
    CONCEPTOS_A_COMPARAR = [
        "IMPUESTO LEY 25413",
        "COM. ONLINE SIRO ELECTRONICOS",
        "I.V.A.",
        "COM.MANTENIMIENTO CUENTA MENSUAL",
        "TR.INTERB. DIST.TIT. 30717991946-BA"
    ]
    col_concepto = "descripcion"
    col_debito = "monto"
    invertir_signo = True

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

def analyze_data(df, col_concepto, col_debito, invertir_signo):
    total_impuestos = 0.0
    fecha_col = find_fecha_column(df)
    detalles_especiales = pd.DataFrame(columns=['Fecha','Concepto','Débito','Grupo'])

    for _, row in df.iterrows():
        concepto_row = str(row.get(col_concepto, '')).strip()
        concepto_lower = concepto_row.lower()
        try:
            monto = float(str(row.get(col_debito, '')).replace(',', ''))
            if invertir_signo and monto < 0:
                monto = -monto
        except:
            continue

        # Conceptos normales
        if any(concepto_lower.startswith(c.lower()) for c in CONCEPTOS_A_COMPARAR):
            total_impuestos += monto

        # Conceptos especiales
        for grupo, keywords in CONCEPTOS_ESPECIALES.items():
            if any(k in concepto_lower for k in keywords):
                detalles_especiales = pd.concat([
                    detalles_especiales,
                    pd.DataFrame([{
                        'Fecha': row.get(fecha_col, '') if fecha_col else '',
                        'Concepto': concepto_row,
                        'Débito': formato_argentino(monto),
                        'Grupo': grupo
                    }])
                ], ignore_index=True)

    return total_impuestos, detalles_especiales

def summarize_per_concept(df, col_concepto, col_debito, invertir_signo):
    suma_por_concepto = {}
    for concepto in CONCEPTOS_A_COMPARAR:
        mask = df[col_concepto].astype(str).str.lower().str.startswith(concepto.lower())
        valores = pd.to_numeric(df[mask][col_debito], errors='coerce')
        if invertir_signo:
            valores = valores[valores < 0] * -1
        suma_por_concepto[concepto] = valores.sum()

    summary = pd.DataFrame(list(suma_por_concepto.items()), columns=['Concepto','Total Débito'])
    total_general = summary['Total Débito'].sum()
    summary = pd.concat([
        summary,
        pd.DataFrame([['TOTAL GENERAL', total_general]], columns=['Concepto','Total Débito'])
    ], ignore_index=True)

    summary['Total Débito'] = summary['Total Débito'].apply(formato_argentino)
    return summary

# --- CARGA DE ARCHIVO ---
uploaded_file = st.file_uploader("Elegir archivo", type=["xlsx", "xls", "csv"])
if uploaded_file:
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            try:
                df = pd.read_csv(uploaded_file, encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(uploaded_file, encoding='latin1')
        else:
            df = pd.read_excel(uploaded_file)

        st.success(f"Archivo cargado: {uploaded_file.name}")
        st.dataframe(df.head())

        total_impuestos, detalles_especiales = analyze_data(df, col_concepto, col_debito, invertir_signo)
        summary = summarize_per_concept(df, col_concepto, col_debito, invertir_signo)

        st.markdown("### Resultados generales")
        st.write(f"**Suma total de impuestos (conceptos normales):** {formato_argentino(total_impuestos)}")

        st.markdown("### Resumen por concepto")
        st.dataframe(summary)

        if not detalles_especiales.empty:
            st.markdown("### Detalle de conceptos especiales")
            for grupo in CONCEPTOS_ESPECIALES.keys():
                grupo_df = detalles_especiales[detalles_especiales['Grupo'] == grupo]
                if not grupo_df.empty:
                    subtotal = sum([float(str(x).replace('.','').replace(',','.')) for x in grupo_df['Débito']])
                    with st.expander(f"📌 {grupo} ({len(grupo_df)} registros) - Total: {formato_argentino(subtotal)}"):
                        st.dataframe(grupo_df[['Fecha','Concepto','Débito']])
        else:
            st.info("No se encontraron registros de conceptos especiales.")

    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")

