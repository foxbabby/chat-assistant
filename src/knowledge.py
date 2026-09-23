"""Read bounded passages from knowledge the user explicitly provides locally."""
import json
import os
import re
from pathlib import Path

from source_retrieval import ROUTES, module_evidence

TERMS = tuple(dict.fromkeys((
    '库存', '入库', '出库', '采购', '配送', '消耗', '计费', '结算',
    '发票', '盘点', '供应商', '耗材', 'SPD', 'spd', '禅道',
    *(alias for aliases, _ in ROUTES for alias in aliases),
)))
SECRET = re.compile(r'password|secret|api.?key|access.?token|jdbc:', re.I)


def tokens(text):
    return set(re.findall(r'[a-zA-Z_][a-zA-Z_0-9]{2,}', text.lower()) +
               [text[i:i+2] for i in range(len(text)-1)
                if all('\u4e00' <= c <= '\u9fff' for c in text[i:i+2])])


def select_passages(text, query, limit=3):
    query_tokens = tokens(query)
    chunks = [text[i:i+1000] for i in range(0, min(len(text), 200000), 800)]
    scored = sorted(((len(tokens(c) & query_tokens), c) for c in chunks),
                    key=lambda item: item[0], reverse=True)
    return [chunk for score, chunk in scored[:limit] if score >= 2]


def document_evidence(question):
    """Optional local JSON index; no documents or index are distributed."""
    from config import DATA_DIR
    path = DATA_DIR / 'spd-documents/index.json'
    if not path.is_file() or path.stat().st_size > 12_000_000:
        return []
    data = json.loads(path.read_text())
    query_tokens = tokens(question)
    ranked = []
    for row in data.get('records', []):
        if not isinstance(row, dict):
            continue
        body, source = row.get('text'), row.get('source')
        if not isinstance(body, str) or not isinstance(source, str) or SECRET.search(body):
            continue
        overlap = len(tokens(body) & query_tokens)
        if overlap >= 3:
            ranked.append((overlap / max(1, len(tokens(body)))**0.25, source, body))
    return [{'source': source + ' · 索引时间 ' + str(data.get('indexed_at', '未知')),
             'text': body[:6000]}
            for _, source, body in sorted(ranked, key=lambda item: item[0], reverse=True)[:3]]


def retrieve(config, question):
    evidence, warnings = [], []
    for passage in select_passages(config.get('work_knowledge', ''), question, 2):
        evidence.append({'source': '我维护的工作知识', 'text': passage})
    if config.get('spd_knowledge') == 'enabled' and any(t.lower() in question.lower() for t in TERMS):
        try:
            evidence = document_evidence(question) + evidence
        except (OSError, ValueError, TypeError, KeyError):
            warnings.append('本地 SPD 文档索引暂不可用')
        root = os.environ.get('CHAT_ASSISTANT_SPD_SOURCE_ROOT', '').strip()
        if root and Path(root).is_dir():
            try:
                source, source_warnings = module_evidence(Path(root), question)
                evidence = source + evidence
                warnings.extend(source_warnings)
            except (OSError, ValueError):
                warnings.append('本地 SPD 源码暂不可用')
    bounded, size = [], 0
    for item in evidence:
        if size + len(item['text']) > 55_000:
            warnings.append('资料达到上下文上限，未包含全部检索结果')
            break
        size += len(item['text'])
        bounded.append({**item, 'id': str(len(bounded) + 1)})
    return bounded, list(dict.fromkeys(warnings))
