@echo off
rem Khoi chay Tham Tra Mo Hinh BIM - ban web
rem May nay: http://localhost:8000   ·   Dong nghiep cung mang LAN: http://<IP-may-nay>:8000
cd /d "%~dp0"
python -c "import fastapi, uvicorn, ifcopenshell, fcl" 2>nul || python -m pip install -r requirements.txt
start "" http://localhost:8000
python -m uvicorn webapp.server:app --host 0.0.0.0 --port 8000
pause
