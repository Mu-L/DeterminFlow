!macro StopDeterminFlowBackend
  DetailPrint "Stopping DeterminFlow backend"
  nsExec::ExecToLog '"$SYSDIR\taskkill.exe" /F /T /IM determinflow-backend.exe'
  Pop $0
  Sleep 250
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro StopDeterminFlowBackend
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro StopDeterminFlowBackend
!macroend
