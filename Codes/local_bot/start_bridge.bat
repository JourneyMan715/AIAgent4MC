@echo off
chcp 65001 >nul
title 本地 Bot 桥接（关闭此窗口 = 停止 Bot）
cd /d "%~dp0"

echo ============================================================
echo  本地 Bot 桥接
echo  这个窗口会一直占用（它是个常驻服务），不要在这里敲别的命令。
echo  另开一个窗口跑 python main.py ...
echo ============================================================
echo.

node server.js

echo.
echo 桥接已退出。按任意键关闭窗口。
pause >nul
