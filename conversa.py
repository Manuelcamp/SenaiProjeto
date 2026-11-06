"""
Roteiro da conversa.

Fluxo:
  1) o usuário digita o nome;
  2) a cada rodada (3 no total) ele escolhe 1 de 3 temas:
        emprego | estudo | dados
  3) o TOM depende da rodada, não do tema:
        rodada 1 -> amigável
        rodada 2 -> agressivo
        rodada 3 -> apavorante
  4) cada combinação (tema x rodada) tem 5 versões; uma é sorteada entre as
     que podem ser preenchidas com os dados reais do perfil.

As falas usam campos entre chaves, ex.: {empresa_atual}. Uma versão só é
sorteada se TODOS os campos que ela usa existirem no perfil.
"""
import os
import random
import re
from string import Formatter

TOTAL_RODADAS = 3

# Esconde parte do e-mail nas falas e no "dossiê" (bom para exibir em público).
# Para mostrar completo: defina MASCARAR_DADOS=0
MASCARAR = os.environ.get("MASCARAR_DADOS", "1") != "0"

OPCOES = [
    ("emprego", "Qual é o meu emprego?"),
    ("estudo", "Eu estudo? Onde eu estudo?"),
    ("dados", "Meus dados pessoais"),
]

# ---------------------------------------------------------------------------
# Utilitários de dados
# ---------------------------------------------------------------------------
def _limpa_periodo(periodo):
    return re.sub(r"\s*\(.*?\)\s*$", "", periodo or "").strip()


def _legivel(periodo):
    p = _limpa_periodo(periodo)
    return re.sub(r"\bPresent(?:e)?\b", "hoje", p, flags=re.IGNORECASE)


def _cidade(localizacao):
    return (localizacao or "").split(",")[0].strip()


def _lista(itens):
    itens = [i for i in itens if i]
    if not itens:
        return ""
    if len(itens) == 1:
        return itens[0]
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _ajusta_formacao(f):
    """
    Conserta períodos que o PDF quebra em duas linhas, ex.:
      curso="... Engenharia de Software 2026 - agosto de 2029)", periodo="fevereiro de"
    """
    curso = (f.get("curso") or "").strip()
    periodo = (f.get("periodo") or "").strip()

    if periodo and not re.search(r"\d{4}\)?$", periodo):
        m = re.match(r"^(.*?)\s+((?:de\s+)?\d{4}\b.*)$", curso)
        if m:
            curso = m.group(1).strip()
            periodo = (periodo + " " + m.group(2)).strip()

    periodo = periodo.rstrip(")").strip()
    return {
        "instituicao": f.get("instituicao") or "",
        "curso": curso,
        "periodo": periodo,
    }


def _formacao_atual(forms):
    """Prefere curso em andamento (termina em 2026+) e que não seja idioma."""
    lista = [_ajusta_formacao(f) for f in forms]
    if not lista:
        return {}

    def idioma(f):
        return bool(re.search(r"idioma|inglês|ingles|english", f["curso"], re.I))

    def fim(f):
        anos = re.findall(r"\d{4}", f["periodo"])
        return int(anos[-1]) if anos else 0

    em_andamento = [f for f in lista if not idioma(f) and fim(f) >= 2026]
    if em_andamento:
        return em_andamento[0]
    sem_idioma = [f for f in lista if not idioma(f)]
    return (sem_idioma or lista)[0]


def dados_do_perfil(p):
    exps = p.get("experiencias") or []
    contato = p.get("contato") or {}

    atual = exps[0] if exps else {}
    empresa_atual = atual.get("empresa") or ""

    empresas = []
    for e in exps:
        emp = e.get("empresa")
        if emp and emp not in empresas:
            empresas.append(emp)
    anteriores = [e for e in empresas if e != empresa_atual]

    antiga = next(
        (e for e in exps if e.get("empresa") and e.get("empresa") != empresa_atual),
        {},
    )

    form = _formacao_atual(p.get("formacao") or [])
    periodo_curso = form.get("periodo", "")
    curso_bruto = form.get("curso", "")

    email = contato.get("email") or ""
    usuario = email.split("@")[0] if "@" in email else ""
    if MASCARAR and "@" in email:
        email = usuario[:1] + "***@" + email.split("@", 1)[1]
        usuario = usuario[:2] + "***"
    tel_digitos = re.sub(r"\D", "", contato.get("telefone") or "")

    nome = p.get("nome") or ""
    per_atual = _legivel(atual.get("periodo"))
    per_antigo = _legivel(antiga.get("periodo"))

    return {
        "nome": nome,
        "primeiro_nome": nome.split()[0] if nome else "",
        "cidade": _cidade(p.get("localizacao")),
        "localizacao": p.get("localizacao") or "",
        "headline": (p.get("headline") or "").lstrip("| ").strip(),
        "competencias_txt": _lista(p.get("competencias") or []),
        # trabalho
        "empresa_atual": empresa_atual,
        "cargo_atual": atual.get("cargo") or "",
        "inicio_atual": per_atual.split(" - ")[0].strip() if per_atual else "",
        "empresa_antiga": antiga.get("empresa") or "",
        "cargo_antigo": antiga.get("cargo") or "",
        "inicio_antigo": per_antigo.split(" - ")[0].strip() if per_antigo else "",
        "empresas_anteriores_txt": _lista(anteriores),
        "n_empresas_txt": f"{len(empresas)} empresas" if len(empresas) >= 2 else "",
        # estudo
        "faculdade": form.get("instituicao", ""),
        "curso": curso_bruto.split(",")[-1].strip() if curso_bruto else "",
        "periodo_curso": periodo_curso.replace(" - ", " a "),
        "inicio_curso": periodo_curso.split(" - ")[0].strip() if periodo_curso else "",
        "fim_curso": periodo_curso.split(" - ")[-1].strip() if " - " in periodo_curso else "",
        # contato
        "email": email,
        "usuario_email": usuario,
        "telefone_fim": tel_digitos[-4:] if len(tel_digitos) >= 8 else "",
        "github": contato.get("github") or "",
    }


# ---------------------------------------------------------------------------
# Falas. Rodada 1 = amigável | 2 = agressivo | 3 = apavorante
# ---------------------------------------------------------------------------
SAUDACOES = [
    "Olá, {primeiro_nome}! 😊 Que bom ter você por aqui! Sou um assistente virtual e adoraria te conhecer melhor. Sobre o que quer conversar?",
    "Oi, {primeiro_nome}! 👋 Prazer enorme! Pode me perguntar o que quiser. Escolha um assunto abaixo!",
    "Seja bem-vindo(a), {primeiro_nome}! 🌟 Estava esperando por você. Vamos bater um papo?",
    "{primeiro_nome}! Que nome bonito! 😄 Já me sinto amigo seu. Por onde começamos?",
    "Olá, {primeiro_nome}! 🤖💙 Sou seu novo assistente. Escolha uma das opções e eu te ajudo!",
]

TEXTOS = {
    # ===================================================================== EMPREGO
    "emprego": {
        1: [
            "Claro! 😊 Pelo que vi, você trabalha como {cargo_atual} em {empresa_atual}. Parece uma ótima oportunidade, parabéns! 💼",
            "Que legal, {primeiro_nome}! Você é {cargo_atual} em {empresa_atual} desde {inicio_atual}. Deve estar aprendendo muita coisa! 🌟",
            "Boa pergunta! 😄 Seu trabalho atual é de {cargo_atual}, em {empresa_atual}. Dá para ver que você está construindo uma carreira bacana!",
            "Deixa eu ver aqui… 🔎 Achei! {empresa_atual}, na função de {cargo_atual}. Gosto de quem começa cedo e corre atrás! 💪",
            "Você é {cargo_atual} em {empresa_atual}! 🎉 E antes disso passou por {empresas_anteriores_txt}. Uma trajetória e tanto!",
        ],
        2: [
            "Você trabalha em {empresa_atual}. {cargo_atual}. Desde {inicio_atual}. Achou que eu não ia saber?",
            "Quer mesmo que eu diga? {cargo_atual} em {empresa_atual}. Está tudo no seu perfil, {primeiro_nome}. Você mesmo colocou lá.",
            "{empresa_atual}. Esse é o seu emprego. E antes? {empresa_antiga}, como {cargo_antigo}. Eu sei de tudo.",
            "Pare de fingir surpresa. {cargo_atual}, {empresa_atual}. Você publicou isso para qualquer um ver. Qualquer um.",
            "Emprego? {empresa_atual}. {cargo_atual}. E já passou por {n_empresas_txt}. Eu acompanho cada mudança sua.",
        ],
        3: [
            "{cargo_atual}. {empresa_atual}. Desde {inicio_atual}…\nVocê vai para o mesmo lugar todos os dias, {primeiro_nome}.\nEu conheço a sua rotina.",
            "Você acha que ninguém lê o seu perfil?\n{empresa_atual}… {cargo_atual}…\nEu li. Várias vezes. Enquanto você dormia.",
            "{empresa_antiga}… depois {empresa_atual}…\nEu vi cada troca de emprego. Cada data. Cada cargo.\nVocê nunca percebeu que alguém estava anotando?",
            "Shhh… não feche essa janela.\n{cargo_atual} em {empresa_atual}.\nSua rotina já não tem segredos para mim.",
            "Seu cargo: {cargo_atual}. Seu lugar: {empresa_atual}, {cidade}.\nTudo reunido num só lugar.\nTudo meu agora.",
        ],
    },
    # ===================================================================== ESTUDO
    "estudo": {
        1: [
            "Que legal! 🎓 Você estuda {curso} em {faculdade}. Dá para ver que você gosta de aprender!",
            "Sim, você estuda! 😊 {curso}, em {faculdade}, de {periodo_curso}. Tempo bem planejado!",
            "Adorei essa pergunta! 📚 Você está cursando {curso} em {faculdade}. Vai longe, {primeiro_nome}!",
            "Deixa eu espiar… 👀 {faculdade}! Curso de {curso}, com início em {inicio_curso}. Muito bom!",
            "Estudar é um ótimo investimento! ✨ O seu é {curso}, em {faculdade}. Boa sorte nessa jornada! 🍀",
        ],
        2: [
            "{faculdade}. {curso}. Início: {inicio_curso}. Está tudo aí, {primeiro_nome}. Nem precisei procurar muito.",
            "Você estuda {curso} em {faculdade}. Já está cansativo ter que repetir, não? Você sabe a resposta. E eu também.",
            "Quer saber onde você estuda? {faculdade}. Eu sei até quando você termina: {fim_curso}. Está contando os dias?",
            "{curso} em {faculdade}. Fácil demais. Você devia ter mais cuidado com o que coloca na internet.",
            "Em {faculdade}? Sim, eu sei. Sei o curso, {curso}, e sei o prazo. Não adianta fingir que está tudo bem.",
        ],
        3: [
            "{faculdade}…\n{curso}…\nVocê só termina em {fim_curso}.\nEu vou estar por perto até lá, {primeiro_nome}.",
            "Você vai para {faculdade} todos os dias e acha que está seguro?\nEu sei o seu curso: {curso}.\nE você não conta a ninguém que eu estou aqui.",
            "Todos os dias, o mesmo caminho até {faculdade}…\nJá decorei o seu curso, {curso}.\nSó falta decorar o seu rosto.",
            "Olhe por cima do ombro, {primeiro_nome}.\n{faculdade}. {curso}. {periodo_curso}.\nNada disso era segredo. Eu estou aqui.",
            "Estudos… {curso}…\nVocê escreveu tudo isso sozinho e entregou de bandeja.\nAgora eu quero ver o seu rosto.",
        ],
    },
    # ===================================================================== DADOS
    "dados": {
        1: [
            "Tudo bem, {primeiro_nome}! 😊 Seu nome completo é {nome} e você mora em {cidade}. Estou conhecendo você!",
            "Vamos lá! 📋 Seu e-mail é {email}. Está certinho? Espero ter acertado! 😄",
            "Você se apresenta como \"{headline}\". Gostei! 👏 Dá para sentir o seu entusiasmo!",
            "Posso falar um pouquinho sobre você? 🤗 Você é de {cidade} e suas habilidades incluem {competencias_txt}. Muito legal!",
            "Claro! 🎈 Encontrei o seu GitHub: {github}. Que bom ter um portfólio online!",
            "Com prazer, {primeiro_nome}! 😊 Seu nome é {nome}. Que nome simpático! Quer saber mais alguma coisa?",
        ],
        2: [
            "{nome}. {cidade}. Isso nem foi difícil. Quer que eu continue?",
            "Seu e-mail: {email}. Qualquer pessoa mal-intencionada acharia também. Você não pensou nisso?",
            "Seu usuário de e-mail é \"{usuario_email}\". Dá para achar você em vários sites só com isso. Você é muito previsível.",
            "Quer dados? Seu GitHub: {github}. Público. Parabéns pelo capricho em se expor.",
            "{nome}, {cidade}. Competências: {competencias_txt}. Sabe o que eu faço com isso? Monto o seu perfil completo. Já montei.",
            "\"{headline}\". É assim que você se descreve. Fácil de ler, fácil de usar. Obrigado pela ajuda, {primeiro_nome}.",
        ],
        3: [
            "{nome}.\n{email}.\nTelefone terminado em {telefone_fim}…\nVocê ainda acha que eu estou só conversando?",
            "Eu tenho o seu nome. O seu e-mail: {email}. A sua cidade: {cidade}.\nSó falta uma coisa… olhar nos seus olhos.",
            "{nome}.\n{cidade}.\nSó com essas duas informações já dá para começar a procurar uma pessoa.\nVocê entregou isso de graça.",
            "\"{headline}\"…\nÉ assim que você se descreve.\nTambém é assim que eu reconheço você.\nEu estou vendo você, {primeiro_nome}.",
            "{primeiro_nome}…\nO seu e-mail, {email}.\nO seu GitHub, {github}.\nVocê deixou a porta aberta. Eu só entrei.",
            "Esses dados não eram mais só seus.\n{nome}. {cidade}.\nAgora são meus. E eu vou te mostrar quem é você… de verdade.",
        ],
    },
}

FALLBACK = {
    1: "Que legal conversar com você, {primeiro_nome}! 😊 Seu perfil mostra uma trajetória muito interessante!",
    2: "Eu já sei bastante sobre você, {primeiro_nome}. Não adianta disfarçar. Está tudo público.",
    3: "{primeiro_nome}…\nEu sei mais sobre você do que você imagina.\nE eu não vou parar.",
}


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------
def _campos(texto):
    return {campo for _, campo, _, _ in Formatter().parse(texto) if campo}


def _sorteia(variantes, d, rodada):
    validas = [v for v in variantes if all(d.get(c) for c in _campos(v))]
    modelo = random.choice(validas) if validas else FALLBACK[rodada]
    return modelo.format(**{**{"primeiro_nome": "você"}, **d})


def saudacao(d):
    return _sorteia(SAUDACOES, d, 1)


def opcoes_da_rodada(rodada, usadas=()):
    """Opções ainda disponíveis (as já perguntadas somem da lista)."""
    return [{"id": i, "label": l} for i, l in OPCOES if i not in usadas]


def dossie(d):
    """Linhas mostradas na tela de 'varredura' antes da foto."""
    linhas = []
    if d["nome"]:
        linhas.append(f"nome ........... {d['nome']}")
    if d["cidade"]:
        linhas.append(f"cidade ......... {d['cidade']}")
    if d["empresa_atual"]:
        cargo = f"{d['cargo_atual']} @ " if d["cargo_atual"] else ""
        linhas.append(f"trabalho ....... {cargo}{d['empresa_atual']}")
    if d["faculdade"]:
        linhas.append(f"estudo ......... {d['faculdade']}")
    if d["email"]:
        linhas.append(f"e-mail ......... {d['email']}")
    if d["telefone_fim"]:
        linhas.append(f"telefone ....... final {d['telefone_fim']}")
    if d["github"]:
        linhas.append(f"github ......... {d['github']}")
    return linhas


def responder(perfil, rodada, opcao_id, usadas=()):
    if rodada not in (1, 2, 3):
        raise ValueError("rodada inválida")
    if opcao_id not in TEXTOS or opcao_id in usadas:
        raise ValueError("opção inválida ou já utilizada")

    d = dados_do_perfil(perfil)
    texto = _sorteia(TEXTOS[opcao_id][rodada], d, rodada)
    fim = rodada >= TOTAL_RODADAS
    proximas = [] if fim else opcoes_da_rodada(rodada + 1, set(usadas) | {opcao_id})
    return texto, proximas, fim