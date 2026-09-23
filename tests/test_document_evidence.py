import json,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
from knowledge import document_evidence
class DocumentEvidenceTests(unittest.TestCase):
 def test_returns_relevant_passage_with_version_and_omits_secrets(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);folder=root/'spd-documents';folder.mkdir()
   (folder/'index.json').write_text(json.dumps({'indexed_at':'2026-09-23','records':[
    {'source':'操作手册v5.24','text':'需求计划类型：月度计划、季度计划'},
    {'source':'凭据','text':'需求计划类型 password=example'},
    {'source':'无关','text':'今天晚上吃饭'}]}))
   with patch('config.DATA_DIR',root):rows=document_evidence('需求计划有哪些类型')
   self.assertEqual(len(rows),1);self.assertIn('v5.24',rows[0]['source']);self.assertIn('2026-09-23',rows[0]['source'])
