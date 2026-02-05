import streamlit as st
import pandas as pd
import re
import unicodedata
from io import BytesIO
import pdfplumber  # <-- NUEVO: para extraer tablas desde PDF

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
    "FEDERACION PATRO": [
        "federacion patro",
        "federación patro",
        "federacion patronal",
        "federación patronal",
        "seguro federacion patronal",
        "seguro federación patronal"
    ],
    # SANCOR: solo dos variantes (pedido del usuario)
    "SANCOR SEGUROS": [
        "sancor",
        "sancor coop.seg"
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
    s = " ".join(s.split())
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    return s

def formato_argentino(valor):
    try:
        return format(float(valor), ',.2f').replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return valor

def parse_amount(x):
    """Parsea importes en formatos variados y devuelve float o NaN."""
    if pd.isna(x):
        return float('nan')
    s = str(x)
    negative = False
    if '(' in s and ')' in s:
        negative = True
    s = re.sub(r'[^0-9,.\-]', '', s)
    if ',' in s and '.' in s:
        s = s.replace('.', '')
        s = s.replace(',', '.')
    elif ',' in s and '.' not in s:
        s = s.replace(',', '.')
    try:
        val = float(s)
        if negative and val > 0:
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
    # Limpia nombres de columnas tanto para CSV, Excel como PDF parseado
    df.columns = (df.columns
                  .astype(str)
                  .str.strip()
                  .str.replace(r'\s+', ' ', regex=True))
    # Limpia strings base
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].astype(str).str.strip()
    # Quita filas totalmente vacías (a veces aparecen desde PDFs)
    df = df.dropna(how='all')
    return df

def conceptos_regex(keywords):
    import re as _re
    kws = [_re.escape(normalize_text(k)) for k in keywords]
    return r'(' + '|'.join(kws) + r')'

# ==========================
# === PDF PARSER ===========
# ==========================

def parse_pdf_to_dataframe(uploaded_pdf, banco: str) -> pd.DataFrame:
    """
    Extrae tablas de un PDF usando pdfplumber.
    Devuelve un DataFrame concatenado y limpio.
    Notas:
      - Si hay múltiples tablas por página, las concatena.
      - Intenta usar la primera fila de cada tabla como encabezado si parece header.
      - Filtra columnas vacías/duplicadas.
    """
    tables = []
    with pdfplumber.open(uploaded_pdf) as pdf:
        for page in pdf.pages:
            # table_settings ayudan con líneas débiles o celdas sin bordes
            extracted = page.extract_tables(table_settings={
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "intersection_tolerance": 5,
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "edge_min_length": 10,
            })
            # Si no detecta líneas, probamos la heurística "text-based"
            if not extracted or len(extracted) == 0:
                extracted = page.extract_tables()  # fallback default

            for tbl in extracted or []:
                if not tbl or len(tbl) == 0:
                    continue
                # Heurística: si la primera fila parece encabezado (más “texto” que números), úsala como header
                header = tbl[0]
                body = tbl[1:] if len(tbl) > 1 else []
                # Si header tiene al menos 2 celdas no vacías considerables
                non_empty = sum([1 for c in header if (c and str(c).strip() != '')])
                if non_empty >= 2 and len(body) >= 1:
                    df_tbl = pd.DataFrame(body, columns=[str(c) if c is not None else f"col_{i}" for i, c in enumerate(header)])
                else:
                    df_tbl = pd.DataFrame(tbl)
                # Quitar columnas totalmente vacías
                if df_tbl.shape[1] > 0:
                    # Renombrar columnas duplicadas
                    df_tbl.columns = pd.io.parsers.ParserBase({'names': df_tbl.columns})._maybe_dedup_names(df_tbl.columns)
                    tables.append(df_tbl)

    if not tables:
        # Si no hubo tablas, devolvemos DF vacío (la app mostrará mensaje)
        return pd.DataFrame()

    df = pd.concat(tables, ignore_index=True)
    df = ensure_clean_columns(df)

    # Normalización leve de nombres conocidos por banco (heurística)
    # Muchos bancos usan columnas como: Fecha / Descripción / Débito / Crédito / Saldo / Importe / Monto, etc.
    # Acá no forzamos nada, pero podríamos homogeneizar si detectamos patrones claros.
    return df

# ==========================
# === STREAMLIT UI =========
# ==========================

st.set_page_config(page_title="Analizador Bancario", layout="wide")
st.title("📊 Analizador de Conceptos Bancarios (v17 con PDF)")

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

st.write(f"Configurado para **{banco}** (columnas objetivo por defecto: **{default_concept_col}** / **{default_debito_col}**).")
st.write("Subí un Excel/CSV/PDF para analizar.")

# Parámetros de carga CSV
c1, c2 = st.columns(2)
with c1:
    csv_sep = st.selectbox("Separador CSV", [";", ",", "\\t"], index=0, help="Solo afecta si subís CSV")
with c2:
    csv_enc = st.selectbox("Encoding CSV", ["latin1", "utf-8", "cp1252"], index=0, help="Solo afecta si subís CSV")

# --- CARGA DE ARCHIVO ---
uploaded_file = st.file_uploader("Elegir archivo", type=["csv", "xlsx", "xls", "pdf"])

if uploaded_file:
    try:
        file_name = uploaded_file.name.lower()

        # Lectura según extensión
        if file_name.endswith(".csv"):
            sep_map = {";": ";", ",": ",", "\\t": "\t"}
            df = pd.read_csv(
                uploaded_file,
                encoding=csv_enc,
                sep=sep_map[csv_sep],
                on_bad_lines='skip'
            )
            df = ensure_clean_columns(df)

        elif file_name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file)
            df = ensure_clean_columns(df)

        elif file_name.endswith(".pdf"):
            st.info("Procesando PDF… Esto puede tardar unos segundos según el tamaño del archivo.")
            df = parse_pdf_to_dataframe(uploaded_file, banco=banco)
            if df.empty or df.columns.size == 0:
                st.error("No se pudieron detectar tablas utilizables en el PDF. Si el PDF es escaneado (imagen), necesitaremos OCR (p. ej., Tesseract).")
                st.stop()
        else:
            st.error("Formato no soportado.")
            st.stop()

        if df.empty or df.columns.size == 0:
            st.error("El archivo está vacío o no tiene columnas reconocibles.")
            st.stop()

        st.success(f"Archivo cargado: {uploaded_file.name}")
        st.write("📑 Columnas detectadas:", list(df.columns))
        st.markdown("### 🧾 Vista preliminar (1ª fila)")
        st.dataframe(df.head(1))

        # --- DETECCIÓN/SELECCIÓN DE COLUMNAS ---
        concept_aliases = ["concepto", "descripcion", "descripción", "detalle", "concept", "desc"]
        debit_aliases = ["debito", "débito", "debitos", "débitos", "monto", "importe", "importe debito", "importe débito", "importe débito/credito", "importe débito/crédito"]

        col_concepto_guess = default_concept_col if default_concept_col in df.columns else (guess_column(df, concept_aliases) or df.columns[0])
        # para el importe, intentamos algunas columnas típicas que aparecen en PDF
        col_debito_guess   = default_debito_col   if default_debito_col   in df.columns else (guess_column(df, debit_aliases)   or df.columns[min(1, len(df.columns)-1)])

        st.info(f"Usando columnas: **{col_concepto_guess}** (concepto) / **{col_debito_guess}** (importe). Podés cambiarlas si no coinciden.")
        c3, c4 = st.columns(2)
        with c3:
            col_concepto = st.selectbox("Columna de concepto", options=df.columns, index=list(df.columns).index(col_concepto_guess))
        with c4:
            col_debito = st.selectbox("Columna de importe (débito)", options=df.columns, index=list(df.columns).index(col_debito_guess))

        # --- VALIDACIONES TEMPRANAS ---
        if col_concepto not in df.columns:
            st.error(f"La columna de concepto seleccionada (**{col_concepto}**) no existe en el archivo.")
            st.stop()
        if col_debito not in df.columns:
            st.error(f"La columna de importe seleccionada (**{col_debito}**) no existe en el archivo.")
            st.stop()

        # --- CAMPOS AUXILIARES (garantizados) ---
        df["_concepto_norm"] = df[col_concepto].apply(normalize_text)
        df["_importe_num"] = df[col_debito].apply(parse_amount)

        # Ajuste de signo para bancos que traen débitos negativos y querés verlos como positivos
        if invertir_signo:
            df["_importe_num"] = df["_importe_num"].where(df["_importe_num"] >= 0, -df["_importe_num"])

        # Fecha (si existe)
        fecha_col = find_fecha_column(df)

        # --- Filtro por fecha opcional ---
        if fecha_col:
            df["_fecha_parse"] = pd.to_datetime(df[fecha_col], errors="coerce", dayfirst=True, infer_datetime_format=True)
            min_f = pd.to_datetime(df["_fecha_parse"].min())
            max_f = pd.to_datetime(df["_fecha_parse"].max())
            if pd.notna(min_f) and pd.notna(max_f):
                st.markdown("#### Filtro por fecha")
                f1, f2 = st.columns(2)
                with f1:
                    desde = st.date_input("Desde", value=min_f.date())
                with f2:
                    hasta = st.date_input("Hasta", value=max_f.date())
                mask_fecha = (df["_fecha_parse"].dt.date >= desde) & (df["_fecha_parse"].dt.date <= hasta)
                df = df.loc[mask_fecha].copy()
            else:
                st.info("Columna de fecha detectada pero no se pudo parsear. Se omite el filtro por fecha.")

        if df["_importe_num"].notna().sum() == 0:
            st.warning("No se pudo interpretar ningún importe numérico. Revisá la columna de importes o el formato en el PDF.")

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

        summary["_Total_Num"] = pd.to_numeric(summary["Total Débito"], errors="coerce").fillna(0.0)
        summary["Total Débito"] = summary["Total Débito"].apply(formato_argentino)

        # --- CONCEPTOS ESPECIALES ---
        detalles_especiales_rows = []
        for grupo, keywords in CONCEPTOS_ESPECIALES.items():
            pattern = conceptos_regex(keywords)
            mask = df["_concepto_norm"].str.contains(pattern, na=False)

            cols_especiales = ([fecha_col] if fecha_col else []) + [col_concepto, "_importe_num"]
            cols_especiales = [c for c in cols_especiales if c in df.columns]
            sub = df.loc[mask, cols_especiales].copy()

            if not sub.empty:
                sub["Grupo"] = grupo
                sub.rename(columns={col_concepto: "Concepto"}, inplace=True)
                if fecha_col and fecha_col in sub.columns:
                    sub.rename(columns={fecha_col: "Fecha"}, inplace=True)
                else:
                    sub["Fecha"] = ""
                sub["Débito"] = sub["_importe_num"].apply(formato_argentino)
                detalles_especiales_rows.append(sub[["Fecha", "Concepto", "_importe_num", "Débito", "Grupo"]])

        if detalles_especiales_rows:
            detalles_especiales = pd.concat(detalles_especiales_rows, ignore_index=True)
        else:
            detalles_especiales = pd.DataFrame(columns=["Fecha", "Concepto", "_importe_num", "Débito", "Grupo"])

        # --- RENDER RESULTADOS ---
        st.markdown("### Resultados generales")
        st.write(f"**Suma total de impuestos (conceptos normales):** {formato_argentino(total_impuestos)}")

        st.markdown("### Resumen por concepto")
        st.dataframe(summary[["Concepto", "Total Débito"]])

        base_chart = summary[summary["Concepto"] != "TOTAL GENERAL"].set_index("Concepto")["_Total_Num"]
        if len(base_chart) > 0:
            st.markdown("#### Visualización rápida")
            st.bar_chart(base_chart)

        if not detalles_especiales.empty:
            st.markdown("### Detalle de conceptos especiales")
            for grupo in CONCEPTOS_ESPECIALES.keys():
                grupo_df = detalles_especiales[detalles_especiales['Grupo'] == grupo]
                if not grupo_df.empty:
                    subtot = float(pd.to_numeric(grupo_df["_importe_num"], errors="coerce").sum())
                    with st.expander(f"📌 {grupo} ({len(grupo_df)} registros) - Total: {formato_argentino(subtot)}"):
                        st.dataframe(grupo_df[["Fecha", "Concepto", "Débito"]])
        else:
            st.info("No se encontraron registros de conceptos especiales.")

        # --- DESCARGA EXCEL ---
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            summary.drop(columns=["_Total_Num"], errors="ignore").to_excel(writer, index=False, sheet_name="Resumen")
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
st.markdown("🛠️ **Versión del script: v17 (PDF habilitado con pdfplumber)**")
