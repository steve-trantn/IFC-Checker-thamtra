@echo off
rem Dong goi Tham Tra Mo Hinh BIM thanh file .exe (khong can cai Python tren may khac)
cd /d "%~dp0"
python -m pip install pyinstaller -q
python -m PyInstaller --noconfirm --clean --windowed --name ThamTraBIM ^
  --collect-all ifcopenshell --collect-all ifctester --collect-all fcl ^
  --add-data "ifc_checker\viewer;ifc_checker\viewer" ^
  --hidden-import anthropic --collect-submodules anthropic ^
  --exclude-module streamlit ^
  main.py
echo.
echo Xong. Thu muc chuong trinh: dist\ThamTraBIM  (chay ThamTraBIM.exe)
pause
