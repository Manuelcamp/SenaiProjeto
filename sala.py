"""
Estado da "sala" para o modo QR Code (telão + celular).

Uma única conversa ativa por vez. O servidor guarda o estado e os dois lados
(telão e celular) só consultam /api/sala/estado de tempos em tempos.

Armazenamento:
  - Local (padrão): memória do processo.
  - Vercel / produção: Redis REST (Upstash / Vercel KV). Basta definir
        UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN
    (ou KV_REST_API_URL + KV_REST_API_TOKEN). Na Vercel a memória NÃO persiste
    entre requisições, então o Redis é obrigatório lá.
"""
import json
import os
import secrets
import threading
import time
import urllib.request

import conversa

TTL_SESSAO = 20 * 60      # conversa abandonada libera a sala após 20 min
TELAO_TTL = 8             # s sem sinal do telão => considerado offline

K_SESSAO = "sala:sessao"
K_TELAO = "sala:telao"


# ---------------------------------------------------------------------------
# Armazenamento
# ---------------------------------------------------------------------------
class _Memoria:
    def __init__(self):
        self._d, self._l = {}, threading.Lock()

    def get(self, k):
        with self._l:
            item = self._d.get(k)
            if not item:
                return None
            bruto, exp = item
            if exp and exp < time.time():
                del self._d[k]
                return None
            return json.loads(bruto)

    def set(self, k, v, ex=None):
        with self._l:
            self._d[k] = (json.dumps(v), time.time() + ex if ex else None)

    def delete(self, k):
        with self._l:
            self._d.pop(k, None)


class _RedisRest:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def _cmd(self, *cmd):
        req = urllib.request.Request(
            self.url,
            data=json.dumps(list(cmd)).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read()).get("result")

    def get(self, k):
        v = self._cmd("GET", k)
        return json.loads(v) if v else None

    def set(self, k, v, ex=None):
        args = ["SET", k, json.dumps(v)]
        if ex:
            args += ["EX", int(ex)]
        self._cmd(*args)

    def delete(self, k):
        self._cmd("DEL", k)


def _criar_armazem():
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    tok = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if url and tok:
        return _RedisRest(url, tok)
    return _Memoria()


ARM = _criar_armazem()
CAMPOS_PUBLICOS = (
    "id", "mensagens", "exibidas", "opcoes", "rodada", "usadas",
    "fim", "fase", "dossie", "nome", "foto",
)


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
def _sessao():
    s = ARM.get(K_SESSAO)
    if s and time.time() - s["atualizado"] > TTL_SESSAO:
        ARM.delete(K_SESSAO)
        return None
    return s


def _salvar(s):
    s["atualizado"] = time.time()
    ARM.set(K_SESSAO, s, ex=TTL_SESSAO * 2)


def _confere(a, b):
    return bool(a) and bool(b) and secrets.compare_digest(str(a), str(b))


def telao_online():
    return ARM.get(K_TELAO) is not None


def ocupada():
    s = _sessao()
    return bool(s)


def iniciar(perfil, nome_digitado, foto):
    """Cria a conversa. Retorna a sessão ou None se a sala estiver ocupada."""
    if ocupada():
        return None
    d = conversa.dados_do_perfil(perfil)
    s = {
        "id": secrets.token_urlsafe(6),     # público (telão usa para detectar sessão nova)
        "sid": secrets.token_urlsafe(16),   # segredo do celular
        "perfil_id": perfil["id"],
        "nome": perfil.get("nome"),
        "foto": foto,
        "mensagens": [
            {"quem": "user", "texto": nome_digitado, "nivel": 0},
            {"quem": "bot", "texto": conversa.saudacao(d), "nivel": 0},
        ],
        "exibidas": 0,
        "opcoes": conversa.opcoes_da_rodada(1),
        "rodada": 1,
        "usadas": [],
        "fim": False,
        "fase": "conversando",
        "dossie": [],
    }
    _salvar(s)
    return s


def visao(papel, sid=None):
    """Estado que cada cliente pode ver."""
    if papel == "telao":
        ARM.set(K_TELAO, 1, ex=TELAO_TTL)

    s = _sessao()
    # o telão acabou de dar sinal de vida: não precisa consultar de novo (economiza comandos)
    online = True if papel == "telao" else telao_online()
    out = {"telao_online": online, "ocupada": bool(s), "minha": False, "sessao": None}
    if not s:
        return out

    minha = _confere(sid, s["sid"])
    if papel != "telao" and not minha:
        return out

    pub = {k: s[k] for k in CAMPOS_PUBLICOS}
    pub["liberado"] = (not s["fim"]) and (
        s["exibidas"] >= len(s["mensagens"]) or not online
    )
    out["sessao"] = pub
    out["minha"] = minha and papel != "telao"
    return out


def escolher(sid, opcao, perfil):
    """Retorna (ok, erro)."""
    s = _sessao()
    if not s or not _confere(sid, s["sid"]):
        return False, "sessao_invalida"
    if s["fim"]:
        return False, "encerrada"
    if telao_online() and s["exibidas"] < len(s["mensagens"]):
        return False, "aguarde"

    try:
        texto, proximas, fim = conversa.responder(
            perfil, s["rodada"], opcao, s["usadas"]
        )
    except ValueError:
        return False, "opcao_invalida"

    rotulo = next((l for i, l in conversa.OPCOES if i == opcao), opcao)
    s["mensagens"].append({"quem": "user", "texto": rotulo, "nivel": s["rodada"] - 1})
    s["mensagens"].append({"quem": "bot", "texto": texto, "nivel": s["rodada"]})
    s["usadas"].append(opcao)
    s["rodada"] += 1
    s["opcoes"] = proximas
    s["fim"] = fim
    if fim:
        s["dossie"] = conversa.dossie(conversa.dados_do_perfil(perfil))
    _salvar(s)
    return True, None


def confirmar_exibidas(sessao_id, n):
    s = _sessao()
    if not s or s["id"] != sessao_id:
        return False
    s["exibidas"] = max(s["exibidas"], min(int(n), len(s["mensagens"])))
    _salvar(s)
    return True


def mudar_fase(sessao_id, fase):
    s = _sessao()
    if not s or s["id"] != sessao_id or fase not in ("revelando", "fim"):
        return False
    s["fase"] = fase
    _salvar(s)
    return True


def reiniciar():
    ARM.delete(K_SESSAO)