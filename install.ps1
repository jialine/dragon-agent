# ================================================================
#  Dragon Agent — Windows PowerShell 一键部署
#  用法: irm https://gitee.com/jialine/dragon-agent/raw/master/install.ps1 | iex
#  选项: -StartWebUI -StartGateway -WebUIPort 5000 -GatewayPort 8090
# ================================================================
param(
    [string]$InstallDir = "$env:USERPROFILE\dragon-agent",
    [string]$Branch = "master",
    [switch]$SkipTest,
    [switch]$StartWebUI,
    [int]$WebUIPort = 5000,
    [switch]$StartGateway,
    [int]$GatewayPort = 8090
)

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║       🐉 Dragon Agent Installer     ║" -ForegroundColor Cyan
Write-Host "  ║    Windows · 默认 DeepSeek V4 Pro   ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/7] 检查 Python..." -ForegroundColor White
$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Host "未找到 Python >= 3.11，请先安装: https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "或: winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
$ver = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "  ✓ Python $ver" -ForegroundColor Green

Write-Host "[2/7] 检查 Git..."
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "请先安装 Git: https://git-scm.com/download/win" -ForegroundColor Red
    Write-Host "或: winget install Git.Git" -ForegroundColor Yellow
    exit 1
}
Write-Host "  ✓ git" -ForegroundColor Green

Write-Host "[3/7] 获取代码..."
if (Test-Path "$InstallDir\.git") {
    Set-Location $InstallDir
    git pull origin $Branch --quiet 2>$null
} else {
    git clone --depth 1 --branch $Branch "https://gitee.com/jialine/dragon-agent.git" $InstallDir 2>$null
    if (-not $?) {
        git clone --depth 1 "https://github.com/jialine/dragon-agent.git" $InstallDir 2>$null
    }
}
Set-Location $InstallDir
Write-Host "  ✓ Done" -ForegroundColor Green

Write-Host "[4/7] 安装依赖..."
if (-not (Test-Path ".venv")) { & $python.Source -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip -q 2>$null
pip install -r requirements.txt -q 2>$null
Write-Host "  ✓ 核心依赖" -ForegroundColor Green

Write-Host "[5/7] 默认配置..."
if (-not (Test-Path "config.yaml")) {
    @"
gateway:
  host: "0.0.0.0"
  port: $GatewayPort

dispatch:
  global_api:
    model: "deepseek-v4-pro"
    base_url: "https://api.andlapi.cn/v1"
    api_key: "${env:ANDLAPI_API_KEY}"

providers:
  deepseek:
    api_key: "${env:DEEPSEEK_API_KEY:-sk-your-key}"
    base_url: "https://api.andlapi.cn/v1"
    model: "deepseek-chat"
"@ | Out-File -Encoding utf8 config.yaml
    Write-Host "  ✓ config.yaml" -ForegroundColor Green
    Write-Host "  ⚠ 获取 Key: https://andlapi.cn（注册送 ¥10）" -ForegroundColor Yellow
    Write-Host "  ⚠ 设置 Key: `$env:ANDLAPI_API_KEY = 'sk-...'" -ForegroundColor Yellow
    Write-Host "  ⚠  或: `$env:DEEPSEEK_API_KEY = 'sk-...'" -ForegroundColor Yellow
}

# ── 6. WebUI (可选) ──────────────────────────────────────────
if ($StartWebUI) {
    Write-Host ""
    Write-Host "[6/7] 安装 WebUI 依赖..." -ForegroundColor White
    if (Test-Path "webui\requirements.txt") {
        pip install -r webui\requirements.txt -q 2>$null
        Write-Host "  ✓ Flask + Flask-CORS" -ForegroundColor Green
    }
    Write-Host "[6/7] 启动 WebUI（端口 $WebUIPort）..." -ForegroundColor White
    if (Test-Path "webui\app.py") {
        $env:PORT = "$WebUIPort"
        $webuiProc = Start-Process -NoNewWindow -PassThru python -ArgumentList "webui\app.py"
        Start-Sleep -Seconds 3
        if (-not $webuiProc.HasExited) {
            Write-Host "  ✓ WebUI 已启动: http://localhost:$WebUIPort (PID: $($webuiProc.Id))" -ForegroundColor Green
        } else {
            Write-Host "  ✗ WebUI 启动失败" -ForegroundColor Red
        }
    } else {
        Write-Host "  ✗ webui\app.py 不存在" -ForegroundColor Red
    }
}

# ── 7. Gateway (可选) ──────────────────────────────────────
if ($StartGateway) {
    Write-Host ""
    Write-Host "[7/7] 启动 Dragon Gateway（端口 $GatewayPort）..." -ForegroundColor White

    # Update config.yaml port if different
    if ($GatewayPort -ne 8090) {
        (Get-Content config.yaml -Raw) -replace 'port: 8090', "port: $GatewayPort" | Set-Content config.yaml
    }

    $env:PORT = "$GatewayPort"
    $gwProc = Start-Process -NoNewWindow -PassThru python -ArgumentList "-m", "dragon", "gateway", "start"
    Start-Sleep -Seconds 3
    if (-not $gwProc.HasExited) {
        Write-Host "  ✓ Gateway 已启动: http://localhost:$GatewayPort (PID: $($gwProc.Id))" -ForegroundColor Green

        # Windows 开机自启提示
        Write-Host ""
        Write-Host "  💡 开机自启: 将以下内容保存为 dragon-gateway.ps1，放入 shell:startup" -ForegroundColor Yellow
        Write-Host "     ────────────────────────────────────────" -ForegroundColor DarkGray
        Write-Host "     `$env:PORT = '$GatewayPort'" -ForegroundColor DarkGray
        Write-Host "     `$env:DEEPSEEK_API_KEY = 'sk-your-key'" -ForegroundColor DarkGray
        Write-Host "     Set-Location '$InstallDir'" -ForegroundColor DarkGray
        Write-Host "     .\.venv\Scripts\Activate.ps1" -ForegroundColor DarkGray
        Write-Host "     python -m dragon gateway start" -ForegroundColor DarkGray
        Write-Host "     ────────────────────────────────────────" -ForegroundColor DarkGray
        Write-Host "     Win+R → shell:startup → 粘贴脚本" -ForegroundColor DarkGray
    } else {
        Write-Host "  ✗ Gateway 启动失败" -ForegroundColor Red
    }
}

# ── 完成 ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║   🐉 Dragon Agent 部署完成！        ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Green
Write-Host "  目录: $InstallDir"
Write-Host "  激活: .venv\Scripts\Activate.ps1"
Write-Host "  WebUI: `$env:PORT=5000; python webui\app.py"
Write-Host "  Gateway: `$env:PORT=$GatewayPort; python -m dragon gateway start"
Write-Host ""
Write-Host "  🔑 获取 Key: https://andlapi.cn（注册送 ¥10）" -ForegroundColor Cyan
Write-Host "  ⚙  设置 Key: `$env:ANDLAPI_API_KEY = 'sk-...'" -ForegroundColor Yellow
Write-Host "  ⚙  或: `$env:DEEPSEEK_API_KEY = 'sk-...'" -ForegroundColor Yellow
