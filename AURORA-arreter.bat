@echo off
rem Arrete AURORA (service et interface lances par AURORA.bat ou start-aurora.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start-aurora.ps1" -Stop
