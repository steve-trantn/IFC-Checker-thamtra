@echo off
rem Khoi chay Tham Tra Mo Hinh BIM (giao dien web, mo trong trinh duyet)
cd /d "%~dp0"
python -c "import streamlit" 2>nul || python -m pip install -r requirements.txt
python -m streamlit run web_app.py --server.maxUploadSize 1024
pause
