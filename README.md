# Chatbot de demonstração (exposição de dados do LinkedIn)

Sem IA, sem internet: roteiro fixo + dados extraídos dos PDFs.

## Como usar

```bash
pip install -r requirements.txt
# 1. coloque os PDFs do LinkedIn (os ~30) na pasta perfis/
python extrair_perfis.py        # gera perfis.json  -> CONFIRA os 3 participantes
python app.py                   # abre em http://localhost:5000
```

## Arquivos

| Arquivo | Função |
|---|---|
| `perfis/` | PDFs do LinkedIn ("Salvar como PDF") |
| `extrair_perfis.py` | PDFs -> `perfis.json` |
| `buscador.py` | Acha o perfil pelo nome digitado (tolera acento, maiúscula, erro de digitação, nome incompleto) |
| `conversa.py` | **Textos do bot e dos botões** (3 rodadas x 3 opções): edite aqui |
| `app.py` | Servidor Flask |
| `static/index.html` | Interface do chat (o visual escurece a cada rodada) |

## Antes da apresentação

- Teste com o nome dos 3 participantes, em cada uma das 9 opções.
- Se um dado do PDF não foi extraído, as frases se adaptam, mas confira o `perfis.json`.
- Abra o navegador em tela cheia (F11) e teste no projetor.
