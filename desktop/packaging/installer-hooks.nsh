!macro NSIS_HOOK_POSTINSTALL
  ; In-place upgrades can retain resource files that no longer ship in the
  ; new installer. They are legacy standalone Provider entrypoints, NOT the
  ; WPS/Enhancement backends used by the consolidated Office provider.
  Delete "$INSTDIR\resources\provider-assets\provider_manifests\wps-office.json"
  Delete "$INSTDIR\resources\provider-assets\provider_manifests\office-enhancement.json"
  ; Pin the desktop shortcut to the shipped ICO instead of relying on the
  ; Windows icon cache for the executable's embedded icon.
  IfFileExists "$INSTDIR\resources\pla-desktop.ico" 0 +4
  IfFileExists "$DESKTOP\PLA Desktop.lnk" 0 +2
  CreateShortCut "$DESKTOP\PLA Desktop.lnk" "$INSTDIR\pla-desktop.exe" "" "$INSTDIR\resources\pla-desktop.ico" 0
  IfFileExists "$SMPROGRAMS\PLA Desktop\PLA Desktop.lnk" 0 +2
  CreateShortCut "$SMPROGRAMS\PLA Desktop\PLA Desktop.lnk" "$INSTDIR\pla-desktop.exe" "" "$INSTDIR\resources\pla-desktop.ico" 0
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Remove only the startup registration belonging to this installation.
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PLADesktop"
  ${If} $R0 == '"$INSTDIR\pla-desktop.exe"'
  ${OrIf} $R0 == '"$INSTDIR\pla-desktop.exe" --autostart'
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PLADesktop"
  ${EndIf}
!macroend
