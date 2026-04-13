@echo off
chcp 65001 >nul
echo ==========================================
echo     RAG Agent 启动器
echo ==========================================
echo.

:: 获取项目目录
set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

:: 检查虚拟环境
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo [1/2] 启动后端服务...
start "RAG Backend (8000)" cmd /k "cd /d "%PROJECT_DIR%" && %PYTHON% -m src.ragent_backend.app"

timeout /t 4 /nobreak >nul

echo [2/2] 启动前端服务...
start "RAG Frontend (5173)" cmd /k "cd /d "%PROJECT_DIR%\frontend" && npm run dev -- --host"

timeout /t 2 /nobreak >nul

echo.
echo ==========================================
echo     服务已启动！
echo ==========================================
echo.
echo 后端 API: http://localhost:8000
echo 前端页面: http://localhost:5173
echo.
echo [提示] 关闭黑色窗口即可停止服务
echo.
pause
start http://localhost:5173
