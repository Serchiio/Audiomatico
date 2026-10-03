[README.md](https://github.com/user-attachments/files/33010709/README.md)

# Audiomático

Programador de audios para Windows. Reproduce tus audios (MP3, WAV, OGG) a las horas que indiques, con cola, prioridades y control de volumen. Funciona desde **Windows 7** en adelante, en 32 y 64 bits.

![Pantalla principal](capturas/1_audios.png)

## Qué hace

- **Programación flexible.** Cada audio combina tres reglas independientes: horas exactas (10:30, 18:40), minutos de cada hora, y repetición cada N minutos dentro de una ventana (por ejemplo, cada 15 min de 09:00 a 22:00). Suena cuando se cumpla cualquiera.
- **Días y vigencia.** Elige los días de la semana y hasta cuándo vale cada audio.
- **Prioridad con damper.** Un audio con prioridad suena primero y baja suavemente el volumen de los demás programas y del audio en curso. Al terminar, el volumen sube de forma progresiva. Hay una espera configurable antes del siguiente audio en cola.
- **Cola visible.** Se puede reordenar arrastrando, quitar elementos, pausar y saltar al siguiente.
- **Nivelación de volumen.** Cada audio se mide y se guarda al mismo nivel, sin distorsión. El fader **TOPE** fija en vivo el nivel máximo en decibelios.
- **Volumen de Windows.** El fader **VOLUMEN** controla la salida del equipo.
- **Vúmetro** animado que muestra lo que suena y hasta dónde llegaría sin nivelar.
- **Colores por categoría.** La prioridad se distingue aparte, con letra negrita y ★.
- **Fundidos** al iniciar y terminar cada audio.
- Se queda en segundo plano junto al reloj, puede iniciar con Windows y abrirse a una hora programada.

![Categorías](capturas/2_categorias.png)

## Instalación (usuarios)

Descarga el instalador desde la sección **Releases** y ejecútalo. Tus datos quedan en `%APPDATA%\ProgramadorAudios` y no se borran al desinstalar.

## Ejecutar desde el código

Requiere **Python 3.8** (el último que soporta Windows 7).

```bash
py -3.8 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe programador_audios.py
```

## Compilar e instalador

`compilar.bat` genera `dist\32bits\Audiomatico.exe` y `dist\64bits\Audiomatico.exe` con PyInstaller, y el instalador con [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`instalador.iss`). Hace falta Python 3.8 de 32 y de 64 bits. Si falta alguno, lo omite.

Nota para Windows 7: necesita el Service Pack 1 y la actualización KB2999226 (Universal C Runtime).

## Licencia

El código se publica bajo **PolyForm Strict 1.0.0** (ver `LICENSE`): puedes leerlo y usarlo para fines no comerciales, pero no modificarlo ni redistribuirlo. El programa instalado se rige por `LICENCIA_USO.txt`.
