"""Fully synthetic reviewed graph inputs; no actual reading or human review."""
from pathlib import Path
import struct,sys,zlib
RELEASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(RELEASE/'research-evidence-workflow/scripts/tools'))
sys.path.insert(1,str(RELEASE/'tests/legacy'))
import core,evidence_decisions as ed
from verify_generalization import GeneralizationTests

def chunk(kind,value):return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value)&0xffffffff)

class GraphFixture:
    def __init__(self):
        self.case=GeneralizationTests();self.case.setUp();self.root=self.case.root
        self.graph=self.case.graph_fixture()[0];self.scoped=self.case.scoped_fixture()
        preview=self.root/'page.png'
        preview.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',100,100,8,6,0,0,0))+
            chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*400)*100))+chunk(b'IEND',b''))
        record=core.load(self.scoped);row=record['reviews'][0];row['visual_checks'][0]['sha256']=core.sha(preview);core.save(self.scoped,record)
        query=core.load(self.root/row['query']['path']);candidate=next(c for c in query['candidates'] if c['candidate_id']==row['candidate_ids'][0])
        self.claim=self.root/'claim.json'
        core.save(self.claim,{'schema':'research-claim-review/1.0','claim':row['claim'],
            'reviewer':{'id':'synthetic-primary','method':'Synthetic provenance regression; no scientific reading'},
            'parts':[{'id':'P1','text':row['claim'],'rationale':'Synthetic binding check only','decision':'supported',
                'conditions':row['conditions'],'material_types':['text'],'units_and_population':'Synthetic fixture only',
                'negation_and_alternatives':'No scientific alternatives inferred','external_validity':'No generalization claimed',
                'citations':[{'query':row['query'],'candidate_id':candidate['candidate_id'],'excerpt':candidate['text'],
                              'visual_check':row['visual_checks'][0]}]}],
            'qualified_answer':'Synthetic scoped fixture statement only'})
        self.independent=self.root/'independent.json'
        core.save(self.independent,{'schema':'research-independent-review/1.0','reviewer_id':'synthetic-second',
            'reviewed_claim_sha256':core.sha(self.claim),'parts':[{'id':'P1','decision':'agree','rationale':'Synthetic provenance check, not expert validation'}]})
        self.decisions={row['id']:{'path':self.claim.name,'sha256':core.sha(self.claim),
            'independent_report':{'path':self.independent.name,'sha256':core.sha(self.independent)}}}
        self.output=self.root/'published.json'
    def publish(self,**kwargs):return ed.update_graph(self.graph,self.scoped,self.decisions,self.root,self.output,**kwargs)
    def close(self):self.case.tearDown()
