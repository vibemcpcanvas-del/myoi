@echo off
chcp 65001 >nul
title SCHALE 전투 추적 시스템 - 바탕화면 바로가기 생성

cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

echo ========================================================================
echo   바탕화면에 'SCHALE 전투 추적 시스템' 바로가기를 생성합니다...
echo ========================================================================
echo.

:: 1. Python 인터프리터 자동 탐색
set "PY_CMD="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0.venv\Scripts\python.exe"
    goto :USE_PY
)
if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0venv\Scripts\python.exe"
    goto :USE_PY
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :USE_PY
)

:: Python 미발견 시 PowerShell 직접 생성
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); $s = $ws.CreateShortcut((Join-Path $d 'SCHALE 전투 추적 시스템.lnk')); $s.TargetPath = '%~dp0실행하기.bat'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'SCHALE 전투 추적 및 YOLO 데이터 수집 시스템'; $s.Save()"
goto :CHECK_STATUS

:USE_PY
"%PY_CMD%" scripts\launcher.py shortcut

:CHECK_STATUS
if errorlevel 1 (
    echo.
    echo [오류] 바로가기 생성에 실패하였습니다.
    pause >nul
    exit /b 1
)

echo.
echo 3초 후 이 창은 자동으로 닫힙니다...
timeout /t 3 >nul
exit /b 0
