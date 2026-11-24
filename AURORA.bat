@echo off
rem AURORA : demarre le service et l'interface sans fenetre, puis ouvre l'application.
rem Journaux : %USERPROFILE%\.aurora\logs  -  Arret : AURORA-arreter.bat
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start-aurora.ps1" -Hidden
