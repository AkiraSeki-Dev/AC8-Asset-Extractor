@echo off
rem Builds "AC8 Asset Extractor.exe" in the folder above. Needs Go (https://go.dev/dl/).
cd /d "%~dp0"
go build -ldflags "-H windowsgui -s -w" -o "..\AC8 Asset Extractor.exe" .
if errorlevel 1 (echo Build failed.) else (echo Built: "%~dp0..\AC8 Asset Extractor.exe")
pause
