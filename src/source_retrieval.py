"""Read-only, bounded business-module retrieval from the installed SPD source tree."""
import re
from pathlib import Path

SECRET = re.compile(r'password|secret|api.?key|access.?token|jdbc:', re.I)
ROUTES = (
    (('供应商结账', '供应商结算', '账入库', '结账单'), 'spd/spd-keep-accounts'),
    (('需求计划', '申领计划'), 'spd/spd-require-plan'),
    (('采购计划','采购订单','采购'), 'spd/spd-purchase'),
    (('入库','收货'), 'spd/spd-warehouse-entry'),
    (('配送','出库'), 'spd/spd-deliver-receive'),
    (('付款','支付'), 'spd/spd-payment'),
    (('发票',), 'spd/spd-invoice'),
    (('收费','计费'), 'spd/spd-charge'),
    (('消耗','成本'), 'spd/spd-consume'),
    (('盘点',), 'spd/spd-special-function'),
    (('库存','调拨'), 'spd/spd-stock'),
    (('打包','拆包','定数包'), 'spd/spd-pack-unpack'),
    (('耗材字典','供货清单','供应商','库房目录','基础数据'), 'spd/spd-basic-data'),
)
ENTITIES = {'spd/spd-require-plan': 'RequirePlan',
            'spd/spd-keep-accounts': 'AccountEntry',
            'spd/spd-purchase': 'PurchasePlan',
            'spd/spd-warehouse-entry': 'CenterWarehouseEntry',
            'spd/spd-payment': 'PayPlan',
            'spd/spd-invoice': 'InvoiceRegister',
            'spd/spd-charge': 'MaterialCharge',
            'spd/spd-stock': 'WarehouseStockCheck',
            'spd/spd-special-function': 'WarehouseStockCheck',
            'spd/spd-basic-data': 'Material'}


def definition_evidence(root, route, question):
    """Shared domain definitions live outside the business implementation module."""
    if not any(word in question for word in ('类型', '种类', '状态', '枚举')):
        return []
    name = ENTITIES.get(route)
    if not name:
        return []
    file = root / 'spd/spd-dao/src/main/java/com/etime/spd/domain' / (name + '.java')
    if not file.is_file():
        return []
    text = _read(file, root)
    lines = text.splitlines()
    selected = set()
    for i, line in enumerate(lines):
        if re.search(r'public\s+static\s+final\s+', line):
            selected.update(range(max(0,i-5),min(len(lines),i+2)))
    if not selected:
        return []
    body = '\n'.join(str(i+1)+': '+lines[i] for i in sorted(selected))
    if len(body) > 24000:
        return []  # Never present a truncated enum family as exhaustive.
    return [{'source':'本地共享模型（非现场字典） '+str(file.relative_to(root)),
             'text':'以下为实际常量声明及邻近注释。声明可能多于旧注释所列值；不得将注释当作完整枚举。现场启用值仍需字典验证。\n'+body}]


def _read(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or path.is_symlink() or path.stat().st_size > 500000:
        return ''
    text = path.read_text(errors='replace')
    return '' if SECRET.search(text) else text


def _split_method(text):
    # Start at the real split implementation, excluding repeated copy/save boilerplate.
    match = re.search(r'public\s+(?:<[^>]+>\s+)?List<[^>]+>\s+split\s*\([^)]*\)\s*\{', text)
    if not match:
        return ''
    start = match.start()
    depth = 1
    pos = match.end()
    # Mask quoted strings/comments while preserving offsets for brace balancing.
    masked = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/',
                    lambda m: ' ' * len(m.group()), text)
    while pos < len(text) and depth:
        depth += (masked[pos] == '{') - (masked[pos] == '}')
        pos += 1
    return text[start:pos] if depth == 0 else ''


def module_evidence(root, question):
    """Return source objects and honest coverage warnings; no generated facts."""
    from knowledge import tokens
    root = Path(root)
    route = next((module for aliases, module in ROUTES if any(a in question for a in aliases)), None)
    if not route:
        # Fall back to the shared model's Chinese description to discover a domain
        # instead of treating the two original hand-picked modules as all of SPD.
        domain = root / 'spd/spd-dao/src/main/java/com/etime/spd/domain'
        from knowledge import tokens
        ranked = []
        for file in sorted(domain.glob('*.java')):
            text = _read(file, root)
            score = len(tokens(text[:2500]) & tokens(question))
            if score >= 4:
                ranked.append((score,file,text))
        return ([{'source':'本地共享模型（非现场版本） '+str(f.relative_to(root)),
                  'text':body[:14000]} for _,f,body in sorted(ranked,key=lambda x:x[0],reverse=True)[:3]],
                ['通用数据模型检索，尚未定位完整业务调用链'] if ranked else [])
    if not (root / route).is_dir():
        return [], []
    module = root / route
    result, warnings = definition_evidence(root, route, question), []
    if route.endswith('spd-keep-accounts') and any(t in question for t in ('拆单', '拆分', '分单')):
        files = sorted(module.glob('**/account/rule/impl/EntrySplitRule*.java'))
        if files:
            for file in files[:24]:
                text = _read(file, root)
                method = _split_method(text)
                if not method or len(method) > 10000:
                    warnings.append('部分拆单规则无法完整读取，规则清单可能不完整')
                    continue
                line = text[:text.index(method)].count('\n') + 1
                result.append({'source': '本地源码（非现场版本） ' + str(file.relative_to(root)) + ':' + str(line),
                               'text': '规则实现类：' + file.stem + '\n' + method})
            if len(files) > 24:
                warnings.append('拆单规则超过读取上限，清单不是完整枚举')
            # The call site proves rules are chained, rather than mutually exclusive.
            for file in module.glob('**/AccountEntrySplitHelper.kt'):
                text = _read(file, root)
                if text and len(text) <= 5000:
                    result.append({'source':'本地源码（非现场版本） '+str(file.relative_to(root))+':1', 'text':text})
            return result, warnings
    # Read implementation bodies in the matched module, not the first two keyword
    # hits across the entire repository. Keep distinct files and source line anchors.
    query_tokens = tokens(question)
    ranked = []
    files = sorted(f for f in module.rglob('*') if f.suffix in ('.java','.kt','.xml') and 'target' not in f.parts)
    for file in files[:800]:
        if 'Import' in file.stem and '导入' not in question:
            continue
        if 'Urgent' in file.stem and '紧急' not in question and not result:
            continue
        text = _read(file, root)
        if not text or re.search(r'public\s+interface\s', text):
            continue
        lines = text.splitlines()
        for start in range(0, len(lines), 35):
            chunk = '\n'.join(lines[start:start+85])
            signatures = [line.strip() for line in lines[:start+1]
                          if re.search(r'\b(?:public|private|protected)\b.*\([^;]*\)\s*\{|\bfun\s+\w+\(', line)]
            method = signatures[-1] if signatures else ''
            overlap = len(tokens(chunk) & query_tokens)
            if overlap < (2 if len(query_tokens) <= 8 else 3):
                continue
            score = overlap
            if any(t in question for t in ('查询','查不到','查不出来')):
                score += sum(t in chunk for t in ('过滤','停用','目录','搜索','查询条件','权限')) * 3
            if '/helper/' in str(file) or '/service/impl/' in str(file):
                score += 2
            if ('replenishment' in method.lower() or '补货' in chunk) and '补货' not in question:
                score -= 15
            chunk = '片段起始所属方法：' + method + '\n以下是局部实现，不能据此推定所有入口：\n' + chunk
            ranked.append((score, file, start+1, chunk))
    seen = set()
    for score, file, line, chunk in sorted(ranked, key=lambda row:row[0], reverse=True):
        if file in seen:
            continue
        seen.add(file)
        result.append({'source':'本地源码（非现场版本） '+str(file.relative_to(root))+':'+str(line), 'text':chunk[:7000]})
        if len(result) >= 6:
            break
    if len(files) > 800:
        warnings.append('业务模块检索达到文件上限，未覆盖全部实现')
    return result, warnings
