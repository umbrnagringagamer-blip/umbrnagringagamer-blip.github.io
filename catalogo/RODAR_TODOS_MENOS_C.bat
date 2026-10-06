@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set DRIVES=
for %%L in (D E F G H I J K L M N O P Q R S T U V W X Y Z) do if exist %%L:\ set DRIVES=!DRIVES! %%L:\
echo HDs encontrados (menos o C:):!DRIVES!
python catalogo_clipes.py!DRIVES!
pause
