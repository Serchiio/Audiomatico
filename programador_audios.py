# -*- coding: utf-8 -*-
"""
Programador de Audios
Python 3.8 (32 bits) - Windows 7 o superior

Dependencias (ver requirements.txt):
    pygame, pycaw, comtypes, pystray, Pillow
"""
import os
import sys
import shutil
import sqlite3
import time
import socket
import threading
import tempfile
import queue
import subprocess
import calendar
import datetime as dt
import math
import struct
import re
import ssl
import json
import hashlib
import urllib.request
import webbrowser
import wave
import array
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog, colorchooser

try:
    import winreg
except ImportError:
    winreg = None

import pygame

try:
    from pycaw.pycaw import AudioUtilities
except Exception:
    AudioUtilities = None

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None

APP = "ProgramadorAudios"   # nombre interno: carpeta de datos, registro y tarea (no cambiar)
NOMBRE = "Audiomático"      # nombre que ve el usuario
VERSION = "1.4.3"          # igual que en instalador.iss
# colores de categoría (se asignan solos, rotando) y fuente exclusiva de la prioridad
PALETA = ["#d9534f", "#f0ad4e", "#5cb85c", "#3ea6c4", "#6f7bd9", "#a463c9", "#e0679a",
          "#8d6e63"]
FUENTE_PRIO = ("Georgia", 9, "bold")
CARPETA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CARPETA_AUDIOS = os.path.join(CARPETA, "audios")
DB_PATH = os.path.join(CARPETA, "datos.db")
# vúmetro
VU_MIN, VU_MAX, VU_SEGS = -60.0, 0.0, 40
VU_FONDO = "#171a1f"
# decibelios: nivel al que se guardan las copias niveladas y rango del fader TOPE
NIVEL_BASE, TOPE_MAX, TOPE_MIN = -16.0, -16.0, -40.0
REGISTRO = os.path.join(CARPETA, "registro.txt")
PUERTO = 47653  # para evitar abrir dos copias del programa
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
EXTENSIONES = (".mp3", ".wav", ".ogg", ".flac")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ----------------------------------------------------------------------
# Base de datos
# ----------------------------------------------------------------------
class BD:
    def __init__(self):
        os.makedirs(CARPETA_AUDIOS, exist_ok=True)
        self.c = sqlite3.connect(DB_PATH)
        self.c.row_factory = sqlite3.Row
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS categorias(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS audios(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            ruta TEXT NOT NULL,
            categoria_id INTEGER,
            prioridad INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1,
            dias TEXT DEFAULT '0,1,2,3,4,5,6',
            horas TEXT DEFAULT '',
            minutos TEXT DEFAULT '',
            hora_desde INTEGER DEFAULT 0,
            hora_hasta INTEGER DEFAULT 23,
            fecha_inicio TEXT,
            fecha_fin TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS config(
            clave TEXT PRIMARY KEY, valor TEXT);
        """)
        # columnas de la nivelación de volumen (bases de datos antiguas)
        for col, tipo in (("ruta_norm", "TEXT DEFAULT ''"), ("nivel_db", "REAL"),
                          ("ganancia_db", "REAL"), ("objetivo_db", "REAL"),
                          ("intervalo", "INTEGER DEFAULT 0"),
                          ("int_desde", "TEXT DEFAULT ''"),
                          ("int_hasta", "TEXT DEFAULT ''"),
                          ("huella", "TEXT DEFAULT ''"),
                          ("duracion", "REAL"),
                          ("problema", "TEXT DEFAULT ''"),
                          ("atenuar", "INTEGER")):        # NULL = según la prioridad
            try:
                self.c.execute("ALTER TABLE audios ADD COLUMN %s %s" % (col, tipo))
            except sqlite3.OperationalError:
                pass
        try:
            self.c.execute("ALTER TABLE categorias ADD COLUMN color TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # categorías que aún no tienen color: se les asigna uno de la paleta
        for i, r in enumerate(self.c.execute(
                "SELECT id FROM categorias WHERE color IS NULL OR color=''").fetchall()):
            self.c.execute("UPDATE categorias SET color=? WHERE id=?",
                           (PALETA[(r[0] - 1) % len(PALETA)], r[0]))
        self.c.commit()

    def q(self, sql, p=()):
        return self.c.execute(sql, p).fetchall()

    def x(self, sql, p=()):
        cur = self.c.execute(sql, p)
        self.c.commit()
        return cur

    def cfg(self, clave, defecto=""):
        r = self.q("SELECT valor FROM config WHERE clave=?", (clave,))
        return r[0]["valor"] if r else defecto

    def set_cfg(self, clave, valor):
        self.x("INSERT OR REPLACE INTO config(clave,valor) VALUES(?,?)",
               (clave, str(valor)))


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def tinte(color, k=0.78):
    """Mezcla un color '#rrggbb' con blanco (k = cuánto blanco) para fondos suaves."""
    try:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        return "#ffffff"
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * k) for c in (r, g, b))


def recurso(nombre):
    """Ruta de un archivo incluido junto al programa (también dentro del .exe)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nombre)


REPO = "Serchiio/Audiomatico"          # donde se publican las versiones nuevas (GitHub Releases)


def version_tupla(txt):
    """'v1.2.3' -> (1, 2, 3) para comparar versiones."""
    n = [int(x) for x in re.findall(r"\d+", txt)[:3]]
    return tuple(n + [0] * (3 - len(n)))


def _ctx_ssl():
    """Conexión segura. Usa los certificados de 'certifi' si están: un Windows 7 sin
    actualizar puede no tener los certificados raíz que usa GitHub."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def _pedir(url, limite=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Audiomatico/" + VERSION, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=20, context=_ctx_ssl())


def buscar_actualizacion(version_actual=None):
    """None si ya está al día; si no, dict con version, instalador (url o None), sha256,
    notas y pagina. Lanza una excepción si no hay conexión."""
    version_actual = version_actual or VERSION
    with _pedir("https://api.github.com/repos/%s/releases/latest" % REPO) as r:
        datos = json.loads(r.read().decode("utf-8"))
    tag = datos.get("tag_name", "")
    if version_tupla(tag) <= version_tupla(version_actual):
        return None
    info = dict(version=tag.lstrip("vV"), instalador=None, sha256=None,
                notas=(datos.get("body") or "").strip(), pagina=datos.get("html_url", ""))
    prefijo = "https://github.com/%s/releases/download/" % REPO
    assets = datos.get("assets", [])
    # dos instaladores: «Instalar_Audiomatico_x.exe» (Windows 7/8, 32 bits, sin módulos nuevos; sirve
    # en cualquier Windows) y «Audiomatico_Win10-11_x.exe» (con duración/artista de otras apps).
    # Windows 10/11 prefiere el segundo; los demás SOLO reciben el primero.
    patrones = [r"(?i)^instalar_audiomatico.*\.exe$"]
    if version_windows()[0] >= 10:
        patrones.insert(0, r"(?i)^audiomatico_win10-11.*\.exe$")
    elegido = [a for pat in patrones for a in assets if re.match(pat, a["name"])]
    for a in elegido[:1]:
        if a["browser_download_url"].startswith(prefijo):
            info["instalador"] = a["browser_download_url"]
            sha = (a.get("digest") or "")
            if sha.startswith("sha256:"):
                info["sha256"] = sha[7:].lower()
            else:                                  # o un archivo "<instalador>.sha256"
                for b in assets:
                    if b["name"] == a["name"] + ".sha256" \
                            and b["browser_download_url"].startswith(prefijo):
                        with _pedir(b["browser_download_url"]) as r2:
                            m = re.search(r"\b[0-9a-fA-F]{64}\b", r2.read().decode("ascii", "ignore"))
                        info["sha256"] = m.group(0).lower() if m else None
            break
    return info


def descargar_verificado(url, destino, sha256, progreso=None):
    """Descarga 'url' en 'destino' y comprueba su SHA-256. Lanza ValueError si no coincide."""
    h = hashlib.sha256()
    with _pedir(url) as r, open(destino, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        hecho = 0
        for bloque in iter(lambda: r.read(65536), b""):
            f.write(bloque)
            h.update(bloque)
            hecho += len(bloque)
            if progreso:
                progreso(hecho, total)
    if h.hexdigest().lower() != sha256.lower():
        os.remove(destino)
        raise ValueError("el archivo descargado no coincide con su huella SHA-256")


FORMATO_12H = False      # False: 14:30   True: 2:30 PM (solo cambia lo que se ve; se guarda en 24 h)
_RE_HORA = re.compile(r"^\s*(\d{1,2})\s*[:.hH]\s*(\d{2})\s*(?:([aApP])\.?\s*[mM]\.?)?\s*$")


def parse_hora(txt):
    """'14:30', '2:30 pm', '2:30 p. m.' -> (14, 30). ValueError si no es una hora válida."""
    m = _RE_HORA.match(txt)
    if not m:
        raise ValueError(txt)
    h, mi, sufijo = int(m.group(1)), int(m.group(2)), m.group(3)
    if sufijo:
        if not 1 <= h <= 12:
            raise ValueError(txt)
        h = h % 12 + (12 if sufijo.lower() == "p" else 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(txt)
    return h, mi


def fmt_hora(hhmm):
    """'14:30' (como se guarda) -> lo que se muestra según el formato elegido."""
    if not hhmm:
        return ""
    h, m = parse_hora(hhmm)
    if not FORMATO_12H:
        return "%02d:%02d" % (h, m)
    return "%d:%02d %s" % (h % 12 or 12, m, "PM" if h >= 12 else "AM")


def fmt_h(h):
    """Hora entera (0-23) para mostrar: '7' o '7 AM'."""
    return "%d %s" % (h % 12 or 12, "PM" if h >= 12 else "AM") if FORMATO_12H else str(h)


def fmt_horas(lista_csv, sep=" "):
    """'10:30,13:00' -> '10:30 13:00' o '10:30 AM 1:00 PM' (según el formato)."""
    return sep.join(fmt_hora(x) for x in lista_csv.split(",") if x)


def parse_horas(txt):
    res = []
    for t in txt.replace(";", ",").split(","):
        t = t.strip()
        if not t:
            continue
        h, m = parse_hora(t)
        res.append("%02d:%02d" % (h, m))
    return sorted(set(res))


def huella_archivo(ruta):
    """SHA-1 del contenido de un archivo (para detectar audios repetidos)."""
    h = hashlib.sha1()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def parse_minutos(txt):
    res = []
    for t in txt.replace(";", ",").split(","):
        t = t.strip()
        if not t:
            continue
        m = int(t)
        if not (0 <= m <= 59):
            raise ValueError(t)
        res.append(m)
    return sorted(set(res))


# ----------------------------------------------------------------------
# Nivelación de volumen (decibelios) - solo módulos estándar de Python
# ----------------------------------------------------------------------
COMPUERTA_DB = -50.0     # los silencios por debajo de esto no cuentan al medir
# Decodificar un audio lo carga entero en RAM. En Python de 32 bits (Windows 7) un audio
# largo da MemoryError, así que por encima de este tamaño ya no se analiza ni se nivela
# (igual se reproduce, en streaming, sin gastar memoria).
MAX_DECODIFICADO = 90 * 1024 * 1024      # bytes de PCM (unos 8 min en estéreo a 44,1 kHz)


try:
    import audioop            # Python 3.8: rápido, en C
except ImportError:           # Python 3.13+: respaldo en Python puro (más lento)
    class audioop(object):
        @staticmethod
        def rms(b, w):
            a = array.array("h", b)
            return int(math.sqrt(sum(x * x for x in a) / len(a))) if len(a) else 0

        @staticmethod
        def max(b, w):
            a = array.array("h", b)
            return max(max(a), -min(a)) if len(a) else 0

        @staticmethod
        def mul(b, w, f):
            a = array.array("h", b)
            return array.array("h", [max(-32768, min(32767, int(x * f))) for x in a]).tobytes()


def _db(x):
    return 20.0 * math.log10(x / 32768.0) if x > 0 else -99.0


def medir_pcm(raw, canales, frecuencia):
    """(nivel medio en dBFS ignorando silencios, pico en dBFS) de PCM de 16 bits."""
    bloque = max(2, int(frecuencia * 0.1)) * canales * 2
    suma, n = 0.0, 0
    suma_todo, n_todo = 0.0, 0
    umbral = 32768.0 * 10 ** (COMPUERTA_DB / 20.0)
    for i in range(0, len(raw) - bloque + 1, bloque):
        r = audioop.rms(raw[i:i + bloque], 2)
        suma_todo += float(r) * r
        n_todo += 1
        if r >= umbral:
            suma += float(r) * r
            n += 1
    if n == 0:                      # todo por debajo del umbral: audio muy bajo, no es un error
        if suma_todo <= 0:
            return None, None       # silencio total (todo ceros)
        return _db(math.sqrt(suma_todo / n_todo)), _db(audioop.max(raw, 2))
    return _db(math.sqrt(suma / n)), _db(audioop.max(raw, 2))


try:
    import miniaudio          # decodificador de respaldo (MP3/OGG/FLAC/WAV) que no usa SDL
except Exception:
    miniaudio = None

NIVEL_ESTIMADO = -12.0        # dB que se supone a un audio que no se pudo medir (voces/anuncios)


def mp3_segundos(ruta):
    """Duración de un MP3 leyendo solo su cabecera (etiqueta Xing/Info o bitrate constante).
    None si no se puede."""
    try:
        with open(ruta, "rb") as f:
            d = f.read(8192)
        tam = os.path.getsize(ruta)
        pos = 0
        if d[:3] == b"ID3":
            pos = 10 + ((d[6] & 0x7F) << 21 | (d[7] & 0x7F) << 14 | (d[8] & 0x7F) << 7 | (d[9] & 0x7F))
            with open(ruta, "rb") as f:
                f.seek(pos)
                d = f.read(8192)
            base = pos
        else:
            base = 0
        for i in range(len(d) - 4):
            if d[i] == 0xFF and (d[i + 1] & 0xE0) == 0xE0:
                ver, capa = (d[i + 1] >> 3) & 3, (d[i + 1] >> 1) & 3
                br_i, sr_i = d[i + 2] >> 4, (d[i + 2] >> 2) & 3
                if ver == 1 or capa != 1 or br_i in (0, 15) or sr_i == 3:
                    continue                                  # solo capa III válida
                mpeg1 = ver == 3
                bitrate = ([32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320] if mpeg1
                           else [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])[br_i - 1] * 1000
                frec = ([44100, 48000, 32000] if ver == 3 else [22050, 24000, 16000] if ver == 2
                        else [11025, 12000, 8000])[sr_i]
                muestras = 1152 if mpeg1 else 576
                mono = (d[i + 3] >> 6) == 3
                lado = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
                for marca in (b"Xing", b"Info"):
                    j = d.find(marca, i, i + 4 + lado + 2 + 4)
                    if j >= 0 and d[j + 7] & 1:
                        frames = struct.unpack(">I", d[j + 8:j + 12])[0]
                        if frames:
                            return frames * muestras / float(frec)
                return (tam - base - i) * 8.0 / bitrate
    except Exception:
        pass
    return None


def decodificado_estimado(ruta):
    """Bytes de PCM que ocuparía el audio decodificado, sin decodificarlo."""
    tam = os.path.getsize(ruta)
    if ruta.lower().endswith(".wav"):
        try:
            with wave.open(ruta, "rb") as w:
                return int(w.getnframes() / float(w.getframerate()) * 176400)
        except Exception:
            return tam * 4
    if ruta.lower().endswith(".mp3"):
        seg = mp3_segundos(ruta)
        if seg:
            return int(seg * 176400)
    return tam * 10          # ogg/flac/otros: unas 10 veces más grande al decodificar


def duracion_estimada(ruta):
    """Segundos aproximados de un audio, sin decodificarlo (WAV exacto; mp3/ogg a ~128 kbps)."""
    try:
        if ruta.lower().endswith(".wav"):
            with wave.open(ruta, "rb") as w:
                return w.getnframes() / float(w.getframerate())
        if ruta.lower().endswith(".mp3"):
            seg = mp3_segundos(ruta)
            if seg:
                return seg
        return os.path.getsize(ruta) / 16000.0
    except Exception:
        return 0.0


def analizar_audio(ruta):
    """(duración, envolvente, nivel dB, motivo). Nunca lanza: si no se puede analizar
    devuelve (0, [], None, 'motivo') y el audio se reproduce igual, sin nivelar."""
    try:
        raw, frec, canales = cargar_pcm(ruta)
        env = envolvente(raw, canales, frec)
        nivel = medir_pcm(raw, canales, frec)[0]
        return len(raw) / float(frec * canales * 2), env, nivel, ""
    except Exception as e:                    # incluye MemoryError
        return 0.0, [], None, motivo_error(e)


def motivo_error(e):
    """Texto corto y útil de una excepción (MemoryError viene con el texto vacío)."""
    if isinstance(e, ValueError):
        return str(e)
    return "%s: %s" % (type(e).__name__, e)


def leer_wav_generico(ruta, frec_destino, canales_destino=2):
    """Respaldo para WAV que SDL no abre: PCM de 8/16/24/32 bits, float de 32/64 bits,
    A-law y mu-law, formato 'extensible', 1 o más canales y cualquier frecuencia.
    Devuelve PCM de 16 bits con la frecuencia y canales de la mezcla."""
    with open(ruta, "rb") as f:
        d = f.read()
    if d[:4] != b"RIFF" or d[8:12] != b"WAVE":
        raise ValueError("no es un archivo WAV válido")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(d):
        tid, tam = d[pos:pos + 4], struct.unpack("<I", d[pos + 4:pos + 8])[0]
        cuerpo = d[pos + 8:pos + 8 + tam]
        if tid == b"fmt ":
            fmt = cuerpo
        elif tid == b"data":
            pcm = cuerpo
        pos += 8 + tam + (tam & 1)
    if fmt is None or pcm is None:
        raise ValueError("el WAV está incompleto")
    tag, canales, frec, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:                    # WAVE_FORMAT_EXTENSIBLE
        tag = struct.unpack("<H", fmt[24:26])[0]
    ancho = max(1, bits // 8)
    pcm = pcm[:len(pcm) - len(pcm) % (ancho * canales)]
    if tag == 1:
        if bits == 8:
            raw = audioop.lin2lin(audioop.bias(pcm, 1, -128), 1, 2)
        elif bits in (16, 24, 32):
            raw = pcm if bits == 16 else audioop.lin2lin(pcm, ancho, 2)
        else:
            raise ValueError("WAV de %d bits no soportado" % bits)
    elif tag == 3 and bits in (32, 64):                     # coma flotante
        if len(pcm) > 120 * 1024 * 1024:
            raise ValueError("WAV de coma flotante demasiado grande")
        v = array.array("f" if bits == 32 else "d", pcm)
        raw = array.array("h", [max(-32768, min(32767, int(x * 32767))) for x in v]).tobytes()
    elif tag == 6:
        raw = audioop.alaw2lin(pcm, 2)
    elif tag == 7:
        raw = audioop.ulaw2lin(pcm, 2)
    else:
        raise ValueError("WAV con formato de compresión %d no soportado" % tag)
    if canales > 2:                                         # se queda con los dos primeros
        a = array.array("h", raw)
        st = array.array("h", [0]) * (len(a) // canales * 2)
        st[0::2], st[1::2] = a[0::canales], a[1::canales]
        raw, canales = st.tobytes(), 2
    if canales != canales_destino:
        raw = (audioop.tostereo(raw, 2, 1, 1) if canales_destino == 2
               else audioop.tomono(raw, 2, 0.5, 0.5))
    if frec != frec_destino:
        raw, _ = audioop.ratecv(raw, 2, canales_destino, frec, frec_destino, None)
    return raw


def cargar_pcm(ruta):
    """(PCM 16 bits, frecuencia, canales) de un audio, con respaldo si SDL no lo abre.
    Lanza ValueError con un motivo claro si no se puede leer."""
    init = pygame.mixer.get_init()
    if not init or init[1] != -16:
        raise ValueError("formato de mezcla no soportado")
    est = decodificado_estimado(ruta)          # ANTES de decodificar: Sound() carga todo en RAM
    if est > MAX_DECODIFICADO:
        raise ValueError("es muy largo (unos %d min)" % (est / 176400 // 60))
    try:
        snd = pygame.mixer.Sound(ruta)
        raw = snd.get_raw()
        del snd
        return raw, init[0], init[2]
    except Exception as e1:
        errores = [motivo_error(e1)]
        if miniaudio is not None:               # 2.º intento: decodificador propio (MP3 MPEG-2, etc.)
            try:
                with open(ruta, "rb") as f:
                    datos = f.read()
                d = miniaudio.decode(datos, output_format=miniaudio.SampleFormat.SIGNED16,
                                     nchannels=init[2], sample_rate=init[0])
                return d.samples.tobytes(), init[0], init[2]
            except Exception as e3:
                errores.append("miniaudio: " + motivo_error(e3))
        if ruta.lower().endswith(".wav"):       # 3.er intento: lector de WAV propio
            try:
                return leer_wav_generico(ruta, init[0], init[2]), init[0], init[2]
            except Exception as e2:
                errores.append(motivo_error(e2))
        raise ValueError("no se pudo leer el audio (%s). Conviértelo a MP3 o WAV estándar." %
                         " | ".join(errores))


def recortar_silencios(raw, canales, frec, margen=0.3, umbral_db=-60.0, minimo=1.0):
    """Quita el silencio sobrante al inicio y al final (deja 'margen' s de aire).
    Solo recorta si se ahorra al menos 'minimo' s. Devuelve (PCM, segundos quitados)."""
    paso = 0.05
    bloque = max(2, int(frec * paso)) * canales * 2
    n = len(raw) // bloque
    if n == 0:
        return raw, 0.0
    umbral = 32768.0 * 10 ** (umbral_db / 20.0)
    activos = [i for i in range(n) if audioop.rms(raw[i * bloque:(i + 1) * bloque], 2) >= umbral]
    if not activos:                                         # todo en silencio: no se toca
        return raw, 0.0
    m = int(round(margen / paso))
    ini, fin = max(0, activos[0] - m), min(n, activos[-1] + 1 + m)
    quitado = (ini + (n - fin)) * paso
    if quitado < minimo:
        return raw, 0.0
    cola = raw[n * bloque:] if fin == n else b""
    return raw[ini * bloque:fin * bloque] + cola, quitado


def analizar_sound(snd):
    """(duración, envolvente, nivel medio dB) de un Sound ya decodificado."""
    init = pygame.mixer.get_init()
    raw = snd.get_raw()
    env = envolvente(raw, init[2], init[0])
    nivel = medir_pcm(raw, init[2], init[0])[0]
    return snd.get_length(), env, nivel


PASO_ENV = 0.04          # segundos por muestra del vúmetro


def envolvente(raw, canales, frecuencia):
    """Nivel (dBFS) cada PASO_ENV segundos, para animar el vúmetro."""
    bloque = max(2, int(frecuencia * PASO_ENV)) * canales * 2
    return [_db(audioop.rms(raw[i:i + bloque], 2))
            for i in range(0, len(raw) - bloque + 1, bloque)]


def nivelar_archivo(ruta, destino, objetivo, techo, recortar=False):
    """
    Crea en 'destino' una copia WAV estándar (16 bits) con el volumen llevado al nivel
    'objetivo' (dBFS). Nunca deja picos por encima de 'techo' (no hay distorsión), así que
    un audio muy fuerte se baja y uno muy débil se sube hasta donde los picos permitan.
    Con recortar=True quita el silencio sobrante al inicio y al final.
    Devuelve (nivel_original, ganancia_db, duración_s, segundos_recortados).
    """
    raw, frec, canales = cargar_pcm(ruta)
    quitado = 0.0
    if recortar:
        raw, quitado = recortar_silencios(raw, canales, frec)
    nivel, pico = medir_pcm(raw, canales, frec)
    if nivel is None:
        raise ValueError("el archivo no tiene sonido (silencio total)")
    g = objetivo - nivel
    if pico + g > techo:          # tope: sin recortes
        g = techo - pico
    g = max(-30.0, min(30.0, g))
    if abs(g) < 0.3:
        g = 0.0     # igual se escribe la copia: deja todo en WAV estándar de 16 bits,
                    # que suena siempre (8/24 bits, mp3 raros, etc.)
    out = audioop.mul(raw, 2, 10 ** (g / 20.0))
    w = wave.open(destino, "wb")
    try:
        w.setnchannels(canales)
        w.setsampwidth(2)
        w.setframerate(frec)
        w.writeframes(out)
    finally:
        w.close()
    return nivel, g, len(out) / float(frec * canales * 2), quitado


def sumar_meses(fecha, n):
    m = fecha.month - 1 + n
    y = fecha.year + m // 12
    m = m % 12 + 1
    d = min(fecha.day, calendar.monthrange(y, m)[1])
    return dt.date(y, m, d)


def le_toca(a, ahora):
    """True si el audio debe sonar en este minuto."""
    if not a["activo"]:
        return False
    hoy = ahora.date().isoformat()
    if a["fecha_inicio"] and hoy < a["fecha_inicio"]:
        return False
    if a["fecha_fin"] and hoy > a["fecha_fin"]:
        return False
    dias = [int(d) for d in a["dias"].split(",") if d != ""]
    if ahora.weekday() not in dias:
        return False
    # Las tres reglas son independientes: basta con que se cumpla una.
    if ahora.strftime("%H:%M") in a["horas"].split(","):                  # hora exacta
        return True
    mins = [int(m) for m in a["minutos"].split(",") if m != ""]           # minuto de cada hora
    if ahora.minute in mins and a["hora_desde"] <= ahora.hour <= a["hora_hasta"]:
        return True
    n = a["intervalo"] or 0                                               # cada N minutos
    if n > 0 and a["int_desde"] and a["int_hasta"]:
        m_dia = ahora.hour * 60 + ahora.minute
        d, h = hhmm_a_min(a["int_desde"]), hhmm_a_min(a["int_hasta"])
        return d <= m_dia <= h and (m_dia - d) % n == 0
    return False


def proximas_emisiones(audios, desde, horizonte_min, limite):
    """[(instante, audio)] de las próximas emisiones, en orden: primero por hora y, a la
    misma hora, los de prioridad (así los pondría en cola el programa)."""
    # Rápido: las horas del día de cada audio se calculan una sola vez (no se prueba minuto
    # a minuto con todos los audios, que en un equipo lento congelaría la ventana).
    fin = desde + dt.timedelta(minutes=horizonte_min)
    res = []
    for a in audios:
        minutos = minutos_programados(a)
        if not minutos:
            continue
        dia = desde.replace(hour=0, minute=0)
        while dia <= fin:
            if dia_permitido(a, dia):
                for m in minutos:
                    t = dia + dt.timedelta(minutes=m)
                    if desde < t <= fin:
                        res.append((t, a))
            dia += dt.timedelta(days=1)
    res.sort(key=lambda x: (x[0], -x[1]["prioridad"]))
    return res[:limite]


def minutos_programados(a):
    """Conjunto de minutos del día (0-1439) en los que suena el audio (sin mirar el día)."""
    res = set()
    for h in a["horas"].split(","):
        if h:
            hh, mm = h.split(":")
            res.add(int(hh) * 60 + int(mm))
    mins = [int(m) for m in a["minutos"].split(",") if m != ""]
    for hora in range(a["hora_desde"], a["hora_hasta"] + 1):
        res.update(hora * 60 + m for m in mins)
    n = a["intervalo"] or 0
    if n > 0 and a["int_desde"] and a["int_hasta"]:
        res.update(range(hhmm_a_min(a["int_desde"]), hhmm_a_min(a["int_hasta"]) + 1, n))
    return res


def dia_permitido(a, fecha):
    """True si el audio puede sonar ese día (activo, vigente y día de la semana)."""
    if not a["activo"]:
        return False
    hoy = fecha.date().isoformat()
    if (a["fecha_inicio"] and hoy < a["fecha_inicio"]) or (a["fecha_fin"] and hoy > a["fecha_fin"]):
        return False
    return fecha.weekday() in [int(d) for d in a["dias"].split(",") if d != ""]


def hhmm_a_min(txt):
    """'09:30' o '9:30 AM' -> minutos desde las 00:00 (ValueError si el formato es inválido)."""
    h, m = parse_hora(txt)
    return h * 60 + m


def comando_inicio():
    """Comando que abre este programa en segundo plano."""
    if getattr(sys, "frozen", False):
        return '"%s" --minimizado' % sys.executable
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return '"%s" "%s" --minimizado' % (pyw, os.path.abspath(sys.argv[0]))


def autoinicio_activo():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def poner_autoinicio(activar):
    if winreg is None:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if activar:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, comando_inicio())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


def partes_inicio():
    """(programa, argumentos) que abren este programa en segundo plano."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--minimizado"
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    return pyw, '"%s" --minimizado' % os.path.abspath(sys.argv[0])


def xml_tarea(hhmm, programa, argumentos, hoy=None):
    """Definición de la tarea diaria. Con «schtasks /Create /SC DAILY» Windows la deja con
    «no iniciar con batería» y «detener a las 72 h» (mataría el programa); aquí no."""
    from xml.sax.saxutils import escape
    h, m = parse_hora(hhmm)
    hoy = hoy or dt.date.today()
    inicio = "%04d-%02d-%02dT%02d:%02d:00" % (hoy.year, hoy.month, hoy.day, h, m)
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            '  <Triggers><CalendarTrigger><StartBoundary>%s</StartBoundary><Enabled>true</Enabled>\n'
            '    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>\n'
            '  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>'
            '<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n'
            '  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
            '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
            '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
            '<StartWhenAvailable>true</StartWhenAvailable>'
            '<AllowHardTerminate>true</AllowHardTerminate><Enabled>true</Enabled>'
            '<ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>\n'
            '  <Actions Context="Author"><Exec><Command>%s</Command><Arguments>%s</Arguments></Exec></Actions>\n'
            '</Task>\n' % (inicio, escape(programa), escape(argumentos)))


def tarea_programada(hhmm, nombre=None):
    """Crea (o borra si hhmm es None) la tarea diaria que abre el programa."""
    nombre = nombre or APP
    flags = 0x08000000  # CREATE_NO_WINDOW
    ruta = None
    try:
        if hhmm is None:
            cmd = ["schtasks", "/Delete", "/TN", nombre, "/F"]
        else:
            programa, argumentos = partes_inicio()
            fd, ruta = tempfile.mkstemp(suffix=".xml")
            with os.fdopen(fd, "w", encoding="utf-16") as f:
                f.write(xml_tarea(hhmm, programa, argumentos))
            cmd = ["schtasks", "/Create", "/TN", nombre, "/XML", ruta, "/F"]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           errors="replace", creationflags=flags)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)
    finally:
        if ruta:
            try:
                os.remove(ruta)
            except OSError:
                pass


def instancia_unica(mostrar):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PUERTO))
        s.listen(1)
        return s
    except OSError:
        if mostrar:
            try:
                c = socket.create_connection(("127.0.0.1", PUERTO), 2)
                c.sendall(b"mostrar")
                c.close()
            except Exception:
                pass
        return None


# ----------------------------------------------------------------------
# Volumen de Audiomático en el Mezclador de Windows
# ----------------------------------------------------------------------
class VolumenWindows:
    """Volumen propio de Audiomático en el Mezclador de volumen de Windows.
    No toca el volumen general del equipo: solo el control de esta aplicación."""

    def __init__(self):
        self.vol = None
        self._buscar()

    def _buscar(self):
        """La sesión de audio de este programa aparece cuando abre la tarjeta de sonido."""
        self.vol = None
        if AudioUtilities is None:
            return
        try:
            pid = os.getpid()
            for s in AudioUtilities.GetAllSessions():
                if s.ProcessId == pid:
                    self.vol = s.SimpleAudioVolume
                    return
        except Exception:
            self.vol = None

    def disponible(self):
        if self.vol is None:
            self._buscar()
        return self.vol is not None

    def leer(self):
        """(porcentaje 0-100, decibelios) o None."""
        if not self.disponible():
            return None
        try:
            v = self.vol.GetMasterVolume()
            return v * 100.0, (20.0 * math.log10(v) if v > 0.001 else -99.0)
        except Exception:
            self.vol = None
            return None

    def poner(self, pct):
        if not self.disponible():
            return
        try:
            self.vol.SetMasterVolume(max(0.0, min(1.0, pct / 100.0)), None)
        except Exception:
            self.vol = None


# ----------------------------------------------------------------------
# Reproductor: dos voces (audio normal y audio con prioridad), damper con
# rampa, espera tras una prioridad y fundidos de entrada/salida
# ----------------------------------------------------------------------
class Reproductor:
    def __init__(self, log, factor):
        try:   # formato fijo (16 bits, estéreo): SDL convierte si la tarjeta es distinta
            pygame.mixer.init(44100, -16, 2, 2048, allowedchanges=0)
        except Exception:
            pygame.mixer.quit()
            pygame.mixer.init()
        pygame.mixer.set_num_channels(8)
        self.log = log
        # --- parámetros (se cambian desde Sistema) ---
        self.factor_duck = factor     # 0.25 = otros programas / audio en curso al 25 %
        self.rampa = 1.5              # s que tarda el damper en bajar y en subir
        self.espera = 30.0            # s de espera tras una prioridad antes del siguiente
        self.modo_pausa = False       # False: atenuar y mezclar | True: pausar y reanudar
        self.fundido = 0.5            # s de fundido al entrar y al salir cada audio
        self.tope = -20.0             # dB: nivel máximo (medio) de los audios
        self.techo = -1.0             # dB: tope de picos al nivelar
        self.usar_norm = True         # usar la copia nivelada si existe
        self.al_fallo = None          # función(audio, motivo): un audio no pudo sonar
        self.al_exito = None          # función(audio): un audio empezó a sonar bien
        # --- voz normal (música/streaming de pygame) ---
        self.actual = None
        self.pausado = False          # pausa pedida por el usuario
        self.pausa_por_prio = False   # pausada porque entró una prioridad (modo pausar)
        self.offset = 0.0
        self.duracion = 0.0
        self.env = []
        self.ganancia = 0.0
        self.nivel_play = None
        self.vol_music = 0.0
        # --- voz de prioridad (Sound en un canal) ---
        self.prio_actual = None
        self.prio_snd = None
        self.canal = None
        self.prio_pos = 0.0
        self.prio_dur = 0.0
        self.prio_env = []
        self.prio_gan = 0.0
        self.prio_nivel = None
        self.vol_prio = 0.0
        # --- colas ---
        self.cola = []                # audios normales esperando turno
        self.cola_prio = []           # audios con prioridad esperando turno
        self.espera_hasta = 0.0
        # --- damper ---
        self.duck = 0.0               # 0 = sin bajar ... 1 = bajado del todo
        self.duck_meta = 0.0
        self.duck_o = 0.0             # lo mismo, pero para las OTRAS apps de Windows
        self.forzar_atenuar_hasta = 0.0   # prueba manual desde la pestaña Apps
        self.persistir = None         # función(dict|None): guarda los volúmenes originales
        self.apps_excluir = set()     # nombres de proceso (minúsculas) que nunca se atenúan
        self.sesiones = []            # [(pid, nombre, SimpleAudioVolume, volumen_original)]
        self.sesiones_cap = False
        self.mult_otros = 1.0
        self.t_otros = 0.0
        self.t_captura = 0.0
        self._avisado_sin_apps = False

    # ---------- utilidades ----------
    def ruta_de(self, a):
        if self.usar_norm:
            try:
                rn = a["ruta_norm"]
                if rn and os.path.exists(rn):
                    return rn
            except (IndexError, KeyError):
                pass
        return a["ruta"]

    @staticmethod
    def _ganancia_de(a, ruta):
        try:
            return (a["ganancia_db"] or 0.0) if ruta != a["ruta"] else 0.0
        except (IndexError, KeyError):
            return 0.0

    def _analizar(self, a, ruta):
        """(duración, envolvente para el vúmetro, nivel medio en dB). Si no se puede
        analizar (audio largo, poca memoria...) devuelve valores vacíos y lo avisa."""
        dur, env, nivel, motivo = analizar_audio(ruta)
        if motivo:
            self.log("Aviso: '%s' suena sin nivelar el volumen (%s)." % (a["nombre"], motivo))
        return dur, env, nivel

    def _factor(self, nivel):
        """Volumen (0-1) que lleva el audio al tope elegido; nunca amplifica."""
        if nivel is None:             # no se pudo medir: se supone un nivel típico, no a todo volumen
            nivel = NIVEL_ESTIMADO
        return min(1.0, 10 ** ((self.tope - nivel) / 20.0))

    @staticmethod
    def _fundir(pos, dur, seg):
        e = 1.0
        if seg > 0:
            if pos < seg:
                e = pos / seg
            if dur > 0 and dur - pos < seg:
                e = min(e, (dur - pos) / seg)
        return max(0.0, min(1.0, e))

    @staticmethod
    def _lin_db(v):
        return 20.0 * math.log10(v) if v > 0.001 else -99.0

    def posicion(self):
        return self.offset + max(0, pygame.mixer.music.get_pos()) / 1000.0

    # ---------- voz normal ----------
    def _copia_estandar(self, a, ruta):
        """Convierte un audio que no abre a WAV estándar (en una caché) y devuelve su ruta."""
        try:
            carpeta = os.path.join(CARPETA_AUDIOS, "cache")
            os.makedirs(carpeta, exist_ok=True)
            dest = os.path.join(carpeta, "c_%s_%d.wav" % (a["id"], int(os.path.getmtime(ruta))))
            if not os.path.exists(dest):
                raw, frec, canales = cargar_pcm(ruta)
                w = wave.open(dest, "wb")
                try:
                    w.setnchannels(canales)
                    w.setsampwidth(2)
                    w.setframerate(frec)
                    w.writeframes(raw)
                finally:
                    w.close()
            return dest
        except Exception:
            return None

    def _tocar_normal(self, a, inicio=0.0):
        try:
            ruta = self.ruta_de(a)
            if not os.path.exists(ruta):
                raise FileNotFoundError("el archivo de audio ya no existe")
            self.duracion, self.env, self.nivel_play = self._analizar(a, ruta)
            self.ganancia = self._ganancia_de(a, ruta)
            try:
                pygame.mixer.music.load(ruta)
            except Exception as e1:                 # no abre tal cual: se prueba una copia estándar
                alt = self._copia_estandar(a, ruta)
                if alt is None:
                    raise
                self.log("Aviso: '%s' no se pudo abrir tal cual (%s); suena desde una copia "
                         "convertida." % (a["nombre"], motivo_error(e1)))
                pygame.mixer.music.load(alt)
            pygame.mixer.music.set_volume(0.0)      # el fundido lo sube
            if inicio > 0:
                try:
                    pygame.mixer.music.play(start=inicio)
                except Exception:
                    pygame.mixer.music.play()
                    inicio = 0.0
            else:
                pygame.mixer.music.play()
            self.offset = inicio
            self.pausado = False
            return True
        except Exception as e:
            motivo = motivo_error(e)
            self.log("ERROR al reproducir '%s': %s" % (a["nombre"], motivo))
            if self.al_fallo:
                self.al_fallo(a, motivo)
            return False

    def _iniciar_normal(self, a):
        if self._tocar_normal(a):
            self.actual = a
            self.log("Reproduciendo: %s" % a["nombre"])
            if self.al_exito:
                self.al_exito(a)
            return True
        return False

    def siguiente_normal(self):
        if (self.actual is not None or self.prio_actual is not None or self.cola_prio
                or time.time() < self.espera_hasta):
            return
        while self.cola:
            if self._iniciar_normal(self.cola.pop(0)):
                return

    # ---------- voz de prioridad ----------
    def _iniciar_prio(self, a):
        try:
            ruta = self.ruta_de(a)
            if not os.path.exists(ruta):
                raise FileNotFoundError("el archivo de audio ya no existe")
            if decodificado_estimado(ruta) > MAX_DECODIFICADO:
                raise ValueError("es demasiado largo para mezclarlo encima de otro audio")
            try:
                snd = pygame.mixer.Sound(ruta)
            except Exception:                       # SDL no lo abre: respaldo propio
                snd = pygame.mixer.Sound(buffer=cargar_pcm(ruta)[0])
            try:
                dur, env, nivel = analizar_sound(snd)
            except Exception as e:            # sin vúmetro ni tope, pero suena
                dur, env, nivel = snd.get_length(), [], None
                self.log("Aviso: '%s' suena sin nivelar el volumen (%s: %s)."
                         % (a["nombre"], type(e).__name__, e))
            snd.set_volume(0.0)
            canal = snd.play()
            if canal is None:
                canal = pygame.mixer.find_channel(True)
                canal.play(snd)
        except Exception as e:
            self.log("Aviso: '%s' se reproducirá como audio normal (%s: %s)."
                     % (a["nombre"], type(e).__name__, e))
            return False
        self.prio_actual, self.prio_snd, self.canal = a, snd, canal
        self.prio_pos, self.prio_dur, self.prio_env = 0.0, dur, env
        self.prio_nivel = nivel
        self.prio_gan = self._ganancia_de(a, ruta)
        self.duck_meta = 1.0
        if self.modo_pausa and self.actual is not None and not self.pausado:
            pygame.mixer.music.pause()
            self.pausa_por_prio = True
        self.log("Reproduciendo (PRIORIDAD): %s" % a["nombre"])
        if self.al_exito:
            self.al_exito(a)
        return True

    def _fin_prio(self):
        self.prio_actual = self.prio_snd = self.canal = None
        self.prio_env = []
        self.vol_prio = 0.0
        while self.cola_prio:
            sig = self.cola_prio.pop(0)
            if self._iniciar_prio(sig):
                return
            self._como_normal(sig)
        self.duck_meta = 0.0                         # el damper vuelve a subir
        if self.pausa_por_prio:
            self.pausa_por_prio = False
            if not self.pausado:
                pygame.mixer.music.unpause()
        if self.cola:
            self.espera_hasta = time.time() + self.espera
            if self.actual is None:
                self.log("Esperando %d s antes de: %s" % (self.espera, self.cola[0]["nombre"]))

    def _como_normal(self, a):
        """Un audio con prioridad que no se puede mezclar encima sigue su camino como normal."""
        if (self.actual is None and self.prio_actual is None and not self.cola
                and time.time() >= self.espera_hasta):
            self._iniciar_normal(a)
        else:
            self.cola.insert(0, a)

    # ---------- cola ----------
    def agregar(self, a):
        ids = [x["id"] for x in self.cola + self.cola_prio]
        for r in (self.actual, self.prio_actual):
            if r is not None:
                ids.append(r["id"])
        if a["id"] in ids:
            return  # ya está sonando o esperando
        if a["prioridad"]:
            if self.prio_actual is None:
                if not self._iniciar_prio(a):
                    self._como_normal(a)
            else:
                self.cola_prio.append(a)
                self.log("En cola (prioridad): %s" % a["nombre"])
        elif (self.actual is None and self.prio_actual is None and not self.cola
              and not self.cola_prio and time.time() >= self.espera_hasta):
            self._iniciar_normal(a)
        else:
            self.cola.append(a)
            self.log("En cola: %s" % a["nombre"])

    def detener_actual(self):
        """Salta lo que se ve como 'sonando ahora'."""
        if self.prio_actual is not None:
            try:
                self.canal.stop()
            except Exception:
                pass
            self._fin_prio()
        elif self.actual is not None:
            pygame.mixer.music.stop()
            self.actual = None
            self.pausado = False
            self.pausa_por_prio = False
            self.espera_hasta = 0.0
            self.siguiente_normal()

    def pausar_reanudar(self):
        if self.actual is None and self.prio_actual is None:
            return
        if self.pausado:
            if self.actual is not None and not self.pausa_por_prio:
                pygame.mixer.music.unpause()
            if self.canal is not None:
                self.canal.unpause()
            self.pausado = False
            self.log("Reanudado")
        else:
            if self.actual is not None:
                pygame.mixer.music.pause()
            if self.canal is not None:
                self.canal.pause()
            self.pausado = True
            self.log("En pausa")

    def vaciar_cola(self):
        self.cola.clear()
        self.cola_prio.clear()
        self.espera_hasta = 0.0

    def quitar_audio(self, aid):
        """Un audio se eliminó: sale de las colas y, si suena ahora, se detiene."""
        self.cola[:] = [x for x in self.cola if x["id"] != aid]
        self.cola_prio[:] = [x for x in self.cola_prio if x["id"] != aid]
        if self.prio_actual is not None and self.prio_actual["id"] == aid:
            self.detener_actual()
        if self.actual is not None and self.actual["id"] == aid:
            self.detener_actual()
        if self.actual is None:             # suelta el archivo para poder borrarlo (Windows lo bloquea)
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

    def mover_cola(self, i, j):
        """Mueve el elemento i de la cola a la posición j."""
        if 0 <= i < len(self.cola) and 0 <= j < len(self.cola) and i != j:
            self.cola.insert(j, self.cola.pop(i))

    def quitar_de_cola(self, i):
        if 0 <= i < len(self.cola):
            self.cola.pop(i)

    def adelantar(self, i):
        """Pone a sonar ya el audio i de la cola (salta lo que suene y la espera)."""
        if 0 <= i < len(self.cola):
            self.cola.insert(0, self.cola.pop(i))
            self.espera_hasta = 0.0
            if self.actual is not None:
                self.detener_actual()
            else:
                self.siguiente_normal()

    # ---------- consulta (para la pantalla) ----------
    def sonando(self):
        """(audio principal, segundos, duración, audio atenuado de fondo)."""
        if self.prio_actual is not None:
            return self.prio_actual, self.prio_pos, self.prio_dur, self.actual
        if self.actual is not None:
            return self.actual, self.posicion(), self.duracion, None
        return None, 0.0, 0.0, None

    def firma(self):
        a, _, _, fondo = self.sonando()
        return (a["id"] if a else None, fondo["id"] if fondo else None,
                tuple(x["id"] for x in self.cola_prio), tuple(x["id"] for x in self.cola))

    def cola_detalle(self):
        """[(texto, es_prioridad)] de lo que espera su turno."""
        return ([(a["nombre"], True, a["categoria_id"]) for a in self.cola_prio]
                + [(a["nombre"], False, a["categoria_id"]) for a in self.cola])

    def espera_restante(self):
        if (self.cola and self.actual is None and self.prio_actual is None):
            return max(0.0, self.espera_hasta - time.time())
        return 0.0

    def niveles(self):
        """(nivel de salida dB, nivel original sin nivelar dB) de todo lo que suena;
        None si no hay nada sonando."""
        if self.pausado:
            return None
        salida, original = [], []
        if self.actual is not None and self.env and not self.pausa_por_prio:
            i = int(self.posicion() / PASO_ENV)
            if 0 <= i < len(self.env):
                salida.append(self.env[i] + self._lin_db(self.vol_music))
                original.append(self.env[i] - self.ganancia)
        if self.prio_actual is not None and self.prio_env:
            i = int(self.prio_pos / PASO_ENV)
            if 0 <= i < len(self.prio_env):
                salida.append(self.prio_env[i] + self._lin_db(self.vol_prio))
                original.append(self.prio_env[i] - self.prio_gan)
        if not salida:
            return None

        def suma(v):
            return 10.0 * math.log10(sum(10 ** (x / 10.0) for x in v))
        return suma(salida), suma(original)

    # ---------- bucle de audio (cada ~40 ms) ----------
    def paso(self, dt):
        # 1) fin de los audios y arranque del siguiente
        if not self.pausado:
            if self.prio_actual is not None:
                self.prio_pos += dt
                if not self.canal.get_busy():
                    self._fin_prio()
            if (self.actual is not None and not self.pausa_por_prio
                    and not pygame.mixer.music.get_busy()):
                self.actual = None
                self.env = []
                # entre dos audios normales (por ejemplo, dos programados a la misma hora) también
                # se respeta la espera; los de prioridad no esperan (para eso son prioridad)
                if self.cola and self.espera > 0 and self.prio_actual is None:
                    self.espera_hasta = time.time() + self.espera
                    self.log("Esperando %d s antes de: %s" % (self.espera, self.cola[0]["nombre"]))
            if self.actual is None:
                self.siguiente_normal()
        # 2) damper con rampa
        if self.rampa <= 0:
            self.duck = self.duck_meta
        elif self.duck < self.duck_meta:
            self.duck = min(self.duck_meta, self.duck + dt / self.rampa)
        elif self.duck > self.duck_meta:
            self.duck = max(self.duck_meta, self.duck - dt / self.rampa)
        mult = 1.0 - (1.0 - self.factor_duck) * self.duck
        # 3) volúmenes: tope de decibelios x fundidos x damper
        if self.actual is not None:
            m = self._factor(self.nivel_play) * self._fundir(
                self.posicion(), self.duracion, self.fundido)
            if not self.modo_pausa:
                m *= mult
            self.vol_music = m
            pygame.mixer.music.set_volume(m)
        if self.prio_actual is not None:
            v = self._factor(self.prio_nivel) * self._fundir(
                self.prio_pos, self.prio_dur, self.fundido)
            self.vol_prio = v
            try:
                self.prio_snd.set_volume(v)
            except Exception:
                pass
        # 4) damper de las OTRAS apps de Windows: lo piden los audios que tienen activada la
        #    opción "bajar el volumen de otras apps" (por defecto, los de prioridad)
        quiere = ((not self.pausado) and any(
            self._quiere_atenuar(x) for x in (
                self.actual if not self.pausa_por_prio else None, self.prio_actual)
            if x is not None)) or time.time() < self.forzar_atenuar_hasta
        meta_o = 1.0 if quiere else 0.0
        if self.rampa <= 0:
            self.duck_o = meta_o
        elif self.duck_o < meta_o:
            self.duck_o = min(meta_o, self.duck_o + dt / self.rampa)
        elif self.duck_o > meta_o:
            self.duck_o = max(meta_o, self.duck_o - dt / self.rampa)
        self._volumen_otros(1.0 - (1.0 - self.factor_duck) * self.duck_o)

    @staticmethod
    def _quiere_atenuar(a):
        """¿Este audio baja el volumen de las otras apps mientras suena? Un audio con
        prioridad SIEMPRE lo hace (así se remarca); en los demás es una opción del audio."""
        if a["prioridad"]:
            return True
        try:
            v = a["atenuar"]
        except (IndexError, KeyError):
            v = None
        return bool(v)

    # ---------- volumen de otras apps (pycaw) ----------
    def _capturar_otros(self):
        """Añade a la lista las apps con audio que aún no están (también las que empiezan a
        sonar con el damper ya activo). Cada una recuerda su volumen original."""
        self.sesiones_cap = True
        if AudioUtilities is None:
            return 0
        nuevas = []
        try:
            conocidas = {pid for pid, _, _, _ in self.sesiones}
            for s in AudioUtilities.GetAllSessions():
                try:
                    if (s.Process is None or s.ProcessId == os.getpid() or s.ProcessId in conocidas
                            or es_sonido_sistema(s)):
                        continue
                    nombre = s.Process.name()
                    if nombre.lower() in self.apps_excluir:
                        continue
                    vol = s.SimpleAudioVolume
                    self.sesiones.append((s.ProcessId, nombre, vol, vol.GetMasterVolume()))
                    nuevas.append(nombre)
                except Exception:
                    continue                      # una app que se cerró justo ahora
        except Exception as e:
            self.log("No se pudo bajar el volumen de otras apps: %s" % motivo_error(e))
            return 0
        if nuevas:
            self.log("Bajando el volumen de otras apps (al %d %%): %s." % (
                round(self.factor_duck * 100), ", ".join(sorted(set(nuevas)))))
            if self.persistir:          # por si el programa se cierra de golpe: ver restaurar_pendientes
                orig = {}
                for _, n, _, o in self.sesiones:
                    orig.setdefault(n, o)
                self.persistir(orig)
        elif not self.sesiones and not self._avisado_sin_apps:
            self._avisado_sin_apps = True
            self.log("No se detectó ninguna otra app con audio para atenuar.")
        return len(nuevas)

    def _restaurar_otros_ya(self):
        for _, _, vol, orig in self.sesiones:
            try:
                vol.SetMasterVolume(orig, None)
            except Exception:
                pass
        if self.sesiones and self.persistir:
            self.persistir(None)
        self.sesiones = []
        self.sesiones_cap = False
        self.mult_otros = 1.0
        self._avisado_sin_apps = False

    def atenuadas(self):
        """Nombres de las apps que ahora mismo tienen el volumen bajado."""
        return sorted({n for _, n, _, _ in self.sesiones}) if self.duck_o > 0 else []

    def _volumen_otros(self, mult):
        if self.duck_o <= 0.0:
            if self.sesiones_cap:
                self._restaurar_otros_ya()
            return
        ahora = time.time()
        if not self.sesiones_cap or ahora - self.t_captura > 1.5:    # y las que empiecen después
            self.t_captura = ahora
            if self._capturar_otros():
                self.mult_otros = -1.0                              # fuerza aplicar a las nuevas
        if abs(mult - self.mult_otros) < 0.004 or ahora - self.t_otros < 0.08:
            return
        self.t_otros, self.mult_otros = ahora, mult
        for _, _, vol, orig in self.sesiones:
            try:
                vol.SetMasterVolume(max(0.0, orig * mult), None)
            except Exception:
                pass

    def cerrar(self):
        try:
            pygame.mixer.music.stop()
            pygame.mixer.stop()
            self._restaurar_otros_ya()
            pygame.mixer.quit()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Ventana para añadir / editar un audio
# ----------------------------------------------------------------------
class DialogoAudio(tk.Toplevel):
    VIGENCIAS = ["Indefinido", "1 mes", "3 meses", "6 meses", "1 año", "Fecha exacta"]
    MESES = {"1 mes": 1, "3 meses": 3, "6 meses": 6, "1 año": 12}

    def __init__(self, app, audio=None, archivo=None):
        super().__init__(app.root)
        self.app = app
        self.audio = audio
        self._guardando = False
        self.title("Editar audio" if audio else "Añadir audio")
        self.transient(app.root)
        self.resizable(False, False)
        self.grab_set()

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        r = 0

        ttk.Label(f, text="Nombre:").grid(row=r, column=0, sticky="e", pady=3)
        self.v_nombre = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_nombre, width=45).grid(
            row=r, column=1, columnspan=3, sticky="w")
        r += 1

        ttk.Label(f, text="Archivo:").grid(row=r, column=0, sticky="e", pady=3)
        self.v_ruta = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_ruta, width=45, state="readonly").grid(
            row=r, column=1, columnspan=2, sticky="w")
        ttk.Button(f, text="Examinar...", command=self.examinar).grid(row=r, column=3)
        r += 1

        ttk.Label(f, text="Categoría:").grid(row=r, column=0, sticky="e", pady=3)
        self.cats = self.app.db.q("SELECT * FROM categorias ORDER BY nombre")
        self.v_cat = tk.StringVar(value="(Sin categoría)")
        ttk.Combobox(f, textvariable=self.v_cat, state="readonly", width=42,
                     values=["(Sin categoría)"] + [c["nombre"] for c in self.cats]
                     ).grid(row=r, column=1, columnspan=3, sticky="w")
        r += 1

        self.v_prio = tk.IntVar()
        ttk.Checkbutton(f, text="PRIORIDAD (suena primero; si ya suena otro audio, lo atenúa "
                                "y se mezcla encima)", variable=self.v_prio
                        ).grid(row=r, column=1, columnspan=3, sticky="w", pady=3)
        r += 1
        self.v_atenuar = tk.IntVar(value=1)
        self.chk_atenuar = ttk.Checkbutton(
            f, text="Bajar el volumen de las otras apps (YouTube Music, Spotify, navegador...) "
                    "mientras suena", variable=self.v_atenuar)
        self.chk_atenuar.grid(row=r, column=1, columnspan=3, sticky="w")
        self.v_prio.trace_add("write", lambda *a: self._sinc_prioridad())
        r += 1
        self.v_activo = tk.IntVar(value=1)
        ttk.Checkbutton(f, text="Activo", variable=self.v_activo
                        ).grid(row=r, column=1, columnspan=3, sticky="w")
        r += 1

        ttk.Label(f, text="Días:").grid(row=r, column=0, sticky="e", pady=6)
        fd = ttk.Frame(f)
        fd.grid(row=r, column=1, columnspan=3, sticky="w")
        self.v_dias = []
        for i, d in enumerate(DIAS):
            v = tk.IntVar(value=1)
            self.v_dias.append(v)
            ttk.Checkbutton(fd, text=d, variable=v).pack(side="left")
        r += 1

        ttk.Label(f, text="Horas exactas:").grid(row=r, column=0, sticky="e", pady=3)
        self.v_horas = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_horas, width=30).grid(row=r, column=1, sticky="w")
        ttk.Label(f, text="Ej: " + ", ".join(fmt_hora(x) for x in ("08:00", "13:30", "17:45"))
                  + "  (acepta también AM/PM)").grid(
            row=r, column=2, columnspan=2, sticky="w")
        r += 1

        ttk.Label(f, text="Minutos de cada hora:").grid(row=r, column=0, sticky="e", pady=3)
        self.v_min = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_min, width=30).grid(row=r, column=1, sticky="w")
        ttk.Label(f, text="Ej: 0, 30  (suena en el min 0 y 30)").grid(
            row=r, column=2, columnspan=2, sticky="w")
        r += 1

        ttk.Label(f, text="Entre las horas:").grid(row=r, column=0, sticky="e", pady=3)
        fh = ttk.Frame(f)
        fh.grid(row=r, column=1, columnspan=3, sticky="w")
        self.v_desde = tk.StringVar(value="0")
        self.v_hasta = tk.StringVar(value="23")
        ttk.Spinbox(fh, from_=0, to=23, width=4, textvariable=self.v_desde).pack(side="left")
        ttk.Label(fh, text=" a ").pack(side="left")
        ttk.Spinbox(fh, from_=0, to=23, width=4, textvariable=self.v_hasta).pack(side="left")
        ttk.Label(fh, text="  (solo aplica a 'minutos de cada hora')").pack(side="left")
        r += 1

        ttk.Label(f, text="Repetir cada:").grid(row=r, column=0, sticky="e", pady=3)
        fi = ttk.Frame(f)
        fi.grid(row=r, column=1, columnspan=3, sticky="w")
        self.v_int = tk.StringVar(value="0")
        self.v_int_desde = tk.StringVar(value=fmt_hora("09:00"))
        self.v_int_hasta = tk.StringVar(value=fmt_hora("22:00"))
        ttk.Spinbox(fi, from_=0, to=720, width=4, textvariable=self.v_int).pack(side="left")
        ttk.Label(fi, text=" min, de ").pack(side="left")
        ttk.Entry(fi, textvariable=self.v_int_desde, width=9).pack(side="left")
        ttk.Label(fi, text=" a ").pack(side="left")
        ttk.Entry(fi, textvariable=self.v_int_hasta, width=9).pack(side="left")
        ttk.Label(fi, text="  (0 = no repetir; cuenta desde la hora inicial)").pack(side="left")
        r += 1
        ttk.Label(f, text="Las tres formas de programar son independientes: el audio suena "
                          "cuando se cumpla cualquiera de ellas.",
                  foreground="#666666").grid(row=r, column=1, columnspan=3, sticky="w")
        r += 1

        ttk.Label(f, text="Vigencia:").grid(row=r, column=0, sticky="e", pady=6)
        fv = ttk.Frame(f)
        fv.grid(row=r, column=1, columnspan=3, sticky="w")
        self.v_vig = tk.StringVar(value="Indefinido")
        ttk.Combobox(fv, textvariable=self.v_vig, values=self.VIGENCIAS,
                     state="readonly", width=14).pack(side="left")
        ttk.Label(fv, text="  Fecha (AAAA-MM-DD):").pack(side="left")
        self.v_fecha = tk.StringVar()
        ttk.Entry(fv, textvariable=self.v_fecha, width=12).pack(side="left")
        r += 1

        fb = ttk.Frame(f)
        fb.grid(row=r, column=0, columnspan=4, pady=(12, 0))
        ttk.Button(fb, text="Guardar", command=self.guardar).pack(side="left", padx=6)
        ttk.Button(fb, text="Cancelar", command=self.destroy).pack(side="left", padx=6)

        if audio:
            self.cargar(audio)
        elif archivo:                      # llegó arrastrado a la ventana
            self.v_ruta.set(archivo)
            self.v_nombre.set(os.path.splitext(os.path.basename(archivo))[0])

    def _sinc_prioridad(self):
        """Un audio con prioridad siempre baja el sonido general: la casilla queda marcada
        y bloqueada para que se vea que no es opcional."""
        if self.v_prio.get():
            self.v_atenuar.set(1)
            self.chk_atenuar.state(["disabled"])
        else:
            self.chk_atenuar.state(["!disabled"])

    def cargar(self, a):
        self.v_nombre.set(a["nombre"])
        self.v_ruta.set(a["ruta"])
        for c in self.cats:
            if c["id"] == a["categoria_id"]:
                self.v_cat.set(c["nombre"])
        self.v_prio.set(a["prioridad"])
        # audios antiguos (sin la opción guardada): la opción sigue a la prioridad, como antes
        self.v_atenuar.set(a["prioridad"] if a["atenuar"] is None else a["atenuar"])
        self._sinc_prioridad()
        self.v_activo.set(a["activo"])
        activos = [int(d) for d in a["dias"].split(",") if d != ""]
        for i, v in enumerate(self.v_dias):
            v.set(1 if i in activos else 0)
        self.v_horas.set(fmt_horas(a["horas"], ", "))
        self.v_min.set(a["minutos"].replace(",", ", "))
        self.v_desde.set(str(a["hora_desde"]))
        self.v_hasta.set(str(a["hora_hasta"]))
        self.v_int.set(str(a["intervalo"] or 0))
        if a["int_desde"]:
            self.v_int_desde.set(fmt_hora(a["int_desde"]))
        if a["int_hasta"]:
            self.v_int_hasta.set(fmt_hora(a["int_hasta"]))
        if a["fecha_fin"]:
            self.v_vig.set("Fecha exacta")
            self.v_fecha.set(a["fecha_fin"])

    def examinar(self):
        ruta = filedialog.askopenfilename(
            parent=self, title="Elegir audio",
            filetypes=[("Audio", "*.mp3 *.wav *.ogg *.flac"), ("Todos", "*.*")])
        if ruta:
            self.v_ruta.set(ruta)
            if not self.v_nombre.get():
                self.v_nombre.set(os.path.splitext(os.path.basename(ruta))[0])

    def guardar(self):
        nombre = self.v_nombre.get().strip()
        ruta = self.v_ruta.get()
        if not nombre or not ruta:
            messagebox.showwarning("Falta información", "Indica nombre y archivo.", parent=self)
            return
        if not ruta.lower().endswith(EXTENSIONES):
            messagebox.showwarning("Formato", "Usa archivos MP3, WAV, OGG o FLAC.", parent=self)
            return
        try:
            horas = parse_horas(self.v_horas.get())
            mins = parse_minutos(self.v_min.get())
            desde = int(self.v_desde.get())
            hasta = int(self.v_hasta.get())
            intervalo = int(self.v_int.get() or 0)
            if intervalo < 0:
                raise ValueError("intervalo")
            if intervalo:
                i_desde = "%02d:%02d" % divmod(hhmm_a_min(self.v_int_desde.get()), 60)
                i_hasta = "%02d:%02d" % divmod(hhmm_a_min(self.v_int_hasta.get()), 60)
                if i_hasta < i_desde:
                    raise ValueError("ventana")
            else:
                i_desde = i_hasta = ""
        except ValueError:
            messagebox.showwarning(
                "Formato", "Revisa las horas (HH:MM), los minutos (0-59) y la repetición "
                           "(minutos y ventana HH:MM, con la hora final posterior a la inicial).",
                parent=self)
            return
        if not horas and not mins and not intervalo:
            messagebox.showwarning("Falta información",
                                   "Indica al menos una hora exacta, minutos de cada hora "
                                   "o una repetición cada N minutos.", parent=self)
            return
        dias = [str(i) for i, v in enumerate(self.v_dias) if v.get()]
        if not dias:
            messagebox.showwarning("Falta información", "Elige al menos un día.", parent=self)
            return

        hoy = dt.date.today()
        vig = self.v_vig.get()
        if vig == "Indefinido":
            fin = ""
        elif vig == "Fecha exacta":
            try:
                fin = dt.date.fromisoformat(self.v_fecha.get().strip()).isoformat()
            except ValueError:
                messagebox.showwarning("Formato", "Fecha inválida. Usa AAAA-MM-DD.", parent=self)
                return
        else:
            fin = sumar_meses(hoy, self.MESES[vig]).isoformat()

        # evitar audios repetidos (mismo archivo o mismo nombre)
        huella = ""
        nuevo_o_cambiado = (not self.audio or ruta != self.audio["ruta"]
                            or nombre != self.audio["nombre"])
        if nuevo_o_cambiado:
            aviso, huella = self.app.buscar_duplicado(
                ruta, nombre, self.audio["id"] if self.audio else None)
            if aviso and not messagebox.askyesno(
                    "Posible audio repetido", aviso + "\n\n¿Añadirlo de todos modos?",
                    default="no", parent=self):
                return
        if self._guardando:         # doble clic en Guardar: no crear dos veces el mismo audio
            return
        self._guardando = True

        # copiar el audio a la carpeta del programa (por si lo mueven de lugar)
        if os.path.dirname(os.path.abspath(ruta)) != os.path.abspath(CARPETA_AUDIOS):
            destino = os.path.join(CARPETA_AUDIOS, "%d_%s" % (
                int(time.time() * 1000), os.path.basename(ruta)))
            try:
                shutil.copy2(ruta, destino)
            except Exception as e:
                self._guardando = False
                messagebox.showerror("Error", "No se pudo copiar el audio:\n%s" % e, parent=self)
                return
            ruta = destino

        cat_id = None
        for c in self.cats:
            if c["nombre"] == self.v_cat.get():
                cat_id = c["id"]

        datos = (nombre, ruta, cat_id, self.v_prio.get(), self.v_activo.get(),
                 ",".join(dias), ",".join(horas), ",".join(str(m) for m in mins),
                 desde, hasta, fin, intervalo, i_desde, i_hasta)
        db = self.app.db
        if self.audio:
            db.x("UPDATE audios SET nombre=?,ruta=?,categoria_id=?,prioridad=?,activo=?,"
                 "dias=?,horas=?,minutos=?,hora_desde=?,hora_hasta=?,fecha_fin=?,"
                 "intervalo=?,int_desde=?,int_hasta=? WHERE id=?",
                 datos + (self.audio["id"],))
            aid = self.audio["id"]
            cambio = ruta != self.audio["ruta"]
            if cambio:      # archivo nuevo: se olvidan los datos y se borran las copias del anterior
                db.x("UPDATE audios SET ruta_norm='',nivel_db=NULL,ganancia_db=NULL,"
                     "objetivo_db=NULL,duracion=NULL,problema='',huella='' WHERE id=?", (aid,))
                self.app.borrar_archivos_audio(self.audio, conservar=(ruta,))
        else:
            aid = db.x("INSERT INTO audios(nombre,ruta,categoria_id,prioridad,activo,dias,horas,"
                       "minutos,hora_desde,hora_hasta,fecha_fin,intervalo,int_desde,int_hasta,"
                       "fecha_inicio) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       datos + (hoy.isoformat(),)).lastrowid
            cambio = True
        if huella:
            db.x("UPDATE audios SET huella=? WHERE id=?", (huella, aid))
        db.x("UPDATE audios SET atenuar=? WHERE id=?", (int(self.v_atenuar.get()), aid))
        # Desde aquí el audio ya está guardado: pase lo que pase la ventana se cierra, así no
        # se queda abierta con el botón Guardar listo para crear el mismo audio otra vez.
        try:
            if cambio:
                self.config(cursor="watch")
                self.update_idletasks()
            self.app.nivelar_audio(aid, forzar=cambio)
            self.app.refrescar_audios()
            # avisos útiles al guardar: problemas con el archivo y choques de programación
            avisos = []
            r = db.q("SELECT problema FROM audios WHERE id=?", (aid,))
            if r and r[0]["problema"]:
                avisos.append("No se pudo analizar este audio: %s\nPuede que suene igual; "
                              "pruébalo con «Probar ahora»." % r[0]["problema"])
            conf = [txt for ids, txt in self.app.conflictos() if aid in ids]
            if conf:
                avisos.append("Conflictos de programación:\n" + "\n".join("• " + c for c in conf[:4])
                              + ("\n(y %d más; mira Sistema → Revisar conflictos)" % (len(conf) - 4)
                                 if len(conf) > 4 else ""))
            if avisos:
                self.config(cursor="")
                messagebox.showwarning("Revisa este audio", "\n\n".join(avisos), parent=self)
        except Exception as e:
            self.app.log("Aviso al guardar '%s': %s" % (nombre, motivo_error(e)))
        finally:
            self.destroy()


# ----------------------------------------------------------------------
# Arrastrar y soltar archivos desde el Explorador (API de Windows, sin librerías extra)
# ----------------------------------------------------------------------
class SoltarArchivos:
    WM_DROPFILES, WM_COPYGLOBALDATA, GWL_WNDPROC = 0x0233, 0x0049, -4

    def __init__(self, root, al_soltar):
        import ctypes
        from ctypes import wintypes
        self.ct, self.root, self.al_soltar = ctypes, root, al_soltar
        self.buzon = []
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        self.shell32 = shell32
        lresult = ctypes.c_ssize_t
        proc_t = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM)
        self.llamar = user32.CallWindowProcW
        self.llamar.restype = lresult
        self.llamar.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR,
                                           wintypes.UINT]
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        set_long.restype = ctypes.c_void_p
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

        self.hwnd = int(root.wm_frame(), 16)          # el marco de la ventana
        shell32.DragAcceptFiles(self.hwnd, True)
        filtro = getattr(user32, "ChangeWindowMessageFilterEx", None)
        if filtro:                                    # Windows 7+: aceptar aunque haya otros permisos
            for msg in (self.WM_DROPFILES, self.WM_COPYGLOBALDATA):
                filtro(self.hwnd, msg, 1, None)
        self._proc = proc_t(self._ventana)            # se guarda: si no, Python lo borra
        self.viejo = set_long(self.hwnd, self.GWL_WNDPROC,
                              ctypes.cast(self._proc, ctypes.c_void_p).value)

    def _ventana(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_DROPFILES:
            archivos = []
            try:
                n = self.shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                for i in range(n):
                    largo = self.shell32.DragQueryFileW(wparam, i, None, 0)
                    buf = self.ct.create_unicode_buffer(largo + 1)
                    self.shell32.DragQueryFileW(wparam, i, buf, largo + 1)
                    archivos.append(buf.value)
            except Exception:
                pass
            finally:
                self.shell32.DragFinish(wparam)
            if archivos:
                # NO se llama a Tkinter desde aquí (estamos dentro de su bucle de mensajes y
                # lo corrompería): solo se deja en el buzón y el bucle de la app lo recoge.
                self.buzon.append(archivos)
            return 0
        return self.llamar(self.viejo, hwnd, msg, wparam, lparam)

    def recoger(self):
        """Lista de lotes de archivos soltados desde la última vez."""
        lotes, self.buzon = self.buzon, []
        return lotes


# ----------------------------------------------------------------------
# Apps con audio: nombre, icono y título de ventana (como el Mezclador de Windows)
# ----------------------------------------------------------------------
try:
    import psutil
except Exception:
    psutil = None


def es_sonido_sistema(s):
    """True si una sesión de audio es la de 'Sonidos del sistema' (avisos y notificaciones de
    Windows): esa nunca se atenúa ni se lista."""
    try:
        return ("audiosrv.dll" in (s.DisplayName or "").lower()
                or "audiosrv.dll" in (s.Identifier or "").lower())
    except Exception:
        return False


def descripcion_exe(ruta):
    """Descripción de un programa (ej. 'Google Chrome') leída de su .exe, o None."""
    try:
        import ctypes
        from ctypes import wintypes
        v = ctypes.windll.version
        n = v.GetFileVersionInfoSizeW(ruta, None)
        if not n:
            return None
        buf = ctypes.create_string_buffer(n)
        if not v.GetFileVersionInfoW(ruta, 0, n, buf):
            return None
        p, ln = ctypes.c_void_p(), wintypes.UINT()
        if not v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(p),
                                ctypes.byref(ln)) or ln.value < 4:
            return None
        lang, cp = struct.unpack("<HH", ctypes.string_at(p.value, 4))
        if not v.VerQueryValueW(buf, "\\StringFileInfo\\%04x%04x\\FileDescription" % (lang, cp),
                                ctypes.byref(p), ctypes.byref(ln)) or not ln.value:
            return None
        return ctypes.wstring_at(p.value).strip() or None
    except Exception:
        return None


def titulo_ventana(pid):
    """Título de la ventana visible de un proceso o de sus procesos padre (en los navegadores
    el audio sale de un proceso auxiliar y la ventana es del principal). None si no hay."""
    try:
        import ctypes
        from ctypes import wintypes
        pids = {pid}
        if psutil is not None:
            try:
                p = psutil.Process(pid)
                for _ in range(3):
                    p = p.parent()
                    if p is None:
                        break
                    pids.add(p.pid)
            except Exception:
                pass
        u = ctypes.windll.user32
        hallados = []

        def cb(hwnd, lparam):
            if u.IsWindowVisible(hwnd):
                proc = wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
                if proc.value in pids:
                    n = u.GetWindowTextLengthW(hwnd)
                    if n > 0:
                        b = ctypes.create_unicode_buffer(n + 1)
                        u.GetWindowTextW(hwnd, b, n + 1)
                        hallados.append(b.value)
            return True
        u.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        return max(hallados, key=len) if hallados else None
    except Exception:
        return None


def icono_exe(ruta, tam=20):
    """PhotoImage con el icono de un .exe (sobre fondo blanco), o None."""
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image, ImageTk
        vp, ci, cu = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        u, g, sh = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.shell32
        u.GetDC.restype, u.GetDC.argtypes = vp, [vp]
        u.ReleaseDC.argtypes = [vp, vp]
        u.DrawIconEx.argtypes = [vp, ci, ci, vp, ci, ci, cu, vp, cu]
        u.DestroyIcon.argtypes = [vp]
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = vp, [vp]
        g.CreateCompatibleBitmap.restype, g.CreateCompatibleBitmap.argtypes = vp, [vp, ci, ci]
        g.SelectObject.restype, g.SelectObject.argtypes = vp, [vp, vp]
        g.PatBlt.argtypes = [vp, ci, ci, ci, ci, cu]
        g.GetDIBits.argtypes = [vp, vp, cu, cu, vp, vp, cu]
        g.DeleteObject.argtypes = [vp]
        g.DeleteDC.argtypes = [vp]
        sh.ExtractIconExW.argtypes = [ctypes.c_wchar_p, ci, ctypes.POINTER(vp), ctypes.POINTER(vp), cu]
        grande, pequeno = vp(), vp()
        if not sh.ExtractIconExW(ruta, 0, ctypes.byref(grande), ctypes.byref(pequeno), 1):
            return None
        hicon = grande.value or pequeno.value
        if not hicon:
            return None

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("a", wintypes.LONG),
                        ("b", wintypes.LONG), ("c", wintypes.DWORD), ("d", wintypes.DWORD)]
        hdc = u.GetDC(None)
        mdc = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, tam, tam)
        g.SelectObject(mdc, bmp)
        g.PatBlt(mdc, 0, 0, tam, tam, 0x00FF0062)               # fondo blanco
        u.DrawIconEx(mdc, 0, 0, hicon, tam, tam, 0, None, 3)
        buf = ctypes.create_string_buffer(tam * tam * 4)
        bi = BIH(ctypes.sizeof(BIH), tam, -tam, 1, 32, 0, 0, 0, 0, 0, 0)
        g.GetDIBits(mdc, bmp, 0, tam, buf, ctypes.byref(bi), 0)
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(None, hdc)
        for h in {grande.value, pequeno.value}:
            if h:
                u.DestroyIcon(h)
        img = Image.frombuffer("RGBA", (tam, tam), buf, "raw", "BGRA", 0, 1).convert("RGB")
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


# ----------------------------------------------------------------------
# Qué suena en otras apps: título limpio y, en Windows 10/11, posición y duración
# ----------------------------------------------------------------------
try:
    from pycaw.pycaw import IAudioMeterInformation
except Exception:
    IAudioMeterInformation = None
def version_windows():
    """(mayor, menor, compilación) REAL de Windows (RtlGetVersion no depende del manifiesto)."""
    try:
        import ctypes
        from ctypes import wintypes

        class OSV(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("major", wintypes.DWORD), ("minor", wintypes.DWORD),
                        ("build", wintypes.DWORD), ("platform", wintypes.DWORD),
                        ("csd", wintypes.WCHAR * 128)]
        v = OSV()
        v.size = ctypes.sizeof(OSV)
        ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(v))
        return v.major, v.minor, v.build
    except Exception:
        g = sys.getwindowsversion()
        return g.major, g.minor, g.build


_GSMTC = None          # controles multimedia de Windows 10/11 (None = aún no cargados)
_GSMTC_PROBADO = False


def cargar_gsmtc():
    """Carga 'winsdk' SOLO en Windows 10 o más nuevo. En Windows 7 ese módulo busca APIs de
    WinRT (combase.dll) que no existen y cierra el programa de golpe, así que ni se importa."""
    global _GSMTC, _GSMTC_PROBADO
    if not _GSMTC_PROBADO:
        _GSMTC_PROBADO = True
        if version_windows()[0] >= 10:
            try:
                from winsdk.windows.media.control import (
                    GlobalSystemMediaTransportControlsSessionManager as _M)
                _GSMTC = _M
            except Exception:
                _GSMTC = None
    return _GSMTC

_NOMBRES_SERVICIOS = {
    "youtube music", "youtube", "spotify", "soundcloud", "deezer", "tidal", "apple music",
    "amazon music", "brave", "google chrome", "chrome", "microsoft edge", "edge",
    "mozilla firefox", "firefox", "opera", "vivaldi", "vlc media player", "vlc", "winamp",
    "itunes", "tunein", "reproductor de windows media", "google play música",
    "google play music", "groove música"}


def limpiar_titulo(titulo, extras=()):
    """'YouTube Music - Mi canción | YouTube Music' -> 'Mi canción': quita el nombre del
    servicio y del navegador y deja solo el título."""
    if not titulo:
        return ""
    ignorar = _NOMBRES_SERVICIOS | {e.lower() for e in extras if e}
    partes = []
    for bloque in titulo.split(" | "):
        for p in re.split(r"\s+[-–—]\s+", bloque):
            p = p.strip()
            if p and p.lower() not in ignorar and p not in partes:
                partes.append(p)
    return " – ".join(partes) if partes else titulo


class LectorMedios(threading.Thread):
    """Lee cada segundo qué suena en las apps con los controles multimedia de Windows 10/11
    (título, artista, posición y duración). Corre en su propio hilo; el resto del programa
    solo mira la lista 'datos'."""

    def __init__(self):
        super().__init__(daemon=True)
        self.datos = []
        self.error = None

    def run(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._bucle())
        except Exception as e:
            self.error = motivo_error(e)

    async def _bucle(self):
        import asyncio
        import datetime
        mgr = await _GSMTC.request_async()
        while True:
            lista = []
            try:
                for s in mgr.get_sessions():
                    try:
                        info = await s.try_get_media_properties_async()
                        tl = s.get_timeline_properties()
                        suena = "PLAYING" in str(s.get_playback_info().playback_status)
                        pos = tl.position.total_seconds()
                        dur = (tl.end_time - tl.start_time).total_seconds()
                        if suena and tl.last_updated_time:          # la posición es de hace un momento
                            ult = tl.last_updated_time
                            ahora = datetime.datetime.now(ult.tzinfo) if ult.tzinfo else datetime.datetime.utcnow()
                            pos += max(0.0, (ahora - ult).total_seconds())
                        lista.append(dict(app=s.source_app_user_model_id or "", titulo=info.title or "",
                                          artista=info.artist or "", pos=pos, dur=dur,
                                          suena=suena, t=time.time()))
                    except Exception:
                        continue
            except Exception as e:
                self.error = motivo_error(e)
            self.datos = lista
            await asyncio.sleep(1.0)


# ----------------------------------------------------------------------
# Vúmetro estilo DJ (se usa para los audios del programa y para la app que se sigue)
# ----------------------------------------------------------------------
class Vumetro:
    """Barra de segmentos en un Canvas: nivel de salida (color), sombra con el nivel original,
    marca de pico y una línea de 'tope' que se puede mover."""

    def __init__(self, canvas, top=16, alto=24, y_etiq=52, cap=(9, 43)):
        self.c, self.top, self.alto, self.y_etiq, self.cap_y = canvas, top, alto, y_etiq, cap
        self.segs, self.thr, self.estado = [], [], []
        self.out = self.org = self.pico = VU_MIN
        self.pico_t = 0.0
        self.tope, self.color_tope = None, "#ffffff"
        canvas.bind("<Configure>", self.construir)

    @staticmethod
    def color_seg(db):
        """(encendido, sombra) de un segmento según su nivel."""
        if db <= -14:
            return "#2ecc71", "#2f5f40"
        if db <= -5:
            return "#f1c40f", "#665c2a"
        return "#e74c3c", "#663030"

    def x(self, db):
        db = max(VU_MIN, min(VU_MAX, db))
        return self.x0 + (db - VU_MIN) / (VU_MAX - VU_MIN) * (self.x1 - self.x0)

    def construir(self, e=None):
        c = self.c
        c.delete("all")
        w = max(80, c.winfo_width())
        self.x0, self.x1 = 8, w - 8
        ancho = (self.x1 - self.x0) / float(VU_SEGS)
        self.segs, self.thr = [], []
        for i in range(VU_SEGS):
            x = self.x0 + i * ancho
            self.segs.append(c.create_rectangle(x + 1, self.top, x + ancho - 1, self.top + self.alto,
                                                fill="#262b32", outline=""))
            self.thr.append(VU_MIN + (i + 0.5) * (VU_MAX - VU_MIN) / VU_SEGS)
        for db in (-60, -40, -20, -10, -5, 0):
            c.create_text(self.x(db), self.y_etiq, text=str(db), fill="#8a939e", font=("Segoe UI", 7))
        self.linea = c.create_line(0, self.cap_y[0], 0, self.cap_y[1], fill="#ffffff", width=2)
        self.estado = [None] * VU_SEGS
        self.dibujar()

    def actualizar(self, out, org, dt, ahora, tope=None, color_tope="#ffffff"):
        caida = 45.0 * dt                         # dB por segundo que baja la barra
        self.out = out if out > self.out else max(out, self.out - caida)
        self.org = org if org > self.org else max(org, self.org - caida)
        if self.out >= self.pico:
            self.pico, self.pico_t = self.out, ahora
        elif ahora - self.pico_t > 0.8:
            self.pico = max(self.out, self.pico - 30.0 * dt)
        self.tope, self.color_tope = tope, color_tope
        self.dibujar()

    def dibujar(self):
        if not self.segs:
            return
        i_pico = None
        if self.pico > VU_MIN + 1:
            i_pico = min(VU_SEGS - 1, int((self.pico - VU_MIN) / (VU_MAX - VU_MIN) * VU_SEGS))
        for i, thr in enumerate(self.thr):
            on, sombra = self.color_seg(thr)
            if i == i_pico:
                col = "#ffffff"
            elif thr <= self.out:
                col = on
            elif thr <= self.org:
                col = sombra
            else:
                col = "#262b32"
            if col != self.estado[i]:
                self.estado[i] = col
                self.c.itemconfig(self.segs[i], fill=col)
        if self.tope is not None:
            x = self.x(self.tope)
            self.c.coords(self.linea, x, self.cap_y[0], x, self.cap_y[1])
            self.c.itemconfig(self.linea, fill=self.color_tope)


# ----------------------------------------------------------------------
# Visor del registro de eventos (reemplaza la antigua pestaña Estado)
# ----------------------------------------------------------------------
class VisorRegistro(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.title("Registro de eventos")
        self.geometry("760x420")
        f = ttk.Frame(self, padding=6)
        f.pack(fill="both", expand=True)
        bt = ttk.Frame(f)
        bt.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(bt, text="Abrir carpeta de datos",
                   command=lambda: os.startfile(CARPETA)).pack(side="left")
        ttk.Button(bt, text="Cerrar", command=self.destroy).pack(side="right")
        sb = ttk.Scrollbar(f, orient="vertical")
        self.txt = tk.Text(f, state="disabled", wrap="none", yscrollcommand=sb.set,
                           font=("Consolas", 9))
        sb.config(command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        try:
            with open(REGISTRO, encoding="utf-8", errors="replace") as fh:
                ultimas = fh.readlines()[-400:]
        except OSError:
            ultimas = []
        for linea in ultimas:
            self.insertar(linea)

    def insertar(self, linea):
        self.txt.config(state="normal")
        self.txt.insert("end", linea)
        self.txt.see("end")
        self.txt.config(state="disabled")


# ----------------------------------------------------------------------
# Aplicación principal
# ----------------------------------------------------------------------
class App:
    def __init__(self, minimizado, sock):
        self.db = BD()
        self.cmd = queue.Queue()
        self.alt_f4 = False
        self.aviso_dado = False
        self.ultimo_minuto = None
        self.ultimo_estado = None
        self.icono = None
        self.visor = None
        self.vu_out = self.vu_org = self.vu_pico = VU_MIN
        self.vu_pico_t = 0.0
        self.vu_t = time.time()
        self.vu_segs = []
        self.colores = {}
        self.proximos = []
        self.manual = False
        self.omitidas = set()               # (id de audio, 'AAAA-MM-DD HH:MM') que se saltan una vez
        self.lector = None                  # hilo que lee la canción de otras apps (Windows 10/11)
        self.app_clave = ""                 # app que se sigue en la pantalla principal
        self.app_meter = self.app_vol = self.app_pid = self.app_smtc = None
        self.app_t_lento = 0.0
        self.app_tit_prev = None
        self.app_titulo = self.app_artista = ""
        self.app_t0 = time.time()
        self._nivelando = False
        self.cola_nivelar = []
        global FORMATO_12H
        FORMATO_12H = self.db.cfg("formato_12h", "0") == "1"

        self.root = tk.Tk()
        self.root.title(NOMBRE)
        try:
            self.root.iconbitmap(recurso("icono.ico"))
        except Exception:
            pass
        self.root.geometry("1200x740")
        self.root.minsize(900, 640)
        try:
            estilo = ttk.Style()
            estilo.theme_use("vista")
            # Tk 8.6.9 (Python 3.8) ignora los colores de las filas del Treeview
            # si no se corrige el mapa de estilos.
            def _mapa(opcion):
                return [e for e in estilo.map("Treeview", query_opt=opcion)
                        if e[:2] != ("!disabled", "!selected")]
            estilo.map("Treeview", foreground=_mapa("foreground"),
                       background=_mapa("background"))
        except Exception:
            pass

        self.vol_win = VolumenWindows()
        self.construir_ui()
        try:
            self.rep = Reproductor(self.log, 0.5)
            self.aplicar_config()
        except Exception as e:
            messagebox.showerror("Audio", "No se pudo iniciar el sistema de audio:\n%s" % e)
            raise
        if AudioUtilities is None:
            self.log("Aviso: pycaw no está instalado; no se bajará el volumen de otros programas.")
        self.restaurar_pendientes()
        self.rep.persistir = lambda d: self.db.set_cfg(
            "volumenes_pendientes", json.dumps(d) if d else "")
        self.rep.al_fallo = lambda a, motivo: self.marcar_problema(a["id"], motivo)
        self.rep.al_exito = self._audio_sono
        self.manual = self.db.cfg("modo_manual", "0") == "1"
        self.limpiar_huerfanos()
        # último minuto ya ejecutado: si el programa se reinicia en ese mismo minuto
        # (por ejemplo al actualizarse) no vuelve a lanzar los audios de ese minuto
        self.ultimo_minuto = self.db.cfg("ultimo_minuto", "") or None
        self.omitidas = {(int(a), b) for a, b in self.cfg_lista("omitidas")}
        # las tareas creadas por versiones anteriores quedaron con «no iniciar con batería» y
        # «detener a las 72 h»: se vuelven a crear una vez con la definición corregida
        apertura = self.db.cfg("apertura", "")
        if apertura and self.db.cfg("tarea_version", "") != VERSION:
            ok, salida = tarea_programada(apertura)
            if ok:
                self.db.set_cfg("tarea_version", VERSION)
            self.log("Apertura automática de las %s %s." % (
                apertura, "actualizada (corre con batería y sin límite de horas)" if ok
                else "NO se pudo actualizar: " + salida[:120]))
        if cargar_gsmtc():                  # solo Windows 10/11: título, posición y duración de otras apps
            self.lector = LectorMedios()
            self.lector.start()
        self.seguir_app(self.db.cfg("app_seguir", ""))
        self.refrescar_categorias()
        self.refrescar_audios()
        self.cola_nivelar = []
        self.root.after(1500, self.programar_nivelacion)  # audios aún sin nivelar
        self.panel.bind("<Configure>", self.ajustar_divisor)
        self.root.after(300, self.activar_soltar)
        self.root.after(2000, self.verificar_archivos)
        self.mostrar_manual()
        self.root.after(30000, self.busqueda_automatica)
        self.t_audio = time.time()
        self.bucle_audio()
        self.animar()
        self.sincronizar_volumen_windows()

        self.root.protocol("WM_DELETE_WINDOW", self.al_cerrar)
        self.root.bind_all("<Alt-F4>", self.marcar_alt_f4)
        self.crear_bandeja()
        threading.Thread(target=self.escuchar, args=(sock,), daemon=True).start()
        if minimizado:
            self.ocultar()
        self.log("Programa iniciado%s (%s %s, Windows %d.%d.%d, Python %s %s)." % (
            " en segundo plano (inicio automático o tarea programada)" if minimizado else "",
            NOMBRE, VERSION, *version_windows(), sys.version.split()[0],
            "64 bits" if sys.maxsize > 2 ** 32 else "32 bits"))
        self.log("Canción/duración de otras apps: %s." % (
            "disponible (Windows 10/11)" if self.lector else "no disponible (solo título y tiempo)"))
        self.tick()

    # ---------------- interfaz ----------------
    def construir_ui(self):
        nb = ttk.Notebook(self.root)
        self.nb = nb
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.tab_audios = ttk.Frame(nb)
        self.tab_cats = ttk.Frame(nb)
        self.tab_apps = ttk.Frame(nb)
        self.tab_sis = ttk.Frame(nb)
        nb.add(self.tab_audios, text="  Audios  ")
        nb.add(self.tab_cats, text="  Categorías  ")
        nb.add(self.tab_apps, text="  Apps  ")
        nb.add(self.tab_sis, text="  Sistema  ")
        self.ui_audios()
        self.ui_categorias()
        self.ui_apps()
        self.ui_sistema()

    # ---------------- pestaña Apps: quién usa el audio y a quién se atenúa ----------------
    def restaurar_pendientes(self):
        """Si el programa se cerró de golpe mientras atenuaba otras apps, esas apps se quedaron
        con el volumen bajo (Windows lo recuerda por app): aquí se les devuelve al abrir."""
        try:
            pend = json.loads(self.db.cfg("volumenes_pendientes", "") or "{}")
        except ValueError:
            pend = {}
        if not pend or AudioUtilities is None:
            return
        n = 0
        try:
            for s in AudioUtilities.GetAllSessions():
                try:
                    if s.Process is None:
                        continue
                    orig = pend.get(s.Process.name())
                    v = s.SimpleAudioVolume
                    if orig is not None and v.GetMasterVolume() < orig - 0.01:
                        v.SetMasterVolume(float(orig), None)
                        n += 1
                except Exception:
                    continue
        except Exception:
            pass
        self.db.set_cfg("volumenes_pendientes", "")
        if n:
            self.log("Se devolvió su volumen a %d app(s) que quedaron atenuadas la vez anterior." % n)

    def cfg_lista(self, clave):
        try:
            v = json.loads(self.db.cfg(clave, "[]"))
            return v if isinstance(v, list) else []
        except ValueError:
            return []

    def set_cfg_lista(self, clave, lista):
        self.db.set_cfg(clave, json.dumps(lista))

    def ui_apps(self):
        f = self.tab_apps
        ttk.Label(f, text="Apps de Windows que están usando el audio (como en el Mezclador de "
                          "volumen). Cuando suena un audio con la opción «Bajar el volumen de las "
                          "otras apps», las marcadas con «Sí» se atenúan y luego vuelven a su volumen.",
                  wraplength=1000, foreground="#555555", justify="left").pack(
            anchor="w", padx=10, pady=(10, 6))
        marco = ttk.Frame(f)
        marco.pack(fill="both", expand=True, padx=10)
        cols = ("ventana", "vol", "estado", "atenuar")
        self.tree_apps = ttk.Treeview(marco, columns=cols, selectmode="browse", height=12)
        self.tree_apps.heading("#0", text="App")
        for c, t, w in (("ventana", "Lo que se ve en su ventana", 380), ("vol", "Volumen", 80),
                        ("estado", "Estado", 110), ("atenuar", "¿Atenuar?", 80)):
            self.tree_apps.heading(c, text=t)
            self.tree_apps.column(c, width=w, anchor="w" if c == "ventana" else "center")
        self.tree_apps.column("#0", width=260)
        sb = ttk.Scrollbar(marco, orient="vertical", command=self.tree_apps.yview)
        self.tree_apps.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree_apps.pack(side="left", fill="both", expand=True)
        self.tree_apps.tag_configure("atenuada", background="#fdebd0")
        self.tree_apps.tag_configure("no", foreground="#999999")
        self.tree_apps.bind("<Double-1>", lambda e: self.alternar_app())
        self.tree_apps.bind("<<TreeviewSelect>>", lambda e: self._app_elegida())
        self.iconos_apps = {}                       # se guardan: si no, Tk los borra
        self.lbl_apps = ttk.Label(f, text="", foreground="#1e64c8", font=("Segoe UI", 9, "bold"))
        self.lbl_apps.pack(anchor="w", padx=10, pady=(6, 0))
        bt = ttk.Frame(f)
        bt.pack(anchor="w", padx=10, pady=8)
        ttk.Button(bt, text="Atenuar sí / no", command=self.alternar_app).pack(side="left")
        ttk.Button(bt, text="Añadir app a mano…", command=self.agregar_app_manual).pack(
            side="left", padx=8)
        ttk.Button(bt, text="Quitar de la lista", command=self.quitar_app_manual).pack(side="left")
        ttk.Button(bt, text="Probar atenuación (4 s)", command=self.probar_atenuacion).pack(
            side="left", padx=18)
        ttk.Button(bt, text="Actualizar", command=self.refrescar_apps).pack(side="left")
        ttk.Button(bt, text="Dejar de mostrar en pantalla principal",
                   command=lambda: self.seguir_app("")).pack(side="left", padx=18)
        ttk.Label(f, text="Al elegir una app (un clic) aparece en la pantalla principal con la "
                          "canción, su línea de reproducción y su medidor en vivo; la línea blanca "
                          "baja (naranja) cuando se atenúa.\nSi una app no aparece sola (no está "
                          "sonando ahora), usa «Añadir app a mano» y elige su programa (.exe). "
                          "Doble clic = atenuar Sí / No.",
                  foreground="#888888", justify="left").pack(anchor="w", padx=10)
        self.root.after(2000, self._ciclo_apps)

    def _ciclo_apps(self):
        try:
            if (self.nb.index("current") == self.nb.index(self.tab_apps)
                    and self.root.winfo_viewable()):
                self.refrescar_apps()
        except tk.TclError:
            pass
        self.root.after(2000, self._ciclo_apps)

    def _listar_apps(self):
        """{nombre_proceso_en_minúsculas: datos} de las apps con audio y de las añadidas a mano."""
        apps = {}
        if AudioUtilities is not None:
            try:
                for s in AudioUtilities.GetAllSessions():
                    try:
                        if s.Process is None or s.ProcessId == os.getpid() or es_sonido_sistema(s):
                            continue
                        nombre = s.Process.name()
                        clave = nombre.lower()
                        d = apps.setdefault(clave, dict(nombre=nombre, ruta=None, pid=s.ProcessId,
                                                        vol=0.0, activo=False, manual=False))
                        try:
                            d["ruta"] = d["ruta"] or s.Process.exe()
                        except Exception:
                            pass
                        d["vol"] = max(d["vol"], s.SimpleAudioVolume.GetMasterVolume())
                        d["activo"] = d["activo"] or getattr(s, "State", 0) == 1
                    except Exception:
                        continue
            except Exception:
                pass
        for ruta in self.cfg_lista("apps_manuales"):
            clave = os.path.basename(ruta).lower()
            d = apps.setdefault(clave, dict(nombre=os.path.basename(ruta), ruta=ruta, pid=None,
                                            vol=None, activo=False, manual=True))
            d["manual"] = True
            d["ruta"] = d["ruta"] or ruta
        return apps

    def refrescar_apps(self):
        apps = self._listar_apps()
        excluidas = set(self.cfg_lista("apps_excluir"))
        atenuadas = {n.lower() for n in self.rep.atenuadas()}
        sel = self.tree_apps.selection()
        self.tree_apps.delete(*self.tree_apps.get_children())
        for clave, d in sorted(apps.items(), key=lambda x: (not x[1]["activo"], x[0])):
            ruta = d["ruta"]
            if ruta and ruta not in self.iconos_apps:
                self.iconos_apps[ruta] = icono_exe(ruta)
            nombre = (descripcion_exe(ruta) if ruta else None) or d["nombre"].rsplit(".", 1)[0]
            ventana = (titulo_ventana(d["pid"]) if d["pid"] else None) or ""
            if clave in atenuadas:
                estado = "Atenuada"
            elif d["activo"]:
                estado = "Sonando"
            elif d["vol"] is None:
                estado = "Sin sesión"
            else:
                estado = "En silencio"
            tags = (["atenuada"] if clave in atenuadas else []) + (["no"] if clave in excluidas else [])
            kw = dict(image=self.iconos_apps[ruta]) if ruta and self.iconos_apps.get(ruta) else {}
            self.tree_apps.insert("", "end", iid=clave, text="  " + nombre, tags=tags, values=(
                ventana[:70], "" if d["vol"] is None else "%d%%" % round(d["vol"] * 100), estado,
                "No" if clave in excluidas else "Sí"), **kw)
        for s_ in (sel or ([self.app_clave] if self.app_clave else [])):
            if self.tree_apps.exists(s_):
                self._ignorar_sel_app = True
                self.tree_apps.selection_set(s_)
        if atenuadas:
            self.lbl_apps.config(text="Atenuando ahora: " + ", ".join(sorted(self.rep.atenuadas())))
        else:
            self.lbl_apps.config(text="" if apps else
                                 "No se detecta ninguna app usando el audio en este momento.")

    def _app_elegida(self):
        """Un clic en una app de la lista: pasa a verse en la pantalla principal."""
        if getattr(self, "_ignorar_sel_app", False):       # selección puesta por el programa
            self._ignorar_sel_app = False
            return
        s = self.tree_apps.selection()
        if s:
            self.seguir_app(s[0])

    def alternar_app(self):
        s = self.tree_apps.selection()
        if not s:
            return
        excl = self.cfg_lista("apps_excluir")
        if s[0] in excl:
            excl.remove(s[0])
        else:
            excl.append(s[0])
        self.set_cfg_lista("apps_excluir", excl)
        self.rep.apps_excluir = set(excl)
        self.refrescar_apps()

    def agregar_app_manual(self):
        ruta = filedialog.askopenfilename(
            parent=self.root, title="Elige el programa (.exe) cuyo audio quieres atenuar",
            filetypes=[("Programas", "*.exe"), ("Todos", "*.*")])
        if ruta:
            lista = self.cfg_lista("apps_manuales")
            if ruta not in lista:
                lista.append(ruta)
                self.set_cfg_lista("apps_manuales", lista)
            self.refrescar_apps()

    def quitar_app_manual(self):
        s = self.tree_apps.selection()
        if not s:
            return
        lista = self.cfg_lista("apps_manuales")
        nueva = [r for r in lista if os.path.basename(r).lower() != s[0]]
        if len(nueva) == len(lista):
            messagebox.showinfo("Apps", "Esta app se detecta sola; solo se pueden quitar las "
                                "añadidas a mano. Si no quieres que se atenúe, ponla en «No».",
                                parent=self.root)
            return
        self.set_cfg_lista("apps_manuales", nueva)
        self.refrescar_apps()

    def probar_atenuacion(self):
        """Baja el volumen de las apps marcadas durante 4 s, para comprobar que se detectan."""
        self.rep.forzar_atenuar_hasta = time.time() + 4.0
        self.root.after(1200, self.refrescar_apps)
        self.root.after(5600, self.refrescar_apps)

    def ui_audios(self):
        f = self.tab_audios
        top = ttk.Frame(f)
        top.pack(fill="x", padx=6, pady=6)
        ttk.Label(top, text="Categoría:").pack(side="left")
        self.v_filtro = tk.StringVar(value="Todas")
        self.cb_filtro = ttk.Combobox(top, textvariable=self.v_filtro, state="readonly", width=28)
        self.cb_filtro.pack(side="left", padx=6)
        self.cb_filtro.bind("<<ComboboxSelected>>", lambda e: self.refrescar_audios())

        ttk.Label(top, text="Buscar:").pack(side="left", padx=(14, 0))
        self.v_buscar = tk.StringVar()
        self.v_buscar.trace_add("write", lambda *a: self.refrescar_audios(False))
        ttk.Entry(top, textvariable=self.v_buscar, width=24).pack(side="left", padx=6)
        ttk.Button(top, text="✕", width=3,
                   command=lambda: self.v_buscar.set("")).pack(side="left")

        self.lbl_proximo = ttk.Label(top, text="", foreground="#1e64c8",
                                     font=("Segoe UI", 9, "bold"))
        self.lbl_proximo.pack(side="right")
        self.lbl_actualiza = tk.Label(top, text="", fg="#c0392b", cursor="hand2",
                                      font=("Segoe UI", 9, "bold"))
        self.lbl_actualiza.bind("<Button-1>", lambda e: self.ofrecer_actualizacion())
        self.nueva = None                      # (se muestra solo si hay versión nueva)
        self.lbl_alerta = tk.Label(top, text="", fg="#c0392b", cursor="hand2",
                                   font=("Segoe UI", 9, "bold"))
        self.lbl_alerta.bind("<Button-1>", lambda e: self.ver_problemas())
        self.lbl_manual = tk.Label(top, text="", fg="white", bg="#c0392b",
                                   font=("Segoe UI", 9, "bold"), padx=8)

        # --- barra inferior (se empaqueta primero para que siempre se vea) ---
        barra = ttk.Frame(f)
        barra.pack(side="bottom", fill="x", padx=6, pady=(4, 6))
        ttk.Separator(f, orient="horizontal").pack(side="bottom", fill="x", padx=6)

        # gestión de audios (izquierda)
        bt = ttk.Frame(barra)
        bt.pack(side="left")
        for txt, fn in (("Añadir", self.nuevo_audio), ("Editar", self.editar_audio),
                        ("Eliminar", self.eliminar_audio),
                        ("Activar / Desactivar", self.alternar_audio),
                        ("Probar ahora", self.probar_audio)):
            ttk.Button(bt, text=txt, command=fn).pack(side="left", padx=3)

        # controles de reproducción (derecha); solo símbolos que existen en Windows 7
        fc = ttk.Frame(barra)
        fc.pack(side="right")
        self.btn_manual = ttk.Button(fc, text="Modo manual", width=23,
                                     command=self.alternar_manual)
        self.btn_manual.pack(side="left", padx=(3, 18))
        self.btn_pausa = ttk.Button(fc, text="❚❚  Pausa", width=12,
                                    command=self.pausar_reanudar)
        self.btn_pausa.pack(side="left", padx=3)
        ttk.Button(fc, text="▶▶  Siguiente", width=12,
                   command=lambda: self.rep.detener_actual()).pack(side="left", padx=3)
        ttk.Button(fc, text="■  Vaciar cola", width=14,
                   command=self.vaciar_todo).pack(side="left", padx=3)

        # --- zona central: lista de audios | cola ---
        panel = ttk.PanedWindow(f, orient="horizontal")
        panel.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        self.panel = panel
        izq = ttk.Frame(panel)
        panel.add(izq, weight=4)
        cols = ("nombre", "cat", "prio", "dias", "horario", "caduca", "estado")
        self.tree = ttk.Treeview(izq, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("nombre", "Nombre", 175), ("cat", "Categoría", 80),
                        ("prio", "Prioridad", 62), ("dias", "Días", 52),
                        ("horario", "Horario", 235), ("caduca", "Caduca", 70),
                        ("estado", "Estado", 58)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, minwidth=40,
                             anchor="center" if c in ("prio", "estado") else "w")
        sb = ttk.Scrollbar(izq, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.editar_audio())
        self.tree.tag_configure("prio", font=FUENTE_PRIO)       # solo la prioridad usa esta letra
        self.tree.tag_configure("inactivo", foreground="#999999")
        self.tree.tag_configure("problema", foreground="#c0392b")
        self.lbl_arrastra = tk.Label(self.tree, text="Arrastra aquí tus audios\n(MP3, WAV, OGG o FLAC)",
                                     fg="#9aa3ad", bg="white", font=("Segoe UI", 15),
                                     justify="center")

        der = ttk.LabelFrame(panel, text="Cola de reproducción", padding=8)
        panel.add(der, weight=1)
        fila = ttk.Frame(der)
        fila.pack(fill="x")
        self.lbl_rep = ttk.Label(fila, text="SIN REPRODUCCIÓN", foreground="#888888",
                                 font=("Segoe UI", 8, "bold"))
        self.lbl_rep.pack(side="left")
        self.lbl_badge = ttk.Label(fila, text="", foreground="#222222", font=FUENTE_PRIO)
        self.lbl_badge.pack(side="right")
        self.lbl_sonando = ttk.Label(der, text="(nada)", font=("Segoe UI", 12),
                                     wraplength=290, justify="left")
        self.lbl_sonando.pack(anchor="w", fill="x", pady=(2, 0))
        self.lbl_fondo = ttk.Label(der, text="", foreground="#888888", font=("Segoe UI", 8),
                                   wraplength=300, justify="left")
        self.lbl_fondo.pack(anchor="w", fill="x", pady=(0, 6))

        fondo = self.root.cget("background")
        self.cv_prog = tk.Canvas(der, height=16, bg=fondo, highlightthickness=0, bd=0)
        self.cv_prog.pack(fill="x")
        self.lbl_tiempo = ttk.Label(der, text="0:00 / 0:00", foreground="#666666")
        self.lbl_tiempo.pack(anchor="e", pady=(0, 6))

        # vúmetro estilo DJ
        self.cv_vu = tk.Canvas(der, height=64, bg=VU_FONDO, highlightthickness=0, bd=0)
        self.cv_vu.pack(fill="x")
        self.vu = Vumetro(self.cv_vu)
        self.lbl_db = ttk.Label(der, text="", foreground="#444444")
        self.lbl_db.pack(anchor="w", pady=(3, 6))

        # --- la app que se sigue (se elige en la pestaña Apps): canción, línea y medidor ---
        self.fr_app = ttk.LabelFrame(der, text="App seleccionada", padding=(8, 2, 8, 4))
        fa = ttk.Frame(self.fr_app)
        fa.pack(fill="x")
        self.lbl_app_icono = tk.Label(fa, bd=0)
        self.lbl_app_icono.pack(side="left")
        self.lbl_app_nombre = ttk.Label(fa, text="", font=("Segoe UI", 8, "bold"),
                                        foreground="#555555")
        self.lbl_app_nombre.pack(side="left", padx=(4, 0))
        self.lbl_app_estado = ttk.Label(fa, text="", font=("Segoe UI", 8, "bold"))
        self.lbl_app_estado.pack(side="right")
        self.lbl_app_cancion = ttk.Label(self.fr_app, text="", font=("Segoe UI", 10, "bold"))
        self.lbl_app_cancion.pack(anchor="w", fill="x")
        self.cv_app_prog = tk.Canvas(self.fr_app, height=14, bg=fondo, highlightthickness=0, bd=0)
        self.cv_app_prog.pack(fill="x", pady=(2, 0))
        self.lbl_app_tiempo = ttk.Label(self.fr_app, text="", foreground="#666666")
        self.lbl_app_tiempo.pack(anchor="e")
        self.cv_app_vu = tk.Canvas(self.fr_app, height=46, bg=VU_FONDO, highlightthickness=0, bd=0)
        self.cv_app_vu.pack(fill="x")
        self.vu_app = Vumetro(self.cv_app_vu, top=8, alto=18, y_etiq=36, cap=(3, 29))
        self.lbl_app_nota = ttk.Label(self.fr_app, text="", font=("Segoe UI", 8),
                                      foreground="#666666")
        self.lbl_app_nota.pack(anchor="w")

        # --- abajo: la cola (izquierda) y el mezclador de dos faders (derecha) ---
        abajo = ttk.Frame(der)
        self.fr_abajo = abajo
        abajo.pack(fill="both", expand=True)

        mezcla = ttk.Frame(abajo)
        mezcla.pack(side="right", fill="y", padx=(10, 0))
        mezcla.rowconfigure(1, weight=1)
        self._fader_arrastre = False
        # fader 1: volumen de Windows (la salida del equipo)
        ttk.Label(mezcla, text="VOLUMEN", font=("Segoe UI", 8, "bold")).grid(row=0, column=0)
        self.v_vol = tk.DoubleVar(value=100.0)
        self.sc_vol = ttk.Scale(mezcla, from_=100, to=0, orient="vertical",
                                variable=self.v_vol, command=self.cambiar_volumen)
        self.sc_vol.grid(row=1, column=0, sticky="ns", padx=10, pady=4)
        self.lbl_vol = ttk.Label(mezcla, text="--", justify="center", width=9, anchor="center")
        self.lbl_vol.grid(row=2, column=0)
        ttk.Label(mezcla, text="Mezclador", foreground="#888888",
                  font=("Segoe UI", 8)).grid(row=3, column=0)
        # fader 2: tope de decibelios de los audios
        ttk.Label(mezcla, text="TOPE", font=("Segoe UI", 8, "bold")).grid(row=0, column=1)
        self.v_tope = tk.DoubleVar(value=float(self.db.cfg("tope_db", "-20")))
        self.sc_tope = ttk.Scale(mezcla, from_=TOPE_MAX, to=TOPE_MIN, orient="vertical",
                                 variable=self.v_tope, command=self.cambiar_tope)
        self.sc_tope.grid(row=1, column=1, sticky="ns", padx=10, pady=4)
        self.lbl_tope = ttk.Label(mezcla, text="", justify="center", width=9, anchor="center")
        self.lbl_tope.grid(row=2, column=1)
        ttk.Label(mezcla, text="de audios", foreground="#888888",
                  font=("Segoe UI", 8)).grid(row=3, column=1)
        self.lbl_tope.config(text="%.0f dB" % self.v_tope.get())
        self.sc_vol.bind("<ButtonPress-1>", lambda e: setattr(self, "_fader_arrastre", True),
                         add="+")
        self.sc_vol.bind("<ButtonRelease-1>", self._soltar_fader_volumen, add="+")
        self.sc_tope.bind("<ButtonRelease-1>", lambda e: self.db.set_cfg(
            "tope_db", "%.0f" % self.v_tope.get()), add="+")

        izqc = ttk.Frame(abajo)
        izqc.pack(side="left", fill="both", expand=True)
        ttk.Label(izqc, text="Próximos audios  (★ negrita = prioridad)",
                  foreground="#666666").pack(anchor="w")
        self.lb_cola_main = ttk.Treeview(izqc, columns=("hora", "audio"), show="headings",
                                         selectmode="browse", height=6)
        self.lb_cola_main.heading("hora", text="Hora")
        self.lb_cola_main.heading("audio", text="Audio")
        self.lb_cola_main.column("hora", width=84, minwidth=60, stretch=False, anchor="w")
        self.lb_cola_main.column("audio", width=110, minwidth=60, stretch=True, anchor="w")
        self.lb_cola_main.tag_configure("prio", font=FUENTE_PRIO)
        self.lb_cola_main.tag_configure("espera", foreground="#7a6a1f")
        self.lb_cola_main.pack(fill="both", expand=True, pady=(2, 0))
        self.lb_cola_main.bind("<ButtonPress-1>", self.cola_press)
        self.lb_cola_main.bind("<B1-Motion>", self.cola_drag)
        self.cola_origen = None
        # los controles se empaquetan ABAJO primero: así nunca se cortan; la lista usa lo que sobra
        self.lbl_ayuda_cola = ttk.Label(izqc, text="Elige un audio de la lista.",
                                        foreground="#888888", font=("Segoe UI", 8),
                                        wraplength=215, justify="left")
        self.lbl_ayuda_cola.pack(side="bottom", anchor="w", pady=(2, 0))
        fb = ttk.Frame(izqc)
        fb.pack(side="bottom", fill="x", pady=(4, 0))
        self.btn_sube = ttk.Button(fb, text="▲", width=3, command=lambda: self.mover_sel(-1))
        self.btn_sube.pack(side="left")
        self.btn_baja = ttk.Button(fb, text="▼", width=3, command=lambda: self.mover_sel(1))
        self.btn_baja.pack(side="left", padx=2)
        self.btn_ahora = ttk.Button(fb, text="▶ Ahora", width=8, command=self.ahora_sel)
        self.btn_ahora.pack(side="left")
        self.btn_quitar = ttk.Button(fb, text="Quitar", width=8, command=self.quitar_sel)
        self.btn_quitar.pack(side="left", padx=(2, 0))
        self.lb_cola_main.pack_forget()
        self.lb_cola_main.pack(fill="both", expand=True, pady=(2, 0))
        self.lb_cola_main.bind("<<TreeviewSelect>>", lambda e: self.actualizar_botones_cola())
        self.root.after(200, self.actualizar_botones_cola)

    def ui_categorias(self):
        f = self.tab_cats
        self.lb_cats = tk.Listbox(f, height=15, exportselection=False, activestyle="none",
                                  font=("Segoe UI", 10))
        self.lb_cats.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        d = ttk.Frame(f)
        d.pack(side="left", fill="y", padx=6, pady=6)
        self.v_nueva_cat = tk.StringVar()
        ttk.Entry(d, textvariable=self.v_nueva_cat, width=30).pack(pady=3)
        ttk.Button(d, text="Añadir categoría", command=self.add_cat).pack(fill="x", pady=3)
        ttk.Button(d, text="Renombrar seleccionada", command=self.ren_cat).pack(fill="x", pady=3)
        ttk.Button(d, text="Cambiar color...", command=self.color_cat).pack(fill="x", pady=3)
        ttk.Button(d, text="Eliminar seleccionada", command=self.del_cat).pack(fill="x", pady=3)
        ttk.Label(d, text="El color de cada categoría se ve en la lista\n"
                          "de audios y en la cola. La prioridad se\n"
                          "distingue aparte: negrita, otra letra y ★.",
                  foreground="#666666", justify="left").pack(anchor="w", pady=(14, 0))

    def ui_sistema(self):
        f = ttk.Frame(self.tab_sis, padding=14)
        f.pack(fill="both", expand=True)

        self.v_auto = tk.IntVar(value=1 if autoinicio_activo() else 0)
        ttk.Checkbutton(f, text="Iniciar con Windows (en segundo plano)",
                        variable=self.v_auto, command=self.cambiar_auto).pack(anchor="w", pady=4)
        self.v_12h = tk.IntVar(value=1 if FORMATO_12H else 0)
        ttk.Checkbutton(f, text="Mostrar las horas en formato de 12 horas (AM/PM)",
                        variable=self.v_12h, command=self.cambiar_12h).pack(anchor="w", pady=2)

        fh = ttk.LabelFrame(f, text="Apertura automática a una hora (todos los días)", padding=8)
        fh.pack(fill="x", pady=8)
        self.v_apertura = tk.StringVar(value=fmt_hora(self.db.cfg("apertura", "")))
        self.v_lbl_apertura = tk.StringVar()
        ttk.Label(fh, textvariable=self.v_lbl_apertura).pack(side="left")
        self._rotulo_apertura()
        ttk.Entry(fh, textvariable=self.v_apertura, width=8).pack(side="left", padx=6)
        ttk.Button(fh, text="Programar", command=self.programar_apertura).pack(side="left", padx=3)
        ttk.Button(fh, text="Quitar", command=self.quitar_apertura).pack(side="left", padx=3)

        fv = ttk.LabelFrame(f, text="Damper (bajar el volumen de las otras apps y del audio en "
                                    "curso mientras suena un audio)", padding=8)
        fv.pack(fill="x", pady=6)
        self.v_pct = tk.StringVar(value=self.db.cfg("bajar_pct", "50"))
        self.v_rampa = tk.StringVar(value=self.db.cfg("damper_rampa", "1.5"))
        self.v_espera = tk.StringVar(value=self.db.cfg("damper_espera", "30"))
        self.v_modo = tk.StringVar(value=self.MODOS[1 if self.db.cfg(
            "damper_modo", "mezclar") == "pausar" else 0])
        self.v_fundido = tk.StringVar(value=self.db.cfg("fundido", "0.5"))
        filas = (("Bajar el sonido general (otras apps y audio en curso) al (%):  [más alto = baja menos]", self.v_pct, 0, 90, 5),
                 ("Rampa del damper: tiempo de bajada y de subida (seg):", self.v_rampa,
                  0, 10, 0.5),
                 ("Espera tras una prioridad antes del siguiente en cola (seg):", self.v_espera,
                  0, 300, 5),
                 ("Fundido al iniciar y al terminar cada audio (seg):", self.v_fundido,
                  0, 5, 0.5))
        for i, (txt, var, lo, hi, inc) in enumerate(filas):
            ttk.Label(fv, text=txt).grid(row=i if i < 3 else i + 1, column=0, sticky="e", pady=2)
            ttk.Spinbox(fv, from_=lo, to=hi, increment=inc, width=6, textvariable=var).grid(
                row=i if i < 3 else i + 1, column=1, sticky="w", padx=6)
        ttk.Label(fv, text="Si ya suena un audio normal y llega una prioridad:").grid(
            row=3, column=0, sticky="e", pady=2)
        ttk.Combobox(fv, textvariable=self.v_modo, values=self.MODOS, state="readonly",
                     width=40).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(fv, text="Guardar", command=self.guardar_damper).grid(
            row=0, column=2, rowspan=2, padx=12)

        fn = ttk.LabelFrame(f, text="Nivelación automática de volumen (decibelios)", padding=8)
        fn.pack(fill="x", pady=6)
        activa, _, techo = self.cfg_norm()
        self.v_norm = tk.StringVar(value="1" if activa else "0")
        ttk.Checkbutton(fn, text="Igualar el volumen de todos los audios", variable=self.v_norm,
                        onvalue="1", offvalue="0").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(fn, text="Tope de picos (dB, ej. -1):").grid(row=1, column=0, sticky="e",
                                                               pady=3)
        self.v_techo = tk.StringVar(value="%g" % techo)
        ttk.Spinbox(fn, from_=-12, to=-0.1, increment=0.5, width=6,
                    textvariable=self.v_techo).grid(row=1, column=1, padx=6, sticky="w")
        ttk.Button(fn, text="Guardar y reanalizar todos",
                   command=self.guardar_nivelacion).grid(row=1, column=2, padx=12)
        self.v_recorte = tk.IntVar(value=int(self.db.cfg("recortar_silencio", "0")))
        ttk.Checkbutton(fn, text="Recortar el silencio sobrante al inicio y al final de cada audio "
                                 "(deja 0,3 s; se aplica al añadir o reanalizar)",
                        variable=self.v_recorte, command=lambda: self.db.set_cfg(
                            "recortar_silencio", str(self.v_recorte.get()))
                        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))
        ttk.Label(fn, text="Cada audio se guarda al mismo nivel (%d dB) sin pasar del tope de "
                           "picos, y el fader TOPE de la pantalla principal\n"
                           "decide hasta qué nivel suenan, en vivo. El archivo original "
                           "no se toca." % NIVEL_BASE,
                  foreground="#666666", justify="left").grid(row=2, column=0, columnspan=4,
                                                             sticky="w", pady=(4, 0))

        fr = ttk.Frame(f)
        fr.pack(anchor="w", pady=(2, 0))
        ttk.Button(fr, text="Ver registro de eventos", command=self.ver_registro).pack(side="left")
        ttk.Button(fr, text="Revisar conflictos de programación",
                   command=self.ver_conflictos).pack(side="left", padx=8)

        ttk.Label(f, text="Al cerrar la ventana (X) el programa sigue en segundo plano.\n"
                          "Para cerrarlo por completo: Alt+F4, este botón o el icono junto al reloj.",
                  justify="left").pack(anchor="w", pady=10)
        ttk.Button(f, text="Cerrar programa por completo",
                   command=lambda: self.salir(True)).pack(anchor="w")
        fu = ttk.Frame(f)
        fu.pack(anchor="w", pady=(14, 0))
        ttk.Label(fu, text="%s %s" % (NOMBRE, VERSION), foreground="#666666").pack(side="left")
        ttk.Button(fu, text="Buscar actualizaciones",
                   command=lambda: self.comprobar_actualizacion(manual=True)).pack(
            side="left", padx=10)
        self.v_autoupd = tk.IntVar(value=int(self.db.cfg("auto_update", "1")))
        ttk.Checkbutton(fu, text="Buscar automáticamente una vez al día", variable=self.v_autoupd,
                        command=self.cambiar_auto_update).pack(side="left")

    # ---------------- registro / estado ----------------
    def log(self, msg):
        """Guarda el evento en registro.txt (se rota al pasar de 512 KB)."""
        linea = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        try:
            if os.path.exists(REGISTRO) and os.path.getsize(REGISTRO) > 512 * 1024:
                os.replace(REGISTRO, REGISTRO + ".1")
            with open(REGISTRO, "a", encoding="utf-8") as fh:
                fh.write(linea)
        except OSError:
            pass
        visor = getattr(self, "visor", None)
        if visor is not None:
            try:
                visor.insertar(linea)
            except tk.TclError:
                self.visor = None

    def ver_registro(self):
        if getattr(self, "visor", None) is not None:
            try:
                self.visor.lift()
                return
            except tk.TclError:
                pass
        self.visor = VisorRegistro(self)

    def actualizar_estado(self):
        est = (self.rep.firma(), tuple((t, a["id"]) for t, a in self.proximos))
        if est == self.ultimo_estado:
            return
        self.ultimo_estado = est
        a, _, _, fondo = self.rep.sonando()
        self.lbl_sonando.config(text=a["nombre"] if a else "(nada)",
                                font=("Georgia", 12, "bold") if a is not None and a["prioridad"]
                                else ("Segoe UI", 12))
        self.lbl_badge.config(text="★ PRIORIDAD" if a is not None and a["prioridad"] else "")
        self.lbl_fondo.config(text=("↳ de fondo, atenuado: " + fondo["nombre"]) if fondo else "")
        cola = self.lb_cola_main
        cola.delete(*cola.get_children())
        for cid, color in self.colores.items():
            cola.tag_configure("c%d" % cid, background=tinte(color))
        n = 0
        # 1) lo que ya está en cola esperando su turno (ya le tocaba sonar)
        for txt, prio, cid in self.rep.cola_detalle():
            tags = (["c%d" % cid] if cid in self.colores else []) + (["prio"] if prio else [])
            cola.insert("", "end", iid=str(n), tags=tags,
                        values=("en cola", ("★ " if prio else "") + txt))
            n += 1
        # 2) lo que está programado para más adelante, con su hora
        ahora = dt.datetime.now().replace(second=0, microsecond=0)
        for t, a in self.proximos:
            cid = a["categoria_id"]
            tags = (["c%d" % cid] if cid in self.colores else []) + (
                ["prio"] if a["prioridad"] else [])
            dia = self._cuando(t, ahora)
            hora = fmt_hora(t.strftime("%H:%M"))
            cola.insert("", "end", iid=str(n), tags=tags, values=(
                hora if dia == "hoy" else "%s %s" % (dia[:3] if dia != "mañana" else "mañ.", hora),
                ("★ " if a["prioridad"] else "") + a["nombre"]))
            n += 1

    @staticmethod
    def _mmss(seg):
        seg = int(seg)
        return "%d:%02d" % (seg // 60, seg % 60)

    def actualizar_progreso(self):
        a, pos, dur, _ = self.rep.sonando()
        if a is None:
            frac, txt = 0.0, "0:00 / 0:00"
        elif dur <= 0:                       # duración desconocida (audio muy largo): solo el tiempo
            frac, txt = 0.0, self._mmss(pos)
        else:
            pos = min(pos, dur)
            frac = pos / dur
            txt = "%s / %s" % (self._mmss(pos), self._mmss(dur))
        if txt != self.lbl_tiempo.cget("text"):
            self.lbl_tiempo.config(text=txt)
        espera = self.rep.espera_restante()
        if a is None and espera > 0:
            estado = ("ESPERA  %s  antes del siguiente" % self._mmss(espera + 0.99), "#b9770e")
        elif a is None:
            estado = ("SIN REPRODUCCIÓN", "#888888")
        elif self.rep.pausado:
            estado = ("❚❚  EN PAUSA", "#b9770e")
        else:
            estado = ("▶  SONANDO AHORA", "#1e8449")
        self.lbl_rep.config(text=estado[0], foreground=estado[1])
        self.btn_pausa.config(text="▶  Reanudar" if self.rep.pausado else "❚❚  Pausa")
        color = self.colores.get(a["categoria_id"]) if a is not None else None
        self.dibujar_progreso(frac, color or "#1e64c8")      # la barra toma el color de la categoría

    def dibujar_progreso(self, frac, color, c=None):
        c = c or self.cv_prog
        w = max(40, c.winfo_width())
        c.delete("all")
        c.create_line(8, 8, w - 8, 8, width=6, capstyle="round", fill="#d5dae1")
        if frac > 0:
            x = 8 + frac * (w - 16)
            c.create_line(8, 8, max(x, 9), 8, width=6, capstyle="round", fill=color)
            c.create_oval(x - 6, 2, x + 6, 14, fill="white", outline=color, width=2)

    def animar(self):
        """Bucle rápido (25 cuadros/s): vúmetros y barras de progreso."""
        if not self.root.winfo_viewable():       # ventana oculta: no gastar CPU
            self.root.after(300, self.animar)
            return
        try:
            ahora = time.time()
            dt_ = min(0.2, ahora - self.vu_t)
            self.vu_t = ahora
            n = self.rep.niveles()
            meta_out, meta_org = (VU_MIN, VU_MIN) if n is None else n
            self.vu.actualizar(meta_out, meta_org, dt_, ahora, self.rep.tope)   # la línea sigue al TOPE
            if n is None:
                txt = "Salida: --   Original: --"
            else:
                txt = "Salida: %.0f dB   Original: %.0f dB%s" % (
                    n[0], n[1], "   (limitado)" if n[1] - n[0] > 1.5 else "")
            if txt != self.lbl_db.cget("text"):
                self.lbl_db.config(text=txt)
            self.actualizar_progreso()
        except Exception as e:                   # un fallo en un cuadro no debe parar la animación
            self._avisar_animacion(e)
        try:
            self.animar_app(time.time(), 0.04)
        except Exception as e:
            self._avisar_animacion(e)
        self.root.after(40, self.animar)

    def _avisar_animacion(self, e):
        clave = motivo_error(e)
        if clave != getattr(self, "_ult_error_anim", None):      # una sola línea por tipo de error
            self._ult_error_anim = clave
            self.log("Error al dibujar la pantalla (se sigue funcionando): " + clave)
    def ajustar_divisor(self, e=None):
        """Deja el panel de la cola a la derecha con ~340 px, al tener tamaño real."""
        w = self.panel.winfo_width()
        if w < 700:
            return
        self.panel.unbind("<Configure>")
        try:
            self.panel.sashpos(0, w - 390)
        except tk.TclError:
            pass

    # --- la cola y los próximos audios: qué se puede hacer con cada fila ---
    def _idx_cola(self, fila):
        """Fila de la lista -> posición en rep.cola (None si no es un audio de la cola)."""
        i = fila - len(self.rep.cola_prio)
        return i if 0 <= i < len(self.rep.cola) else None

    def _redibujar_cola(self, seleccionar=None):
        self.ultimo_estado = None
        self.actualizar_estado()
        hijos = self.lb_cola_main.get_children()
        if seleccionar is not None and 0 <= seleccionar < len(hijos):
            self.lb_cola_main.selection_set(hijos[seleccionar])
        self.actualizar_botones_cola()

    def _fila_cola(self, item):
        return self.lb_cola_main.index(item) if item else None

    def cola_press(self, e):
        fila = self._fila_cola(self.lb_cola_main.identify_row(e.y))
        self.cola_origen = fila if fila is not None and self._idx_cola(fila) is not None else None

    def cola_drag(self, e):
        if self.cola_origen is None:
            return
        fila = self._fila_cola(self.lb_cola_main.identify_row(e.y))
        if fila is None:
            return
        i, j = self._idx_cola(self.cola_origen), self._idx_cola(fila)
        if j is None or i == j:
            return
        self.rep.mover_cola(i, j)
        self.cola_origen = fila
        self._redibujar_cola(fila)

    def _tipo_sel(self):
        """(tipo, índice) de la fila elegida: 'prio' (prioridad en espera), 'cola' (le tocaba
        sonar y espera su turno) o 'prog' (programado más adelante). (None, None) si no hay."""
        s = self.lb_cola_main.selection()
        if not s:
            return None, None
        fila = self._fila_cola(s[0])
        n_prio = len(self.rep.cola_prio)
        n_cola = n_prio + len(self.rep.cola)
        if fila < n_prio:
            return "prio", fila
        if fila < n_cola:
            return "cola", fila - n_prio
        j = fila - n_cola
        return ("prog", j) if j < len(self.proximos) else (None, None)

    def actualizar_botones_cola(self):
        tipo, i = self._tipo_sel()

        def estado(boton, activo):
            boton.state(["!disabled"] if activo else ["disabled"])
        n = len(self.rep.cola)
        estado(self.btn_sube, tipo == "cola" and i > 0)
        estado(self.btn_baja, tipo == "cola" and i < n - 1)
        estado(self.btn_ahora, tipo in ("cola", "prog"))
        estado(self.btn_quitar, tipo in ("prio", "cola", "prog"))
        self.btn_quitar.config(text="Saltar" if tipo == "prog" else "Quitar")
        self.lbl_ayuda_cola.config(text={
            None: "Elige un audio de la lista.",
            "cola": "Ya le tocaba sonar. ▲ ▼ o arrastrar cambian el orden; «Ahora» lo pone ya.",
            "prio": "Prioridad en espera de turno: puedes quitarla de la cola.",
            "prog": "Programado: «Ahora» lo reproduce ya y salta su hora; «Saltar esta vez» "
                    "solo lo omite esa vez."}[tipo])

    def mover_sel(self, d):
        tipo, i = self._tipo_sel()
        if tipo != "cola" or not 0 <= i + d < len(self.rep.cola):
            return
        self.rep.mover_cola(i, i + d)
        self._redibujar_cola(len(self.rep.cola_prio) + i + d)

    def quitar_sel(self):
        tipo, i = self._tipo_sel()
        if tipo == "cola":
            self.rep.quitar_de_cola(i)
        elif tipo == "prio":
            self.rep.cola_prio.pop(i)
        elif tipo == "prog":
            t, a = self.proximos[i]
            self.omitir(a["id"], t)
            self.log("Se omitió una vez '%s' de las %s." % (a["nombre"], fmt_hora(t.strftime("%H:%M"))))
        else:
            return
        self._redibujar_cola()

    def ahora_sel(self):
        tipo, i = self._tipo_sel()
        if tipo == "cola":
            self.rep.adelantar(i)
        elif tipo == "prog":
            t, a = self.proximos[i]
            self.omitir(a["id"], t)               # ya suena: no vuelve a sonar a su hora
            self.rep.agregar(a)
            self.log("Adelantado: %s (estaba para las %s)." % (a["nombre"], fmt_hora(t.strftime("%H:%M"))))
        else:
            return
        self._redibujar_cola()

    def omitir(self, aid, t):
        """No lanzar el audio 'aid' en el minuto 't' (solo esa vez)."""
        self.omitidas.add((aid, t.strftime("%Y-%m-%d %H:%M")))
        self._guardar_omitidas()
        self.calcular_proximo()

    def _guardar_omitidas(self):
        ayer = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        self.omitidas = {o for o in self.omitidas if o[1] >= ayer}
        self.set_cfg_lista("omitidas", sorted([a, b] for a, b in self.omitidas))

    # --- la app que se sigue en la pantalla principal ---
    def seguir_app(self, clave):
        """Muestra (o deja de mostrar) una app de la pestaña Apps en la pantalla principal."""
        clave = clave or ""
        if clave == self.app_clave and (clave == "") == (not self.fr_app.winfo_ismapped()):
            return
        self.app_clave = clave
        self.db.set_cfg("app_seguir", clave)
        self.app_meter = self.app_vol = None
        self.app_pid = None
        self.app_t_lento = 0.0
        self.app_tit_prev = None
        if clave:
            self.fr_app.pack(fill="x", before=self.fr_abajo, pady=(0, 6))
            self.lbl_app_nombre.config(text=clave.rsplit(".", 1)[0])
            self.lbl_app_icono.config(image="")
        else:
            self.fr_app.pack_forget()

    def _buscar_sesion_app(self):
        """Cada segundo: localiza la sesión de audio de la app, su título y su canción."""
        clave = self.app_clave
        mejor, mejor_pico = None, -1.0
        if AudioUtilities is not None and IAudioMeterInformation is not None:
            try:
                for s in AudioUtilities.GetAllSessions():
                    try:
                        if (s.Process is None or s.ProcessId == os.getpid()
                                or s.Process.name().lower() != clave):
                            continue
                        m = s._ctl.QueryInterface(IAudioMeterInformation)
                        pico = m.GetPeakValue()
                        if mejor is None or pico > mejor_pico:
                            mejor, mejor_pico = (s, m), pico
                    except Exception:
                        continue
            except Exception:
                pass
        ruta = None
        if mejor:
            s, m = mejor
            self.app_meter, self.app_vol, self.app_pid = m, s.SimpleAudioVolume, s.ProcessId
            try:
                ruta = s.Process.exe()
            except Exception:
                ruta = None
        else:
            self.app_meter = self.app_vol = self.app_pid = None
        if not ruta:
            for r in self.cfg_lista("apps_manuales"):
                if os.path.basename(r).lower() == clave:
                    ruta = r
        nombre = (descripcion_exe(ruta) if ruta else None) or clave.rsplit(".", 1)[0]
        self.lbl_app_nombre.config(text=nombre)
        if ruta:
            if ruta not in self.iconos_apps:
                self.iconos_apps[ruta] = icono_exe(ruta, 16)
            img = self.iconos_apps.get(ruta)
            if img:
                self.lbl_app_icono.config(image=img)
        # canción: Windows 10/11 la entrega con su duración; si no, se limpia el título de la ventana
        base = clave.rsplit(".", 1)[0]
        smtc = None
        if self.lector is not None:
            cand = [d for d in self.lector.datos if base in d["app"].lower()]
            if cand:
                smtc = max(cand, key=lambda d: d["suena"])
        self.app_smtc = smtc
        if smtc and smtc["titulo"]:
            titulo, artista = smtc["titulo"], smtc["artista"]
        else:
            titulo = limpiar_titulo(titulo_ventana(self.app_pid) if self.app_pid else "", [nombre])
            artista = ""
        if titulo != self.app_tit_prev:          # canción nueva: el cronómetro vuelve a cero
            self.app_tit_prev, self.app_t0 = titulo, time.time()
        self.app_titulo, self.app_artista = titulo, artista

    def animar_app(self, ahora, dt_):
        if not self.app_clave:
            return
        if ahora - self.app_t_lento > 1.0:
            self.app_t_lento = ahora
            self._buscar_sesion_app()
        pre, vol = VU_MIN, 1.0
        if self.app_meter is not None:
            try:
                p = self.app_meter.GetPeakValue()
                pre = 20.0 * math.log10(p) if p > 1e-5 else VU_MIN
                vol = self.app_vol.GetMasterVolume()
            except Exception:
                self.app_meter = self.app_vol = None
        out = pre + (20.0 * math.log10(vol) if vol > 0.001 else -99.0)   # el medidor de Windows lee antes del volumen
        # línea de tope: cuánto bajó su volumen respecto al original (se ve el damper funcionando)
        orig = next((o for p, n, _, o in self.rep.sesiones if p == self.app_pid), None)
        if orig is None:                      # (otra sesión del mismo programa)
            orig = next((o for _, n, _, o in self.rep.sesiones
                         if n.lower() == self.app_clave and o > 0.001), None)
        tope, color, nota = 0.0, "#ffffff", "Volumen %d %%" % round(vol * 100)
        if orig and self.rep.duck_o > 0 and orig > 0.001:
            tope = 20.0 * math.log10(max(vol, 1e-4) / orig)
            if tope < -0.5:
                color, nota = "#ff9f43", "ATENUADA a %d %% de su volumen (suena un audio de Audiomático)" % round(
                    vol / orig * 100)
        elif self.app_clave in set(self.cfg_lista("apps_excluir")):
            nota += "  ·  no se atenúa (marcada «No»)"
        self.vu_app.actualizar(max(out, VU_MIN), max(pre, VU_MIN), dt_, ahora, tope, color)
        if nota != self.lbl_app_nota.cget("text"):
            self.lbl_app_nota.config(text=nota, foreground="#d35400" if color != "#ffffff" else "#666666")
        # estado, canción y línea de reproducción
        smtc = self.app_smtc if self.app_meter is not None or self.app_smtc else None
        suena = self.app_meter is not None and pre > VU_MIN + 6
        estado = ("Sin audio", "#888888") if self.app_meter is None else (
            ("Sonando", "#1e8449") if suena else ("En silencio", "#888888"))
        if smtc and not smtc["suena"] and self.app_meter is not None and not suena:
            estado = ("En pausa", "#b9770e")
        if estado[0] != self.lbl_app_estado.cget("text"):
            self.lbl_app_estado.config(text=estado[0], foreground=estado[1])
        cancion = self.app_titulo or ("(sin título)" if self.app_meter is not None else "")
        if len(cancion) > 46:
            cancion = cancion[:45] + "…"
        if self.app_artista:
            cancion = ("%s — %s" % (cancion, self.app_artista))[:60]
        if cancion != self.lbl_app_cancion.cget("text"):
            self.lbl_app_cancion.config(text=cancion)
        if smtc and smtc["dur"] > 0:
            pos = smtc["pos"] + ((ahora - smtc["t"]) if smtc["suena"] else 0.0)
            pos = min(pos, smtc["dur"])
            frac, txt = pos / smtc["dur"], "%s / %s" % (self._mmss(pos), self._mmss(smtc["dur"]))
        elif self.app_titulo:
            frac, txt = 0.0, self._mmss(ahora - self.app_t0)      # sin duración: tiempo transcurrido
        else:
            frac, txt = 0.0, ""
        if txt != self.lbl_app_tiempo.cget("text"):
            self.lbl_app_tiempo.config(text=txt)
        self.dibujar_progreso(frac, "#d35400" if color != "#ffffff" else "#1e64c8", self.cv_app_prog)

    # --- próximo audio programado ---
    @staticmethod
    def _cuando(t, base):
        """'hoy', 'mañana' o el día de la semana, para un instante t."""
        dias = (t.date() - base.date()).days
        return "hoy" if dias == 0 else ("mañana" if dias == 1 else DIAS[t.weekday()])

    def calcular_proximo(self):
        """Calcula los próximos audios programados (con su hora) para la cola y la
        etiqueta de arriba. Mira hasta 4 días adelante y guarda hasta 15 emisiones."""
        base = dt.datetime.now().replace(second=0, microsecond=0)
        activos = self.db.q("SELECT * FROM audios WHERE activo=1")
        self.proximos = [
            (t, a) for t, a in proximas_emisiones(activos, base, 4 * 24 * 60, 15 + len(self.omitidas))
            if (a["id"], t.strftime("%Y-%m-%d %H:%M")) not in self.omitidas][:15]
        if not self.proximos:
            self.lbl_proximo.config(text="" if not activos else
                                    "Próximo: nada en los próximos 4 días")
            return
        t0 = self.proximos[0][0]
        mismos = [a for t, a in self.proximos if t == t0]
        extra = " (+%d)" % (len(mismos) - 1) if len(mismos) > 1 else ""
        self.lbl_proximo.config(text="Próximo: %s %s – %s%s%s" % (
            self._cuando(t0, base), fmt_hora(t0.strftime("%H:%M")), mismos[0]["nombre"][:26],
            extra, "  (detenido)" if self.manual else ""))

    def pausar_reanudar(self):
        self.rep.pausar_reanudar()
        self.actualizar_progreso()

    def vaciar_todo(self):
        self.rep.vaciar_cola()
        self.ultimo_estado = None

    # --- fader VOLUMEN: el volumen propio de Audiomático en el Mezclador de Windows ---
    def _txt_vol(self, pct, db):
        return "%d%%\n%s" % (pct, "silencio" if pct <= 0 else "%.1f dB" % db)

    def _soltar_fader_volumen(self, e=None):
        self._fader_arrastre = False
        self.db.set_cfg("volumen_app", "%.0f" % self.v_vol.get())   # se recuerda entre aperturas

    def cambiar_volumen(self, valor):
        pct = float(valor)
        self.vol_win.poner(pct)
        self.lbl_vol.config(text=self._txt_vol(
            pct, 20.0 * math.log10(pct / 100.0) if pct > 0 else -99.0))

    def sincronizar_volumen_windows(self):
        """Mantiene el fader igual al control de Audiomático en el Mezclador de Windows
        (por si lo mueven desde allí). La sesión aparece cuando el programa abre el audio."""
        v = self.vol_win.leer()
        if v is None:
            self.sc_vol.state(["disabled"])
            self.lbl_vol.config(text="buscando…")
        else:
            self.sc_vol.state(["!disabled"])
            if not getattr(self, "_vol_aplicado", False):
                self._vol_aplicado = True       # primera vez: se recupera el volumen guardado
                guardado = self.db.cfg("volumen_app", "")
                if guardado:
                    self.vol_win.poner(float(guardado))
                    v = self.vol_win.leer() or v
            if not self._fader_arrastre:
                self.v_vol.set(v[0])
                self.lbl_vol.config(text=self._txt_vol(v[0], v[1]))
        self.root.after(1000, self.sincronizar_volumen_windows)

    # --- fader TOPE: nivel máximo (en dB) de los audios, en vivo ---
    def cambiar_tope(self, valor):
        db = float(valor)
        self.lbl_tope.config(text="%.0f dB" % db)
        if hasattr(self, "rep"):
            self.rep.tope = db

    # --- configuración del reproductor ---
    MODOS = ["Atenuarlo y mezclar la prioridad encima", "Pausarlo y reanudarlo después"]

    @staticmethod
    def _num(txt, defecto, lo, hi):
        try:
            return max(lo, min(hi, float(str(txt).replace(",", "."))))
        except ValueError:
            return defecto

    def aplicar_config(self):
        r, cfg = self.rep, self.db.cfg
        r.factor_duck = self._num(cfg("bajar_pct", "50"), 50, 0, 90) / 100.0
        r.rampa = self._num(cfg("damper_rampa", "1.5"), 1.5, 0, 10)
        r.espera = self._num(cfg("damper_espera", "30"), 30, 0, 300)
        r.modo_pausa = cfg("damper_modo", "mezclar") == "pausar"
        r.fundido = self._num(cfg("fundido", "0.5"), 0.5, 0, 5)
        r.tope = self.v_tope.get()
        activa, _, techo = self.cfg_norm()
        r.usar_norm, r.techo = activa, techo
        r.apps_excluir = set(self.cfg_lista("apps_excluir"))

    def guardar_damper(self):
        s = self.db.set_cfg
        s("bajar_pct", int(self._num(self.v_pct.get(), 50, 0, 90)))
        s("damper_rampa", self._num(self.v_rampa.get(), 1.5, 0, 10))
        s("damper_espera", self._num(self.v_espera.get(), 30, 0, 300))
        s("damper_modo", "pausar" if self.v_modo.get() == self.MODOS[1] else "mezclar")
        s("fundido", self._num(self.v_fundido.get(), 0.5, 0, 5))
        self.aplicar_config()
        messagebox.showinfo("Guardado", "Configuración de prioridad y damper guardada.")

    def bucle_audio(self):
        """Mueve el audio (fundidos, damper, siguiente de la cola) aunque la ventana esté oculta."""
        ahora = time.time()
        dt_ = min(0.25, ahora - self.t_audio)
        self.t_audio = ahora
        try:
            self.rep.paso(dt_)
        except Exception as e:
            self.log("Error en el reproductor: %s" % e)
        soltar = getattr(self, "soltar", None)
        if soltar is not None and soltar.buzon:
            for lote in soltar.recoger():
                self.al_soltar(lote)
        self.root.after(40, self.bucle_audio)

    # ---------------- audios ----------------
    def refrescar_audios(self, proximo=True):
        if proximo:
            self.calcular_proximo()
        for i in self.tree.get_children():
            self.tree.delete(i)
        filtro = self.v_filtro.get()
        sql = ("SELECT a.*, c.nombre AS cat FROM audios a "
               "LEFT JOIN categorias c ON c.id=a.categoria_id")
        params = ()
        if filtro != "Todas":
            sql += " WHERE c.nombre=?"
            params = (filtro,)
        buscar = self.v_buscar.get().strip().lower()
        for cid, color in self.colores.items():
            self.tree.tag_configure("c%d" % cid, background=tinte(color))
        sql += " ORDER BY a.nombre"
        for a in self.db.q(sql, params):
            if buscar and buscar not in a["nombre"].lower():
                continue
            dias = [int(d) for d in a["dias"].split(",") if d != ""]
            txt_dias = "Todos" if len(dias) == 7 else ", ".join(DIAS[d] for d in dias)
            partes = []
            if a["horas"]:
                partes.append(fmt_horas(a["horas"]))
            if a["minutos"]:
                partes.append("min %s (%s-%s%s)" % (
                    a["minutos"].replace(",", "/"), fmt_h(a["hora_desde"]),
                    fmt_h(a["hora_hasta"]), "" if FORMATO_12H else "h"))
            if a["intervalo"]:
                partes.append("cada %d min %s-%s" % (
                    a["intervalo"], fmt_hora(a["int_desde"]), fmt_hora(a["int_hasta"])))
            tags = []
            if a["categoria_id"] in self.colores:
                tags.append("c%d" % a["categoria_id"])
            if a["prioridad"]:
                tags.append("prio")
            if not a["activo"]:
                tags.append("inactivo")
            if a["problema"]:
                tags.append("problema")
            self.tree.insert("", "end", iid=str(a["id"]), tags=tags, values=(
                a["nombre"], a["cat"] or "-", "★ SI" if a["prioridad"] else "",
                txt_dias, " | ".join(partes), a["fecha_fin"] or "Indefinido",
                "⚠ Error" if a["problema"] else ("Activo" if a["activo"] else "Inactivo")))
        if self.db.q("SELECT 1 FROM audios LIMIT 1"):
            self.lbl_arrastra.place_forget()
        else:                                    # lista vacía: indica que se puede arrastrar
            self.lbl_arrastra.place(relx=0.5, rely=0.42, anchor="center")

    # ---------------- nivelación de volumen ----------------
    def cfg_norm(self):
        """(activa, nivel base al que se guardan las copias, tope de picos)."""
        try:
            techo = float(self.db.cfg("norm_techo", "-1"))
        except ValueError:
            techo = -1.0
        return self.db.cfg("norm_activa", "1") == "1", NIVEL_BASE, techo

    def _borrar_norm(self, a):
        r = a["ruta_norm"]
        if r and os.path.dirname(r) == CARPETA_AUDIOS:
            try:
                os.remove(r)
            except OSError:
                pass

    def nivelar_audio(self, aid, forzar=False):
        activa, obj, techo = self.cfg_norm()
        filas = self.db.q("SELECT * FROM audios WHERE id=?", (aid,))
        if not filas:
            return
        a = filas[0]
        if not activa:
            return
        # nivel_db = -99 marca "falló la última vez": se reintenta (por ejemplo tras instalar
        # una versión con mejores decodificadores) en cada arranque
        if (not forzar and a["objetivo_db"] == obj and a["nivel_db"] not in (None, -99)):
            return
        self._borrar_norm(a)
        destino = os.path.join(CARPETA_AUDIOS, "n_%d_%d.wav" % (aid, int(time.time() * 1000)))
        try:
            nivel, g, dur, quitado = nivelar_archivo(
                a["ruta"], destino, obj, techo, self.db.cfg("recortar_silencio", "0") == "1")
        except Exception as e:            # incluye MemoryError (su texto viene vacío)
            motivo = motivo_error(e)
            self.log("No se pudo nivelar '%s' (suena igual, sin nivelar): %s" % (a["nombre"], motivo))
            if os.path.exists(destino):
                os.remove(destino)
            # nivel_db=-99 marca "no analizable": no se reintenta en cada arranque
            self.db.x("UPDATE audios SET ruta_norm='',nivel_db=-99,ganancia_db=0,"
                      "objetivo_db=?,duracion=? WHERE id=?",
                      (obj, duracion_estimada(a["ruta"]), aid))
            if not motivo.startswith("es muy largo"):     # un audio largo suena bien, solo sin nivelar
                self.marcar_problema(aid, motivo)
            return
        self.db.x("UPDATE audios SET ruta_norm=?,nivel_db=?,ganancia_db=?,objetivo_db=?,duracion=? "
                  "WHERE id=?", (destino, nivel, g, obj, dur, aid))
        self.log("Nivelado '%s': %.1f dB -> %.1f dB (ajuste %+.1f dB)%s"
                 % (a["nombre"], nivel, nivel + g, g,
                    "; recortados %.1f s de silencio" % quitado if quitado else ""))
        if a["problema"]:
            self.limpiar_problema(aid)

    def nivelar_pendientes(self):
        """Nivela de a un audio por vez para no congelar la ventana."""
        if not self.cola_nivelar:
            self._nivelando = False
            self.refrescar_audios(False)
            return
        self._nivelando = True
        self.nivelar_audio(self.cola_nivelar.pop(0))
        self.root.after(50, self.nivelar_pendientes)

    def programar_nivelacion(self, forzar=False):
        activa, obj, _ = self.cfg_norm()
        self.rep.usar_norm = activa
        if not activa:
            self.refrescar_audios(False)
            return
        if forzar:
            ids = [r["id"] for r in self.db.q("SELECT id FROM audios")]
        else:
            ids = [r["id"] for r in self.db.q(
                "SELECT id FROM audios WHERE nivel_db IS NULL OR nivel_db=-99 "
                "OR objetivo_db IS NULL OR objetivo_db<>?", (obj,))]
        if forzar:
            self.db.x("UPDATE audios SET objetivo_db=NULL")
        # sin repetir: solo se añaden los que aún no están en la cola, y no se abre
        # una segunda cadena de trabajo si ya hay una en marcha
        self.cola_nivelar += [i for i in ids if i not in self.cola_nivelar]
        if not self._nivelando:
            self.nivelar_pendientes()

    def guardar_nivelacion(self):
        try:
            techo = float(self.v_techo.get())
        except ValueError:
            messagebox.showwarning("Formato", "Escribe los decibelios como número (ej. -1).")
            return
        if not (-12 <= techo <= -0.1):
            messagebox.showwarning("Valores fuera de rango",
                                   "El tope de picos va entre -12 y -0.1 dB.")
            return
        self.db.set_cfg("norm_activa", self.v_norm.get())
        self.db.set_cfg("norm_techo", techo)
        self.rep.techo = techo
        self.programar_nivelacion(forzar=True)
        messagebox.showinfo("Nivelación", "Configuración guardada. Se están reanalizando los "
                            "audios (el avance queda en el registro de eventos).")

    def sel_audio(self):
        s = self.tree.selection()
        if not s:
            messagebox.showinfo("Audios", "Selecciona un audio de la lista.")
            return None
        return self.db.q("SELECT * FROM audios WHERE id=?", (int(s[0]),))[0]

    def nuevo_audio(self, archivo=None):
        DialogoAudio(self, archivo=archivo)

    # --- arrastrar archivos a la ventana ---
    def activar_soltar(self):
        try:
            self.soltar = SoltarArchivos(self.root, self.al_soltar)
        except Exception as e:
            self.soltar = None
            self.log("Aviso: no se pudo activar arrastrar y soltar (%s: %s)." % (
                type(e).__name__, e))

    def al_soltar(self, rutas):
        """Archivos o carpetas soltados sobre la ventana: abre 'Añadir audio' para cada uno."""
        audios, ignorados = [], 0
        for r in rutas:
            if os.path.isdir(r):
                for carpeta, _, nombres in os.walk(r):
                    for n in sorted(nombres):
                        if n.lower().endswith(EXTENSIONES):
                            audios.append(os.path.join(carpeta, n))
            elif r.lower().endswith(EXTENSIONES):
                audios.append(r)
            else:
                ignorados += 1
        vistos = []
        for a in audios:                         # sin repetir el mismo archivo soltado dos veces
            if os.path.normcase(a) not in [os.path.normcase(v) for v in vistos]:
                vistos.append(a)
        if ignorados or not vistos:
            messagebox.showinfo("Arrastrar audios",
                                ("Se ignoraron %d archivo(s). " % ignorados if ignorados else "")
                                + ("Solo se aceptan MP3, WAV, OGG o FLAC." if not vistos else ""),
                                parent=self.root)
        self.mostrar()
        self.pend_altas = getattr(self, "pend_altas", []) + vistos
        self._siguiente_alta()

    def _siguiente_alta(self):
        if getattr(self, "dlg_alta", None) is not None or not getattr(self, "pend_altas", None):
            return
        archivo = self.pend_altas.pop(0)
        dlg = DialogoAudio(self, archivo=archivo)
        self.dlg_alta = dlg

        def cerrado(e):
            if e.widget is dlg:
                self.dlg_alta = None
                self.root.after(150, self._siguiente_alta)
        dlg.bind("<Destroy>", cerrado)

    def buscar_duplicado(self, ruta, nombre, excluir=None):
        """(aviso o None, huella). Detecta el mismo archivo o el mismo nombre."""
        try:
            h = huella_archivo(ruta)
        except OSError:
            h = ""
        for a in self.db.q("SELECT id, nombre, ruta, huella FROM audios"):
            if a["id"] == excluir:
                continue
            if h:
                ha = a["huella"]
                if not ha:                       # audios antiguos: se calcula y se guarda
                    try:
                        ha = huella_archivo(a["ruta"])
                        self.db.x("UPDATE audios SET huella=? WHERE id=?", (ha, a["id"]))
                    except OSError:
                        ha = ""
                if ha == h:
                    return "Este archivo es idéntico al del audio «%s»." % a["nombre"], h
            if a["nombre"].strip().lower() == nombre.strip().lower():
                return "Ya existe un audio llamado «%s»." % a["nombre"], h
        return None, h

    # --- audios con problemas (archivo que falta, que no se puede leer o no suena) ---
    MSG_FALTA = "falta el archivo de audio"

    def marcar_problema(self, aid, motivo):
        self.db.x("UPDATE audios SET problema=? WHERE id=?", (motivo[:300], aid))
        self.refrescar_audios(False)
        self.actualizar_alerta()

    def limpiar_problema(self, aid):
        self.db.x("UPDATE audios SET problema='' WHERE id=?", (aid,))
        self.refrescar_audios(False)
        self.actualizar_alerta()

    def _audio_sono(self, a):
        """Un audio empezó a sonar bien: si tenía un aviso de problema, se quita."""
        r = self.db.q("SELECT problema FROM audios WHERE id=?", (a["id"],))
        if r and r[0]["problema"]:
            self.limpiar_problema(a["id"])

    def verificar_archivos(self):
        """Marca los audios cuyo archivo ya no existe (y quita la marca si reaparece)."""
        cambio = False
        for a in self.db.q("SELECT id, ruta, ruta_norm, problema FROM audios"):
            existe = os.path.exists(a["ruta"]) or bool(a["ruta_norm"] and
                                                       os.path.exists(a["ruta_norm"]))
            if not existe and a["problema"] != self.MSG_FALTA:
                self.db.x("UPDATE audios SET problema=? WHERE id=?", (self.MSG_FALTA, a["id"]))
                self.log("Aviso: falta el archivo del audio #%d." % a["id"])
                cambio = True
            elif existe and a["problema"] == self.MSG_FALTA:
                self.db.x("UPDATE audios SET problema='' WHERE id=?", (a["id"],))
                cambio = True
        if cambio:
            self.refrescar_audios(False)
        self.actualizar_alerta()
        self.root.after(3600 * 1000, self.verificar_archivos)       # y cada hora

    def actualizar_alerta(self):
        n = len(self.db.q("SELECT 1 FROM audios WHERE problema<>''"))
        if n:
            self.lbl_alerta.config(text="⚠ %d con problemas · Ver" % n)
            self.lbl_alerta.pack(side="right", padx=(0, 14))
        else:
            self.lbl_alerta.pack_forget()

    def ver_problemas(self):
        filas = self.db.q("SELECT id, nombre, problema FROM audios WHERE problema<>'' "
                          "ORDER BY nombre")
        v = tk.Toplevel(self.root)
        v.title("Audios con problemas")
        v.geometry("720x320")
        v.transient(self.root)
        f = ttk.Frame(v, padding=8)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Estos audios no se pudieron leer o ya no existen. El aviso se quita "
                          "solo cuando el audio vuelve a sonar bien.",
                  wraplength=690, foreground="#555555").pack(anchor="w", pady=(0, 6))
        t = ttk.Treeview(f, columns=("a", "m"), show="headings", height=8)
        t.heading("a", text="Audio")
        t.heading("m", text="Motivo")
        t.column("a", width=200)
        t.column("m", width=480)
        for r in filas:
            t.insert("", "end", iid=str(r["id"]), values=(r["nombre"], r["problema"]))
        t.pack(fill="both", expand=True)

        def quitar():
            for r in filas:
                self.db.x("UPDATE audios SET problema='' WHERE id=?", (r["id"],))
            self.refrescar_audios(False)
            self.actualizar_alerta()
            v.destroy()
        bt = ttk.Frame(f)
        bt.pack(fill="x", pady=(8, 0))
        ttk.Button(bt, text="Quitar los avisos", command=quitar).pack(side="left")
        ttk.Button(bt, text="Cerrar", command=v.destroy).pack(side="right")

    # --- modo manual: detiene la programación sin cerrar el programa ---
    def alternar_manual(self):
        if not self.manual and not messagebox.askyesno(
                "Modo manual", "Los audios programados NO sonarán hasta que vuelvas a activar "
                "la programación.\n\n¿Pasar a modo manual?", default="no", parent=self.root):
            return
        self.manual = not self.manual
        self.db.set_cfg("modo_manual", "1" if self.manual else "0")
        self.log("Modo manual %s." % ("ACTIVADO: programación detenida" if self.manual
                                      else "desactivado: programación activa"))
        self.mostrar_manual()
        self.calcular_proximo()

    def mostrar_manual(self):
        if self.manual:
            self.btn_manual.config(text="Reanudar programación")
            self.lbl_manual.config(text="MODO MANUAL")
            self.lbl_manual.pack(side="right", padx=(0, 14))
        else:
            self.btn_manual.config(text="Modo manual")
            self.lbl_manual.pack_forget()

    # --- conflictos de programación ---
    def conflictos(self, dias=7):
        """[(ids de audios implicados, texto)] con los choques de la programación."""
        base = dt.datetime.now().replace(second=0, microsecond=0)
        audios = self.db.q("SELECT * FROM audios WHERE activo=1")
        dur = {a["id"]: (a["duracion"] or duracion_estimada(a["ruta"])) for a in audios}
        ems = proximas_emisiones(audios, base, dias * 1440, 20000)
        res, vistos = [], set()

        def unico(clave):
            if clave in vistos:
                return False
            vistos.add(clave)
            return True

        def cuando(t):
            return "%s %s" % (self._cuando(t, base).capitalize(), fmt_hora(t.strftime("%H:%M")))

        # 1) dos audios del mismo tipo en el mismo minuto
        por_min = {}
        for t, a in ems:
            por_min.setdefault((t, bool(a["prioridad"])), []).append(a)
        for (t, prio), lista in sorted(por_min.items()):
            if len(lista) > 1:
                ids = tuple(sorted(a["id"] for a in lista))
                if unico(("mismo", ids)):
                    nombres = " y ".join("«%s»" % a["nombre"] for a in lista)
                    res.append((set(ids), "%s: %s caen en el mismo minuto; uno esperará en la "
                                          "cola." % (cuando(t), nombres)))
        # 2) un audio dura más que el tiempo hasta su siguiente repetición
        ult = {}
        for t, a in ems:
            if a["id"] in ult and a["id"] in dur:
                gap = (t - ult[a["id"]]).total_seconds()
                if 0 < gap < dur[a["id"]] and unico(("solo", a["id"])):
                    res.append(({a["id"]}, "«%s» dura %s pero se repite cada %s: se solaparía "
                                           "consigo mismo." % (a["nombre"], self._mmss(dur[a["id"]]),
                                                               self._mmss(gap))))
            ult[a["id"]] = t
        # 3) un audio normal empieza tarde porque el anterior todavía no terminó
        fin_prev, prev = None, None
        for t, a in sorted((x for x in ems if not x[1]["prioridad"]), key=lambda x: x[0]):
            if prev is not None and fin_prev and t < fin_prev and prev["id"] != a["id"]:
                if unico(("tarde", prev["id"], a["id"])):
                    res.append(({prev["id"], a["id"]},
                                "%s: «%s» empezará tarde porque «%s» dura %s y no habrá terminado."
                                % (cuando(t), a["nombre"], prev["nombre"], self._mmss(dur[prev["id"]]))))
            ini = max(t, fin_prev) if fin_prev else t
            fin_prev, prev = ini + dt.timedelta(seconds=dur.get(a["id"], 0)), a
        # 4) cómo está configurado cada audio
        hoy = base.date().isoformat()
        con_emision = {a["id"] for _, a in ems}
        for a in audios:
            if a["id"] not in con_emision:
                if a["fecha_fin"] and a["fecha_fin"] < hoy:
                    motivo = "su vigencia terminó el %s" % a["fecha_fin"]
                else:
                    motivo = "no le toca sonar en los próximos %d días (revisa los días y las horas)" % dias
                res.append(({a["id"]}, "«%s» está activo pero %s." % (a["nombre"], motivo)))
        for a in audios:                    # horas exactas que ya cubre otra regla del mismo audio
            exactas = {hhmm_a_min(h) for h in a["horas"].split(",") if h}
            otras = set()
            mins = [int(m) for m in a["minutos"].split(",") if m != ""]
            for hora in range(a["hora_desde"], a["hora_hasta"] + 1):
                otras.update(hora * 60 + m for m in mins)
            n = a["intervalo"] or 0
            if n > 0 and a["int_desde"] and a["int_hasta"]:
                otras.update(range(hhmm_a_min(a["int_desde"]), hhmm_a_min(a["int_hasta"]) + 1, n))
            rep = sorted(exactas & otras)
            if rep:
                res.append(({a["id"]}, "«%s»: la hora %s ya la cubre otra de sus reglas; sonará "
                            "una sola vez, no se repite." % (a["nombre"], ", ".join(
                                fmt_hora("%02d:%02d" % divmod(m, 60)) for m in rep[:4]))))
        iguales = {}                        # mismo archivo y misma programación
        for a in audios:
            if a["huella"]:
                clave = (a["huella"], a["dias"], frozenset(minutos_programados(a)))
                iguales.setdefault(clave, []).append(a)
        for lista in iguales.values():
            if len(lista) > 1:
                res.append(({a["id"] for a in lista}, "%s son el mismo archivo con la misma "
                            "programación: sonarían repetidos." %
                            " y ".join("«%s»" % a["nombre"] for a in lista)))
        return res

    def ver_conflictos(self):
        res = self.conflictos()
        v = tk.Toplevel(self.root)
        v.title("Revisión de la programación")
        v.geometry("760x360")
        v.transient(self.root)
        f = ttk.Frame(v, padding=8)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Revisión de los próximos 7 días (solo audios activos).",
                  foreground="#555555").pack(anchor="w", pady=(0, 6))
        t = tk.Text(f, wrap="word", font=("Segoe UI", 10), height=12)
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        if res:
            t.insert("end", "Se encontraron %d aviso(s):\n\n" % len(res))
            for _, txt in res[:60]:
                t.insert("end", "• %s\n\n" % txt)
        else:
            t.insert("end", "No se encontraron conflictos: ningún audio se pisa con otro.")
        t.config(state="disabled")
        ttk.Button(v, text="Cerrar", command=v.destroy).pack(pady=(0, 8))

    @staticmethod
    def _mmss(seg):
        seg = int(round(seg))
        return "%d:%02d" % (seg // 60, seg % 60)

    def editar_audio(self):
        a = self.sel_audio()
        if a:
            DialogoAudio(self, a)

    def eliminar_audio(self):
        a = self.sel_audio()
        if a and messagebox.askyesno("Eliminar", "¿Eliminar '%s'?" % a["nombre"]):
            self.rep.quitar_audio(a["id"])           # fuera de la cola (y se detiene si suena)
            self.db.x("DELETE FROM audios WHERE id=?", (a["id"],))
            self.borrar_archivos_audio(a)
            self.refrescar_audios()
            self.actualizar_alerta()

    def borrar_archivos_audio(self, a, conservar=()):
        """Borra las copias propias de un audio: la original copiada, la nivelada y las de
        la caché de conversión. Nunca toca archivos fuera de la carpeta del programa ni
        uno que use otro audio."""
        carpeta = os.path.abspath(CARPETA_AUDIOS)
        ajenas = {os.path.abspath(p) for r in self.db.q("SELECT ruta, ruta_norm FROM audios")
                  for p in (r["ruta"], r["ruta_norm"]) if p}
        ajenas |= {os.path.abspath(p) for p in conservar}
        candidatos = [a["ruta"], a["ruta_norm"]]
        cache = os.path.join(carpeta, "cache")
        if os.path.isdir(cache):
            candidatos += [os.path.join(cache, f) for f in os.listdir(cache)
                           if f.startswith("c_%s_" % a["id"])]
        for r in candidatos:
            try:
                if (r and os.path.dirname(os.path.abspath(r)) in (carpeta, cache)
                        and os.path.abspath(r) not in ajenas and os.path.exists(r)):
                    os.remove(r)
            except OSError:
                pass

    def limpiar_huerfanos(self):
        """Al abrir: borra copias que ya no pertenecen a ningún audio (quedaron de ediciones
        o de errores). Solo toca archivos con los nombres que crea este programa."""
        try:
            carpeta = os.path.abspath(CARPETA_AUDIOS)
            filas = self.db.q("SELECT id, ruta, ruta_norm FROM audios")
            usadas = {os.path.abspath(p) for r in filas for p in (r["ruta"], r["ruta_norm"]) if p}
            ids = {str(r["id"]) for r in filas}
            borrados = 0

            def quitar(ruta):               # un archivo bloqueado no detiene la limpieza
                try:
                    os.remove(ruta)
                    return 1
                except OSError:
                    return 0
            for f in os.listdir(carpeta):
                ruta = os.path.join(carpeta, f)
                if (os.path.isfile(ruta) and os.path.abspath(ruta) not in usadas
                        and (re.match(r"^\d{10,}_", f) or re.match(r"^n_\d+_\d+\.wav$", f))):
                    borrados += quitar(ruta)
            cache = os.path.join(carpeta, "cache")
            if os.path.isdir(cache):
                for f in os.listdir(cache):
                    m = re.match(r"^c_(\d+)_\d+\.wav$", f)
                    if m and m.group(1) not in ids:
                        borrados += quitar(os.path.join(cache, f))
            if borrados:
                self.log("Limpieza: se borraron %d copia(s) de audios que ya no existen." % borrados)
        except OSError:
            pass

    def alternar_audio(self):
        a = self.sel_audio()
        if a:
            self.db.x("UPDATE audios SET activo=? WHERE id=?", (0 if a["activo"] else 1, a["id"]))
            self.refrescar_audios()

    def probar_audio(self):
        a = self.sel_audio()
        if a:
            self.rep.agregar(a)

    # ---------------- categorías ----------------
    def refrescar_categorias(self):
        filas = self.db.q("SELECT id, nombre, color FROM categorias ORDER BY nombre")
        cats = [c["nombre"] for c in filas]
        self.colores = {c["id"]: c["color"] for c in filas}     # categoria_id -> color
        self.lb_cats.delete(0, "end")
        for c in filas:
            self.lb_cats.insert("end", "  " + c["nombre"])
            self.lb_cats.itemconfig("end", background=tinte(c["color"], 0.6),
                                    selectbackground=c["color"])
        self.cb_filtro.config(values=["Todas"] + cats)
        if self.v_filtro.get() not in ["Todas"] + cats:
            self.v_filtro.set("Todas")

    def add_cat(self):
        n = self.v_nueva_cat.get().strip()
        if not n:
            return
        usados = len(self.db.q("SELECT id FROM categorias"))
        try:
            self.db.x("INSERT INTO categorias(nombre,color) VALUES(?,?)",
                      (n, PALETA[usados % len(PALETA)]))
        except sqlite3.IntegrityError:
            messagebox.showinfo("Categorías", "Esa categoría ya existe.")
            return
        self.v_nueva_cat.set("")
        self.refrescar_categorias()

    def color_cat(self):
        n = self.cat_sel()
        if not n:
            return
        r = self.db.q("SELECT id, color FROM categorias WHERE nombre=?", (n,))[0]
        _, nuevo = colorchooser.askcolor(color=r["color"], parent=self.root,
                                         title="Color de la categoría '%s'" % n)
        if nuevo:
            self.db.x("UPDATE categorias SET color=? WHERE id=?", (nuevo, r["id"]))
            self.refrescar_categorias()
            self.refrescar_audios(False)
            self.ultimo_estado = None

    def cat_sel(self):
        s = self.lb_cats.curselection()
        if not s:
            messagebox.showinfo("Categorías", "Selecciona una categoría.")
            return None
        return self.lb_cats.get(s[0]).strip()

    def ren_cat(self):
        n = self.cat_sel()
        if not n:
            return
        nuevo = simpledialog.askstring("Renombrar", "Nuevo nombre:", initialvalue=n,
                                       parent=self.root)
        if nuevo and nuevo.strip():
            try:
                self.db.x("UPDATE categorias SET nombre=? WHERE nombre=?", (nuevo.strip(), n))
            except sqlite3.IntegrityError:
                messagebox.showinfo("Categorías", "Ese nombre ya existe.")
            self.refrescar_categorias()
            self.refrescar_audios()

    def del_cat(self):
        n = self.cat_sel()
        if n and messagebox.askyesno("Eliminar", "¿Eliminar la categoría '%s'?\n"
                                     "Sus audios quedarán sin categoría." % n):
            r = self.db.q("SELECT id FROM categorias WHERE nombre=?", (n,))[0]
            self.db.x("UPDATE audios SET categoria_id=NULL WHERE categoria_id=?", (r["id"],))
            self.db.x("DELETE FROM categorias WHERE id=?", (r["id"],))
            self.refrescar_categorias()
            self.refrescar_audios()

    # ---------------- sistema ----------------
    def cambiar_auto(self):
        try:
            poner_autoinicio(bool(self.v_auto.get()))
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---------------- actualizaciones (GitHub Releases) ----------------
    def comprobar_actualizacion(self, manual=False):
        """Busca en segundo plano (la ventana no se congela) y avisa en el hilo principal."""
        if getattr(self, "_buscando", False):
            return
        self._buscando = True
        resultado = {}

        def trabajo():
            try:
                resultado["info"] = buscar_actualizacion()
            except Exception as e:
                resultado["error"] = "%s: %s" % (type(e).__name__, e)

        threading.Thread(target=trabajo, daemon=True).start()

        def esperar():
            if not resultado:
                self.root.after(300, esperar)
                return
            self._buscando = False
            if "error" in resultado:
                self.log("No se pudo buscar actualizaciones: " + resultado["error"])
                if manual:
                    messagebox.showwarning(
                        "Actualizaciones", "No se pudo comprobar si hay versiones nuevas.\n"
                        "Revisa la conexión a Internet.\n\n" + resultado["error"],
                        parent=self.root)
                return
            self.db.set_cfg("ultima_busqueda", dt.date.today().isoformat())
            info = resultado["info"]
            if info is None:
                self.nueva = None
                self.lbl_actualiza.pack_forget()
                if manual:
                    messagebox.showinfo("Actualizaciones", "Ya tienes la última versión (%s)."
                                        % VERSION, parent=self.root)
                return
            self.nueva = info
            self.log("Hay una versión nueva disponible: %s" % info["version"])
            self.lbl_actualiza.config(text="⬆ Nueva versión %s · Actualizar" % info["version"])
            self.lbl_actualiza.pack(side="right", padx=(0, 14))
            if manual:
                self.ofrecer_actualizacion()
        esperar()

    def busqueda_automatica(self):
        """Una vez al día, como máximo, y solo si está activada en Sistema."""
        if (self.db.cfg("auto_update", "1") == "1"
                and self.db.cfg("ultima_busqueda", "") != dt.date.today().isoformat()):
            self.comprobar_actualizacion(manual=False)
        self.root.after(6 * 3600 * 1000, self.busqueda_automatica)

    def ofrecer_actualizacion(self):
        info = getattr(self, "nueva", None)
        if not info:
            return
        if not info["instalador"] or not info["sha256"]:
            if messagebox.askyesno(
                    "Nueva versión %s" % info["version"],
                    "Hay una versión nueva, pero no se puede instalar automáticamente.\n"
                    "¿Abrir la página de descarga?", parent=self.root):
                webbrowser.open(info["pagina"])
            return
        notas = info["notas"][:500] + ("…" if len(info["notas"]) > 500 else "")
        if not messagebox.askyesno(
                "Nueva versión %s" % info["version"],
                "Tienes la versión %s y hay una nueva: %s.\n\n%s\n\n"
                "Se descargará e instalará ahora. El programa se cerrará y se volverá a abrir "
                "solo; mientras tanto no sonarán los audios programados.\n\n¿Actualizar?"
                % (VERSION, info["version"], notas), parent=self.root):
            return
        self.descargar_e_instalar(info)

    def descargar_e_instalar(self, info):
        carpeta = os.path.join(tempfile.gettempdir(), "Audiomatico_actualizacion")
        os.makedirs(carpeta, exist_ok=True)
        destino = os.path.join(carpeta, "Instalar_Audiomatico_%s.exe" % info["version"])
        ventana = tk.Toplevel(self.root)
        ventana.title("Descargando la actualización")
        ventana.transient(self.root)
        ventana.resizable(False, False)
        ventana.protocol("WM_DELETE_WINDOW", lambda: None)
        ventana.grab_set()
        ttk.Label(ventana, text="Descargando la versión %s…" % info["version"],
                  padding=(16, 14, 16, 4)).pack()
        barra = ttk.Progressbar(ventana, length=320, maximum=100)
        barra.pack(padx=16, pady=(0, 14))
        estado = {"hecho": 0, "total": 0}
        resultado = {}

        def trabajo():
            try:
                descargar_verificado(info["instalador"], destino, info["sha256"],
                                     lambda h, t: estado.update(hecho=h, total=t))
                resultado["ok"] = True
            except Exception as e:
                resultado["error"] = "%s: %s" % (type(e).__name__, e)

        threading.Thread(target=trabajo, daemon=True).start()

        def esperar():
            if estado["total"]:
                barra["value"] = estado["hecho"] * 100.0 / estado["total"]
            if not resultado:
                self.root.after(150, esperar)
                return
            ventana.destroy()
            if "error" in resultado:
                self.log("Falló la descarga de la actualización: " + resultado["error"])
                messagebox.showerror("Actualización", "No se pudo descargar la actualización.\n\n"
                                     + resultado["error"], parent=self.root)
                return
            self.log("Instalando la versión %s…" % info["version"])
            import ctypes     # el instalador pide permisos de administrador (ShellExecute los gestiona)
            r = ctypes.windll.shell32.ShellExecuteW(
                None, "open", destino, "/SILENT /CLOSEAPPLICATIONS", None, 1)
            if r <= 32:
                messagebox.showerror("Actualización", "No se pudo iniciar el instalador "
                                     "(código %d).\nEstá en:\n%s" % (r, destino), parent=self.root)
                return
            self.root.after(1500, lambda: self.salir(False))   # el instalador lo vuelve a abrir
        esperar()

    def cambiar_auto_update(self):
        self.db.set_cfg("auto_update", str(self.v_autoupd.get()))

    def _rotulo_apertura(self):
        self.v_lbl_apertura.set("Hora (%s):" % ("ej. 7:30 AM" if FORMATO_12H else "HH:MM"))

    def cambiar_12h(self):
        global FORMATO_12H
        FORMATO_12H = bool(self.v_12h.get())
        self.db.set_cfg("formato_12h", "1" if FORMATO_12H else "0")
        self._rotulo_apertura()
        self.v_apertura.set(fmt_hora(self.db.cfg("apertura", "")))
        self.refrescar_audios()          # también actualiza "Próximo: ..."

    def programar_apertura(self):
        try:
            h, m = parse_hora(self.v_apertura.get())
            hhmm = "%02d:%02d" % (h, m)
        except ValueError:
            messagebox.showwarning("Formato", "Escribe la hora como %s." % (
                "7:30 AM" if FORMATO_12H else "HH:MM (ej. 07:30)"))
            return
        ok, salida = tarea_programada(hhmm)
        if ok:
            self.db.set_cfg("apertura", hhmm)
            self.db.set_cfg("tarea_version", VERSION)
            self.v_apertura.set(fmt_hora(hhmm))
            messagebox.showinfo("Listo", "El programa se abrirá todos los días a las %s."
                                % fmt_hora(hhmm))
        else:
            messagebox.showerror("No se pudo programar", salida)

    def quitar_apertura(self):
        ok, salida = tarea_programada(None)
        self.db.set_cfg("apertura", "")
        self.v_apertura.set("")
        messagebox.showinfo("Apertura", "Apertura automática quitada." if ok else salida)

    # ---------------- bandeja / cierre ----------------
    def crear_bandeja(self):
        if pystray is None:
            return
        try:
            import icono
            img = icono.dibujar(64)
            menu = pystray.Menu(
                pystray.MenuItem("Abrir", lambda i, it: self.cmd.put("mostrar"), default=True),
                pystray.MenuItem("Salir del programa", lambda i, it: self.cmd.put("salir")))
            self.icono = pystray.Icon(APP, img, NOMBRE, menu)
            self.icono.run_detached()
        except Exception:
            self.icono = None

    def ocultar(self):
        if self.icono:
            self.root.withdraw()
        else:
            self.root.iconify()

    def mostrar(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def marcar_alt_f4(self, event=None):
        self.alt_f4 = True
        self.root.after(800, lambda: setattr(self, "alt_f4", False))

    def al_cerrar(self):
        if self.alt_f4:
            self.salir(True)
            return
        self.ocultar()
        if self.icono and not self.aviso_dado:
            self.aviso_dado = True
            try:
                self.icono.notify("Sigue funcionando en segundo plano.",
                                  NOMBRE)
            except Exception:
                pass

    def salir(self, confirmar=True):
        if confirmar:
            self.mostrar()
            if not messagebox.askyesno(
                    "Cerrar programa",
                    "Si lo cierras, los audios programados dejarán de sonar.\n"
                    "¿Cerrar definitivamente?"):
                return
        self.rep.cerrar()
        if self.icono:
            try:
                self.icono.stop()
            except Exception:
                pass
        self.root.destroy()
        os._exit(0)

    def escuchar(self, sock):
        while True:
            try:
                c, _ = sock.accept()
            except OSError:
                break
            try:
                if c.recv(16) == b"mostrar":
                    self.cmd.put("mostrar")
            except Exception:
                pass
            finally:
                c.close()

    # ---------------- bucle principal ----------------
    def tick(self):
        try:
            while True:
                c = self.cmd.get_nowait()
                if c == "mostrar":
                    self.mostrar()
                elif c == "salir":
                    self.salir(False)
                    return
        except queue.Empty:
            pass

        ahora = dt.datetime.now()
        clave = ahora.strftime("%Y-%m-%d %H:%M")
        if clave != self.ultimo_minuto:
            self.ultimo_minuto = clave
            self.db.set_cfg("ultimo_minuto", clave)
            self.calcular_proximo()
            toca = [] if self.manual else [      # en modo manual no suena nada programado
                a for a in self.db.q("SELECT * FROM audios WHERE activo=1")
                if le_toca(a, ahora) and (a["id"], clave) not in self.omitidas]   # y no los saltados
            toca.sort(key=lambda a: -a["prioridad"])  # primero los de prioridad
            for a in toca:
                self.rep.agregar(a)

        self.actualizar_estado()
        self.root.after(500, self.tick)


def diagnostico(rutas):
    """Audiomatico.exe --diagnostico archivo.mp3 ...  Escribe diagnostico.txt (en la carpeta de
    datos) con qué decodificadores hay y si cada audio se puede leer, medir y reproducir."""
    os.makedirs(CARPETA, exist_ok=True)
    lin = ["%s %s - diagnóstico del %s" % (NOMBRE, VERSION, time.strftime("%Y-%m-%d %H:%M:%S")),
           "Python %s %s" % (sys.version.split()[0], "(64 bits)" if sys.maxsize > 2 ** 32 else "(32 bits)"),
           "pygame %s (SDL %s, SDL_mixer %s)" % (
               pygame.version.ver, ".".join(map(str, pygame.version.SDL)),
               ".".join(map(str, pygame.mixer.get_sdl_mixer_version()))),
           "miniaudio: %s | psutil: %s | pycaw: %s | controles multimedia de Windows 10/11: %s" % (
               "sí" if miniaudio else "NO", "sí" if psutil else "NO", "sí" if AudioUtilities else "NO",
               "sí" if cargar_gsmtc() else "no disponibles (Windows %d.%d: se necesita Windows 10 o más nuevo)" % version_windows()[:2])]
    try:
        try:
            pygame.mixer.init(44100, -16, 2, 2048, allowedchanges=0)
        except Exception:
            pygame.mixer.quit()
            pygame.mixer.init()
        lin.append("Mezcla de audio: %s" % (pygame.mixer.get_init(),))
    except Exception as e:
        lin.append("No se pudo iniciar la tarjeta de sonido: %s" % motivo_error(e))
    for r in rutas:
        lin += ["", "Archivo: " + r]
        try:
            lin.append("  Tamaño: %.1f KB | duración según su cabecera: %s" % (
                os.path.getsize(r) / 1024.0, ("%.1f s" % mp3_segundos(r)) if mp3_segundos(r) else "?"))
        except OSError as e:
            lin.append("  No se puede abrir el archivo: %s" % e)
            continue
        for nombre, f in (("pygame Sound (lo usa el nivelador)", lambda: pygame.mixer.Sound(r)),
                          ("pygame music (la reproducción)", lambda: pygame.mixer.music.load(r))):
            try:
                f()
                lin.append("  %-36s OK" % nombre)
            except Exception as e:
                lin.append("  %-36s FALLA: %s" % (nombre, motivo_error(e)))
        dur, env, nivel, motivo = analizar_audio(r)
        if motivo:
            lin.append("  Análisis completo                    FALLA: %s" % motivo)
        else:
            lin.append("  Análisis completo                    OK: dura %.1f s, nivel medio %s" % (
                dur, "%.1f dB" % nivel if nivel is not None else "sin sonido"))
    lin += ["", "Apps de Windows con audio ahora mismo:"]
    try:
        apps = sorted({s.Process.name() for s in AudioUtilities.GetAllSessions()
                       if s.Process is not None and s.ProcessId != os.getpid()})
        lin += ["  " + a for a in apps] or ["  (ninguna)"]
    except Exception as e:
        lin.append("  no se pudo listar: %s" % motivo_error(e))
    ruta_txt = os.path.join(CARPETA, "diagnostico.txt")
    with open(ruta_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lin) + "\n")
    try:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo(NOMBRE, "Diagnóstico guardado en:\n%s" % ruta_txt)
        r.destroy()
    except Exception:
        pass


_FALLOS = None


def activar_registro_de_fallos():
    """Si el programa se cierra de golpe (error del sistema, módulo que falla) o lanza un error no
    controlado, queda escrito en fallos.txt (carpeta de datos) qué pasó y dónde. Sin esto, en un
    .exe sin consola un cierre brusco no dejaría ninguna pista."""
    global _FALLOS
    try:
        os.makedirs(CARPETA, exist_ok=True)
        ruta = os.path.join(CARPETA, "fallos.txt")
        if os.path.exists(ruta) and os.path.getsize(ruta) > 256 * 1024:
            os.replace(ruta, ruta + ".1")
        _FALLOS = open(ruta, "a", encoding="utf-8", buffering=1)
        _FALLOS.write("\n=== %s  %s %s  Windows %d.%d.%d  Python %s %s ===\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), NOMBRE, VERSION, *version_windows(),
            sys.version.split()[0], "64 bits" if sys.maxsize > 2 ** 32 else "32 bits"))
        import faulthandler
        faulthandler.enable(file=_FALLOS, all_threads=True)       # cierres bruscos (violación de acceso...)

        def excepcion(tipo, valor, tb):
            import traceback
            _FALLOS.write("Error no controlado:\n" + "".join(traceback.format_exception(tipo, valor, tb)))
            try:
                messagebox.showerror(NOMBRE, "El programa tuvo un error y se cerrará.\n\nSe guardó el "
                                     "detalle en:\n%s" % ruta)
            except Exception:
                pass
        sys.excepthook = excepcion
    except Exception:
        _FALLOS = None


def main():
    global PUERTO
    if "--diagnostico" in sys.argv:
        diagnostico(sys.argv[sys.argv.index("--diagnostico") + 1:])
        return
    if "--puerto" in sys.argv:          # solo para pruebas: permite abrir una segunda copia
        PUERTO = int(sys.argv[sys.argv.index("--puerto") + 1])
    minimizado = "--minimizado" in sys.argv
    sock = instancia_unica(not minimizado)
    if sock is None:
        return  # ya hay una copia abierta
    activar_registro_de_fallos()
    app = App(minimizado, sock)
    app.root.mainloop()


if __name__ == "__main__":
    main()
