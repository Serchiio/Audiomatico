; Instalador de Audiomático (Inno Setup). Se compila con compilar.bat, que genera las dos variantes.
#define Nombre "Audiomático"
#define Version "1.4.3"
#define Exe "Audiomatico.exe"
; Variante: /DVariante=win7  -> 32 bits, SIN winsdk. Sirve en todo Windows (7, 8, 10, 11).
;           /DVariante=win10 -> con winsdk (artista y duración de otras apps). Solo Windows 10/11.
#ifndef Variante
  #define Variante "win7"
#endif

[Setup]
AppId={{92177A21-DFD8-4980-9293-37B8AE61C4EE}
AppName={#Nombre}
AppVersion={#Version}
AppPublisher={#Nombre}
DefaultDirName={autopf}\Audiomatico
DefaultGroupName={#Nombre}
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Nombre}
SetupIconFile=icono.ico
LicenseFile=LICENCIA_USO.txt
OutputDir=instalador
#if Variante == "win10"
OutputBaseFilename=Audiomatico_Win10-11_{#Version}
MinVersion=10.0
#else
OutputBaseFilename=Instalar_Audiomatico_{#Version}
#endif
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "dist\{#Variante}\{#Exe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#Nombre}"; Filename: "{app}\{#Exe}"
Name: "{group}\Desinstalar {#Nombre}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#Nombre}"; Filename: "{app}\{#Exe}"; Tasks: escritorio

[Run]
Filename: "{app}\{#Exe}"; Description: "Abrir {#Nombre}"; Flags: nowait postinstall skipifsilent
; actualización automática desde el programa (instalación silenciosa): lo vuelve a abrir
Filename: "{app}\{#Exe}"; Parameters: "--minimizado"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
; quita la apertura automática programada, si existe (los datos del usuario se conservan)
Filename: "schtasks.exe"; Parameters: "/Delete /TN ProgramadorAudios /F"; Flags: runhidden; RunOnceId: "QuitarTarea"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
