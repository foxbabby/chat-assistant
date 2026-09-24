import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
from source_retrieval import module_evidence
from knowledge import retrieve
from config import DEFAULTS

class SourceRetrievalTests(unittest.TestCase):
    def test_types_include_shared_model_and_values_missing_from_old_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'spd/spd-require-plan').mkdir(parents=True)
            domain=root/'spd/spd-dao/src/main/java/com/etime/spd/domain'
            domain.mkdir(parents=True)
            (domain/'RequirePlan.java').write_text('class RequirePlan {\n// 类型 MONTH 月度\npublic static final String TYPE_MONTH = "MONTH";\npublic static final String TYPE_URGENT = "URGENT";\n}')
            e,w=module_evidence(root,'需求计划有哪些类型')
            self.assertEqual(len(e),1)
            self.assertIn('TYPE_URGENT',e[0]['text'])
            self.assertIn('spd-dao',e[0]['source'])
            self.assertEqual(module_evidence(root,'需求计划耗材查询不到'),([],[]))

    def test_rule_family_not_capped_at_eight_and_kotlin_entry_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rule=root/'spd/spd-keep-accounts/src/account/rule/impl'
            rule.mkdir(parents=True)
            for i in range(10):
                (rule/f'EntrySplitRule{i}.java').write_text('public class Rule { public <T> List<T> split(Collection<T> rows) { if (rows.isEmpty()) { return rows; } /* } */ String x="{"; return grouped; } void other() { } }')
            (root/'spd/spd-keep-accounts/AccountEntrySplitHelper.kt').write_text('for (rule in rules) { result = rule.split(result) }')
            with patch('knowledge.SPD_ROOT',root), patch('knowledge.document_evidence',return_value=[]), patch('knowledge.verify_profile',side_effect=OSError), patch('spd_database.database_evidence',return_value=[]):
                e,w=retrieve(DEFAULTS,'供应商结账单拆单规则有哪些')
            self.assertEqual(len(e),11)
            self.assertEqual(len({x['id'] for x in e}),11)
            self.assertTrue(any('.kt' in x['source'] for x in e))
            self.assertNotIn('void other()',e[0]['text'])

    def test_module_scope_implementation_and_secret_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);module=root/'spd/spd-require-plan';module.mkdir(parents=True)
            (module/'Actual.kt').write_text('需求计划 耗材查询 过滤 目录 停用 查询条件\nif (disabled) return emptyList()')
            (module/'Api.java').write_text('public interface Query { /*需求计划 耗材查询 过滤 目录 停用*/ }')
            (module/'Sensitive.java').write_text('需求计划 耗材查询 过滤 目录 password=example')
            other=root/'outside.java';other.write_text('需求计划 耗材查询 过滤 目录')
            (module/'Linked.java').symlink_to(other)
            e,w=module_evidence(root,'需求计划耗材查询不出来')
            self.assertEqual(len(e),1)
            self.assertIn('Actual.kt',e[0]['source'])
            self.assertEqual(module_evidence(root,'普通聊天'),([],[]))

    def test_disabled_knowledge_does_not_read_source(self):
        with patch('source_retrieval.module_evidence') as source, patch('knowledge.run_dws') as remote:
            retrieve({**DEFAULTS,'spd_knowledge':'disabled'},'供应商结账单拆单规则')
            source.assert_not_called();remote.assert_not_called()

    def test_unspecified_split_finds_rules_but_keeps_scope_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rules = root/'spd/spd-keep-accounts/src/account/rule/impl'
            rules.mkdir(parents=True)
            (rules/'EntrySplitRuleOne.java').write_text('public List<Row> split(List<Row> rows) { return rows; }')
            evidence, warnings = module_evidence(root, 'spd有几种拆单方式')
            self.assertEqual(len(evidence), 1)
            self.assertIn('不代表整个 SPD', warnings[0])
            # An explicit different module must never be silently replaced.
            self.assertEqual(module_evidence(root, '采购订单拆单方式'), ([], []))
