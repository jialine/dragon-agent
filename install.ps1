# ================================================================
#  Dragon Agent — Windows PowerShell 一键部署
#  用法: irm https://gitee.com/jialine/dragon-agent/raw/master/install.ps1 | iex
# ================================================================
param(
    [string]$InstallDir = "$env:USERPROFILE\dragon-agent",
    [string]$Branch = "master",
    [switch]$SkipTest
)

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║       🐉 Dragon Agent Installer     ║" -ForegroundColor Cyan
Write-Host "  ║    Windows · 默认 DeepSeek V4 Pro   ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/5] 检查 Python..." -ForegroundColor White
$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Host "未找到 Python >= 3.11，请先安装: https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "或: winget install Python.Python.3.12" -ForegroundColor Yellow
    exit 1
}
$ver = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "  ✓ Python $ver" -ForegroundColor Green

Write-Host "[2/5] 检查 Git..."
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "请先安装 Git: https://git-scm.com/download/win" -ForegroundColor Red
    Write-Host "或: winget install Git.Git" -ForegroundColor Yellow
    exit 1
}
Write-Host "  ✓ git" -ForegroundColor Green

Write-Host "[3/5] 获取代码..."
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

Write-Host "[4/5] 安装依赖..."
if (-not (Test-Path ".venv")) { & $python.Source -m venv .venv }
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip -q 2>$null
pip install -r requirements.txt -q 2>$null
Write-Host "  ✓ Done" -ForegroundColor Green

Write-Host "[5/5] 默认配置..."
if (-not (Test-Path "config.yaml")) {
    @"
gateway:
  host: "0.0.0.0"
  port: 8090

dispatch:
  global_api:
    model: "deepseek-v4-pro"
    base_url: "https://api.andlapi.cn/v1"
    api_key: "${env:ANDLAPI_API_KEY}"
"@ | Out-File -Encoding utf8 config.yaml
    Write-Host "  ✓ config.yaml" -ForegroundColor Green
    Write-Host "  ⚠ 获取 Key: https://andlapi.cn（注册送 ¥10）" -ForegroundColor Yellow
    Write-Host "  ⚠ 设置 Key: `$env:ANDLAPI_API_KEY = 'sk-...'" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║   🐉 Dragon Agent 部署完成！        ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Green
Write-Host "  获取 Key: https://andlapi.cn（注册送 ¥10）"
Write-Host "  激活: .venv\Scripts\Activate.ps1"
Write-Host "  启动: python dragon_agent_loop.py"
Write-Host "  Key:  `$env:ANDLAPI_API_KEY = 'sk-...'"
