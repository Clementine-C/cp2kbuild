@echo off
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
python -m cp2kbuild %*
