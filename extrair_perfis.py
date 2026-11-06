"""
Extrai dados de PDFs de perfil do LinkedIn ("Salvar como PDF") para perfis.json,
obtém a foto de perfil com Selenium (em segundo plano) e salva uma cópia local
em static/fotos/.

Uso:
    python extrair_perfis.py
    python extrair_perfis.py outra_pasta
    python extrair_perfis.py --refazer     (apaga e busca de novo todas as fotos)
    python extrair_perfis.py --visivel     (mostra o Chrome, útil p/ captcha)

Login automático (opcional): defina LINKEDIN_EMAIL e LINKEDIN_SENHA.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber

from imagemPerfil import (
    ImagemPerfilLinkedIn,
    baixar_imagem,
    normalizar_url_linkedin,
)

BASE = Path(__file__).resolve().parent
PASTA_PADRAO = BASE / "perfis"
SAIDA = BASE / "perfis.json"
# Se existir public/static (layout da Vercel), as fotos vão para lá.
_PUB = BASE / "public" / "static"
FOTOS_DIR = (_PUB if _PUB.exists() else BASE / "static") / "fotos"

LIMITE_LATERAL = 210

DATA_RE = re.compile(r"^[A-Za-zÀ-ú]+(?: de)? \d{4}\s*-\s*.+", re.IGNORECASE)
DURACAO_RE = re.compile(
    r"^\d+\s+(?:mês|mes|meses|ano|anos|mo|mos|yr|yrs|year|years|month|months)\b",
    re.IGNORECASE,
)
PAGINA_RE = re.compile(r"^(?:Page|Página)\s+\d+\s+(?:of|de)\s+\d+$", re.IGNORECASE)
LOCAL_RE = re.compile(r"(Brasil|Brazil|Remoto|Remote|Área|Area|,)", re.IGNORECASE)

SECOES_RESUMO = {"resumo", "summary", "sobre", "about"}
SECOES_EXPERIENCIA = {"experiência", "experiencia", "experience"}
SECOES_FORMACAO = {"formação acadêmica", "formacao academica", "education", "formação"}
LATERAL_CONTATO = {"contato", "contact"}
LATERAL_COMPETENCIAS = {"principais competências", "principais competencias", "top skills"}
LATERAL_CERTIFICACOES = {
    "certifications", "certificações", "certificacoes", "licenses & certifications",
}
LATERAL_IDIOMAS = {"languages", "idiomas"}

LINKEDIN_RE = re.compile(
    r"(?:(?:https?://)?(?:www\.)?)?linkedin\.com/in/[A-Za-z0-9_%\-]+/?",
    re.IGNORECASE,
)


def perto(valor, alvo, tol=0.2):
    return abs(valor - alvo) <= tol


def slug(texto):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def extrair_url_linkedin(texto):
    """Localiza URL LinkedIn no texto e devolve uma URL HTTPS normalizada."""
    if not texto:
        return None
    m = LINKEDIN_RE.search(texto)
    if not m:
        return None
    url = m.group(0).rstrip(".,;)")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return normalizar_url_linkedin(url)


def _colar_linhas(linhas):
    """
    Junta linhas de contato; quando uma URL quebra no fim da linha
    (termina em '-', '/' ou '.'), cola sem espaço.
    """
    saida = ""
    for l in linhas:
        if saida and saida[-1] in "-/._" and not saida.endswith(("Mobile)", "Home)")):
            saida += l
        else:
            saida += (" " if saida else "") + l
    return saida


def linhas_coluna(pagina, x0, x1):
    """Devolve [(texto, tamanho)] de uma coluna da página."""
    recorte = pagina.crop((x0, 0, x1, pagina.height))
    saida = []
    for linha in recorte.extract_text_lines(return_chars=True):
        texto = linha["text"].strip()
        if not texto or PAGINA_RE.match(texto):
            continue
        chars = linha.get("chars") or []
        tamanho = chars[0]["size"] if chars else 10.5
        saida.append((texto, tamanho))
    return saida


def parse_lateral(linhas):
    secoes, atual = {}, None

    for texto, tam in linhas:
        if perto(tam, 13.0, 1.0) and len(texto) < 40:
            atual = texto.lower()
            secoes[atual] = []
        elif atual:
            secoes[atual].append(texto)

    def pega(conjunto):
        for chave, valor in secoes.items():
            if chave in conjunto:
                return valor
        return []

    contato_linhas = pega(LATERAL_CONTATO)
    contato_txt = " ".join(contato_linhas)
    contato_colado = _colar_linhas(contato_linhas)

    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", contato_txt)
    telefone = re.search(r"\+?\d[\d\s().-]{7,}\d", contato_txt)
    # Usa o texto colado: o link do LinkedIn costuma quebrar em duas linhas.
    linkedin = extrair_url_linkedin(contato_colado)
    github = re.search(
        r"(?:(?:https?://)?(?:www\.)?)?github\.com/[\w-]+",
        contato_txt,
        re.IGNORECASE,
    )

    certs_linhas = pega(LATERAL_CERTIFICACOES)
    certificacoes = [" ".join(certs_linhas)] if certs_linhas else []

    return {
        "contato": {
            "email": email.group(0) if email else None,
            "telefone": telefone.group(0).strip() if telefone else None,
            "linkedin": linkedin,
            "github": github.group(0) if github else None,
        },
        "competencias": pega(LATERAL_COMPETENCIAS),
        "certificacoes": certificacoes,
        "idiomas": pega(LATERAL_IDIOMAS),
    }


def parse_experiencias(linhas):
    exps, emp, cargo = [], None, None

    for texto, tam in linhas:
        if perto(tam, 12.0):
            emp = {"empresa": texto, "duracao_total": None}
            cargo = None
        elif perto(tam, 11.5):
            cargo = {
                "empresa": emp["empresa"] if emp else None,
                "cargo": texto,
                "periodo": None,
                "local": None,
                "descricao": "",
            }
            exps.append(cargo)
        elif cargo is None:
            if emp and DURACAO_RE.match(texto):
                emp["duracao_total"] = texto
        else:
            if cargo["periodo"] is None and DATA_RE.match(texto):
                cargo["periodo"] = texto
            elif (
                cargo["local"] is None
                and not cargo["descricao"]
                and cargo["periodo"]
                and len(texto) < 60
                and LOCAL_RE.search(texto)
            ):
                cargo["local"] = texto
            else:
                cargo["descricao"] = (cargo["descricao"] + " " + texto).strip()

    return exps


def parse_formacao(linhas):
    itens, atual = [], None

    for texto, tam in linhas:
        if perto(tam, 12.0):
            atual = {"instituicao": texto, "curso": None, "periodo": None}
            itens.append(atual)
        elif atual:
            m = re.match(r"^(.*?)\s*·\s*\(?(.+?)\)?\s*$", texto)
            if m:
                atual["curso"] = m.group(1)
                atual["periodo"] = m.group(2)
            elif atual["curso"] is None:
                atual["curso"] = texto
            else:
                atual["curso"] += " " + texto

    return itens


def _url_linkedin_anotada(caminho):
    """
    Lê o link CLICÁVEL do PDF (anotação). É a fonte mais confiável: contém a
    URL completa mesmo quando o texto visível quebra em duas linhas.
    """
    try:
        with pdfplumber.open(caminho) as pdf:
            for pagina in pdf.pages:
                for link in (pagina.hyperlinks or []):
                    url = normalizar_url_linkedin(link.get("uri"))
                    if url:
                        return url
    except Exception:
        pass
    return None


def parse_pdf(caminho):
    lateral, principal, texto_completo = [], [], []

    with pdfplumber.open(caminho) as pdf:
        for pagina in pdf.pages:
            lateral += linhas_coluna(pagina, 0, min(LIMITE_LATERAL, pagina.width))
            principal += linhas_coluna(
                pagina, min(LIMITE_LATERAL + 5, pagina.width), pagina.width
            )
            texto_completo.append(pagina.extract_text() or "")

    perfil = {"arquivo": caminho.name}
    perfil.update(parse_lateral(lateral))

    nome, cabecalho, secoes, atual = None, [], {}, None

    for texto, tam in principal:
        if perto(tam, 26.0, 3):
            nome = f"{nome} {texto}".strip() if nome else texto
        elif perto(tam, 15.8, 1.2):
            atual = texto.lower()
            secoes[atual] = []
        elif atual is None:
            cabecalho.append(texto)
        else:
            secoes[atual].append((texto, tam))

    perfil["nome"] = nome or caminho.stem.replace("_", " ").replace("-", " ")
    perfil["id"] = slug(perfil["nome"])

    # Prioridade: link clicável (completo) > lateral colado > texto da página.
    perfil["contato"]["linkedin"] = (
        _url_linkedin_anotada(caminho)
        or perfil["contato"].get("linkedin")
        or extrair_url_linkedin(" ".join(texto_completo))
    )

    perfil["foto_url"] = None
    perfil["foto_local"] = None
    perfil["foto_origem"] = None  # URL do perfil de onde a foto veio
    perfil["localizacao"] = cabecalho[-1] if cabecalho else None
    perfil["headline"] = " ".join(cabecalho[:-1]) if len(cabecalho) > 1 else None

    perfil["resumo"] = ""
    perfil["experiencias"] = []
    perfil["formacao"] = []
    perfil["outras_secoes"] = {}

    for titulo, conteudo in secoes.items():
        if titulo in SECOES_RESUMO:
            perfil["resumo"] = " ".join(t for t, _ in conteudo)
        elif titulo in SECOES_EXPERIENCIA:
            perfil["experiencias"] = parse_experiencias(conteudo)
        elif titulo in SECOES_FORMACAO:
            perfil["formacao"] = parse_formacao(conteudo)
        else:
            perfil["outras_secoes"][titulo] = [t for t, _ in conteudo]

    return perfil


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------
def _foto_local_existente(pid):
    for ext in (".jpg", ".png", ".webp"):
        if (FOTOS_DIR / f"{pid}{ext}").exists():
            return f"/static/fotos/{pid}{ext}"
    return None


def _apagar_fotos_locais(pid):
    for ext in (".jpg", ".png", ".webp"):
        arq = FOTOS_DIR / f"{pid}{ext}"
        if arq.exists():
            arq.unlink()


def _carregar_fotos_existentes():
    """Lê o cache anterior: id -> {foto_url, foto_origem}."""
    if not SAIDA.exists():
        return {}
    try:
        antigos = json.loads(SAIDA.read_text(encoding="utf-8"))
        return {
            p.get("id"): {
                "foto_url": p.get("foto_url"),
                "foto_origem": p.get("foto_origem"),
            }
            for p in antigos
            if p.get("id")
        }
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def _baixar_e_registrar(p):
    if not p.get("foto_url"):
        return False
    _apagar_fotos_locais(p["id"])
    salvo = baixar_imagem(p["foto_url"], FOTOS_DIR / p["id"])
    if salvo:
        p["foto_local"] = f"/static/fotos/{salvo.name}"
        p["foto_origem"] = p["contato"].get("linkedin")
        print(f"[OK FOTO] {p['nome']}: salva em {salvo.name}")
        return True
    return False


def preencher_fotos(perfis, visivel=False):
    for p in perfis:
        if p.get("foto_url") and not p.get("foto_local"):
            if not _baixar_e_registrar(p):
                p["foto_url"] = None  # provavelmente expirou

    pendentes = [
        p for p in perfis
        if not p.get("foto_local") and p.get("contato", {}).get("linkedin")
    ]

    for p in perfis:
        if not p.get("foto_local") and not p.get("contato", {}).get("linkedin"):
            print(f"[FOTO] {p['nome']}: URL LinkedIn não encontrada no PDF.")

    if not pendentes:
        return

    print(f"\nVou buscar a foto de {len(pendentes)} perfil(is).")
    for p in pendentes:
        print(f"  - {p['nome']}: {p['contato']['linkedin']}")

    try:
        with ImagemPerfilLinkedIn(visivel=visivel) as coletor:
            coletor.autenticar()

            for p in pendentes:
                try:
                    url = coletor.obter_url_foto(p["contato"]["linkedin"], p["nome"])
                    if url:
                        p["foto_url"] = url
                        p["foto_origem"] = p["contato"].get("linkedin")
                        if not _baixar_e_registrar(p):
                            print(
                                f"[AVISO] {p['nome']}: URL obtida, mas o download "
                                "falhou (será usada a URL direta)."
                            )
                    else:
                        print(f"[SEM FOTO] {p['nome']}: não encontrada.")
                except Exception as erro:
                    print(f"[ERRO FOTO] {p['nome']}: {type(erro).__name__}: {erro}")
                    continue

    except Exception as erro:
        print(f"[ERRO SELENIUM] Não foi possível iniciar/usar o Chrome: {type(erro).__name__}: {erro}")
        print("Os dados dos PDFs ainda serão salvos; as fotos podem ser tentadas depois.")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    refazer = "--refazer" in sys.argv
    visivel = "--visivel" in sys.argv

    pasta = Path(args[0]) if args else PASTA_PADRAO

    if not pasta.exists() or not pasta.is_dir():
        sys.exit(f"Pasta de PDFs não encontrada: {pasta}")

    pdfs = sorted(pasta.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"Nenhum PDF encontrado em {pasta}")

    fotos_antigas = {} if refazer else _carregar_fotos_existentes()
    perfis, ids = [], set()

    for pdf in pdfs:
        try:
            p = parse_pdf(pdf)
        except Exception as erro:
            print(f"[ERRO PDF] {pdf.name}: {type(erro).__name__}: {erro}")
            continue

        base_id, n = p["id"], 2
        while p["id"] in ids:
            p["id"] = f"{base_id}-{n}"
            n += 1
        ids.add(p["id"])

        if refazer:
            _apagar_fotos_locais(p["id"])
            p["foto_url"], p["foto_local"] = None, None
        else:
            antigo = fotos_antigas.get(p["id"]) or {}
            origem_atual = p["contato"].get("linkedin")
            local = _foto_local_existente(p["id"])
            if local and origem_atual and antigo.get("foto_origem") == origem_atual:
                p["foto_url"] = antigo.get("foto_url")
                p["foto_local"] = local
                p["foto_origem"] = origem_atual
            else:
                if local:
                    print(
                        f"[FOTO] {p['nome']}: foto em cache sem origem confirmada "
                        "(ou link mudou); será buscada de novo."
                    )
                _apagar_fotos_locais(p["id"])
                p["foto_url"], p["foto_local"] = None, None
        perfis.append(p)

    if not perfis:
        sys.exit("Nenhum perfil pôde ser extraído dos PDFs.")

    preencher_fotos(perfis, visivel=visivel)

    for p in perfis:
        avisos = []
        if not p.get("localizacao"):
            avisos.append("sem localização")
        if not p.get("experiencias"):
            avisos.append("sem experiências")
        if not p.get("formacao"):
            avisos.append("sem formação")
        if not p.get("foto_local") and not p.get("foto_url"):
            avisos.append("sem foto")
        if not p.get("contato", {}).get("linkedin"):
            avisos.append("URL LinkedIn não encontrada")
        status = "OK" if not avisos else "REVISAR: " + ", ".join(avisos)
        print(f"[{status}] {p['nome']} ({p['arquivo']})")

    SAIDA.write_text(json.dumps(perfis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(perfis)} perfis salvos em {SAIDA}")


if __name__ == "__main__":
    main()