; Instalador de ARGUS para Windows (Inno Setup 6.4 o posterior; 0.17.0, ADR-022).
;
; Lo compila packaging\construir.py con los valores de esta construcción:
;   ISCC.exe /DAppVersion=0.17.0 /DSourceDir=...\dist\ARGUS /DOutputDir=...\dist\paquetes
;            /DOutputName=ARGUS-0.17.0-Windows-instalador /DIconFile=...\argus.ico /DReadme=...\LEEME.txt
;            [/DClaveFichero=...\clave.iss]  argus.iss
; Con ClaveFichero (un fichero con  #define Clave "..." ) el instalador pide la contraseña y lleva los ficheros
; cifrados (XChaCha20): sin ella no se puede instalar ni sacar nada del instalador.
;
; Por omisión se instala solo para el usuario (sin permisos de administrador, en %LOCALAPPDATA%\Programs\ARGUS);
; el instalador deja elegir instalarlo para todos.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\ARGUS"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist\paquetes"
#endif
#ifndef OutputName
  #define OutputName "ARGUS-" + AppVersion + "-Windows-instalador"
#endif
#ifndef IconFile
  #define IconFile "..\..\build\iconos\argus.ico"
#endif
#ifndef Readme
  #define Readme "..\LEEME.txt"
#endif
#ifdef ClaveFichero
  #include ClaveFichero
#endif

[Setup]
; El mismo AppId en todas las versiones: así cada una sustituye a la anterior.
AppId={{DFB19C1E-AF4A-464E-A8C7-591729650167}
AppName=ARGUS
AppVersion={#AppVersion}
AppVerName=ARGUS {#AppVersion}
AppPublisher=Rafael Guerrero Alonso
AppComments=Constructor y visor de geometrías PENGEOM y ficheros de entrada de penhan (PENELOPE)
VersionInfoVersion={#AppVersion}
VersionInfoProductName=ARGUS
VersionInfoDescription=Instalador de ARGUS {#AppVersion}
DefaultDirName={autopf}\ARGUS
DefaultGroupName=ARGUS
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\ARGUS.exe
UninstallDisplayName=ARGUS {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes
#ifdef Clave
Password={#Clave}
Encryption=yes
#endif

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; Flags: unchecked

[InstallDelete]
; Lo de la versión anterior se quita antes de copiar la nueva (no quedan bibliotecas viejas mezcladas).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Readme}"; DestDir: "{app}"; DestName: "LEEME.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\ARGUS"; Filename: "{app}\ARGUS.exe"; Comment: "Geometrías PENGEOM y ficheros de penhan"
Name: "{autodesktop}\ARGUS"; Filename: "{app}\ARGUS.exe"; Tasks: escritorio

[Registry]
; «Abrir con» ARGUS para los .geo y los .in (sin quitárselos a la aplicación que los abra ya).
Root: HKA; Subkey: "Software\Classes\ARGUS.Fichero"; ValueType: string; ValueName: ""; ValueData: "Fichero de PENGEOM o de penhan"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\ARGUS.Fichero\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\ARGUS.exe,0"
Root: HKA; Subkey: "Software\Classes\ARGUS.Fichero\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ARGUS.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.geo\OpenWithProgids"; ValueType: string; ValueName: "ARGUS.Fichero"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.in\OpenWithProgids"; ValueType: string; ValueName: "ARGUS.Fichero"; ValueData: ""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\ARGUS.exe"; Description: "Abrir ARGUS"; Flags: nowait postinstall skipifsilent
