@echo off
cd /d "%~dp0"
if not exist assets\icon.ico (
  echo Icon missing, extracting from the game ...
  python make_icon.py
)
echo Building ADOFAI_AutoPlay.exe ...
python -m PyInstaller --noconsole --onefile --clean --name ADOFAI_AutoPlay ^
  --icon assets\icon.ico --add-data "assets;assets" ^
  --hidden-import tkinter --hidden-import tkinter.filedialog --hidden-import tkinter.ttk ^
  app.py
if errorlevel 1 goto fail
if exist config.json copy /y config.json dist\config.json >nul
echo.
echo Done -^> dist\ADOFAI_AutoPlay.exe
echo Put config.json next to the exe (it remembers the last played chart).
echo You can copy the whole dist folder anywhere, or just the exe + config.json.
pause
exit /b 0
:fail
echo Build failed. See errors above.
pause
