# --- Configuration ---
$PYTHON_VERSION = "3.10"
$PROJECT_REPO = "https://github.com/mynl/archivum_project.git"
$BUILD_DIR = (Get-Item -Path "Env:TEMP").Value + "\readthedocs_build"
$VENV_DIR = Join-Path $BUILD_DIR "venv"
$HTML_OUTPUT_DIR = Join-Path $BUILD_DIR "html"

# --- Prepare Environment ---
Write-Host "Cleaning previous build directory..."
Remove-Item -LiteralPath $BUILD_DIR -Recurse -Force -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Path $BUILD_DIR | Out-Null

# --- Clone Repository ---
Write-Host "Cloning repository..."
git clone --depth 1 $PROJECT_REPO $BUILD_DIR
if ($LASTEXITCODE -ne 0) {
    Write-Error "Git clone failed. Exiting."
    exit $LASTEXITCODE
}

Set-Location $BUILD_DIR

# --- Fetch latest changes ---
Write-Host "Fetching latest changes..."
git fetch origin --force --prune --prune-tags --depth 50 refs/heads/master:refs/remotes/origin/master
if ($LASTEXITCODE -ne 0) {
    Write-Error "Git fetch failed. Exiting."
    exit $LASTEXITCODE
}

# --- Checkout master branch ---
Write-Host "Checking out master branch..."
git checkout --force origin/master
if ($LASTEXITCODE -ne 0) {
    Write-Error "Git checkout failed. Exiting."
    exit $LASTEXITCODE
}

# --- Setup Virtual Environment ---
Write-Host "Creating virtual environment for Python $PYTHON_VERSION..."
# Assuming 'uv' is installed and available in PATH.
# If not, you might need to install it: uv pip install uv
uv venv $VENV_DIR --python $PYTHON_VERSION
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create virtual environment. Ensure uv and Python $PYTHON_VERSION are available. Exiting."
    exit $LASTEXITCODE
}

# Activate the virtual environment
# Note: PowerShell activation script requires a specific dot-sourcing
. "$VENV_DIR\Scripts\Activate.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to activate virtual environment. Exiting."
    exit $LASTEXITCODE
}

# --- Install Dependencies ---
Write-Host "Upgrading uv and setuptools..."
uv pip install --upgrade uv setuptools
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to upgrade uv and setuptools. Exiting."
    exit $LASTEXITCODE
}

Write-Host "Installing Sphinx..."
uv pip install --upgrade sphinx
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install Sphinx. Exiting."
    exit $LASTEXITCODE
}

Write-Host "Installing project dependencies from pyproject.toml..."
uv pip install --upgrade --no-cache-dir .[dev]
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install project dependencies. Exiting."
    exit $LASTEXITCODE
}

# --- Build HTML Documentation ---
Write-Host "Building HTML documentation..."
python -m sphinx -T -b html -d "_build\doctrees" -D language=en docs $HTML_OUTPUT_DIR
if ($LASTEXITCODE -ne 0) {
    Write-Error "HTML build failed. Exiting."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "HTML documentation built successfully in $HTML_OUTPUT_DIR"

# --- Optional: Build LaTeX/PDF (commented out) ---
# Write-Host "Building LaTeX/PDF documentation..."
# python -m sphinx -T -b latex -d "_build\doctrees" -D language=en docs (Join-Path $BUILD_DIR "pdf")
# if ($LASTEXITCODE -ne 0) {
#     Write-Error "LaTeX build failed. Exiting."
#     exit $LASTEXITCODE
# }
#
# Write-Host "Running latexmk to generate PDF..."
# Set-Location (Join-Path $BUILD_DIR "pdf")
# latexmk -r latexmkrc -pdf -f -dvi- -ps- -jobname=archivum-project -interaction=nonstopmode
# if ($LASTEXITCODE -ne 0) {
#     Write-Error "PDF generation failed. Exiting."
#     exit $LASTEXITCODE
# }
# Set-Location $BUILD_DIR
#
# Write-Host "PDF documentation built successfully in (Join-Path $BUILD_DIR "pdf")"
