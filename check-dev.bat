@echo off
chcp 65001 >nul

echo ===== 1. 执行 Python 语法检查 =====
python -m py_compile src\business_module\*.py src\common_utils\*.py src\harness_core\*.py
if %errorlevel% neq 0 goto error

echo ===== 2. 执行 Flake8 代码风格检查 =====
flake8 src/
if %errorlevel% neq 0 goto error

echo ===== 3. 执行 Pylint 代码质量检查 =====
pylint src/
if %errorlevel% neq 0 goto error

echo.
echo [OK] 所有校验通过，可正常提交代码、提PR！
pause
exit /b 0

:error
echo.
echo [ERROR] 校验失败，请根据报错信息修复代码后重试！
pause
exit /b 1
