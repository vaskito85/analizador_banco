import streamlit as st
import pandas as pd
import re
import unicodedata
from io import BytesIO

# ==========================
# === CONFIG GENERAL =======
# ==========================

CONCEPTOS_ESPECIALES = {
    "AGUAS BONAERENSES": ["aguas bonaerenses", "aguasbonaerenses"],
    "CONSORCIO ABIERT": ["consorcio abiert"],
    "CAMUZZI": ["camuzzi"],
    "SAN CRISTOBAL": ["san cristobal", "sancristobal"],
    "CABLEVISION": ["cablevision", "cablevisión"],
    "EDES": ["edes"],
    "ARCA VEP": ["arca vep"],
    "BVNET": ["bvnet"],
    "Maria Luisa": ["maria luisa"],
    "SODAGO": ["sodago"],
    "PAGO AUTOMATICO SERVICIOS": ["pago automatico servicios", "pago automático servicios"],
    # --- NUEVO CONCEPTO ESPECIAL ---
    "FEDERACION PATRO": [
        "federacion patro",
        "federación patro",
        "federacion patronal",
        "federación patronal",
        "seguro federacion patronal",
        "seguro federación patronal"
    ]
}

# ==========================
# === UTILIDADES ===========
# ==========================

def normalize_text(s: str) -> str:
    """Minúsculas, sin acentos, sin doble espacio, strip."""
    if pd.isna(s):
        return ""
    s = str(s).strip().lower()
    s = " ".join(s.split())  # colapsa espacios
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    return s

def formato_argentino(valor):
    try:
        return format(float(valor), ',.2f').replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return valor

def parse_amount(x):
    """Parsea importes en formatos variados:
       - "1.234,56", "1234,56", "1,234.56", "1234.56"
       - "$ 1.234,56", " (1.234,56) "
       Devuelve float o NaN.
    """
    if pd.isna(x):
        return float('nan')
    s = str(x)
    # usa paréntesis como negativo
    negative = '(' in s and ')' in s

    # elimina todo menos dígitos, coma, punto y signo menos
    s = re.sub(r'[^0-9,.\-]', '', s)

    # si hay coma y punto, asumimos que el punto es miles y la coma es decimal (estilo AR)
    if ',' in s and '.' in s:
        s = s.replace('.', '')  # quita miles
        s = s.replace(',', '.') # decimal punto

    # si solo hay coma, tómala como decimal
    elif ',' in s and '.' not in s:
        s = s.replace(',', '.')

    # si solo hay punto, ya es decimal
    # si no hay separador, es entero

    try:
        val = float(s)
        if negative:
            val = -val
        return val
    except:
        return float('nan')

def find_fecha_column(df):
    for col in df.columns:
        c = normalize_text(col)
        if 'fecha' in c or 'date' in c or c == 'fec':
            return col
    return None

def guess_column(df, candidates):
    """Intenta encontrar una columna que contenga cualquiera de los alias en candidates."""
    cols_norm = {col: normalize_text(col) for col in df.columns}
    for alias in candidates:
        for col, cn in cols_norm.items():
            if alias in cn:
                return col
    return None

def ensure_clean_columns(df):
    # Limpia nombres de columnas tanto para CSV como Excel
    df.columns = (df.columns
                  .str.strip()
                  .str.replace(r'\s+', ' ', regex=True))
    # Limpia strings base
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].astype(str).str.strip()
    return df

def conceptos_regex(keywords):
    # arma regex OR sobre keywords normalizados
    kws = [re.escape(normalize_text(k)) for k in keywords]
    return r'(' + '|'.join(kws) + r')'

# ==========================
# === STREAMLIT UI =========
# ==========================

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
    default_concept_col = "Concepto"
    default_debito_col = "Débito"
    invertir_signo = False

elif banco == "Banco Galicia":
    CONCEPTOS_A_COMPARAR = [
        "Imp. Deb. Ley 25413 Gral.",
        "Imp. Cre. Ley 25413",
        "Iva"
    ]
    default_concept_col = "Descripción"
    default_debito_col = "Débitos"
    invertir_signo = False

else:  # Banco Roela
    CONCEPTOS_A_COMPARAR = [
        "IMPUESTO LEY 25413",
        "IMPUESTO LEY 25413 CONSORCIO ABIERT",
        "IMPUESTO LEY 25413 SAN CRISTOBAL SG",
        "COM. ONLINE SIRO ELECTRONICOS",
        "I.V.A.",
        "COM.MANTENIMIENTO CUENTA MENSUAL",
        "TR.INTERB. DIST.TIT. 30717991946-BA"
    ]
    default_concept_col = "Descripción"
    default_debito_col = "Monto"
    invertir_signo = True

st.write(f"Configurado para **{banco}** (columnas objetivo: **{default_concept_col}** / **{default_debito_col}**).")
st.write("Subí un Excel/CSV para analizar.")

# Parámetros de carga (por si el CSV no es estándar)
c1, c2 = st.columns(2)
with c1:
    csv_sep = st.selectbox("Separador CSV", [";", ",", "\\t"], index=0, help="Solo afecta si subís CSV")
with c2:
    csv_enc = st.selectbox("Encoding CSV", ["latin1", "utf-8", "cp1252"], index=0, help="Solo afecta si subís CSV")

# --- CARGA DE ARCHIVO ---
uploaded_file = st.file_uploader("Elegir archivo", type=["csv", "xlsx", "xls"])

if uploaded_file:
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            sep_map = {";": ";", ",": ",", "\\t": "\t"}
            df = pd.read_csv(uploaded_file, encoding=csv_enc, sep=sep_map[csv_sep])
            df = ensure_clean_columns(df)
        else:
            df = pd.read_excel(uploaded_file)
            df = ensure_clean_columns(df)

        if df.empty or df.columns.size == 0:
            st.error("El archivo está vacío o no tiene columnas reconocibles.")
            st.stop()

        st.success(f"Archivo cargado: {uploaded_file.name}")
        st.write("📑 Columnas detectadas:", list(df.columns))
        st.markdown("### 🧾 Vista preliminar (1ª fila)")
        st.dataframe(df.head(1))

        # --- DETECCIÓN/SELECCIÓN DE COLUMNAS ---
        # Intento detectar por alias si las default no están
        concept_aliases = ["concepto", "descripcion", "descripción", "detalle", "concept", "desc"]
        debit_aliases = ["debito", "débito", "debitos", "débitos", "monto", "importe", "importe debito", "importe débito"]

        col_concepto = default_concept_col if default_concept_col in df.columns else (guess_column(df, concept_aliases) or df.columns[0])
        col_debito   = default_debito_col   if default_debito_col   in df.columns else (guess_column(df, debit_aliases)   or df.columns[1])

        st.info(f"Usando columnas: **{col_concepto}** (concepto) / **{col_debito}** (importe). Podés cambiarlas si no coinciden.")
        c3, c4 = st.columns(2)
        with c3:
            col_concepto = st.selectbox("Columna de concepto", options=df.columns, index=list(df.columns).index(col_concepto))
        with c4:
            col_debito = st.selectbox("Columna de importe (débito)", options=df.columns, index=list(df.columns).index(col_debito))

        # --- PREPARACIÓN DE CAMPOS NORMALIZADOS ---
        df["_concepto_norm"] = df[col_concepto].apply(normalize_text)
        df["_importe_num"] = df[col_debito].apply(parse_amount)

        # Si el banco maneja débitos como negativos, invierto el signo de los negativos para comparabilidad
        if invertir_signo:
            df["_importe_num"] = df["_importe_num"].where(df["_importe_num"] >= 0, -df["_importe_num"])

        # Fecha (si existe)
        fecha_col = find_fecha_column(df)

        # --- CÁLCULO: IMPUESTOS / CONCEPTOS NORMALES ---
        conceptos_norm = [normalize_text(c) for c in CONCEPTOS_A_COMPARAR]
        total_impuestos = 0.0
        resumen_items = []

        for c_raw, c_norm in zip(CONCEPTOS_A_COMPARAR, conceptos_norm):
            mask = df["_concepto_norm"].str.startswith(c_norm, na=False)
            suma = df.loc[mask, "_importe_num"].sum(min_count=1)
            suma = 0.0 if pd.isna(suma) else float(suma)
            resumen_items.append((c_raw, suma))
            total_impuestos += suma

        summary = pd.DataFrame(resumen_items, columns=["Concepto", "Total Débito"])
        total_general = summary["Total Débito"].sum()
        summary = pd.concat([summary, pd.DataFrame([["TOTAL GENERAL", total_general]], columns=["Concepto", "Total Débito"])], ignore_index=True)

        # Guardamos numérico para gráfico antes de formatear
        summary["_Total_Num"] = pd.to_numeric(summary["Total Débito"], errors="coerce").fillna(0.0)
        summary["Total Débito"] = summary["Total Débito"].apply(formato_argentino)

        # --- CONCEPTOS ESPECIALES ---
        detalles_especiales_rows = []
        for grupo, keywords in CONCEPTOS_ESPECIALES.items():
            pattern = conceptos_regex(keywords)
            mask = df["_concepto_norm"].str.contains(pattern, na=False)
            sub = df.loc[mask, [fecha_col] if fecha_col else [] + [col_concepto, "_importe_num"]].copy()
            if not sub.empty:
                sub["Grupo"] = grupo
                sub.rename(columns={col_concepto: "Concepto"}, inplace=True)
                if fecha_col:
                    sub.rename(columns={fecha_col: "Fecha"}, inplace=True)
                else:
                    sub["Fecha"] = ""
                sub["Débito"] = sub["_importe_num"].apply(formato_argentino)
                detalles_especiales_rows.append(sub[["Fecha", "Concepto", "Débito", "Grupo"]])

        detalles_especiales = pd.concat(detalles_especiales_rows, ignore_index=True) if detalles_especiales_rows else pd.DataFrame(columns=["Fecha", "Concepto", "Débito", "Grupo"])

        # --- RENDER RESULTADOS ---
        st.markdown("### Resultados generales")
        st.write(f"**Suma total de impuestos (conceptos normales):** {formato_argentino(total_impuestos)}")

        st.markdown("### Resumen por concepto")
        st.dataframe(summary[["Concepto", "Total Débito"]])

        # Gráfico (sin la fila TOTAL GENERAL)
        base_chart = summary[summary["Concepto"] != "TOTAL GENERAL"].set_index("Concepto")["_Total_Num"]
        if len(base_chart) > 0:
            st.markdown("#### Visualización rápida")
            st.bar_chart(base_chart)

        if not detalles_especiales.empty:
            st.markdown("### Detalle de conceptos especiales")
            for grupo in CONCEPTOS_ESPECIALES.keys():
                grupo_df = detalles_especiales[detalles_especiales['Grupo'] == grupo]
                if not grupo_df.empty:
                    # Calcular subtotal desde el numérico: necesitamos volver a parsear brevemente
                    subtot = 0.0
                    # Convertimos los "Débito" formateados de vuelta a float para subtotal
                    subtot_vals = grupo_df["Débito"].apply(lambda x: float(str(x).replace('.', '').replace(',', '.')))
                    subtot = float(subtot_vals.sum())
                    with st.expander(f"📌 {grupo} ({len(grupo_df)} registros) - Total: {formato_argentino(subtot)}"):
                        st.dataframe(grupo_df[["Fecha", "Concepto", "Débito"]])
        else:
            st.info("No se encontraron registros de conceptos especiales.")

        # --- DESCARGA EXCEL ---
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            # Guardar versión numérica y luego formatear para Excel
            summary.to_excel(writer, index=False, sheet_name="Resumen")
            if not detalles_especiales.empty:
                detalles_especiales.to_excel(writer, index=False, sheet_name="Especiales")
        st.download_button(
            "⬇️ Descargar resultados (Excel)",
            data=buffer.getvalue(),
            file_name=f"analisis_{banco.replace(' ', '_').lower()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        st.error(f"Error procesando el archivo: {e}")

# --- VERSIÓN DEL SCRIPT ---
st.markdown("---")
st.markdown("🛠️ **Versión del script: v15**")
