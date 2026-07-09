@echo off
cd /d "%~dp0"

echo === Relic Picker v5 - Build ===
echo.

:: Clean old builds
:: if exist dist rmdir /s /q dist
:: if exist build rmdir /s /q build

:: Build
pyinstaller --noconfirm --onefile --windowed ^
  --name "RelicPicker_v5" ^
  --add-data "static;static" ^
  --add-data "proto;proto" ^
  --add-data "ae_names.json;." ^
  --hidden-import grpc ^
  --hidden-import pywebview ^
  --hidden-import internal_pb2 ^
  --hidden-import internal_pb2_grpc ^
  --hidden-import soapstone_pb2 ^
  --hidden-import soapstone_pb2_grpc ^
  --hidden-import common_pb2 ^
  --hidden-import common_pb2_grpc ^
  --collect-all grpc ^
  --collect-all google ^
  --collect-all pywebview ^
  main.py

if %ERRORLEVEL% NEQ 0 (
  echo.
  echo === Build FAILED ===
  pause
  exit /b 1
)

echo.
echo === Build OK ===
echo dist\RelicPicker_v5.exe
pause
