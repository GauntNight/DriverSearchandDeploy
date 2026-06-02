@echo off
echo Starting Redis Server...
netstat -ano | findstr ":6379" | findstr LISTENING >nul 2>nul
if not errorlevel 1 (
    echo Redis/Memurai is already listening on port 6379 - nothing to start.
    goto :eof
)
echo Press Ctrl+C to stop.
if exist "tools\redis\redis-server.exe" (
    tools\redis\redis-server.exe redis.conf
) else (
    where memurai >nul 2>nul
    if not errorlevel 1 (
        memurai redis.conf
    ) else (
        where redis-server >nul 2>nul
        if not errorlevel 1 (
            redis-server redis.conf
        ) else (
            echo ERROR: Redis not found. Install with: winget install Memurai.MemuraiDeveloper
        )
    )
)
