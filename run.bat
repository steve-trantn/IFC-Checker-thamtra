@echo off
rem Khoi chay Tham Tra Mo Hinh BIM (giao dien desktop)
cd /d "%~dp0"
where python >nul 2>nul || (echo Chua cai Python 3.10+ hoac chua them vao PATH. & pause & exit /b 1)
python -c "import ifcopenshell, openpyxl" 2>nul || (
  echo Dang cai thu vien can thiet...
  python -m pip install -r requirements.txt || (pause & exit /b 1)
)
echo Dang mo chuong trinh... (cua so nay se dong khi tat chuong trinh)
python main.py
if errorlevel 1 pause
