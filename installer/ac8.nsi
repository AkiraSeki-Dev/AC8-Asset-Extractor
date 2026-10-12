; AC8 Asset Extractor installer (NSIS 3). Built by the Release workflow:
;   makensis -DVERSION=1.1 -DSRC=<staged app folder incl. python\> -DOUT=<setup.exe> installer/ac8.nsi
; Installs per user (no admin) to %LOCALAPPDATA%\Programs\AC8 Asset Extractor. Your data\, logs\ and AC8_Exports\
; are never touched by an upgrade, and the uninstaller asks before removing them.

Unicode true
!include "MUI2.nsh"

!ifndef VERSION
  !define VERSION "0.0"
!endif
!ifndef SRC
  !error "pass -DSRC=<folder to install>"
!endif
!ifndef OUT
  !define OUT "AC8_Asset_Extractor_Setup.exe"
!endif

!define APPNAME "AC8 Asset Extractor"
!define EXE "AC8 Asset Extractor.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\AC8AssetExtractor"

Name "${APPNAME} ${VERSION}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "${APPNAME} ${VERSION}"

VIProductVersion "${VERSION}.0.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} installer"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Tool for modding, research and education; game assets belong to their copyright holders"

!define MUI_ICON "..\launcher\app.ico"
!define MUI_UNICON "..\launcher\app.ico"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!define MUI_LICENSEPAGE_CHECKBOX
!define MUI_LICENSEPAGE_CHECKBOX_TEXT "I understand and accept the disclaimer"
!insertmacro MUI_PAGE_LICENSE "DISCLAIMER.txt"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APPNAME} now"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "${APPNAME}" SecApp
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; replace the program files cleanly (your data\, logs\, AC8_Exports\ and AES key are left alone)
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  File /r "${SRC}\*.*"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${EXE}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk" "$INSTDIR\Uninstall.exe"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "AkiraSeki-Dev"
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "https://github.com/AkiraSeki-Dev/AC8-Asset-Extractor"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXE}"
SectionEnd

LangString DESC_App ${LANG_ENGLISH} "The app, with its own Python and the unpacking tools. Nothing else needs installing."
LangString DESC_Desktop ${LANG_ENGLISH} "Put a shortcut on the desktop."
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecApp} $(DESC_App)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_Desktop)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\AC8 Unpacker\tools"
  Delete "$INSTDIR\AC8 Unpacker\ac8_unpack.py"
  Delete "$INSTDIR\AC8 Unpacker\LICENSE"
  RMDir /r "$INSTDIR\AC8 Unpacker\__pycache__"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\${EXE}.old"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  DeleteRegKey HKCU "${UNINST_KEY}"
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
    "Also delete your settings, AES key, logs and extractions in$\r$\n$INSTDIR ?$\r$\n$\r$\nChoose No to keep them." \
    /SD IDNO IDNO keep
    RMDir /r "$INSTDIR\AC8 Unpacker"
    RMDir /r "$INSTDIR\data"
    RMDir /r "$INSTDIR\logs"
    RMDir /r "$INSTDIR\AC8_Exports"
  keep:
  RMDir "$INSTDIR\AC8 Unpacker"
  RMDir "$INSTDIR"
SectionEnd
