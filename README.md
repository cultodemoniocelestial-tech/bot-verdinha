# Bots Integrados (Download + Upload) — Rodar no Zorin OS (sem Docker)

Este pacote sobe **4 processos**:

- **Download Dashboard (API/UI):** http://localhost:5000
- **Download Worker (baixa de verdade)**
- **Upload Dashboard (API/UI):** http://localhost:5001
- **Upload Worker (envia de verdade)**

## 1) Preparar o ambiente (uma vez)

> Se você já instalou as dependências antes, pode pular.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r download/requirements.txt -r upload/requirements.txt

# Playwright (necessário pro download)
python3 -m playwright install chromium
```

## 2) Rodar (sempre)

Na pasta do projeto:

```bash
chmod +x iniciar.sh
./iniciar.sh
```

Ele vai:
- limpar as portas (5000/5001),
- subir os serviços,
- fazer health-check,
- e mostrar os PIDs.

Para parar, **Ctrl+C** (o script já encerra os processos automaticamente).
Se quiser parar manualmente, use o `kill ...` que ele imprime.

## 3) Usar

### Download
1. Abra http://localhost:5000
2. Clique **“Importar Tudo do Catálogo”** (ou “Iniciar Manual”)
3. Os jobs entram na fila e o **Download Worker** começa a processar.

### Upload
1. Abra http://localhost:5001
2. Ajuste o que precisar e inicie.
3. O Upload Worker consome a fila e envia.

## Logs

Os arquivos ficam na raiz do projeto:

- `download_dashboard.log`
- `download_worker.log`
- `upload_dashboard.log`
- `upload_worker.log`

Para acompanhar:

```bash
tail -f download_worker.log
```

## Se ficar “travado” (reset rápido)

Se você quiser zerar fila/progresso, feche tudo e apague o banco:

```bash
rm -f data/queue.db
```

Depois rode o `./iniciar.sh` de novo.
