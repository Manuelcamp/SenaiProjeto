"""
Obtém a foto de perfil (preferencialmente 400_400) de um perfil do LinkedIn
usando Selenium e permite baixar a imagem.

- Por padrão o Chrome roda em SEGUNDO PLANO (headless).
- Se ainda não houver sessão salva em ./chrome_linkedin, abre uma janela
  visível uma única vez para o login (automático se as variáveis de ambiente
  LINKEDIN_EMAIL e LINKEDIN_SENHA existirem; senão, manual) e depois volta
  para o segundo plano.
- Valida o nome na página para não salvar a foto de outra pessoa.
"""
import os
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

BASE = Path(__file__).resolve().parent
PERFIL_CHROME = BASE / "chrome_linkedin"
PASTA_DEBUG = BASE / "debug_linkedin"

LINKEDIN_HOME = "https://www.linkedin.com/"
LINKEDIN_LOGIN = "https://www.linkedin.com/login"
RE_TAMANHO = re.compile(r"(?:shrink|scale|crop)_(\d+)_(\d+)", re.IGNORECASE)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# --------------------------------------------------------------------------
# Utilitários
# --------------------------------------------------------------------------
def normalizar_url_linkedin(url):
    """Valida e normaliza uma URL de perfil LinkedIn (remove query string)."""
    if not url:
        return None

    url = url.strip().strip(".,;)")

    if url.startswith("www.linkedin.com/"):
        url = "https://" + url
    elif url.startswith("linkedin.com/"):
        url = "https://www." + url
    elif url.startswith("/in/"):
        url = "https://www.linkedin.com" + url

    if not url.startswith(("https://", "http://")):
        return None

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host not in {"linkedin.com", "www.linkedin.com"}:
        return None

    if not re.match(r"^/in/[^/]+", parsed.path, re.IGNORECASE):
        return None

    return f"https://www.linkedin.com{parsed.path.rstrip('/')}/"


def tamanho_da_url(url):
    m = RE_TAMANHO.search(url or "")
    return int(m.group(1)) if m else 0


def _pontuar(url):
    """400_400 ganha de tudo; depois, quanto maior, melhor."""
    t = tamanho_da_url(url)
    return 10_000 if t == 400 else t


def baixar_imagem(url, destino_sem_extensao):
    """Baixa a imagem e salva com a extensão correta. Retorna Path ou None."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Referer": LINKEDIN_HOME}
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            tipo = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            dados = resp.read()
    except Exception as erro:
        print(f"[FOTO] Falha ao baixar imagem: {type(erro).__name__}: {erro}")
        return None

    if not tipo.startswith("image/") or len(dados) < 500:
        print(f"[FOTO] Resposta não parece uma imagem (tipo={tipo!r}).")
        return None

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
        tipo, ".jpg"
    )
    destino = Path(str(destino_sem_extensao) + ext)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    return destino


# --------------------------------------------------------------------------
# Scripts executados no navegador
# --------------------------------------------------------------------------
JS_CANDIDATAS = """
const primeiro = (arguments[0] || "").toLowerCase();
const lista = [];
let i = 0;
document.querySelectorAll("img").forEach((img) => {
  const src = img.currentSrc || img.src || "";
  if (!src.includes("media.licdn.com") || !src.includes("profile-displayphoto")) return;
  const r = img.getBoundingClientRect();
  img.setAttribute("data-cand", String(i));
  lista.push({
    idx: i,
    src: src,
    alt: img.alt || "",
    w: Math.round(r.width),
    top: Math.round(r.top + window.scrollY),
    main: !!img.closest("main"),
    dialog: !!img.closest("[role=dialog], .artdeco-modal"),
    nome: primeiro && (img.alt || "").toLowerCase().includes(primeiro)
  });
  i++;
});
return lista;
"""

JS_OG_IMAGE = """
const m = document.querySelector('meta[property="og:image"]');
return m ? m.content : null;
"""

JS_NOME_PAGINA = """
const h = document.querySelector("main h1") || document.querySelector("h1");
return h ? h.innerText : (document.title || "");
"""


def _pagina_pronta(driver):
    return driver.execute_script("return document.readyState") == "complete"


def _sem_acento(t):
    import unicodedata
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


class ImagemPerfilLinkedIn:
    """Sessão Selenium reutilizável para vários perfis."""

    def __init__(self, visivel=False):
        self.visivel = visivel
        self.driver = None
        self._autenticado = False
        self._iniciar(headless=not visivel)

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------
    def _iniciar(self, headless):
        opcoes = Options()
        opcoes.add_argument(f"--user-data-dir={PERFIL_CHROME.resolve()}")
        opcoes.add_argument("--window-size=1440,1000")
        opcoes.add_argument("--lang=pt-BR")
        opcoes.add_argument("--disable-blink-features=AutomationControlled")
        opcoes.add_experimental_option("excludeSwitches", ["enable-automation"])
        if headless:
            opcoes.add_argument("--headless=new")
            opcoes.add_argument(f"--user-agent={UA}")
            opcoes.add_argument("--disable-gpu")
        self.driver = webdriver.Chrome(options=opcoes)
        self._headless = headless

    def _encerrar_driver(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None

    def _tem_sessao(self):
        return any(c.get("name") == "li_at" for c in self.driver.get_cookies())

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def _login_na_janela(self):
        """Executa o login (automático se houver credenciais; senão manual)."""
        email = os.environ.get("LINKEDIN_EMAIL")
        senha = os.environ.get("LINKEDIN_SENHA")

        self.driver.get(LINKEDIN_LOGIN)
        WebDriverWait(self.driver, 30).until(_pagina_pronta)

        if email and senha:
            try:
                self.driver.find_element(By.ID, "username").send_keys(email)
                campo = self.driver.find_element(By.ID, "password")
                campo.send_keys(senha)
                campo.send_keys(Keys.ENTER)
                print("Login automático enviado.")
                try:
                    WebDriverWait(self.driver, 25).until(
                        lambda d: self._tem_sessao()
                    )
                except TimeoutException:
                    pass
            except Exception as erro:
                print(f"[LOGIN] Falha no login automático: {erro}")

        if not self._tem_sessao():
            print("\nConclua o login (e qualquer verificação/captcha) na janela do Chrome.")
            input("Pressione ENTER depois de concluir o login... ")

    def autenticar(self):
        # 1) tenta usar a sessão salva (em segundo plano, se for o caso)
        self.driver.get(LINKEDIN_HOME)
        WebDriverWait(self.driver, 30).until(_pagina_pronta)

        if not self._tem_sessao():
            # 2) precisa de login: usa janela visível uma vez
            if self._headless:
                self._encerrar_driver()
                self._iniciar(headless=False)
            self._login_na_janela()

            self.driver.get(LINKEDIN_HOME)
            WebDriverWait(self.driver, 30).until(_pagina_pronta)
            if not self._tem_sessao():
                raise RuntimeError(
                    "Não foi detectada uma sessão autenticada do LinkedIn "
                    "(cookie li_at). Confira o login e tente novamente."
                )

            # 3) volta para segundo plano mantendo a sessão salva
            if not self.visivel:
                self._encerrar_driver()
                time.sleep(1)
                self._iniciar(headless=True)
                self.driver.get(LINKEDIN_HOME)
                WebDriverWait(self.driver, 30).until(_pagina_pronta)
                if not self._tem_sessao():
                    print("[AVISO] Sessão não persistiu em segundo plano; usando janela visível.")
                    self._encerrar_driver()
                    self._iniciar(headless=False)

        self._autenticado = True
        print("Sessão do LinkedIn autenticada.")

    # ------------------------------------------------------------------
    # Fotos
    # ------------------------------------------------------------------
    def _candidatas(self, primeiro_nome):
        try:
            return self.driver.execute_script(JS_CANDIDATAS, primeiro_nome) or []
        except Exception:
            return []

    def _foto_do_topo(self, primeiro_nome):
        topo = [
            c for c in self._candidatas(primeiro_nome)
            if c["main"] and not c["dialog"] and c["top"] < 900 and c["w"] >= 80
        ]
        if not topo:
            return None, None
        topo.sort(key=lambda c: (c["nome"], c["w"]), reverse=True)
        return topo[0]["src"], topo[0]["idx"]

    def _foto_do_modal(self, idx, primeiro_nome):
        if idx is None:
            return None
        try:
            alvo = self.driver.find_element(By.CSS_SELECTOR, f"img[data-cand='{idx}']")
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", alvo
            )
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", alvo)

            melhor = None
            for _ in range(10):
                time.sleep(0.5)
                no_modal = [c for c in self._candidatas(primeiro_nome) if c["dialog"]]
                if no_modal:
                    no_modal.sort(key=lambda c: _pontuar(c["src"]), reverse=True)
                    melhor = no_modal[0]["src"]
                    break
            try:
                self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            except Exception:
                pass
            return melhor
        except Exception:
            return None

    def _og_image(self):
        try:
            og = self.driver.execute_script(JS_OG_IMAGE)
        except Exception:
            return None
        if og and "media.licdn.com" in og and "profile-displayphoto" in og:
            return og
        return None

    def _nome_confere(self, nome):
        """True se o primeiro nome esperado aparece no título da página."""
        if not nome:
            return True
        try:
            achado = self.driver.execute_script(JS_NOME_PAGINA) or ""
        except Exception:
            return True
        if not achado.strip():
            return True  # não deu para validar
        primeiro = _sem_acento(nome).split()[0]
        ok = primeiro in _sem_acento(achado)
        if not ok:
            print(
                f"[FOTO] ATENÇÃO: a página mostra '{achado.strip()[:50]}', "
                f"que não confere com '{nome}'. Foto descartada."
            )
        return ok

    def _salvar_debug(self, url_perfil):
        try:
            PASTA_DEBUG.mkdir(exist_ok=True)
            nome = re.sub(r"[^a-z0-9]+", "-", url_perfil.lower()).strip("-")[-60:]
            (PASTA_DEBUG / f"{nome}.html").write_text(
                self.driver.page_source, encoding="utf-8"
            )
            self.driver.save_screenshot(str(PASTA_DEBUG / f"{nome}.png"))
            print(f"[FOTO] Debug salvo em {PASTA_DEBUG}")
        except Exception:
            pass

    def obter_url_foto(self, url_perfil, nome=None):
        url_perfil = normalizar_url_linkedin(url_perfil)
        if not url_perfil:
            print("[FOTO] URL de perfil inválida ou ausente.")
            return None

        if not self._autenticado:
            self.autenticar()

        primeiro = (nome or "").split()[0] if nome else ""

        print(f"[FOTO] Abrindo {url_perfil}")
        self.driver.get(url_perfil)
        WebDriverWait(self.driver, 30).until(_pagina_pronta)

        atual = self.driver.current_url.lower()
        if any(k in atual for k in ("authwall", "/login", "/checkpoint", "/uas/")):
            self._salvar_debug(url_perfil)
            raise RuntimeError(
                f"LinkedIn redirecionou para login/verificação ({atual}). "
                "Rode com --visivel e resolva no navegador."
            )

        self.driver.execute_script("window.scrollTo(0, 0)")

        try:
            WebDriverWait(self.driver, 15).until(
                lambda d: self._foto_do_topo(primeiro)[0] is not None
            )
        except TimeoutException:
            pass

        if not self._nome_confere(nome):
            self._salvar_debug(url_perfil)
            return None

        encontradas = []
        url_topo, idx = self._foto_do_topo(primeiro)
        if url_topo:
            encontradas.append((url_topo, "topo"))

        if not url_topo or tamanho_da_url(url_topo) < 400:
            url_modal = self._foto_do_modal(idx, primeiro)
            if url_modal:
                encontradas.append((url_modal, "modal"))

        og = self._og_image()
        if og:
            encontradas.append((og, "og:image"))

        if not encontradas:
            print(
                "[FOTO] Nenhuma foto de perfil encontrada. Pode ser perfil sem "
                "foto, bloqueio ou mudança no LinkedIn."
            )
            self._salvar_debug(url_perfil)
            return None

        encontradas.sort(key=lambda e: _pontuar(e[0]), reverse=True)
        url, origem = encontradas[0]
        tam = tamanho_da_url(url)

        if tam == 400:
            print(f"[FOTO] 400_400 encontrada (origem: {origem}).")
        else:
            print(
                f"[FOTO] 400_400 indisponível; usando a melhor encontrada "
                f"({tam or '?'}px, origem: {origem})."
            )
        return url

    # ------------------------------------------------------------------
    def fechar(self):
        self._encerrar_driver()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.fechar()
        return False


def obter_foto_perfil(url_perfil, nome=None, visivel=False):
    with ImagemPerfilLinkedIn(visivel=visivel) as coletor:
        return coletor.obter_url_foto(url_perfil, nome)


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("Uso: python imagemPerfil.py https://www.linkedin.com/in/usuario/ [--visivel]")

    with ImagemPerfilLinkedIn(visivel="--visivel" in sys.argv) as coletor:
        foto = coletor.obter_url_foto(args[0])
        print("\nURL da foto:", foto or "não encontrada")