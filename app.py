import streamlit as st
import pandas as pd
import re
import unicodedata
from io import BytesIO
import pdfplumber  # Parser PDF (texto seleccionable)

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
    # SANCOR: solo dos variantes (pedido tuyo)
    "SANCOR SEGUROS": [
        "sancor",
        "sancor coop.seg"
    ]
}

# ==========================
# === UTILIDADES ===========
# ==========================

def normalize_text(s: str) -> str:
    """Minúsculas, sin acentos, espacios colapsados, strip."""
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
    """Parsea importes en formatos variados y devuelve float o NaN (robusto AR/US, paréntesis)."""
    if pd.isna(x):
        return float('nan')
    s = str(x)
    negative = False
    if '(' in s and ')' in s:
        negative = True
    s = re.sub(r'[^0-9,.\-]', '', s)  # quita símbolos no numéricos (excepto . , -)
    if ',' in s and '.' in s:
        s = s.replace('.', '')   # quita miles
        s = s.replace(',', '.')  # usa punto decimal
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

# ==========================
# === PDF PARSER (v19) =====
# ==========================

# Patrones generales
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
NUM_RE  = re.compile(r"^[\(\)\-\s\$]*\d{1,3}(\.\d{3})*(,\d+)?$|^[\(\)\-\s\$]*\d+(\.\d+)?$")

STANDARD_NAMES = ["Fecha", "Concepto", "Nro.Cpbte.", "Débito", "Crédito", "Saldo", "Cód."]

# Algunas palabras que suelen aparecer en conceptos (para validar que NO es encabezado)
CONCEPTO_KEYWORDS = [
    "transf", "inmediata", "ctas", "dist", "titular", "interbanking", "impuesto",
    "credito", "crédito", "debito", "débito", "comision", "comisión", "pago",
    "servicio", "suscripcion", "suscripción", "debin", "deposito", "depósito",
    "transfer", "cuentas", "obligaciones", "siro", "haberes", "consorcio", "arca",
    "vep", "iva", "ali", "ley", "periodico", "periódico", "accion", "acción",
]

def _is_amount(tok: str) -> bool:
    return bool(NUM_RE.match(tok or ""))

def _is_alpha(tok: str) -> bool:
    if not tok: return False
    t = re.sub(r'[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', '', tok)
    return len(t) >= 2

def _looks_like_concept(tokens_after_date) -> bool:
    """Debe existir texto alfabético o keywords típicas (evita tomar encabezados)."""
    tail = " ".join([t for t in tokens_after_date if t and t != "<LB>"]).lower()
    has_alpha = any(_is_alpha(t) for t in tokens_after_date)
    has_kw = any(k in tail for k in CONCEPTO_KEYWORDS)
    return has_alpha or has_kw

def _group_lines(words, y_tol=3.0):
    """Agrupa palabras en líneas según coordenada 'top' (tolerancia)."""
    lines = []
    words_sorted = sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"])))
    current_y, current = None, []
    for w in words_sorted:
        y = float(w["top"])
        if current_y is None or abs(y - current_y) <= y_tol:
            current.append(w)
            current_y = y if current_y is None else (current_y + y) / 2.0
        else:
            lines.append(current)
            current = [w]
            current_y = y
    if current: lines.append(current)
    # Cada línea como lista de strings, ordenada por x
    return [[ww["text"] for ww in sorted(line, key=lambda x: float(x["x0"]))] for line in lines]

def _find_first_movement_anchor(pages_lines):
    """
    Encuentra el índice (page_idx, line_idx) de la primera línea que parezca
    un movimiento real: inicia con dd/mm/yyyy y luego hay concepto 'real'.
    """
    for p_idx, lines in enumerate(pages_lines):
        for l_idx, line in enumerate(lines):
            if not line: 
                continue
            # Buscar una fecha al inicio (o muy al principio de la línea)
            for i, tok in enumerate(line[:3]):  # toleramos fecha en primeras 3 posiciones
                if DATE_RE.match(tok):
                    # El resto de la línea (y eventualmente la próxima) debe parecer concepto
                    tail = line[i+1:]
                    next_tail = lines[l_idx+1] if (l_idx + 1) < len(lines) else []
                    if _looks_like_concept(tail or next_tail):
                        return (p_idx, l_idx, i)  # (página, línea, pos_fecha_en_linea)
    return None

def _lines_from_anchor(pages_lines, anchor):
    """Devuelve todas las líneas desde el ancla (incluida), como lista única."""
    p0, l0, _ = anchor
    out = []
    for p_idx in range(p0, len(pages_lines)):
        lines = pages_lines[p_idx]
        start = l0 if p_idx == p0 else 0
        out.extend(lines[start:])
    return out

def _parse_lines_to_records(lines):
    """
    Convierte líneas (desde la primera fecha válida) a registros.
    Regla: cada movimiento se abre con una fecha dd/mm/yyyy. 
    Para cada bloque:
      - Fecha = primer token fecha
      - Tomamos los últimos 2 o 3 números del bloque como importes: 
        * si hay 3 => Débito, Crédito, Saldo
        * si hay 2 => Crédito, Saldo (Débito=0)
      - Intentamos Nro.Cpbte. como el token inmediatamente anterior a los importes
        si luce id corto [A-Za-z0-9-.]{3,12}
      - Concepto = texto remanente entre Fecha y Nro/Primer Importe
      - Cód. = vacío (suele no estar estable en texto plano)
    """
    records = []
    tokens = []
    for ln in lines:
        tokens.extend(ln + ["<LB>"])

    i, L = 0, len(tokens)
    while i < L:
        tok = tokens[i]
        if isinstance(tok, str) and DATE_RE.match(tok):
            chunk = [tok]
            i += 1
            while i < L and not (isinstance(tokens[i], str) and DATE_RE.match(tokens[i])):
                chunk.append(tokens[i]); i += 1
            rec = _chunk_to_record(chunk)
            if rec:
                records.append(rec)
        else:
            i += 1

    if not records:
        return pd.DataFrame(columns=STANDARD_NAMES)
    return pd.DataFrame(records, columns=STANDARD_NAMES)

def _chunk_to_record(chunk):
    # Limpia saltos
    chunk = [t for t in chunk if t != "<LB>"]
    if not chunk or not DATE_RE.match(chunk[0]): 
        return None

    fecha, body = chunk[0], chunk[1:]

    # Recolectar últimos 3 números (posibles importes)
    idx, nums, idxs = len(body) - 1, [], []
    while idx >= 0 and len(nums) < 3:
        t = body[idx]
        if _is_amount(str(t)):
            nums.append(str(t)); idxs.append(idx)
        idx -= 1
    if len(nums) < 2:
        # si no hay al menos 2 cifras, no lo consideramos movimiento completo
        return None

    nums_rev, idxs_rev = list(reversed(nums)), list(reversed(idxs))
    deb, cred, saldo = "0", "0", "0"
    if len(nums_rev) == 3:
        deb, cred, saldo = nums_rev[0], nums_rev[1], nums_rev[2]
        first_amount_ix = idxs_rev[0]
    else:
        # 2 números => Crédito, Saldo
        cred, saldo = nums_rev[0], nums_rev[1]
        first_amount_ix = idxs_rev[0]

    # Nro.Cpbte.: token inmediatamente antes del primer importe, si parece id corto
    nro = ""
    candidate_ix = first_amount_ix - 1
    if candidate_ix >= 0 and re.fullmatch(r"[A-Za-z0-9\-\.]{3,12}", str(body[candidate_ix])):
        nro = str(body[candidate_ix])
        concept_tokens = body[:candidate_ix]
    else:
        concept_tokens = body[:first_amount_ix]

    concepto = " ".join([t for t in concept_tokens if t])
    return [fecha, concepto, nro, deb, cred, saldo, ""]

def parse_pdf_to_dataframe(uploaded_pdf, banco: str) -> pd.DataFrame:
    """
    v19: 
      1) Extrae palabras por página
      2) Detecta la PRIMERA fecha de movimiento 'real' -> descarta encabezado
      3) Reconstruye filas (por texto) a partir de esa ancla
      4) Devuelve DF con columnas estándar y solo movimientos válidos
    """
    pages_lines = []
    with pdfplumber.open(uploaded_pdf) as pdf:
        for page in pdf.pages:
            try:
                words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            except Exception:
                words = []
            lines = _group_lines(words) if words else []
            pages_lines.append(lines)

    anchor = _find_first_movement_anchor(pages_lines)
    if not anchor:
        # Si no pudimos detectar la primera fila válida, devolvemos vacío
        return pd.DataFrame(columns=STANDARD_NAMES)

    work_lines = _lines_from_anchor(pages_lines, anchor)
    df = _parse_lines_to_records(work_lines)

    # Filtrado final: Fecha válida + al menos un importe con dígitos
    if df.empty:
        return df
    df = ensure_clean_columns(df)

    mask_fecha = df["Fecha"].astype(str).str.match(DATE_RE)
    any_importe = (
        df["Débito"].astype(str).str.contains(r"\d") |
        df["Crédito"].astype(str).str.contains(r"\d") |
        df["Saldo"].astype(str).str.contains(r"\d")
    )
    df = df[mask_fecha & any_importe].reset_index(drop=True)

    return df

# ==========================
# === STREAMLIT UI =========
# ==========================

st.set_page_config(page_title="Analizador Bancario (v19.1 PDF universal)", layout="wide")
st.title("📊 Analizador de Conceptos Bancarios (v19.1, PDF universal con recorte de encabezado)")

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

# --- Vista previa configurable ---
def show_preview(df: pd.DataFrame):
    st.markdown("### 🧾 Vista preliminar")
    n = st.selectbox(
        "Cantidad de filas a mostrar",
        options=[1, 5, 10, 15, 20],
        index=0,  # por defecto 1
        help="Mostramos la primera N filas del archivo ya parseado."
    )
    st.dataframe(df.head(n))

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
                st.error("No se detectaron movimientos útiles en el PDF. Si es escaneado (imagen), se requiere OCR.")
                st.stop()
        else:
            st.error("Formato no soportado.")
            st.stop()

        if df.empty or df.columns.size == 0:
            st.error("El archivo está vacío o no tiene columnas reconocibles.")
            st.stop()

        st.success(f"Archivo cargado: {uploaded_file.name}")
        st.write("📑 Columnas detectadas:", list(df.columns))

        # ⬇️ Nueva vista previa configurable (por defecto 1 fila)
        show_preview(df)

        # --- DETECCIÓN/SELECCIÓN DE COLUMNAS ---
        concept_aliases = ["concepto", "descripcion", "descripción", "detalle", "concept", "desc"]
        debit_aliases   = ["debito", "débito", "debitos", "débitos", "monto", "importe", "importe debito", "importe débito", "debe"]
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
st.markdown("🛠️ **Versión del script: v19.1**")
