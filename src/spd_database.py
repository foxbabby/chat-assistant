"""Small, read-only SPD test database lookups using the user's local pgpass file.

Only fixed catalog queries and exact material codes are accepted. Chat text is
never executed as SQL, and database rows are not copied wholesale to the model.
"""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from work_config import WORK_SETTINGS

PASSFILE = Path(WORK_SETTINGS.get('spd_passfile', str(Path.home() / '.config/chat-assistant/spd.pgpass')))
CONNECTION = WORK_SETTINGS.get('spd_connection', '')
TOPIC_TABLES = (
    (('需求计划', '申领计划'), ('require_plan', 'require_plan_detail', 'material')),
    (('供应商结账', '结账单', '账入库'), ('account_entry', 'account_entry_detail')),
    (('采购',), ('purchase_plan', 'purchase_plan_detail')),
    (('入库', '收货'), ('center_warehouse_entry', 'center_warehouse_entry_detail')),
    (('付款', '支付'), ('pay_plan',)),
    (('发票',), ('invoice_register',)),
    (('收费', '计费'), ('material_charge',)),
    (('盘点',), ('warehouse_stock_check',)),
    (('耗材', '物资'), ('material',)),
)


class DatabaseUnavailable(Exception):
    pass


def _query(sql):
    if not CONNECTION:
        raise DatabaseUnavailable('尚未配置本机测试库连接')
    try:
        info = PASSFILE.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise DatabaseUnavailable('测试库凭据文件权限不安全')
    except FileNotFoundError:
        raise DatabaseUnavailable('本机没有测试库凭据') from None
    psql = shutil.which('psql')
    if not psql:
        raise DatabaseUnavailable('本机没有 PostgreSQL 客户端')
    env = {**os.environ, 'PGPASSFILE': str(PASSFILE),
           'PGOPTIONS': '-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'}
    try:
        result = subprocess.run([psql, CONNECTION, '-X', '-A', '-F', '\t', '-t',
                                 '-v', 'ON_ERROR_STOP=1', '-c', sql],
                                env=env, capture_output=True, text=True, timeout=9)
    except (OSError, subprocess.TimeoutExpired):
        raise DatabaseUnavailable('测试库连接或查询超时') from None
    if result.returncode:
        # psql stderr can include server values; keep it out of UI and logs.
        raise DatabaseUnavailable('测试库查询失败')
    return [row.split('\t') for row in result.stdout.splitlines() if row]


def database_evidence(question):
    """Return bounded structural evidence plus aggregate counts for an exact code."""
    tables = next((names for aliases, names in TOPIC_TABLES
                   if any(alias in question for alias in aliases)), ())
    if not tables:
        return []
    allowed = ','.join("'" + name + "'" for name in tables)
    rows = _query("SELECT table_name,column_name FROM information_schema.columns "
                  "WHERE table_schema='public' AND table_name IN (" + allowed + ") "
                  "ORDER BY table_name,ordinal_position LIMIT 120")
    grouped = {}
    for table, column in rows:
        if table in tables and re.fullmatch(r'[a-z_][a-z_0-9]*', column):
            grouped.setdefault(table, []).append(column)
    if not grouped:
        return []
    text = '测试库表结构（字段存在不代表功能启用或现场数据状态）：\n'
    text += '\n'.join(name + ': ' + ', '.join(columns[:35])
                      for name, columns in grouped.items())
    evidence = [{'source': 'SPD测试数据库 · 表结构', 'text': text[:5000]}]
    match = re.search(r'(?:耗材编码|物资编码|物料编码|材料编码)[：:\s]+([A-Za-z0-9._-]{4,40})', question)
    if match and 'material' in grouped:
        code = match.group(1)  # strict character allowlist makes the fixed SQL literal safe
        counts = _query("SELECT COALESCE(material_status,'<空>'), "
                        "COALESCE(audit_level,'<空>'), COALESCE(hospital_apply_yn,'<空>'), count(*) "
                        "FROM public.material WHERE material_code='" + code + "' "
                        "GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20")
        lines = ['编码 ' + code + ' 在测试库的状态、审核级别、院内申领标记及记录数：']
        lines.extend(' / '.join(row) for row in counts if len(row) == 4)
        if not counts:
            lines.append('未找到对应记录')
        evidence.append({'source': 'SPD测试数据库 · 耗材编码精确匹配（非现场数据）',
                         'text': '\n'.join(lines)[:2000]})
    return evidence
