"""Busca de produtos por texto — regra única do sistema.

Usada no SQL (função ``product_search_score``, registrada em
``connection._connect``) pelos filtros da biblioteca, do estoque do evento, do
painel do vendedor e das movimentações. O catálogo e o seletor de produtos das
promoções usam ``static/js/product-search.js``, espelho deste módulo: qualquer
mudança aqui precisa ser repetida lá.

Os nomes vêm do ERP em maiúsculas, abreviados e com medidas coladas
(``RESINA VITTRA APS REFIL DENTINA DA1 1SERX4G FGM``, ``ESCOVA P/POLIM``).
Cada palavra da busca precisa aparecer no nome (ou variante) ou no SKU:

- como começo de palavra (``vit`` → VITTRA). A última palavra pode estar pela
  metade (``vittra a`` → APS): a busca roda enquanto se digita;
- no meio da busca, palavras de até 2 caracteres e números valem inteiros
  (``k 15``, ``a1``, ``lima 25``), para não virar "qualquer palavra com k";
- a partir de uma troca letra↔número (``4g`` e ``serx`` → 1SERX4G);
- atravessando o espaço do nome ou da busca (``vittraaps``, ``vit tra``,
  ``kfile`` → K-FILE);
- código de cor/medida dentro de outro código (``a2`` → DA2, EA2);
- palavra abreviada do ERP e plural (``polimento`` → POLIM, ``resinas`` →
  RESINA), siglas (``para`` → P/, ``braquete`` → BKT) e mesmo radical
  (``clareamento`` → CLAREADOR);
- um erro de digitação em palavras de 5+ letras (``vitra`` → VITTRA);
- ``de``, ``da``, ``com``... não são obrigatórias (``fio de sutura`` → FIO SUTURA).

A pontuação ordena o resultado: palavra inteira > trecho > começo de palavra >
abreviação > código > erro de digitação, com bônus para a frase digitada em
sequência, para o nome que começa pela busca e para o SKU idêntico.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable, List, NamedTuple, Optional, Sequence, Tuple

# Qualidade de cada palavra da busca (soma na pontuação).
EXACT = 12      # palavra inteira do nome
SEGMENT = 10    # trecho inteiro de um código (1SERX4G → "4g", "serx")
PREFIX = 8      # começo de palavra
PARTIAL = 6     # começo de trecho ou atravessando o espaço
ABBREV = 6      # abreviação/plural/sigla do ERP
CODE = 4        # código dentro de outro (DA2 ← "a2")
RADICAL = 4     # mesmo radical (CLAREADOR ← "clareamento")
TYPO = 2        # uma letra errada
SKU_FULL = 12   # palavra é o SKU inteiro
SKU_PART = 5    # palavra dentro do SKU
TAG = 3         # começo de palavra da categoria
STOP_HIT = 1    # "de", "com"... quando aparecem

#: Palavras que não precisam estar no nome ("fio de sutura" acha FIO SUTURA).
STOP_WORDS = frozenset(
    {"de", "da", "do", "das", "dos", "e", "em", "na", "no", "nas", "nos",
     "com", "para", "pra", "por"}
)

#: Siglas e abreviações do cadastro do ERP que não são o começo da palavra
#: por extenso com 4+ letras (essas — APLICAD, DESCART, ELAST — já casam pela
#: regra de abreviação).
ABBREVIATIONS = {
    "p": ("para",), "c": ("com",), "s": ("sem",), "n": ("numero",),
    "cx": ("caixa",), "cxs": ("caixas",),
    "un": ("unidade", "unidades"), "und": ("unidade", "unidades"),
    "unid": ("unidade", "unidades"),
    "pct": ("pacote",), "pc": ("peca", "pacote"), "pcs": ("pecas",),
    "fr": ("frasco",), "frs": ("frascos",), "tb": ("tubo",), "ser": ("seringa",),
    "ref": ("refil", "referencia"), "kt": ("kit",), "cj": ("conjunto",),
    "jg": ("jogo",), "reg": ("regular",), "esp": ("especial",),
    "desc": ("descartavel", "descartaveis"),
    "bkt": ("braquete", "braquetes", "bracket"), "rep": ("reposicao",),
    "inf": ("inferior",), "sup": ("superior",),
    "peq": ("pequeno", "pequena"), "med": ("medio", "media"),
    "gde": ("grande",), "ang": ("angulado", "angulada", "angulo"),
    "liq": ("liquido",), "tam": ("tamanho",),
    "esq": ("esquerdo", "esquerda"), "dir": ("direito", "direita"),
    "mat": ("material", "materiais"), "gan": ("gancho",), "can": ("canino",),
    "lat": ("lateral",),
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CODE_PARTS = re.compile(r"[a-z]+|[0-9]+")


def fold(text) -> str:
    """Minúsculas, sem acento; tudo que não é letra/número vira espaço."""
    s = unicodedata.normalize("NFD", str(text or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return _NON_ALNUM.sub(" ", s).strip()


@lru_cache(maxsize=1024)
def _is_code(tok: str) -> bool:
    """Letras e números juntos (A1, DA2, 25MM)."""
    return any(ch.isdigit() for ch in tok) and any(ch.isalpha() for ch in tok)


class Query(NamedTuple):
    tokens: Tuple[str, ...]
    #: Precisa casar inteiro (curta ou número que não é a última palavra).
    strict: Tuple[bool, ...]
    #: Precisa aparecer (``False`` para "de", "com"...).
    required: Tuple[bool, ...]
    phrase: str
    compact: str


@lru_cache(maxsize=512)
def parse_query(q: Optional[str]) -> Query:
    raw = (q or "").strip()
    if raw.startswith("#"):
        raw = raw[1:]
    tokens = tuple(t for t in fold(raw).split(" ") if t)
    last = len(tokens) - 1
    strict = tuple(
        i < last and (len(t) <= 2 or t.isdigit()) for i, t in enumerate(tokens)
    )
    only_stop = all(t in STOP_WORDS for t in tokens)
    required = tuple(only_stop or t not in STOP_WORDS for t in tokens)
    return Query(tokens, strict, required, " ".join(tokens), "".join(tokens))


class _Title(NamedTuple):
    text: str
    words: Tuple[str, ...]
    word_pos: Tuple[int, ...]   # início de cada palavra em ``joined``
    joined: str                 # palavras coladas ("|" entre dois números)
    starts: Tuple[int, ...]     # começo de palavra ou de trecho letra/número
    word_starts: frozenset
    seg_ends: frozenset
    word_ends: frozenset


@lru_cache(maxsize=32768)
def _title_index(text: str) -> _Title:
    folded = fold(text)
    words = tuple(w for w in folded.split(" ") if w)
    joined = ""
    word_pos: List[int] = []
    starts: List[int] = []
    seg_ends = set()
    word_ends = set()
    for w in words:
        # "1,5" não pode virar "15": números vizinhos ficam separados.
        if joined and joined[-1].isdigit() and w[0].isdigit():
            joined += "|"
        pos = len(joined)
        word_pos.append(pos)
        starts.append(pos)
        for k in range(1, len(w)):
            if w[k].isdigit() != w[k - 1].isdigit():
                starts.append(pos + k)
                seg_ends.add(pos + k)
        joined += w
        seg_ends.add(len(joined))
        word_ends.add(len(joined))
    return _Title(
        folded, words, tuple(word_pos), joined, tuple(starts),
        frozenset(word_pos), frozenset(seg_ends), frozenset(word_ends),
    )


def _within_one_edit(a: str, b: str, allow_swap: bool) -> bool:
    """Uma letra a mais, a menos, duas invertidas ou (``allow_swap``) trocada."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = 0
    while i < la and i < lb and a[i] == b[i]:
        i += 1
    if la == lb:
        if a[i + 2:] == b[i + 2:] and a[i:i + 2] == b[i:i + 2][::-1]:
            return True
        return allow_swap and a[i + 1:] == b[i + 1:]
    return a[i + 1:] == b[i:] if la > lb else a[i:] == b[i + 1:]


def _expands_to(tok: str, full: str) -> bool:
    """``tok`` é a palavra por extenso da sigla: inteira, no plural ou o começo
    dela com 4+ letras ("unid"). "composta" não é "com" (C/), nem "parafuso" é
    "para" (P/)."""
    return tok in (full, full + "s", full + "es") or (len(tok) >= 4 and full.startswith(tok))


@lru_cache(maxsize=65536)
def _word_variant_quality(tok: str, word: str) -> int:
    """Abreviação, sigla, radical ou erro de digitação de ``tok`` contra ``word``.

    Todas as regras exigem a mesma primeira letra (quem chama já filtra).
    """
    if not word.isalpha():
        return 0
    if len(word) >= 4 and tok.startswith(word):
        return ABBREV
    expansions = ABBREVIATIONS.get(word)
    if expansions and any(_expands_to(tok, f) for f in expansions):
        return ABBREV
    if len(tok) >= 7 and len(word) >= 6 and tok[:6] == word[:6]:
        return RADICAL
    if len(tok) >= 5 and len(word) >= 4 and tok[:2] == word[:2]:
        # Letra trocada só a partir de 6 letras: CURTA/CURVA, PONTA/PORTA e
        # MEDIA/MEDIO são produtos diferentes.
        swap = len(tok) >= 6
        if _within_one_edit(tok, word, swap):
            return TYPO
        if len(word) > len(tok) and _within_one_edit(tok, word[:len(tok)], swap):
            return TYPO
        if len(tok) > len(word) >= 6:
            for n in range(len(word) - 1, len(word) + 2):
                if _within_one_edit(tok[:n], word, swap):
                    return TYPO
    return 0


def _match_title(tok: str, strict: bool, t: _Title, loose: bool = True) -> Tuple[int, int]:
    """``(qualidade, posição)`` do melhor encaixe de ``tok`` no título, ou ``(0, -1)``.

    ``loose=False``: só encaixe por posição (sem código, abreviação nem erro de
    digitação) — para duas palavras da busca coladas, que senão casariam
    "resinavittra" como abreviação de RESINA.
    """
    best_q, best_pos = 0, -1
    joined = t.joined
    for s in t.starts if tok in joined else ():
        if not joined.startswith(tok, s):
            continue
        e = s + len(tok)
        complete = e in t.seg_ends
        if strict and not complete:
            continue
        crosses = any(s < w < e for w in t.word_starts)
        if crosses:
            q = PARTIAL
        elif s in t.word_starts and e in t.word_ends:
            q = EXACT
        elif complete:
            q = SEGMENT
        elif s in t.word_starts:
            q = PREFIX
        else:
            q = PARTIAL
        if q > best_q:
            best_q, best_pos = q, s
            if q == EXACT:
                return best_q, best_pos
    if best_q or not loose:
        return best_q, best_pos
    if _is_code(tok):
        for w, pos in zip(t.words, t.word_pos):
            i = w.find(tok, 1)
            end = i + len(tok)
            if i > 0 and (end == len(w) or w[end].isdigit() != w[end - 1].isdigit()):
                return CODE, pos + i
    if tok.isalpha() and len(tok) >= 3:
        first = tok[0]
        for w, pos in zip(t.words, t.word_pos):
            if w[0] != first:
                continue
            q = _word_variant_quality(tok, w)
            if q > best_q:
                best_q, best_pos = q, pos
    return best_q, best_pos


class _Sku(NamedTuple):
    compact: str
    words: frozenset


@lru_cache(maxsize=32768)
def _sku_index(sku: str) -> _Sku:
    folded = fold(sku)
    return _Sku(folded.replace(" ", ""), frozenset(folded.split(" ")))


def _match_sku(tok: str, skus: Sequence[_Sku]) -> int:
    best = 0
    for sk in skus:
        if sk.compact == tok:
            return SKU_FULL
        if (len(tok) >= 3 and tok in sk.compact) or tok in sk.words:
            best = SKU_PART
    return best


def _sku_query_bonus(query: Query, skus: Sequence[_Sku]) -> int:
    """A busca inteira (sem espaços) é o SKU, o começo dele ou um trecho."""
    qc = query.compact
    if len(qc) < 3:
        return 0
    best = 0
    for sk in skus:
        if sk.compact == qc:
            return 1000
        if sk.compact.startswith(qc):
            best = max(best, 400)
        elif qc in sk.compact:
            best = max(best, 200)
    return best


def _match_tag(tok: str, tags: Sequence[_Title]) -> int:
    """Categoria e afins: só palavra inteira ou começo de palavra."""
    for t in tags:
        for w in t.words:
            if w.startswith(tok):
                return TAG
    return 0


def _code_parts_match(tok: str, is_last: bool, t: _Title) -> bool:
    """Código digitado junto e separado no nome ("k15" → K FILE 15).

    Os trechos antes do último precisam ser palavras inteiras do nome (o "a"
    de "a2" não pode sair de A3); o último, trecho inteiro — ou pela metade,
    se é a palavra que está sendo digitada.
    """
    parts = _CODE_PARTS.findall(tok)
    for n, part in enumerate(parts):
        final = n == len(parts) - 1
        q = _match_title(part, not (is_last and final), t, False)[0]
        if not q or (not final and q != EXACT):
            return False
    return True


def _score_title(
    query: Query,
    t: _Title,
    skus: Sequence[_Sku],
    tags: Sequence[_Title],
    sku_hit: bool,
) -> Optional[float]:
    tokens = query.tokens
    total = 0.0
    positions: List[int] = []
    for i, tok in enumerate(tokens):
        q, pos = _match_title(tok, query.strict[i], t)
        if not q:
            # Palavra digitada em dois pedaços, ou duas palavras coladas.
            if i > 0 and _match_title(tokens[i - 1] + tok, False, t, False)[0]:
                q = PARTIAL
            elif i + 1 < len(tokens) and _match_title(tok + tokens[i + 1], False, t, False)[0]:
                q = PARTIAL
            elif _is_code(tok) and _code_parts_match(tok, i == len(tokens) - 1, t):
                q = PARTIAL
        if q:
            total += q if query.required[i] else STOP_HIT
            if pos >= 0:
                positions.append(pos)
            continue
        sq = _match_sku(tok, skus) or _match_tag(tok, tags)
        if sq:
            total += sq
        elif query.required[i] and not sku_hit:
            return None
    padded = f" {t.text} "
    if t.text.startswith(query.phrase):
        total += 40
    elif f" {query.phrase}" in padded:
        total += 25
    if positions and positions[0] == 0:
        total += 10
    if len(positions) >= 2 and positions == sorted(positions):
        total += 6
    total -= min(10.0, len(t.words) * 0.4)
    return total


def score(
    q,
    titles: Iterable[str],
    skus: Iterable[str] = (),
    tags: Iterable[str] = (),
) -> Optional[float]:
    """Pontuação de um produto para a busca ``q``; ``None`` se não casa.

    ``titles``: nome + variante primeiro; os demais (nomes das variantes de um
    card do catálogo) valem um pouco menos. Todas as palavras precisam estar
    no mesmo título (ou no SKU). ``skus``: SKU e apelidos. ``tags``: textos de
    apoio (categoria) que só casam por começo de palavra.
    """
    query = q if isinstance(q, Query) else parse_query(q)
    if not query.tokens:
        return 0.0
    sku_list = [_sku_index(s) for s in skus if s]
    tag_list = [_title_index(s) for s in tags if s]
    sku_bonus = _sku_query_bonus(query, sku_list)
    best: Optional[float] = None
    for n, title in enumerate(titles):
        if not title:
            continue
        s = _score_title(query, _title_index(title), sku_list, tag_list, sku_bonus > 0)
        if s is None:
            continue
        if n:
            s *= 0.9
        if best is None or s > best:
            best = s
    if best is None:
        return float(sku_bonus) if sku_bonus else None
    return best + sku_bonus


@lru_cache(maxsize=16384)
def sql_score(q, name, variant, sku, aliases) -> Optional[float]:
    """Função SQL ``product_search_score(q, nome, variante, sku, apelidos)``.

    ``apelidos``: SKUs extras separados por ``|`` (``GROUP_CONCAT``) ou ``NULL``.
    """
    title = f"{name or ''} {variant or ''}".strip()
    skus = [sku or ""]
    if aliases:
        skus.extend(str(aliases).split("|"))
    return score(q, [title], skus)
