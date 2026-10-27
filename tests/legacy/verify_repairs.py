from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"research-evidence-workflow/scripts/tools"
RUN=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from pathlib import Path

import sys,json,unittest,copy

from unittest.mock import patch

from verify_hardening import HardeningTests

import core,evidence_search as es, evidence_decisions as ed,source_providers as sp

class RepairTests(HardeningTests):
 def test_unrelated_registry_change_does_not_destroy_exact_corpus_replay(self):
  q=es.query('corpus',self.manifest,'graph',self.root,original_only=True);f=self.root/'q.json';core.save(f,q)
  m=core.load(self.manifest);m['description']='unrelated annotation';core.save(self.manifest,m)
  r=es.resolve(f,q['candidates'][0]['candidate_id'],self.root)
  self.assertTrue(r['replayed']);self.assertTrue(r['source_registry_changed']);self.assertFalse(r['ranking_replayed'])
 def test_tampered_original_passage_cannot_replay(self):
  q=es.query('corpus',self.manifest,'graph',self.root,original_only=True);f=self.root/'q.json'
  q['candidates'][0]['text']='fabricated assertion';core.save(f,q)
  with self.assertRaises(ValueError):es.resolve(f,q['candidates'][0]['candidate_id'],self.root)
 def test_pool_warning_precedes_quality_filter(self):
  hits=[{'anchor':'x'+str(i),'text':'graph original readable candidate paragraph with enough scientific words.',
   'document_id':'D'+str(i),'title':'T','page':1,'bbox':None,'source':{},'index_binding':{},'score':1.} for i in range(100)]
  hits[0]['text']='\ufffd'*100
  with patch('literature_tools.retrieve',return_value={'hits':hits,'warnings':[],'metadata_only_documents':[],
       'retrieval_policy':'fixture','pool_may_be_truncated':True}):
   q=es.query('library',self.meta,'graph',self.root,pool_limit=100)
  self.assertEqual(q['raw_pool_returned'],100);self.assertEqual(q['quality_filtered_count'],1)
  self.assertTrue(q['candidate_pool_may_be_truncated'])
 def test_provider_doi_normalization_and_no_title_guessing(self):
  self.assertEqual(sp.doi('https://doi.org/10.1000/ABC'),'10.1000/abc')
  self.assertNotEqual(sp.doi('10.1000/A'),sp.doi('10.1000/B'))
 def test_arxiv_version_not_publisher_version(self):
  xml=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Fixture</title><id>https://arxiv.org/abs/0000.0000v2</id><published>2020</published><updated>2021</updated><link title="pdf" href="https://arxiv.org/pdf/0000.0000v2"/></entry></feed>'
  row=sp.parse('arxiv',xml)[0];self.assertIn('preprint',row['version'])
  self.assertNotEqual(row['date_fields']['published'],row['date_fields']['updated'])
 def test_unknown_claim_never_exports_answer_despite_candidate_presence(self):
  f=self.root/'claim.json';core.save(f,{'schema':'research-claim-review/1.0','reviewer':{'id':'R','method':'fixture'},
   'claim':'No local measurement exists','qualified_answer':'An invented favourable answer',
   'parts':[{'id':'P1','text':'The new city improves by 2 degrees','decision':'unknown','rationale':'No site measurements'}]})
  r=ed.audit(f,self.root);self.assertIsNone(r['answer']);self.assertFalse(r['can_export_assertion'])
 def test_supported_claim_cannot_use_unreviewed_candidate(self):
  f=self.root/'claim.json';core.save(f,{'schema':'research-claim-review/1.0','reviewer':{'id':'R','method':'fixture'},
   'claim':'A supported statement','parts':[{'id':'P1','text':'Statement','decision':'supported','rationale':'candidate exists'}]})
  with self.assertRaises(ValueError):ed.audit(f,self.root)
 def test_claim_independent_requirement_does_not_self_certify(self):
  f=self.root/'claim.json';core.save(f,{'schema':'research-claim-review/1.0','reviewer':{'id':'R','method':'fixture'},
   'claim':'Unknown statement','parts':[{'id':'P1','text':'Statement','decision':'unknown','rationale':'missing'}]})
  r=ed.audit(f,self.root,True);self.assertEqual(r['independent_status'],'not_performed')
  self.assertTrue(any(x['part']=='independent_review' for x in r['gaps']))
