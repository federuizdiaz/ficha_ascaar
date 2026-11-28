from pathlib import Path
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).parent
# Cambiá el nombre si tu archivo es .jpg
ORIGEN = BASE_DIR / "firma_original.png"
DESTINO = BASE_DIR / "firma_limpia.png"

img = Image.open(ORIGEN).convert("L")  # Escala de grises
img = ImageOps.autocontrast(img, cutoff=2)

# Convertimos píxeles claros (papel) en transparentes y oscurecemos la tinta
rgba = Image.new("RGBA", img.size)
datos_gris = img.getdata()
datos_rgba = []

UMBRAL = 165  # Si queda algo de sombra, bajalo a 145; si se corta trazo, subilo a 180
for v in datos_gris:
    if v > UMBRAL:
        datos_rgba.append((0, 0, 0, 0))  # Transparente
    else:
        # Tinta oscura con transparencia suave en bordes
        alpha = min(255, int((UMBRAL - v) * 2.5))
        datos_rgba.append((20, 20, 20, alpha))

rgba.putdata(datos_rgba)

# Recortar bordes transparentes sobrantes
bbox = rgba.getbbox()
if bbox:
    rgba = rgba.crop(bbox)

rgba.save(DESTINO, "PNG")
print(f"Firma limpia guardada en: {DESTINO}")