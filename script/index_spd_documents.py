"""Build a private text index from explicitly downloaded SPD reference documents."""
import hashlib,json,os,sys,zipfile
from datetime import datetime,timezone
from pathlib import Path
from xml.etree import ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
from config import DATA_DIR,atomic_json
from source_retrieval import SECRET
root=DATA_DIR/'spd-documents'
from work_config import WORK_SETTINGS
SOURCES = WORK_SETTINGS.get('document_sources', [])
records=[];coverage=[]
for name,title,node in SOURCES:
 file=root/name
 if not file.is_file():
  coverage.append({'title':title,'status':'not_downloaded'});continue
 paragraphs=[]
 if name.endswith('.docx'):
  with zipfile.ZipFile(file) as z:
   xml=z.read('word/document.xml')
   tree=ET.fromstring(xml);ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
   for p in tree.iter(ns+'p'):
    text=''.join(t.text or '' for t in p.iter(ns+'t')).strip()
    if text:paragraphs.append(text)
 else:
  import xlrd
  book=xlrd.open_workbook(file)
  for sheet in book.sheets():
   headers=' | '.join(str(v) for v in sheet.row_values(0)) if sheet.nrows else ''
   for row in range(sheet.nrows):
    values=' | '.join(str(v).strip() for v in sheet.row_values(row) if str(v).strip())
    if values:paragraphs.append('工作表：'+sheet.name+'；表头：'+headers+'；行'+str(row+1)+'：'+values)
 chunks=[];buf=''
 for p in paragraphs:
  if SECRET.search(p):continue
  if len(buf)+len(p)>1600 and buf:chunks.append(buf);buf=''
  buf+='\n'+p[:5000]
 if buf:chunks.append(buf)
 for i,chunk in enumerate(chunks):records.append({'source':title+'（本地资料快照） 段落组'+str(i+1),'text':chunk,'node_id':node})
 coverage.append({'title':title,'status':'indexed','paragraphs':len(paragraphs),'chunks':len(chunks),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
index={'indexed_at':datetime.now(timezone.utc).isoformat(),'coverage':coverage,'records':records}
body=json.dumps(index,ensure_ascii=False).encode()
if len(body)>12000000:raise SystemExit('Index over 12MB; not installed')
atomic_json(root/'index.json',index)
print(json.dumps({'coverage':coverage,'bytes':len(body)},ensure_ascii=False))
