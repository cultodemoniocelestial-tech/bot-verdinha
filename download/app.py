#!/usr/bin/env python3
"""
Verdinha Dashboard - Backend Robusto
Dashboard web para gerenciar downloads do site Verdinha
Versão com melhorias de robustez, retry, persistência e thread-safety
"""

import os
import json
import threading
import queue
import time
import random
import traceback
import requests
import uuid
from pathlib import Path
import sys
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from shared.queue_store import QueueStore, mirror_legacy_queue_json
from shared.download_store import DownloadQueueStore

from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

# ============================================
# Função para carregar .env manualmente
# ============================================

def load_env_file():
    """Carrega variáveis do arquivo .env manualmente"""
    env_vars = {}
    
    # Tentar vários caminhos possíveis para o .env
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
                # Ignorar comentários e linhas vazias
                if not line or line.startswith('#'):
                    continue
                # Separar chave=valor
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # Remover aspas se houver
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    env_vars[key] = value
                    # Também setar no os.environ
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
app.config['SECRET_KEY'] = 'verdinha-dash-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Diretório de downloads
DOWNLOADS_DIR = Path(ENV_VARS.get('DOWNLOADS_DIR', os.environ.get('DOWNLOADS_DIR', './downloads')))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Credenciais (carregadas do .env)
EMAIL = ENV_VARS.get('VERDINHA_EMAIL', '')
SENHA = ENV_VARS.get('VERDINHA_SENHA', '')

# Debug para log
if EMAIL:
    print(f"\n*** Credenciais carregadas: {EMAIL} ***\n")
else:
    print("\n*** AVISO: VERDINHA_EMAIL não encontrado no .env! ***\n")

# Configurações de robustez
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # segundos
REQUEST_TIMEOUT = 45  # segundos
MAX_LOGS = 300

# Screenshot ao vivo
SCREENSHOT_DIR = Path(__file__).parent / 'static'
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOT_FILE = SCREENSHOT_DIR / 'live_screenshot.png'
CURRENT_PAGE = None  # Referência global para a página do navegador

# Regras de extração/validação
MIN_IMAGES_PER_CHAPTER = int(os.environ.get('VERDINHA_MIN_IMAGES_PER_CHAPTER', '3'))
# Capítulos com menos imagens não serão ignorados: viram 'partial' ou 'broken'.
MIN_IMAGES_PARTIAL = int(os.environ.get('VERDINHA_MIN_IMAGES_PARTIAL', '1'))
MIN_DIM_PX = int(os.environ.get('VERDINHA_MIN_DIM_PX', '300'))
BATCH_SIZE_DEFAULT = int(os.environ.get('VERDINHA_BATCH_SIZE', '0'))

# IA opcional para sugerir ajustes de extração (NÃO altera navegação)
AI_ENABLED = os.environ.get('VERDINHA_AI_ENABLED', '0') == '1'
AI_MODEL = os.environ.get('OPENAI_MODEL', os.environ.get('VERDINHA_AI_MODEL', 'gpt-4o-mini'))
AI_MAX_CALLS_PER_JOB = int(os.environ.get('VERDINHA_AI_MAX_CALLS_PER_JOB', '5'))

SITE_PROFILE_FILE = DOWNLOADS_DIR / '_site_profile.json'

def normalize_url(url: str) -> str:
    """Normaliza URL para comparação (sem #hash e sem parâmetros de tracking)."""
    if not url:
        return ''
    try:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(url)
        # Remover fragment
        fragment = ''
        # Manter query mas remover parâmetros comuns de tracking
        keep_q = []
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            lk = k.lower()
            if lk.startswith('utm_') or lk in {'ref', 'fbclid', 'gclid', 'mc_cid', 'mc_eid'}:
                continue
            keep_q.append((k, v))
        query = urlencode(keep_q, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))
    except Exception:
        return url.split('#', 1)[0]


# ============================================
# Estado Global com Thread-Safety
# ============================================

status_lock = threading.Lock()
history_lock = threading.Lock()

download_queue = queue.Queue()
current_download = None
download_history = []

bot_status = {
    'running': False,
    'current_job': None,
    'progress': 0,
    'chapter': 0,
    'total_images': 0,
    'state': 'idle',  # idle, starting, running, stopping, completed, error
    'logs': []
}

WORKER_SIO = None  # socketio client when running in worker mode
RUN_MODE = os.environ.get('DOWNLOAD_RUN_MODE', 'api')

def _should_continue():
    """No modo worker, respeita a flag de stop no SQLite."""
    if RUN_MODE == 'worker':
        return DOWNLOAD_STORE.get_flag('download_stop_requested', '0') != '1'
    with status_lock:
        return bool(bot_status.get('running'))


# ============================================
# Persistência de Histórico e Progresso
# ============================================

HISTORY_FILE = DOWNLOADS_DIR / 'history.json'
PROGRESS_FILE = DOWNLOADS_DIR / 'progress.json'
FILA_UPLOAD_FILE = Path(__file__).parent.parent / 'fila_upload.json'
QUEUE_STORE = QueueStore()
DOWNLOAD_STORE = DownloadQueueStore()

def adicionar_fila_upload(obra_nome, job):
    """Adiciona uma obra na fila (fonte de verdade: SQLite)."""
    try:
        # job_id estável para idempotência
        job_id = str(job.get('job_id') or job.get('id') or f"{obra_nome}-{int(time.time())}")
        pasta_relativa = job.get('pasta') or str(DOWNLOADS_DIR / obra_nome)
        pasta_absoluta = str(Path(pasta_relativa).resolve())

        payload = dict(job)
        payload['obra_nome'] = obra_nome
        payload['pasta'] = pasta_absoluta
        payload['job_id'] = job_id

        QUEUE_STORE.enqueue(job_id=job_id, obra_nome=obra_nome, pasta=pasta_absoluta, payload=payload)

        # Espelho legacy para compatibilidade/inspeção
        try:
            mirror_legacy_queue_json(QUEUE_STORE, FILA_UPLOAD_FILE)
        except Exception:
            pass

        log_message(f"Obra adicionada à fila (SQLite): {obra_nome}")
        return True
    except Exception as e:
        log_message(f"Erro ao adicionar na fila (SQLite): {e}", level='error')
        return False
def load_history():
    """Carrega o histórico de downloads do disco"""
    global download_history
    try:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                download_history = json.load(f)
    except Exception as e:
        print(f"Erro ao carregar histórico: {e}")
        download_history = []

def save_history():
    """Salva o histórico de downloads no disco"""
    with history_lock:
        try:
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(download_history[-100:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Erro ao salvar histórico: {e}")

def load_progress(obra_nome):
    """Carrega o progresso de uma obra específica"""
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                all_progress = json.load(f)
                return all_progress.get(obra_nome, {})
    except Exception as e:
        print(f"Erro ao carregar progresso: {e}")
    return {}

def save_progress(obra_nome, progress_data):
    """Salva o progresso de uma obra específica"""
    try:
        all_progress = {}
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                all_progress = json.load(f)
        
        all_progress[obra_nome] = progress_data
        
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao salvar progresso: {e}")

def clear_progress(obra_nome):
    """Limpa o progresso de uma obra (quando concluída)"""
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                all_progress = json.load(f)
            
            if obra_nome in all_progress:
                del all_progress[obra_nome]
                
            with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao limpar progresso: {e}")

# Carregar histórico na inicialização
load_history()

# ============================================
# Logging com Thread-Safety
# ============================================

def log_message(message, level='info', job_id=None, chapter=None, step=None):
    """Envia mensagem de log para o frontend via Socket.IO.

    - modo API: emite diretamente para clientes conectados
    - modo WORKER: envia via cliente Socket.IO (WORKER_SIO) para o dashboard
    """
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = {
        'timestamp': timestamp,
        'level': level,
        'message': message,
        'job_id': job_id,
        'chapter': chapter,
        'step': step
    }

    with status_lock:
        bot_status['logs'].append(log_entry)
        if len(bot_status['logs']) > MAX_LOGS:
            bot_status['logs'] = bot_status['logs'][-MAX_LOGS:]

    if RUN_MODE == 'worker' and WORKER_SIO is not None:
        try:
            WORKER_SIO.emit('log', log_entry)
        except Exception:
            pass
    else:
        socketio.emit('log', log_entry)

def update_status(data):
    """Atualiza status e envia para o frontend.

    No modo worker, também faz heartbeat no SQLite para /api/status.
    """
    with status_lock:
        bot_status.update(data)
        snapshot = dict(bot_status)

    # Heartbeat no DB (modo worker)
    if RUN_MODE == 'worker':
        try:
            current = snapshot.get('current_job') or {}
            jid = current.get('job_id') or current.get('id')
            if jid:
                DOWNLOAD_STORE.heartbeat(
                    job_id=str(jid),
                    chapter=int(snapshot.get('chapter') or 0),
                    progress=int(snapshot.get('progress') or 0),
                    total_images=int(snapshot.get('total_images') or 0),
                    state=str(snapshot.get('state') or '')
                )
        except Exception:
            pass

    if RUN_MODE == 'worker' and WORKER_SIO is not None:
        try:
            WORKER_SIO.emit('status', snapshot)
        except Exception:
            pass
    else:
        socketio.emit('status', snapshot)

# ============================================
# Funções de Download com Retry
# ============================================

def download_with_retry(url, filepath, cookies_dict, headers, max_retries=MAX_RETRIES):
    """
    Baixa um arquivo com retry e backoff exponencial.
    Usa streaming para eficiência de memória e escrita atômica.
    """
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url,
                cookies=cookies_dict,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                stream=True
            )
            response.raise_for_status()
            
            # Verificar Content-Type
            content_type = response.headers.get('Content-Type', '')
            if not content_type.startswith('image/'):
                raise ValueError(f"Content-Type inválido: {content_type}")
            
            # Escrita atômica: salvar em .tmp primeiro
            tmp_filepath = str(filepath) + '.tmp'
            total_size = 0
            
            with open(tmp_filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        total_size += len(chunk)
            
            # Verificar se o arquivo não está vazio
            if total_size == 0:
                raise ValueError("Arquivo vazio recebido")
            
            # Renomear para o nome final (atômico)
            os.rename(tmp_filepath, filepath)
            return True
            
        except Exception as e:
            last_error = e
            
            # Limpar arquivo temporário se existir
            tmp_filepath = str(filepath) + '.tmp'
            if os.path.exists(tmp_filepath):
                try:
                    os.remove(tmp_filepath)
                except:
                    pass
            
            if attempt < max_retries:
                wait_time = RETRY_BACKOFF_BASE ** attempt + random.uniform(0, 1)
                log_message(
                    f"Tentativa {attempt}/{max_retries} falhou. Aguardando {wait_time:.1f}s...",
                    level='warning'
                )
                time.sleep(wait_time)
            else:
                log_message(
                    f"Falha após {max_retries} tentativas: {str(e)}",
                    level='error'
                )
    
    return False


# -------------------------
# Perfil do site (seletores/filtros) + IA opcional
# -------------------------

DEFAULT_CONTAINER_SELECTORS = [
    '.images-container',
    '#chapter-content',
    '.reading-content',
    '.wp-manga-chapter-img',
    '.chapter-content',
    '.entry-content'
]

DEFAULT_COMMENT_SELECTORS = [
    '#comments',
    '.comments',
    '.chapter-comments',
    '.comentarios-section',
    '.comment',
    '.wpd-comment'
]

PROFILE_LOCK = threading.Lock()

def _load_site_profile():
    try:
        if SITE_PROFILE_FILE.exists():
            with open(SITE_PROFILE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Erro ao carregar profile do site: {e}")
    return {}

def _save_site_profile(profile: dict):
    try:
        with PROFILE_LOCK:
            tmp = str(SITE_PROFILE_FILE) + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SITE_PROFILE_FILE)
    except Exception as e:
        print(f"Erro ao salvar profile do site: {e}")

def get_profile_for_url(url: str) -> dict:
    host = ''
    try:
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or '').lower()
    except Exception:
        host = ''
    allp = _load_site_profile()
    p = allp.get(host, {}) if host else {}
    return p if isinstance(p, dict) else {}

def update_profile_for_url(url: str, updates: dict):
    host = ''
    try:
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or '').lower()
    except Exception:
        host = ''
    if not host:
        return
    allp = _load_site_profile()
    cur = allp.get(host, {})
    if not isinstance(cur, dict):
        cur = {}
    cur.update(updates)
    allp[host] = cur
    _save_site_profile(allp)

def is_probably_blocked(page) -> bool:
    """Detecta bloqueios comuns. Não tenta burlar; apenas pausa para continuação manual."""
    try:
        title = (page.title() or '').lower()
        if 'just a moment' in title or 'attention required' in title or 'cloudflare' in title:
            return True
    except Exception:
        pass
    try:
        if page.locator('iframe[src*="turnstile"]').count() > 0:
            return True
        if page.locator('input[name="cf-turnstile-response"]').count() > 0:
            return True
    except Exception:
        pass
    try:
        txt = page.evaluate("() => (document.body && document.body.innerText ? document.body.innerText : '')") or ''
        t = txt.lower()
        if 'cloudflare' in t and ('checking your browser' in t or 'verify you are human' in t or 'just a moment' in t):
            return True
        if 'access denied' in t or 'forbidden' in t:
            return True
    except Exception:
        pass
    return False

def ai_suggest_profile(snapshot: dict, job_id: str = None) -> dict | None:
    """Chama OpenAI (opcional) para sugerir seletores/filtros. Nunca altera navegação."""
    if not AI_ENABLED:
        return None
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        return None

    schema = {
        "type": "object",
        "properties": {
            "container_selectors": {"type": "array", "items": {"type": "string"}},
            "comment_selectors": {"type": "array", "items": {"type": "string"}},
            "min_dim_px": {"type": "integer"},
            "min_images_ok": {"type": "integer"},
            "notes": {"type": "string"}
        },
        "required": ["container_selectors", "comment_selectors", "min_dim_px", "min_images_ok"]
    }

    system = (
        "Ajuste somente seletores de EXTRAÇÃO de imagens (container e comentários) e filtros como min_dim_px. "
        "NÃO sugira bypass de Cloudflare/anti-bot e NÃO altere navegação (botão Próximo). "
        "Retorne apenas JSON no schema."
    )

    payload = {
        "model": AI_MODEL,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)}
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "verdinha_profile_plan",
                "schema": schema,
                "strict": True
            }
        }
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45
        )
        resp.raise_for_status()
        data = resp.json()
        plan_text = data.get("output_text") or ""
        if not plan_text:
            # fallback: tentar achar em output[0].content[0].text
            try:
                outputs = data.get("output", [])
                if outputs:
                    content = outputs[0].get("content", [])
                    if content:
                        plan_text = content[0].get("text", "")
            except Exception:
                plan_text = ""

        if not plan_text:
            return None
        plan = json.loads(plan_text)

        return {
            "container_selectors": [s for s in (plan.get("container_selectors") or []) if isinstance(s, str)][:20],
            "comment_selectors": [s for s in (plan.get("comment_selectors") or []) if isinstance(s, str)][:20],
            "min_dim_px": int(plan.get("min_dim_px") or MIN_DIM_PX),
            "min_images_ok": int(plan.get("min_images_ok") or MIN_IMAGES_PER_CHAPTER),
            "notes": str(plan.get("notes") or "")[:500]
        }
    except Exception as e:
        if job_id:
            log_message(f"IA: falha ao chamar OpenAI: {e}", level='warning', job_id=job_id, step='ai')
        return None

# ============================================
# Bot de Download Principal
# ============================================

# -------------------------
# Robustez de renderização/scroll e extração de imagens (sem mexer na navegação)
# -------------------------

SCROLL_MAX_CYCLES = int(os.environ.get('VERDINHA_SCROLL_MAX_CYCLES', '140'))
SCROLL_STABLE_CYCLES = int(os.environ.get('VERDINHA_SCROLL_STABLE_CYCLES', '4'))
CHAPTER_READY_TIMEOUT_MS = int(os.environ.get('VERDINHA_CHAPTER_READY_TIMEOUT_MS', '25000'))
CHAPTER_READY_MIN_WRAPPERS = int(os.environ.get('VERDINHA_CHAPTER_READY_MIN_WRAPPERS', '1'))
EXTRACT_RETRIES = int(os.environ.get('VERDINHA_EXTRACT_RETRIES', '2'))

_JS_WAIT_READY = """({containerSelectors, minWrappers}) => {
  const pickContainer = () => {
    for (const sel of containerSelectors || []) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  };
  const c = pickContainer();
  if (!c) return false;
  const w = c.querySelectorAll('.page-wrapper').length;
  const imgs = c.querySelectorAll('img').length;
  return (w >= minWrappers) || (imgs >= minWrappers);
}"""

def wait_for_chapter_ready(page, container_selectors, min_wrappers=CHAPTER_READY_MIN_WRAPPERS, timeout_ms=CHAPTER_READY_TIMEOUT_MS):
    """Espera o container do capítulo existir e começar a ter páginas/imagens."""
    try:
        page.wait_for_function(
            _JS_WAIT_READY,
            {"containerSelectors": container_selectors, "minWrappers": int(min_wrappers)},
            timeout=timeout_ms
        )
        return True
    except Exception:
        return False

_JS_COUNT_WRAPPERS = """(sels) => {
  let c = null;
  let used = '';
  for (const sel of sels || []) {
    const el = document.querySelector(sel);
    if (el) { c = el; used = sel; break; }
  }
  const root = c || document;
  const wrappers = root.querySelectorAll('.page-wrapper').length;
  const imgs = root.querySelectorAll('img').length;
  const h = document.body ? document.body.scrollHeight : 0;
  return {wrappers, imgs, scrollHeight: h, container_used: used};
}"""

def scroll_until_stable(page, container_selectors, max_cycles=SCROLL_MAX_CYCLES, stable_cycles=SCROLL_STABLE_CYCLES):
    """Scroll incremental até a quantidade de páginas/imagens estabilizar."""
    last_wrappers = -1
    last_imgs = -1
    stable = 0
    cycles = 0
    last_stats = {}
    for i in range(int(max_cycles)):
        cycles = i + 1
        # Scroll incremental (mais robusto que 'pular' direto pro fim)
        try:
            page.evaluate("""() => { window.scrollBy(0, Math.floor(window.innerHeight * 0.9)); }""")
        except Exception:
            pass
        time.sleep(random.uniform(0.25, 0.55))

        # A cada alguns ciclos, força ir mais pro fim (gatilho de infinite scroll)
        if (i + 1) % 10 == 0:
            try:
                page.evaluate("""() => { const h = document.body ? document.body.scrollHeight : 0; window.scrollTo(0, h); }""")
            except Exception:
                pass
            time.sleep(random.uniform(0.7, 1.2))

        try:
            stats = page.evaluate(_JS_COUNT_WRAPPERS, container_selectors) or {}
        except Exception:
            stats = {}

        wrappers = int(stats.get('wrappers') or 0)
        imgs = int(stats.get('imgs') or 0)
        last_stats = stats

        if wrappers > last_wrappers or imgs > last_imgs:
            stable = 0
            last_wrappers = max(last_wrappers, wrappers)
            last_imgs = max(last_imgs, imgs)
        else:
            stable += 1

        if stable >= int(stable_cycles) and (wrappers > 0 or imgs > 0):
            break

    last_stats = last_stats or {}
    last_stats['cycles'] = cycles
    last_stats['stable'] = stable
    return last_stats

_JS_EXTRACT_URLS = r"""({containerSelectors, commentSelectors, minDim}) => {
  const normalize = (u) => {
    if (!u) return '';
    try { return new URL(u, window.location.href).href; } catch (e) { return u; }
  };

  const isInComments = (el) => {
    if (!el || !el.closest) return false;
    for (const sel of (commentSelectors || [])) {
      try { if (el.closest(sel)) return true; } catch (e) {}
    }
    return false;
  };

  const pickContainer = () => {
    for (const sel of (containerSelectors || [])) {
      const el = document.querySelector(sel);
      if (el) return {el, sel};
    }
    return {el: null, sel: ''};
  };

  const pickFromSrcset = (srcset) => {
    if (!srcset) return '';
    const parts = srcset.split(',').map(p => p.trim()).filter(Boolean);
    if (!parts.length) return '';
    // tenta escolher o MAIOR (largura/dpr), senão pega o último
    let best = {u: '', score: -1};
    for (const p of parts) {
      const seg = p.split(/\s+/).filter(Boolean);
      const u = seg[0] || '';
      let score = 0;
      if (seg[1]) {
        const d = seg[1].trim();
        if (d.endsWith('w')) score = parseInt(d.slice(0, -1)) || 0;
        else if (d.endsWith('x')) score = (parseFloat(d.slice(0, -1)) || 0) * 1000;
      }
      if (score >= best.score) best = {u, score};
    }
    return best.u || parts[parts.length - 1].split(/\s+/)[0] || '';
  };

  const pickUrl = (img) => {
    if (!img) return '';
    let u = img.currentSrc || img.getAttribute('src') || '';
    if (!u) u = img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original') || '';
    if (!u) u = pickFromSrcset(img.getAttribute('srcset') || img.getAttribute('data-srcset') || '');
    if (!u) return '';
    return normalize(u);
  };

  const getBgUrl = (el) => {
    if (!el) return '';
    let bg = '';
    try { bg = window.getComputedStyle(el).backgroundImage || ''; } catch (e) { bg = (el.style && el.style.backgroundImage) || ''; }
    const m = /url\((['"]?)(.*?)\1\)/i.exec(bg || '');
    return m && m[2] ? normalize(m[2]) : '';
  };

  const isJunkUrl = (u) => {
    const s = (u || '').toLowerCase();
    return (
      s.includes('avatar') ||
      s.includes('logo') ||
      s.includes('icon') ||
      s.includes('favicon') ||
      s.includes('sprite') ||
      s.includes('emoji')
    );
  };

  const looksLikeImage = (u) => {
    const s = (u || '').toLowerCase();
    return (
      /\.(jpe?g|png|webp|gif|avif)(\?|$)/i.test(u) ||
      s.includes('format=webp') ||
      s.includes('format=png') ||
      s.includes('format=jpg') ||
      s.includes('format=jpeg') ||
      s.includes('/scans/')
    );
  };

  const acceptBySize = (img) => {
    if (!img) return true;
    const w = img.naturalWidth || img.width || 0;
    const h = img.naturalHeight || img.height || 0;
    if (w > 0 && h > 0 && minDim) {
      if (w < minDim || h < minDim) return false;
    }
    return true;
  };

  const {el: container, sel: containerUsed} = pickContainer();
  const root = container || document;

  // Preferir pages do capítulo (page-wrapper) se existirem
  const wrappers = Array.from(root.querySelectorAll('.page-wrapper'));
  const urls = [];

  const pushUrl = (u) => {
    u = normalize(u);
    if (!u) return;
    if (isJunkUrl(u)) return;
    if (!looksLikeImage(u)) return;
    urls.push(u);
  };

  if (wrappers.length) {
    for (const w of wrappers) {
      if (isInComments(w)) continue;

      // 1) imgs dentro do wrapper
      const imgs = Array.from(w.querySelectorAll('img'));
      if (imgs.length) {
        for (const img of imgs) {
          if (isInComments(img)) continue;
          const u = pickUrl(img);
          if (!u) continue;
          if (!acceptBySize(img)) continue;
          pushUrl(u);
        }
      }

      // 2) fallback: background-image no wrapper
      if (!urls.length || imgs.length === 0) {
        const bg = getBgUrl(w);
        if (bg) pushUrl(bg);
      }
    }
  } else {
    // fallback: sem wrappers, tenta imgs do container
    const imgs = Array.from(root.querySelectorAll('img'));
    for (const img of imgs) {
      if (isInComments(img)) continue;
      const u = pickUrl(img);
      if (!u) continue;
      if (!acceptBySize(img)) continue;
      pushUrl(u);
    }
  }

  // dedupe mantendo ordem
  const seen = new Set();
  const uniq = [];
  for (const u of urls) {
    if (!seen.has(u)) { seen.add(u); uniq.push(u); }
  }

  const debug = {
    container_used: containerUsed || '',
    wrappers: wrappers.length,
    imgs_dom: root.querySelectorAll('img').length,
    extracted: uniq.length
  };

  return {urls: uniq, debug};
}"""

def extract_image_urls(page, container_selectors, comment_selectors, min_dim):
    """Extrai URLs das imagens do capítulo, com debug."""
    try:
        res = page.evaluate(_JS_EXTRACT_URLS, {
            "containerSelectors": container_selectors,
            "commentSelectors": comment_selectors,
            "minDim": int(min_dim or 0),
        }) or {}
        urls = res.get('urls') or []
        debug = res.get('debug') or {}
        # garantir list[str]
        urls = [u for u in urls if isinstance(u, str) and u.strip()]
        return urls, debug
    except Exception as e:
        return [], {"error": str(e)}

def run_download_bot(job):
    """Executa o bot de download para um job específico"""
    from playwright.sync_api import sync_playwright
    
    url = job['url']
    obra_nome = job['nome']
    job_id = job['id']
    pasta_obra = DOWNLOADS_DIR / obra_nome
    pasta_obra.mkdir(parents=True, exist_ok=True)
    
    # Carregar progresso anterior (se existir)
    progress_data = load_progress(obra_nome)
    ultimo_capitulo_url = progress_data.get('ultimo_capitulo_url')
    capitulos_baixados = progress_data.get('capitulos_baixados', [])
    upload_enqueued = bool(progress_data.get('upload_enqueued'))

    # Memória de URLs já visitadas (evita duplicação/loop sem depender de padrão de URL)
    visited_urls = set()
    for c in capitulos_baixados:
        u = normalize_url(c.get('url', ''))
        if u:
            visited_urls.add(u)
    
    force_url = bool(job.get('force_url'))
    if ultimo_capitulo_url and not force_url:
        log_message(
            f"Continuando download de onde parou ({len(capitulos_baixados)} capítulos já baixados)",
            level='info', job_id=job_id
        )
        url = ultimo_capitulo_url
    
    log_message(f"Iniciando download: {obra_nome}", job_id=job_id)
    log_message(f"URL: {url}", job_id=job_id)
    
    capitulo = len(capitulos_baixados) + 1
    total_imagens = 0
    imagens_falhas = 0
    start_time = time.time()

    stop_reason = None
    stop_url = None
    ai_calls = 0
    consecutive_broken = 0
    batch_counter = 0

    browser = None
    
    try:
        with sync_playwright() as p:
            # Importar Divine Stealth
            from divine_stealth import (
                apply_divine_stealth, 
                get_stealth_context_options, 
                get_stealth_browser_args,
                human_type,
                human_click,
                human_delay,
                random_mouse_movement,
                random_scroll
            )
            log_message("Divine Stealth carregado!", job_id=job_id, level='success')
            
            # Configurar navegador com argumentos de stealth máximo
            browser_args = get_stealth_browser_args()
            
            browser = p.chromium.launch(
                headless=True,
                args=browser_args
            )
            
            # Criar contexto com opções de stealth
            context_options = get_stealth_context_options()
            context = browser.new_context(**context_options)
            
            page = context.new_page()
            
            # Salvar referência global para screenshots ao vivo
            global CURRENT_PAGE
            CURRENT_PAGE = page
            
            # Aplicar Divine Stealth JavaScript
            try:
                apply_divine_stealth(page)
                log_message("Divine Stealth aplicado com sucesso!", job_id=job_id, level='success')
            except Exception as e:
                log_message(f"Aviso ao aplicar Divine Stealth: {e}", level='warning', job_id=job_id)

            # Fazer login com retry
            # Recarregar credenciais do .env para garantir que pegou as mudanças
            global EMAIL, SENHA
            env_vars = load_env_file()
            EMAIL = env_vars.get('VERDINHA_EMAIL', '')
            SENHA = env_vars.get('VERDINHA_SENHA', '')
            
            log_message(f"Email configurado: {EMAIL if EMAIL else 'NÃO CONFIGURADO'}", job_id=job_id)

            # Login com Divine Stealth (comportamento 100% humanizado)
            if EMAIL and SENHA:
                log_message("Iniciando login com Divine Stealth...", job_id=job_id, step='login')
                login_success = False
                
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        # Navegar para a página de login
                        log_message("Navegando para página de login...", job_id=job_id)
                        page.goto('https://verdinha.wtf/login', timeout=60000)
                        
                        # Re-aplicar stealth após navegação
                        apply_divine_stealth(page)
                        
                        # Aguardar o campo de email aparecer (indica que a página carregou)
                        log_message("Aguardando página carregar...", job_id=job_id)
                        email_input = page.locator('#email')
                        
                        try:
                            email_input.wait_for(state='visible', timeout=30000)
                            log_message("Página de login carregada!", job_id=job_id, level='success')
                        except:
                            log_message("Timeout aguardando página. Tentando continuar...", job_id=job_id, level='warning')
                        
                        # Delay humanizado após carregar página
                        human_delay(1.0, 2.0)
                        
                        # Movimentos aleatórios de mouse (simula humano olhando a página)
                        random_mouse_movement(page)
                        human_delay(0.5, 1.0)
                        
                        senha_input = page.locator('#password')
                        entrar_btn = page.locator('button:has-text("Entrar")')
                        
                        if email_input.is_visible(timeout=10000):
                            log_message("Campos de login encontrados. Digitando...", job_id=job_id)
                            
                            # Clicar no campo de email de forma humanizada
                            human_click(page, '#email')
                            human_delay(0.2, 0.5)
                            
                            # Digitar email caractere por caractere
                            human_type(page, '#email', EMAIL)
                            human_delay(0.3, 0.8)
                            
                            # Movimento de mouse para o campo de senha
                            random_mouse_movement(page)
                            human_delay(0.2, 0.4)
                            
                            # Clicar no campo de senha
                            human_click(page, '#password')
                            human_delay(0.2, 0.5)
                            
                            # Digitar senha caractere por caractere
                            human_type(page, '#password', SENHA)
                            human_delay(0.5, 1.2)
                            
                            # Movimento final antes do clique
                            random_mouse_movement(page)
                            human_delay(0.3, 0.7)
                            
                            # Clicar no botão de entrar de forma humanizada
                            human_click(page, 'button:has-text("Entrar")')
                            
                            # Aguardar resposta do servidor
                            human_delay(3.0, 6.0)
                            
                            # Verificar se logou
                            if not page.locator('#email').is_visible(timeout=5000):
                                log_message("Login realizado com sucesso! (Divine Stealth)", job_id=job_id, step='login', level='success')
                                login_success = True
                                break
                            else:
                                log_message(f"Tentativa {attempt}: Falha no login. Verificando...", level='warning', job_id=job_id)
                                
                                # Verificar se tem CAPTCHA ou erro
                                page_content = page.content()
                                if 'captcha' in page_content.lower() or 'recaptcha' in page_content.lower():
                                    log_message("CAPTCHA detectado! Aguardando...", level='warning', job_id=job_id)
                                    human_delay(5.0, 10.0)
                                elif 'erro' in page_content.lower() or 'inválid' in page_content.lower():
                                    log_message("Credenciais inválidas!", level='error', job_id=job_id)
                                
                    except Exception as e:
                        log_message(f"Tentativa {attempt}/{MAX_RETRIES} falhou: {e}", level='warning', job_id=job_id)
                        if attempt < MAX_RETRIES:
                            # Backoff exponencial humanizado
                            wait_time = RETRY_BACKOFF_BASE ** attempt + random.uniform(1, 3)
                            human_delay(wait_time, wait_time + 2)
                
                if not login_success:
                    log_message("Não foi possível logar. Tentando continuar sem login...", level='warning', job_id=job_id)
            else:
                log_message("Credenciais não configuradas. Continuando sem login...", job_id=job_id)        
            # Navegar para o capítulo
            update_status({'state': 'running'})
            log_message(f"Navegando para o capítulo...", job_id=job_id, step='navigation')
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            time.sleep(random.uniform(3, 5))
            # Seletores/filtros do site (perfil por host) — não altera navegação
            profile = get_profile_for_url(url)
            container_sel = profile.get('container_selectors') or DEFAULT_CONTAINER_SELECTORS
            comment_sel = profile.get('comment_selectors') or DEFAULT_COMMENT_SELECTORS
            try:
                min_dim = int(profile.get('min_dim_px') or MIN_DIM_PX)
            except Exception:
                min_dim = MIN_DIM_PX
            # Garantir tipos
            if not isinstance(container_sel, list):
                container_sel = DEFAULT_CONTAINER_SELECTORS
            if not isinstance(comment_sel, list):
                comment_sel = DEFAULT_COMMENT_SELECTORS

            
            # Loop de download
            while _should_continue():
                update_status({
                    'chapter': capitulo,
                    'progress': 0
                })

                # Se o usuário informou o total esperado de capítulos, parar quando atingir
                expected_total = int(job.get('expected_total') or 0)
                if expected_total > 0 and capitulo > expected_total:
                    log_message(f"Total esperado atingido ({expected_total}). Finalizando.", level='info', job_id=job_id, step='done')
                    stop_reason = 'expected_total_reached'
                    stop_url = page.url
                    break

                downloaded_this_chapter = False

                current_url = page.url
                current_url_norm = normalize_url(current_url)

                # Se voltar para uma URL já visitada, parar para evitar duplicação/loop
                if current_url_norm in visited_urls:
                    log_message(
                        f"[Capítulo {capitulo}] URL repetida detectada. Parando para evitar duplicação.",
                        level='warning', job_id=job_id, chapter=capitulo
                    )
                    log_message(f"RESUME_URL: {current_url}", level='warning', job_id=job_id)
                    print(f"RESUME_URL: {current_url}")

                    stop_reason = 'url_repetida'
                    stop_url = current_url

                    save_progress(obra_nome, {
                        'ultimo_capitulo_url': current_url,
                        'capitulos_baixados': capitulos_baixados,
                        'ultima_atualizacao': datetime.now().isoformat(),
                        'stopped_reason': 'url_repetida',
                        'stopped_at_url': current_url
                    })
                    break

                visited_urls.add(current_url_norm)

                log_message(
                    f"[Capítulo {capitulo}] Extraindo imagens...",
                    job_id=job_id, chapter=capitulo, step='extract'
                )
                
                # Robust: esperar capítulo montar e carregar páginas (sem depender de padrão de URL)
                wait_for_chapter_ready(page, container_sel, min_wrappers=CHAPTER_READY_MIN_WRAPPERS, timeout_ms=CHAPTER_READY_TIMEOUT_MS)

                # Scroll até estabilizar a quantidade de páginas/imagens (lazy/infinite scroll)
                scroll_info = {}
                try:
                    scroll_info = scroll_until_stable(page, container_sel)
                except Exception:
                    scroll_info = {}

                # (Opcional) voltar um pouco ao topo para reduzir chance de currentSrc vazio em alguns sites
                try:
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(0.4)
                except Exception:
                    pass

                # Extrair URLs das imagens (robusto) com retries se vier só 0/1 em capítulo grande
                imgs = []
                extract_info = {}
                for attempt in range(EXTRACT_RETRIES + 1):
                    imgs, extract_info = extract_image_urls(page, container_sel, comment_sel, min_dim)

                    wrappers = int((extract_info or {}).get('wrappers') or 0) if isinstance(extract_info, dict) else 0
                    if len(imgs) <= 1 and wrappers >= 5 and attempt < EXTRACT_RETRIES:
                        log_message(
                            f"[Capítulo {capitulo}] Poucas imagens extraídas ({len(imgs)}). Tentando carregar mais páginas...",
                            level='warning', job_id=job_id, chapter=capitulo, step='extract'
                        )
                        try:
                            _ = scroll_until_stable(page, container_sel, max_cycles=60, stable_cycles=3)
                        except Exception:
                            pass
                        time.sleep(1.2)
                        continue
                    break

                # Incluir info do scroll/extração para auditoria
                if isinstance(extract_info, dict):
                    extract_info = dict(extract_info)
                    extract_info['scroll'] = scroll_info
                    extract_info['attempts'] = attempt + 1
                # Criar pasta do capítulo SEMPRE (ok/partial/broken)
                pasta_cap = pasta_obra / f"cap_{capitulo:03d}"
                pasta_cap.mkdir(parents=True, exist_ok=True)

                found_n = len(imgs) if imgs else 0
                status = 'ok' if found_n >= MIN_IMAGES_PER_CHAPTER else ('partial' if found_n >= MIN_IMAGES_PARTIAL else 'broken')

                log_message(
                    f"[Capítulo {capitulo}] {found_n} imagens encontradas (status={status})",
                    job_id=job_id, chapter=capitulo
                )

                # Salvar meta do capítulo
                try:
                    meta = {
                        "chapter": capitulo,
                        "url": current_url,
                        "status": status,
                        "found_images": found_n,
                        "debug": extract_info,
                        "updated_at": datetime.now().isoformat()
                    }
                    with open(pasta_cap / "meta.json", "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

                # Se parece bloqueio (Cloudflare etc.), parar para continuação manual
                if status == 'broken' and is_probably_blocked(page):
                    log_message(
                        f"[Capítulo {capitulo}] Possível bloqueio detectado. Pausando para continuar manualmente.",
                        level='warning', job_id=job_id, chapter=capitulo
                    )
                    log_message(f"RESUME_URL: {current_url}", level='warning', job_id=job_id)
                    print(f"RESUME_URL: {current_url}")
                    stop_reason = 'blocked'
                    stop_url = current_url

                    # Registrar capítulo bloqueado e salvar progresso
                    capitulos_baixados.append({
                        'numero': capitulo,
                        'url': current_url,
                        'imagens': 0,
                        'status': 'blocked',
                        'data': datetime.now().isoformat()
                    })
                    visited_urls.add(current_url_norm)
                    save_progress(obra_nome, {
                        'ultimo_capitulo_url': current_url,
                        'capitulos_baixados': capitulos_baixados,
                        'ultima_atualizacao': datetime.now().isoformat(),
                        'expected_total': int(job.get('expected_total') or 0),
                        'batch_size': int(job.get('batch_size') or BATCH_SIZE_DEFAULT),
                        'stop_reason': stop_reason,
                        'stop_url': stop_url
                    })
                    break

                # IA: se muitos capítulos seguidos vierem broken, tentar ajustar profile
                if status == 'broken':
                    consecutive_broken += 1
                else:
                    consecutive_broken = 0

                if AI_ENABLED and consecutive_broken >= 3 and ai_calls < AI_MAX_CALLS_PER_JOB:
                    snapshot = {
                        "url": current_url,
                        "title": page.title(),
                        "found_images": found_n,
                        "container_selectors_current": container_sel,
                        "comment_selectors_current": comment_sel,
                        "html_sample": (page.content() or "")[:8000]
                    }
                    plan = ai_suggest_profile(snapshot, job_id=job_id)
                    ai_calls += 1
                    if plan and plan.get("container_selectors") and plan.get("comment_selectors"):
                        update_profile_for_url(current_url, {
                            "container_selectors": plan["container_selectors"],
                            "comment_selectors": plan["comment_selectors"],
                            "min_dim_px": int(plan.get("min_dim_px") or MIN_DIM_PX)
                        })
                        # Aplicar profile sugerido também na execução atual (sem reiniciar)
                        try:
                            container_sel = plan.get('container_selectors') or container_sel
                            comment_sel = plan.get('comment_selectors') or comment_sel
                            min_dim = int(plan.get('min_dim_px') or min_dim)
                        except Exception:
                            pass

                        log_message(f"IA: profile atualizado. Notes: {plan.get('notes','')}", level='info', job_id=job_id, step='ai')
                    consecutive_broken = 0

                imagens_baixadas_cap = 0
                if not imgs:
                    # Sem imagens: segue para o próximo sem baixar nada
                    pass
                else:
# Obter cookies do Playwright para usar com requests
                    cookies = context.cookies()
                    cookies_dict = {c['name']: c['value'] for c in cookies}
                    
                    # Headers para simular navegador
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
                        'Referer': current_url,
                    }
                    
                    # Baixar imagens
                    imagens_baixadas_cap = 0
                    for i, img_url in enumerate(imgs, 1):
                        # Verificar se deve parar
                        if not _should_continue():
                            log_message(
                                "Download interrompido pelo usuário",
                                level='warning', job_id=job_id
                            )
                            # Salvar progresso antes de sair
                            save_progress(obra_nome, {
                                'ultimo_capitulo_url': current_url,
                                'capitulos_baixados': capitulos_baixados,
                                'ultima_atualizacao': datetime.now().isoformat()
                            })
                            break
                        
                        try:
                            ext = img_url.split('.')[-1].split('?')[0] or 'jpg'
                            arquivo = pasta_cap / f"{i:03d}.{ext}"
                            
                            # Verificar se a imagem já existe
                            if arquivo.exists() and arquivo.stat().st_size > 0:
                                total_imagens += 1
                                imagens_baixadas_cap += 1
                            elif download_with_retry(img_url, arquivo, cookies_dict, headers):
                                total_imagens += 1
                                imagens_baixadas_cap += 1
                            else:
                                imagens_falhas += 1
                            
                            progress = int((i / len(imgs)) * 100)
                            update_status({'progress': progress, 'total_images': total_imagens})
                            
                        except Exception as e:
                            imagens_falhas += 1
                            log_message(
                                f"Erro na imagem {i}: {traceback.format_exc()}",
                                level='error', job_id=job_id, chapter=capitulo
                            )
                        
                        time.sleep(random.uniform(0.1, 0.3))
                    
                    log_message(
                        f"[Capítulo {capitulo}] {imagens_baixadas_cap}/{len(imgs)} imagens baixadas",
                        job_id=job_id, chapter=capitulo
                    )
                    
                    
                # Registrar capítulo como baixado (sempre)
                try:
                    capitulos_baixados.append({
                        'numero': capitulo,
                        'url': current_url,
                        'imagens': int(imagens_baixadas_cap or 0),
                        'status': status,
                        'data': datetime.now().isoformat()
                    })
                except Exception:
                    capitulos_baixados.append({
                        'numero': capitulo,
                        'url': current_url,
                        'imagens': 0,
                        'status': status,
                        'data': datetime.now().isoformat()
                    })

                visited_urls.add(current_url_norm)

                # Verificar se deve parar antes de ir para o próximo
                if not _should_continue():
                    break
                
                # Tentar ir para o próximo capítulo
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(1)
                
                try:
                    # Usar JavaScript para clicar no botão Próximo
                    has_next = page.evaluate('''
                        () => {
                            const btns = document.querySelectorAll('button');
                            for (const b of btns) {
                                if (b.textContent.includes('Próximo') && !b.disabled) {
                                    b.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    ''')
                    
                    if has_next:
                        log_message(
                            f"-> Indo para o capítulo {capitulo + 1}...",
                            job_id=job_id, chapter=capitulo
                        )

                        # Esperar a navegação acontecer (sem mudar a lógica do botão)
                        prev_url_norm = current_url_norm
                        time.sleep(random.uniform(3, 5))
                        new_url = page.url
                        new_url_norm = normalize_url(new_url)

                        if new_url_norm == prev_url_norm or new_url_norm in visited_urls:
                            log_message(
                                "Clique em 'Próximo' não avançou (URL repetida). Parando para evitar duplicação/loop.",
                                level='warning', job_id=job_id, chapter=capitulo
                            )
                            log_message(f"RESUME_URL: {new_url}", level='warning', job_id=job_id)
                            print(f"RESUME_URL: {new_url}")

                            stop_reason = 'next_nao_avancou'
                            stop_url = new_url

                            save_progress(obra_nome, {
                                'ultimo_capitulo_url': new_url,
                                'capitulos_baixados': capitulos_baixados,
                                'ultima_atualizacao': datetime.now().isoformat(),
                                'stopped_reason': 'next_nao_avancou',
                                'stopped_at_url': new_url
                            })
                            break

                        # Salvar progresso após cada capítulo
                        save_progress(obra_nome, {
                            'ultimo_capitulo_url': new_url,
                            'capitulos_baixados': capitulos_baixados,
                            'expected_total': int(job.get('expected_total') or 0),
                            'batch_size': int(job.get('batch_size') or BATCH_SIZE_DEFAULT),
                            'upload_enqueued': upload_enqueued,
                            'ultima_atualizacao': datetime.now().isoformat()
                        })

                        if not upload_enqueued and len(capitulos_baixados) >= 1:
                            if adicionar_fila_upload(obra_nome, job):
                                upload_enqueued = True
                                save_progress(obra_nome, {
                                    'ultimo_capitulo_url': new_url,
                                    'capitulos_baixados': capitulos_baixados,
                                    'expected_total': int(job.get('expected_total') or 0),
                                    'batch_size': int(job.get('batch_size') or BATCH_SIZE_DEFAULT),
                                    'upload_enqueued': upload_enqueued,
                                    'ultima_atualizacao': datetime.now().isoformat()
                                })

                        capitulo += 1

                        # Batch: reencolar automaticamente para reiniciar a cada N capítulos
                        batch_size = int(job.get('batch_size') or BATCH_SIZE_DEFAULT)
                        batch_counter += 1
                        if batch_size > 0 and batch_counter >= batch_size:
                            log_message(f"Batch atingido ({batch_counter}/{batch_size}). Vou parar e reiniciar automaticamente.", level='info', job_id=job_id, step='batch')
                            log_message(f"RESUME_URL: {new_url}", level='warning', job_id=job_id)
                            print(f"RESUME_URL: {new_url}")

                            next_job = {
                                'id': datetime.now().strftime('%Y%m%d%H%M%S') + '_cont',
                                'url': new_url,
                                'nome': obra_nome,
                                'expected_total': int(job.get('expected_total') or 0),
                                'batch_size': batch_size,
                                'force_url': True,
                                'created_at': datetime.now().isoformat(),
                                'auto_requeued_from': job_id
                            }
                            # Reenfileirar continuação no SQLite (worker vai continuar automaticamente)
                            next_job_id = f"{job_id}-cont-{int(time.time())}"
                            pasta = str((DOWNLOADS_DIR / obra_nome).resolve())
                            DOWNLOAD_STORE.enqueue(url=new_url, nome=obra_nome, pasta=pasta, expected_total=int(job.get('expected_total') or 0), batch_size=batch_size, job_id=next_job_id)
                            next_job['job_id'] = next_job_id
                            log_message(f"Continuação reenfileirada (SQLite): {obra_nome} (job {next_job_id})", level='info', job_id=next_job_id)
                            stop_reason = 'batch_requeued'
                            stop_url = new_url
                            return {'success': True, 'stopped': True, 'stop_reason': stop_reason, 'stop_url': stop_url, 'requeued_job': next_job}
                    else:
                        log_message("Fim da obra!", job_id=job_id)
                        # Limpar progresso quando a obra termina
                        clear_progress(obra_nome)
                        
                        # ========== BAIXAR CAPA DA OBRA ==========
                        obra_url = job.get('obra_url', '')
                        if obra_url:
                            try:
                                log_message(f"Baixando capa da obra...", job_id=job_id, step='capa')
                                page.goto(obra_url, wait_until='domcontentloaded', timeout=30000)
                                time.sleep(random.uniform(2, 4))
                                
                                # Procurar imagem da capa na página
                                capa_img = page.locator('img[src*="storage"], img[src*="capa"], img[src*="cover"], .cover img, .capa img, [class*="cover"] img, [class*="capa"] img').first
                                
                                if capa_img.is_visible():
                                    capa_src = capa_img.get_attribute('src')
                                    if capa_src:
                                        # Baixar a capa
                                        capa_path = pasta_obra / 'capa.jpg'
                                        try:
                                            capa_response = requests.get(capa_src, timeout=30)
                                            if capa_response.status_code == 200:
                                                with open(capa_path, 'wb') as f:
                                                    f.write(capa_response.content)
                                                log_message(f"Capa baixada: {capa_path}", job_id=job_id, step='capa')
                                            else:
                                                log_message(f"Erro ao baixar capa: HTTP {capa_response.status_code}", level='warning', job_id=job_id)
                                        except Exception as ce:
                                            log_message(f"Erro ao baixar capa: {ce}", level='warning', job_id=job_id)
                                else:
                                    log_message("Capa não encontrada na página", level='warning', job_id=job_id)
                            except Exception as e:
                                log_message(f"Erro ao buscar capa: {e}", level='warning', job_id=job_id)
                        # ========================================
                        
                        break
                        
                except Exception as e:
                    log_message(
                        f"Erro ao ir para próximo: {traceback.format_exc()}",
                        level='error', job_id=job_id
                    )
                    break
            
    except Exception as e:
        log_message(
            f"Erro crítico no bot: {traceback.format_exc()}",
            level='error', job_id=job_id
        )
        update_status({'state': 'error'})
        
        # Salvar progresso em caso de erro
        if capitulos_baixados:
            save_progress(obra_nome, {
                'ultimo_capitulo_url': url,
                'capitulos_baixados': capitulos_baixados,
                'ultima_atualizacao': datetime.now().isoformat()
            })
        
        return {'error': str(e)}
        
    finally:
        # Garantir que o browser seja fechado
        if browser:
            try:
                browser.close()
            except:
                pass
    
    # Calcular métricas
    elapsed_time = time.time() - start_time
    avg_time_per_image = elapsed_time / total_imagens if total_imagens > 0 else 0
    error_rate = imagens_falhas / (total_imagens + imagens_falhas) if (total_imagens + imagens_falhas) > 0 else 0
    
    result = {
        'capitulos': len(capitulos_baixados),
        'imagens': total_imagens,
        'imagens_falhas': imagens_falhas,
        'pasta': str(pasta_obra),
        'tempo_total': f"{elapsed_time:.1f}s",
        'tempo_por_imagem': f"{avg_time_per_image:.2f}s",
        'taxa_erro': f"{error_rate:.1%}"
    }

    if stop_reason:
        result['stopped_reason'] = stop_reason
        result['resume_url'] = stop_url

    if stop_reason:
        log_message(
            f"Download interrompido ({stop_reason}). Baixados {len(capitulos_baixados)} capítulos, {total_imagens} imagens. RESUME_URL: {stop_url}",
            level='warning', job_id=job_id
        )
    else:
        log_message(
            f"Download concluído! {len(capitulos_baixados)} capítulos, {total_imagens} imagens ({imagens_falhas} falhas)",
            job_id=job_id
        )
        
        # Adicionar obra na fila de upload
        adicionar_fila_upload(obra_nome, job)
    
    # Salvar relatório do job
    try:
        report_file = pasta_obra / 'summary.json'
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump({
                'job': job,
                'result': result,
                'capitulos_baixados': capitulos_baixados,
                'completed_at': datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_message(f"Erro ao salvar relatório: {e}", level='warning', job_id=job_id)
    
    return result

def download_worker():
    """Worker que processa a fila de downloads"""
    global current_download
    
    while True:
        try:
            job = download_queue.get(timeout=1)
            current_download = job
            
            update_status({
                'running': True,
                'current_job': job,
                'chapter': 0,
                'progress': 0,
                'total_images': 0,
                'state': 'starting'
            })
            
            result = run_download_bot(job)
            
            # Adicionar ao histórico
            with history_lock:
                job['result'] = result
                job['completed_at'] = datetime.now().isoformat()
                download_history.append(job)
            
            # Salvar histórico no disco
            save_history()
            
            update_status({
                'running': False,
                'current_job': None,
                'state': 'completed' if 'error' not in result else 'error'
            })
            
            current_download = None
            download_queue.task_done()
            
        except queue.Empty:
            continue
        except Exception as e:
            log_message(f"Erro no worker: {traceback.format_exc()}", level='error')
            update_status({
                'running': False,
                'current_job': None,
                'state': 'error'
            })
            current_download = None

# Worker agora roda em processo separado (modo worker).


# ============================================
# Socket.IO - Repassar eventos do worker
# ============================================

@socketio.on('log')
def _on_worker_log(data):
    # worker emite 'log' -> repassa para navegadores
    emit('log', data, broadcast=True, include_self=False)

@socketio.on('status_update')
def _on_worker_status(data):
    emit('status_update', data, broadcast=True, include_self=False)

# ============================================

@socketio.on('status')
def _on_worker_status(data):
    emit('status', data, broadcast=True, include_self=False)

# Rotas da API
# ============================================

@app.route('/')
def index():
    """Página principal da dashboard"""
    return render_template('index.html')

@app.route('/api/health')
def health():
    """Endpoint de saúde para healthcheck"""
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

@app.route('/api/download', methods=['POST'])
def start_download():
    """Inicia um novo download"""
    data = request.json
    url = data.get('url', '').strip()
    nome = data.get('nome', '').strip()
    expected_total = int(data.get('expected_total') or data.get('total_chapters') or 0)
    batch_size = int(data.get('batch_size') or BATCH_SIZE_DEFAULT)
    force_url = bool(data.get('force_url') or False)
    
    if not url:
        return jsonify({'error': 'URL é obrigatória'}), 400
    
    if not nome:
        # Extrair nome da URL se não fornecido
        nome = f"obra_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Sanitizar nome da pasta
    nome = "".join(c for c in nome if c.isalnum() or c in (' ', '-', '_')).strip()
    nome = nome.replace(' ', '_')
    
    job = {
        'id': datetime.now().strftime('%Y%m%d%H%M%S'),
        'url': url,
        'nome': nome,
        'expected_total': expected_total,
        'batch_size': batch_size,
        'force_url': force_url,
        'created_at': datetime.now().isoformat()
    }
    
    job_id = str(job.get('id') or datetime.now().strftime('%Y%m%d%H%M%S'))
    job['job_id'] = job_id
    pasta = str((DOWNLOADS_DIR / nome).resolve())
    DOWNLOAD_STORE.enqueue(url=url, nome=nome, pasta=pasta, expected_total=expected_total, batch_size=batch_size, job_id=job_id)
    log_message(f"Download enfileirado (SQLite): {nome}", job_id=job_id)
    
    return jsonify({'success': True, 'job': job})

@app.route('/api/import_catalogo', methods=['POST'])
def import_catalogo():
    """Importa obras do catalogo.json para a fila de downloads"""
    # Procurar na pasta raiz do bot (onde está o app.py)
    catalogo_path = Path('catalogo.json')
    
    if not catalogo_path.exists():
        # Tentar na pasta downloads como fallback
        catalogo_path = DOWNLOADS_DIR / 'catalogo.json'
    
    if not catalogo_path.exists():
        return jsonify({'error': 'catalogo.json não encontrado na pasta do bot'}), 404
    
    try:
        with open(catalogo_path, 'r', encoding='utf-8') as f:
            catalogo = json.load(f)
        
        obras = catalogo.get('obras', [])
        added = 0
        
        for obra in obras:
            titulo = obra.get('title', '')
            primeiro_cap_url = obra.get('primeiro_capitulo_url', '')
            total_caps = obra.get('total_capitulos', '0')
            
            # Converter total_capitulos para int
            try:
                total_caps = int(float(str(total_caps).replace(',', '.')))
            except:
                total_caps = 0
            
            if not primeiro_cap_url or not titulo:
                continue
            
            # Sanitizar nome da pasta
            nome = "".join(c for c in titulo if c.isalnum() or c in (' ', '-', '_')).strip()
            nome = nome.replace(' ', '_')
            
            # Pegar URL da obra para baixar a capa depois
            obra_url = obra.get('url', '')
            
            job = {
                'id': datetime.now().strftime('%Y%m%d%H%M%S') + str(added),
                'url': primeiro_cap_url,
                'nome': nome,
                'expected_total': total_caps,
                'batch_size': BATCH_SIZE_DEFAULT,
                'force_url': False,
                'created_at': datetime.now().isoformat(),
                'obra_url': obra_url  # URL da página da obra para baixar a capa
            }
            
            job_id = str(job.get('job_id') or job.get('id') or f"cat-{int(time.time())}-1732")
            job['job_id'] = job_id
            pasta = str((DOWNLOADS_DIR / nome).resolve())
            DOWNLOAD_STORE.enqueue(url=primeiro_cap_url, nome=nome, pasta=pasta, expected_total=total_caps, batch_size=BATCH_SIZE_DEFAULT, job_id=job_id)
            log_message(f"Obra importada (enfileirada): {titulo} ({total_caps} caps)", job_id=job_id)
            added += 1
        
        return jsonify({'success': True, 'imported': added, 'total': len(obras)})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/catalogo', methods=['GET'])
def get_catalogo():
    """Retorna as obras do catalogo.json"""
    catalogo_path = Path('catalogo.json')
    if not catalogo_path.exists():
        catalogo_path = DOWNLOADS_DIR / 'catalogo.json'
    
    if not catalogo_path.exists():
        return jsonify({'obras': [], 'error': 'catalogo.json não encontrado'})
    
    try:
        with open(catalogo_path, 'r', encoding='utf-8') as f:
            catalogo = json.load(f)
        return jsonify(catalogo)
    except Exception as e:
        return jsonify({'obras': [], 'error': str(e)})

@app.route('/api/stop', methods=['POST'])
def stop_download():
    """Solicita parada do download atual (worker observa via flag no SQLite)."""
    DOWNLOAD_STORE.set_flag('download_stop_requested', '1')
    log_message('Solicitação de parada recebida')
    return jsonify({'success': True})

@app.route('/api/resume', methods=['POST'])
def resume_download():
    """Remove a solicitação de parada (permite continuar)."""
    DOWNLOAD_STORE.set_flag('download_stop_requested', '0')
    log_message('Solicitação de parada limpa (resume)')
    return jsonify({'success': True})

@app.route('/api/status')
def get_status():
    """Retorna status atual (fonte de verdade: SQLite) - sem limitar a 300 jobs.
    Importante: antes o /api/status olhava só os 300 jobs mais recentes e isso fazia:
    - parecer que o catálogo importou só 300
    - parecer que o worker não estava baixando (quando ele pegava jobs mais antigos)
    """
    running_flag = DOWNLOAD_STORE.get_flag('download_running', '1')
    stop_req = DOWNLOAD_STORE.get_flag('download_stop_requested', '0')

    # Contadores globais (sem limite)
    try:
        queue_size = int(DOWNLOAD_STORE.count_status('queued'))
    except Exception:
        queue_size = 0
    try:
        queue_ready = int(DOWNLOAD_STORE.count_queued_ready())
    except Exception:
        queue_ready = queue_size
    try:
        total_jobs = int(DOWNLOAD_STORE.count_all())
    except Exception:
        total_jobs = queue_size

    # Job ativo real (sem depender do recorte de list_jobs)
    current_row = None
    try:
        current_row = DOWNLOAD_STORE.get_latest_active_job()
    except Exception:
        current_row = None

    if current_row:
        current_job = {
            'id': current_row.job_id,
            'job_id': current_row.job_id,
            'url': current_row.url,
            'nome': current_row.nome,
            'expected_total': current_row.expected_total,
            'batch_size': current_row.batch_size,
        }
        status = {
            'running': True,
            'current_job': current_job,
            'chapter': current_row.chapter,
            'progress': current_row.progress,
            'total_images': current_row.total_images,
            'state': current_row.state or ('running' if current_row.status == 'downloading' else 'validating'),
        }
    else:
        status = {
            'running': False,
            'current_job': None,
            'chapter': 0,
            'progress': 0,
            'total_images': 0,
            'state': 'idle',
        }

    return jsonify({
        'status': status,
        'queue_size': queue_size,
        'queue_ready': queue_ready,
        'total_jobs': total_jobs,
        'controls': {
            'download_running': str(running_flag) == '1',
            'stop_requested': str(stop_req) == '1',
        }
    })


@app.route('/api/progress/<obra_nome>')
def get_progress(obra_nome):
    """Retorna o progresso salvo de uma obra"""
    progress = load_progress(obra_nome)
    return jsonify(progress)

@app.route('/api/config', methods=['GET', 'POST'])
def config():
    """Gerencia configurações"""
    global EMAIL, SENHA
    
    if request.method == 'POST':
        data = request.json
        EMAIL = data.get('email', EMAIL)
        SENHA = data.get('senha', SENHA)
        return jsonify({'success': True})
    
    return jsonify({
        'email': EMAIL,
        'has_senha': bool(SENHA)
    })

@app.route('/api/screenshot')
def get_screenshot():
    """Retorna o screenshot ao vivo do navegador"""
    from flask import send_file
    import io
    
    global CURRENT_PAGE
    
    if CURRENT_PAGE:
        try:
            # Tirar screenshot da página atual
            screenshot_bytes = CURRENT_PAGE.screenshot(type='png')
            return send_file(
                io.BytesIO(screenshot_bytes),
                mimetype='image/png',
                as_attachment=False
            )
        except Exception as e:
            pass
    
    # Se não tem página ativa, retornar imagem placeholder
    if SCREENSHOT_FILE.exists():
        return send_file(str(SCREENSHOT_FILE), mimetype='image/png')
    
    # Retornar imagem vazia/placeholder
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
    """Cliente conectou"""
    with status_lock:
        emit('status', bot_status)
        emit('logs', bot_status['logs'])

@socketio.on('disconnect')
def handle_disconnect():
    """Cliente desconectou"""
    pass

# ============================================
# Main
# ============================================



# ============================================
# Worker (modo separado)
# ============================================

def _backoff_seconds(tries: int) -> int:
    # 1m, 2m, 4m, 8m ... até 1h
    base = 60
    s = base * (2 ** max(0, int(tries)))
    return int(min(s, 3600))

def _count_images(path: Path) -> int:
    exts = {'.jpg','.jpeg','.png','.webp','.gif'}
    total = 0
    for p in path.rglob('*'):
        if p.is_file() and p.suffix.lower() in exts:
            total += 1
    return total

def _validate_and_write_summary(job: dict, pasta_obra: Path) -> dict:
    expected = int(job.get('expected_total') or 0)
    found = _count_images(pasta_obra)
    missing = max(0, expected - found) if expected > 0 else 0
    summary = {
        'job_id': job.get('job_id') or job.get('id'),
        'obra_nome': job.get('nome'),
        'pasta': str(pasta_obra.resolve()),
        'expected_total': expected,
        'images_found': found,
        'missing': missing,
        'ok': (missing == 0) if expected > 0 else True,
        'validated_at': time.time(),
    }
    try:
        with open(pasta_obra / 'summary_validation.json', 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return summary

def download_worker_loop():
    """Consome download_jobs do SQLite e executa run_download_bot(job)."""
    os.environ['DOWNLOAD_RUN_MODE'] = 'worker'
    global RUN_MODE
    RUN_MODE = 'worker'

    worker_id = os.environ.get('DOWNLOAD_WORKER_ID') or f"download-worker-{uuid.uuid4()}"
    api_url = os.environ.get('DOWNLOAD_SOCKET_URL', 'http://127.0.0.1:5000')

    def emit_log(message: str, level: str = 'info', job_id: str = None):
        data = {'message': message, 'level': level, 'timestamp': datetime.now().isoformat()}
        try:
            print(f"[{data['timestamp']}] [{level.upper()}] {message}", flush=True)
        except Exception:
            pass
        if job_id:
            data['job_id'] = job_id
        if WORKER_SIO:
            try:
                WORKER_SIO.emit('log', data)
            except Exception:
                pass

    def emit_status(payload: dict):
        if WORKER_SIO:
            try:
                WORKER_SIO.emit('status', payload)
            except Exception:
                pass



    emit_log(f"Worker iniciado: {worker_id} (API: {api_url})", level='info')

    # Cliente Socket.IO para enviar logs/status ao dashboard
    try:
        import socketio as _sio
        global WORKER_SIO
        WORKER_SIO = _sio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)
        WORKER_SIO.connect(api_url, wait_timeout=10)
        emit_log("Socket.IO conectado ao dashboard", level='info')
    except Exception:
        WORKER_SIO = None
        emit_log("Socket.IO indisponível (seguindo sem realtime)", level='warning')

    # Garantir flags padrão
    try:
        if DOWNLOAD_STORE.get_flag('download_running', '') == '':
            DOWNLOAD_STORE.set_flag('download_running', '1')
    except Exception:
        pass

    last_reclaim = 0
    while True:
        try:
            # recolher órfãos
            now = time.time()
            if now - last_reclaim > 60:
                reclaimed = DOWNLOAD_STORE.reclaim_stale_downloading(timeout_seconds=600)
                if reclaimed:
                    emit_log(f"Reclaimed {reclaimed} job(s) stale in downloading/validating", level='warning')
                last_reclaim = now

            if DOWNLOAD_STORE.get_flag('download_running', '1') != '1':
                time.sleep(1)
                continue

            job_row = DOWNLOAD_STORE.claim_next(worker_id=worker_id)
            if not job_row:
                time.sleep(1)
                continue

            # limpar stop request ao iniciar um novo job
            DOWNLOAD_STORE.set_flag('download_stop_requested', '0')

            job = {
                'id': job_row.job_id,
                'job_id': job_row.job_id,
                'url': job_row.url,
                'nome': job_row.nome,
                'expected_total': job_row.expected_total,
                'batch_size': job_row.batch_size or BATCH_SIZE_DEFAULT,
                'force_url': False,
                'created_at': datetime.fromtimestamp(job_row.created_at).isoformat(),
            }

            emit_log(f"Iniciando download: {job['nome']}", level='success', job_id=job['job_id'])
            emit_status({'running': True, 'current_job': job, 'state': 'starting'})

            # Hook: update_status/log_message do core já atualiza UI via socketio server;
            # no modo worker, a UI é atualizada via emit_log/emit_status + /api/status lendo SQLite.
            # Vamos rodar o download:
            DOWNLOAD_STORE.set_status(job['job_id'], 'downloading', state='running')
            result = run_download_bot(job)

            # Validação explícita
            pasta_obra = (DOWNLOADS_DIR / job['nome'])
            DOWNLOAD_STORE.set_status(job['job_id'], 'validating', state='validating')
            summary = _validate_and_write_summary(job, pasta_obra)

            # Concluir e persistir
            DOWNLOAD_STORE.mark_done(job['job_id'], result=result, summary=summary)
            emit_log(f"Download concluído: {job['nome']}", level='success', job_id=job['job_id'])
            emit_status({'running': False, 'current_job': None, 'state': 'completed'})

        except KeyboardInterrupt:
            emit_log("Worker interrompido (Ctrl+C). Saindo...", level='warning')
            break
        except Exception as e:
            # Se falhou com job atual, aplicar backoff
            try:
                jid = job.get('job_id') if 'job' in locals() else None
            except Exception:
                jid = None
            if jid:
                j = DOWNLOAD_STORE.get_job_by_job_id(jid)
                tries = int(j.tries) if j else 0
                next_at = int(time.time() + _backoff_seconds(tries))
                if tries >= 5:
                    DOWNLOAD_STORE.fail_permanently(jid, str(e))
                    emit_log(f"Falha permanente no job {jid}: {e}", level='error', job_id=jid)
                else:
                    DOWNLOAD_STORE.mark_failed(jid, str(e), next_available_at=next_at)
                    emit_log(f"Erro no download (retry com backoff): {e}", level='error', job_id=jid)
            else:
                emit_log(f"Erro no worker: {e}", level='error')
            time.sleep(1)

    try:
        if WORKER_SIO:
            WORKER_SIO.disconnect()
    except Exception:
        pass

if __name__ == '__main__':
    import sys
    mode = 'api'
    if len(sys.argv) > 1:
        mode = sys.argv[1].strip().lower()
    if mode == 'worker':
        download_worker_loop()
    else:
        # modo API (dashboard)
        os.environ['DOWNLOAD_RUN_MODE'] = 'api'
        RUN_MODE = 'api'
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
