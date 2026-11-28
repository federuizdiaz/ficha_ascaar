from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import re

import jinja2
import pandas as pd
import streamlit as st
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from weasyprint import HTML

BASE_DIR = Path(__file__).parent
TEMPLATE_PATH = BASE_DIR / "ficha_ascaar.html"
TEMPLATE_FICHA_ANESTESIA = BASE_DIR / "ficha_anestesia.html"
OUTPUT_DIR = BASE_DIR / "pdfs_ascaar"
CSV_PATH = BASE_DIR / "historial_ascaar.csv"

OUTPUT_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="Ficha ASCAAR - Generador PDF", layout="centered")
st.title("Ficha ASCAAR — Carga y Generación de PDF")


def sanitizar_nombre(texto: str) -> str:
    limpio = re.sub(r"[^A-Za-z0-9_-]+", "_", texto.strip())
    return limpio.strip("_") or "sin_dato"


def partir_texto_dos_lineas(texto: str, limite_linea_1: int = 58) -> tuple[str, str]:
    texto_limpio = " ".join(texto.strip().split())
    if len(texto_limpio) <= limite_linea_1:
        return texto_limpio, ""
    corte = texto_limpio.rfind(" ", 0, limite_linea_1)
    if corte == -1:
        corte = limite_linea_1
    return texto_limpio[:corte].strip(), texto_limpio[corte:].strip()


def ajustar_fuente(texto: str, ancho_mm: float, max_pt: float = 8.0, min_pt: float = 5.5) -> tuple[float, bool]:
    """Devuelve (tamaño de letra en pt, si entra en el casillero).
    Achica la letra cuando el texto es largo, hasta un mínimo legible."""
    if not texto:
        return max_pt, True
    # Estimación conservadora: ~0.56 em por caracter en negrita (1 pt = 0.3528 mm)
    pt_que_entra = (ancho_mm - 2) / (len(texto) * 0.56 * 0.3528)
    return round(max(min_pt, min(max_pt, pt_que_entra)), 1), pt_que_entra >= min_pt


def escala_horas(hora_texto: str, columnas: int = 7, paso_min: int = 5) -> list[str] | None:
    """Arma las etiquetas de la fila "Hora" de la Ficha de Anestesia.

    La primera es la hora de inicio completa (ej. 14:30); las siguientes suman
    `paso_min` minutos cada una y muestran solo los minutos (35', 40'...), salvo
    cuando cambia la hora, que muestran la hora completa (15:00).
    Devuelve None si no se puede interpretar la hora."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:.hH]?\s*(\d{2})\s*(?:hs?|hrs?)?\s*", hora_texto or "", flags=re.IGNORECASE)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    t = datetime(2000, 1, 1, hh, mm)
    etiquetas = [f"{t.hour}:{t.minute:02d}"]
    anterior = t
    for _ in range(columnas - 1):
        actual = anterior + timedelta(minutes=paso_min)
        if actual.hour != anterior.hour:
            etiquetas.append(f"{actual.hour}:{actual.minute:02d}")
        else:
            etiquetas.append(f"{actual.minute}'")
        anterior = actual
    return etiquetas


# --- Documentación adicional: fotos y PDF que se unen en un solo archivo ---
DPI_PAGINA = 200        # nitidez de las fotos dentro del PDF
CALIDAD_JPEG = 80       # 1-95: más alto = más nítido, pero el PDF pesa más
MARGEN_PAGINA_MM = 8    # margen blanco alrededor de cada foto


def imagen_a_pdf(datos: bytes) -> bytes:
    """Convierte una foto (JPG/PNG) en un PDF de una página A4.
    La página queda vertical u horizontal según la foto, que se endereza
    según la orientación del celular y se achica si hace falta (nunca se agranda)."""
    img = ImageOps.exif_transpose(Image.open(BytesIO(datos)))
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, "white")
        img.paste(rgba, mask=rgba.split()[-1])
    else:
        img = img.convert("RGB")

    ancho_pag = round(210 / 25.4 * DPI_PAGINA)
    alto_pag = round(297 / 25.4 * DPI_PAGINA)
    if img.width > img.height:
        ancho_pag, alto_pag = alto_pag, ancho_pag
    margen = round(MARGEN_PAGINA_MM / 25.4 * DPI_PAGINA)
    img.thumbnail((ancho_pag - 2 * margen, alto_pag - 2 * margen), Image.LANCZOS)

    pagina = Image.new("RGB", (ancho_pag, alto_pag), "white")
    pagina.paste(img, ((ancho_pag - img.width) // 2, (alto_pag - img.height) // 2))
    salida = BytesIO()
    pagina.save(salida, format="PDF", resolution=DPI_PAGINA, quality=CALIDAD_JPEG)
    return salida.getvalue()


def archivo_a_pdf(nombre: str, contenido: bytes) -> bytes:
    """Devuelve el contenido como PDF: los PDF pasan tal cual, las fotos se convierten."""
    try:
        if nombre.lower().endswith(".pdf"):
            return contenido
        return imagen_a_pdf(contenido)
    except Exception as exc:
        raise ValueError(f"No pude leer el archivo «{nombre}»: {exc}") from exc


def unir_pdfs(partes: list[tuple[str, bytes]]) -> tuple[bytes, list[tuple[str, int]]]:
    """Une los PDF en el orden recibido. Devuelve (PDF final, [(nombre, cantidad de páginas)])."""
    escritor = PdfWriter()
    resumen = []
    for nombre, contenido in partes:
        try:
            lector = PdfReader(BytesIO(contenido))
            if lector.is_encrypted and not lector.decrypt(""):
                raise ValueError("el PDF está protegido con contraseña")
            for pagina in lector.pages:
                escritor.add_page(pagina)
            resumen.append((nombre, len(lector.pages)))
        except Exception as exc:
            raise ValueError(f"No pude unir «{nombre}»: {exc}") from exc
    salida = BytesIO()
    escritor.write(salida)
    return salida.getvalue(), resumen


def limpiar_adjuntos() -> None:
    """Vacía el cuadro de archivos (cambiándole la clave) y borra el resultado anterior."""
    st.session_state["uploader_n"] += 1
    st.session_state.pop("resultado", None)


def renderizar_pdf(plantilla: Path, datos: dict, ruta_pdf: Path) -> bytes:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(BASE_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    template = env.get_template(plantilla.name)
    html_renderizado = template.render(**datos)
    pdf_bytes = HTML(string=html_renderizado, base_url=str(BASE_DIR)).write_pdf()
    ruta_pdf.write_bytes(pdf_bytes)
    return pdf_bytes


def guardar_en_csv(fila: dict) -> None:
    df_nueva = pd.DataFrame([fila])
    if CSV_PATH.exists():
        df_nueva.to_csv(CSV_PATH, mode="a", header=False, index=False, encoding="utf-8-sig")
    else:
        df_nueva.to_csv(CSV_PATH, mode="w", header=True, index=False, encoding="utf-8-sig")


# Valores iniciales de fecha y hora: se fijan UNA sola vez al abrir la página.
# Si el valor por defecto cambiara en cada recarga (como pasa con la hora actual),
# Streamlit lo tomaría por un campo nuevo y borraría lo que el usuario escribió.
if "fecha_inicial" not in st.session_state:
    st.session_state["fecha_inicial"] = date.today()
if "hora_inicial" not in st.session_state:
    st.session_state["hora_inicial"] = datetime.now().strftime("%H:%M")
if "uploader_n" not in st.session_state:
    st.session_state["uploader_n"] = 0

with st.form("form_ascaar", clear_on_submit=False):
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    with col_f1:
        fecha_atencion = st.date_input(
            "Fecha", value=st.session_state["fecha_inicial"], format="DD/MM/YYYY", key="campo_fecha"
        )
    with col_f2:
        hora_str = st.text_input("Hora", value=st.session_state["hora_inicial"], key="campo_hora")
    with col_f3:
        edad = st.text_input("Edad", placeholder="Ej: 42 años")

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        obra_social = st.text_input("Obra Social", placeholder="Ej: IOSCOR / OSDE")
    with col_c2:
        sanatorio = st.text_input("Sanatorio", placeholder="Ej: Sanatorio / Hospital")

    col_p1, col_p2 = st.columns([2, 1])
    with col_p1:
        nombre_afiliado = st.text_input("Nombre Afiliado *", placeholder="Apellido y Nombre")
    with col_p2:
        nro_afiliado = st.text_input("N° de Afiliado")

    cirujano = st.text_input("Cirujano")
    operaciones_realizadas = st.text_area("Operaciones Realizadas", height=80)
    codigos_anestesiologia = st.text_input("Códigos de Anestesiología", placeholder="Ej: 22.01.02")

    st.markdown("**Condiciones / Adicionales**")
    c_op1, c_op2 = st.columns(2)
    with c_op1:
        eval_preop = st.radio("Evaluación Preoperatorio", ["NO", "SI"], horizontal=True)
    with c_op2:
        urgencia = st.radio("Urgencia", ["NO", "SI"], horizontal=True)

    c_op3, c_op4, c_op5 = st.columns(3)
    with c_op3:
        nocturno = st.radio("Nocturno", ["NO", "SI"], horizontal=True)
    with c_op4:
        feriado = st.radio("Feriado", ["NO", "SI"], horizontal=True)
    with c_op5:
        fin_de_semana = st.radio("Fin de Semana", ["NO", "SI"], horizontal=True)

    nombre_anestesiologo = st.text_input("Nombre del Anestesiólogo")

    st.markdown("**Documentación adicional**")
    archivos = st.file_uploader(
        "Arrastrá acá el protocolo quirúrgico, la autorización y otros documentos "
        "(fotos o PDF). Se agregan después del Bono, en el orden en que los cargues.",
        type=["pdf", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"archivos_{st.session_state['uploader_n']}",
    )
    incluir_ficha = st.checkbox("Incluir la Ficha de Anestesia en el PDF completo", value=True)

    generar = st.form_submit_button("Generar documentación")

if generar:
    if not nombre_afiliado.strip():
        st.error("Completá al menos **Nombre Afiliado**.")
    else:
        op_1, op_2 = partir_texto_dos_lineas(operaciones_realizadas)
        datos = {
            "dia": fecha_atencion.strftime("%d"),
            "mes": fecha_atencion.strftime("%m"),
            "anio": fecha_atencion.strftime("%y"),
            "hora": hora_str.strip(),
            "obra_social": obra_social.strip(),
            "sanatorio": sanatorio.strip(),
            "nombre_afiliado": nombre_afiliado.strip(),
            "nro_afiliado": nro_afiliado.strip(),
            "edad": edad.strip(),
            "cirujano": cirujano.strip(),
            "operaciones_linea_1": op_1,
            "operaciones_linea_2": op_2,
            "codigos_anestesiologia": codigos_anestesiologia.strip(),
            "eval_preop": eval_preop,
            "urgencia": urgencia,
            "nocturno": nocturno,
            "feriado": feriado,
            "fin_de_semana": fin_de_semana,
            "nombre_anestesiologo": nombre_anestesiologo.strip(),
        }

        nombre_pdf = (
            f"{fecha_atencion.strftime('%Y-%m-%d')}_"
            f"ASCAAR_{sanitizar_nombre(nombre_afiliado)}.pdf"
        )
        ruta_pdf = OUTPUT_DIR / nombre_pdf

        # --- Ficha de Anestesia: mismos datos, otra plantilla ---
        nombre_ficha = (
            f"{fecha_atencion.strftime('%Y-%m-%d')}_"
            f"FICHA_ANESTESIA_{sanitizar_nombre(nombre_afiliado)}.pdf"
        )
        ruta_ficha = OUTPUT_DIR / nombre_ficha

        datos_ficha = {
            "sanatorio": sanatorio.strip(),
            "paciente": nombre_afiliado.strip(),
            "edad": edad.strip(),
            "fecha": fecha_atencion.strftime("%d/%m/%y"),
            "procedimiento": " ".join(operaciones_realizadas.split()),
            "cirujano": cirujano.strip(),
            "anestesiologo": nombre_anestesiologo.strip(),
        }
        # Escala de horas: arranca en la hora cargada en el formulario
        horas = escala_horas(hora_str)
        if horas is None:
            st.warning(
                "No pude interpretar la hora (usá el formato HH:MM, por ejemplo 14:30). "
                "La Ficha de Anestesia saldrá sin la escala de horas."
            )
            horas = [""] * 7
        for i, etiqueta in enumerate(horas):
            datos_ficha[f"hora_{i}"] = etiqueta

        # Ancho (mm) de cada casillero de la ficha, para achicar la letra si hace falta
        anchos_ficha = {
            "sanatorio": 77.8,
            "paciente": 55.9,
            "edad": 19.0,
            "fecha": 15.2,
            "procedimiento": 92.3,
            "cirujano": 57.0,
            "anestesiologo": 57.0,
        }
        for campo, ancho in anchos_ficha.items():
            pt, entra = ajustar_fuente(datos_ficha[campo], ancho)
            datos_ficha[f"fs_{campo}"] = pt
            if not entra:
                st.warning(
                    f"En la Ficha de Anestesia el texto de **{campo}** es muy largo "
                    "y puede quedar cortado. Conviene abreviarlo."
                )

        try:
            pdf_bytes = renderizar_pdf(TEMPLATE_PATH, datos, ruta_pdf)
            ficha_bytes = renderizar_pdf(TEMPLATE_FICHA_ANESTESIA, datos_ficha, ruta_ficha)

            # PDF completo: el Bono siempre primero; después la ficha (si se elige) y los archivos cargados
            partes = [("Bono ASCAAR", pdf_bytes)]
            if incluir_ficha:
                partes.append(("Ficha de Anestesia", ficha_bytes))
            for archivo in archivos or []:
                partes.append((archivo.name, archivo_a_pdf(archivo.name, archivo.getvalue())))
            completo_bytes, resumen = unir_pdfs(partes)
            nombre_completo = (
                f"{fecha_atencion.strftime('%Y-%m-%d')}_"
                f"DOCUMENTACION_COMPLETA_{sanitizar_nombre(nombre_afiliado)}.pdf"
            )
            (OUTPUT_DIR / nombre_completo).write_bytes(completo_bytes)

            registro = {
                "fecha": fecha_atencion.strftime("%d/%m/%Y"),
                "hora": hora_str.strip(),
                "obra_social": obra_social.strip(),
                "sanatorio": sanatorio.strip(),
                "nombre_afiliado": nombre_afiliado.strip(),
                "nro_afiliado": nro_afiliado.strip(),
                "edad": edad.strip(),
                "cirujano": cirujano.strip(),
                "operaciones_realizadas": operaciones_realizadas.strip(),
                "codigos_anestesiologia": codigos_anestesiologia.strip(),
                "eval_preop": eval_preop,
                "urgencia": urgencia,
                "nocturno": nocturno,
                "feriado": feriado,
                "fin_de_semana": fin_de_semana,
                "nombre_anestesiologo": nombre_anestesiologo.strip(),
                "archivo_pdf": nombre_pdf,
            }
            guardar_en_csv(registro)
            st.session_state["resultado"] = {
                "bono": (nombre_pdf, pdf_bytes),
                "ficha": (nombre_ficha, ficha_bytes),
                "completo": (nombre_completo, completo_bytes),
                "resumen": resumen,
            }
        except Exception as exc:
            st.session_state.pop("resultado", None)
            st.error(f"Error al generar el PDF: {exc}")

# Descargas (fuera del bloque anterior para que sigan disponibles tras cada clic)
if "resultado" in st.session_state:
    res = st.session_state["resultado"]
    st.success("Documentación generada.")
    st.markdown("**El PDF completo incluye, en este orden:**")
    for n, (nombre, paginas) in enumerate(res["resumen"], start=1):
        st.markdown(f"{n}. `{nombre}` — {paginas} pág.")
    st.caption(f"Tamaño del PDF completo: {len(res['completo'][1]) / 1_000_000:.1f} MB")
    st.download_button(
        label="Descargar documentación completa",
        data=res["completo"][1],
        file_name=res["completo"][0],
        mime="application/pdf",
        key="descarga_completo",
    )
    with st.expander("Descargar el Bono y la Ficha por separado"):
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            st.download_button(
                label="Descargar Bono ASCAAR",
                data=res["bono"][1],
                file_name=res["bono"][0],
                mime="application/pdf",
                key="descarga_bono",
            )
        with col_d2:
            st.download_button(
                label="Descargar Ficha de Anestesia",
                data=res["ficha"][1],
                file_name=res["ficha"][0],
                mime="application/pdf",
                key="descarga_ficha",
            )

st.divider()
st.button(
    "Quitar archivos cargados",
    on_click=limpiar_adjuntos,
    help="Usalo antes de cargar otro paciente, para no adjuntarle por error los archivos del anterior.",
)
