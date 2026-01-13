
import os
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

# Configurações
BASE_URL = "https://culto-demoniaco.online"
ADMIN_EMAIL = "culto.demonio.celestial@gmail.com"
ADMIN_PASS = "55436231"
CATALOGO_PATH = "catalogo.json"
TAGS_PATH = "tags_unicas.json"
DOWNLOADS_DIR = Path("./downloads")

def run_automation():
    if not os.path.exists(CATALOGO_PATH):
        print(f"Erro: {CATALOGO_PATH} não encontrado!")
        return

    with open(CATALOGO_PATH, 'r', encoding='utf-8') as f:
        catalogo = json.load(f)
    
    tags_oficiais = []
    if os.path.exists(TAGS_PATH):
        with open(TAGS_PATH, 'r', encoding='utf-8') as f:
            tags_oficiais = json.load(f)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # Login
        print("Realizando login...")
        page.goto(f"{BASE_URL}/login")
        page.fill("#email", ADMIN_EMAIL)
        page.fill("#password", ADMIN_PASS)
        page.click("button:has-text('Entrar')")
        page.wait_for_url(BASE_URL + "/")
        print("Login realizado!")

        for obra in catalogo.get('obras', []):
            titulo = obra.get('title')
            sinopse = obra.get('sinopse')
            tags = obra.get('tags', [])
            
            print(f"\n>>> Processando obra: {titulo}")

            # Sanitizar nome da pasta (igual ao bot de download)
            nome_pasta = "".join(c for c in titulo if c.isalnum() or c in (' ', '-', '_')).strip()
            nome_pasta = nome_pasta.replace(' ', '_')
            
            # Procurar pasta da obra
            obra_folder = DOWNLOADS_DIR / nome_pasta
            if not obra_folder.exists():
                obra_folder = DOWNLOADS_DIR / titulo
            if not obra_folder.exists():
                obra_folder = DOWNLOADS_DIR / titulo.replace(" ", "_")
            
            if not obra_folder.exists():
                print(f"Pasta de downloads não encontrada para {titulo}. Pulando...")
                continue

            # Verificar se tem a capa local (baixada pelo bot de download)
            capa_local = obra_folder / 'capa.jpg'
            if not capa_local.exists():
                # Tentar outras extensões
                for ext in ['.png', '.webp', '.jpeg']:
                    capa_local = obra_folder / f'capa{ext}'
                    if capa_local.exists():
                        break
                else:
                    capa_local = None

            # Ir para o Painel Admin
            page.goto(f"{BASE_URL}/admin")
            
            # Verificar se a obra já existe
            page.fill("input[placeholder='Buscar obras...']", titulo)
            time.sleep(2)
            
            obra_row = page.locator("tr", has_text=titulo).first
            
            if obra_row.is_visible():
                print(f"Obra '{titulo}' já existe. Indo para capítulos...")
                obra_row.locator("button[hint='Gerenciar capítulos']").click()
            else:
                print(f"Obra '{titulo}' não encontrada. Criando nova...")
                page.goto(f"{BASE_URL}/admin/manga/new")
                
                # Upload da Capa LOCAL (baixada pelo bot de download)
                if capa_local and capa_local.exists():
                    print(f"Usando capa local: {capa_local}")
                    page.set_input_files("input[type='file']", str(capa_local))
                    time.sleep(2)
                else:
                    print(f"Capa não encontrada para {titulo}")

                # Preencher Título e Descrição
                page.fill("#title", titulo)
                page.fill("#description", sinopse or "")

                # Marcar como +18 se tiver a tag HENTAI
                is_adult = any(t.upper() == "HENTAI" for t in tags)
                if is_adult:
                    print(f"Obra '{titulo}' identificada como +18 (Tag HENTAI encontrada).")
                    try:
                        # Clicar no switch de conteúdo adulto
                        page.click("button[role='switch']:has-text('Conteúdo Adulto'), button[role='switch']:has-text('18+')")
                    except:
                        pass
                
                # Adicionar Tags/Gêneros
                for tag in tags:
                    try:
                        page.click("button[role='combobox']:has-text('Selecione um gênero')")
                        page.click(f"div[role='option']:has-text('{tag}')", timeout=2000)
                        page.click("button:has-text('Adicionar')")
                        print(f"Tag '{tag}' adicionada.")
                    except:
                        print(f"Tag '{tag}' não encontrada no site. Pulando...")

                # Criar Obra
                page.click("button:has-text('Criar Obra')")
                page.wait_for_url("**/chapters")
                print(f"Obra '{titulo}' criada com sucesso!")

            # --- Parte de Upload de Capítulos ---
            print(f"Pasta de downloads: {obra_folder}")
            
            # Listar capítulos locais (só pastas cap_XXX)
            cap_folders = sorted([d for d in obra_folder.iterdir() if d.is_dir() and d.name.startswith('cap_')])
            
            for cap_folder in cap_folders:
                cap_name = cap_folder.name.replace("cap_", "")
                
                # Verificar se o capítulo já existe no site
                if page.locator(f"tr:has-text('#{cap_name}')").is_visible():
                    print(f"Capítulo {cap_name} já existe no site. Pulando...")
                    continue
                
                print(f"Enviando capítulo {cap_name}...")
                page.click("button:has-text('Novo Capítulo')")
                
                # Preencher Número e Título (repetindo o número como solicitado)
                page.fill("#chapter-number", cap_name)
                page.fill("#chapter-title", cap_name)
                
                # Selecionar Status Publicado
                page.select_option("select", label="Publicado")
                
                # Upload das imagens (excluindo meta.json)
                images = sorted([str(img) for img in cap_folder.iterdir() if img.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']])
                if images:
                    page.set_input_files("button:has-text('Escolher arquivos') + input", images)
                    time.sleep(2)
                    page.click("button:has-text('Criar Capítulo')")
                    print(f"Capítulo {cap_name} enviado!")
                    time.sleep(3) # Esperar processamento
                else:
                    print(f"Capítulo {cap_name} não tem imagens válidas. Pulando...")
                    page.click("button:has-text('Cancelar')")

        print("\n=== Automação Concluída ===")
        browser.close()

if __name__ == "__main__":
    run_automation()
