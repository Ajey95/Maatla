param([int]$Port = 8008)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$privateEnv = Join-Path $projectRoot 'backend/.env'
$frontendBuild = Join-Path $projectRoot 'frontend/dist/index.html'
$python = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'

if (-not (Test-Path -LiteralPath $privateEnv)) {
    throw 'Copy backend/.env.example to backend/.env and fill the private settings first.'
}
if (-not (Test-Path -LiteralPath $frontendBuild)) {
    throw 'Build the frontend first: npm ci --prefix frontend; npm run build --prefix frontend'
}
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create the Python environment and install backend/requirements.txt first.'
}

& $python -m uvicorn app.main:app --app-dir (Join-Path $projectRoot 'backend') --host 127.0.0.1 --port $Port --no-access-log
