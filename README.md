# CV Training Interface

Ferramenta local para **revisar anotações YOLO**, versionar rótulos e **treinar / testar** modelos — tudo em uma UI web no navegador.

O fluxo típico: importar um export YOLO (ou fotos soltas) → pré-classificar com um modelo existente → corrigir caixas no revisor → salvar versões → treinar um fine-tune e validar nas métricas.

---

## O que tem

| Área | O que faz |
|------|-----------|
| **Revisor** | Canvas com zoom, desenho/edição de caixas, limiar de confiança, filtros por status/classe/conflito |
| **Inserir fotos** | Fotos avulsas ou pasta YOLO completa (`images/`, `labels/`, `data.yaml`); hard link das imagens (não altera o export original) |
| **Pré-classificação** | Roda um YOLO nas fotos novas e grava sugestões + confiança |
| **Versões** | Snapshot só dos rótulos (sem fotos) em `work/versoes/` — dá para restaurar depois |
| **Modelos** | Monta dataset a partir do revisado, treina Ultralytics YOLO, mostra mAP/plots e testa em uma foto |
| **Notebook** | `treinamento_fine_tuning.ipynb` — passo a passo do fine-tune (ONNX→PT, classes, split, treino) |

---

## Requisitos

- Python 3.10+
- GPU NVIDIA recomendada para treino (CUDA)
- Windows / Linux / macOS

---

## Setup rápido

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

# PyTorch com CUDA (ajuste o índice se precisar) — instale ANTES do ultralytics
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126

pip install ultralytics fastapi uvicorn python-multipart pyyaml pandas onnx onnxruntime
```

Coloque os pesos locais (não vão para o git):

```text
models/seu_modelo.pt     # ou .onnx reconstruído via onnx_para_pt.py
```

---

## Subir o revisor

Na raiz do projeto:

```bash
python -m revisor --modelo models/seu_modelo.pt
```

Abra [http://127.0.0.1:8765](http://127.0.0.1:8765).

Opções úteis:

```bash
python -m revisor --help
python -m revisor --porta 8765 --revisao work/revisao --revisado work/revisado
python -m revisor gerar-conf --ajuda   # gera confianças a partir de um modelo
```

---

## Fluxo de trabalho

```text
export YOLO  ──importar──►  work/revisao/          (pré-anotações + imagens)
                                  │
                                  ▼
                            [ Revisor UI ]
                                  │
                                  ▼
                            work/revisado/         (rótulos revisados)
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
              work/versoes/   datasets/      runs/ + models/
              (snapshots)     (treino)       (pesos / métricas)
```

1. **Importar** — botão *Inserir fotos*: escolha um dataset YOLO na raiz do projeto ou solte JPGs.
2. **Revisar** — corrija caixas; `Enter` salva e avança. Rótulos vão para `work/revisado/`.
3. **Versionar** — *Versões* cria um snapshot leve (só labels/meta).
4. **Treinar** — *Modelos*: epochs, split, base weights; acompanhe o job e abra plots/mAP.
5. **Testar** — mesma aba: rode o `.pt` numa imagem e veja as detecções.

O export YOLO original e as fotos em `work/revisao/images/` **não são reescritos** no treino: as imagens entram no dataset por hard link (ou cópia se o FS não permitir).

---

## Layout do projeto

```text
CV-Training-Interface/
├── revisor/                 # app FastAPI + UI estática
│   ├── servidor.py
│   ├── treino.py
│   ├── importar_dataset.py
│   ├── versoes.py
│   ├── preclass.py
│   └── static/              # HTML / CSS / JS
├── treinamento_fine_tuning.ipynb
├── onnx_para_pt.py          # reconstrói .pt a partir de .onnx Ultralytics
├── work/
│   ├── revisao/             # pré-anotações (labels, conf, classes.txt)
│   │   └── images/          # fotos — só local (.gitignore)
│   ├── revisado/            # rótulos após revisão
│   └── versoes/             # snapshots de labels
├── models/                  # pesos — só local
├── datasets/ · runs/        # gerados no treino — só local
└── infractions-yolo-*/      # exports brutos — só local
```

---

## Atalhos (resumo)

| Tecla | Ação |
|-------|------|
| `Enter` | Salvar e próxima |
| `A` / `D` ou setas | Anterior / próxima |
| Arrastar no vazio | Nova caixa |
| `1`…`0`, `Q`, `W`… | Classe (da caixa ou ativa) |
| `Del` | Apagar caixa |
| Roda do mouse | Zoom |
| `Espaço` + arrastar | Pan |
| `?` | Ajuda completa na UI |

---

## Privacidade

Pesos (`.pt`, `.onnx`, `.engine`), pastas `models/`, imagens e runs de treino **não entram no repositório**. Versionamos só código, notebook limpo e progresso de rótulos (texto).

---

## Licença / uso

Projeto interno de treinamento de visão computacional. Ajuste classes, limiares e hiperparâmetros conforme o seu dataset.
