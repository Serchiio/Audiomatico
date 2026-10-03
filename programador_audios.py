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
import queue
import subprocess
import calendar
import datetime as dt
import math
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
VERSION = "1.0.0"           # igual que en instalador.iss
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
EXTENSIONES = (".mp3", ".wav", ".ogg")
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
                          ("int_hasta", "TEXT DEFAULT ''")):
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


def parse_horas(txt):
    res = []
    for t in txt.replace(";", ",").split(","):
        t = t.strip()
        if not t:
            continue
        h, m = t.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(t)
        res.append("%02d:%02d" % (h, m))
    return sorted(set(res))


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
MAX_SEGUNDOS = 900       # audios más largos no se nivelan (memoria)


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
    umbral = 32768.0 * 10 ** (COMPUERTA_DB / 20.0)
    for i in range(0, len(raw) - bloque + 1, bloque):
        r = audioop.rms(raw[i:i + bloque], 2)
        if r >= umbral:
            suma += float(r) * r
            n += 1
    if n == 0:
        return None, None
    return _db(math.sqrt(suma / n)), _db(audioop.max(raw, 2))


PASO_ENV = 0.04          # segundos por muestra del vúmetro


def envolvente(raw, canales, frecuencia):
    """Nivel (dBFS) cada PASO_ENV segundos, para animar el vúmetro."""
    bloque = max(2, int(frecuencia * PASO_ENV)) * canales * 2
    return [_db(audioop.rms(raw[i:i + bloque], 2))
            for i in range(0, len(raw) - bloque + 1, bloque)]


def nivelar_archivo(ruta, destino, objetivo, techo):
    """
    Crea en 'destino' una copia WAV con el volumen llevado al nivel 'objetivo'
    (dBFS). Nunca deja picos por encima de 'techo' (no hay distorsión), así que un
    audio muy fuerte se baja y uno muy débil se sube hasta donde los picos permitan.
    Devuelve (nivel_original, ganancia_db). Si no hace falta cambiar nada devuelve
    ganancia 0 y no escribe el archivo.
    """
    snd = pygame.mixer.Sound(ruta)
    init = pygame.mixer.get_init()
    if not init or init[1] != -16:
        raise ValueError("formato de mezcla no soportado")
    frec, _, canales = init
    if snd.get_length() > MAX_SEGUNDOS:
        raise ValueError("audio demasiado largo para nivelar")
    raw = snd.get_raw()
    nivel, pico = medir_pcm(raw, canales, frec)
    if nivel is None:
        raise ValueError("el audio está en silencio")
    g = objetivo - nivel
    if pico + g > techo:          # tope: sin recortes
        g = techo - pico
    g = max(-30.0, min(20.0, g))
    if abs(g) < 0.3:
        return nivel, 0.0
    out = audioop.mul(raw, 2, 10 ** (g / 20.0))
    w = wave.open(destino, "wb")
    try:
        w.setnchannels(canales)
        w.setsampwidth(2)
        w.setframerate(frec)
        w.writeframes(out)
    finally:
        w.close()
    return nivel, g


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


def hhmm_a_min(txt):
    """'09:30' -> minutos desde las 00:00 (ValueError si el formato es inválido)."""
    h, m = txt.strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(txt)
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


def tarea_programada(hhmm):
    """Crea (o borra si hhmm es None) la tarea diaria que abre el programa."""
    flags = 0x08000000  # CREATE_NO_WINDOW
    if hhmm is None:
        cmd = ["schtasks", "/Delete", "/TN", APP, "/F"]
    else:
        cmd = ["schtasks", "/Create", "/SC", "DAILY", "/TN", APP,
               "/TR", comando_inicio(), "/ST", hhmm, "/F"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           errors="replace", creationflags=flags)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)


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
# Volumen maestro de Windows (la salida de audio del equipo)
# ----------------------------------------------------------------------
class VolumenWindows:
    def __init__(self):
        self.ep = None
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            disp = AudioUtilities.GetSpeakers()
            iface = disp.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self.ep = cast(iface, POINTER(IAudioEndpointVolume))
        except Exception:
            self.ep = None

    def disponible(self):
        return self.ep is not None

    def leer(self):
        """(porcentaje 0-100, decibelios) o None."""
        try:
            return (self.ep.GetMasterVolumeLevelScalar() * 100.0,
                    self.ep.GetMasterVolumeLevel())
        except Exception:
            return None

    def poner(self, pct):
        try:
            self.ep.SetMasterVolumeLevelScalar(max(0.0, min(1.0, pct / 100.0)), None)
        except Exception:
            pass


# ----------------------------------------------------------------------
# Reproductor: dos voces (audio normal y audio con prioridad), damper con
# rampa, espera tras una prioridad y fundidos de entrada/salida
# ----------------------------------------------------------------------
class Reproductor:
    def __init__(self, log, factor):
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
        self.sesiones = []            # [(SimpleAudioVolume, volumen_original)]
        self.sesiones_cap = False
        self.mult_otros = 1.0
        self.t_otros = 0.0

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

    @staticmethod
    def _analizar(snd):
        """(duración, envolvente para el vúmetro, nivel medio en dB)."""
        dur = snd.get_length()
        env, nivel = [], None
        init = pygame.mixer.get_init()
        if init and init[1] == -16 and dur <= MAX_SEGUNDOS:
            raw = snd.get_raw()
            env = envolvente(raw, init[2], init[0])
            nivel = medir_pcm(raw, init[2], init[0])[0]
        return dur, env, nivel

    def _factor(self, nivel):
        """Volumen (0-1) que lleva el audio al tope elegido; nunca amplifica."""
        if nivel is None:
            return 1.0
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
    def _tocar_normal(self, a, inicio=0.0):
        try:
            ruta = self.ruta_de(a)
            snd = pygame.mixer.Sound(ruta)
            self.duracion, self.env, self.nivel_play = self._analizar(snd)
            del snd
            self.ganancia = self._ganancia_de(a, ruta)
            pygame.mixer.music.load(ruta)
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
            self.log("ERROR al reproducir '%s': %s" % (a["nombre"], e))
            return False

    def _iniciar_normal(self, a):
        if self._tocar_normal(a):
            self.actual = a
            self.log("Reproduciendo: %s" % a["nombre"])
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
            snd = pygame.mixer.Sound(ruta)
            dur, env, nivel = self._analizar(snd)
            snd.set_volume(0.0)
            canal = snd.play()
            if canal is None:
                canal = pygame.mixer.find_channel(True)
                canal.play(snd)
        except Exception as e:
            self.log("ERROR al reproducir '%s': %s" % (a["nombre"], e))
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
        return True

    def _fin_prio(self):
        self.prio_actual = self.prio_snd = self.canal = None
        self.prio_env = []
        self.vol_prio = 0.0
        while self.cola_prio:
            if self._iniciar_prio(self.cola_prio.pop(0)):
                return
        self.duck_meta = 0.0                         # el damper vuelve a subir
        if self.pausa_por_prio:
            self.pausa_por_prio = False
            if not self.pausado:
                pygame.mixer.music.unpause()
        if self.cola:
            self.espera_hasta = time.time() + self.espera
            if self.actual is None:
                self.log("Esperando %d s antes de: %s" % (self.espera, self.cola[0]["nombre"]))

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
                    return
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

    def mover_cola(self, i, j):
        """Mueve el elemento i de la cola a la posición j."""
        if 0 <= i < len(self.cola) and 0 <= j < len(self.cola) and i != j:
            self.cola.insert(j, self.cola.pop(i))

    def quitar_de_cola(self, i):
        if 0 <= i < len(self.cola):
            self.cola.pop(i)

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
        self._volumen_otros(mult)

    # ---------- volumen de otros programas (pycaw) ----------
    def _capturar_otros(self):
        self.sesiones_cap = True
        self.sesiones = []
        if AudioUtilities is None:
            return
        try:
            for s in AudioUtilities.GetAllSessions():
                if s.Process is None or s.ProcessId == os.getpid():
                    continue
                vol = s.SimpleAudioVolume
                self.sesiones.append((vol, vol.GetMasterVolume()))
        except Exception as e:
            self.log("No se pudo bajar el volumen de otros programas: %s" % e)

    def _restaurar_otros_ya(self):
        for vol, orig in self.sesiones:
            try:
                vol.SetMasterVolume(orig, None)
            except Exception:
                pass
        self.sesiones = []
        self.sesiones_cap = False
        self.mult_otros = 1.0

    def _volumen_otros(self, mult):
        if self.duck <= 0.0:
            if self.sesiones_cap:
                self._restaurar_otros_ya()
            return
        if not self.sesiones_cap:
            self._capturar_otros()
        ahora = time.time()
        if abs(mult - self.mult_otros) < 0.004 or ahora - self.t_otros < 0.08:
            return
        self.t_otros, self.mult_otros = ahora, mult
        for vol, orig in self.sesiones:
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

    def __init__(self, app, audio=None):
        super().__init__(app.root)
        self.app = app
        self.audio = audio
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
        ttk.Checkbutton(f, text="PRIORIDAD (suena primero y baja el volumen de lo demás: "
                                "otros programas y el audio en curso)", variable=self.v_prio
                        ).grid(row=r, column=1, columnspan=3, sticky="w", pady=3)
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
        ttk.Label(f, text="Ej: 08:00, 13:30, 17:45").grid(
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
        self.v_int_desde = tk.StringVar(value="09:00")
        self.v_int_hasta = tk.StringVar(value="22:00")
        ttk.Spinbox(fi, from_=0, to=720, width=4, textvariable=self.v_int).pack(side="left")
        ttk.Label(fi, text=" min, de ").pack(side="left")
        ttk.Entry(fi, textvariable=self.v_int_desde, width=6).pack(side="left")
        ttk.Label(fi, text=" a ").pack(side="left")
        ttk.Entry(fi, textvariable=self.v_int_hasta, width=6).pack(side="left")
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

    def cargar(self, a):
        self.v_nombre.set(a["nombre"])
        self.v_ruta.set(a["ruta"])
        for c in self.cats:
            if c["id"] == a["categoria_id"]:
                self.v_cat.set(c["nombre"])
        self.v_prio.set(a["prioridad"])
        self.v_activo.set(a["activo"])
        activos = [int(d) for d in a["dias"].split(",") if d != ""]
        for i, v in enumerate(self.v_dias):
            v.set(1 if i in activos else 0)
        self.v_horas.set(a["horas"].replace(",", ", "))
        self.v_min.set(a["minutos"].replace(",", ", "))
        self.v_desde.set(str(a["hora_desde"]))
        self.v_hasta.set(str(a["hora_hasta"]))
        self.v_int.set(str(a["intervalo"] or 0))
        if a["int_desde"]:
            self.v_int_desde.set(a["int_desde"])
        if a["int_hasta"]:
            self.v_int_hasta.set(a["int_hasta"])
        if a["fecha_fin"]:
            self.v_vig.set("Fecha exacta")
            self.v_fecha.set(a["fecha_fin"])

    def examinar(self):
        ruta = filedialog.askopenfilename(
            parent=self, title="Elegir audio",
            filetypes=[("Audio", "*.mp3 *.wav *.ogg"), ("Todos", "*.*")])
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
            messagebox.showwarning("Formato", "Usa archivos MP3, WAV u OGG.", parent=self)
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

        # copiar el audio a la carpeta del programa (por si lo mueven de lugar)
        if os.path.dirname(os.path.abspath(ruta)) != os.path.abspath(CARPETA_AUDIOS):
            destino = os.path.join(CARPETA_AUDIOS, "%d_%s" % (
                int(time.time() * 1000), os.path.basename(ruta)))
            try:
                shutil.copy2(ruta, destino)
            except Exception as e:
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
        else:
            aid = db.x("INSERT INTO audios(nombre,ruta,categoria_id,prioridad,activo,dias,horas,"
                       "minutos,hora_desde,hora_hasta,fecha_fin,intervalo,int_desde,int_hasta,"
                       "fecha_inicio) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       datos + (hoy.isoformat(),)).lastrowid
            cambio = True
        if cambio:
            self.config(cursor="watch")
            self.update_idletasks()
        self.app.nivelar_audio(aid, forzar=cambio)
        self.app.refrescar_audios()
        self.destroy()


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

        self.root = tk.Tk()
        self.root.title(NOMBRE)
        try:
            self.root.iconbitmap(recurso("icono.ico"))
        except Exception:
            pass
        self.root.geometry("1120x680")
        self.root.minsize(900, 600)
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
            self.rep = Reproductor(self.log, 0.25)
            self.aplicar_config()
        except Exception as e:
            messagebox.showerror("Audio", "No se pudo iniciar el sistema de audio:\n%s" % e)
            raise
        if AudioUtilities is None:
            self.log("Aviso: pycaw no está instalado; no se bajará el volumen de otros programas.")
        if not self.vol_win.disponible():
            self.sc_vol.state(["disabled"])
            self.lbl_vol.config(text="n/d")

        self.refrescar_categorias()
        self.refrescar_audios()
        self.cola_nivelar = []
        self.root.after(1500, self.programar_nivelacion)  # audios aún sin nivelar
        self.panel.bind("<Configure>", self.ajustar_divisor)
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
        self.log("Programa iniciado.")
        self.tick()

    # ---------------- interfaz ----------------
    def construir_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.tab_audios = ttk.Frame(nb)
        self.tab_cats = ttk.Frame(nb)
        self.tab_sis = ttk.Frame(nb)
        nb.add(self.tab_audios, text="  Audios  ")
        nb.add(self.tab_cats, text="  Categorías  ")
        nb.add(self.tab_sis, text="  Sistema  ")
        self.ui_audios()
        self.ui_categorias()
        self.ui_sistema()

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
        for c, t, w in (("nombre", "Nombre", 205), ("cat", "Categoría", 85),
                        ("prio", "Prioridad", 65), ("dias", "Días", 55),
                        ("horario", "Horario", 190), ("caduca", "Caduca", 70),
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
        self.cv_vu.bind("<Configure>", self.construir_vu)
        self.lbl_db = ttk.Label(der, text="", foreground="#444444")
        self.lbl_db.pack(anchor="w", pady=(3, 0))
        ttk.Label(der, text="Sombreado = nivel original, antes de nivelar",
                  foreground="#999999", font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 8))
        # --- abajo: la cola (izquierda) y el mezclador de dos faders (derecha) ---
        abajo = ttk.Frame(der)
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
        ttk.Label(mezcla, text="Windows", foreground="#888888",
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
        self.sc_vol.bind("<ButtonRelease-1>", lambda e: setattr(self, "_fader_arrastre", False),
                         add="+")
        self.sc_tope.bind("<ButtonRelease-1>", lambda e: self.db.set_cfg(
            "tope_db", "%.0f" % self.v_tope.get()), add="+")

        izqc = ttk.Frame(abajo)
        izqc.pack(side="left", fill="both", expand=True)
        ttk.Label(izqc, text="Siguientes  (★ negrita = prioridad)",
                  foreground="#666666").pack(anchor="w")
        self.lb_cola_main = ttk.Treeview(izqc, show="tree", selectmode="browse", height=6)
        self.lb_cola_main.column("#0", width=150, stretch=True)
        self.lb_cola_main.tag_configure("prio", font=FUENTE_PRIO)
        self.lb_cola_main.pack(fill="both", expand=True, pady=(2, 0))
        self.lb_cola_main.bind("<ButtonPress-1>", self.cola_press)
        self.lb_cola_main.bind("<B1-Motion>", self.cola_drag)
        self.cola_origen = None
        fb = ttk.Frame(izqc)
        fb.pack(fill="x", pady=(6, 0))
        ttk.Button(fb, text="▲", width=3,
                   command=lambda: self.mover_sel(-1)).pack(side="left")
        ttk.Button(fb, text="▼", width=3,
                   command=lambda: self.mover_sel(1)).pack(side="left", padx=3)
        ttk.Button(fb, text="Quitar",
                   command=self.quitar_sel).pack(side="left")
        ttk.Label(izqc, text="Arrastra para cambiar el orden",
                  foreground="#888888").pack(anchor="w", pady=(4, 0))

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

        fh = ttk.LabelFrame(f, text="Apertura automática a una hora (todos los días)", padding=8)
        fh.pack(fill="x", pady=8)
        self.v_apertura = tk.StringVar(value=self.db.cfg("apertura", ""))
        ttk.Label(fh, text="Hora (HH:MM):").pack(side="left")
        ttk.Entry(fh, textvariable=self.v_apertura, width=8).pack(side="left", padx=6)
        ttk.Button(fh, text="Programar", command=self.programar_apertura).pack(side="left", padx=3)
        ttk.Button(fh, text="Quitar", command=self.quitar_apertura).pack(side="left", padx=3)

        fv = ttk.LabelFrame(f, text="Prioridad y damper (bajar el volumen mientras suena "
                                    "un audio con prioridad)", padding=8)
        fv.pack(fill="x", pady=6)
        self.v_pct = tk.StringVar(value=self.db.cfg("bajar_pct", "25"))
        self.v_rampa = tk.StringVar(value=self.db.cfg("damper_rampa", "1.5"))
        self.v_espera = tk.StringVar(value=self.db.cfg("damper_espera", "30"))
        self.v_modo = tk.StringVar(value=self.MODOS[1 if self.db.cfg(
            "damper_modo", "mezclar") == "pausar" else 0])
        self.v_fundido = tk.StringVar(value=self.db.cfg("fundido", "0.5"))
        filas = (("Bajar otros programas y el audio en curso al (%):", self.v_pct, 0, 90, 5),
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
        ttk.Label(fn, text="Cada audio se guarda al mismo nivel (%d dB) sin pasar del tope de "
                           "picos, y el fader TOPE de la pantalla principal\n"
                           "decide hasta qué nivel suenan, en vivo. El archivo original "
                           "no se toca." % NIVEL_BASE,
                  foreground="#666666", justify="left").grid(row=2, column=0, columnspan=4,
                                                             sticky="w", pady=(4, 0))

        ttk.Button(f, text="Ver registro de eventos",
                   command=self.ver_registro).pack(anchor="w", pady=(2, 0))

        ttk.Label(f, text="Al cerrar la ventana (X) el programa sigue en segundo plano.\n"
                          "Para cerrarlo por completo: Alt+F4, este botón o el icono junto al reloj.",
                  justify="left").pack(anchor="w", pady=10)
        ttk.Button(f, text="Cerrar programa por completo",
                   command=lambda: self.salir(True)).pack(anchor="w")
        ttk.Label(f, text="%s %s" % (NOMBRE, VERSION), foreground="#999999").pack(
            anchor="w", pady=(14, 0))

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
        est = self.rep.firma()
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
        for i, (txt, prio, cid) in enumerate(self.rep.cola_detalle()):
            tags = (["c%d" % cid] if cid in self.colores else []) + (["prio"] if prio else [])
            cola.insert("", "end", iid=str(i), tags=tags,
                        text="%d.  %s%s" % (i + 1, "★ " if prio else "", txt))

    @staticmethod
    def _mmss(seg):
        seg = int(seg)
        return "%d:%02d" % (seg // 60, seg % 60)

    def actualizar_progreso(self):
        a, pos, dur, _ = self.rep.sonando()
        if a is None or dur <= 0:
            frac, txt = 0.0, "0:00 / 0:00"
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

    def dibujar_progreso(self, frac, color):
        c = self.cv_prog
        w = max(40, c.winfo_width())
        c.delete("all")
        c.create_line(8, 8, w - 8, 8, width=6, capstyle="round", fill="#d5dae1")
        if frac > 0:
            x = 8 + frac * (w - 16)
            c.create_line(8, 8, max(x, 9), 8, width=6, capstyle="round", fill=color)
            c.create_oval(x - 6, 2, x + 6, 14, fill="white", outline=color, width=2)

    # --- vúmetro ---
    @staticmethod
    def _color_seg(db):
        """(encendido, sombra) de un segmento según su nivel."""
        if db <= -14:
            return "#2ecc71", "#2f5f40"
        if db <= -5:
            return "#f1c40f", "#665c2a"
        return "#e74c3c", "#663030"

    def _x_vu(self, db):
        db = max(VU_MIN, min(VU_MAX, db))
        return self.vu_x0 + (db - VU_MIN) / (VU_MAX - VU_MIN) * (self.vu_x1 - self.vu_x0)

    def construir_vu(self, e=None):
        c = self.cv_vu
        c.delete("all")
        w = max(80, c.winfo_width())
        self.vu_x0, self.vu_x1 = 8, w - 8
        ancho = (self.vu_x1 - self.vu_x0) / float(VU_SEGS)
        self.vu_segs, self.vu_thr = [], []
        for i in range(VU_SEGS):
            db = VU_MIN + (i + 0.5) * (VU_MAX - VU_MIN) / VU_SEGS
            x = self.vu_x0 + i * ancho
            self.vu_segs.append(c.create_rectangle(x + 1, 16, x + ancho - 1, 40,
                                                   fill="#262b32", outline=""))
            self.vu_thr.append(db)
        for db in (-60, -40, -20, -10, -5, 0):
            c.create_text(self._x_vu(db), 52, text=str(db), fill="#8a939e",
                          font=("Segoe UI", 7))
        self.vu_cap = c.create_line(0, 9, 0, 43, fill="#ffffff", width=2)
        self.vu_seg_estado = [None] * VU_SEGS
        self.dibujar_vu()

    def dibujar_vu(self):
        if not self.vu_segs:
            return
        c = self.cv_vu
        out, org, pico = self.vu_out, self.vu_org, self.vu_pico
        i_pico = None
        if pico > VU_MIN + 1:
            i_pico = min(VU_SEGS - 1, int((pico - VU_MIN) / (VU_MAX - VU_MIN) * VU_SEGS))
        for i, thr in enumerate(self.vu_thr):
            on, sombra = self._color_seg(thr)
            if i == i_pico:
                col = "#ffffff"
            elif thr <= out:
                col = on
            elif thr <= org:
                col = sombra
            else:
                col = "#262b32"
            if col != self.vu_seg_estado[i]:
                self.vu_seg_estado[i] = col
                c.itemconfig(self.vu_segs[i], fill=col)
        x = self._x_vu(self.rep.tope)       # la línea blanca sigue al fader TOPE
        c.coords(self.vu_cap, x, 9, x, 43)

    def animar(self):
        """Bucle rápido (25 cuadros/s): vúmetro y barra de progreso."""
        if not self.root.winfo_viewable():       # ventana oculta: no gastar CPU
            self.root.after(300, self.animar)
            return
        ahora = time.time()
        dt_ = min(0.2, ahora - self.vu_t)
        self.vu_t = ahora
        n = self.rep.niveles()
        meta_out, meta_org = (VU_MIN, VU_MIN) if n is None else n
        caida = 45.0 * dt_                        # dB por segundo que baja la barra
        self.vu_out = meta_out if meta_out > self.vu_out else max(meta_out, self.vu_out - caida)
        self.vu_org = meta_org if meta_org > self.vu_org else max(meta_org, self.vu_org - caida)
        if self.vu_out >= self.vu_pico:
            self.vu_pico, self.vu_pico_t = self.vu_out, ahora
        elif ahora - self.vu_pico_t > 0.8:
            self.vu_pico = max(self.vu_out, self.vu_pico - 30.0 * dt_)
        self.dibujar_vu()
        if n is None:
            txt = "Salida: --   Original: --"
        else:
            txt = "Salida: %.0f dB   Original: %.0f dB%s" % (
                n[0], n[1], "   (limitado)" if n[1] - n[0] > 1.5 else "")
        if txt != self.lbl_db.cget("text"):
            self.lbl_db.config(text=txt)
        self.actualizar_progreso()
        self.root.after(40, self.animar)

    def ajustar_divisor(self, e=None):
        """Deja el panel de la cola a la derecha con ~340 px, al tener tamaño real."""
        w = self.panel.winfo_width()
        if w < 700:
            return
        self.panel.unbind("<Configure>")
        try:
            self.panel.sashpos(0, w - 340)
        except tk.TclError:
            pass

    # --- reordenar la cola ---
    def _idx_cola(self, fila):
        """Fila del listbox -> posición en rep.cola (None si es una prioridad en espera)."""
        i = fila - len(self.rep.cola_prio)
        return i if 0 <= i < len(self.rep.cola) else None

    def _redibujar_cola(self, seleccionar=None):
        self.ultimo_estado = None
        self.actualizar_estado()
        hijos = self.lb_cola_main.get_children()
        if seleccionar is not None and 0 <= seleccionar < len(hijos):
            self.lb_cola_main.selection_set(hijos[seleccionar])

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

    def _sel_cola(self):
        s = self.lb_cola_main.selection()
        return self._fila_cola(s[0]) if s else None

    def mover_sel(self, d):
        fila = self._sel_cola()
        i = self._idx_cola(fila) if fila is not None else None
        if i is None:
            return
        self.rep.mover_cola(i, i + d)
        self._redibujar_cola(fila + d if 0 <= i + d < len(self.rep.cola) else fila)

    def quitar_sel(self):
        fila = self._sel_cola()
        i = self._idx_cola(fila) if fila is not None else None
        if i is not None:
            self.rep.quitar_de_cola(i)
            self._redibujar_cola()

    # --- próximo audio programado ---
    def calcular_proximo(self):
        base = dt.datetime.now().replace(second=0, microsecond=0)
        activos = self.db.q("SELECT * FROM audios WHERE activo=1")
        if not activos:
            self.lbl_proximo.config(text="")
            return
        for n in range(1, 4 * 24 * 60):
            t = base + dt.timedelta(minutes=n)
            nombres = [a["nombre"] for a in activos if le_toca(a, t)]
            if nombres:
                cuando = "hoy" if t.date() == base.date() else (
                    "mañana" if (t.date() - base.date()).days == 1
                    else DIAS[t.weekday()])
                extra = " (+%d)" % (len(nombres) - 1) if len(nombres) > 1 else ""
                self.lbl_proximo.config(text="Próximo: %s %s – %s%s" % (
                    cuando, t.strftime("%H:%M"), nombres[0][:32], extra))
                return
        self.lbl_proximo.config(text="Próximo: nada en los próximos 4 días")

    def pausar_reanudar(self):
        self.rep.pausar_reanudar()
        self.actualizar_progreso()

    def vaciar_todo(self):
        self.rep.vaciar_cola()
        self.ultimo_estado = None

    # --- fader VOLUMEN: volumen maestro de Windows (la salida del equipo) ---
    def _txt_vol(self, pct, db):
        return "%d%%\n%s" % (pct, "silencio" if pct <= 0 else "%.1f dB" % db)

    def cambiar_volumen(self, valor):
        pct = float(valor)
        self.vol_win.poner(pct)
        self.lbl_vol.config(text=self._txt_vol(
            pct, 20.0 * math.log10(pct / 100.0) if pct > 0 else -99.0))

    def sincronizar_volumen_windows(self):
        """Mantiene el fader igual al volumen de Windows (por si lo cambian desde fuera)."""
        if self.vol_win.disponible() and not self._fader_arrastre:
            v = self.vol_win.leer()
            if v is not None:
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
        r.factor_duck = self._num(cfg("bajar_pct", "25"), 25, 0, 90) / 100.0
        r.rampa = self._num(cfg("damper_rampa", "1.5"), 1.5, 0, 10)
        r.espera = self._num(cfg("damper_espera", "30"), 30, 0, 300)
        r.modo_pausa = cfg("damper_modo", "mezclar") == "pausar"
        r.fundido = self._num(cfg("fundido", "0.5"), 0.5, 0, 5)
        r.tope = self.v_tope.get()
        activa, _, techo = self.cfg_norm()
        r.usar_norm, r.techo = activa, techo

    def guardar_damper(self):
        s = self.db.set_cfg
        s("bajar_pct", int(self._num(self.v_pct.get(), 25, 0, 90)))
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
                partes.append(a["horas"].replace(",", " "))
            if a["minutos"]:
                partes.append("min %s (%d-%dh)" % (
                    a["minutos"].replace(",", "/"), a["hora_desde"], a["hora_hasta"]))
            if a["intervalo"]:
                partes.append("cada %d min %s-%s" % (
                    a["intervalo"], a["int_desde"], a["int_hasta"]))
            tags = []
            if a["categoria_id"] in self.colores:
                tags.append("c%d" % a["categoria_id"])
            if a["prioridad"]:
                tags.append("prio")
            if not a["activo"]:
                tags.append("inactivo")
            self.tree.insert("", "end", iid=str(a["id"]), tags=tags, values=(
                a["nombre"], a["cat"] or "-", "★ SI" if a["prioridad"] else "",
                txt_dias, " | ".join(partes), a["fecha_fin"] or "Indefinido",
                "Activo" if a["activo"] else "Inactivo"))

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
        if (not forzar and a["objetivo_db"] == obj and a["nivel_db"] is not None):
            return
        self._borrar_norm(a)
        destino = os.path.join(CARPETA_AUDIOS, "n_%d_%d.wav" % (aid, int(time.time() * 1000)))
        try:
            nivel, g = nivelar_archivo(a["ruta"], destino, obj, techo)
        except Exception as e:
            self.log("No se pudo nivelar '%s': %s" % (a["nombre"], e))
            self.db.x("UPDATE audios SET ruta_norm='',nivel_db=NULL,ganancia_db=NULL,"
                      "objetivo_db=? WHERE id=?", (obj, aid))
            return
        self.db.x("UPDATE audios SET ruta_norm=?,nivel_db=?,ganancia_db=?,objetivo_db=? "
                  "WHERE id=?", (destino if g else "", nivel, g, obj, aid))
        self.log("Nivelado '%s': %.1f dB -> %.1f dB (ajuste %+.1f dB)"
                 % (a["nombre"], nivel, nivel + g, g))

    def nivelar_pendientes(self):
        """Nivela de a un audio por vez para no congelar la ventana."""
        if not self.cola_nivelar:
            self.refrescar_audios(False)
            return
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
                "SELECT id FROM audios WHERE nivel_db IS NULL OR objetivo_db IS NULL "
                "OR objetivo_db<>?", (obj,))]
        if forzar:
            self.db.x("UPDATE audios SET objetivo_db=NULL")
        self.cola_nivelar = ids
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

    def nuevo_audio(self):
        DialogoAudio(self)

    def editar_audio(self):
        a = self.sel_audio()
        if a:
            DialogoAudio(self, a)

    def eliminar_audio(self):
        a = self.sel_audio()
        if a and messagebox.askyesno("Eliminar", "¿Eliminar '%s'?" % a["nombre"]):
            self.db.x("DELETE FROM audios WHERE id=?", (a["id"],))
            for r in (a["ruta"], a["ruta_norm"]):
                try:
                    if r and os.path.dirname(r) == CARPETA_AUDIOS:
                        os.remove(r)
                except OSError:
                    pass
            self.refrescar_audios()

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

    def programar_apertura(self):
        try:
            h, m = self.v_apertura.get().strip().split(":")
            hhmm = "%02d:%02d" % (int(h), int(m))
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except Exception:
            messagebox.showwarning("Formato", "Escribe la hora como HH:MM (ej. 07:30).")
            return
        ok, salida = tarea_programada(hhmm)
        if ok:
            self.db.set_cfg("apertura", hhmm)
            messagebox.showinfo("Listo", "El programa se abrirá todos los días a las %s." % hhmm)
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
            self.calcular_proximo()
            toca = [a for a in self.db.q("SELECT * FROM audios WHERE activo=1")
                    if le_toca(a, ahora)]
            toca.sort(key=lambda a: -a["prioridad"])  # primero los de prioridad
            for a in toca:
                self.rep.agregar(a)

        self.actualizar_estado()
        self.root.after(500, self.tick)


def main():
    global PUERTO
    if "--puerto" in sys.argv:          # solo para pruebas: permite abrir una segunda copia
        PUERTO = int(sys.argv[sys.argv.index("--puerto") + 1])
    minimizado = "--minimizado" in sys.argv
    sock = instancia_unica(not minimizado)
    if sock is None:
        return  # ya hay una copia abierta
    app = App(minimizado, sock)
    app.root.mainloop()


if __name__ == "__main__":
    main()
