; Instalador de Audiomático (Inno Setup 6). Se compila con compilar.bat.
; Incluye el .exe de 64 bits si existe; si no, instala el de 32 bits (corre en ambos).
#define Nombre "Audiomático"
#define Version "1.3.0"
#define Exe "Audiomatico.exe"

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
OutputBaseFilename=Instalar_Audiomatico_{#Version}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
CloseApplications=yes
#ifexist "dist\64bits\Audiomatico.exe"
ArchitecturesInstallIn64BitMode=x64
#endif

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
#ifexist "dist\64bits\Audiomatico.exe"
Source: "dist\64bits\{#Exe}"; DestDir: "{app}"; Flags: ignoreversion; Check: Is64BitInstallMode
Source: "dist\32bits\{#Exe}"; DestDir: "{app}"; Flags: ignoreversion; Check: not Is64BitInstallMode
#else
Source: "dist\32bits\{#Exe}"; DestDir: "{app}"; Flags: ignoreversion
#endif

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
