import streamlit as st
import pandas as pd
import re
import unicodedata
from io import BytesIO
import pdfplumber  # PDF parser

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
    # SANCOR: solo dos variantes (según tu pedido)
    "SANCOR SEGUROS": [
        "sancor",
        "sancor coop.seg"
    ]
}

# ==========================
# === UTILIDADES ===========
# ==========================

def normalize_text(s: str) -> str:
    """Minus, sin acentos, espacios colapsados, strip."""
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
    """Busca una columna conteniendo alguno de los alias indicados."""
    cols_norm = {col: normalize_text(col) for col in df.columns}
    for alias in candidates:
        for col, cn in cols_norm.items():
            if alias in cn:
                return col
    return None

def ensure_clean_columns(df):
    # Limpia nombres y valores tipo texto
    df.columns = (pd.Index(df.columns)
                    .astype(str)
                    .str.strip()
                    .str.replace(r'\s+', ' ', regex=True))
    for col in df.select_dtypes(include=['object']).columns:
        df[col] = df[col].astype(str).str.strip()
    df = df.dropna(how='all')
    return df

def conceptos_regex(keywords):
    kws = [re.escape(normalize_text(k)) for k in keywords]
    return r'(' + '|'.join(kws) + r')'

def _dedup_columns(cols):
    """Dedup de nombres preservando orden: Fecha, Fecha -> Fecha, Fecha.1"""
    out, seen = [], {}
    for c in cols:
        name = str(c) if c is not None else ""
        if name not in seen:
            seen[name] = 0
            out.append(name)
        else:
            seen[name] += 1
            out.append(f"{name}.{seen[name]}")
    return out

# ==========================
# === PDF PARSER UNIVERSAL ==
# ==========================

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_NUM_RE  = re.compile(r"^[\(\)\-\s\$]*\d{1,3}(\.\d{3})*(,\d+)?$|^[\(\)\-\s\$]*\d+(\.\d+)?$")

HEADER_ALIASES = {
    "fecha": ["fecha", "date", "fec"],
    "concepto": ["concepto", "descripcion", "descripción", "detalle", "concept", "desc"],
    "nro": ["nro", "nro.cpbte", "nro cpbte", "comprobante", "nro.comprobante", "nº", "numero", "número", "doc", "ref"],
    "debito": ["debito", "débito", "debe", "deb.", "déb."],
    "credito": ["credito", "crédito", "haber", "cred.", "créd."],
    "saldo": ["saldo", "balance", "disponible", "contable"],
    "cod": ["cod", "cód", "codigo", "código"]
}

STANDARD_NAMES = ["Fecha", "Concepto", "Nro.Cpbte.", "Débito", "Crédito", "Saldo", "Cód."]

def _best_name(colname_norm):
    """Mapea un encabezado crudo a un nombre estándar, si calza."""
    for std, aliases in HEADER_ALIASES.items():
        if any(a in colname_norm for a in aliases):
            if std == "nro": return "Nro.Cpbte."
            if std == "debito": return "Débito"
            if std == "credito": return "Crédito"
            if std == "cod": return "Cód."
            return std.capitalize()
    return None

def _map_headers(cols):
    """Intenta mapear columnas crudas a nombres estándar; conserva las que no mapean."""
    mapped = []
    used = set()
    for c in cols:
        name = str(c)
        m = _best_name(normalize_text(name))
        if m and m not in used:
            mapped.append(m)
            used.add(m)
        else:
            mapped.append(name)  # dejamos como está
    return mapped

def _extract_tables_all_strategies(pdf):
    """Devuelve lista de tablas crudas (listas de filas) probando varias estrategias."""
    tables = []
    STRATS = [
        dict(vertical_strategy="lines", horizontal_strategy="lines",
             intersection_tolerance=5, snap_tolerance=3, join_tolerance=3, edge_min_length=10),
        dict(vertical_strategy="lines", horizontal_strategy="text"),
        dict(vertical_strategy="text",  horizontal_strategy="text"),
    ]
    for page in pdf.pages:
        page_tables = []
        for ts in STRATS:
            try:
                t = page.extract_tables(table_settings=ts)
                if t:
                    page_tables.extend(t)
            except Exception:
                continue
        # fallback simple (sin settings)
        if not page_tables:
            try:
                t = page.extract_tables()
                if t:
                    page_tables.extend(t)
            except Exception:
                pass
        tables.extend(page_tables)
    return tables

def _tables_to_df(tables):
    """Convierte tablas crudas en un DF unificado, intentando detectar encabezados."""
    dfs = []
    for tbl in tables or []:
        if not tbl or len(tbl) == 0:
            continue
        # Heurística de encabezado: si la primera fila tiene >2 celdas no vacías y hay filas debajo
        header, body = tbl[0], tbl[1:] if len(tbl) > 1 else (tbl[0], [])
        non_empty = sum(1 for c in header if c and str(c).strip())
        # Si parece encabezado y hay cuerpo
        if non_empty >= 2 and len(body) >= 1:
            cols = [str(c) if c is not None else f"col_{i}" for i, c in enumerate(header)]
            df_tbl = pd.DataFrame(body, columns=cols)
        else:
            df_tbl = pd.DataFrame(tbl)
        if df_tbl.shape[1] == 0:
            continue
        df_tbl.columns = _dedup_columns(df_tbl.columns)
        # Intentar mapear encabezados a estándar
        df_tbl.columns = _map_headers(df_tbl.columns)
        dfs.append(df_tbl)
    if not dfs:
        return pd.DataFrame()
    # Unificamos por columnas (outer join-like, pero más simple: concatenamos y rellenamos faltantes)
    df = pd.concat(dfs, ignore_index=True, sort=False)
    return ensure_clean_columns(df)

def _words_to_records(pdf):
    """
    Reconstruye filas por texto:
    - detecta inicio de fila por fecha dd/mm/yyyy
    - toma los ÚLTIMOS 3 números como Débito, Crédito, Saldo
    - el resto intermedio es Concepto; se intenta tomar Nro.Cpbte. si hay un num. 'aislado' justo antes de importes
    """
    records = []

    def group_lines(words, y_tol=3.0):
        # agrupa por hilera usando 'top' aproximado
        lines = []
        words_sorted = sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"])))
        current_y = None
        current = []
        for w in words_sorted:
            y = float(w["top"])
            if current_y is None or abs(y - current_y) <= y_tol:
                current.append(w)
                current_y = y if current_y is None else (current_y + y) / 2.0
            else:
                lines.append(current)
                current = [w]
                current_y = y
        if current:
            lines.append(current)
        # devuelve lista de líneas: cada línea es lista de palabras ordenadas por x
        return [[ww["text"] for ww in sorted(line, key=lambda x: float(x["x0"]))] for line in lines]

    for page in pdf.pages:
        try:
            words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
        except Exception:
            words = []
        if not words:
            continue
        lines = group_lines(words)
        # stream de tokens: línea a línea
        tokens = []
        for ln in lines:
            # unimos por espacios, pero mantenemos tokens separados
            tokens.extend(ln + ["<LB>"])  # marcador de salto de línea

        # cortar registros por fecha
        i, L = 0, len(tokens)
        while i < L:
            if isinstance(tokens[i], str) and _DATE_RE.match(tokens[i]):
                chunk = [tokens[i]]
                i += 1
                while i < L and not (_DATE_RE.match(tokens[i]) if isinstance(tokens[i], str) else False):
                    chunk.append(tokens[i])
                    i += 1
                # parsear chunk -> registro
                rec = _parse_chunk_to_record(chunk)
                if rec:
                    records.append(rec)
            else:
                i += 1

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records, columns=["Fecha", "Concepto", "Nro.Cpbte.", "Débito", "Crédito", "Saldo", "Cód."])
    return ensure_clean_columns(df)

def _parse_chunk_to_record(chunk):
    """
    chunk: [ 'dd/mm/yyyy', ..., '<LB>' ... ]
    Heurística:
      - Fecha = primer token
      - Buscar desde el final 3 NUMÉRICOS -> (Débito, Crédito, Saldo) en ese orden (si no, intentar permutar)
      - Intentar Nro.Cpbte. como el numérico inmediatamente anterior a los importes (si luce 'id' corto)
      - Concepto = todo entre Fecha y ese Nro/primer importe
      - Cód. = vacío (suele no venir estable en texto plano)
    """
    # limpiar '<LB>'
    chunk = [t for t in chunk if t != "<LB>"]
    if not chunk or not _DATE_RE.match(chunk[0]):
        return None

    fecha = chunk[0]
    body = chunk[1:]

    # tomar los últimos 5 tokens como candidatos a importes/cpbte/cod
    # y expandir si hace falta
    # estrategia: escanear desde el final, recolectar NUM hasta obtener 3
    idx = len(body) - 1
    nums = []
    idxs = []
    while idx >= 0 and len(nums) < 3:
        t = body[idx]
        if _NUM_RE.match(t):
            nums.append(t)
            idxs.append(idx)
        idx -= 1
    if len(nums) < 2:
        return None  # no parece un movimiento completo

    # Los importes encontrados están al revés
    nums_rev = list(reversed(nums))
    idxs_rev = list(reversed(idxs))

    # Asignación conservadora:
    # - Si hay 3: Débito, Crédito, Saldo = nums_rev[0], nums_rev[1], nums_rev[2]
    # - Si hay 2: asumimos Crédito, Saldo (o Débito, Saldo); dejamos el faltante en "0"
    deb, cred, saldo = "0", "0", "0"
    if len(nums_rev) == 3:
        deb, cred, saldo = nums_rev[0], nums_rev[1], nums_rev[2]
        first_amount_ix = idxs_rev[0]
    else:
        # 2 números: damos prioridad a "Saldo" como el último
        saldo = nums_rev[-1]
        first_amount_ix = idxs_rev[0]
        # el restante lo consideramos Crédito
        cred = nums_rev[0]

    # Intento de Nro.Cpbte.: token inmediatamente antes del primer importe, si es un número "de id"
    nro = ""
    candidate_ix = first_amount_ix - 1
    if candidate_ix >= 0 and re.fullmatch(r"[A-Za-z0-9\-\.]{3,12}", body[candidate_ix]):
        nro = body[candidate_ix]
        concept_tokens = body[:candidate_ix]
    else:
        concept_tokens = body[:first_amount_ix]

    concepto = " ".join([t for t in concept_tokens if t])

    return [fecha, concepto, nro, deb, cred, saldo, ""]

def parse_pdf_to_dataframe(uploaded_pdf, banco: str) -> pd.DataFrame:
    """
    Parser PDF universal:
      1) intenta tablas con varias estrategias
      2) si falla o no hay columnas útiles, reconstruye por texto (words)
    """
    with pdfplumber.open(uploaded_pdf) as pdf:
        # 1) Tablas
        tables = _extract_tables_all_strategies(pdf)
        df_tables = _tables_to_df(tables)
        usable = (not df_tables.empty) and (df_tables.shape[0] > 0)

        # ¿tenemos algo parecido a columnas clave?
        has_any_core = any(_best_name(normalize_text(c)) in ["Fecha", "Concepto", "Débito", "Crédito", "Saldo", "Nro.Cpbte.", "Cód."]
                           for c in df_tables.columns) if usable else False

        if usable and has_any_core:
            return df_tables

        # 2) Reconstrucción por words
        df_words = _words_to_records(pdf)
        if not df_words.empty:
            return df_words

    # si nada funcionó:
    return pd.DataFrame()

# ==========================
# === STREAMLIT UI =========
# ==========================

st.set_page_config(page_title="Analizador Bancario (v18 PDF universal)", layout="wide")
st.title("📊 Analizador de Conceptos Bancarios (v18, PDF universal)")

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
            st.info("Procesando PDF… puede tardar unos segundos.")
            df = parse_pdf_to_dataframe(uploaded_file, banco=banco)
            if df.empty or df.columns.size == 0:
                st.error("No se pudieron detectar tablas o líneas útiles en el PDF. Si el PDF es escaneado (imagen), se requiere OCR.")
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
        debit_aliases   = ["debito", "débito", "debitos", "débitos", "monto", "importe", "importe debito", "importe débito", "debe"]
        # Defaults si existen; si no, adivinamos por alias
        col_concepto_guess = default_concept_col if default_concept_col in df.columns else (guess_column(df, concept_aliases) or df.columns[0])
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
            min_f, max_f = pd.to_datetime(df["_fecha_parse"].min()), pd.to_datetime(df["_fecha_parse"].max())
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
            st.warning("No se pudo interpretar ningún importe numérico. Revisá la columna de importes o el formato del archivo.")

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

        # --- RENDER RESULTADOS (sin gráfico) ---
        st.markdown("### Resultados generales")
        st.write(f"**Suma total de impuestos (conceptos normales):** {formato_argentino(total_impuestos)}")

        st.markdown("### Resumen por concepto")
        st.dataframe(summary[["Concepto", "Total Débito"]])

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
st.markdown("🛠️ **Versión del script: v18 (PDF universal, sin gráfico)**")
