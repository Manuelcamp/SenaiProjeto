"""
Servidor Flask do chat.  Execute: python app.py

Rotas de tela:
  /          modo clássico (digita o nome no próprio PC)
  /telao     modo QR Code: tela do telão (abra no computador principal)
  /c         modo QR Code: página que abre no celular (é o que o QR aponta)

Variáveis de ambiente úteis:
  PORT=8080                porta (padrão 8080)
  URL_PUBLICA=https://...  endereço que vai dentro do QR (Vercel, ngrok etc.)
  MASCARAR_DADOS=0         mostra e-mail completo nas falas
  TELAO_USUARIO / TELAO_SENHA   login do telão (padrão: Magenta / MGM9013#a)
  SECRET_KEY               chave da sessão (opcional; há um padrão estável)
"""
import hashlib
import io
import json
import os
import secrets
import socket
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import qrcode
import qrcode.image.svg
from flask import Flask, Response, jsonify, request, send_from_directory, session

import conversa
import sala
from buscador import buscar

BASE = Path(__file__).parent

# Layout para Vercel: public/static/  (a Vercel serve public/** pela CDN).
# Layout local antigo: static/  (continua funcionando).
_PUBLIC_STATIC = BASE / "public" / "static"
STATIC_DIR = _PUBLIC_STATIC if _PUBLIC_STATIC.exists() else BASE / "static"

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)

# ---- login do telão -------------------------------------------------------
TELAO_USUARIO = os.environ.get("TELAO_USUARIO", "Magenta")
TELAO_SENHA = os.environ.get("TELAO_SENHA", "MGM9013#a")
# Chave estável (importante na Vercel, onde há várias instâncias do servidor).
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    f"{TELAO_USUARIO}|{TELAO_SENHA}|projeto-privacidade".encode()
).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),  # https na Vercel
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


def telao_obrigatorio(f):
    @wraps(f)
    def interno(*args, **kwargs):
        if not session.get("telao"):
            return jsonify(erro="nao_autorizado"), 401
        return f(*args, **kwargs)
    return interno


def carregar_perfis():
    arquivo = BASE / "perfis.json"
    if not arquivo.exists():
        raise SystemExit(
            "perfis.json não existe. Rode primeiro: python extrair_perfis.py"
        )
    return json.loads(arquivo.read_text(encoding="utf-8"))


def url_foto_segura(valor):
    """Aceita apenas URLs HTTPS com host definido."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        parsed = urlparse(valor.strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return valor.strip()


def foto_do_perfil(perfil):
    """Prefere a cópia local (não expira); cai para a URL remota."""
    local = perfil.get("foto_local")
    if local:
        # Na Vercel as fotos ficam na CDN (public/), fora do alcance do Python.
        if os.environ.get("VERCEL"):
            return local
        rel = local[len("/static/"):] if local.startswith("/static/") else local.lstrip("/")
        if (STATIC_DIR / rel).exists():
            return local
    return url_foto_segura(perfil.get("foto_url"))


PERFIS = carregar_perfis()
POR_ID = {p["id"]: p for p in PERFIS}

MSG_AMBIGUO = "Encontrei mais de um perfil parecido. Pode digitar seu nome completo?"
MSG_NAO_ACHADO = (
    "Não consegui encontrar esse nome. "
    "Pode digitar o nome completo como aparece no perfil?"
)


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/telao")
def tela_telao():
    return send_from_directory(app.static_folder, "telao.html")


@app.get("/c")
def tela_celular():
    return send_from_directory(app.static_folder, "celular.html")


# ---------------------------------------------------------------------------
# Modo clássico
# ---------------------------------------------------------------------------
@app.post("/api/nome")
def api_nome():
    texto = (request.get_json(silent=True) or {}).get("nome", "")
    status, resultado = buscar(texto, PERFIS)

    if status == "ok":
        d = conversa.dados_do_perfil(resultado)
        return jsonify(
            status="ok",
            perfil_id=resultado["id"],
            nome=resultado.get("nome"),
            mensagem=conversa.saudacao(d),
            opcoes=conversa.opcoes_da_rodada(1),
            rodada=1,
            foto=foto_do_perfil(resultado),
        )
    if status == "ambiguo":
        return jsonify(status="ambiguo", mensagem=MSG_AMBIGUO)
    return jsonify(status="nao_achado", mensagem=MSG_NAO_ACHADO)


@app.post("/api/escolha")
def api_escolha():
    dados = request.get_json(silent=True) or {}
    perfil = POR_ID.get(dados.get("perfil_id"))
    rodada = dados.get("rodada")
    opcao = dados.get("opcao")
    usadas = dados.get("usadas") or []
    if not isinstance(usadas, list):
        usadas = []

    if not perfil or rodada not in (1, 2, 3):
        return jsonify(erro="requisição inválida"), 400

    try:
        texto, proximas, fim = conversa.responder(perfil, rodada, opcao, usadas)
    except ValueError:
        return jsonify(erro="opção inválida"), 400

    resposta = dict(
        mensagem=texto,
        opcoes=proximas,
        rodada=rodada + 1,
        fim=fim,
        nivel=rodada,  # 1 amigável | 2 agressivo | 3 apavorante
    )
    if fim:
        resposta.update(
            foto=foto_do_perfil(perfil),
            nome=perfil.get("nome"),
            dossie=conversa.dossie(conversa.dados_do_perfil(perfil)),
        )
    return jsonify(resposta)


# ---------------------------------------------------------------------------
# Login do telão
# ---------------------------------------------------------------------------
@app.get("/api/telao/status")
def telao_status():
    return jsonify(logado=bool(session.get("telao")))


@app.post("/api/telao/login")
def telao_login():
    d = request.get_json(silent=True) or {}
    usuario = str(d.get("usuario", "")).strip().lower().encode()
    senha = str(d.get("senha", "")).encode()
    ok_user = secrets.compare_digest(usuario, TELAO_USUARIO.lower().encode())
    ok_senha = secrets.compare_digest(senha, TELAO_SENHA.encode())
    if ok_user and ok_senha:
        session["telao"] = True
        session.permanent = True
        return jsonify(ok=True)
    time.sleep(1)  # atrasa tentativas em sequência
    return jsonify(erro="credenciais_invalidas"), 401


@app.post("/api/telao/sair")
def telao_sair():
    session.pop("telao", None)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Modo QR Code (telão + celular)
# ---------------------------------------------------------------------------
def _ip_local():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _base_url():
    publica = os.environ.get("URL_PUBLICA", "").strip().rstrip("/")
    if publica:
        return publica
    host = request.host
    nome, _, porta = host.partition(":")
    if nome in ("localhost", "127.0.0.1", "[::1]"):
        host = _ip_local() + (f":{porta}" if porta else "")
    esquema = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{esquema}://{host}"


@app.get("/api/info")
@telao_obrigatorio
def api_info():
    base = _base_url()
    return jsonify(url_celular=f"{base}/c", url_telao=f"{base}/telao")


@app.get("/api/qr.svg")
@telao_obrigatorio
def api_qr():
    img = qrcode.make(
        f"{_base_url()}/c",
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
        border=1,
    )
    buf = io.BytesIO()
    img.save(buf)
    resp = Response(buf.getvalue(), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/sala/estado")
def sala_estado():
    papel = "telao" if request.args.get("papel") == "telao" else "celular"
    if papel == "telao" and not session.get("telao"):
        return jsonify(erro="nao_autorizado"), 401
    resp = jsonify(sala.visao(papel, request.args.get("sid")))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/sala/nome")
def sala_nome():
    texto = ((request.get_json(silent=True) or {}).get("nome") or "").strip()
    if sala.ocupada():
        return jsonify(
            status="ocupada",
            mensagem="Outra pessoa está conversando agora. Aguarde a sua vez! ⏳",
        )

    status, resultado = buscar(texto, PERFIS)
    if status == "ambiguo":
        return jsonify(status="ambiguo", mensagem=MSG_AMBIGUO)
    if status != "ok":
        return jsonify(status="nao_achado", mensagem=MSG_NAO_ACHADO)

    s = sala.iniciar(resultado, texto, foto_do_perfil(resultado))
    if not s:
        return jsonify(
            status="ocupada",
            mensagem="Outra pessoa está conversando agora. Aguarde a sua vez! ⏳",
        )
    return jsonify(status="ok", sid=s["sid"])


@app.post("/api/sala/escolha")
def sala_escolha():
    dados = request.get_json(silent=True) or {}
    s = sala._sessao()
    perfil = POR_ID.get(s["perfil_id"]) if s else None
    if not perfil:
        return jsonify(erro="sessao_invalida"), 400
    ok, erro = sala.escolher(dados.get("sid"), dados.get("opcao"), perfil)
    if not ok:
        return jsonify(erro=erro), 400
    return jsonify(ok=True)


@app.post("/api/sala/exibidas")
@telao_obrigatorio
def sala_exibidas():
    d = request.get_json(silent=True) or {}
    return jsonify(ok=sala.confirmar_exibidas(d.get("id"), d.get("n", 0)))


@app.post("/api/sala/fase")
@telao_obrigatorio
def sala_fase():
    d = request.get_json(silent=True) or {}
    return jsonify(ok=sala.mudar_fase(d.get("id"), d.get("fase")))


@app.post("/api/sala/reiniciar")
@telao_obrigatorio
def sala_reiniciar():
    sala.reiniciar()
    return jsonify(ok=True)


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 8080))
    ip = _ip_local()
    print("\n  Modo clássico : http://localhost:%d/" % porta)
    print("  Telão (QR)    : http://localhost:%d/telao" % porta)
    print("  Celular (QR)  : http://%s:%d/c   <- é o que o QR aponta\n" % (ip, porta))
    # 0.0.0.0 permite que o celular (mesma rede Wi-Fi) acesse o computador.
    app.run(host="0.0.0.0", port=porta, debug=False, threaded=True)