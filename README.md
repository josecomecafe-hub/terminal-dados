# Dados CVM para o Terminal

Toda segunda-feira, uma GitHub Action baixa os dados abertos da CVM e publica JSONs
pequenos que o terminal lê via CDN (com CORS):

| Pasta | Conteúdo | Script |
|---|---|---|
| `data/cvm/` | 10+ anos de DRE, Balanço, DFC e DVA por companhia listada (`<CD_CVM>.json`), `index.json` (ticker → empresa) e `calendario.json` (próximas divulgações) | `build_cvm.py` |
| `data/fundos/` | `index.json` (busca) e séries mensais por fundo em `s/NNN.json`: cota, PL, captação líquida e cotistas | `build_fundos.py` |
| `data/fii/` | `index.json` com FIIs listados (ticker pelo ISIN): VP/cota, PL, DY e rentabilidade patrimonial do mês, cotistas | `build_fundos.py` |

## Configuração (uma vez)

1. Crie um repositório **público** no GitHub e envie estes arquivos (mantendo as pastas).
2. Aba **Actions** → "Atualizar dados CVM" → **Run workflow**. A primeira execução leva ~10–20 min
   (os informes diários de fundos são grandes).
3. No terminal: ☰ → **📑 Fonte de dados CVM** e cole:

   `https://cdn.jsdelivr.net/gh/SEU_USUARIO/SEU_REPO@main/data/cvm`

   O terminal acha `data/fundos` e `data/fii` a partir dessa mesma URL.
   (Sem cache de CDN: `https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPO/main/data/cvm`.)

## Testar localmente

    python scripts/build_cvm.py                 # data/cvm
    CVM_YEARS=3 python scripts/build_cvm.py     # só os 3 últimos anos
    FUNDOS_MESES=3 python scripts/build_fundos.py   # data/fundos e data/fii (rápido)

## Observações

- Balanços: valores em R$ (escala "MIL" convertida), consolidado quando existe, versão mais recente
  de cada DFP. Instituições financeiras são marcadas com `"fin": true`.
- Agenda: a data **prevista** repete a data em que a empresa entregou o mesmo período no ano anterior,
  limitada ao **prazo legal** (ITR em até 45 dias após o trimestre; DFP até 31/03). É uma estimativa.
- Fundos: só entram classes com informe nos 2 últimos meses. Subclasses aparecem apenas quando a
  classe não tem informe próprio. As colunas são detectadas pelo nome, cobrindo o layout antigo
  (`CNPJ_FUNDO`) e o da RCVM 175 (`CNPJ_FUNDO_CLASSE`).
- FIIs: o ticker é derivado do ISIN (`BR` + radical + `CTF…` → radical + `11`). Os percentuais do
  informe são normalizados para %.
- Se a etapa de fundos falhar, os balanços continuam sendo publicados (`continue-on-error`).
