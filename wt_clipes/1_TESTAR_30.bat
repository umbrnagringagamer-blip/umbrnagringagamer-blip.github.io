@echo off
cd /d "%~dp0"
echo Instalando dependencias (so na primeira vez)...
python -m pip install -q -r requirements.txt || (echo Instale o Python 3.10+ em python.org e marque "Add to PATH". & pause & exit /b 1)
echo Testando em 30 clipes aleatorios...
python wt_clipes.py testar --n 30
echo.
echo Pronto! Os mosaicos estao em teste_30. Envie a pasta teste_30 para o Claude conferir.
pause
