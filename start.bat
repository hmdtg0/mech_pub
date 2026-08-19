@echo off
title Mech Order Helper
cd /d "%~dp0"
set MECH_LOCAL_DEV=1
streamlit run app.py --server.port 8502
pause
