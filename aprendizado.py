"""
Aprendizado com as metricas dos videos postados (YouTube).

  python aprendizado.py            # coleta metricas + analisa + gera relatorio
  python aprendizado.py coletar    # so coleta (snapshot das metricas)
  python aprendizado.py analisar   # so reanalisa o que ja foi coletado
  python aprendizado.py relatorio  # mostra o ultimo relatorio

Como funciona:
  1. COLETA: para cada corte postado no YouTube busca views/likes/comentarios (e, se o token tiver
     o escopo yt-analytics.readonly, retencao media e inscritos ganhos). Cada execucao grava um
     "snapshot" com a idade do video -> da pra comparar todos na mesma idade (48h).
  2. ANALISE: calcula um indice de desempenho por video (views normalizadas p/ 48h + likes + retencao),
     separa os que DERAM CERTO (top 30%) e os que NAO DERAM (bottom 30%) e mede o efeito de cada
     caracteristica (gatilho chat/audio, duracao, horario, titulo com emoji/aspas/tamanho, palavras,
     hashtags, score do detector). Pede ao Claude (haiku) regras curtas comparando os titulos.
  3. APLICACAO (automatica, a partir de MIN_VIDEOS videos maduros):
     - publicador.py usa prever() para escolher qual corte postar primeiro;
     - cortador.py injeta dicas_prompt() no prompt que cria titulo/descricao/hashtags.
Arquivos: metricas.json (historico bruto), aprendizado.json (modelo), APRENDIZADO.md (relatorio).
"""
import json, math, os, re, shutil, subprocess, sys, time, datetime as dt
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
FILA = os.path.join(BASE, "fila.json")
METR = os.path.join(BASE, "metricas.json")
APRE = os.path.join(BASE, "aprendizado.json")
RELAT = os.path.join(BASE, "APRENDIZADO.md")
MIN_IDADE_H = 12      # video mais novo que isso ainda nao entra na analise
REF_H = 48            # compara todos os videos como se tivessem 48h
MIN_VIDEOS = 8        # minimo de videos maduros para o aprendizado comecar a influenciar
SHRINK = 3            # suavizacao: grupos pequenos puxados para a media (evita conclusao por sorte)
STOP = set("de da do das dos a o as os e em no na nos nas um uma pra pro para com que se ao mas por mais ja eu ele ela vc voce isso esse essa".split())

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(*a):
    print(time.strftime("[%H:%M:%S]"), "[aprende]", *a, flush=True)


def _ler(p, padrao):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return padrao


def _gravar(p, obj):
    tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _vid(link):
    if not link or "teste" in link.lower():
        return None
    m = re.search(r"(?:shorts/|v=|youtu\.be/)([\w-]{11})", link)
    return m.group(1) if m else None


# ================================================================ COLETA
def coletar():
    from publicador import _yt_service
    fila = _ler(FILA, [])
    db = _ler(METR, {})
    alvos = {}
    for e in fila:
        vid = _vid(e.get("links", {}).get("youtube"))
        if vid:
            alvos[vid] = e
    if not alvos:
        log("nenhum video postado no YouTube ainda")
        return db
    yt = _yt_service()
    agora = time.time()
    ids = list(alvos)
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="statistics,contentDetails,snippet", id=",".join(ids[i:i + 50])).execute()
        vistos = set()
        for it in r.get("items", []):
            vid = it["id"]; vistos.add(vid)
            e = alvos[vid]
            s = it.get("statistics", {})
            ent = db.setdefault(vid, {"nome": e["nome"], "json": e["json"], "snaps": []})
            ent["postado_em"] = e.get("postado_em", {}).get("youtube") or ent.get("postado_em")
            ent["titulo_yt"] = it["snippet"]["title"]
            ent["duracao_yt"] = it.get("contentDetails", {}).get("duration")
            idade = (agora - (ent["postado_em"] or agora)) / 3600
            ent["snaps"].append({"t": agora, "idade_h": round(idade, 2), "views": int(s.get("viewCount", 0)),
                                 "likes": int(s.get("likeCount", 0)), "coments": int(s.get("commentCount", 0))})
            ent["snaps"] = ent["snaps"][-60:]
        for vid in ids[i:i + 50]:
            if vid not in vistos:
                db.setdefault(vid, {"nome": alvos[vid]["nome"], "json": alvos[vid]["json"], "snaps": []})["removido"] = True
    _analytics(db, ids)
    _gravar(METR, db)
    log(f"metricas coletadas de {len(ids)} videos")
    return db


def _analytics(db, ids):
    """Retencao/inscritos via YouTube Analytics. Precisa do escopo yt-analytics.readonly (login youtube de novo)."""
    try:
        from publicador import _yt_creds
        from googleapiclient.discovery import build
        creds = _yt_creds()
        if "https://www.googleapis.com/auth/yt-analytics.readonly" not in (creds.scopes or []):
            return
        ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        hoje = dt.date.today().isoformat()
        for i in range(0, len(ids), 40):
            r = ya.reports().query(ids="channel==MINE", startDate="2020-01-01", endDate=hoje,
                                   metrics="views,averageViewDuration,averageViewPercentage,subscribersGained",
                                   dimensions="video", filters="video==" + ",".join(ids[i:i + 40])).execute()
            cols = [c["name"] for c in r.get("columnHeaders", [])]
            for row in r.get("rows", []):
                d = dict(zip(cols, row))
                if d["video"] in db:
                    db[d["video"]]["analytics"] = {"ret_pct": d.get("averageViewPercentage"),
                                                   "ret_s": d.get("averageViewDuration"),
                                                   "inscritos": d.get("subscribersGained")}
    except Exception as ex:
        log("analytics indisponivel (ok, segue so com views/likes):", str(ex)[:160])


# ================================================================ FEATURES
def features(meta, postado_em=None):
    t = meta.get("titulo", "") or ""
    dur = (meta.get("end", 0) - meta.get("start", 0)) or None
    f = {}
    cz, az = meta.get("chat_z") or 0, meta.get("audio_z") or 0
    f["gatilho"] = "chat+audio" if cz >= 3 and az >= 3 else ("audio" if az > cz else "chat")
    if dur:
        f["duracao"] = "<35s" if dur < 35 else ("35-50s" if dur <= 50 else ">50s")
    n = len(t)
    f["titulo_tam"] = "curto(<40)" if n < 40 else ("medio(40-60)" if n <= 60 else "longo(>60)")
    f["titulo_emoji"] = "sim" if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", t) else "nao"
    f["titulo_aspas"] = "sim" if re.search(r"[\"'“”‘’]", t) else "nao"
    f["titulo_pergunta"] = "sim" if "?" in t else "nao"
    f["titulo_caps"] = "sim" if re.search(r"\b[A-ZÁÉÍÓÚÃÕÇ]{4,}\b", t) else "nao"
    f["streamer"] = meta.get("streamer") or "loud"  # cortes antigos (sem a chave) eram todos do LOUD Coringa
    f["cita_streamer"] ="sim" if re.search(r"coringa|loud|bistec|davy|alanzoka|alan\b", t, re.I) else "nao"
    if postado_em:
        h = dt.datetime.fromtimestamp(postado_em).hour
        f["horario"] = "madrugada(0-6)" if h < 6 else "manha(6-12)" if h < 12 else "tarde(12-18)" if h < 18 else "noite(18-24)"
    words = {w for w in re.findall(r"[a-zà-ú0-9]{3,}", t.lower()) if w not in STOP}
    tags = {x.lower() for x in meta.get("hashtags", [])}
    return f, words, tags


# ================================================================ ANALISE
def _views_ref(snaps):
    """views estimadas com REF_H horas de idade (interpola entre snapshots; extrapola linear se necessario)."""
    sn = sorted((s for s in snaps if s["idade_h"] > 0), key=lambda s: s["idade_h"])
    if not sn or sn[-1]["idade_h"] < MIN_IDADE_H:
        return None
    for a, b in zip(sn, sn[1:]):
        if a["idade_h"] <= REF_H <= b["idade_h"]:
            k = (REF_H - a["idade_h"]) / max(1e-6, b["idade_h"] - a["idade_h"])
            return a["views"] + k * (b["views"] - a["views"])
    s = sn[0] if sn[0]["idade_h"] > REF_H else sn[-1]
    # Shorts crescem rapido no inicio: extrapolacao por raiz (mais conservadora que linear)
    return s["views"] * math.sqrt(REF_H / s["idade_h"])


def _desempenho(ent):
    v = _views_ref(ent.get("snaps", []))
    if v is None:
        return None
    last = ent["snaps"][-1]
    like_rate = last["likes"] / max(1, last["views"])
    p = math.log1p(v) + min(like_rate, 0.15) * 6     # like rate 5% ~ +0.3
    ret = (ent.get("analytics") or {}).get("ret_pct")
    if ret is not None:
        p += (ret - 70) / 100                          # Shorts: retencao ~70% e o "normal"
    return p, v, like_rate, ret


def _efeito(grupos, media):
    out = {}
    for val, ps in grupos.items():
        n = len(ps)
        suav = (sum(ps) + SHRINK * media) / (n + SHRINK)
        out[val] = {"n": n, "efeito": round(suav - media, 3)}
    return out


def _regras_claude(top, flop):
    claude = shutil.which("claude")
    if not claude or len(top) < 3 or len(flop) < 3:
        return []
    fmt = lambda L: "\n".join(f"- \"{x['titulo']}\" ({int(x['views48'])} views/48h) fala: {x.get('fala', '')[:120]}" for x in L)
    prompt = ("Voce analisa desempenho de YouTube Shorts (cortes de live de GTA 6, pt-BR).\n"
              f"DERAM CERTO:\n{fmt(top)}\n\nNAO DERAM CERTO:\n{fmt(flop)}\n\n"
              "Compare e escreva de 3 a 6 regras curtas e acionaveis (max 18 palavras cada) para os PROXIMOS "
              "titulos/escolha de cortes. Baseie-se SO nos dados acima, sem generalidades. "
              "Responda SOMENTE um JSON: {\"regras\": [\"...\"]}")
    try:
        r = subprocess.run([claude, "-p", "--model", "haiku", "--output-format", "text"], input=prompt,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return [s.strip() for s in json.loads(re.search(r"\{.*\}", r.stdout, re.S).group(0))["regras"]][:6]
    except Exception as ex:
        log("regras via Claude falharam:", str(ex)[:120])
        return []


def analisar():
    db = _ler(METR, {})
    vids = []
    for vid, ent in db.items():
        if ent.get("removido"):
            continue
        d = _desempenho(ent)
        meta = _ler(ent["json"], None)
        if d is None or meta is None:
            continue
        f, words, tags = features(meta, ent.get("postado_em"))
        vids.append({"id": vid, "perf": d[0], "views48": d[1], "like_rate": d[2], "ret": d[3],
                     "titulo": meta.get("titulo", ent.get("titulo_yt", "")), "fala": meta.get("transcricao", ""),
                     "score": meta.get("score", 0), "f": f, "words": words, "tags": tags})
    if not vids:
        log("ainda nao ha videos maduros (>= %dh) para analisar" % MIN_IDADE_H)
        return None
    vids.sort(key=lambda x: -x["perf"])
    media = sum(v["perf"] for v in vids) / len(vids)
    k = max(1, round(len(vids) * 0.3))
    top, flop = vids[:k], vids[-k:]

    efeitos = {}
    for feat in sorted({kk for v in vids for kk in v["f"]}):
        g = defaultdict(list)
        for v in vids:
            if feat in v["f"]:
                g[v["f"][feat]].append(v["perf"])
        if len(g) > 1:
            efeitos[feat] = _efeito(g, media)
    def _tokens(chave, minimo=2):
        g = defaultdict(list)
        for v in vids:
            for w in v[chave]:
                g[w].append(v["perf"])
        e = {w: x for w, x in _efeito({w: p for w, p in g.items() if len(p) >= minimo}, media).items()}
        return dict(sorted(e.items(), key=lambda kv: -abs(kv[1]["efeito"]))[:25])
    palavras, hashtags = _tokens("words"), _tokens("tags")

    # o score do detector preve sucesso? (correlacao de Spearman simples)
    corr = None
    if len(vids) >= 5:
        rs = {v["id"]: i for i, v in enumerate(sorted(vids, key=lambda x: x["score"]))}
        rp = {v["id"]: i for i, v in enumerate(sorted(vids, key=lambda x: x["perf"]))}
        n = len(vids)
        corr = round(1 - 6 * sum((rs[i] - rp[i]) ** 2 for i in rs) / (n * (n * n - 1)), 2)

    regras = _regras_claude(top, flop)
    modelo = {"atualizado": time.time(), "n_videos": len(vids), "ativo": len(vids) >= MIN_VIDEOS,
              "media_perf": round(media, 3), "efeitos": efeitos, "palavras": palavras, "hashtags": hashtags,
              "corr_score_detector": corr, "regras": regras,
              "top": [{"titulo": v["titulo"], "views48": int(v["views48"]), "id": v["id"]} for v in top],
              "flop": [{"titulo": v["titulo"], "views48": int(v["views48"]), "id": v["id"]} for v in flop]}
    _gravar(APRE, modelo)
    _relatorio(modelo, vids)
    log(f"analise feita: {len(vids)} videos maduros | aprendizado {'ATIVO' if modelo['ativo'] else f'em observacao (precisa {MIN_VIDEOS})'}")
    return modelo


def _relatorio(m, vids):
    L = [f"# Aprendizado do canal — {dt.datetime.fromtimestamp(m['atualizado']):%d/%m/%Y %H:%M}", "",
         f"Videos analisados: **{m['n_videos']}** (com {MIN_IDADE_H}h ou mais) · views comparadas em {REF_H}h · "
         f"aprendizado **{'ATIVO (influencia postagem e titulos)' if m['ativo'] else f'em observacao (ativa com {MIN_VIDEOS} videos)'}**", ""]
    if m["regras"]:
        L += ["## Regras aprendidas (Claude comparando os melhores x piores)", ""] + [f"- {r}" for r in m["regras"]] + [""]
    L += ["## ✅ Deram certo", ""] + [f"- {x['views48']} views — {x['titulo']} (https://youtube.com/shorts/{x['id']})" for x in m["top"]]
    L += ["", "## ❌ Nao deram certo", ""] + [f"- {x['views48']} views — {x['titulo']} (https://youtube.com/shorts/{x['id']})" for x in m["flop"]]
    L += ["", "## O que pesa no desempenho", "", "Efeito > 0 = acima da media do canal; n = quantos videos.", ""]
    for feat, g in m["efeitos"].items():
        L.append(f"- **{feat}**: " + " · ".join(f"{val} {d['efeito']:+.2f} (n={d['n']})" for val, d in sorted(g.items(), key=lambda kv: -kv[1]["efeito"])))
    if m["palavras"]:
        L += ["", "**Palavras no titulo**: " + " · ".join(f"{w} {d['efeito']:+.2f}" for w, d in list(m["palavras"].items())[:15])]
    if m["hashtags"]:
        L += ["", "**Hashtags**: " + " · ".join(f"{w} {d['efeito']:+.2f}" for w, d in list(m["hashtags"].items())[:12])]
    if m["corr_score_detector"] is not None:
        c = m["corr_score_detector"]
        L += ["", f"**Score do detector x desempenho real**: correlacao {c:+.2f} — " +
              ("o detector esta acertando os melhores momentos." if c > 0.3 else
               "o detector NAO esta prevendo bem o que viraliza; o aprendizado passa a pesar mais na escolha." if c < 0.1 else
               "relacao fraca, o aprendizado complementa.")]
    open(RELAT, "w", encoding="utf-8").write("\n".join(L) + "\n")


# ================================================================ APLICACAO
def _modelo():
    m = _ler(APRE, None)
    return m if m and m.get("ativo") else None


def prever(meta, postado_em=None):
    """Ajuste (~ -1..+1) de quanto este corte tende a performar acima/abaixo da media do canal."""
    m = _modelo()
    if not m:
        return 0.0
    f, words, tags = features(meta, postado_em or time.time())
    adj = sum(m["efeitos"].get(k, {}).get(v, {}).get("efeito", 0) for k, v in f.items())
    adj += 0.5 * sum(m["palavras"].get(w, {}).get("efeito", 0) for w in words)
    adj += 0.3 * sum(m["hashtags"].get(t, {}).get("efeito", 0) for t in tags)
    return max(-1.5, min(1.5, adj))


def prioridade(meta):
    """Usado pelo publicador para ordenar a fila: score do detector + aprendizado.
    Quanto pior o detector preve o sucesso real, mais o aprendizado pesa."""
    m = _modelo()
    score = meta.get("score", 0) or 0
    if not m:
        return score
    c = m.get("corr_score_detector")
    peso = 3.0 if c is None else (4.0 if c < 0.1 else 2.5 if c < 0.3 else 1.5)
    return score + peso * prever(meta)


def dicas_prompt():
    """Texto curto para o prompt que gera titulo/descricao/hashtags (cortador.py)."""
    m = _ler(APRE, None)
    if not m or m.get("n_videos", 0) < 4:
        return ""
    p = ["\nAPRENDIZADO DO CANAL (dados reais dos nossos Shorts; siga isto):"]
    p += [f"- {r}" for r in m.get("regras", [])]
    if m.get("top"):
        p.append("Titulos que DERAM CERTO: " + " | ".join(f"\"{x['titulo']}\"" for x in m["top"][:5]))
    if m.get("flop"):
        p.append("Titulos que NAO deram certo (evite o estilo): " + " | ".join(f"\"{x['titulo']}\"" for x in m["flop"][:4]))
    boas = [t for t, d in m.get("hashtags", {}).items() if d["efeito"] > 0.1][:5]
    if boas:
        p.append("Hashtags com melhor resultado: " + " ".join(boas))
    return "\n".join(p) + "\n"


def ciclo():
    """Chamado periodicamente pelo publicador."""
    try:
        coletar()
        analisar()
    except Exception as ex:
        log("erro no ciclo de aprendizado:", str(ex)[:300])


if __name__ == "__main__":
    a = (sys.argv[1:] or ["tudo"])[0]
    if a == "coletar":
        coletar()
    elif a == "analisar":
        analisar()
    elif a == "relatorio":
        print(open(RELAT, encoding="utf-8").read() if os.path.exists(RELAT) else "sem relatorio ainda")
    else:
        coletar(); analisar()
        if os.path.exists(RELAT):
            print("\n" + open(RELAT, encoding="utf-8").read())
