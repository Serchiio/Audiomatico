# -*- coding: utf-8 -*-
"""
Icono de Audiomático: un parlante con ondas sobre un cuadrado azul.
Uso:  python icono.py     -> genera icono.ico (16 a 256 px)
"""
import os
from PIL import Image, ImageDraw

AZUL = (30, 100, 200, 255)
BLANCO = (255, 255, 255, 255)


def dibujar(tam=256):
    """Imagen RGBA del icono a 'tam' píxeles (se dibuja grande y se reduce: bordes suaves)."""
    s = 512
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=112, fill=AZUL)
    # parlante: caja + cono
    d.rectangle([118, 202, 200, 310], fill=BLANCO)
    d.polygon([(190, 202), (300, 120), (300, 392), (190, 310)], fill=BLANCO)
    # ondas de sonido
    for radio in (86, 140):
        d.arc([300 - radio, 256 - radio, 300 + radio, 256 + radio], -48, 48,
              fill=BLANCO, width=26)
    return img.resize((tam, tam), Image.LANCZOS)


if __name__ == "__main__":
    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icono.ico")
    dibujar(256).save(destino, format="ICO",
                      sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                             (128, 128), (256, 256)])
    print("Icono creado:", destino)
