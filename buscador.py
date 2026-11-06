"""Busca aproximada de nome."""
import difflib
import re
import unicodedata


def normalizar(texto):
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _tokens(texto):
    return [
        t for t in normalizar(texto).split()
        if t not in {"de", "da", "do", "das", "dos", "e"}
    ]


def _similar(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def pontuar(digitado, nome_perfil):
    dig, nome = _tokens(digitado), _tokens(nome_perfil)
    if not dig or not nome:
        return 0.0

    melhores = [max(_similar(d, n) for n in nome) for d in dig]
    acertos = [m for m in melhores if m >= 0.8]
    if not acertos:
        return 0.0

    cobertura_digitado = len(acertos) / len(dig)
    qualidade = sum(acertos) / len(acertos)
    cobertura_nome = min(len(acertos), len(nome)) / len(nome)
    return qualidade * (0.6 * cobertura_digitado + 0.4 * cobertura_nome)


def buscar(digitado, perfis, limite_minimo=0.55, margem=0.12):
    if len(normalizar(digitado)) < 2:
        return "nao_achado", None

    notas = sorted(
        ((pontuar(digitado, p.get("nome", "")), p) for p in perfis),
        key=lambda x: x[0],
        reverse=True,
    )
    notas = [(n, p) for n, p in notas if n >= limite_minimo]
    if not notas:
        return "nao_achado", None

    melhor_nota, melhor = notas[0]
    empatados = [p for n, p in notas if melhor_nota - n < margem]
    if len(empatados) > 1:
        return "ambiguo", empatados
    return "ok", melhor
