import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
import knowledge as k
class DictionaryTests(unittest.TestCase):
 def setUp(self):k._dictionary_cache=None
 def tearDown(self):k._dictionary_cache=None
 def response(self,**kw):return {'data':{'complete':True,'hasMore':False,'records':[{'cells':{'f':'需求计划耗材查询不到时核对目录'}},{'cells':{'f':'其他事项'}}],**kw}}
 def test_full_read_local_search_and_cache(self):
  with patch('knowledge.run_dws',return_value=self.response()) as cli:
   self.assertEqual(len(k.dictionary_passages('需求计划查询耗材')),1)
   self.assertEqual(len(k.dictionary_passages('需求计划目录')),1)
   cli.assert_called_once();self.assertNotIn('--query',cli.call_args.args[0])
 def test_partial_or_malformed_never_cached(self):
  for obj in [self.response(complete=False),self.response(hasMore=True),self.response(nextCursor='next'),self.response(records='bad'),self.response(records=[{'cells':None}])]:
   with patch('knowledge.run_dws',return_value=obj):
    with self.assertRaises(k.EvidenceUnavailable):k.dictionary_passages('需求计划')
    self.assertIsNone(k._dictionary_cache)
 def test_expired_cache_requires_successful_refresh(self):
  k._dictionary_cache=(-1000,['旧资料需求计划'])
  with patch('knowledge.run_dws',side_effect=k.EvidenceUnavailable('error')):
   with self.assertRaises(k.EvidenceUnavailable):k.dictionary_passages('需求计划')
