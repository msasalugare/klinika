from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pymysql, pymysql.cursors, os, requests, uuid
from datetime import datetime
from functools import wraps
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from io import BytesIO

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'promeniti_u_produkciji_2024')

# Dodaj enumerate u Jinja2 environment
app.jinja_env.globals['enumerate'] = enumerate

@app.template_filter('from_json')
def from_json_filter(s):
    import json as _j
    if not s: return []
    try:
        return _j.loads(s) if isinstance(s, str) else s
    except:
        return []

@app.template_filter('dijagnoze')
def dijagnoze_filter(s):
    import json as _j
    if not s: return []
    try:
        d = _j.loads(s)
        return d if isinstance(d, list) else [str(d)]
    except:
        return [s.strip()] if s.strip() else []

DB_CONFIG = {
    'host':      os.environ.get('DB_HOST', 'mariadb'),
    'user':      os.environ.get('DB_USER', 'klinika'),
    'password':  os.environ.get('DB_PASS', 'klinika123'),
    'database':  os.environ.get('DB_NAME', 'klinika'),
    'charset':   'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

ORTHANC_URL  = os.environ.get('ORTHANC_URL',  'http://orthanc:8042')
ORTHANC_USER = os.environ.get('ORTHANC_USER', 'admin')
ORTHANC_PASS = os.environ.get('ORTHANC_PASS', 'orthanc123')

UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', '/app/uploads')
N8N_URL     = os.environ.get('N8N_URL', 'http://n8n:5678')
OLLAMA_URL  = os.environ.get('OLLAMA_URL', 'http://ollama:11434')
SMTP_HOST   = os.environ.get('SMTP_HOST', '')
SMTP_PORT   = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER   = os.environ.get('SMTP_USER', '')
SMTP_PASS   = os.environ.get('SMTP_PASS', '')
SMTP_FROM   = os.environ.get('SMTP_FROM', '')
ALLOWED_EXT   = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx', 'xls', 'xlsx', 'dcm'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── DB ────────────────────────────────────────────────────────────────────────

def get_db():
    return pymysql.connect(**DB_CONFIG)

def init_db():
    import time
    for attempt in range(15):
        try:
            with get_db() as db:
                with db.cursor() as cur:
                    cur.execute('''CREATE TABLE IF NOT EXISTS doktori (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        ime VARCHAR(100) NOT NULL,
                        prezime VARCHAR(100) NOT NULL,
                        specijalnost VARCHAR(200),
                        licenca VARCHAR(100),
                        username VARCHAR(100) UNIQUE NOT NULL,
                        password_hash VARCHAR(256) NOT NULL,
                        uloga ENUM('admin','administracija','doktor') DEFAULT 'doktor',
                        created_at DATETIME DEFAULT NOW()
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                    cur.execute("ALTER TABLE doktori ADD COLUMN IF NOT EXISTS uloga ENUM('admin','administracija','doktor') DEFAULT 'doktor'")

                    cur.execute('''CREATE TABLE IF NOT EXISTS klinika (
                        id INT PRIMARY KEY,
                        naziv VARCHAR(200),
                        adresa VARCHAR(300),
                        telefon VARCHAR(50),
                        email VARCHAR(150),
                        pib VARCHAR(50)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS pacijenti (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        ime VARCHAR(100) NOT NULL,
                        prezime VARCHAR(100) NOT NULL,
                        jmbg VARCHAR(13) UNIQUE,
                        datum_rodjenja DATE,
                        pol CHAR(1),
                        adresa VARCHAR(300),
                        telefon VARCHAR(50),
                        email VARCHAR(150),
                        doktor_id INT NOT NULL,
                        krvna_grupa VARCHAR(10),
                        alergije TEXT,
                        hronicne_bolesti TEXT,
                        kontraindikacije TEXT,
                        trudnoca TINYINT DEFAULT 0,
                        napomena_anamneza TEXT,
                        created_at DATETIME DEFAULT NOW(),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                    # Migracija postojecih tabela
                    for kolona, definicija in [
                        ('krvna_grupa', 'VARCHAR(10)'),
                        ('alergije', 'TEXT'),
                        ('hronicne_bolesti', 'TEXT'),
                        ('kontraindikacije', 'TEXT'),
                        ('trudnoca', 'TINYINT DEFAULT 0'),
                        ('napomena_anamneza', 'TEXT'),
                    ]:
                        try:
                            cur.execute(f'ALTER TABLE pacijenti ADD COLUMN IF NOT EXISTS {kolona} {definicija}')
                        except:
                            pass

                    cur.execute('''CREATE TABLE IF NOT EXISTS posete (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        pacijent_id INT NOT NULL,
                        doktor_id INT NOT NULL,
                        datum DATE NOT NULL,
                        anamneza TEXT,
                        dijagnoza TEXT,
                        terapija TEXT,
                        napomena TEXT,
                        created_at DATETIME DEFAULT NOW(),
                        FOREIGN KEY (pacijent_id) REFERENCES pacijenti(id),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS dozvole (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        pacijent_id INT NOT NULL,
                        vlasnik_id INT NOT NULL,
                        doktor_id INT NOT NULL,
                        created_at DATETIME DEFAULT NOW(),
                        UNIQUE KEY uniq_dozvola (pacijent_id, doktor_id),
                        FOREIGN KEY (pacijent_id) REFERENCES pacijenti(id),
                        FOREIGN KEY (vlasnik_id) REFERENCES doktori(id),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS dokumenti (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        pacijent_id INT NOT NULL,
                        doktor_id INT NOT NULL,
                        naziv VARCHAR(300) NOT NULL,
                        originalni_naziv VARCHAR(300),
                        tip VARCHAR(20) NOT NULL,
                        velicina BIGINT,
                        orthanc_id VARCHAR(100),
                        created_at DATETIME DEFAULT NOW(),
                        FOREIGN KEY (pacijent_id) REFERENCES pacijenti(id),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS mkb10 (
                        sifra VARCHAR(10) NOT NULL PRIMARY KEY,
                        naziv VARCHAR(500) NOT NULL,
                        INDEX idx_naziv (naziv(100))
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                    cur.execute("ALTER TABLE doktori MODIFY COLUMN uloga ENUM('admin','administracija','doktor') DEFAULT 'doktor'")
                    cur.execute('''CREATE TABLE IF NOT EXISTS ai_analize (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        poseta_id INT NOT NULL,
                        pacijent_id INT NOT NULL,
                        status ENUM('na_cekanju','gotova','greska') DEFAULT 'na_cekanju',
                        upozorenja JSON,
                        analiza_tekst TEXT,
                        kreirao_id INT,
                        created_at DATETIME DEFAULT NOW(),
                        FOREIGN KEY (poseta_id) REFERENCES posete(id) ON DELETE CASCADE,
                        FOREIGN KEY (pacijent_id) REFERENCES pacijenti(id),
                        FOREIGN KEY (kreirao_id) REFERENCES doktori(id) ON DELETE SET NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                    
                    cur.execute('''CREATE TABLE IF NOT EXISTS tipovi_pregleda (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        naziv VARCHAR(200) NOT NULL,
                        trajanje_min INT DEFAULT 30,
                        cena DECIMAL(10,2),
                        aktivan TINYINT DEFAULT 1,
                        created_at DATETIME DEFAULT NOW()
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS dostupnost (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        doktor_id INT NOT NULL,
                        dan TINYINT NOT NULL COMMENT '0=pon,1=uto,2=sri,3=cet,4=pet,5=sub,6=ned',
                        od TIME NOT NULL,
                        do TIME NOT NULL,
                        UNIQUE KEY uniq_dostupnost (doktor_id, dan),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS nedostupnost (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        doktor_id INT NOT NULL,
                        datum_od DATE NOT NULL,
                        datum_do DATE NOT NULL,
                        razlog VARCHAR(200),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS termini (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        pacijent_id INT NOT NULL,
                        doktor_id INT NOT NULL,
                        tip_pregleda_id INT,
                        datum DATE NOT NULL,
                        vreme TIME NOT NULL,
                        trajanje_min INT DEFAULT 30,
                        cena DECIMAL(10,2),
                        status ENUM('zakazan','potvrden','realizovan','otkazan') DEFAULT 'zakazan',
                        napomena VARCHAR(500),
                        kreirao_id INT,
                        created_at DATETIME DEFAULT NOW(),
                        FOREIGN KEY (pacijent_id) REFERENCES pacijenti(id),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id),
                        FOREIGN KEY (tip_pregleda_id) REFERENCES tipovi_pregleda(id) ON DELETE SET NULL,
                        FOREIGN KEY (kreirao_id) REFERENCES doktori(id) ON DELETE SET NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('''CREATE TABLE IF NOT EXISTS lekovi (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        naziv VARCHAR(300) NOT NULL,
                        inn VARCHAR(300),
                        atc_sifra VARCHAR(20),
                        farmaceutski_oblik VARCHAR(200),
                        jacina VARCHAR(200),
                        INDEX idx_naziv_lek (naziv(100)),
                        INDEX idx_inn (inn(100))
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                    cur.execute('''CREATE TABLE IF NOT EXISTS terapije (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        pacijent_id INT NOT NULL,
                        doktor_id INT NOT NULL,
                        poseta_id INT,
                        naziv_leka VARCHAR(300) NOT NULL,
                        inn VARCHAR(200),
                        atc_sifra VARCHAR(20),
                        farmaceutski_oblik VARCHAR(200),
                        jacina VARCHAR(200),
                        doza VARCHAR(200),
                        nacin_primene VARCHAR(100),
                        ucestalost VARCHAR(200),
                        datum_pocetka DATE NOT NULL,
                        datum_kraja DATE,
                        status ENUM('aktivna','zavrsena','prekinuta') DEFAULT 'aktivna',
                        napomena TEXT,
                        created_at DATETIME DEFAULT NOW(),
                        FOREIGN KEY (pacijent_id) REFERENCES pacijenti(id),
                        FOREIGN KEY (doktor_id) REFERENCES doktori(id),
                        FOREIGN KEY (poseta_id) REFERENCES posete(id) ON DELETE SET NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')

                    cur.execute('SELECT COUNT(*) as c FROM klinika')
                    if cur.fetchone()['c'] == 0:
                        cur.execute('''INSERT INTO klinika VALUES (1,%s,%s,%s,%s,%s)''',
                                    ('Medicinska Klinika','Adresa klinike bb',
                                     '011/000-0000','klinika@example.com',''))
                    cur.execute('SELECT COUNT(*) as c FROM doktori')
                    if cur.fetchone()['c'] == 0:
                        cur.execute('''INSERT INTO doktori
                            (ime,prezime,specijalnost,licenca,username,password_hash,uloga)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                            ('Admin','Admin','Administracija','','admin',
                             generate_password_hash('admin123'),'admin'))
                    else:
                        cur.execute("UPDATE doktori SET uloga='admin' WHERE username='admin' AND uloga='doktor'")
                    cur.execute('SELECT COUNT(*) as c FROM tipovi_pregleda')
                    if cur.fetchone()['c'] == 0:
                        cur.executemany('INSERT INTO tipovi_pregleda (naziv,trajanje_min,cena) VALUES (%s,%s,%s)', [
                            ('Internistički pregled', 30, 3000),
                            ('Kontrolni pregled', 15, 1500),
                            ('Hitni pregled', 20, 4000),
                            ('Specijalstički pregled', 45, 5000),
                            ('Preventivni pregled', 30, 2500),
                        ])
                    # Uvezi MKB-10 ako je tabela prazna
                    cur.execute('SELECT COUNT(*) as c FROM mkb10')
                    if cur.fetchone()['c'] == 0:
                        _uvezi_mkb10(cur)
                    # Uvezi lekove ako je tabela prazna
                    cur.execute('SELECT COUNT(*) as c FROM lekovi')
                    if cur.fetchone()['c'] == 0:
                        db.commit()  # commit pre dugog uvoza
                        _uvezi_lekove(cur)
                    db.commit()
            print('✅ Baza inicijalizovana.')
            return
        except Exception as e:
            print(f'⏳ Čekam MariaDB ({attempt+1}/15): {e}')
            time.sleep(4)
    raise RuntimeError('Ne mogu da se povežem na MariaDB!')

def _uvezi_mkb10(cur):
    import json as _json, os as _os
    mkb_path = _os.path.join(_os.path.dirname(__file__), 'mkb10.json')
    if not _os.path.exists(mkb_path):
        print('⚠️  mkb10.json nije pronađen.')
        return
    with open(mkb_path, 'r', encoding='utf-8') as f:
        podaci = _json.load(f)
    batch = list(podaci.items())
    cur.executemany('INSERT IGNORE INTO mkb10 (sifra, naziv) VALUES (%s, %s)', batch)
    print(f'✅ MKB-10 uvežen: {len(batch)} šifara.')

# ── Helpers ───────────────────────────────────────────────────────────────────

def _uvezi_lekove(cur):
    """Preuzima ALIMS CSV i uvozi lekove u bazu."""
    import csv, io, urllib.request as _req
    url = 'https://www.alims.gov.rs/lekovi/lekovi_humani.csv'
    print(f'⏳ Preuzimam lekove sa ALIMS ({url})...')
    try:
        req = _req.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with _req.urlopen(req, timeout=60) as r:
            raw = r.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f'⚠️  ALIMS nedostupan: {e}')
        return
    # Format CSV: "STATUS";"NAZIV";"INN";"REZIM";"OBLIK_JACINA_PAKOVANJE";"BROJ_DOZVOLE";...;"ATC_SIFRA";...
    reader = csv.reader(io.StringIO(raw), delimiter=';', quotechar='"')
    batch = []
    seen = set()
    for row in reader:
        if len(row) < 5:
            continue
        naziv = row[1].strip() if len(row) > 1 else ''
        inn   = row[2].strip() if len(row) > 2 else ''
        oblik_jacina = row[4].strip() if len(row) > 4 else ''
        atc   = row[12].strip() if len(row) > 12 else ''
        # Parsiraj oblik i jacinu iz kolone 4
        # Format: "farmaceutski oblik; jacina; pakovanje"
        delovi = [x.strip() for x in oblik_jacina.split(';')]
        farmaceutski_oblik = delovi[0] if len(delovi) > 0 else ''
        jacina = delovi[1] if len(delovi) > 1 else ''
        if naziv and naziv not in seen:
            seen.add(naziv)
            batch.append((naziv, inn or None, atc or None,
                         farmaceutski_oblik or None, jacina or None))
        if len(batch) >= 1000:
            cur.executemany(
                'INSERT IGNORE INTO lekovi (naziv, inn, atc_sifra, farmaceutski_oblik, jacina) VALUES (%s,%s,%s,%s,%s)',
                batch)
            batch = []
    if batch:
        cur.executemany(
            'INSERT IGNORE INTO lekovi (naziv, inn, atc_sifra, farmaceutski_oblik, jacina) VALUES (%s,%s,%s,%s,%s)',
            batch)
    print(f'✅ Lekovi uveženi: {len(seen)} unikalnih lekova.')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def parsiraj_dijagnoze(dijagnoza_str):
    import json as _json
    if not dijagnoza_str:
        return []
    try:
        podaci = _json.loads(dijagnoza_str)
        if isinstance(podaci, list):
            return [d for d in podaci if d]
        return [str(podaci)] if podaci else []
    except:
        return [dijagnoza_str.strip()] if dijagnoza_str.strip() else []

def orthanc_upload(filepath):
    try:
        with open(filepath, 'rb') as f:
            r = requests.post(f'{ORTHANC_URL}/instances', data=f.read(),
                              headers={'Content-Type': 'application/dicom'},
                              auth=(ORTHANC_USER, ORTHANC_PASS), timeout=30)
        if r.status_code == 200:
            return r.json().get('ID')
    except Exception as e:
        print(f'Orthanc upload error: {e}')
    return None

def orthanc_dostupan():
    try:
        r = requests.get(f'{ORTHANC_URL}/system',
                         auth=(ORTHANC_USER, ORTHANC_PASS), timeout=3)
        return r.status_code == 200
    except:
        return False

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'doktor_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def trenutni_doktor():
    if 'doktor_id' not in session:
        return None
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM doktori WHERE id=%s', (session['doktor_id'],))
            return cur.fetchone()

def role_required(*uloge):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('doktor_uloga') not in uloge:
                flash('Nemate pristup ovoj stranici.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def ima_pristup(pid, doktor_id):
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT doktor_id FROM pacijenti WHERE id=%s', (pid,))
            p = cur.fetchone()
            if not p: return False
            if p['doktor_id'] == doktor_id: return True
            cur.execute('SELECT 1 FROM dozvole WHERE pacijent_id=%s AND doktor_id=%s',
                        (pid, doktor_id))
            return cur.fetchone() is not None

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'doktor_id' in session else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        with get_db() as db:
            with db.cursor() as cur:
                cur.execute('SELECT * FROM doktori WHERE username=%s', (username,))
                doktor = cur.fetchone()
        if doktor and check_password_hash(doktor['password_hash'], password):
            session['doktor_id']  = doktor['id']
            session['doktor_uloga'] = doktor['uloga']
            prefiks = '' if doktor['uloga'] == 'administracija' else 'Dr. '
            session['doktor_ime'] = f"{prefiks}{doktor['ime']} {doktor['prezime']}"
            return redirect(url_for('dashboard'))
        flash('Pogrešno korisničko ime ili lozinka.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT COUNT(*) as c FROM pacijenti WHERE doktor_id=%s', (doktor['id'],))
            moji_pacijenti = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) as c FROM posete WHERE doktor_id=%s AND datum=CURDATE()", (doktor['id'],))
            posete_danas = cur.fetchone()['c']
            cur.execute('''SELECT po.*, CONCAT(pa.ime,' ',pa.prezime) as pacijent_naziv
                FROM posete po JOIN pacijenti pa ON pa.id=po.pacijent_id
                WHERE po.doktor_id=%s ORDER BY po.created_at DESC LIMIT 5''', (doktor['id'],))
            poslednje_posete = cur.fetchall()
            cur.execute('SELECT COUNT(DISTINCT pacijent_id) as c FROM dozvole WHERE doktor_id=%s', (doktor['id'],))
            podeljeni = cur.fetchone()['c']
    return render_template('dashboard.html', doktor=doktor, moji_pacijenti=moji_pacijenti,
                           posete_danas=posete_danas, poslednje_posete=poslednje_posete, podeljeni=podeljeni)

# ── Pacijenti ─────────────────────────────────────────────────────────────────

@app.route('/pacijenti')
@login_required
def pacijenti():
    doktor = trenutni_doktor()
    q = request.args.get('q', '').strip()
    with get_db() as db:
        with db.cursor() as cur:
            if q:
                like = f'%{q}%'
                cur.execute('''SELECT * FROM pacijenti WHERE doktor_id=%s
                    AND (ime LIKE %s OR prezime LIKE %s OR jmbg LIKE %s)
                    ORDER BY prezime, ime''', (doktor['id'], like, like, like))
            else:
                cur.execute('SELECT * FROM pacijenti WHERE doktor_id=%s ORDER BY prezime, ime', (doktor['id'],))
            moji = cur.fetchall()
            cur.execute('SELECT pacijent_id FROM dozvole WHERE doktor_id=%s', (doktor['id'],))
            pid_list = [r['pacijent_id'] for r in cur.fetchall()]
            podeljeni = []
            if pid_list:
                fmt = ','.join(['%s']*len(pid_list))
                if q:
                    like = f'%{q}%'
                    cur.execute(f'''SELECT p.*, CONCAT(d.ime,' ',d.prezime) as vlasnik_naziv
                        FROM pacijenti p JOIN doktori d ON d.id=p.doktor_id
                        WHERE p.id IN ({fmt}) AND (p.ime LIKE %s OR p.prezime LIKE %s)
                        ORDER BY p.prezime, p.ime''', pid_list+[like,like])
                else:
                    cur.execute(f'''SELECT p.*, CONCAT(d.ime,' ',d.prezime) as vlasnik_naziv
                        FROM pacijenti p JOIN doktori d ON d.id=p.doktor_id
                        WHERE p.id IN ({fmt}) ORDER BY p.prezime, p.ime''', pid_list)
                podeljeni = cur.fetchall()
    return render_template('pacijenti.html', moji=moji, podeljeni=podeljeni, q=q)

@app.route('/pacijenti/novi', methods=['GET', 'POST'])
@login_required
def novi_pacijent():
    doktor = trenutni_doktor()
    if request.method == 'POST':
        with get_db() as db:
            with db.cursor() as cur:
                try:
                    cur.execute('''INSERT INTO pacijenti
                        (ime,prezime,jmbg,datum_rodjenja,pol,adresa,telefon,email,doktor_id,
                         krvna_grupa,alergije,hronicne_bolesti,kontraindikacije,trudnoca,napomena_anamneza)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (request.form['ime'].strip(), request.form['prezime'].strip(),
                         request.form.get('jmbg','').strip() or None,
                         request.form.get('datum_rodjenja','') or None,
                         request.form.get('pol',''),
                         request.form.get('adresa','').strip(),
                         request.form.get('telefon','').strip(),
                         request.form.get('email','').strip(),
                         doktor['id'],
                         request.form.get('krvna_grupa','').strip() or None,
                         request.form.get('alergije','').strip() or None,
                         request.form.get('hronicne_bolesti','').strip() or None,
                         request.form.get('kontraindikacije','').strip() or None,
                         1 if request.form.get('trudnoca') else 0,
                         request.form.get('napomena_anamneza','').strip() or None))
                    db.commit()
                    flash('Pacijent uspešno dodat.', 'success')
                    return redirect(url_for('pacijenti'))
                except pymysql.IntegrityError:
                    flash('Pacijent sa tim JMBG-om već postoji.', 'danger')
    return render_template('novi_pacijent.html', doktor=doktor)

@app.route('/pacijenti/<int:pid>/izmeni', methods=['GET', 'POST'])
@login_required
def izmeni_pacijenta(pid):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (pid,))
            pacijent = cur.fetchone()
    if not pacijent or pacijent['doktor_id'] != doktor['id']:
        flash('Nemate pravo da menjate podatke ovog pacijenta.', 'danger')
        return redirect(url_for('pacijenti'))
    if request.method == 'POST':
        with get_db() as db:
            with db.cursor() as cur:
                try:
                    cur.execute('''UPDATE pacijenti
                        SET ime=%s, prezime=%s, jmbg=%s, datum_rodjenja=%s,
                            pol=%s, adresa=%s, telefon=%s, email=%s,
                            krvna_grupa=%s, alergije=%s, hronicne_bolesti=%s,
                            kontraindikacije=%s, trudnoca=%s, napomena_anamneza=%s
                        WHERE id=%s''',
                        (request.form['ime'].strip(),
                         request.form['prezime'].strip(),
                         request.form.get('jmbg', '').strip() or None,
                         request.form.get('datum_rodjenja', '') or None,
                         request.form.get('pol', ''),
                         request.form.get('adresa', '').strip(),
                         request.form.get('telefon', '').strip(),
                         request.form.get('email', '').strip(),
                         request.form.get('krvna_grupa','').strip() or None,
                         request.form.get('alergije','').strip() or None,
                         request.form.get('hronicne_bolesti','').strip() or None,
                         request.form.get('kontraindikacije','').strip() or None,
                         1 if request.form.get('trudnoca') else 0,
                         request.form.get('napomena_anamneza','').strip() or None,
                         pid))
                    db.commit()
                    flash('Podaci pacijenta uspešno sačuvani.', 'success')
                    return redirect(url_for('pacijent_detalji', pid=pid))
                except pymysql.IntegrityError:
                    flash('Pacijent sa tim JMBG-om vec postoji.', 'danger')
    return render_template('izmeni_pacijenta.html', pacijent=pacijent, doktor=doktor)

@app.route('/pacijenti/<int:pid>')
@login_required
def pacijent_detalji(pid):
    doktor = trenutni_doktor()
    if not ima_pristup(pid, doktor['id']):
        flash('Nemate pristup ovom pacijentu.', 'danger')
        return redirect(url_for('pacijenti'))
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (pid,))
            pacijent = cur.fetchone()
            cur.execute('''SELECT po.*, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                FROM posete po JOIN doktori d ON d.id=po.doktor_id
                WHERE po.pacijent_id=%s ORDER BY po.datum DESC, po.created_at DESC''', (pid,))
            posete = cur.fetchall()
            cur.execute('SELECT * FROM doktori WHERE id=%s', (pacijent['doktor_id'],))
            vlasnik = cur.fetchone()
            je_vlasnik = pacijent['doktor_id'] == doktor['id']
            dozvole, svi_doktori = [], []
            if je_vlasnik:
                cur.execute('''SELECT doz.*, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                    FROM dozvole doz JOIN doktori d ON d.id=doz.doktor_id
                    WHERE doz.pacijent_id=%s''', (pid,))
                dozvole = cur.fetchall()
                dodeljeni = {d['doktor_id'] for d in dozvole}
                cur.execute('SELECT * FROM doktori WHERE id!=%s ORDER BY prezime,ime', (doktor['id'],))
                svi_doktori = [d for d in cur.fetchall() if d['id'] not in dodeljeni]
            cur.execute('''SELECT dok.*, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                FROM dokumenti dok JOIN doktori d ON d.id=dok.doktor_id
                WHERE dok.pacijent_id=%s ORDER BY dok.created_at DESC''', (pid,))
            dokumenti = cur.fetchall()
            cur.execute('''SELECT t.*, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                FROM terapije t JOIN doktori d ON d.id=t.doktor_id
                WHERE t.pacijent_id=%s AND t.status='aktivna'
                ORDER BY t.datum_pocetka DESC''', (pid,))
            aktivne_terapije = cur.fetchall()
            # AI analize za posete
            poseta_ids = [p['id'] for p in posete]
            ai_status = {}
            if poseta_ids:
                fmt = ','.join(['%s']*len(poseta_ids))
                cur.execute(f'SELECT poseta_id, status, upozorenja FROM ai_analize WHERE poseta_id IN ({fmt}) ORDER BY created_at DESC',
                            poseta_ids)
                for r in cur.fetchall():
                    if r['poseta_id'] not in ai_status:
                        ai_status[r['poseta_id']] = r
            # Provjeri da li ima lekova iz nove posete za potvrdu
            nova_poseta_id = request.args.get('nova_poseta_id', type=int)
            lekovi_za_potvrdu = []
            if nova_poseta_id:
                import json as _json
                cur.execute('SELECT * FROM posete WHERE id=%s AND doktor_id=%s',
                            (nova_poseta_id, doktor['id']))
                nova_pos = cur.fetchone()
                if nova_pos and nova_pos['terapija']:
                    try:
                        lekovi_za_potvrdu = _json.loads(nova_pos['terapija'])
                    except:
                        lekovi_za_potvrdu = []
    return render_template('pacijent_detalji.html',
                           pacijent=pacijent, posete=posete, vlasnik=vlasnik,
                           dozvole=dozvole, je_vlasnik=je_vlasnik, doktor=doktor,
                           svi_doktori=svi_doktori, dokumenti=dokumenti,
                           aktivne_terapije=aktivne_terapije,
                           nova_poseta_id=nova_poseta_id,
                           lekovi_za_potvrdu=lekovi_za_potvrdu,
                           ai_status=ai_status,
                           orthanc_ok=orthanc_dostupan(), orthanc_url=ORTHANC_URL)

# ── Posete ────────────────────────────────────────────────────────────────────

@app.route('/pacijenti/<int:pid>/nova-poseta', methods=['GET', 'POST'])
@login_required
def nova_poseta(pid):
    doktor = trenutni_doktor()
    if not ima_pristup(pid, doktor['id']):
        flash('Nemate pristup ovom pacijentu.', 'danger')
        return redirect(url_for('pacijenti'))
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (pid,))
            pacijent = cur.fetchone()
            if request.method == 'POST':
                # Skupi do 10 dijagnoza, filtriraj prazne
                import json as _json
                dijagnoze = []
                for i in range(1, 11):
                    d = request.form.get(f'dijagnoza_{i}', '').strip()
                    if d:
                        dijagnoze.append(d)
                dijagnoza_json = _json.dumps(dijagnoze, ensure_ascii=False) if dijagnoze else '[]'
                # Skupi lekove iz posete (do 5)
                lekovi_posete = []
                for i in range(1, 6):
                    naziv = request.form.get(f'lek_naziv_{i}', '').strip()
                    if naziv:
                        lekovi_posete.append({
                            'naziv': naziv,
                            'inn': request.form.get(f'lek_inn_{i}', '').strip(),
                            'atc': request.form.get(f'lek_atc_{i}', '').strip(),
                            'oblik': request.form.get(f'lek_oblik_{i}', '').strip(),
                            'jacina': request.form.get(f'lek_jacina_{i}', '').strip(),
                            'doza': request.form.get(f'lek_doza_{i}', '').strip(),
                        })
                # Obogati lekove sa ATC/INN iz ALIMS baze
                for lek in lekovi_posete:
                    if lek.get('naziv') and not lek.get('atc'):
                        cur.execute(
                            'SELECT inn, atc_sifra, farmaceutski_oblik, jacina FROM lekovi WHERE naziv=%s LIMIT 1',
                            (lek['naziv'],))
                        alims = cur.fetchone()
                        if alims:
                            if not lek.get('inn'): lek['inn'] = alims['inn'] or ''
                            if not lek.get('atc'): lek['atc'] = alims['atc_sifra'] or ''
                            if not lek.get('oblik'): lek['oblik'] = alims['farmaceutski_oblik'] or ''
                            if not lek.get('jacina'): lek['jacina'] = alims['jacina'] or ''
                lekovi_json = _json.dumps(lekovi_posete, ensure_ascii=False)
                cur.execute('''INSERT INTO posete
                    (pacijent_id,doktor_id,datum,anamneza,dijagnoza,terapija,napomena)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                    (pid, doktor['id'], request.form['datum'],
                     request.form.get('anamneza','').strip(),
                     dijagnoza_json,
                     lekovi_json,
                     request.form.get('napomena','').strip()))
                db.commit()
                poseta_id = cur.lastrowid
                flash('Poseta sacuvana.', 'success')
                # Pokreni AI analizu u pozadini
                pokreni_ai_analizu(poseta_id, doktor['id'])
                flash('AI analiza interakcija pokrenuta.', 'info')
                if lekovi_posete:
                    return redirect(url_for('pacijent_detalji', pid=pid, nova_poseta_id=poseta_id))
                return redirect(url_for('pacijent_detalji', pid=pid))
    return render_template('nova_poseta.html', pacijent=pacijent, doktor=doktor,
                           danas=datetime.now().strftime('%Y-%m-%d'))

# ── Dokumenti ─────────────────────────────────────────────────────────────────

@app.route('/pacijenti/<int:pid>/upload', methods=['POST'])
@login_required
def upload_dokument(pid):
    doktor = trenutni_doktor()
    if not ima_pristup(pid, doktor['id']):
        flash('Nemate pristup.', 'danger')
        return redirect(url_for('pacijenti'))
    if 'fajl' not in request.files or not request.files['fajl'].filename:
        flash('Nije odabran fajl.', 'danger')
        return redirect(url_for('pacijent_detalji', pid=pid))
    fajl = request.files['fajl']
    if not allowed_file(fajl.filename):
        flash('Tip fajla nije dozvoljen. Dozvoljeno: PDF, slike, Word, Excel, DICOM', 'danger')
        return redirect(url_for('pacijent_detalji', pid=pid))

    originalni = secure_filename(fajl.filename)
    ext = originalni.rsplit('.', 1)[1].lower()
    jedinstveni = f"{uuid.uuid4().hex}.{ext}"
    putanja = os.path.join(UPLOAD_FOLDER, jedinstveni)
    fajl.save(putanja)
    velicina = os.path.getsize(putanja)
    orthanc_id = None

    if ext == 'dcm':
        orthanc_id = orthanc_upload(putanja)
        if orthanc_id:
            os.remove(putanja)
            jedinstveni = None  # DICOM je u Orthancu

    naziv = request.form.get('naziv','').strip() or originalni
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('''INSERT INTO dokumenti
                (pacijent_id,doktor_id,naziv,originalni_naziv,tip,velicina,orthanc_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (pid, doktor['id'], naziv, jedinstveni or originalni, ext, velicina, orthanc_id))
            db.commit()

    if ext == 'dcm':
        flash('DICOM uploadovan u Orthanc viewer.' if orthanc_id else 'DICOM sačuvan lokalno (Orthanc nedostupan).', 'success' if orthanc_id else 'warning')
    else:
        flash('Dokument uspešno uploadovan.', 'success')
    return redirect(url_for('pacijent_detalji', pid=pid))

@app.route('/dokumenti/<int:did>/preuzmi')
@login_required
def preuzmi_dokument(did):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM dokumenti WHERE id=%s', (did,))
            dok = cur.fetchone()
    if not dok or not ima_pristup(dok['pacijent_id'], doktor['id']):
        flash('Pristup odbijen.', 'danger')
        return redirect(url_for('pacijenti'))
    putanja = os.path.join(UPLOAD_FOLDER, dok['originalni_naziv'])
    if not os.path.exists(putanja):
        flash('Fajl nije pronađen.', 'danger')
        return redirect(url_for('pacijent_detalji', pid=dok['pacijent_id']))
    return send_file(putanja, as_attachment=True, download_name=dok['naziv'])

@app.route('/dokumenti/<int:did>/obrisi', methods=['POST'])
@login_required
def obrisi_dokument(did):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM dokumenti WHERE id=%s', (did,))
            dok = cur.fetchone()
    if not dok or not ima_pristup(dok['pacijent_id'], doktor['id']):
        flash('Pristup odbijen.', 'danger')
        return redirect(url_for('pacijenti'))
    pid = dok['pacijent_id']
    if dok['orthanc_id']:
        try:
            requests.delete(f"{ORTHANC_URL}/instances/{dok['orthanc_id']}",
                            auth=(ORTHANC_USER, ORTHANC_PASS), timeout=5)
        except: pass
    elif dok['originalni_naziv']:
        p = os.path.join(UPLOAD_FOLDER, dok['originalni_naziv'])
        if os.path.exists(p): os.remove(p)
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('DELETE FROM dokumenti WHERE id=%s', (did,))
            db.commit()
    flash('Dokument obrisan.', 'success')
    return redirect(url_for('pacijent_detalji', pid=pid))

# ── Dozvole ───────────────────────────────────────────────────────────────────

@app.route('/pacijenti/<int:pid>/dozvole/dodaj', methods=['POST'])
@login_required
def dodaj_dozvolu(pid):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (pid,))
            p = cur.fetchone()
            if not p or p['doktor_id'] != doktor['id']:
                flash('Nemate pravo.', 'danger')
                return redirect(url_for('pacijenti'))
            drugi = request.form.get('doktor_id')
            if drugi:
                cur.execute('INSERT IGNORE INTO dozvole (pacijent_id,vlasnik_id,doktor_id) VALUES (%s,%s,%s)',
                            (pid, doktor['id'], int(drugi)))
                db.commit()
                flash('Dozvola dodeljena.', 'success')
    return redirect(url_for('pacijent_detalji', pid=pid))

@app.route('/pacijenti/<int:pid>/dozvole/ukloni/<int:did>', methods=['POST'])
@login_required
def ukloni_dozvolu(pid, did):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (pid,))
            p = cur.fetchone()
            if not p or p['doktor_id'] != doktor['id']:
                flash('Nemate pravo.', 'danger')
                return redirect(url_for('pacijenti'))
            cur.execute('DELETE FROM dozvole WHERE id=%s AND pacijent_id=%s', (did, pid))
            db.commit()
            flash('Dozvola uklonjena.', 'success')
    return redirect(url_for('pacijent_detalji', pid=pid))

# ── PDF ───────────────────────────────────────────────────────────────────────

@app.route('/posete/<int:poseta_id>/izvestaj')
@login_required
def izvestaj_pdf(poseta_id):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM posete WHERE id=%s', (poseta_id,))
            poseta = cur.fetchone()
            if not poseta or not ima_pristup(poseta['pacijent_id'], doktor['id']):
                flash('Pristup odbijen.', 'danger')
                return redirect(url_for('pacijenti'))
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (poseta['pacijent_id'],))
            pacijent = cur.fetchone()
            cur.execute('SELECT * FROM doktori WHERE id=%s', (poseta['doktor_id'],))
            dp = cur.fetchone()
            cur.execute('SELECT * FROM klinika WHERE id=1')
            klinika = cur.fetchone()
            cur.execute('''SELECT t.*, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                FROM terapije t JOIN doktori d ON d.id=t.doktor_id
                WHERE t.pacijent_id=%s AND t.status='aktivna'
                ORDER BY t.datum_pocetka DESC''', (poseta['pacijent_id'],))
            aktivne_terapije = cur.fetchall()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm,
                            leftMargin=2.5*cm, rightMargin=2.5*cm)
    story = []
    hs  = ParagraphStyle('h',  fontSize=16, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=4)
    ss  = ParagraphStyle('s',  fontSize=9,  fontName='Helvetica', alignment=TA_CENTER, textColor=colors.grey, spaceAfter=2)
    vs  = ParagraphStyle('v',  fontSize=10, fontName='Helvetica', spaceAfter=6)
    ts  = ParagraphStyle('t',  fontSize=13, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceBefore=10, spaceAfter=10)
    sec = ParagraphStyle('sc', fontSize=10, fontName='Helvetica-Bold', textColor=colors.HexColor('#1a5276'), spaceBefore=12, spaceAfter=4)
    bs  = ParagraphStyle('b',  fontSize=10, fontName='Helvetica', leading=14, spaceAfter=8)
    ps  = ParagraphStyle('p',  fontSize=8,  fontName='Helvetica', textColor=colors.grey)

    story.append(Paragraph(klinika['naziv'] or 'Medicinska Klinika', hs))
    if klinika['adresa']: story.append(Paragraph(klinika['adresa'], ss))
    k = []
    if klinika['telefon']: k.append(f"Tel: {klinika['telefon']}")
    if klinika['email']:   k.append(klinika['email'])
    if k: story.append(Paragraph(' | '.join(k), ss))
    story += [Spacer(1,.3*cm), HRFlowable(width="100%",thickness=2,color=colors.HexColor('#1a5276')),
              Spacer(1,.3*cm), Paragraph('LEKARSKI IZVEŠTAJ', ts),
              HRFlowable(width="100%",thickness=.5,color=colors.lightgrey), Spacer(1,.4*cm)]

    story.append(Paragraph('PODACI O PACIJENTU', sec))
    pol = {'M':'Muški','Z':'Ženski'}.get(pacijent['pol'], pacijent['pol'] or '—')
    dr  = str(pacijent['datum_rodjenja']) if pacijent['datum_rodjenja'] else '—'
    t = Table([['Ime i prezime:', f"{pacijent['ime']} {pacijent['prezime']}", 'Datum rodjenja:', dr],
               ['JMBG:', pacijent['jmbg'] or '—', 'Pol:', pol],
               ['Adresa:', pacijent['adresa'] or '—', 'Telefon:', pacijent['telefon'] or '—']],
              colWidths=[3.5*cm,6*cm,3.5*cm,4*cm])
    t.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Helvetica'),
                            ('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),
                            ('FONTNAME',(2,0),(2,-1),'Helvetica-Bold'),
                            ('FONTSIZE',(0,0),(-1,-1),9),
                            ('TEXTCOLOR',(0,0),(0,-1),colors.HexColor('#555')),
                            ('TEXTCOLOR',(2,0),(2,-1),colors.HexColor('#555')),
                            ('BOTTOMPADDING',(0,0),(-1,-1),5),
                            ('TOPPADDING',(0,0),(-1,-1),3)]))
    story += [t, Spacer(1,.3*cm), Paragraph('PODACI O POSETI', sec),
              Paragraph(f"<b>Datum posete:</b> {poseta['datum']}", vs),
              Paragraph(f"<b>Lekar:</b> Dr. {dp['ime']} {dp['prezime']}"
                        + (f", {dp['specijalnost']}" if dp['specijalnost'] else ''), vs)]
    for label, field in [('Anamneza','anamneza'),('Terapija','terapija'),('Napomena','napomena')]:
        if poseta[field]:
            story += [Paragraph(f'{label}:', sec),
                      Paragraph(poseta[field].replace('\n','<br/>'), bs)]
    # Dijagnoze - poseban prikaz
    dijagnoze_pdf = parsiraj_dijagnoze(poseta['dijagnoza'])
    if dijagnoze_pdf:
        story.append(Paragraph('Dijagnoza:', sec))
        for idx, dg in enumerate(dijagnoze_pdf, 1):
            prefix = f'{idx}. ' if len(dijagnoze_pdf) > 1 else ''
            story.append(Paragraph(f'{prefix}{dg}', bs))
    # Aktivne terapije u PDF-u
    if aktivne_terapije:
        story.append(Paragraph('Aktivne terapije:', sec))
        for t in aktivne_terapije:
            delovi = [t['naziv_leka']]
            if t['inn']: delovi.append(f"({t['inn']})")
            if t['doza']: delovi.append(f"— {t['doza']}")
            if t['ucestalost']: delovi.append(t['ucestalost'])
            if t['nacin_primene']: delovi.append(f"({t['nacin_primene']})")
            story.append(Paragraph('• ' + ' '.join(delovi), bs))
    story += [Spacer(1,1.5*cm), HRFlowable(width="100%",thickness=.5,color=colors.lightgrey),
              Spacer(1,.3*cm),
              Paragraph(f"Izveštaj generisao: Dr. {dp['ime']} {dp['prezime']}"
                        + (f" | Licenca: {dp['licenca']}" if dp['licenca'] else '')
                        + f" | {datetime.now().strftime('%d.%m.%Y %H:%M')}", ps)]
    doc.build(story)
    buf.seek(0)
    fn = f"izvestaj_{pacijent['prezime']}_{pacijent['ime']}_{poseta['datum']}.pdf"
    return send_file(buf, mimetype='application/pdf', as_attachment=False, download_name=fn)

# ── Podešavanja ───────────────────────────────────────────────────────────────

@app.route('/podesavanja', methods=['GET', 'POST'])
@login_required
def podesavanja():
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM klinika WHERE id=1')
            klinika = cur.fetchone()
            cur.execute('SELECT * FROM doktori WHERE id!=%s ORDER BY prezime,ime', (doktor['id'],))
            svi_doktori = cur.fetchall()
    if request.method == 'POST':
        akcija = request.form.get('akcija')
        with get_db() as db:
            with db.cursor() as cur:
                if akcija == 'klinika':
                    cur.execute('UPDATE klinika SET naziv=%s,adresa=%s,telefon=%s,email=%s,pib=%s WHERE id=1',
                                (request.form['naziv'].strip(), request.form['adresa'].strip(),
                                 request.form['telefon'].strip(), request.form['email'].strip(),
                                 request.form['pib'].strip()))
                    db.commit(); flash('Podaci klinike sačuvani.', 'success')
                elif akcija == 'profil':
                    cur.execute('UPDATE doktori SET ime=%s,prezime=%s,specijalnost=%s,licenca=%s WHERE id=%s',
                                (request.form['ime'].strip(), request.form['prezime'].strip(),
                                 request.form.get('specijalnost','').strip(),
                                 request.form.get('licenca','').strip(), doktor['id']))
                    db.commit()
                    session['doktor_ime'] = f"Dr. {request.form['ime']} {request.form['prezime']}"
                    flash('Profil sačuvan.', 'success')
                elif akcija == 'lozinka':
                    stara, nova, potvrda = request.form['stara_lozinka'], request.form['nova_lozinka'], request.form['potvrda_lozinka']
                    if not check_password_hash(doktor['password_hash'], stara):
                        flash('Stara lozinka nije tačna.', 'danger')
                    elif nova != potvrda: flash('Lozinke se ne poklapaju.', 'danger')
                    elif len(nova) < 6:   flash('Minimum 6 karaktera.', 'danger')
                    else:
                        cur.execute('UPDATE doktori SET password_hash=%s WHERE id=%s',
                                    (generate_password_hash(nova), doktor['id']))
                        db.commit(); flash('Lozinka promenjena.', 'success')
                elif akcija == 'novi_doktor':
                    try:
                        uloga = request.form.get('d_uloga', 'doktor')
                        cur.execute('''INSERT INTO doktori
                            (ime,prezime,specijalnost,licenca,username,password_hash,uloga)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                            (request.form['d_ime'].strip(), request.form['d_prezime'].strip(),
                             request.form.get('d_specijalnost','').strip(),
                             request.form.get('d_licenca','').strip(),
                             request.form['d_username'].strip(),
                             generate_password_hash(request.form['d_password']),
                             uloga))
                        db.commit(); flash(f'Korisnik ({uloga}) dodat.', 'success')
                    except pymysql.IntegrityError:
                        flash('Korisničko ime već postoji.', 'danger')
        return redirect(url_for('podesavanja'))
    return render_template('podesavanja.html', doktor=doktor, klinika=klinika, svi_doktori=svi_doktori)

@app.route('/doktori/<int:did>/obrisi', methods=['POST'])
@login_required
def obrisi_doktora(did):
    doktor = trenutni_doktor()
    if did == doktor['id']:
        flash('Ne možete obrisati sopstveni nalog.', 'danger')
        return redirect(url_for('podesavanja'))
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('DELETE FROM doktori WHERE id=%s', (did,))
            db.commit()
    flash('Doktor obrisan.', 'success')
    return redirect(url_for('podesavanja'))

# ── MKB-10 Autocomplete ───────────────────────────────────────────────────────


# ── Tipovi pregleda (Admin) ───────────────────────────────────────────────────

@app.route('/admin/tipovi-pregleda', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def tipovi_pregleda():
    with get_db() as db:
        with db.cursor() as cur:
            if request.method == 'POST':
                akcija = request.form.get('akcija')
                if akcija == 'dodaj':
                    cur.execute('INSERT INTO tipovi_pregleda (naziv,trajanje_min,cena) VALUES (%s,%s,%s)',
                                (request.form['naziv'].strip(),
                                 int(request.form.get('trajanje_min', 30)),
                                 float(request.form.get('cena', 0)) or None))
                    db.commit(); flash('Tip pregleda dodat.', 'success')
                elif akcija == 'obrisi':
                    cur.execute('UPDATE tipovi_pregleda SET aktivan=0 WHERE id=%s',
                                (request.form['tid'],))
                    db.commit(); flash('Tip pregleda deaktiviran.', 'success')
                return redirect(url_for('tipovi_pregleda'))
            cur.execute('SELECT * FROM tipovi_pregleda WHERE aktivan=1 ORDER BY naziv')
            tipovi = cur.fetchall()
    return render_template('admin_tipovi_pregleda.html', tipovi=tipovi)

# ── Dostupnost doktora ────────────────────────────────────────────────────────

@app.route('/moja-dostupnost', methods=['GET', 'POST'])
@login_required
@role_required('doktor', 'admin')
def moja_dostupnost():
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            if request.method == 'POST':
                akcija = request.form.get('akcija')
                if akcija == 'raspored':
                    cur.execute('DELETE FROM dostupnost WHERE doktor_id=%s', (doktor['id'],))
                    for dan in range(7):
                        od = request.form.get(f'od_{dan}','').strip()
                        do_v = request.form.get(f'do_{dan}','').strip()
                        if od and do_v:
                            cur.execute(
                                'INSERT INTO dostupnost (doktor_id,dan,od,do) VALUES (%s,%s,%s,%s)',
                                (doktor['id'], dan, od, do_v))
                    db.commit(); flash('Raspored sačuvan.', 'success')
                elif akcija == 'nedostupnost_dodaj':
                    cur.execute(
                        'INSERT INTO nedostupnost (doktor_id,datum_od,datum_do,razlog) VALUES (%s,%s,%s,%s)',
                        (doktor['id'], request.form['datum_od'],
                         request.form['datum_do'],
                         request.form.get('razlog','').strip() or None))
                    db.commit(); flash('Nedostupnost dodana.', 'success')
                elif akcija == 'nedostupnost_obrisi':
                    cur.execute('DELETE FROM nedostupnost WHERE id=%s AND doktor_id=%s',
                                (request.form['nid'], doktor['id']))
                    db.commit(); flash('Nedostupnost uklonjena.', 'success')
                return redirect(url_for('moja_dostupnost'))
            cur.execute('SELECT * FROM dostupnost WHERE doktor_id=%s ORDER BY dan', (doktor['id'],))
            raspored = {r['dan']: r for r in cur.fetchall()}
            cur.execute(
                'SELECT * FROM nedostupnost WHERE doktor_id=%s AND datum_do >= CURDATE() ORDER BY datum_od',
                (doktor['id'],))
            nedostupnosti = cur.fetchall()
    dani_nazivi = ['Ponedeljak','Utorak','Sreda','Četvrtak','Petak','Subota','Nedelja']
    return render_template('moja_dostupnost.html', doktor=doktor, raspored=raspored,
                           nedostupnosti=nedostupnosti, dani_nazivi=dani_nazivi)

# ── Moji termini (Doktor) ─────────────────────────────────────────────────────

@app.route('/moji-termini')
@login_required
@role_required('doktor', 'admin')
def moji_termini():
    doktor = trenutni_doktor()
    datum = request.args.get('datum', datetime.now().strftime('%Y-%m-%d'))
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute(
                'SELECT t.*, CONCAT(p.ime," ",p.prezime) as pacijent_naziv, tp.naziv as tip_naziv'
                ' FROM termini t'
                ' JOIN pacijenti p ON p.id=t.pacijent_id'
                ' LEFT JOIN tipovi_pregleda tp ON tp.id=t.tip_pregleda_id'
                ' WHERE t.doktor_id=%s AND t.datum=%s AND t.status != "otkazan"'
                ' ORDER BY t.vreme',
                (doktor['id'], datum))
            termini = cur.fetchall()
            from datetime import date, timedelta
            d = date.fromisoformat(datum)
            sedmica = [(d - timedelta(days=d.weekday()) + timedelta(days=i)).isoformat()
                       for i in range(7)]
    return render_template('moji_termini.html', doktor=doktor, termini=termini,
                           datum=datum, sedmica=sedmica)

# ── Zakazivanje (Administracija) ──────────────────────────────────────────────

@app.route('/zakazivanje', methods=['GET', 'POST'])
@login_required
@role_required('administracija', 'admin')
def zakazivanje():
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            if request.method == 'POST':
                akcija = request.form.get('akcija')
                if akcija == 'zakazi':
                    cur.execute(
                        'SELECT id FROM termini WHERE doktor_id=%s AND datum=%s AND vreme=%s AND status!="otkazan"',
                        (request.form['doktor_id'], request.form['datum'], request.form['vreme']))
                    if cur.fetchone():
                        flash('Taj termin je već zauzet.', 'danger')
                    else:
                        tip_id = request.form.get('tip_pregleda_id') or None
                        cena = None; trajanje = 30
                        if tip_id:
                            cur.execute('SELECT * FROM tipovi_pregleda WHERE id=%s', (tip_id,))
                            tip = cur.fetchone()
                            if tip: cena = tip['cena']; trajanje = tip['trajanje_min']
                        cur.execute(
                            'INSERT INTO termini'
                            ' (pacijent_id,doktor_id,tip_pregleda_id,datum,vreme,'
                            '  trajanje_min,cena,status,napomena,kreirao_id)'
                            ' VALUES (%s,%s,%s,%s,%s,%s,%s,"zakazan",%s,%s)',
                            (request.form['pacijent_id'], request.form['doktor_id'],
                             tip_id, request.form['datum'], request.form['vreme'],
                             trajanje, cena,
                             request.form.get('napomena','').strip() or None,
                             doktor['id']))
                        db.commit(); flash('Termin uspešno zakazan.', 'success')
                elif akcija == 'otkazan':
                    cur.execute('UPDATE termini SET status="otkazan" WHERE id=%s', (request.form['tid'],))
                    db.commit(); flash('Termin otkazan.', 'success')
                elif akcija in ('potvrden', 'realizovan'):
                    cur.execute('UPDATE termini SET status=%s WHERE id=%s', (akcija, request.form['tid']))
                    db.commit(); flash('Status termina ažuriran.', 'success')
                return redirect(url_for('zakazivanje', datum=request.form.get('datum', datetime.now().strftime('%Y-%m-%d'))))

            datum = request.args.get('datum', datetime.now().strftime('%Y-%m-%d'))
            doktor_filter = request.args.get('doktor_id', type=int)
            cur.execute('SELECT * FROM doktori WHERE uloga="doktor" ORDER BY prezime,ime')
            svi_doktori = cur.fetchall()
            cur.execute('SELECT * FROM tipovi_pregleda WHERE aktivan=1 ORDER BY naziv')
            tipovi = cur.fetchall()
            cur.execute('SELECT * FROM pacijenti ORDER BY prezime,ime')
            svi_pacijenti = cur.fetchall()

            q = ('SELECT t.*, CONCAT(p.ime," ",p.prezime) as pacijent_naziv,'
                 ' CONCAT(d.ime," ",d.prezime) as doktor_naziv, tp.naziv as tip_naziv'
                 ' FROM termini t'
                 ' JOIN pacijenti p ON p.id=t.pacijent_id'
                 ' JOIN doktori d ON d.id=t.doktor_id'
                 ' LEFT JOIN tipovi_pregleda tp ON tp.id=t.tip_pregleda_id'
                 ' WHERE t.datum=%s AND t.status!="otkazan"')
            params = [datum]
            if doktor_filter:
                q += ' AND t.doktor_id=%s'; params.append(doktor_filter)
            q += ' ORDER BY t.doktor_id, t.vreme'
            cur.execute(q, params); termini = cur.fetchall()

            from datetime import date, timedelta, datetime as dt
            d = date.fromisoformat(datum)
            dan_u_nedelji = d.weekday()
            slobodni = {}
            for dr in svi_doktori:
                if doktor_filter and dr['id'] != doktor_filter:
                    continue
                cur.execute('SELECT * FROM dostupnost WHERE doktor_id=%s AND dan=%s',
                            (dr['id'], dan_u_nedelji))
                dos = cur.fetchone()
                if not dos: slobodni[dr['id']] = []; continue
                cur.execute(
                    'SELECT 1 FROM nedostupnost WHERE doktor_id=%s AND datum_od<=%s AND datum_do>=%s',
                    (dr['id'], datum, datum))
                if cur.fetchone(): slobodni[dr['id']] = []; continue
                od = dt.strptime(str(dos['od']), '%H:%M:%S')
                do_v = dt.strptime(str(dos['do']), '%H:%M:%S')
                zauzeti = {str(t['vreme'])[:5] for t in termini if t['doktor_id'] == dr['id']}
                slotovi = []; trenutno = od
                while trenutno < do_v:
                    s = trenutno.strftime('%H:%M')
                    if s not in zauzeti: slotovi.append(s)
                    ukupno = trenutno.hour*60+trenutno.minute+30
                    trenutno = dt(trenutno.year,trenutno.month,trenutno.day,ukupno//60,ukupno%60)
                slobodni[dr['id']] = slotovi

            sedmica = [(d - timedelta(days=d.weekday()) + timedelta(days=i)).isoformat()
                       for i in range(7)]

    return render_template('zakazivanje.html', doktor=doktor, datum=datum,
                           svi_doktori=svi_doktori, tipovi=tipovi,
                           svi_pacijenti=svi_pacijenti, termini=termini,
                           slobodni=slobodni, doktor_filter=doktor_filter,
                           sedmica=sedmica)

# ── Dodaj terapije iz posete ─────────────────────────────────────────────────

@app.route('/posete/<int:poseta_id>/dodaj-terapije', methods=['POST'])
@login_required
def dodaj_terapije_iz_posete(poseta_id):
    import json as _json
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM posete WHERE id=%s', (poseta_id,))
            poseta = cur.fetchone()
            if not poseta or not ima_pristup(poseta['pacijent_id'], doktor['id']):
                flash('Pristup odbijen.', 'danger')
                return redirect(url_for('pacijenti'))
            pid = poseta['pacijent_id']
            # Dodaj samo potvrđene lekove
            try:
                lekovi = _json.loads(poseta['terapija'] or '[]')
            except:
                lekovi = []
            dodato = 0
            for i, lek in enumerate(lekovi):
                kljuc = f'lek_{i}'
                if request.form.get(kljuc) == 'da':
                    cur.execute('''INSERT INTO terapije
                        (pacijent_id, doktor_id, poseta_id, naziv_leka, inn,
                         atc_sifra, farmaceutski_oblik, jacina, doza, datum_pocetka, status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'aktivna')''',
                        (pid, doktor['id'], poseta_id,
                         lek.get('naziv',''), lek.get('inn','') or None,
                         lek.get('atc','') or None, lek.get('oblik','') or None,
                         lek.get('jacina','') or None, lek.get('doza','') or None,
                         poseta['datum']))
                    dodato += 1
            db.commit()
    if dodato:
        flash(f'{dodato} lek{"a" if dodato > 1 else ""} dodat{"a" if dodato > 1 else ""} u aktivne terapije.', 'success')
    return redirect(url_for('pacijent_detalji', pid=pid))


# ── AI Analiza ────────────────────────────────────────────────────────────────

def pokreni_ai_analizu(poseta_id, doktor_id):
    import threading
    def _analiziraj():
        try:
            import json as _json
            with get_db() as db:
                with db.cursor() as cur:
                    cur.execute("""SELECT po.*, p.ime, p.prezime, p.datum_rodjenja, p.pol,
                        CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                        FROM posete po
                        JOIN pacijenti p ON p.id=po.pacijent_id
                        JOIN doktori d ON d.id=po.doktor_id
                        WHERE po.id=%s""", (poseta_id,))
                    poseta = cur.fetchone()
                    if not poseta:
                        return
                    pid = poseta["pacijent_id"]
                    cur.execute("""SELECT t.naziv_leka, t.inn, t.atc_sifra, t.doza,
                        t.ucestalost, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                        FROM terapije t JOIN doktori d ON d.id=t.doktor_id
                        WHERE t.pacijent_id=%s AND t.status='aktivna' """, (pid,))
                    terapije = cur.fetchall()
                    try:
                        lek_posete = _json.loads(poseta["terapija"] or "[]")
                    except:
                        lek_posete = []
                    try:
                        dijagnoze = _json.loads(poseta["dijagnoza"] or "[]")
                    except:
                        dijagnoze = [poseta["dijagnoza"]] if poseta["dijagnoza"] else []
                    # Medicinska istorija pacijenta
                    cur.execute("SELECT * FROM pacijenti WHERE id=%s", (pid,))
                    pacijent_info = cur.fetchone()
                    cur.execute("SELECT id FROM ai_analize WHERE poseta_id=%s", (poseta_id,))
                    existing = cur.fetchone()
                    if existing:
                        cur.execute("UPDATE ai_analize SET status='na_cekanju' WHERE poseta_id=%s", (poseta_id,))
                    else:
                        cur.execute("""INSERT INTO ai_analize
                            (poseta_id, pacijent_id, status, kreirao_id)
                            VALUES (%s,%s,'na_cekanju',%s)""",
                            (poseta_id, pid, doktor_id))
                    db.commit()

            pol_txt = "muskog" if poseta["pol"] == "M" else "zenskog"
            dob = ""
            if poseta["datum_rodjenja"]:
                from datetime import date
                dr = poseta["datum_rodjenja"]
                if hasattr(dr, "year"):
                    dob = f"{date.today().year - dr.year} godina"

            # Formatiraj lekove sa ATC + INN iz ALIMS baze
            def fmt_lek_posete(l):
                parts = [f"- {l.get('naziv','')}"]
                if l.get('inn'): parts.append(f"(INN: {l['inn']})")
                if l.get('atc'): parts.append(f"[ATC: {l['atc']}]")
                if l.get('doza'): parts.append(f"— {l['doza']}")
                return " ".join(parts)

            def fmt_terapija(t):
                parts = [f"- {t['naziv_leka']}"]
                if t['inn']: parts.append(f"(INN: {t['inn']})")
                if t['atc_sifra']: parts.append(f"[ATC: {t['atc_sifra']}]")
                if t['doza']: parts.append(f"— {t['doza']}")
                if t['ucestalost']: parts.append(t['ucestalost'])
                parts.append(f"— Dr. {t['doktor_naziv']}")
                return " ".join(parts)

            dijagnoze_txt = chr(10).join(f"- {d}" for d in dijagnoze) if dijagnoze else "- Nije navedena dijagnoza"
            lekovi_txt = chr(10).join(fmt_lek_posete(l) for l in lek_posete) if lek_posete else "- Nisu propisani lekovi na ovoj poseti"
            terapije_txt = chr(10).join(fmt_terapija(t) for t in terapije) if terapije else "- Nema aktivnih terapija"


            # Medicinska istorija pacijenta za AI
            med_istorija = []
            if pacijent_info:
                if pacijent_info.get('krvna_grupa'): med_istorija.append(f"Krvna grupa: {pacijent_info['krvna_grupa']}")
                if pacijent_info.get('trudnoca'): med_istorija.append("TRUDNOCA: DA - poseban oprez!")
                if pacijent_info.get('alergije'): med_istorija.append(f"Alergije: {pacijent_info['alergije']}")
                if pacijent_info.get('hronicne_bolesti'): med_istorija.append(f"Hronicne bolesti: {pacijent_info['hronicne_bolesti']}")
                if pacijent_info.get('kontraindikacije'): med_istorija.append(f"Kontraindikacije: {pacijent_info['kontraindikacije']}")
                if pacijent_info.get('napomena_anamneza'): med_istorija.append(f"Napomena: {pacijent_info['napomena_anamneza']}")
            med_istorija_txt = chr(10).join(f"- {x}" for x in med_istorija) if med_istorija else "- Nije unesena"

            prompt = f"""Ti si medicinski ekspert sistem specijalizovan za farmakologiju i klinicku medicinu.
Koristis ATC (Anatomsko-Terapijsko-Hemijska) klasifikaciju i INN (International Nonproprietary Names)
za preciznu identifikaciju lekova i analizu interakcija na nivou aktivnih supstanci.
Odgovaraj ISKLJUCIVO na srpskom jeziku. Budi koncizan, precizan i strukturiran.

PACIJENT: {poseta["ime"]} {poseta["prezime"]}, {pol_txt} pola{", " + dob if dob else ""}
DATUM POSETE: {poseta["datum"]}
LEKAR: {poseta["doktor_naziv"]}

MEDICINSKA ISTORIJA PACIJENTA:
{med_istorija_txt}

DIJAGNOZE (MKB-10):
{dijagnoze_txt}

LEKOVI PROPISANI NA OVOJ POSETI:
{lekovi_txt}

AKTIVNE TERAPIJE PACIJENTA (ranije propisane):
{terapije_txt}

NAPOMENA: INN nazivi i ATC sifre su navedeni radi precizne identifikacije aktivnih supstanci
bez obzira na lokalni naziv brenda. Analiziraj interakcije na nivou aktivnih supstanci.
Obavezno uzmi u obzir medicinsku istoriju pacijenta — alergije, hronicne bolesti, trudnocu i kontraindikacije.

Analiziraj i odgovori u ovom TACNOM formatu:

## UPOZORENJA
[Svako upozorenje pocinje sa "UPOZORENJE:" na posebnoj liniji.
Navedi samo klinicki relevantna upozorenja sa obrazlozenjem.
Ako nema upozorenja, napisi tacno: "Nisu detektovana upozorenja."]

## INTERAKCIJE IZMEDJU LEKOVA
[Za svaki par lekova koji imaju interakciju: navedi ATC sifre, mehanizam interakcije,
klinicki znacaj (blaga/umerena/ozbiljna) i preporuku. Ako nema interakcija, napisi "Nisu detektovane klinicki znacajne interakcije."]

## USKLADENOST SA DIJAGNOZAMA
[Za svaki propisani lek proceni: da li je indikovan za navedene dijagnoze?
Navedi ATC grupu i obrazlozenje.]

## PREPORUKE
[Konkretne, akcione preporuke za lekara — maksimalno 4 stavke.
Fokus na bezbednost pacijenta.]

## NAPOMENA
Ovo je automatizovana savetodavna analiza bazirana na ATC/INN klasifikaciji.
Ne zamenjuje klinicki pregled. Lekar donosi konacnu medicinsku odluku."""

            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "qwen2.5:7b",
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1200}
                },
                timeout=300
            )

            if resp.status_code == 200:
                analiza_tekst = resp.json().get("response", "")
                upozorenja = []
                for line in analiza_tekst.split(chr(10)):
                    if line.strip().startswith("UPOZORENJE:"):
                        upozorenja.append(line.strip().replace("UPOZORENJE:", "").strip())
                import json as _json
                with get_db() as db:
                    with db.cursor() as cur:
                        cur.execute("""UPDATE ai_analize
                            SET status='gotova', analiza_tekst=%s, upozorenja=%s
                            WHERE poseta_id=%s""",
                            (analiza_tekst, _json.dumps(upozorenja, ensure_ascii=False), poseta_id))
                        db.commit()
            else:
                with get_db() as db:
                    with db.cursor() as cur:
                        cur.execute("UPDATE ai_analize SET status='greska' WHERE poseta_id=%s", (poseta_id,))
                        db.commit()
        except Exception as e:
            print(f"AI greska: {e}")
            try:
                with get_db() as db:
                    with db.cursor() as cur:
                        cur.execute("UPDATE ai_analize SET status='greska' WHERE poseta_id=%s", (poseta_id,))
                        db.commit()
            except:
                pass
    import threading
    t = threading.Thread(target=_analiziraj, daemon=True)
    t.start()


@app.route("/posete/<int:poseta_id>/ai-analiza")
@login_required
def ai_analiza(poseta_id):
    doktor = trenutni_doktor()
    import json as _json
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM posete WHERE id=%s", (poseta_id,))
            poseta = cur.fetchone()
            if not poseta or not ima_pristup(poseta["pacijent_id"], doktor["id"]):
                flash("Pristup odbijen.", "danger")
                return redirect(url_for("pacijenti"))
            cur.execute("SELECT * FROM pacijenti WHERE id=%s", (poseta["pacijent_id"],))
            pacijent = cur.fetchone()
            cur.execute("SELECT * FROM ai_analize WHERE poseta_id=%s ORDER BY created_at DESC LIMIT 1", (poseta_id,))
            analiza = cur.fetchone()
            try:
                dijagnoze = _json.loads(poseta["dijagnoza"] or "[]")
            except:
                dijagnoze = [poseta["dijagnoza"]] if poseta["dijagnoza"] else []
            try:
                lek_posete = _json.loads(poseta["terapija"] or "[]")
            except:
                lek_posete = []
            cur.execute("""SELECT t.*, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                FROM terapije t JOIN doktori d ON d.id=t.doktor_id
                WHERE t.pacijent_id=%s AND t.status='aktivna' """, (poseta["pacijent_id"],))
            aktivne_terapije = cur.fetchall()
            cur.execute("SELECT * FROM klinika WHERE id=1")
            klinika = cur.fetchone()
    return render_template("ai_analiza.html", poseta=poseta, pacijent=pacijent,
                           analiza=analiza, dijagnoze=dijagnoze,
                           lek_posete=lek_posete, aktivne_terapije=aktivne_terapije,
                           klinika=klinika, doktor=doktor)


@app.route("/posete/<int:poseta_id>/ai-analiza/pokreni", methods=["POST"])
@login_required
def pokreni_analizu(poseta_id):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM posete WHERE id=%s", (poseta_id,))
            poseta = cur.fetchone()
    if not poseta or not ima_pristup(poseta["pacijent_id"], doktor["id"]):
        flash("Pristup odbijen.", "danger")
        return redirect(url_for("pacijenti"))
    pokreni_ai_analizu(poseta_id, doktor["id"])
    flash("AI analiza pokrenuta. Rezultat za 1-2 minuta.", "info")
    return redirect(url_for("ai_analiza", poseta_id=poseta_id))


@app.route("/posete/<int:poseta_id>/ai-analiza/status")
@login_required
def ai_analiza_status(poseta_id):
    from flask import jsonify
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute("SELECT status, upozorenja FROM ai_analize WHERE poseta_id=%s ORDER BY created_at DESC LIMIT 1", (poseta_id,))
            r = cur.fetchone()
    if not r:
        return jsonify({"status": "nema"})
    return jsonify({"status": r["status"], "upozorenja": r["upozorenja"]})


@app.route("/posete/<int:poseta_id>/ai-analiza/pdf")
@login_required
def ai_analiza_pdf(poseta_id):
    doktor = trenutni_doktor()
    import re as _re
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM posete WHERE id=%s", (poseta_id,))
            poseta = cur.fetchone()
            if not poseta or not ima_pristup(poseta["pacijent_id"], doktor["id"]):
                flash("Pristup odbijen.", "danger")
                return redirect(url_for("pacijenti"))
            cur.execute("SELECT * FROM ai_analize WHERE poseta_id=%s ORDER BY created_at DESC LIMIT 1", (poseta_id,))
            analiza = cur.fetchone()
            if not analiza or analiza["status"] != "gotova":
                flash("Analiza nije dostupna.", "danger")
                return redirect(url_for("ai_analiza", poseta_id=poseta_id))
            cur.execute("SELECT * FROM pacijenti WHERE id=%s", (poseta["pacijent_id"],))
            pacijent = cur.fetchone()
            cur.execute("SELECT * FROM klinika WHERE id=1")
            klinika = cur.fetchone()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm,
                            leftMargin=2.5*cm, rightMargin=2.5*cm)
    story = []
    hs  = ParagraphStyle("h",  fontSize=16, fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=4)
    ss  = ParagraphStyle("s",  fontSize=9,  fontName="Helvetica", alignment=TA_CENTER, textColor=colors.grey, spaceAfter=2)
    ts  = ParagraphStyle("t",  fontSize=13, fontName="Helvetica-Bold", alignment=TA_CENTER, spaceBefore=10, spaceAfter=10)
    sec = ParagraphStyle("sc", fontSize=10, fontName="Helvetica-Bold", textColor=colors.HexColor("#1a5276"), spaceBefore=12, spaceAfter=4)
    bs  = ParagraphStyle("b",  fontSize=10, fontName="Helvetica", leading=14, spaceAfter=6)
    ws  = ParagraphStyle("w",  fontSize=10, fontName="Helvetica", leading=14, spaceAfter=6,
                         textColor=colors.HexColor("#7d3c00"), backColor=colors.HexColor("#fef9e7"))
    nap = ParagraphStyle("n",  fontSize=9,  fontName="Helvetica", textColor=colors.grey, alignment=TA_CENTER, spaceBefore=20)

    story.append(Paragraph(klinika["naziv"] or "Medicinska Klinika", hs))
    if klinika["adresa"]: story.append(Paragraph(klinika["adresa"], ss))
    story += [Spacer(1,.3*cm), HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a5276")),
              Spacer(1,.3*cm), Paragraph("AI ANALIZA INTERAKCIJA I DIJAGNOZA", ts),
              HRFlowable(width="100%", thickness=.5, color=colors.lightgrey), Spacer(1,.4*cm)]
    story.append(Paragraph(f"Pacijent: {pacijent['ime']} {pacijent['prezime']}  |  Datum: {poseta['datum']}  |  Generisano: {datetime.now().strftime('%d.%m.%Y %H:%M')}", ss))
    story.append(Spacer(1, .5*cm))

    tekst = analiza["analiza_tekst"] or ""
    for line in tekst.split(chr(10)):
        line = line.strip()
        if not line:
            story.append(Spacer(1, .15*cm))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], sec))
        elif line.startswith("UPOZORENJE:"):
            story.append(Paragraph("&#9888; " + line.replace("UPOZORENJE:", "").strip(), ws))
        else:
            clean = _re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", line)
            story.append(Paragraph(clean, bs))

    story += [Spacer(1, 1.5*cm),
              HRFlowable(width="100%", thickness=.5, color=colors.lightgrey),
              Spacer(1, .3*cm),
              Paragraph("Ovo je automatizovana savetodavna analiza AI sistema. Ne zamenjuje klinicki pregled. Lekar donosi konacnu medicinsku odluku.", nap)]
    doc.build(story)
    buf.seek(0)
    fn = f"ai_analiza_{pacijent['prezime']}_{poseta['datum']}.pdf"
    return send_file(buf, mimetype="application/pdf", as_attachment=False, download_name=fn)


@app.route("/posete/<int:poseta_id>/ai-analiza/email", methods=["POST"])
@login_required
def ai_analiza_email(poseta_id):
    doktor = trenutni_doktor()
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM posete WHERE id=%s", (poseta_id,))
            poseta = cur.fetchone()
            if not poseta or not ima_pristup(poseta["pacijent_id"], doktor["id"]):
                flash("Pristup odbijen.", "danger")
                return redirect(url_for("pacijenti"))
            cur.execute("SELECT * FROM ai_analize WHERE poseta_id=%s ORDER BY created_at DESC LIMIT 1", (poseta_id,))
            analiza = cur.fetchone()
            cur.execute("SELECT * FROM pacijenti WHERE id=%s", (poseta["pacijent_id"],))
            pacijent = cur.fetchone()
            cur.execute("""SELECT DISTINCT d.email, d.ime, d.prezime
                FROM terapije t JOIN doktori d ON d.id=t.doktor_id
                WHERE t.pacijent_id=%s AND d.email IS NOT NULL AND d.email != ''""",
                (poseta["pacijent_id"],))
            primaoci = cur.fetchall()
    if not SMTP_HOST:
        flash("SMTP nije konfigurisan.", "warning")
        return redirect(url_for("ai_analiza", poseta_id=poseta_id))
    if not primaoci:
        flash("Nema doktora sa email adresom koji su davali terapiju.", "warning")
        return redirect(url_for("ai_analiza", poseta_id=poseta_id))
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        poslato = 0
        for p in primaoci:
            msg = MIMEMultipart()
            msg["From"]    = SMTP_FROM or SMTP_USER
            msg["To"]      = p["email"]
            msg["Subject"] = f"AI Analiza — {pacijent['prezime']}, {pacijent['ime']} ({poseta['datum']})"
            body = f"""Postovani Dr. {p['ime']} {p['prezime']},

AI sistem je generisao analizu za pacijenta {pacijent['ime']} {pacijent['prezime']}.

{analiza['analiza_tekst'] if analiza else 'Analiza nije dostupna.'}

---
Ovo je automatizovana savetodavna analiza. Lekar donosi konacnu medicinsku odluku.
"""
            msg.attach(MIMEText(body, "plain", "utf-8"))
            server.send_message(msg)
            poslato += 1
        server.quit()
        flash(f"Email poslat na {poslato} adresa.", "success")
    except Exception as e:
        flash(f"Greska pri slanju emaila: {e}", "danger")
    return redirect(url_for("ai_analiza", poseta_id=poseta_id))

# ── Lekovi autocomplete ───────────────────────────────────────────────────────

@app.route('/lekovi/pretraga')
@login_required
def lekovi_pretraga():
    from flask import jsonify
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    with get_db() as db:
        with db.cursor() as cur:
            like = f'%{q}%'
            cur.execute('''
                SELECT naziv, inn, atc_sifra, farmaceutski_oblik, jacina
                FROM lekovi
                WHERE naziv LIKE %s OR inn LIKE %s
                ORDER BY CASE WHEN naziv LIKE %s THEN 0 ELSE 1 END, naziv
                LIMIT 20
            ''', (f'{q}%', like, f'{q}%'))
            r = cur.fetchall()
    return jsonify(r)

# ── Terapije ──────────────────────────────────────────────────────────────────

@app.route('/pacijenti/<int:pid>/terapije')
@login_required
def terapije_pacijenta(pid):
    doktor = trenutni_doktor()
    if not ima_pristup(pid, doktor['id']):
        flash('Nemate pristup.', 'danger')
        return redirect(url_for('pacijenti'))
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (pid,))
            pacijent = cur.fetchone()
            cur.execute('''
                SELECT t.*, CONCAT(d.ime,' ',d.prezime) as doktor_naziv
                FROM terapije t JOIN doktori d ON d.id=t.doktor_id
                WHERE t.pacijent_id=%s
                ORDER BY t.status='aktivna' DESC, t.datum_pocetka DESC
            ''', (pid,))
            terapije = cur.fetchall()
            cur.execute('SELECT * FROM doktori WHERE id=%s', (pacijent['doktor_id'],))
            vlasnik = cur.fetchone()
            je_vlasnik = pacijent['doktor_id'] == doktor['id']
    return render_template('terapije.html', pacijent=pacijent, terapije=terapije,
                           vlasnik=vlasnik, je_vlasnik=je_vlasnik, doktor=doktor)

@app.route('/pacijenti/<int:pid>/terapije/nova', methods=['GET', 'POST'])
@login_required
def nova_terapija(pid):
    doktor = trenutni_doktor()
    if not ima_pristup(pid, doktor['id']):
        flash('Nemate pristup.', 'danger')
        return redirect(url_for('pacijenti'))
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM pacijenti WHERE id=%s', (pid,))
            pacijent = cur.fetchone()
            if request.method == 'POST':
                cur.execute('''
                    INSERT INTO terapije
                    (pacijent_id, doktor_id, naziv_leka, inn, atc_sifra,
                     farmaceutski_oblik, jacina, doza, nacin_primene,
                     ucestalost, datum_pocetka, datum_kraja, napomena)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ''', (
                    pid, doktor['id'],
                    request.form['naziv_leka'].strip(),
                    request.form.get('inn', '').strip() or None,
                    request.form.get('atc_sifra', '').strip() or None,
                    request.form.get('farmaceutski_oblik', '').strip() or None,
                    request.form.get('jacina', '').strip() or None,
                    request.form.get('doza', '').strip(),
                    request.form.get('nacin_primene', '').strip() or None,
                    request.form.get('ucestalost', '').strip() or None,
                    request.form['datum_pocetka'],
                    request.form.get('datum_kraja', '') or None,
                    request.form.get('napomena', '').strip() or None,
                ))
                db.commit()
                flash('Terapija uspešno propisana.', 'success')
                return redirect(url_for('terapije_pacijenta', pid=pid))
    return render_template('nova_terapija.html', pacijent=pacijent, doktor=doktor,
                           danas=datetime.now().strftime('%Y-%m-%d'))

@app.route('/terapije/<int:tid>/status', methods=['POST'])
@login_required
def promeni_status_terapije(tid):
    doktor = trenutni_doktor()
    novi_status = request.form.get('status')
    if novi_status not in ('aktivna', 'zavrsena', 'prekinuta'):
        flash('Nevažeći status.', 'danger')
        return redirect(url_for('pacijenti'))
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM terapije WHERE id=%s', (tid,))
            t = cur.fetchone()
            if not t or not ima_pristup(t['pacijent_id'], doktor['id']):
                flash('Nemate pristup.', 'danger')
                return redirect(url_for('pacijenti'))
            datum_kraja = datetime.now().strftime('%Y-%m-%d') if novi_status != 'aktivna' else None
            cur.execute('''
                UPDATE terapije SET status=%s, datum_kraja=%s WHERE id=%s
            ''', (novi_status, datum_kraja, tid))
            db.commit()
            poruke = {'zavrsena': 'Terapija označena kao završena.',
                      'prekinuta': 'Terapija prekinuta.',
                      'aktivna':   'Terapija ponovo aktivirana.'}
            flash(poruke.get(novi_status, 'Status ažuriran.'), 'success')
    return redirect(url_for('terapije_pacijenta', pid=t['pacijent_id']))

@app.route('/terapije/<int:tid>/obrisi', methods=['POST'])
@login_required
def obrisi_terapiju(tid):
    doktor = trenutni_doktor()
    with get_db() as db:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM terapije WHERE id=%s', (tid,))
            t = cur.fetchone()
            if not t or t['doktor_id'] != doktor['id']:
                flash('Samo doktor koji je propisao terapiju može je obrisati.', 'danger')
                return redirect(url_for('pacijenti'))
            pid = t['pacijent_id']
            cur.execute('DELETE FROM terapije WHERE id=%s', (tid,))
            db.commit()
            flash('Terapija obrisana.', 'success')
    return redirect(url_for('terapije_pacijenta', pid=pid))

@app.route('/mkb10/pretraga')
@login_required
def mkb10_pretraga():
    from flask import jsonify
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    with get_db() as db:
        with db.cursor() as cur:
            # Traži po šifri ILI nazivu
            like = f'%{q}%'
            cur.execute('''
                SELECT sifra, naziv FROM mkb10
                WHERE sifra LIKE %s OR naziv LIKE %s
                ORDER BY
                    CASE WHEN sifra LIKE %s THEN 0 ELSE 1 END,
                    sifra
                LIMIT 15
            ''', (f'{q}%', like, f'{q}%'))
            rezultati = cur.fetchall()
    return jsonify([{'sifra': r['sifra'], 'naziv': r['naziv']} for r in rezultati])


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
