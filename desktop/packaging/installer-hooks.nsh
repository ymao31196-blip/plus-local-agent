!macro NSIS_HOOK_PREUNINSTALL
  ; Remove only the startup registration belonging to this installation.
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PLADesktop"
  ${If} $R0 == '"$INSTDIR\pla-desktop.exe"'
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PLADesktop"
  ${EndIf}
!macroend
