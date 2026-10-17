#!/usr/bin/env bash
# scripts/ci-local.sh
# Lokaler CI-Lauf — ersetzt das frühere .github/workflows/ci.yml.
#
# Verwendung:
#   bash scripts/ci-local.sh
#   bash scripts/ci-local.sh --skip-e2e --skip-docker
#
# E2E laeuft gegen einen eigenen Preview-Server des aktuellen Builds (Port
# E2E_PORT, Standard 4181 — startet Playwright selbst per webServer), NIE gegen
# Port 8080: dort laeuft die echte Installation. Alle API-Aufrufe sind gemockt.
# Die Smoke-Tests der Windows-Skripte (scripts/test-scripts.ps1) laufen nur
# ueber ci-local.ps1.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

skip_e2e=0
skip_docker=0
skip_frontend=0
skip_backend=0
for arg in "$@"; do
    case "$arg" in
        --skip-e2e) skip_e2e=1 ;;
        --skip-docker) skip_docker=1 ;;
        --skip-frontend) skip_frontend=1 ;;
        --skip-backend) skip_backend=1 ;;
    esac
done

failed=()

run_step() {
    local name="$1"; shift
    echo ""
    echo "=========================================="
    echo "  $name"
    echo "=========================================="
    local start=$SECONDS
    if "$@"; then
        echo "[OK] $name ($((SECONDS-start))s)"
    else
        echo "[FAIL] $name ($((SECONDS-start))s)"
        failed+=("$name")
    fi
}

backend_tests() {
    cd backend
    python -m pytest tests/ -q --tb=short
    local rc=$?
    cd ..
    return $rc
}

frontend_build() {
    cd frontend
    if [ ! -d node_modules ]; then npm ci || { cd ..; return 1; }; fi
    npm run build
    local rc=$?
    cd ..
    return $rc
}

frontend_unit_tests() {
    cd frontend
    npm test
    local rc=$?
    cd ..
    return $rc
}

docker_backend() { docker build -t zettelwirtschaft-backend ./backend; }
docker_frontend() { docker build --build-context repo=. -t zettelwirtschaft-frontend ./frontend; }

# Gemockte API, kein Backend/Stack noetig: Playwright baut das Frontend und
# startet `vite preview --strictPort` auf E2E_PORT. Beide Projekte (Desktop-
# Chromium + Pixel 5), damit mobile-only-Regressionen auffallen. BASE_URL wird
# geleert, sonst wuerde der eigene Preview-Server umgangen.
e2e_tests() {
    cd e2e
    if [ ! -d node_modules ]; then
        npm ci || { cd ..; return 1; }
        npx playwright install --with-deps chromium
    fi
    npm run typecheck || { cd ..; return 1; }
    env -u BASE_URL npx playwright test --reporter=list
    local rc=$?
    cd ..
    return $rc
}

[ $skip_backend -eq 0 ] && run_step "Backend pytest" backend_tests
[ $skip_frontend -eq 0 ] && {
    run_step "Frontend npm build" frontend_build
    run_step "Frontend Vitest" frontend_unit_tests
}
[ $skip_docker -eq 0 ] && {
    run_step "Docker build backend" docker_backend
    run_step "Docker build frontend" docker_frontend
}
[ $skip_e2e -eq 0 ] && run_step "Playwright E2E" e2e_tests

echo ""
echo "=========================================="
if [ ${#failed[@]} -eq 0 ]; then
    echo "  ALLE CHECKS GRUEN"
    exit 0
else
    echo "  FEHLGESCHLAGEN: ${failed[*]}"
    exit 1
fi
