!macro NSIS_HOOK_POSTINSTALL
  ; In-place upgrades can retain resource files that no longer ship in the
  ; new installer. They are legacy standalone Provider entrypoints, NOT the
  ; WPS/Enhancement backends used by the consolidated Office provider.
  Delete "$INSTDIR\resources\provider-assets\provider_manifests\wps-office.json"
  Delete "$INSTDIR\resources\provider-assets\provider_manifests\office-enhancement.json"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Remove only the startup registration belonging to this installation.
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PLADesktop"
  ${If} $R0 == '"$INSTDIR\pla-desktop.exe"'
  ${OrIf} $R0 == '"$INSTDIR\pla-desktop.exe" --autostart'
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "PLADesktop"
  ${EndIf}
!macroend
