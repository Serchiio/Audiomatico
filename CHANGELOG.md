# Historial de cambios

## 1.5.1
Corrige un **cierre inesperado en Windows 7** y un fallo del nivelador a volumen bajo.

- **El programa ya no se cierra solo** cuando está activado «Volumen general de Windows al…». Al leer ese volumen se liberaba un objeto del sistema dos veces, y en Windows 7 acababa cerrando el programa a los pocos segundos o minutos.
- **El nivelador ya se recupera a volumen bajo.** Con el volumen de una app por debajo del 20 %, los pasos de subida eran tan pequeños que se descartaban: tras una canción más baja o con la barra de volumen de la página bajada, el volumen casi no subía. Ahora sube al ritmo previsto (unos segundos).

## 1.5.0
Volúmenes fijos y **nivelador automático** de otras apps (pestaña Apps, panel «Volúmenes fijos y nivelador»).

- **Bloqueo de volúmenes.** Se puede fijar el volumen general de Windows (por ejemplo 70 %) y el de cada app (por ejemplo Google al 25 %). Si alguien los mueve, el programa los devuelve a su valor y lo anota en el registro. Desmarcando el bloqueo se pueden cambiar a mano. Mientras suena un audio que baja el volumen de otras apps (prioridad) no se toca nada, para no pelear con el damper.
- **Nivelador.** Para las apps marcadas «Nivelar», el programa mide su señal y mueve su volumen en el Mezclador para que la salida quede siempre en el nivel objetivo (por defecto -18 dB): baja rápido ante una canción fuerte y sube despacio ante una baja. El % indicado es el máximo. En una pausa o un silencio no cambia nada. Hay un botón «Tomar el nivel actual de la app mostrada en pantalla» para calibrar el objetivo.
- **Verificado en Windows 7 SP1 de 32 bits con Firefox:** canciones con 12 dB de diferencia de volumen quedan en la salida a menos de 2 dB entre sí; el bloqueo del volumen general de Windows devuelve el valor fijado.
- **Límites:** el control de volumen que está dentro de una app (el de YouTube Music, por ejemplo) no se puede tocar desde fuera; el nivelador compensa lo que cambie. No ve el futuro: una canción fuerte tras una baja suena un instante por encima del objetivo antes de ajustarse. Una canción muy baja no puede subir más del máximo fijado.

## 1.4.3
Corrige la **apertura automática a una hora** y deja constancia en el registro.

**Qué pasaba**
- La tarea programada que creaba el programa quedaba con la configuración por defecto de Windows: **«no iniciar si el equipo funciona con batería»** (en un portátil sin cargador nunca se abría), **«detener a las 72 horas»** (Windows cerraba el programa a los 3 días) y sin recuperar la apertura si el PC estaba apagado a esa hora.
- Comprobado en Windows 7 SP1 de 32 bits: «Iniciar con Windows» y la apertura programada sí funcionaban con el equipo enchufado y el usuario con sesión iniciada. Por eso la causa más probable de que no abriera en otros equipos son esas tres opciones (portátil con batería, PC apagado a esa hora, o sesión sin iniciar).

**Qué cambia**
- La tarea ahora se crea con una definición completa: corre con batería, no se detiene sola y, si el PC estaba apagado a la hora, se abre al encenderlo.
- Quien ya tenía una hora programada no tiene que hacer nada: al abrir la 1.4.3 la tarea se vuelve a crear sola (queda anotado en el registro).
- El registro dice si el programa se abrió en segundo plano (inicio automático o tarea programada).
- Nota: abrir «en segundo plano» deja el programa junto al reloj, sin ventana. Es lo esperado.

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
