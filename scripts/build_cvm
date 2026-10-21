#!/usr/bin/env python3
"""
Pipeline CVM → JSON por empresa (para o Terminal).

Baixa os ZIPs anuais da DFP (Demonstrações Financeiras Padronizadas) e o FCA
(Formulário Cadastral, para os tickers) do Portal de Dados Abertos da CVM,
extrai as contas-chave de DRE, Balanço, DFC e DVA e grava:

    <OUT>/index.json          → {"gerado":..., "tickers":{"PETR4":"9512",...}, "empresas":{...}}
    <OUT>/<CD_CVM>.json       → séries anuais da empresa (valores em R$)

Só usa a biblioteca padrão do Python (3.9+). Variáveis de ambiente opcionais:
    CVM_BASE   base dos dados (padrão: https://dados.cvm.gov.br/dados/CIA_ABERTA)
    CVM_OUT    pasta de saída (padrão: data/cvm)
    CVM_YEARS  quantidade de anos de DFP (padrão: 11 → ano atual e 10 anteriores)
"""
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.environ.get("CVM_BASE", "https://dados.cvm.gov.br/dados/CIA_ABERTA").rstrip("/")
OUT = os.environ.get("CVM_OUT", "data/cvm")
N_YEARS = int(os.environ.get("CVM_YEARS", "11"))
UA = "Mozilla/5.0 (compatible; TerminalCVM/1.0; +https://github.com)"

csv.field_size_limit(10 * 1024 * 1024)

# Contas fixas do plano padrão da CVM (empresas não financeiras).
WANTED = {
    "DRE": {"3.01", "3.02", "3.03", "3.04", "3.05", "3.06", "3.07", "3.08", "3.09", "3.11", "3.11.01"},
    "BPA": {"1", "1.01", "1.01.01", "1.01.02", "1.02"},
    "BPP": {"2", "2.01", "2.01.04", "2.02", "2.02.01", "2.03", "2.03.09"},
    "DFC_MI": {"6.01", "6.02", "6.03", "6.05"},
    "DVA": {"7.04.01"},
}
CAPEX_RE = re.compile(r"(AQUISI|COMPRA|ADI[CÇ]|INVESTIMENTO).*(IMOBILIZADO|INTANG)")
FILE_RE = re.compile(r"dfp_cia_aberta_(DRE|BPA|BPP|DFC_MI|DVA)_(con|ind)_(\d{4})\.csv$", re.I)
ROOT_RE = re.compile(r"^(dfp|itr)_cia_aberta_\d{4}\.csv$", re.I)
TODAY = dt.date.fromisoformat(os.environ["CVM_TODAY"]) if os.environ.get("CVM_TODAY") else dt.date.today()
TICKER_RE = re.compile(r"^[A-Z]{4}\d{1,2}$")


def norm(s):
    """Maiúsculas sem acento (para comparar 'ÚLTIMO', nomes de colunas etc.)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def digits(s):
    return re.sub(r"\D", "", str(s or ""))


def num(s):
    """Converte '1234.56', '1234,56' ou '1.234,56' em float; None se vazio/inválido."""
    s = str(s or "").strip()
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def fetch(url, tries=4):
    """GET com retentativas. Retorna bytes, ou None se o arquivo não existe (404)."""
    for i in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=180) as r:
                return r.read()
        except HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except (URLError, TimeoutError, ConnectionError) as e:
            err = e
        wait = 5 * (2 ** i)
        print(f"  ! {url}: {err} — nova tentativa em {wait}s", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"falha ao baixar {url}")


def read_csv(zf, name):
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="latin-1", newline="")
        reader = csv.DictReader(text, delimiter=";")
        for row in reader:
            yield {norm(k): v for k, v in row.items() if k is not None}


def find_col(cols, *needles):
    for c in cols:
        if all(n in c for n in needles):
            return c
    return None


# ───────────────────────── DFP ─────────────────────────
def parse_deliveries(zf, name, deliv):
    """deliv[cd][DT_REFER] = data da 1ª entrega (DT_RECEB mínima entre as versões)."""
    rows = read_csv(zf, name)
    first = next(rows, None)
    if not first:
        return
    c_rec = find_col(list(first.keys()), "DT_RECEB")
    if not c_rec:
        print(f"  ! {name}: coluna DT_RECEB ausente — agenda de resultados ignorada", file=sys.stderr)
        return
    for r in [first, *rows]:
        cd = (r.get("CD_CVM") or "").strip().lstrip("0")
        ref = (r.get("DT_REFER") or "").strip()[:10]
        rec = (r.get(c_rec) or "").strip()[:10]
        if not cd or not re.match(r"^\d{4}-\d{2}-\d{2}$", ref) or not re.match(r"^\d{4}-\d{2}-\d{2}$", rec):
            continue
        d = deliv.setdefault(cd, {})
        if ref not in d or rec < d[ref]:
            d[ref] = rec


def parse_dfp_year(blob, store, shares, deliv=None):
    """store[cd][year][base] = {'dt':DT_REFER,'v':versão,'vals':{...},'desc301':...}"""
    zf = zipfile.ZipFile(io.BytesIO(blob))
    for name in zf.namelist():
        base_name = os.path.basename(name)
        if deliv is not None and ROOT_RE.match(base_name):
            parse_deliveries(zf, name, deliv)
            continue
        m = FILE_RE.search(base_name)
        if m:
            demo, base = m.group(1).upper(), m.group(2).lower()
            wanted = WANTED[demo]
            for r in read_csv(zf, name):
                if norm(r.get("ORDEM_EXERC")) != "ULTIMO":
                    continue
                cd = (r.get("CD_CVM") or "").strip().lstrip("0")
                dtref = (r.get("DT_REFER") or "").strip()
                if not cd or len(dtref) < 4:
                    continue
                year = dtref[:4]
                try:
                    ver = int(float(r.get("VERSAO") or 0))
                except ValueError:
                    ver = 0
                comp = store.setdefault(cd, {"cnpj": digits(r.get("CNPJ_CIA")), "nome": (r.get("DENOM_CIA") or "").strip()})
                slot = comp.setdefault("anos", {}).setdefault(year, {}).setdefault(base, {"dt": dtref, "v": -1, "vals": {}})
                # mantém só a versão mais recente da DFP (reapresentações) e o último DT_REFER do ano
                key_new, key_old = (dtref, ver), (slot["dt"], slot["v"])
                if key_new < key_old:
                    continue
                if key_new > key_old:
                    slot.update(dt=dtref, v=ver, vals={})
                val = num(r.get("VL_CONTA"))
                if val is None:
                    continue
                if norm(r.get("ESCALA_MOEDA")) == "MIL":
                    val *= 1000.0
                code = (r.get("CD_CONTA") or "").strip()
                if code in wanted:
                    slot["vals"][code] = val
                    if code == "3.01":
                        slot["desc301"] = norm(r.get("DS_CONTA"))
                elif demo == "DFC_MI" and code.startswith("6.02.") and val < 0 and CAPEX_RE.search(norm(r.get("DS_CONTA"))):
                    slot["vals"]["capex"] = slot["vals"].get("capex", 0.0) + val
            continue
        if "composicao_capital" in base_name.lower():
            rows = read_csv(zf, name)
            first = next(rows, None)
            if not first:
                continue
            cols = list(first.keys())
            c_tot = find_col(cols, "QT_ACAO_TOTAL", "CAP_INTEGR")
            c_tes = find_col(cols, "QT_ACAO_TOTAL", "TESOURO")
            if not c_tot:
                continue
            for r in [first, *rows]:
                cnpj, dtref = digits(r.get("CNPJ_CIA")), (r.get("DT_REFER") or "").strip()
                tot = num(r.get(c_tot))
                if not cnpj or len(dtref) < 4 or tot is None:
                    continue
                tes = num(r.get(c_tes)) if c_tes else 0.0
                shares.setdefault(cnpj, {})[dtref[:4]] = tot - (tes or 0.0)


# ───────────────────────── FCA (tickers) e cadastro ─────────────────────────
def parse_tickers(blob):
    out = {}
    zf = zipfile.ZipFile(io.BytesIO(blob))
    for name in zf.namelist():
        if "valor_mobiliario" not in name.lower():
            continue
        rows = read_csv(zf, name)
        first = next(rows, None)
        if not first:
            continue
        cols = list(first.keys())
        c_cnpj = find_col(cols, "CNPJ")
        c_tk = find_col(cols, "CODIGO", "NEGOCIACAO")
        if not c_cnpj or not c_tk:
            print(f"  ! colunas de ticker não encontradas em {name}: {cols}", file=sys.stderr)
            continue
        for r in [first, *rows]:
            tk = norm(r.get(c_tk)).replace(" ", "")
            cnpj = digits(r.get(c_cnpj))
            if cnpj and TICKER_RE.match(tk):
                out.setdefault(cnpj, set()).add(tk)
    return out


def parse_cadastro(blob):
    out = {}
    text = io.TextIOWrapper(io.BytesIO(blob), encoding="latin-1", newline="")
    for row in csv.DictReader(text, delimiter=";"):
        r = {norm(k): v for k, v in row.items() if k is not None}
        cnpj = digits(r.get("CNPJ_CIA"))
        if cnpj:
            out[cnpj] = {"setor": (r.get("SETOR_ATIV") or "").strip(), "sit": (r.get("SIT") or "").strip()}
    return out


# ───────────────────────── agenda de resultados ─────────────────────────
def _shift_year(d, n=1):
    try:
        return d.replace(year=d.year + n)
    except ValueError:            # 29/02 → 28/02
        return d.replace(year=d.year + n, day=28)


def build_calendar(deliv):
    """Próximas divulgações por empresa: prazo legal (ITR: 45 dias após o trimestre;
    DFP: 31/03) e previsão = data da entrega do mesmo período no ano anterior."""
    out = {}
    lim_old = TODAY - dt.timedelta(days=7)
    for cd, refs in deliv.items():
        if not refs:
            continue
        last_ref = max(refs, key=lambda k: refs[k])
        last_rec = refs[last_ref]
        if dt.date.fromisoformat(last_rec) < TODAY - dt.timedelta(days=550):
            continue                                   # sem entregas recentes: provavelmente inativa
        nxt = []
        for y in (TODAY.year - 1, TODAY.year, TODAY.year + 1):
            for mmdd, kind in (("03-31", "ITR"), ("06-30", "ITR"), ("09-30", "ITR"), ("12-31", "DFP")):
                ref = f"{y}-{mmdd}"
                if ref in refs:
                    continue                           # já entregue
                ref_d = dt.date.fromisoformat(ref)
                prazo = ref_d + dt.timedelta(days=45) if kind == "ITR" else dt.date(y + 1, 3, 31)
                if prazo < lim_old:
                    continue
                prev_ref = f"{y - 1}-{mmdd}"
                prev = refs.get(prev_ref)
                prev_d = dt.date.fromisoformat(prev) if prev else None
                est = min(_shift_year(prev_d), prazo) if prev_d else None
                q = {"03-31": "1T", "06-30": "2T", "09-30": "3T", "12-31": "4T"}[mmdd]
                nxt.append({"ref": ref, "tipo": f"{kind} {q}{str(y)[2:]}", "prazo": prazo.isoformat(),
                            "previsto": est.isoformat() if est else None, "base": prev})
        nxt.sort(key=lambda e: e["previsto"] or e["prazo"])
        out[cd] = {"ultimo": {"ref": last_ref, "data": last_rec}, "proximos": nxt[:2]}
    return out


# ───────────────────────── montagem ─────────────────────────
def build():
    this_year = TODAY.year
    store, shares, deliv = {}, {}, {}
    for y in range(this_year - N_YEARS + 1, this_year + 1):
        url = f"{BASE}/DOC/DFP/DADOS/dfp_cia_aberta_{y}.zip"
        print(f"DFP {y} …", flush=True)
        blob = fetch(url)
        if blob is None:
            print(f"  (sem arquivo para {y})")
            continue
        parse_dfp_year(blob, store, shares, deliv if y >= this_year - 2 else None)

    for y in (this_year - 1, this_year):              # ITR: só o arquivo-raiz (datas de entrega)
        print(f"ITR {y} (datas de entrega) …", flush=True)
        blob = fetch(f"{BASE}/DOC/ITR/DADOS/itr_cia_aberta_{y}.zip")
        if blob:
            zf = zipfile.ZipFile(io.BytesIO(blob))
            for name in zf.namelist():
                if ROOT_RE.match(os.path.basename(name)):
                    parse_deliveries(zf, name, deliv)

    tickers = {}
    for y in (this_year, this_year - 1):
        blob = fetch(f"{BASE}/DOC/FCA/DADOS/fca_cia_aberta_{y}.zip")
        if blob:
            for cnpj, tks in parse_tickers(blob).items():
                tickers.setdefault(cnpj, set()).update(tks)
            if tickers:
                break
    cad_blob = fetch(f"{BASE}/CAD/DADOS/cad_cia_aberta.csv")
    cad = parse_cadastro(cad_blob) if cad_blob else {}

    os.makedirs(OUT, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index = {"gerado": now, "tickers": {}, "empresas": {}}
    written = 0
    for cd, comp in store.items():
        tks = sorted(tickers.get(comp["cnpj"], ()))
        if not tks:
            continue  # só companhias com ação negociada
        anos, fin = {}, False
        for year, bases in sorted(comp.get("anos", {}).items()):
            # consolidado se tiver DRE; senão individual
            pick = None
            for b in ("con", "ind"):
                s = bases.get(b)
                if s and "3.01" in s["vals"]:
                    pick = (b, s)
                    break
            if not pick:
                continue
            b, s = pick
            vals = {k: round(v) for k, v in s["vals"].items()}
            sh = shares.get(comp["cnpj"], {}).get(year)
            if sh:
                vals["acoes"] = round(sh)
            vals["base"] = b
            vals["dt"] = s["dt"]
            anos[year] = vals
            d = s.get("desc301", "")
            if "INTERMEDIA" in d or "SEGURO" in d or "PREMIO" in d:
                fin = True
        if not anos:
            continue
        info = cad.get(comp["cnpj"], {})
        doc = {"cd": cd, "cnpj": comp["cnpj"], "nome": comp["nome"], "setor": info.get("setor", ""),
               "tickers": tks, "fin": fin, "anos": anos, "gerado": now}
        with open(os.path.join(OUT, f"{cd}.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        index["empresas"][cd] = {"n": comp["nome"], "t": tks}
        for t in tks:
            index["tickers"][t] = cd
        written += 1
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    cal = build_calendar({cd: deliv.get(cd, {}) for cd in index["empresas"]})
    with open(os.path.join(OUT, "calendario.json"), "w", encoding="utf-8") as f:
        json.dump({"gerado": now, "hoje": TODAY.isoformat(), "eventos": cal}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Agenda: {len(cal)} empresas com próximas divulgações")
    print(f"OK: {written} empresas, {len(index['tickers'])} tickers → {OUT}")
    if written == 0:
        sys.exit("nenhuma empresa gerada — verifique as fontes")


if __name__ == "__main__":
    build()
