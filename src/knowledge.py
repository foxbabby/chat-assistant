"""Retrieve relevant passages from user-maintained knowledge only.

The public distribution does not include private enterprise connectors or data.
"""
import re

TERMS = ('库存','入库','出库','采购','配送','消耗','计费','结算','发票','盘点','供应商','耗材','SPD','spd','禅道')

def tokens(text):
    return set(re.findall(r'[a-zA-Z_][a-zA-Z_0-9]{2,}', text.lower()) +
               [text[i:i+2] for i in range(len(text)-1) if all('\u4e00' <= c <= '\u9fff' for c in text[i:i+2])])


def select_passages(text, query, limit=3):
    query_tokens = tokens(query)
    chunks = [text[i:i+1000] for i in range(0, min(len(text), 200000), 800)]
    scored = sorted(((len(tokens(c) & query_tokens), c) for c in chunks), key=lambda x:x[0], reverse=True)
    return [c for score, c in scored[:limit] if score >= 2]


def retrieve(config, question):
    evidence, warnings = [], []
    def add(source, text):
        for passage in select_passages(text, question, 2):
            evidence.append({'id': str(len(evidence)+1), 'source': source, 'text': passage})
    add('我维护的工作知识', config.get('work_knowledge', ''))
    return evidence[:8], warnings
