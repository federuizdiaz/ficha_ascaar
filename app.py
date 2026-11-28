from datetime import date, datetime
from pathlib import Path
import re

import jinja2
import pandas as pd
import streamlit as st
from weasyprint import HTML

BASE_DIR = Path(__file__).parent
TEMPLATE_PATH = BASE_DIR / "ficha_ascaar.html"
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


def renderizar_pdf(datos: dict, ruta_pdf: Path) -> bytes:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(BASE_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    template = env.get_template(TEMPLATE_PATH.name)
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


with st.form("form_ascaar", clear_on_submit=False):
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

    generar = st.form_submit_button("Generar PDF ASCAAR")

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

        try:
            pdf_bytes = renderizar_pdf(datos, ruta_pdf)
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
            st.success(f"PDF generado: `{nombre_pdf}`")
            st.download_button(
                label="Descargar PDF",
                data=pdf_bytes,
                file_name=nombre_pdf,
                mime="application/pdf",
            )
        except Exception as exc:
            st.error(f"Error al generar el PDF: {exc}")