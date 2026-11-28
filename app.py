from datetime import date, datetime
import io
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import jinja2
import pandas as pd
import streamlit as st
from weasyprint import HTML
import xlrd
from xlutils.copy import copy as xl_copy

BASE_DIR = Path(__file__).parent
TEMPLATE_HTML_PATH = BASE_DIR / "ficha_ascaar.html"
XLS_BASE_PATH = BASE_DIR / "FICHA ANESTÉSICA CIMI Sedación VEDA.xls"
OUTPUT_DIR = BASE_DIR / "pdfs_generados"
CSV_PATH = BASE_DIR / "historial_ascaar.csv"

OUTPUT_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="Generador ASCAAR y CIMI", layout="centered")
st.title("Ficha ASCAAR + Ficha CIMI — Carga Única y Exportación PDF")


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


def generar_pdf_ascaar(datos: dict, ruta_pdf: Path) -> bytes:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(BASE_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    template = env.get_template(TEMPLATE_HTML_PATH.name)
    html_renderizado = template.render(**datos)
    pdf_bytes = HTML(string=html_renderizado, base_url=str(BASE_DIR)).write_pdf()
    ruta_pdf.write_bytes(pdf_bytes)
    return pdf_bytes


def encontrar_libreoffice() -> str:
    for comando in ("soffice", "libreoffice"):
        ruta = shutil.which(comando)
        if ruta:
            return ruta
    rutas_windows = [
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ]
    for ruta_win in rutas_windows:
        if ruta_win.exists():
            return str(ruta_win)
    raise FileNotFoundError(
        "No se encontró LibreOffice (soffice) para convertir la ficha CIMI a PDF."
    )


def generar_pdf_cimi(datos: dict, ruta_pdf: Path, ruta_xls: Path = XLS_BASE_PATH) -> bytes:
    if not ruta_xls.exists():
        raise FileNotFoundError(f"No se encontró la plantilla Excel: {ruta_xls.name}")

    rb = xlrd.open_workbook(ruta_xls, formatting_info=True)
    wb = xl_copy(rb)
    ws = wb.get_sheet("Hoja1")

    fecha_formateada = f"{datos['dia']}/{datos['mes']}/{datos['anio']}"
    operaciones_completas = " ".join(
        parte.strip()
        for parte in (datos.get("operaciones_linea_1", ""), datos.get("operaciones_linea_2", ""))
        if parte and parte.strip()
    )

    # Fila 3 (índice 2): PACIENTE (B3:K3), EDAD (L3:Q3) y FECHA (U3:AA3)
    ws.write(2, 1, datos["nombre_afiliado"])  # B3
    ws.write(2, 11, f"Edad: {datos['edad']}".strip())  # L3
    ws.write(2, 20, f"FECHA: {fecha_formateada}")  # U3

    # Fila 39 (índice 38): PROCEDIMIENTO en A39, operaciones en B39 (rango B39:V39)
    ws.write(38, 1, operaciones_completas)  # B39

    # Filas 41 y 42 (índices 40 y 41): CIRUJANO y ANESTESIOLOGO
    ws.write(40, 1, datos["cirujano"])  # B41
    ws.write(41, 1, datos["nombre_anestesiologo"])  # B42

    soffice_bin = encontrar_libreoffice()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        xls_temp = tmp_path / "ficha_cimi_temp.xls"
        wb.save(str(xls_temp))

        resultado = subprocess.run(
            [
                soffice_bin,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_path),
                str(xls_temp),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        pdf_generado = tmp_path / "ficha_cimi_temp.pdf"
        if resultado.returncode != 0 or not pdf_generado.exists():
            raise RuntimeError(
                f"Error convirtiendo CIMI a PDF: {resultado.stderr or resultado.stdout}"
            )

        pdf_bytes = pdf_generado.read_bytes()
        ruta_pdf.write_bytes(pdf_bytes)
        return pdf_bytes


def guardar_en_csv(fila: dict) -> None:
    df_nueva = pd.DataFrame([fila])
    if CSV_PATH.exists():
        df_nueva.to_csv(CSV_PATH, mode="a", header=False, index=False, encoding="utf-8-sig")
    else:
        df_nueva.to_csv(CSV_PATH, mode="w", header=True, index=False, encoding="utf-8-sig")


with st.form("form_unico", clear_on_submit=False):
    col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
    with col_f1:
        fecha_atencion = st.date_input("Fecha", value=date.today(), format="DD/MM/YYYY")
    with col_f2:
        hora_str = st.text_input("Hora", value=datetime.now().strftime("%H:%M"))
    with col_f3:
        edad = st.text_input("Edad", placeholder="Ej: 42 años")

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        obra_social = st.text_input("Obra Social", placeholder="Ej: IOSCOR / OSDE")
    with col_c2:
        sanatorio = st.text_input("Sanatorio", placeholder="Ej: CIMI")

    col_p1, col_p2 = st.columns([2, 1])
    with col_p1:
        nombre_afiliado = st.text_input("Nombre Afiliado *", placeholder="Apellido y Nombre")
    with col_p2:
        nro_afiliado = st.text_input("N° de Afiliado")

    cirujano = st.text_input("Cirujano")
    operaciones_realizadas = st.text_area("Operaciones Realizadas / Procedimiento", height=80)
    codigos_anestesiologia = st.text_input("Códigos de Anestesiología", placeholder="Ej: 22.01.02")

    st.markdown("**Condiciones / Adicionales (ASCAAR)**")
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

    generar = st.form_submit_button("Generar Ambos PDFs (ASCAAR + CIMI)")

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

        prefijo_fecha = fecha_atencion.strftime("%Y-%m-%d")
        paciente_limpio = sanitizar_nombre(nombre_afiliado)

        nombre_pdf_ascaar = f"{prefijo_fecha}_ASCAAR_{paciente_limpio}.pdf"
        nombre_pdf_cimi = f"{prefijo_fecha}_CIMI_{paciente_limpio}.pdf"

        ruta_pdf_ascaar = OUTPUT_DIR / nombre_pdf_ascaar
        ruta_pdf_cimi = OUTPUT_DIR / nombre_pdf_cimi

        try:
            pdf_ascaar_bytes = generar_pdf_ascaar(datos, ruta_pdf_ascaar)
            pdf_cimi_bytes = generar_pdf_cimi(datos, ruta_pdf_cimi)

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
                "pdf_ascaar": nombre_pdf_ascaar,
                "pdf_cimi": nombre_pdf_cimi,
            }
            guardar_en_csv(registro)

            st.success("Ambos PDFs generados correctamente.")
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                st.download_button(
                    label="Descargar PDF ASCAAR",
                    data=pdf_ascaar_bytes,
                    file_name=nombre_pdf_ascaar,
                    mime="application/pdf",
                )
            with col_d2:
                st.download_button(
                    label="Descargar PDF CIMI",
                    data=pdf_cimi_bytes,
                    file_name=nombre_pdf_cimi,
                    mime="application/pdf",
                )
        except Exception as exc:
            st.error(f"Error al generar los PDFs: {exc}")