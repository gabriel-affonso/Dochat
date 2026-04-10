# Dochat

Este diretório contém a aplicação principal do Dochat: a interface de uso diário do produto, com chat, documentos, notas, arquivo e integrações com modelos locais ou compatíveis com OpenAI.

O projeto aqui dentro parte de uma base derivada de Open WebUI, mas foi adaptado para a proposta do Dochat, com foco em acervo conversável, memória institucional, rastreabilidade e operação self-hosted.

Para a visão geral do repositório, consulte [`../README.md`](../README.md).

## Principais capacidades

- Chat contextual sobre documentos e coleções.
- Workspace de conhecimento para ingestão e organização documental.
- Notas integradas ao fluxo de leitura, síntese e acompanhamento.
- Área de arquivo para resgatar conversas, notas e documentos antigos.
- Operação local com Ollama ou provedores compatíveis com OpenAI.

## Executando com Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

O acesso local fica, por padrão, em `http://localhost:3000`.

## Executando em desenvolvimento

Instale as dependências do frontend:

```bash
npm install
```

Crie um ambiente virtual para o backend e instale as dependências Python:

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
```

Inicie o backend:

```bash
cd backend
./start.sh
```

Em outro terminal, inicie o frontend:

```bash
npm run dev
```

## Configuração mínima

O arquivo [`.env.example`](.env.example) cobre a configuração inicial. As variáveis mais importantes são:

- `OLLAMA_BASE_URL`
- `OPENAI_API_BASE_URL`
- `OPENAI_API_KEY`
- `CORS_ALLOW_ORIGIN`

## Estrutura relevante

- `src/`: frontend SvelteKit.
- `backend/`: API FastAPI e serviços principais.
- `static/`: assets e arquivos públicos.
- `docker-compose.yaml`: subida local com contêiner da aplicação e Ollama.

## Observação

O nome da aplicação, os metadados principais e parte da experiência já foram adaptados para `Dochat`, então este README substitui o material herdado do `Open WebUI` e passa a documentar a aplicação conforme o estado atual do projeto.
