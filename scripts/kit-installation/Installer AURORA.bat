@echo off
rem Kit d'installation d'AURORA : copie AURORA dans C:\AURORA, puis ouvre sa fenetre d'installation.
rem Rien n'est ecrase, et rien n'est telecharge avant le clic sur INSTALLER. Voir LISEZ-MOI.txt.
rem 24/11/2026 : cette fenetre noire se ferme d'elle-meme des que la fenetre d'installation s'ouvre. Elle ne
rem reste ouverte que si la copie s'arrete, pour que son message puisse se lire.
title Installer AURORA
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0poser-aurora.ps1"
if errorlevel 1 pause
