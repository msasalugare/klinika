#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
#  Medicinska Klinika — Kompletan Install Skript
#  Testiran na: Orange Pi 5 Pro (16GB), Ubuntu 24.04 ARM64
#  Pokreni: bash install.sh
# ═══════════════════════════════════════════════════════════════════════════════

set -e
KLINIKA_DIR="$HOME/docker/Klinika"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PLAVA='\033[0;34m'; ZELENA='\033[0;32m'; ZUTA='\033[1;33m'
CRVENA='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'

info()   { echo -e "${PLAVA}[INFO]${RESET}  $1"; }
uspeh()  { echo -e "${ZELENA}[OK]${RESET}    $1"; }
upozor() { echo -e "${ZUTA}[WARN]${RESET}  $1"; }
greska() { echo -e "${CRVENA}[ERR]${RESET}   $1"; exit 1; }

echo ""
echo -e "${BOLD}╔════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║        Medicinska Klinika — Fresh Install          ║${RESET}"
echo -e "${BOLD}║   MariaDB · Orthanc · Flask · Ollama · n8n         ║${RESET}"
echo -e "${BOLD}╚════════════════════════════════════════════════════╝${RESET}"
echo ""

# ── Korak 1: Sistem ───────────────────────────────────────────────────────────
info "Ažuriranje sistema..."
sudo apt-get update -qq
sudo apt-get install -y -qq curl wget unzip jq
uspeh "Sistem ažuriran."

# ── Korak 2: Docker ───────────────────────────────────────────────────────────
info "Provjera Docker-a..."
if ! command -v docker &> /dev/null; then
    info "Instaliram Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    uspeh "Docker instaliran."
    echo ""
    echo -e "${ZUTA}VAŽNO: Moraš se odjaviti i prijaviti da bi docker radio bez sudo.${RESET}"
    echo -e "${ZUTA}Ili pokreni: newgrp docker && bash install.sh${RESET}"
    echo ""
    newgrp docker
else
    uspeh "Docker $(docker --version | cut -d' ' -f3 | tr -d ',')"
fi

# ── Korak 3: Fajlovi ──────────────────────────────────────────────────────────
info "Provjera fajlova..."
for f in app.py Dockerfile docker-compose.yml requirements.txt mkb10.json; do
    [ -f "$SCRIPT_DIR/$f" ] || greska "Nedostaje: $f"
done
[ -d "$SCRIPT_DIR/templates" ] && \
    [ "$(ls $SCRIPT_DIR/templates/*.html 2>/dev/null | wc -l)" -gt 0 ] || \
    greska "Nedostaje folder templates/ sa HTML fajlovima"
uspeh "Svi fajlovi OK."

# ── Korak 4: Direktorijumi ────────────────────────────────────────────────────
info "Kreiranje strukture..."
mkdir -p "$KLINIKA_DIR/templates"
uspeh "$KLINIKA_DIR kreiran."

# ── Korak 5: Kopiranje fajlova ────────────────────────────────────────────────
info "Kopiranje fajlova..."
cp "$SCRIPT_DIR/app.py"             "$KLINIKA_DIR/app.py"
cp "$SCRIPT_DIR/Dockerfile"         "$KLINIKA_DIR/Dockerfile"
cp "$SCRIPT_DIR/docker-compose.yml" "$KLINIKA_DIR/docker-compose.yml"
cp "$SCRIPT_DIR/requirements.txt"   "$KLINIKA_DIR/requirements.txt"
cp "$SCRIPT_DIR/templates/"*.html   "$KLINIKA_DIR/templates/"
[ ! -f "$KLINIKA_DIR/mkb10.json" ] && \
    cp "$SCRIPT_DIR/mkb10.json" "$KLINIKA_DIR/mkb10.json" && \
    uspeh "mkb10.json kopiran." || uspeh "mkb10.json već postoji."
uspeh "Fajlovi kopirani."

# ── Korak 6: Pokretanje servisa ───────────────────────────────────────────────
cd "$KLINIKA_DIR"

SERVISI_RADE=false
docker compose ps 2>/dev/null | grep -q "running\|Up" && SERVISI_RADE=true

if [ "$SERVISI_RADE" = true ]; then
    info "Sistem radi — rebuild aplikacije..."
    docker compose up -d --build klinika
else
    info "Fresh install — pokrećem sve servise..."
    docker compose pull --quiet 2>/dev/null || true
    docker compose up -d --build
fi
uspeh "Servisi pokrenuti."

# ── Korak 7: Čekanje na Flask ─────────────────────────────────────────────────
echo ""
info "Čekam da Flask bude spreman..."
for i in $(seq 1 60); do
    sleep 3
    if curl -s --max-time 2 "http://localhost:5000" > /dev/null 2>&1; then
        uspeh "Flask spreman! (${i}x3=${i*3}s)"
        break
    fi
    echo -ne "\r${PLAVA}[INFO]${RESET}  Čekam Flask... ($((i*3))s)   "
done
echo ""

# ── Korak 8: Preuzimanje AI modela ────────────────────────────────────────────
echo ""
info "Provjera Ollama modela..."

# Čekaj da Ollama bude spremna
for i in $(seq 1 20); do
    sleep 3
    if docker exec klinika-ollama-1 ollama list &>/dev/null; then
        break
    fi
    echo -ne "\r${PLAVA}[INFO]${RESET}  Čekam Ollama... ($((i*3))s)   "
done
echo ""

MODEL_POSTOJI=$(docker exec klinika-ollama-1 ollama list 2>/dev/null | grep "qwen2.5:7b" || echo "")
if [ -z "$MODEL_POSTOJI" ]; then
    info "Preuzimam qwen2.5:7b model (~4.5GB). Ovo može trajati 10-30 minuta..."
    upozor "Ne prekidaj proces!"
    docker exec klinika-ollama-1 ollama pull qwen2.5:7b
    uspeh "Model preuzet!"
else
    uspeh "Model qwen2.5:7b već postoji."
fi

# ── Korak 9: Provjera baze ────────────────────────────────────────────────────
echo ""
info "Provjera baze podataka..."
sleep 5
MKB=$(docker exec klinika-mariadb-1 mariadb -u klinika -pklinika123 klinika \
    -se "SELECT COUNT(*) FROM mkb10;" 2>/dev/null || echo "0")
LEKOVI=$(docker exec klinika-mariadb-1 mariadb -u klinika -pklinika123 klinika \
    -se "SELECT COUNT(*) FROM lekovi;" 2>/dev/null || echo "0")
TIPOVI=$(docker exec klinika-mariadb-1 mariadb -u klinika -pklinika123 klinika \
    -se "SELECT COUNT(*) FROM tipovi_pregleda;" 2>/dev/null || echo "0")

uspeh "MKB-10 srpski: $MKB dijagnoza"
uspeh "ALIMS lekovi:  $LEKOVI lekova"
uspeh "Tipovi pregleda: $TIPOVI"

# ── Status ────────────────────────────────────────────────────────────────────
echo ""
info "Status svih servisa:"
docker compose ps
echo ""

IP=$(hostname -I | awk '{print $1}')

echo -e "${BOLD}╔════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║              Instalacija završena! ✅              ║${RESET}"
echo -e "${BOLD}╚════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "${BOLD}Pristup:${RESET}"
echo -e "  ${ZELENA}Aplikacija:${RESET}  http://${IP}:5000"
echo -e "  ${ZELENA}Orthanc:${RESET}     http://${IP}:8042"
echo -e "  ${ZELENA}n8n:${RESET}         http://${IP}:5678"
echo ""
echo -e "${BOLD}Default logini (PROMENI odmah!):${RESET}"
echo -e "  Aplikacija:  admin / admin123"
echo -e "  Orthanc:     admin / orthanc123"
echo -e "  n8n:         admin / n8n_admin_2024"
echo ""
echo -e "${BOLD}Uloge:${RESET}"
echo -e "  ${PLAVA}admin${RESET}          — sve"
echo -e "  ${PLAVA}administracija${RESET} — zakazivanje, kartoni"
echo -e "  ${PLAVA}doktor${RESET}         — termini, posete, AI analiza"
echo ""
echo -e "${BOLD}AI model:${RESET}"
echo -e "  qwen2.5:7b (lokalni, srpski, ~4.5GB RAM)"
echo -e "  Analiza se pokreće automatski posle svake posete"
echo ""
echo -e "${BOLD}Korisne komande:${RESET}"
echo -e "  cd $KLINIKA_DIR"
echo -e "  docker compose logs -f klinika   # logovi aplikacije"
echo -e "  docker compose logs -f ollama    # logovi AI modela"
echo -e "  docker compose ps                # status servisa"
echo -e "  bash $SCRIPT_DIR/install.sh      # update (baza ostaje)"
echo ""
