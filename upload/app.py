"""
Culto Demoníaco Uploader - Dashboard
Dashboard web para gerenciar uploads para o site culto-demoniaco.online
Processa automaticamente a fila de obras baixadas pelo bot de download
"""

import os
import json
import threading
import queue
import time
import traceback
from pathlib import Path
import sys
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from shared.queue_store import QueueStore, mirror_legacy_queue_json

from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

# ============================================
# Função para carregar .env manualmente
# ============================================

def load_env_file():
    """Carrega variáveis do arquivo .env manualmente"""
    env_vars = {}
    
    possible_paths = [
        Path(__file__).parent / '.env',
        Path('.env'),
        Path(os.getcwd()) / '.env',
    ]
    
    env_path = None
    for p in possible_paths:
        if p.exists():
            env_path = p
            break
    
    if env_path and env_path.exists():
        print(f"Carregando .env de: {env_path.absolute()}")
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    env_vars[key] = value
                    os.environ[key] = value
    else:
        print(f"AVISO: Arquivo .env não encontrado!")
    
    return env_vars

# Carregar .env no início
ENV_VARS = load_env_file()

# ============================================
# Configurações
# ============================================

app = Flask(__name__)
app.config['SECRET_KEY'] = 'culto-upload-secret-key'
# Usar async_mode=None para auto-detectar ou evitar travamento
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=None)


@app.route('/')
def index():
    # Página do dashboard
    return render_template('index.html')

# Credenciais do site
SITE_URL = ENV_VARS.get('CULTO_URL', 'https://culto-demoniaco.online')
SITE_EMAIL = ENV_VARS.get('CULTO_EMAIL', '')
SITE_SENHA = ENV_VARS.get('CULTO_SENHA', '')

# Caminhos
CATALOGO_PATH = Path(ENV_VARS.get('CATALOGO_PATH', 'catalogo.json'))
DOWNLOADS_DIR = Path(ENV_VARS.get('DOWNLOADS_DIR', '../download/downloads'))
FILA_UPLOAD_FILE = Path(__file__).parent.parent / 'fila_upload.json'
QUEUE_STORE = QueueStore()
CAPITULOS_QUEBRADOS_FILE = Path(__file__).parent.parent / 'capitulos_quebrados.csv'

# Screenshot ao vivo
SCREENSHOT_DIR = Path(__file__).parent / 'static'
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_PAGE = None  # Referência global para a página do navegador

# ============================================
# Funções de Relatório de Capítulos Quebrados
# ============================================

def inicializar_relatorio_quebrados():
    """Inicializa o arquivo CSV de capítulos quebrados se não existir"""
    if not CAPITULOS_QUEBRADOS_FILE.exists():
        with open(CAPITULOS_QUEBRADOS_FILE, 'w', encoding='utf-8') as f:
            f.write('obra,capitulo,motivo,data_hora\n')

def registrar_capitulo_quebrado(obra_nome, capitulo, motivo):
    """Registra um capítulo quebrado no arquivo CSV"""
    inicializar_relatorio_quebrados()
    data_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Escapar vírgulas nos campos
    obra_nome = obra_nome.replace(',', ';')
    motivo = motivo.replace(',', ';')
    
    with open(CAPITULOS_QUEBRADOS_FILE, 'a', encoding='utf-8') as f:
        f.write(f'{obra_nome},{capitulo},{motivo},{data_hora}\n')
    
    print(f"[QUEBRADO] {obra_nome} - Capítulo {capitulo}: {motivo}")

def carregar_relatorio_quebrados():
    """Carrega o relatório de capítulos quebrados"""
    if not CAPITULOS_QUEBRADOS_FILE.exists():
        return []
    
    quebrados = []
    with open(CAPITULOS_QUEBRADOS_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()[1:]  # Pular cabeçalho
        for line in lines:
            parts = line.strip().split(',')
            if len(parts) >= 4:
                quebrados.append({
                    'obra': parts[0],
                    'capitulo': parts[1],
                    'motivo': parts[2],
                    'data_hora': parts[3]
                })
    return quebrados

# Debug
if SITE_EMAIL:
    print(f"\n*** Credenciais carregadas: {SITE_EMAIL} ***\n")
else:
    print("\n*** AVISO: CULTO_EMAIL não encontrado no .env! ***\n")

# ============================================
# Estado Global
# ============================================

status_lock = threading.Lock()
upload_queue = queue.Queue()

bot_status = {
    'running': False,
    'watching': False,
    'current_obra': None,
    'obras_total': 0,
    'obras_processadas': 0,
    'capitulos_enviados': 0,
    'fila_pendente': 0,
    'state': 'idle',
    'logs': []
}

MAX_LOGS = 300

# ============================================
# Funções de Log e Status
# ============================================

def log_message(message, level='info', job_id=None):
    """Envia mensagem de log para o frontend"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = {
        'timestamp': timestamp,
        'message': message,
        'level': level,
        'job_id': job_id
    }
    
    with status_lock:
        bot_status['logs'].append(log_entry)
        if len(bot_status['logs']) > MAX_LOGS:
            bot_status['logs'] = bot_status['logs'][-MAX_LOGS:]
    

    # Persistência sistêmica de eventos por job (SQLite)
    try:
        jid = job_id
        if jid is None:
            cur = QUEUE_STORE.get_runtime('upload_current', None)
            if isinstance(cur, dict):
                jid = cur.get('job_id')
        if jid:
            QUEUE_STORE.log_event(jid, level, message)
    except Exception:
        pass

    try:
        socketio.emit('log', log_entry)
    except Exception:
        pass  # Ignorar erro se não houver clientes conectados
    print(f"[{timestamp}] [{level.upper()}] {message}")

def update_status(updates):
    """Atualiza o status do bot"""
    with status_lock:
        bot_status.update(updates)
    try:
        socketio.emit('status', bot_status)
    except Exception:
        pass  # Ignorar erro se não houver clientes conectados

# ============================================
# Funções de Fila
# ============================================

def carregar_fila():
    """Retorna a fila atual a partir do SQLite (status=queued)."""
    jobs = QUEUE_STORE.list_jobs(status='queued', limit=2000)
    fila = []
    for j in jobs:
        item = dict(j.payload)
        item.setdefault('obra_nome', j.obra_nome)
        item.setdefault('pasta', j.pasta)
        item.setdefault('job_id', j.id)
        fila.append(item)
    # manter espelho legacy
    try:
        mirror_legacy_queue_json(QUEUE_STORE, FILA_UPLOAD_FILE)
    except Exception:
        pass
    return fila
def salvar_fila(fila):
    """Compatibilidade: a fonte de verdade é o SQLite. Mantém espelho legacy."""
    try:
        # espelho legacy apenas
        FILA_UPLOAD_FILE.write_text(json.dumps(fila, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
def remover_da_fila(job_id):
    """Marca um job como concluído no SQLite."""
    try:
        QUEUE_STORE.mark_done(str(job_id))
        try:
            mirror_legacy_queue_json(QUEUE_STORE, FILA_UPLOAD_FILE)
        except Exception:
            pass
        return True
    except Exception as e:
        log_message(f"Erro ao marcar como done: {e}", level='error')
        return False
def carregar_catalogo():
    """Carrega o catálogo de obras"""
    catalogo_path = CATALOGO_PATH
    if not catalogo_path.exists():
        catalogo_path = Path('catalogo.json')
    if not catalogo_path.exists():
        catalogo_path = Path(__file__).parent / 'catalogo.json'
    
    if catalogo_path.exists():
        with open(catalogo_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'obras': []}

def buscar_obra_no_catalogo(obra_nome):
    """Busca informações de uma obra no catálogo"""
    catalogo = carregar_catalogo()
    for obra in catalogo.get('obras', []):
        titulo = obra.get('title', '')
        # Comparar nome sanitizado
        nome_sanitizado = "".join(c for c in titulo if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
        if nome_sanitizado == obra_nome or titulo == obra_nome:
            return obra
    return None

# ============================================
# Bot de Upload
# ============================================

def upload_obra(obra_info):
    """Faz upload de uma única obra"""
    from playwright.sync_api import sync_playwright
    
    obra_nome = obra_info.get('obra_nome')
    pasta = Path(obra_info.get('pasta'))
    
    if not pasta.exists():
        log_message(f"Pasta não encontrada: {pasta}", level='error')
        return False
    
    # Buscar informações no catálogo
    obra_catalogo = buscar_obra_no_catalogo(obra_nome)
    titulo = obra_catalogo.get('title', obra_nome) if obra_catalogo else obra_nome
    sinopse = obra_catalogo.get('sinopse', '') if obra_catalogo else ''
    tags = obra_catalogo.get('tags', []) if obra_catalogo else []
    
    log_message(f"Iniciando upload: {titulo}")
    update_status({'current_obra': titulo, 'state': 'uploading'})
    
    # Recarregar credenciais
    env_vars = load_env_file()
    email = env_vars.get('CULTO_EMAIL', '')
    senha = env_vars.get('CULTO_SENHA', '')
    
    if not email or not senha:
        log_message("Credenciais não configuradas!", level='error')
        return False
    
    browser = None
    capitulos_enviados = 0
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            
            # Salvar referência global para screenshots ao vivo
            global CURRENT_PAGE
            CURRENT_PAGE = page
            
            # Login com retry
            log_message("Fazendo login...")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    page.goto(f"{SITE_URL}/login", wait_until='domcontentloaded', timeout=60000)
                    time.sleep(2)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        log_message(f"Tentativa {attempt+1} falhou, tentando novamente...", level='warning')
                        time.sleep(5)
                    else:
                        raise e
            
            page.fill("#email", email)
            page.fill("#password", senha)
            page.click("button:has-text('Entrar')")
            time.sleep(3)
            
            if '/login' in page.url:
                log_message("Falha no login!", level='error')
                return False
            
            log_message("Login realizado!", level='success')
            
            # Verificar capa local
            capa_local = None
            for ext in ['.jpg', '.png', '.webp', '.jpeg']:
                capa_path = pasta / f'capa{ext}'
                if capa_path.exists():
                    capa_local = capa_path
                    break
            if not capa_local:
                capa_path = pasta / 'capa.jpg'
                if capa_path.exists():
                    capa_local = capa_path
            
            # Ir para o painel admin
            page.goto(f"{SITE_URL}/admin", wait_until='domcontentloaded', timeout=30000)
            time.sleep(2)
            
            # Buscar se a obra já existe
            try:
                search_input = page.locator("input[placeholder='Buscar obras...']")
                if search_input.is_visible(timeout=5000):
                    search_input.fill(titulo)
                    time.sleep(2)
            except:
                pass
            
            obra_existe = False
            try:
                obra_row = page.locator("tr", has_text=titulo).first
                if obra_row.is_visible(timeout=3000):
                    obra_existe = True
                    log_message(f"Obra '{titulo}' já existe. Indo para capítulos...")
                    try:
                        obra_row.locator("button").nth(1).click(timeout=5000)
                    except:
                        pass
                    time.sleep(2)
            except:
                pass
            
            if not obra_existe:
                log_message(f"Criando nova obra: {titulo}")
                page.goto(f"{SITE_URL}/admin/manga/new", wait_until='domcontentloaded', timeout=30000)
                time.sleep(2)
                
                # Upload da capa
                if capa_local and capa_local.exists():
                    log_message(f"Enviando capa: {capa_local.name}")
                    try:
                        file_input = page.locator("input[type='file']").first
                        if file_input.is_visible(timeout=5000):
                            file_input.set_input_files(str(capa_local))
                            time.sleep(2)
                    except Exception as e:
                        log_message(f"Erro ao enviar capa: {e}", level='warning')
                
                # Preencher título e descrição
                page.fill("#title", titulo)
                if sinopse:
                    try:
                        desc_input = page.locator("#description, textarea[name='description']").first
                        if desc_input.is_visible(timeout=3000):
                            desc_input.fill(sinopse)
                    except:
                        pass
                
                # Marcar +18 se tiver tag HENTAI
                is_adult = any(t.upper() == "HENTAI" for t in tags)
                if is_adult:
                    log_message(f"Marcando como +18 (Tag HENTAI)")
                    try:
                        adult_switch = page.locator("button[role='switch']").first
                        if adult_switch.is_visible(timeout=3000):
                            adult_switch.click()
                    except:
                        pass
                
                # Criar obra
                try:
                    page.click("button:has-text('Criar')", timeout=5000)
                    time.sleep(3)
                    log_message(f"Obra '{titulo}' criada!", level='success')
                except Exception as e:
                    log_message(f"Erro ao criar obra: {e}", level='error')
                    return False
            
            # Upload de capítulos
            cap_folders = sorted([d for d in pasta.iterdir() if d.is_dir() and d.name.startswith('cap_')])
            
            for cap_folder in cap_folders:
                # Verificar se foi solicitado parar
                with status_lock:
                    if not bot_status['running']:
                        log_message("Upload interrompido pelo usuário", level='warning')
                        break
                
                cap_name = cap_folder.name.replace("cap_", "").lstrip("0") or "0"
                
                # Verificar se capítulo já existe
                try:
                    if page.locator(f"tr:has-text('#{cap_name}')").is_visible(timeout=1000):
                        continue
                except:
                    pass
                
                images = sorted([str(img) for img in cap_folder.iterdir() 
                               if img.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']])
                
                # Verificar se o capítulo está quebrado (pasta vazia ou sem imagens)
                if not images:
                    log_message(f"Capítulo {cap_name} QUEBRADO (pasta vazia ou sem imagens)", level='warning')
                    registrar_capitulo_quebrado(titulo, cap_name, 'Pasta vazia ou sem imagens válidas')
                    continue
                
                # Verificar se tem poucas imagens (possível quebrado)
                if len(images) < 3:
                    log_message(f"Capítulo {cap_name} com poucas imagens ({len(images)}). Pode estar incompleto.", level='warning')
                    registrar_capitulo_quebrado(titulo, cap_name, f'Apenas {len(images)} imagens - possivelmente incompleto')
                
                log_message(f"Enviando capítulo {cap_name} ({len(images)} imagens)...")
                
                try:
                    page.click("button:has-text('Novo Capítulo')", timeout=5000)
                    time.sleep(1)
                    
                    page.fill("#chapter-number", cap_name)
                    page.fill("#chapter-title", cap_name)
                    
                    # Upload das imagens
                    file_input = page.locator("input[type='file'][multiple]").first
                    if file_input.is_visible(timeout=5000):
                        file_input.set_input_files(images)
                        time.sleep(2)
                    
                    page.click("button:has-text('Criar Capítulo')", timeout=5000)
                    time.sleep(3)
                    
                    capitulos_enviados += 1
                    with status_lock:
                        bot_status['capitulos_enviados'] += 1
                    update_status({'capitulos_enviados': bot_status['capitulos_enviados']})
                    log_message(f"Capítulo {cap_name} enviado!", level='success')
                except Exception as e:
                    log_message(f"Erro no capítulo {cap_name}: {e}", level='error')
                    try:
                        page.click("button:has-text('Cancelar')", timeout=2000)
                    except:
                        pass
            
            log_message(f"Upload de '{titulo}' concluído! {capitulos_enviados} capítulos enviados.", level='success')
            return True
            
    except Exception as e:
        log_message(f"Erro crítico: {traceback.format_exc()}", level='error')
        return False
    finally:
        if browser:
            try:
                browser.close()
            except:
                pass

def fila_watcher(stop_event=None, worker_id=None):
    """Loop do worker: consome a fila transacional (SQLite) e processa uploads.

    Melhorias sistêmicas:
    - recuperação de jobs "processing" órfãos via timeout (heartbeat)
    - backoff por falha via available_at (no QueueStore)
    - heartbeat periódico enquanto um job está em execução
    - graceful shutdown via stop_event
    """
    wid = worker_id or f"upload-worker-{os.getpid()}"
    log_message(f"Worker de upload iniciado (SQLite) - worker_id={wid}")
    update_status({'watching': True})

    last_reclaim = 0

    while True:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            log_message("Worker de upload finalizado (shutdown solicitado).")
            return

        try:
            # 1) limpeza periódica de jobs processing órfãos (a cada ~60s)
            now = time.time()
            if now - last_reclaim > 60:
                try:
                    n = QUEUE_STORE.reclaim_stale_processing(timeout_seconds=600)
                    if n:
                        log_message(f"Re-enfileirados {n} job(s) órfão(s) em processing.", level='warning')
                except Exception as e:
                    log_message(f"Falha ao re-enfileirar órfãos: {e}", level='warning')
                last_reclaim = now

            # 2) runtime flag controlado pela API
            running = bool(QUEUE_STORE.get_runtime('upload_running', False))
            if not running:
                time.sleep(2)
                continue

            job = QUEUE_STORE.claim_next(worker_id=wid)
            if not job:
                time.sleep(2)
                continue

            obra_info = dict(job.payload)
            obra_info.setdefault('obra_nome', job.obra_nome)
            obra_info.setdefault('pasta', job.pasta)
            obra_info.setdefault('job_id', job.id)

            QUEUE_STORE.set_runtime('upload_current', {'job_id': job.id, 'obra_nome': job.obra_nome})
            log_message(f"Processando job: {job.obra_nome} (id={job.id})")

            # 3) heartbeat em background enquanto o upload roda
            hb_stop = threading.Event()

            def _hb_loop():
                while not hb_stop.is_set():
                    try:
                        QUEUE_STORE.heartbeat(job.id, wid)
                    except Exception:
                        pass
                    hb_stop.wait(10)

            hb_thread = threading.Thread(target=_hb_loop, daemon=True)
            hb_thread.start()

            try:
                sucesso = upload_obra(obra_info)
            finally:
                hb_stop.set()
                hb_thread.join(timeout=2)

            if sucesso:
                QUEUE_STORE.mark_done(job.id)
                QUEUE_STORE.set_runtime('upload_current', None)
                log_message(f"Upload concluído: {job.obra_nome}", level='success')
            else:
                tries, st = QUEUE_STORE.mark_failed(job.id, 'Falha no upload', requeue=True, max_tries=5)
                QUEUE_STORE.set_runtime('upload_current', None)
                log_message(f"Falha no upload: {job.obra_nome} (tentativa {tries}, status={st})", level='error')

            # espelho legacy
            try:
                mirror_legacy_queue_json(QUEUE_STORE, FILA_UPLOAD_FILE)
            except Exception:
                pass

            time.sleep(1)

        except Exception as e:
            tb = traceback.format_exc()
            log_message(f"Erro no worker: {e}\n{tb}", level='error')
            time.sleep(5)

def index():
    return render_template('index.html')

@app.route('/api/start', methods=['POST'])
def start_upload():
    """Inicia o processamento da fila"""
    with status_lock:
        if bot_status['running']:
            return jsonify({'error': 'Upload já está em andamento'}), 400
        bot_status['running'] = True
        bot_status['state'] = 'running'
    
    QUEUE_STORE.set_runtime('upload_running', True)
    log_message("Upload iniciado - monitorando fila (SQLite)...")
    update_status({'running': True, 'state': 'running'})
    
    return jsonify({'success': True})

@app.route('/api/stop', methods=['POST'])
def stop_upload():
    """Para o processamento"""
    with status_lock:
        bot_status['running'] = False
        bot_status['state'] = 'stopping'
    QUEUE_STORE.set_runtime('upload_running', False)
    log_message("Solicitação de parada recebida...")
    return jsonify({'success': True})

@app.route('/api/status')
def get_status():
    """Retorna status atual (API local + runtime do worker)."""
    fila = carregar_fila()
    running = bool(QUEUE_STORE.get_runtime('upload_running', False))
    current = QUEUE_STORE.get_runtime('upload_current', None)

    with status_lock:
        bot_status['running'] = running
        bot_status['state'] = 'running' if running else 'stopped'
        bot_status['fila_pendente'] = len(fila)
        if current:
            bot_status['current_job'] = current
        return jsonify(bot_status)

@app.route('/api/job/<job_id>/events')
def get_job_events(job_id):
    """Retorna eventos persistidos do job (observabilidade sistêmica)."""
    try:
        ev = QUEUE_STORE.list_events(job_id, limit=300)
        return jsonify({'job_id': job_id, 'events': ev})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/fila')
def get_fila():
    """Retorna a fila de upload"""
    return jsonify(carregar_fila())

@app.route('/api/config', methods=['GET'])
def config():
    """Retorna configurações"""
    return jsonify({
        'site_url': SITE_URL,
        'email': SITE_EMAIL,
        'catalogo_path': str(CATALOGO_PATH),
        'downloads_dir': str(DOWNLOADS_DIR)
    })

@app.route('/api/quebrados')
def get_quebrados():
    """Retorna lista de capítulos quebrados"""
    return jsonify(carregar_relatorio_quebrados())

@app.route('/api/quebrados/download')
def download_quebrados():
    """Baixa o arquivo CSV de capítulos quebrados"""
    from flask import send_file
    if CAPITULOS_QUEBRADOS_FILE.exists():
        return send_file(str(CAPITULOS_QUEBRADOS_FILE), as_attachment=True, download_name='capitulos_quebrados.csv')
    return jsonify({'error': 'Arquivo não encontrado'}), 404

@app.route('/api/screenshot')
def get_screenshot():
    """Retorna o screenshot ao vivo do navegador"""
    from flask import send_file
    import io
    
    global CURRENT_PAGE
    
    if CURRENT_PAGE:
        try:
            screenshot_bytes = CURRENT_PAGE.screenshot(type='png')
            return send_file(
                io.BytesIO(screenshot_bytes),
                mimetype='image/png',
                as_attachment=False
            )
        except Exception as e:
            pass
    
    return jsonify({'error': 'Nenhum screenshot disponível'}), 404

@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve arquivos estáticos"""
    from flask import send_from_directory
    return send_from_directory(str(SCREENSHOT_DIR), filename)

# ============================================
# WebSocket Events
# ============================================

@socketio.on('connect')
def handle_connect():
    fila = carregar_fila()
    with status_lock:
        bot_status['fila_pendente'] = len(fila)
        emit('status', bot_status)
        emit('logs', bot_status['logs'])

# ============================================
# Main
# ============================================

if __name__ == '__main__':
    print("=" * 50)
    print("Culto Demoníaco Uploader - Dashboard")
    print("=" * 50)
    print(f"Site: {SITE_URL}")
    print(f"Email: {SITE_EMAIL or 'NÃO CONFIGURADO'}")
    print(f"Fila: {FILA_UPLOAD_FILE}")
    print("=" * 50)
    print("Acesse: http://localhost:5001")
    print("=" * 50)
    
    socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)