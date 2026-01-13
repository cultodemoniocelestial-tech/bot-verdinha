# 🌿 Verdinha Dashboard v2.0 - Robusto

Dashboard web para download automatizado de mangás e manhwas do site Verdinha.

## ✨ Funcionalidades

- **Download Automatizado**: Baixa todos os capítulos automaticamente navegando pelo botão "Próximo"
- **Retry Automático**: Se uma imagem falhar, tenta novamente até 3 vezes com backoff exponencial
- **Continuação Automática**: Se o download for interrompido (energia, erro, etc.), continua de onde parou
- **Persistência de Progresso**: Salva o progresso em disco para não perder trabalho
- **Interface Web**: Dashboard moderna com logs em tempo real via WebSocket
- **Thread-Safe**: Operações seguras com múltiplas threads
- **Stealth Mode**: Usa playwright-stealth para evitar detecção
- **Docker Ready**: Pronto para deploy com Docker Compose

## 🚀 Como Usar

### Pré-requisitos

- Docker Desktop instalado
- Conta no site Verdinha

### Instalação

1. **Extraia o arquivo ZIP** em uma pasta de sua preferência

2. **Configure as credenciais** no arquivo `.env`:
   ```env
   VERDINHA_EMAIL=seu@email.com
   VERDINHA_SENHA=suasenha
   ```

3. **Inicie o container**:
   ```bash
   docker-compose up -d --build
   ```

4. **Acesse o dashboard**: http://localhost:5000

### Uso

1. Cole a URL do **primeiro capítulo** que deseja baixar
2. Digite um nome para a pasta (ex: "Solo Leveling")
3. Clique em "Iniciar Download"
4. Acompanhe o progresso pelos logs em tempo real

### Parar e Continuar

- Clique em "Parar" para interromper o download
- O progresso é salvo automaticamente
- Ao iniciar novamente com o mesmo nome de pasta, o bot continua de onde parou

## 📁 Estrutura de Arquivos

```
verdinha_dash/
├── app.py                 # Backend Flask
├── templates/
│   └── index.html         # Interface web
├── downloads/             # Pasta com os downloads
│   ├── history.json       # Histórico de downloads
│   ├── progress.json      # Progresso salvo
│   └── [nome_obra]/       # Pastas das obras
│       ├── cap_001/       # Capítulos
│       ├── cap_002/
│       └── summary.json   # Relatório do download
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env                   # Credenciais (não compartilhe!)
```

## 🔧 Configuração Avançada

### Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `VERDINHA_EMAIL` | Email de login | - |
| `VERDINHA_SENHA` | Senha de login | - |
| `DOWNLOADS_DIR` | Diretório de downloads | `./downloads` |

### Limites de Recursos (docker-compose.yml)

O container está configurado com limites para não travar seu PC:

```yaml
deploy:
  resources:
    limits:
      cpus: '2'      # Máximo 2 CPUs
      memory: 4G     # Máximo 4GB RAM
```

Ajuste conforme necessário para sua máquina.

## 📝 API Endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/` | GET | Página principal da dashboard |
| `/api/health` | GET | Healthcheck do serviço |
| `/api/download` | POST | Inicia um novo download |
| `/api/stop` | POST | Para o download atual |
| `/api/status` | GET | Retorna status atual |
| `/api/progress/<nome>` | GET | Retorna progresso salvo de uma obra |
| `/api/config` | GET/POST | Gerencia configurações |

## 🔍 Troubleshooting

### Erro: "ModuleNotFoundError"

**Causa**: Dependências não instaladas corretamente.

**Solução**:
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Erro: "Target page, context or browser has been closed"

**Causa**: Bug em versões anteriores onde o navegador era fechado prematuramente.

**Solução**: Atualize para a versão 2.0 que usa `requests` para download de imagens.

### Download muito lento ou travando

**Causas possíveis**:
1. Conexão lenta com a internet
2. Site com rate limiting

**Soluções**:
- Verifique sua conexão
- Aguarde alguns minutos e tente novamente
- O bot tem retry automático, então falhas temporárias são recuperadas

### Container não inicia

**Causa**: Porta 5000 já em uso.

**Solução**:
```bash
# Verificar o que está usando a porta
netstat -ano | findstr :5000

# Ou mude a porta no docker-compose.yml:
ports:
  - "5001:5000"
```

### Erro de memória no WSL

**Causa**: WSL consumindo muita memória.

**Solução**: Crie um arquivo `.wslconfig` em `C:\Users\SeuUsuario\`:
```ini
[wsl2]
memory=4GB
processors=2
```

Depois reinicie o WSL:
```bash
wsl --shutdown
```

### Imagens não baixando (erro 403/401)

**Causa**: Cookies de autenticação expiraram ou login falhou.

**Soluções**:
1. Verifique se as credenciais no `.env` estão corretas
2. Pare e inicie o download novamente
3. Verifique se sua conta não foi bloqueada no site

### Como ver os logs do container

```bash
# Logs em tempo real
docker-compose logs -f

# Últimas 100 linhas
docker-compose logs --tail=100
```

### Como acessar os arquivos baixados

Os arquivos ficam na pasta `downloads/` dentro do diretório do projeto.

No Windows, você pode acessar diretamente pelo Explorer.

## 📊 Métricas e Relatórios

Após cada download, um arquivo `summary.json` é criado na pasta da obra com:

- Total de capítulos baixados
- Total de imagens
- Imagens que falharam
- Tempo total de execução
- Taxa de erro

## 🛡️ Segurança

- **Nunca compartilhe** o arquivo `.env` com suas credenciais
- O arquivo `.env` está no `.gitignore` por padrão
- As credenciais são armazenadas apenas localmente

## 📝 Changelog

### v2.0 (Atual)
- ✅ Retry automático (3 tentativas) com backoff exponencial
- ✅ Persistência de progresso (continua de onde parou)
- ✅ Download com streaming (menor uso de RAM)
- ✅ Thread-safety com locks
- ✅ Estados consistentes na UI
- ✅ Logs estruturados
- ✅ Limites de recursos no Docker
- ✅ Healthcheck do container
- ✅ Versões fixas das dependências

### v1.0
- Versão inicial com funcionalidades básicas

## 🤝 Suporte

Se encontrar problemas:

1. Verifique a seção de Troubleshooting acima
2. Confira os logs do container
3. Reinicie o container com `docker-compose restart`

## ⚠️ Aviso Legal

Este projeto é apenas para uso pessoal e educacional. Respeite os termos de serviço do site e os direitos autorais dos conteúdos.
