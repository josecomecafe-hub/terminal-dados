#!/usr/bin/env python3
"""
Pipeline CVM → fundos de investimento e FIIs (para o Terminal).

Saída (em <OUT>, padrão data/):
  fundos/index.json        lista [cnpj, nome, classe, PL, data] dos fundos com informe recente
  fundos/s/<NNN>.json      séries MENSAIS por fundo (shard = 3 primeiros dígitos do CNPJ):
                           {cnpj: [[AAAA-MM, cota_fim, PL_fim, captacao_liquida_mes, cotistas_fim, ultima_data], ...]}
  fii/index.json           FIIs listados (ticker pelo ISIN) com série mensal do informe:
                           [AAAA-MM, VP/cota, PL, DY_mes_%, rent_patrimonial_mes_%, cotistas, cotas]

Fontes: dados.cvm.gov.br  FI/DOC/INF_DIARIO (diário, por mês), FI/CAD (cadastro),
        FII/DOC/INF_MENSAL (anual). Só biblioteca padrão. Variáveis opcionais:
  CVM_ROOT (padrão https://dados.cvm.gov.br/dados) · CVM_OUT (padrão data) ·
  FUNDOS_MESES (padrão 25) · CVM_TODAY (AAAA-MM-DD, para testes)
"""
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_cvm import digits, fetch, norm, num  # noqa: E402  (mesmos utilitários testados)

ROOT = os.environ.get("CVM_ROOT", "https://dados.cvm.gov.br/dados").rstrip("/")
OUT = os.environ.get("CVM_OUT", "data")
N_MONTHS = int(os.environ.get("FUNDOS_MESES", "25"))   # 25 meses = janela completa de 24M
TODAY = dt.date.fromisoformat(os.environ["CVM_TODAY"]) if os.environ.get("CVM_TODAY") else dt.date.today()
ISIN_FII = re.compile(r"^BR([A-Z]{4})CTF\d{3}$")

csv.field_size_limit(10 * 1024 * 1024)


def rows_of(zf, name):
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        for row in csv.DictReader(text, delimiter=";"):
            yield {norm(k): v for k, v in row.items() if k is not None}


def rows_text(blob):
    text = io.TextIOWrapper(io.BytesIO(blob), encoding="latin-1", newline="")
    for row in csv.DictReader(text, delimiter=";"):
        yield {norm(k): v for k, v in row.items() if k is not None}


def pick(cols, *alts, exclude=()):
    """1ª coluna cujo nome contém todos os pedaços de alguma alternativa (e nenhum excluído)."""
    for alt in alts:
        parts = alt if isinstance(alt, tuple) else (alt,)
        for c in cols:
            if all(p in c for p in parts) and not any(x in c for x in exclude):
                return c
    return None


def months_back(n):
    y, m = TODAY.year, TODAY.month
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return sorted(out)


# ───────────────────────── informe diário ─────────────────────────
def ingest_daily(rows, agg, cols_cache):
    """agg[key][ym] = [data, cota, PL, captação líquida acumulada, cotistas]"""
    for r in rows:
        if not cols_cache:
            cols = list(r.keys())
            cols_cache.update(
                cnpj=pick(cols, "CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO"),
                sub=pick(cols, "ID_SUBCLASSE"),
                dt=pick(cols, "DT_COMPTC"),
                quota=pick(cols, "VL_QUOTA"),
                pl=pick(cols, "VL_PATRIM_LIQ"),
                cap=pick(cols, "CAPTC_DIA"),
                res=pick(cols, "RESG_DIA"),
                cot=pick(cols, "NR_COTST"),
            )
            miss = [k for k in ("cnpj", "dt", "quota") if not cols_cache.get(k)]
            if miss:
                raise RuntimeError(f"informe diário sem colunas {miss}: {cols}")
        c = cols_cache
        cnpj = digits(r.get(c["cnpj"]))
        d = (r.get(c["dt"]) or "").strip()[:10]
        if len(cnpj) != 14 or not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            continue
        sub = (r.get(c["sub"]) or "").strip() if c.get("sub") else ""
        key = cnpj if not sub else f"{cnpj}#{sub}"
        q = num(r.get(c["quota"]))
        pl = num(r.get(c["pl"])) if c.get("pl") else None
        flow = (num(r.get(c["cap"])) or 0.0) - (num(r.get(c["res"])) or 0.0) if c.get("cap") and c.get("res") else 0.0
        cot = num(r.get(c["cot"])) if c.get("cot") else None
        ym = d[:7]
        slot = agg.setdefault(key, {}).get(ym)
        if slot is None:
            agg[key][ym] = [d, q, pl, flow, cot]
        else:
            slot[3] += flow
            if d >= slot[0]:
                slot[0] = d
                if q:
                    slot[1] = q
                if pl is not None:
                    slot[2] = pl
                if cot is not None:
                    slot[4] = cot


def build_funds():
    agg = {}
    months = months_back(N_MONTHS)
    hist_years_done = set()
    for ym in months:
        blob = fetch(f"{ROOT}/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{ym}.zip")
        if blob is None:
            y = ym[:4]
            if y in hist_years_done:
                continue
            hist_years_done.add(y)
            print(f"  {ym}: mensal ausente → tentando HIST/{y}", flush=True)
            blob = fetch(f"{ROOT}/FI/DOC/INF_DIARIO/DADOS/HIST/inf_diario_fi_{y}.zip")
            if blob is None:
                continue
        print(f"Informe diário {ym} …", flush=True)
        zf = zipfile.ZipFile(io.BytesIO(blob))
        for name in zf.namelist():
            if name.lower().endswith(".csv"):
                ingest_daily(rows_of(zf, name), agg, {})        # colunas detectadas por arquivo (layout mudou em 2024)
    if not agg:
        raise RuntimeError("nenhum informe diário lido")

    # cadastro: nome e classe (formato antigo cad_fi.csv + registro_fundo_classe.zip da RCVM 175)
    info = {}
    blob = fetch(f"{ROOT}/FI/CAD/DADOS/cad_fi.csv")
    if blob:
        for r in rows_text(blob):
            cn = digits(r.get("CNPJ_FUNDO"))
            if len(cn) == 14:
                info[cn] = {"n": (r.get("DENOM_SOCIAL") or "").strip(), "c": (r.get("CLASSE_ANBIMA") or r.get("CLASSE") or "").strip(),
                            "s": (r.get("SIT") or "").strip()}
    blob = fetch(f"{ROOT}/FI/CAD/DADOS/registro_fundo_classe.zip")
    if blob:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        for name in sorted(zf.namelist(), key=lambda n: 0 if "fundo" in n.lower() else 1):  # classe sobrescreve fundo
            low = name.lower()
            if not low.endswith(".csv") or "subclasse" in low:
                continue
            it = rows_of(zf, name)
            first = next(it, None)
            if not first:
                continue
            cl = list(first.keys())
            c_cn = pick(cl, ("CNPJ", "CLASSE"), ("CNPJ", "FUNDO"), exclude=("ADMIN", "AUDIT", "CUSTOD", "CONTROL", "GESTOR"))
            c_nm = pick(cl, "DENOMINACAO_SOCIAL", "DENOM")
            c_cls = pick(cl, "CLASSIFICACAO_ANBIMA", "CLASSIFICACAO", "TIPO_CLASSE", "TIPO_FUNDO")
            c_sit = pick(cl, "SITUACAO")
            if not c_cn or not c_nm:
                print(f"  ! {name}: colunas não reconhecidas {cl}", file=sys.stderr)
                continue
            for r in [first, *it]:
                cn = digits(r.get(c_cn))
                if len(cn) != 14:
                    continue
                cur = info.setdefault(cn, {"n": "", "c": "", "s": ""})
                cur["n"] = (r.get(c_nm) or "").strip() or cur["n"]
                if c_cls and (r.get(c_cls) or "").strip():
                    cur["c"] = r.get(c_cls).strip()
                if c_sit and (r.get(c_sit) or "").strip():
                    cur["s"] = r.get(c_sit).strip()

    last_ym = max(ym for s in agg.values() for ym in s)
    recent = {last_ym, months_ym_minus(last_ym, 1)}
    class_level = {k for k in agg if "#" not in k and set(agg[k]) & recent}   # classe com informe recente
    index, shards = [], {}
    for key, series in agg.items():
        cnpj = key.split("#")[0]
        if "#" in key and cnpj in class_level:
            continue                                   # subclasse: só quando não há informe da classe
        yms = sorted(series)
        if not (set(yms) & recent):
            continue                                   # sem informe nos 2 últimos meses: encerrado/inativo
        last = series[yms[-1]]
        if not last[1] or not last[2] or last[2] <= 0:
            continue
        sid = key if "#" in key else cnpj
        rows = [[ym, round(series[ym][1], 8) if series[ym][1] else None,
                 round(series[ym][2], 2) if series[ym][2] is not None else None,
                 round(series[ym][3], 2), int(series[ym][4]) if series[ym][4] is not None else None, series[ym][0]]
                for ym in yms]
        shards.setdefault(cnpj[:3], {})[sid] = rows
        meta = info.get(cnpj, {})
        nome = meta.get("n") or "(sem nome no cadastro)"
        if "#" in key:
            nome += " · subclasse " + key.split("#")[1]
        index.append([sid, nome, meta.get("c", ""), round(last[2], 2), last[0]])

    base = os.path.join(OUT, "fundos")
    os.makedirs(os.path.join(base, "s"), exist_ok=True)
    for p, d in shards.items():
        with open(os.path.join(base, "s", f"{p}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, separators=(",", ":"))
    index.sort(key=lambda x: -x[3])
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(base, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"gerado": now, "ultimo_mes": last_ym, "fundos": index}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Fundos: {len(index)} com informe recente, {len(shards)} shards")


def months_ym_minus(ym, n):
    y, m = int(ym[:4]), int(ym[5:7]) - n
    while m <= 0:
        m, y = m + 12, y - 1
    return f"{y}-{m:02d}"


# ───────────────────────── FIIs (informe mensal) ─────────────────────────
def build_fii():
    geral, comp = {}, {}
    for y in (TODAY.year - 2, TODAY.year - 1, TODAY.year):
        blob = fetch(f"{ROOT}/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{y}.zip")
        if blob is None:
            continue
        print(f"FII informe mensal {y} …", flush=True)
        zf = zipfile.ZipFile(io.BytesIO(blob))
        for name in zf.namelist():
            low = name.lower()
            kind = "geral" if "geral" in low else "comp" if "complemento" in low else None
            if not kind:
                continue
            it = rows_of(zf, name)
            first = next(it, None)
            if not first:
                continue
            cl = list(first.keys())
            c_cn = pick(cl, ("CNPJ", "FUNDO"), "CNPJ", exclude=("ADMIN",))
            c_dt = pick(cl, "DATA_REFERENCIA")
            c_v = pick(cl, "VERSAO")
            if not c_cn or not c_dt:
                print(f"  ! {name}: colunas não reconhecidas {cl}", file=sys.stderr)
                continue
            if kind == "geral":
                m = dict(nome=pick(cl, "NOME_FUNDO", "NOME"), isin=pick(cl, "ISIN"), seg=pick(cl, "SEGMENTO"),
                         mand=pick(cl, "MANDATO"), gest=pick(cl, "TIPO_GESTAO", "GESTAO"))
            else:
                m = dict(vp=pick(cl, ("VALOR_PATRIMONIAL", "COTA")), pl=pick(cl, "PATRIMONIO_LIQUIDO"),
                         dy=pick(cl, "DIVIDEND_YIELD"), rp=pick(cl, ("RENTABILIDADE", "PATRIMONIAL")),
                         cot=pick(cl, ("TOTAL", "NUMERO_COTISTAS"), "NUMERO_COTISTAS"),
                         cotas=pick(cl, "COTAS_EMITIDAS"))
            tgt = geral if kind == "geral" else comp
            for r in [first, *it]:
                cn = digits(r.get(c_cn))
                d = (r.get(c_dt) or "").strip()[:10]
                if len(cn) != 14 or not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                    continue
                try:
                    v = int(float(r.get(c_v) or 0)) if c_v else 0
                except ValueError:
                    v = 0
                prev = tgt.get((cn, d))
                if prev and prev[0] > v:
                    continue                           # mantém a versão mais recente
                tgt[(cn, d)] = (v, {k: (r.get(col) if col else None) for k, col in m.items()})

    meta = {}
    for (cn, d), (_, g) in sorted(geral.items(), key=lambda kv: kv[0][1]):
        meta[cn] = g                                   # o mais recente vence
    fiis = {}
    for (cn, d), (_, c) in comp.items():
        g = meta.get(cn)
        if not g:
            continue
        mt = ISIN_FII.match(norm(g.get("isin")))
        if not mt:
            continue
        tk = mt.group(1) + "11"
        f = fiis.setdefault(tk, {"cnpj": cn, "nome": (g.get("nome") or "").strip(), "segmento": (g.get("seg") or "").strip(),
                                 "mandato": (g.get("mand") or "").strip(), "gestao": (g.get("gest") or "").strip(), "serie": {}})
        if f["cnpj"] != cn:
            continue                                   # radical de ISIN repetido em outro CNPJ: mantém o 1º
        f["serie"][d[:7]] = [d[:7], num(c.get("vp")), num(c.get("pl")), num(c.get("dy")), num(c.get("rp")),
                             int(num(c.get("cot"))) if num(c.get("cot")) is not None else None, num(c.get("cotas"))]
    # A CVM publica percentuais ora como fração (0,0085) ora como % (0,85): detecta a escala no
    # conjunto inteiro (DY mensal típico fica entre 0,3% e 1,5%) e normaliza para %.
    dys = sorted(abs(r[3]) for f in fiis.values() for r in f["serie"].values() if r[3])
    if dys and dys[len(dys) // 2] < 0.05:
        for f in fiis.values():
            for r in f["serie"].values():
                r[3] = r[3] * 100 if r[3] is not None else None
                r[4] = r[4] * 100 if r[4] is not None else None
        print("  (percentuais do informe vieram como fração → convertidos para %)")
    out = {}
    for tk, f in fiis.items():
        ser = [f["serie"][k] for k in sorted(f["serie"])][-N_MONTHS:]
        if not ser:
            continue
        f["serie"] = ser
        out[tk] = f
    os.makedirs(os.path.join(OUT, "fii"), exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(OUT, "fii", "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"gerado": now, "fiis": out}, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"FIIs: {len(out)} com ticker identificado")


if __name__ == "__main__":
    ok = True
    for step in (build_funds, build_fii):
        try:
            step()
        except Exception as e:                         # uma parte falhar não derruba a outra
            ok = False
            print(f"ERRO em {step.__name__}: {e}", file=sys.stderr)
    if not ok:
        sys.exit(1)
