# Historial de cambios

## 1.4.2
Dos instaladores, uno para Windows 7/8 y otro para Windows 10/11. Es la versión que reemplaza a las 1.4.0 y 1.4.1, que se retiraron.

**Qué pasaba con la 1.4.0 y la 1.4.1**
- La 1.4.0 incluyó `winsdk` para mostrar artista y duración de la canción de otras apps (Windows 10/11). Ese módulo busca funciones de Windows que no existen en Windows 7.
- En Windows 7 el programa se cerraba de golpe al abrir, sin mensaje. Se reprodujo en una máquina virtual con Windows 7 SP1 de 32 bits: el informe de errores de Windows señalaba `winsdk\_winrt.pyd` (excepción `0x40000015`).
- La 1.4.1 solo cargaba el módulo en Windows 10 o más nuevo, pero el módulo seguía dentro del .exe.

**Qué cambia**
- `Instalar_Audiomatico_x.exe` (Windows 7/8, sirve en cualquier Windows) **no incluye** `winsdk`.
- `Audiomatico_Win10-11_x.exe` (solo Windows 10/11) sí lo incluye.
- El actualizador elige el instalador según el Windows; un Windows 7 nunca recibe el de 10/11.
- Si el programa se cierra de golpe o falla, queda escrito en `fallos.txt` (`%APPDATA%\ProgramadorAudios`). El registro anota la versión de Windows y si la canción/duración de otras apps está disponible.
- Verificado en Windows 7 SP1 de 32 bits: la 1.4.0 se cierra, la 1.4.2 abre (también al instalarse sobre la 1.4.0).

## 1.4.0
App seguida en la pantalla principal, botones de la cola con función real y espera entre audios. *Retirada: no abre en Windows 7.*

## 1.3.0
Versión estable anterior.
