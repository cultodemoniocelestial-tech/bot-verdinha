"""
Culto Uploader - Bot de Upload Automático
Faz upload de capítulos baixados para o site culto-demoniaco.online
Com validação de pastas vazias e imagens quebradas
"""

import os
import time
import json
import struct
import imghdr
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ============================================
# Configurações
# ============================================

SITE_URL = "https://culto-demoniaco.online"
EMAIL = "culto.demonio.celestial@gmail.com"
PASSWORD = "55436231"

# Diretório de downloads do bot original
DOWNLOADS_DIR = Path(os.environ.get('DOWNLOADS_DIR', './downloads'))

# Configurações de validação
MIN_IMAGE_SIZE_BYTES = 1024  # Imagens menores que 1KB são consideradas quebradas
MIN_IMAGE_DIMENSION = 100    # Dimensão mínima em pixels
MIN_IMAGES_PER_CHAPTER = 1   # Mínimo de imagens válidas para considerar o capítulo

# Extensões de imagem suportadas
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

# Arquivo de controle de uploads
UPLOAD_CONTROL_FILE = DOWNLOADS_DIR / '_upload_control.json'

# ============================================
# Funções de Validação de Imagens
# ============================================

def get_image_size(filepath):
    """Retorna as dimensões de uma imagem (width, height) ou None se inválida"""
    try:
        with open(filepath, 'rb') as f:
            head = f.read(32)
            
            # PNG
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                if head[12:16] == b'IHDR':
                    width = struct.unpack('>I', head[16:20])[0]
                    height = struct.unpack('>I', head[20:24])[0]
                    return width, height
                    
            # JPEG
            elif head[:2] == b'\xff\xd8':
                f.seek(0)
                f.read(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2:
                        break
                    if marker[0] != 0xff:
                        break
                    if marker[1] == 0xc0 or marker[1] == 0xc2:  # SOF0 or SOF2
                        f.read(3)
                        height = struct.unpack('>H', f.read(2))[0]
                        width = struct.unpack('>H', f.read(2))[0]
                        return width, height
                    else:
                        length = struct.unpack('>H', f.read(2))[0]
                        f.read(length - 2)
                        
            # GIF
            elif head[:6] in (b'GIF87a', b'GIF89a'):
                width = struct.unpack('<H', head[6:8])[0]
                height = struct.unpack('<H', head[8:10])[0]
                return width, height
                
            # WebP
            elif head[:4] == b'RIFF' and head[8:12] == b'WEBP':
                f.seek(0)
                data = f.read(30)
                if data[12:16] == b'VP8 ':
                    width = struct.unpack('<H', data[26:28])[0] & 0x3fff
                    height = struct.unpack('<H', data[28:30])[0] & 0x3fff
                    return width, height
                elif data[12:16] == b'VP8L':
                    bits = struct.unpack('<I', data[21:25])[0]
                    width = (bits & 0x3fff) + 1
                    height = ((bits >> 14) & 0x3fff) + 1
                    return width, height
                    
    except Exception:
        pass
    return None


def validate_image(filepath):
    """
    Valida uma imagem verificando:
    - Se o arquivo existe
    - Se tem tamanho mínimo
    - Se tem dimensões mínimas
    
    Retorna: (is_valid, reason)
    """
    filepath = Path(filepath)
    
    if not filepath.exists():
        return False, "Arquivo não existe"
    
    # Verificar tamanho do arquivo
    file_size = filepath.stat().st_size
    if file_size < MIN_IMAGE_SIZE_BYTES:
        return False, f"Arquivo muito pequeno ({file_size} bytes)"
    
    # Verificar dimensões
    dimensions = get_image_size(filepath)
    if dimensions is None:
        return False, "Não foi possível ler dimensões (arquivo corrompido?)"
    
    width, height = dimensions
    if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
        return False, f"Dimensões muito pequenas ({width}x{height})"
    
    return True, "OK"


def validate_chapter(chapter_path):
    """
    Valida um capítulo verificando se tem imagens válidas suficientes.
    
    Retorna: (is_valid, valid_images, invalid_images, reason)
    """
    chapter_path = Path(chapter_path)
    
    if not chapter_path.exists():
        return False, [], [], "Pasta não existe"
    
    if not chapter_path.is_dir():
        return False, [], [], "Não é uma pasta"
    
    # Buscar todas as imagens na pasta
    all_images = []
    for ext in IMAGE_EXTENSIONS:
        all_images.extend(chapter_path.glob(f'*{ext}'))
        all_images.extend(chapter_path.glob(f'*{ext.upper()}'))
    
    if not all_images:
        return False, [], [], "Pasta vazia - nenhuma imagem encontrada"
    
    # Validar cada imagem
    valid_images = []
    invalid_images = []
    
    for img_path in all_images:
        is_valid, reason = validate_image(img_path)
        if is_valid:
            valid_images.append(str(img_path))
        else:
            invalid_images.append((str(img_path), reason))
    
    # Verificar se tem imagens válidas suficientes
    if len(valid_images) < MIN_IMAGES_PER_CHAPTER:
        return False, valid_images, invalid_images, f"Imagens válidas insuficientes ({len(valid_images)}/{MIN_IMAGES_PER_CHAPTER})"
    
    return True, valid_images, invalid_images, f"OK - {len(valid_images)} imagens válidas"


# ============================================
# Controle de Uploads
# ============================================

def load_upload_control():
    """Carrega o controle de uploads do disco"""
    try:
        if UPLOAD_CONTROL_FILE.exists():
            with open(UPLOAD_CONTROL_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Erro ao carregar controle de uploads: {e}")
    return {'uploaded_chapters': {}, 'skipped_chapters': {}}


def save_upload_control(control):
    """Salva o controle de uploads no disco"""
    try:
        with open(UPLOAD_CONTROL_FILE, 'w', encoding='utf-8') as f:
            json.dump(control, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao salvar controle de uploads: {e}")


def mark_chapter_uploaded(control, manga_name, chapter_name):
    """Marca um capítulo como enviado"""
    if manga_name not in control['uploaded_chapters']:
        control['uploaded_chapters'][manga_name] = {}
    control['uploaded_chapters'][manga_name][chapter_name] = datetime.now().isoformat()
    save_upload_control(control)


def mark_chapter_skipped(control, manga_name, chapter_name, reason):
    """Marca um capítulo como pulado (pasta vazia ou imagens quebradas)"""
    if manga_name not in control['skipped_chapters']:
        control['skipped_chapters'][manga_name] = {}
    control['skipped_chapters'][manga_name][chapter_name] = {
        'reason': reason,
        'timestamp': datetime.now().isoformat()
    }
    save_upload_control(control)


def is_chapter_processed(control, manga_name, chapter_name):
    """Verifica se um capítulo já foi processado (enviado ou pulado)"""
    uploaded = control.get('uploaded_chapters', {}).get(manga_name, {}).get(chapter_name)
    skipped = control.get('skipped_chapters', {}).get(manga_name, {}).get(chapter_name)
    return uploaded is not None or skipped is not None


# ============================================
# Classe Principal do Uploader
# ============================================

class CultoUploader:
    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.logged_in = False
        self.upload_control = load_upload_control()
        
    def start(self):
        """Inicia o navegador"""
        print("Iniciando navegador...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        self.page = self.context.new_page()
        print("Navegador iniciado!")
        
    def login(self):
        """Faz login no site"""
        print(f"Fazendo login em {SITE_URL}...")
        
        try:
            self.page.goto(f"{SITE_URL}/login", wait_until='networkidle')
            time.sleep(2)
            
            # Preencher email
            email_input = self.page.locator('input#email')
            email_input.fill(EMAIL)
            
            # Preencher senha
            password_input = self.page.locator('input#password')
            password_input.fill(PASSWORD)
            
            # Clicar em Entrar
            self.page.click('button:has-text("Entrar")')
            
            # Esperar redirecionamento
            time.sleep(3)
            
            # Verificar se logou (procurar menu de usuário)
            try:
                self.page.wait_for_selector('button:has-text("Demônio")', timeout=10000)
                print("Login realizado com sucesso!")
                self.logged_in = True
                return True
            except:
                print("Erro: Não foi possível confirmar o login")
                return False
                
        except Exception as e:
            print(f"Erro no login: {e}")
            return False
    
    def get_mangas_from_site(self):
        """Obtém a lista de mangás cadastrados no site"""
        print("Buscando obras cadastradas no site...")
        
        try:
            self.page.goto(f"{SITE_URL}/admin", wait_until='networkidle')
            time.sleep(2)
            
            mangas = {}
            
            # Buscar todas as linhas de obras na tabela
            rows = self.page.locator('div.bg-card, tr').all()
            
            # Buscar links de capítulos
            chapter_links = self.page.locator('a[href*="/chapters"]').all()
            
            for link in chapter_links:
                href = link.get_attribute('href')
                if href:
                    # Extrair o ID do mangá da URL
                    # /admin/manga/{id}/chapters
                    parts = href.split('/')
                    if 'manga' in parts and 'chapters' in parts:
                        manga_idx = parts.index('manga')
                        if manga_idx + 1 < len(parts):
                            manga_id = parts[manga_idx + 1]
                            
                            # Tentar pegar o nome do mangá
                            try:
                                parent = link.locator('xpath=ancestor::tr | ancestor::div[contains(@class, "flex")]').first
                                text = parent.inner_text()
                                name = text.split('\n')[0].split('\t')[0].strip()
                                if name and name not in ['Obra', 'Ações']:
                                    mangas[name] = {
                                        'id': manga_id,
                                        'chapters_url': f"{SITE_URL}{href}"
                                    }
                            except:
                                pass
            
            print(f"Encontradas {len(mangas)} obras no site")
            return mangas
            
        except Exception as e:
            print(f"Erro ao buscar mangás: {e}")
            return {}
    
    def create_manga(self, manga_name, sinopse=None, capa_path=None):
        """Cria uma nova obra no site com capa e sinopse do mapeamento"""
        print(f"Criando nova obra: {manga_name}")
        
        try:
            self.page.goto(f"{SITE_URL}/admin", wait_until='networkidle')
            time.sleep(2)
            
            # Clicar em Nova Obra
            self.page.click('a:has-text("Nova Obra"), button:has-text("Nova Obra")')
            time.sleep(2)
            
            # Preencher título
            self.page.fill('input#title, input[placeholder*="título"]', manga_name)
            
            # Preencher sinopse (do mapeamento ou padrão)
            descricao = sinopse if sinopse else f"Sinopse de {manga_name}"
            self.page.fill('textarea#description, textarea[placeholder*="sinopse"], textarea[placeholder*="história"]', 
                          descricao)
            
            # Upload da capa (se tiver)
            if capa_path and Path(capa_path).exists():
                try:
                    file_input = self.page.locator('input[type="file"]')
                    file_input.set_input_files(str(capa_path))
                    print(f"Capa carregada: {capa_path}")
                    time.sleep(2)
                except Exception as e:
                    print(f"Aviso: Não foi possível carregar a capa: {e}")
            
            # Selecionar status "Em Andamento" se disponível
            try:
                self.page.select_option('select', label='Em Andamento')
            except:
                pass
            
            # Clicar em criar
            self.page.click('button:has-text("Criar Obra"), button:has-text("Salvar")')
            time.sleep(3)
            
            # Buscar a obra criada
            mangas = self.get_mangas_from_site()
            
            # Normalizar nome para comparação
            def normalize(s):
                return "".join(s.lower().split()).replace('_', '')
            
            norm_name = normalize(manga_name)
            for name, data in mangas.items():
                if normalize(name) == norm_name or norm_name in normalize(name):
                    print(f"Obra criada com sucesso: {name}")
                    return data
            
            print(f"Aviso: Obra criada mas não encontrada na lista")
            return None
            
        except Exception as e:
            print(f"Erro ao criar obra: {e}")
            return None
    
    def upload_chapter(self, manga_data, chapter_path, chapter_num, images):
        """Faz upload de um capítulo"""
        print(f"Enviando capítulo {chapter_num} ({len(images)} imagens)...")
        
        try:
            # Navegar para a página de capítulos
            self.page.goto(manga_data['chapters_url'], wait_until='networkidle')
            time.sleep(2)
            
            # Clicar em Novo Capítulo
            self.page.click('button:has-text("Novo Capítulo")')
            time.sleep(2)
            
            # Preencher número do capítulo
            chapter_input = self.page.locator('input#chapter-number')
            chapter_input.fill('')
            chapter_input.fill(str(chapter_num))
            
            # Selecionar status "Publicado"
            try:
                self.page.select_option('select', label='Publicado')
            except:
                try:
                    self.page.select_option('select', index=1)  # Geralmente Publicado é a segunda opção
                except:
                    pass
            
            # Preencher título (opcional)
            try:
                title_input = self.page.locator('input#chapter-title')
                title_input.fill(str(chapter_num))
            except:
                pass
            
            # Upload de imagens
            # Ordenar imagens por nome
            sorted_images = sorted(images, key=lambda x: Path(x).name)
            
            # Encontrar o input de arquivo
            file_input = self.page.locator('input[type="file"]')
            file_input.set_input_files(sorted_images)
            
            print(f"Imagens selecionadas: {len(sorted_images)}")
            
            # Aguardar preview das imagens (se houver)
            time.sleep(2)
            
            # Clicar em Criar Capítulo
            self.page.click('button:has-text("Criar Capítulo")')
            
            # Aguardar conclusão
            time.sleep(5)
            
            # Verificar se deu certo (modal fechou ou mensagem de sucesso)
            try:
                # Verificar se o modal fechou
                modal_visible = self.page.locator('button:has-text("Criar Capítulo")').is_visible()
                if not modal_visible:
                    print(f"Capítulo {chapter_num} enviado com sucesso!")
                    return True
            except:
                pass
            
            # Verificar mensagem de sucesso
            try:
                self.page.wait_for_selector('text=sucesso', timeout=5000)
                print(f"Capítulo {chapter_num} enviado com sucesso!")
                return True
            except:
                pass
            
            print(f"Capítulo {chapter_num} possivelmente enviado (não foi possível confirmar)")
            return True
            
        except Exception as e:
            print(f"Erro ao enviar capítulo {chapter_num}: {e}")
            return False
    
    def find_manga_match(self, manga_name, site_mangas):
        """Encontra correspondência de mangá no site"""
        def normalize(s):
            return "".join(s.lower().split()).replace('_', '').replace('-', '')
        
        norm_name = normalize(manga_name)
        
        for site_name, data in site_mangas.items():
            if normalize(site_name) == norm_name:
                return data
            if norm_name in normalize(site_name) or normalize(site_name) in norm_name:
                return data
        
        return None
    
    def process_downloads(self):
        """Processa todas as pastas de downloads"""
        if not self.logged_in:
            if not self.login():
                print("Erro: Não foi possível fazer login")
                return
        
        # Obter mangás do site
        site_mangas = self.get_mangas_from_site()
        print(f"Obras no site: {list(site_mangas.keys())}")
        
        # Estatísticas
        stats = {
            'total_chapters': 0,
            'uploaded': 0,
            'skipped_empty': 0,
            'skipped_invalid': 0,
            'skipped_already': 0,
            'errors': 0
        }
        
        # Percorrer pastas de downloads
        for manga_folder in DOWNLOADS_DIR.iterdir():
            if not manga_folder.is_dir():
                continue
            if manga_folder.name.startswith('.') or manga_folder.name.startswith('_'):
                continue
            
            manga_name = manga_folder.name.replace('_', ' ')
            print(f"\n{'='*50}")
            print(f"Processando: {manga_name}")
            print(f"{'='*50}")
            
            # Encontrar ou criar mangá no site
            manga_data = self.find_manga_match(manga_name, site_mangas)
            
            if not manga_data:
                print(f"Obra não encontrada no site. Criando...")
                
                # Buscar sinopse e capa do mapeamento
                sinopse = None
                capa_path = None
                
                # Tentar carregar do mapeamento.json
                mapeamento_file = Path('./mapeamento.json')
                obras_file = Path('./obras_mapeadas.json')
                
                # Normalizar nome para busca
                def normalize_name(s):
                    return "".join(s.lower().split()).replace('_', '').replace('-', '')
                
                norm_manga = normalize_name(manga_name)
                
                # Buscar no mapeamento.json
                if mapeamento_file.exists():
                    try:
                        with open(mapeamento_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            if normalize_name(data.get('nome', '')) == norm_manga:
                                sinopse = data.get('sinopse')
                                capa_path = data.get('capa_local')
                    except:
                        pass
                
                # Buscar no obras_mapeadas.json
                if not sinopse and obras_file.exists():
                    try:
                        with open(obras_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            obras = data.get('obras', []) if isinstance(data, dict) else data
                            for obra in obras:
                                if normalize_name(obra.get('nome', '')) == norm_manga:
                                    sinopse = obra.get('sinopse')
                                    capa_path = obra.get('capa_local')
                                    break
                    except:
                        pass
                
                # Verificar se existe capa na pasta da obra
                if not capa_path:
                    for ext in ['.jpg', '.png', '.webp']:
                        capa_local = manga_folder / f'capa{ext}'
                        if capa_local.exists():
                            capa_path = str(capa_local)
                            break
                
                manga_data = self.create_manga(manga_name, sinopse, capa_path)
                if not manga_data:
                    print(f"Erro: Não foi possível criar a obra '{manga_name}'")
                    continue
                # Atualizar lista de mangás
                site_mangas = self.get_mangas_from_site()
                manga_data = self.find_manga_match(manga_name, site_mangas)
            
            # Buscar capítulos
            chapters = []
            for item in manga_folder.iterdir():
                if item.is_dir() and item.name.startswith('cap_'):
                    try:
                        cap_num = int(item.name.replace('cap_', ''))
                        chapters.append((cap_num, item))
                    except ValueError:
                        chapters.append((item.name, item))
            
            # Ordenar capítulos
            chapters.sort(key=lambda x: x[0] if isinstance(x[0], int) else 0)
            
            print(f"Encontrados {len(chapters)} capítulos")
            
            for chapter_num, chapter_path in chapters:
                stats['total_chapters'] += 1
                chapter_name = chapter_path.name
                
                # Verificar se já foi processado
                if is_chapter_processed(self.upload_control, manga_folder.name, chapter_name):
                    print(f"  [{chapter_name}] Já processado anteriormente - pulando")
                    stats['skipped_already'] += 1
                    continue
                
                # Validar capítulo
                is_valid, valid_images, invalid_images, reason = validate_chapter(chapter_path)
                
                if not is_valid:
                    print(f"  [{chapter_name}] PULANDO - {reason}")
                    if invalid_images:
                        print(f"    Imagens inválidas: {len(invalid_images)}")
                        for img, img_reason in invalid_images[:3]:  # Mostrar até 3
                            print(f"      - {Path(img).name}: {img_reason}")
                    
                    mark_chapter_skipped(self.upload_control, manga_folder.name, chapter_name, reason)
                    
                    if 'vazia' in reason.lower():
                        stats['skipped_empty'] += 1
                    else:
                        stats['skipped_invalid'] += 1
                    continue
                
                # Fazer upload
                print(f"  [{chapter_name}] Válido - {len(valid_images)} imagens")
                
                success = self.upload_chapter(manga_data, chapter_path, chapter_num, valid_images)
                
                if success:
                    mark_chapter_uploaded(self.upload_control, manga_folder.name, chapter_name)
                    stats['uploaded'] += 1
                else:
                    stats['errors'] += 1
                
                # Pequena pausa entre uploads
                time.sleep(2)
        
        # Mostrar estatísticas
        print(f"\n{'='*50}")
        print("ESTATÍSTICAS FINAIS")
        print(f"{'='*50}")
        print(f"Total de capítulos: {stats['total_chapters']}")
        print(f"Enviados com sucesso: {stats['uploaded']}")
        print(f"Pulados (pasta vazia): {stats['skipped_empty']}")
        print(f"Pulados (imagens inválidas): {stats['skipped_invalid']}")
        print(f"Pulados (já processados): {stats['skipped_already']}")
        print(f"Erros: {stats['errors']}")
    
    def close(self):
        """Fecha o navegador"""
        print("Fechando navegador...")
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        print("Navegador fechado!")


# ============================================
# Execução Principal
# ============================================

def main():
    print("""
╔═══════════════════════════════════════════════════════════╗
║           CULTO UPLOADER - Bot de Upload Automático       ║
║                                                           ║
║  Funcionalidades:                                         ║
║  - Login automático no site                               ║
║  - Validação de capítulos (pula pastas vazias)            ║
║  - Upload automático de imagens                           ║
║  - Controle de capítulos já enviados                      ║
╚═══════════════════════════════════════════════════════════╝
    """)
    
    uploader = CultoUploader(headless=True)
    
    try:
        uploader.start()
        uploader.process_downloads()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário")
    except Exception as e:
        print(f"\nErro fatal: {e}")
        import traceback
        traceback.print_exc()
    finally:
        uploader.close()


if __name__ == "__main__":
    main()
