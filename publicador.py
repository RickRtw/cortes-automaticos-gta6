"""
Postador automatico de cortes -> YouTube Shorts, Instagram Reels, TikTok.

  python publicador.py rodar                 # loop: posta a fila respeitando limites/intervalos
  python publicador.py listar                # mostra a fila e o status por plataforma
  python publicador.py aprovar <nome|todos>  # libera cortes em modo "aprovar"
  python publicador.py rejeitar <nome>       # tira da fila
  python publicador.py login youtube|tiktok  # autoriza a conta (abre o navegador uma vez)
  python publicador.py checar                # valida credenciais sem postar

Credenciais ficam em ./credenciais (NUNCA versionar/compartilhar).
Cada corte so e postado UMA vez por plataforma (status gravado em fila.json).
"""
import json, os, sys, time, threading, datetime as dt

BASE = os.path.dirname(os.path.abspath(__file__))
CRED = os.path.join(BASE, "credenciais")
FILA = os.path.join(BASE, "fila.json")
from creditos import carregar_config
PCFG = carregar_config("publicacao.json")
PLATS = ("youtube", "instagram", "tiktok")
_lock = threading.Lock()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(*a):
    print(time.strftime("[%H:%M:%S]"), "[post]", *a, flush=True)


def _load():
    try:
        return json.load(open(FILA, encoding="utf-8"))
    except Exception:
        return []


def _save(f):
    tmp = FILA + ".tmp"
    json.dump(f, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, FILA)


def ativas():
    return [p for p in PLATS if PCFG["plataformas"].get(p, {}).get("ativo")]


# ------------------------------------------------------------------ fila
def enfileirar(json_path):
    meta = json.load(open(json_path, encoding="utf-8"))
    with _lock:
        f = _load()
        if any(e["json"] == json_path for e in f):
            return
        aprovado = PCFG["modo"] == "auto" and meta.get("score", 99) >= PCFG.get("score_minimo", 0)
        f.append({"json": json_path, "nome": os.path.basename(os.path.splitext(json_path)[0]),
                  "criado": time.time(), "aprovado": aprovado,
                  "status": {p: "pendente" for p in PLATS}, "links": {}, "erros": {}})
        _save(f)
    log(("na fila (auto): " if aprovado else "aguardando aprovacao: ") + os.path.basename(json_path))


def legenda(meta, plat):
    tags = meta.get("hashtags", [])
    t, d = meta.get("titulo", ""), meta.get("descricao", "")
    if plat == "youtube":
        tags_yt = [x for x in tags if x.lower() not in ("#reels", "#tiktok", "#fyp")]
        tags_yt += ["#shorts"] if "#shorts" not in [x.lower() for x in tags_yt] else []
        return {"title": t[:100], "description": (d + "\n\n" + " ".join(tags_yt))[:4900],
                "tags": [x.lstrip("#") for x in tags][:15]}
    if plat == "instagram":
        return {"caption": (t + "\n\n" + d + "\n\n" + " ".join(tags[:30]))[:2200]}
    if plat == "tiktok":
        return {"title": (t + " " + " ".join(tags))[:2200]}


# ------------------------------------------------------------------ YouTube
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
             "https://www.googleapis.com/auth/yt-analytics.readonly"]  # analytics: retencao p/ aprendizado.py


def _yt_creds(interactive=False):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    tok = os.path.join(CRED, "youtube_token.json")
    # carrega com os escopos que o token REALMENTE tem (token antigo sem analytics continua valendo)
    creds = Credentials.from_authorized_user_file(tok) if os.path.exists(tok) else None
    if interactive and creds and not set(YT_SCOPES) <= set(creds.scopes or []):
        creds = None  # login de novo para ganhar os escopos que faltam
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("YouTube nao autorizado: rode  python publicador.py login youtube")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(os.path.join(CRED, "youtube_client_secret.json"), YT_SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    open(tok, "w").write(creds.to_json())
    return creds


def _yt_service(interactive=False):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=_yt_creds(interactive), cache_discovery=False)


def post_youtube(mp4, meta):
    c = PCFG["plataformas"]["youtube"]
    L = legenda(meta, "youtube")
    if c.get("modo") == "navegador":  # YouTube Studio com a sua conta logada (fora da cota da API)
        import youtube_web
        return youtube_web.postar(mp4, L["title"], L["description"])
    from googleapiclient.http import MediaFileUpload
    body = {"snippet": {"title": L["title"], "description": L["description"], "tags": L["tags"],
                        "categoryId": "20", "defaultLanguage": "pt-BR", "defaultAudioLanguage": "pt-BR"},
            "status": {"privacyStatus": c.get("privacidade", "public"), "selfDeclaredMadeForKids": False}}
    req = _yt_service().videos().insert(part="snippet,status", body=body,
                                        media_body=MediaFileUpload(mp4, chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return f"https://youtube.com/shorts/{resp['id']}"


# ------------------------------------------------------------------ Instagram (Reels, upload resumable)
def post_instagram(mp4, meta):
    if PCFG["plataformas"]["instagram"].get("modo") == "navegador":  # instagram.com com a sua conta logada
        import instagram_web
        return instagram_web.postar(mp4, legenda(meta, "instagram")["caption"])
    import requests
    c = json.load(open(os.path.join(CRED, "instagram.json"), encoding="utf-8"))
    host, ver, uid, tok = c.get("host", "graph.instagram.com"), c.get("api_version", "v21.0"), c["ig_user_id"], c["access_token"]
    api = f"https://{host}/{ver}"
    r = requests.post(f"{api}/{uid}/media", data={"media_type": "REELS", "upload_type": "resumable",
                                                   "caption": legenda(meta, "instagram")["caption"],
                                                   "share_to_feed": "true", "access_token": tok}, timeout=60)
    r.raise_for_status()
    cid, uri = r.json()["id"], r.json()["uri"]
    size = os.path.getsize(mp4)
    with open(mp4, "rb") as fh:
        u = requests.post(uri, data=fh, headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                                  "file_size": str(size)}, timeout=600)
    u.raise_for_status()
    for _ in range(90):  # ate ~15 min processando
        s = requests.get(f"{api}/{cid}", params={"fields": "status_code,status", "access_token": tok}, timeout=30).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram recusou o video: {s}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram nao terminou de processar a tempo")
    p = requests.post(f"{api}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60)
    p.raise_for_status()
    mid = p.json()["id"]
    link = requests.get(f"{api}/{mid}", params={"fields": "permalink", "access_token": tok}, timeout=30).json()
    return link.get("permalink", f"instagram media {mid}")


# ------------------------------------------------------------------ TikTok (Content Posting API, Direct Post)
TT = "https://open.tiktokapis.com"


def _tt_token():
    import requests
    path = os.path.join(CRED, "tiktok.json")
    c = json.load(open(path, encoding="utf-8"))
    if not c.get("access_token"):
        raise RuntimeError("TikTok nao autorizado: rode  python publicador.py login tiktok")
    if time.time() > c.get("expires_at", 0) - 300:
        r = requests.post(f"{TT}/v2/oauth/token/", data={"client_key": c["client_key"], "client_secret": c["client_secret"],
                                                          "grant_type": "refresh_token", "refresh_token": c["refresh_token"]},
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30).json()
        if "access_token" not in r:
            raise RuntimeError(f"TikTok: falha ao renovar token: {r}")
        c.update(access_token=r["access_token"], refresh_token=r.get("refresh_token", c["refresh_token"]),
                 expires_at=time.time() + r.get("expires_in", 86400))
        json.dump(c, open(path, "w", encoding="utf-8"), indent=2)
    return c["access_token"]


def post_tiktok(mp4, meta):
    if PCFG["plataformas"]["tiktok"].get("modo") == "navegador":
        import tiktok_web  # posta pelo TikTok Studio com a sua conta logada (titulo + hashtags, publico)
        return tiktok_web.postar(mp4, legenda(meta, "tiktok")["title"])
    import requests
    tok = _tt_token()
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"}
    info = requests.post(f"{TT}/v2/post/publish/creator_info/query/", headers=H, timeout=30).json()
    opts = info.get("data", {}).get("privacy_level_options", [])
    want = PCFG["plataformas"]["tiktok"].get("privacidade", "PUBLIC_TO_EVERYONE")
    priv = want if want in opts else ("SELF_ONLY" if "SELF_ONLY" in opts else (opts or ["SELF_ONLY"])[0])
    if priv != want:
        log(f"TikTok: privacidade '{want}' indisponivel (app sem auditoria?) -> usando {priv}")
    size = os.path.getsize(mp4)
    chunk = size if size <= 64 * 1024 * 1024 else 32 * 1024 * 1024
    n = max(1, size // chunk)
    src = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": n}
    modo = "direto"
    init = {"error": {"code": "unaudited_forcado"}} if PCFG["plataformas"]["tiktok"].get("modo") == "rascunho" else \
        requests.post(f"{TT}/v2/post/publish/video/init/", headers=H, timeout=60, json={
            "post_info": {"title": legenda(meta, "tiktok")["title"], "privacy_level": priv,
                          "disable_duet": False, "disable_comment": False, "disable_stitch": False},
            "source_info": src}).json()
    if "unaudited" in str(init.get("error", {}).get("code", "")):
        # app sem auditoria: Direct Post so em conta privada. Envia como RASCUNHO para a caixa de entrada
        # do TikTok (notificacao no app) -> o dono abre, ja escolhe publico e posta com 1 toque.
        modo = "rascunho"
        init = requests.post(f"{TT}/v2/post/publish/inbox/video/init/", headers=H, timeout=60,
                             json={"source_info": src}).json()
    if init.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok init: {init['error']}")
    up, pid = init["data"]["upload_url"], init["data"]["publish_id"]
    with open(mp4, "rb") as fh:
        for i in range(n):
            a = i * chunk
            b = size - 1 if i == n - 1 else a + chunk - 1
            fh.seek(a)
            data = fh.read(b - a + 1)
            requests.put(up, data=data, timeout=600, headers={"Content-Type": "video/mp4",
                                                              "Content-Range": f"bytes {a}-{b}/{size}"}).raise_for_status()
    for _ in range(60):
        s = requests.post(f"{TT}/v2/post/publish/status/fetch/", headers=H, json={"publish_id": pid}, timeout=30).json()
        st = s.get("data", {}).get("status")
        if st == "PUBLISH_COMPLETE":
            return f"tiktok publish_id {pid} ({priv})"
        if st == "SEND_TO_USER_INBOX":
            titulo = legenda(meta, "tiktok")["title"]
            open(os.path.join(BASE, "tiktok_legendas.txt"), "a", encoding="utf-8").write(
                f"{time.strftime('%d/%m %H:%M')} | {os.path.basename(mp4)}\n{titulo}\n\n")
            return f"tiktok rascunho na caixa de entrada (publish_id {pid}) - legenda em tiktok_legendas.txt"
        if st == "FAILED":
            raise RuntimeError(f"TikTok falhou: {s}")
        time.sleep(10)
    return f"tiktok publish_id {pid} (processando)"


POSTERS = {"youtube": post_youtube, "instagram": post_instagram, "tiktok": post_tiktok}


# ------------------------------------------------------------------ agendador
def _hoje(e_list, plat):
    d0 = dt.datetime.now().replace(hour=0, minute=0, second=0).timestamp()
    ts = [e.get("postado_em", {}).get(plat, 0) for e in e_list]
    return sum(1 for t in ts if t >= d0), max(ts + [0])


ESTADO = os.path.join(BASE, "estado_post.json")


def _estado():
    try:
        return json.load(open(ESTADO, encoding="utf-8"))
    except Exception:
        return {}


def _proximo_reset_cota_yt():
    # cota da YouTube API zera a meia-noite do horario do Pacifico
    from zoneinfo import ZoneInfo
    pt = dt.datetime.now(ZoneInfo("America/Los_Angeles"))
    return (pt.replace(hour=0, minute=5, second=0, microsecond=0) + dt.timedelta(days=1)).timestamp()


def _eh_cota(ex):
    s = str(ex)
    return any(k in s for k in ("quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded"))


def _intervalo_s(plat):
    """Intervalo entre posts com variacao aleatoria (padrao humano, nao robo de relogio).
    publicacao.json: "intervalo_min" (global) e "variacao_pct"; cada plataforma pode sobrescrever."""
    import random
    c = PCFG["plataformas"][plat]
    base = c.get("intervalo_min", PCFG.get("intervalo_min", 10))
    v = c.get("variacao_pct", PCFG.get("variacao_pct", 30)) / 100
    return max(60, base * 60 * random.uniform(1 - v, 1 + v))


def ciclo():
    with _lock:
        f = _load()
    est = _estado()
    for plat in ativas():
        if time.time() < est.get(f"{plat}_pausa_ate", 0):
            continue
        c = PCFG["plataformas"][plat]
        n_hoje, ultimo = _hoje(f, plat)
        if n_hoje >= (c.get("max_por_dia") or 10 ** 9) or time.time() < est.get(f"{plat}_proximo", 0):
            continue
        cand = [e for e in f if e["aprovado"] and e["status"].get(plat) == "pendente"
                and time.time() - e["criado"] < PCFG.get("validade_horas", 24) * 3600]
        if not cand:
            continue
        # melhor score primeiro (hype mais forte), desempate pelo mais recente
        # prioridade = score do detector + o que o canal aprendeu com as metricas reais (aprendizado.py)
        def score(e):
            try:
                m = json.load(open(e["json"], encoding="utf-8"))
            except Exception:
                return 0
            try:
                import aprendizado
                return aprendizado.prioridade(m)
            except Exception:
                return m.get("score", 0)
        e = max(cand, key=lambda x: (score(x), x["criado"]))
        meta = json.load(open(e["json"], encoding="utf-8"))
        mp4 = meta["arquivo"]
        log(f"postando no {plat}: {e['nome']}")
        with _lock:
            f = _load()
            ent = next(x for x in f if x["json"] == e["json"])
            ent["status"][plat] = "enviando"
            _save(f)
        try:
            link = POSTERS[plat](mp4, meta)
            res, err = "postado", None
            log(f"✅ {plat}: {link}")
        except Exception as ex:
            if plat == "youtube" and _eh_cota(ex):
                # cota/limite do dia: devolve p/ fila e pausa ate a cota zerar
                ate = _proximo_reset_cota_yt()
                est = _estado(); est["youtube_pausa_ate"] = ate
                json.dump(est, open(ESTADO, "w", encoding="utf-8"), indent=2)
                with _lock:
                    f = _load()
                    next(x for x in f if x["json"] == e["json"])["status"][plat] = "pendente"
                    _save(f)
                log(f"⏸ youtube: cota do dia esgotada -> pausado ate {time.strftime('%d/%m %H:%M', time.localtime(ate))}")
                continue
            if type(ex).__name__ == "PrecisaHumano":
                # login expirou / CAPTCHA / limite da plataforma: NAO insiste (evita ban). Devolve p/ fila e pausa.
                h = PCFG.get("pausa_bloqueio_horas", 1)
                est = _estado(); est[f"{plat}_pausa_ate"] = time.time() + h * 3600
                json.dump(est, open(ESTADO, "w", encoding="utf-8"), indent=2)
                with _lock:
                    f = _load()
                    next(x for x in f if x["json"] == e["json"])["status"][plat] = "pendente"
                    _save(f)
                log(f"⏸ {plat}: {ex} -> pausado {h}h. Resolva no navegador: python {plat}_web.py login")
                continue
            link, res, err = None, "erro", str(ex)[:500]
            log(f"❌ {plat}: {err}")
        with _lock:
            f = _load()
            ent = next(x for x in f if x["json"] == e["json"])
            ent["status"][plat] = res
            ent.setdefault("postado_em", {})[plat] = time.time()  # conta no intervalo mesmo se falhar (evita martelar API)
            if link:
                ent["links"][plat] = link
            if err:
                ent["erros"][plat] = err
            _save(f)
        est = _estado(); est[f"{plat}_proximo"] = time.time() + _intervalo_s(plat)
        json.dump(est, open(ESTADO, "w", encoding="utf-8"), indent=2)
        log(f"⏭ proximo post no {plat} as {time.strftime('%H:%M', time.localtime(est[f'{plat}_proximo']))}")


def rodar():
    import creditos
    creditos.mostrar()
    log("postador ativo:", ", ".join(ativas()) or "(nenhuma plataforma ativa em publicacao.json)", "| modo:", PCFG["modo"])

    def aprender():  # coleta metricas e reaprende a cada N horas (thread separada, nao trava a postagem)
        import aprendizado
        while True:
            aprendizado.ciclo()
            time.sleep(PCFG.get("aprendizado_horas", 6) * 3600)
    if "youtube" in ativas():
        threading.Thread(target=aprender, daemon=True).start()
    while True:
        try:
            ciclo()
        except Exception as ex:
            log("erro no ciclo:", ex)
        time.sleep(30)


def agora():
    """Posta ja (sem intervalo) todos os aprovados pendentes, melhor score primeiro, ate a cota/limite."""
    PCFG["validade_horas"] = 10 ** 6
    for plat in ativas():
        PCFG["plataformas"][plat]["intervalo_min"] = 0
        PCFG["plataformas"][plat]["max_por_dia"] = 10 ** 6
    global _intervalo_s
    _intervalo_s = lambda plat: 0  # noqa: E731
    est = _estado()
    for k in [k for k in est if k.endswith("_proximo")]:
        est.pop(k)
    json.dump(est, open(ESTADO, "w", encoding="utf-8"), indent=2)
    while True:
        antes = json.dumps(_load())
        ciclo()
        f = _load()
        pend = [e for e in f if e["aprovado"] and any(e["status"].get(p) == "pendente" for p in ativas())]
        pausadas = all(time.time() < _estado().get(f"{p}_pausa_ate", 0) for p in ativas())
        if not pend or pausadas or json.dumps(f) == antes:
            break
    restam = [e["nome"] for e in _load() if e["aprovado"] and any(e["status"].get(p) == "pendente" for p in ativas())]
    log(f"fim do 'agora'. Ficaram na fila: {len(restam)}")


def listar():
    for e in _load():
        st = " ".join(f"{p[:2]}:{e['status'][p]}" for p in PLATS)
        print(("✔" if e["aprovado"] else "…"), e["nome"], "|", st, "|", " ".join(e.get("links", {}).values()))


def aprovar(alvo, valor=True):
    with _lock:
        f = _load()
        for e in f:
            if alvo == "todos" or alvo in e["nome"]:
                e["aprovado"] = valor
                if not valor:
                    e["status"] = {p: "rejeitado" for p in PLATS}
                print(("aprovado: " if valor else "rejeitado: ") + e["nome"])
        _save(f)


def login(plat):
    os.makedirs(CRED, exist_ok=True)
    if plat == "youtube":
        yt = _yt_service(interactive=True)
        ch = yt.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        print("YouTube autorizado no canal:", ch)
    elif plat == "tiktok":
        import secrets, hashlib, base64, urllib.parse, webbrowser, requests
        from http.server import HTTPServer, BaseHTTPRequestHandler
        path = os.path.join(CRED, "tiktok.json")
        c = json.load(open(path, encoding="utf-8"))
        redirect = c.get("redirect_uri", "http://localhost:8765/callback/")
        verifier = secrets.token_urlsafe(48)
        challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok desktop: hex(SHA256)
        state = secrets.token_urlsafe(16)
        url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
            "client_key": c["client_key"], "response_type": "code", "scope": "user.info.basic,video.publish,video.upload",
            "redirect_uri": redirect, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        got = {}

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                got.update({k: v[0] for k, v in q.items()})
                self.send_response(200); self.end_headers()
                self.wfile.write("Pode fechar esta aba e voltar ao terminal.".encode())

            def log_message(self, *a):
                pass
        port = urllib.parse.urlparse(redirect).port or 8765
        srv = HTTPServer(("localhost", port), H)
        print("Abrindo o navegador para autorizar o TikTok...")
        webbrowser.open(url)
        while "code" not in got and "error" not in got:
            srv.handle_request()
        if got.get("state") != state or "code" not in got:
            raise SystemExit(f"TikTok: autorizacao falhou: {got}")
        r = requests.post(f"{TT}/v2/oauth/token/", headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
            "client_key": c["client_key"], "client_secret": c["client_secret"], "code": got["code"],
            "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier}, timeout=30).json()
        if "access_token" not in r:
            raise SystemExit(f"TikTok: troca de token falhou: {r}")
        c.update(access_token=r["access_token"], refresh_token=r["refresh_token"], expires_at=time.time() + r["expires_in"])
        json.dump(c, open(path, "w", encoding="utf-8"), indent=2)
        print("TikTok autorizado.")
    else:
        print("Instagram usa token de longa duracao: preencha credenciais/instagram.json (ver GUIA_POSTAGEM.md)")


def checar():
    for p in PLATS:
        if p not in ativas():
            print(f"{p}: desativado em publicacao.json"); continue
        try:
            if p == "youtube":
                ch = _yt_service().channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
                print("youtube: OK ->", ch)
            elif p == "instagram":
                import requests
                c = json.load(open(os.path.join(CRED, "instagram.json"), encoding="utf-8"))
                r = requests.get(f"https://{c.get('host', 'graph.instagram.com')}/{c.get('api_version', 'v21.0')}/{c['ig_user_id']}",
                                 params={"fields": "username", "access_token": c["access_token"]}, timeout=30).json()
                print("instagram:", "OK -> @" + r["username"] if "username" in r else r)
            elif p == "tiktok":
                import requests
                r = requests.post(f"{TT}/v2/post/publish/creator_info/query/", timeout=30,
                                  headers={"Authorization": f"Bearer {_tt_token()}", "Content-Type": "application/json"}).json()
                d = r.get("data", {})
                print("tiktok:", f"OK -> @{d.get('creator_username')} privacidades={d.get('privacy_level_options')}" if d else r)
        except Exception as ex:
            print(f"{p}: ERRO -> {ex}")


if __name__ == "__main__":
    a = sys.argv[1:] or ["listar"]
    {"rodar": lambda: rodar(), "agora": lambda: agora(), "listar": lambda: listar(), "checar": lambda: checar(),
     "aprovar": lambda: aprovar(a[1]), "rejeitar": lambda: aprovar(a[1], False),
     "login": lambda: login(a[1])}.get(a[0], lambda: print(__doc__))()
