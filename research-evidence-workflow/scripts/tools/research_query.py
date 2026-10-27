"""Whole-instance retrieval entry, preserving separate original query records."""
from pathlib import Path
import argparse
import json
import core
import evidence_search as es


def run(config,question,root,output,model_dir=None):
    root=Path(root).resolve();spec=core.load(config);output=Path(output).resolve()
    if spec.get('schema')!='research-retrieval-config/1.0':raise ValueError('Unsupported source configuration')
    if output.exists():raise FileExistsError('Keep query histories')
    output.mkdir(parents=True);groups=[]
    for number,item in enumerate(spec['sources']):
        source=core.inside(root,item['path'])
        semantic=core.inside(root,item['semantic_index']) if item.get('semantic_index') else None
        model=Path(model_dir) if model_dir else core.inside(root,spec['model_dir']) if spec.get('model_dir') else None
        warning=None
        options={}
        if semantic and semantic.is_file() and model:
            options={'semantic_index':semantic,'model_dir':model}
        elif item.get('semantic_index'):
            warning='Semantic cache/model unavailable; explicit lexical-only result, not cross-language coverage'
        if spec.get('translation_model'):
            options['translation_model']=core.inside(root,spec['translation_model'])
        try:
            result=es.query(item['backend'],source,question,root,limit=20,original_only=True,
                            per_document=3,**options)
        except ImportError as exc:
            # No network install or automatic substitute model.
            result=es.query(item['backend'],source,question,root,limit=20,original_only=True,per_document=3)
            warning='Optional semantic runtime unavailable: '+str(exc)
        path=output/f'query_{number+1}.json';core.save(path,result)
        groups.append({'source_label':item['label'],'query':{'path':path.relative_to(root).as_posix(),'sha256':core.sha(path)},
                       'candidates':len(result['candidates']),'warning':warning,'scope':item['scope']})
    report={'schema':'research-instance-query/1.0','question':question,'sources':groups,
        'answer_status':'insufficient_reviewed_evidence','answer':None,
        'scope':'Includes every configured formal batch plus separately registered source supplements. Duplicate versions are not new papers; each source retains original identity and review state.'}
    core.save(output/'summary.json',report);return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--question',required=True)
    p.add_argument('--root',required=True);p.add_argument('--output',required=True);p.add_argument('--model-dir');a=p.parse_args()
    print(json.dumps(run(a.config,a.question,a.root,a.output,a.model_dir),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
