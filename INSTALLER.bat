@echo off
rem AURORA : installe ce qu'il faut sur ce PC (environnement Python, bibliotheques, interface, modeles locaux, icone).
rem Rien n'est telecharge sans accord. Pour seulement verifier : installer-aurora.ps1 -Verifier
rem 22/11/2026 : une vraie fenetre (fenetre-installation.ps1) lance l'installateur cache, montre ses etapes a cote de
rem la sphere d'AURORA animee, et ne telecharge qu'apres le clic sur INSTALLER. En secours : INSTALLER.bat console,
rem qui garde la console et ses questions (o/n) ; c'est aussi ce qui tourne si la fenetre manque.
if /i "%~1"=="console" goto console
if not exist "%~dp0fenetre-installation.ps1" goto console
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0fenetre-installation.ps1"
exit /b 0
:console
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer-aurora.ps1"
pause
