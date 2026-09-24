"""Bounded, read-only work evidence. Never execute commands supplied by a message."""
import json
import re
import shutil
import subprocess
import time
import threading
from pathlib import Path
from work_config import WORK_SETTINGS

DWS = str(Path.home() / '.local/bin/dws')
SPD_ROOT = Path(WORK_SETTINGS.get('spd_root', str(Path.home() / 'Documents/SPD/spd-lts')))
PROFILE = WORK_SETTINGS.get('knowledge_profile', '')
EXPERIENCE_NODE = WORK_SETTINGS.get('experience_node', '')
TERMS = ('供应商结账','结账单','账入库','需求计划','库存','入库','出库','采购','配送','消耗','计费','结算','发票','盘点','供应商','耗材','SPD','spd','禅道','拆单','分单')
from source_retrieval import ROUTES
TERMS = tuple(dict.fromkeys((*TERMS, *(alias for aliases, _ in ROUTES for alias in aliases))))

class EvidenceUnavailable(ValueError):
    pass


def run_dws(args, profile=True, timeout=15):
    cmd = [DWS, *args, '--format', 'json']
    if profile:
        if not PROFILE:
            raise EvidenceUnavailable('尚未配置本机工作知识账号')
        cmd += ['--profile', PROFILE]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise EvidenceUnavailable('钉钉资料读取失败')
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise EvidenceUnavailable('钉钉返回格式无法读取') from None
    if data.get('error') or data.get('success') is False or data.get('status') == 'error':
        raise EvidenceUnavailable('钉钉资料读取失败')
    return data


def verify_profile():
    data = run_dws(['profile', 'list'], profile=False)
    matches = [p for p in data.get('profiles', []) if p.get('profile') == PROFILE
               and p.get('isOrgCurrent') is True and p.get('isCurrent') is True
               and p.get('corpName') == WORK_SETTINGS.get('knowledge_company') and p.get('userName') == WORK_SETTINGS.get('knowledge_user')]
    if len(matches) != 1:
        raise EvidenceUnavailable('钉钉当前账号与工作知识账号不一致')


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.lower() not in ('token','accesstoken','refreshtoken','trace_id','logid','nextpagetoken','url','docurl','nodeid'):
                yield from strings(item)


def tokens(text):
    return set(re.findall(r'[a-zA-Z_][a-zA-Z_0-9]{2,}', text.lower()) +
               [text[i:i+2] for i in range(len(text)-1) if all('\u4e00' <= c <= '\u9fff' for c in text[i:i+2])])


def select_passages(text, query, limit=3):
    query_tokens = tokens(query)
    chunks = [text[i:i+1000] for i in range(0, min(len(text), 200000), 800)]
    scored = sorted(((len(tokens(c) & query_tokens), c) for c in chunks), key=lambda x:x[0], reverse=True)
    return [c for score, c in scored[:limit] if score >= 2]


_dictionary_cache = None
_dictionary_lock = threading.Lock()


def dictionary_passages(question):
    """The upstream full-text query currently fails serialization; match locally.

    Cache only complete bounded reads, in memory, for two minutes. The caller
    verifies the configured work profile on every retrieval, including cache hits.
    """
    global _dictionary_cache
    with _dictionary_lock:
        if _dictionary_cache is None or time.monotonic() - _dictionary_cache[0] >= 120:
            data = run_dws(['aitable','record','query','--base-id',WORK_SETTINGS.get('dictionary_base', ''),
                            '--table-id',WORK_SETTINGS.get('dictionary_table', ''),'--limit','100','--all','--page-limit','10'], timeout=45)
            payload = data.get('data', {})
            records = payload.get('records')
            if (payload.get('complete') is not True or payload.get('hasMore') is not False
                    or payload.get('nextCursor') or not isinstance(records,list) or len(records)>1000
                    or any(not isinstance(row,dict) or not isinstance(row.get('cells'),dict) for row in records)):
                raise EvidenceUnavailable('报错字典读取不完整')
            texts = ['\n'.join(strings(row['cells'])) for row in records]
            texts = [t for t in texts if t and len(t)<30000 and not re.search(r'password|secret|api.?key|access.?token|jdbc:',t,re.I)]
            _dictionary_cache = (time.monotonic(),texts)
        texts = _dictionary_cache[1]
    query_tokens = tokens(question)
    ranked = sorted(((len(tokens(t)&query_tokens),t) for t in texts),key=lambda pair:pair[0],reverse=True)
    return [text for score,text in ranked[:5] if score>=2]


def document_evidence(question):
    from config import DATA_DIR
    path = DATA_DIR / 'spd-documents/index.json'
    if not path.is_file() or path.stat().st_size > 12000000:
        return []
    data = json.loads(path.read_text())
    query_tokens = tokens(question)
    ranked = []
    for row in data.get('records', []):
        text = row.get('text', '')
        if not isinstance(text,str) or re.search(r'password|secret|api.?key|access.?token|jdbc:',text,re.I):
            continue
        overlap = len(tokens(text) & query_tokens)
        if overlap < 3:
            continue
        score = overlap / max(1,len(tokens(text)))**0.25
        ranked.append((score,row))
    return [{'source':row['source']+' · 索引时间 '+data.get('indexed_at','未知'), 'text':row['text'][:6000]}
            for _,row in sorted(ranked,key=lambda pair:pair[0],reverse=True)[:3]]


def retrieve(config, question):
    evidence, warnings = [], []
    def add(source, text):
        for passage in select_passages(text, question, 2):
            evidence.append({'id': str(len(evidence)+1), 'source': source, 'text': passage})
    add('我维护的工作知识', config.get('work_knowledge', ''))
    relevant = [term for term in TERMS if term in question]
    if not relevant or config.get('spd_knowledge') != 'enabled':
        return evidence, warnings
    try:
        evidence = document_evidence(question) + evidence
    except (OSError, ValueError, TypeError, KeyError):
        warnings.append('本地 SPD 文档索引暂不可用')
    from source_retrieval import module_evidence
    try:
        source_items, source_warnings = module_evidence(SPD_ROOT, question)
        warnings.extend(source_warnings)
    except (OSError, ValueError):
        source_items = []
        warnings.append('业务模块源码读取失败')
    # The test DB uses the same direct connection as the user's working Navicat
    # profile. Query only a fixed table allowlist, with a read-only session.
    from spd_database import PASSFILE, DatabaseUnavailable, database_evidence
    db_items = []
    if PASSFILE.exists():
        try:
            db_items = database_evidence(question)
        except DatabaseUnavailable:
            warnings.append('SPD测试数据库暂不可用')
    # Topic keyword only; never send a whole private chat to the document search service.
    keyword = next((t for t in relevant if t.lower() != 'spd'), 'SPD')
    try:
        verify_profile()
    except (EvidenceUnavailable, OSError, subprocess.TimeoutExpired):
        warnings.append('钉钉工作账号未就绪，未读取在线资料')
    else:
        try:
            for text in dictionary_passages(question):
                add('报错排查字典', text)
        except (EvidenceUnavailable, OSError, subprocess.TimeoutExpired, TypeError, AttributeError):
            warnings.append('报错排查字典暂不可用')
        try:
            data = run_dws(['doc', '+fetch', '--node', EXPERIENCE_NODE, '--scope', 'full'])
            content = data.get('content') or data.get('data') or data.get('matches') or ''
            add('现场群问题经验库', '\n'.join(strings(content)))
        except (EvidenceUnavailable, OSError, subprocess.TimeoutExpired):
            warnings.append('现场群问题经验库暂不可用')
    # Source snippets are supporting evidence only, never proof of production behavior.
    rg = shutil.which('rg') or '/opt/homebrew/bin/rg'
    if not source_items and SPD_ROOT.is_dir() and Path(rg).is_file():
        try:
            result = subprocess.run([rg, '-n', '-F', '-m', '2', '-g', '*.java', '-g', '*.xml',
                                     '--glob', '!**/target/**', '--', keyword, str(SPD_ROOT)],
                                    capture_output=True, text=True, timeout=8)
            lines = result.stdout.splitlines()
            # Rank actual implementation windows instead of returning only interfaces
            # whose comments happen to repeat the question's exact words.
            candidates = []
            seen = set()
            for line in lines[:500]:
                path, number, _ = line.split(':', 2)
                file = Path(path)
                if file.stat().st_size > 500000:
                    continue
                n = int(number); body = file.read_text(errors='replace').splitlines()
                start = max(0,n-12)
                snippet = '\n'.join(body[start:n+35])
                if re.search(r'password|secret|api.?key|access.?token|jdbc:', snippet, re.I):
                    continue
                if str(file) in seen:
                    continue
                seen.add(str(file))
                score = len(tokens(snippet) & tokens(question))
                if 'interface ' in snippet:
                    score -= 8
                if any(word in snippet for word in ('过滤', '停用', '查询条件', '目录', '权限')):
                    score += 4
                candidates.append((score, str(file.relative_to(SPD_ROOT))+':'+str(start+1), snippet))
            for _, source, snippet in sorted(candidates, key=lambda x:x[0], reverse=True)[:4]:
                add('本地源码（非现场版本） ' + source, snippet)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            warnings.append('本地 SPD 源码暂不可用')
    # Business rules must not be lost behind a generic profile paragraph or an
    # unrelated case. Preserve all bounded rule implementations before anecdotes.
    # Reserve space per source: manuals/profile text must not crowd the repaired
    # dictionary out of the model context.
    supplemental = []
    for prefix,limit in [('报错排查字典',2),('SPD操作手册',2),('新SPD_',2),
                         ('现场群问题经验库',2),('我维护的工作知识',1)]:
        supplemental.extend([e for e in evidence if e['source'].startswith(prefix)][:limit])
    selected = source_items + db_items + supplemental if source_items else (
        db_items + supplemental + [e for e in evidence if e not in supplemental])[:10]
    if source_items:
        try:
            revision = subprocess.run(['git','-C',str(SPD_ROOT),'log','-1','--format=%h %cs'],
                                      capture_output=True,text=True,timeout=3,check=True).stdout.strip()
            for item in source_items:
                item['source'] += ' · 本地代码 '+revision
        except (OSError, subprocess.SubprocessError):
            warnings.append('无法核对本地源码版本')
    bounded, size = [], 0
    for item in selected:
        if size + len(item['text']) > 55000:
            warnings.append('资料达到上下文上限，未包含全部检索结果')
            break
        size += len(item['text'])
        bounded.append({**item, 'id':str(len(bounded)+1)})
    return bounded, list(dict.fromkeys(warnings))
