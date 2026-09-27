@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0run.ps1" -Refetch -Retitle -Revision
pause
