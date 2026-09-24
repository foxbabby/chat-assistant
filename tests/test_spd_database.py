import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
import spd_database as db


class SpdDatabaseTests(unittest.TestCase):
    def test_read_only_connection_and_no_raw_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            passfile = Path(tmp) / 'pgpass'
            passfile.write_text('test credential')
            os.chmod(passfile, 0o600)
            with patch.object(db, 'CONNECTION', 'host=example.invalid sslmode=disable'), patch.object(db, 'PASSFILE', passfile), patch('spd_database.shutil.which', return_value='/usr/bin/psql'), patch('spd_database.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='material\tmaterial_code\n')) as run:
                self.assertEqual(db._query('SELECT 1'), [['material', 'material_code']])
                self.assertIn('default_transaction_read_only=on', run.call_args.kwargs['env']['PGOPTIONS'])
                self.assertEqual(run.call_args.kwargs['env']['PGPASSFILE'], str(passfile))
                self.assertIn('sslmode=disable', run.call_args.args[0][1])

    def test_only_fixed_schema_and_exact_material_code(self):
        with patch('spd_database._query', side_effect=[
            [['material', 'material_code'], ['material', 'material_status']],
            [['ACTIVE', '1', 'Y', '2']]]) as query:
            evidence = db.database_evidence('耗材编码：A-1234 为什么查不到')
        self.assertEqual(len(evidence), 2)
        self.assertIn('测试数据库', evidence[1]['source'])
        self.assertIn("material_code='A-1234'", query.call_args_list[1].args[0])
        with patch('spd_database._query', return_value=[['material', 'material_code']]) as query:
            db.database_evidence("耗材编码：A-1234';DROP TABLE material;--")
        self.assertTrue(all('DROP TABLE' not in call.args[0] for call in query.call_args_list))


if __name__ == '__main__':
    unittest.main()
