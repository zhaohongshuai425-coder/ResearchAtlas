"""Read-only PDF quality triage. Flags are review leads, not repaired text."""
from pathlib import Path
import argparse
import json
import re
import shutil
import unicodedata
import core

SEVERE={'replacement_characters','private_use_mapping','unexpected_script_mapping','image_without_text','empty_text_needs_review','sparse_text_needs_review'}

def text_quality(text, expected_script='unknown'):
    chars=[c for c in text if not c.isspace()]
    n=max(len(chars),1)
    ratios={'replacement':sum(c=='\ufffd' for c in chars)/n,
            'private_use':sum(unicodedata.category(c)=='Co' for c in chars)/n,
            'cjk':sum('\u3400'<=c<='\u9fff' for c in chars)/n,
            'indic_thai_tibetan_myanmar':sum('\u0900'<=c<='\u109f' for c in chars)/n}
    flags=[]
    if ratios['replacement']>=0.005: flags.append('replacement_characters')
    if ratios['private_use']>=0.01: flags.append('private_use_mapping')
    if expected_script=='cjk' and ratios['indic_thai_tibetan_myanmar']>=0.08 and ratios['cjk']<0.2:
        flags.append('unexpected_script_mapping')
    if len(chars)<40: flags.append('sparse_text_needs_review')
    if not chars: flags.append('empty_text_needs_review')
    return {'flags':flags,'characters':len(chars),'script_ratios':ratios,'expected_script':expected_script,
            'mathematical_integrity':'not_checked_requires_original','layout_integrity':'not_checked_requires_original',
            'gate':'hold_text_for_review' if SEVERE.intersection(flags) else 'candidate_text_only'}

def inspect_pdf(path, pages=None, expected_script='unknown', tables=False):
    import pdfplumber
    path=Path(path).resolve(); before=core.sha(path)
    result={'schema':'research-pdf-quality/1.0','source_sha256':before,'source_name':path.name,
            'page_definition':'one-based physical PDF page','pages':[],
            'ocr':{'executable_available':bool(shutil.which('tesseract')),'executed':False},
            'scope':'Read-only selected-page inspection. OCR, formula meaning and table correctness are not certified.'}
    with pdfplumber.open(path,laparams={'all_texts':True}) as pdf:
        result['total_pages']=len(pdf.pages)
        selected=list(range(1,len(pdf.pages)+1)) if pages is None else sorted(set(pages))
        if any(isinstance(p,bool) or not isinstance(p,int) or p<1 or p>len(pdf.pages) for p in selected):
            raise ValueError('Select physical pages within the original PDF')
        result['selected_pages']=selected
        for number in selected:
            page=pdf.pages[number-1]
            boxes=page.objects.get('textboxhorizontal',[])
            blocks=[{'text':b.get('text',''),'bbox':[float(b[k]) for k in ['x0','top','x1','bottom']]}
                    for b in sorted(boxes,key=lambda b:(round(b['top'],1),b['x0'])) if b.get('text','').strip()]
            object_chars=sum(len(str(c.get('text','')).strip()) for c in page.chars)
            extracted=sum(len(re.sub(r'\s+','',b['text'])) for b in blocks)
            fallback=not blocks or (object_chars>0 and extracted<.85*object_chars)
            if fallback:
                text=page.extract_text() or ''
                blocks=[{'text':text,'bbox':None}] if text.strip() else []
            text='\n'.join(b['text'] for b in blocks)
            quality=text_quality(text,expected_script)
            if page.images and not quality['characters']:
                quality['flags'].append('image_without_text');quality['gate']='hold_text_for_review'
            left=any(b['bbox'] and b['bbox'][2]<page.width*.58 for b in blocks)
            right=any(b['bbox'] and b['bbox'][0]>page.width*.42 for b in blocks)
            layout=[]
            if left and right:layout.append('possible_columns_check_reading_order')
            if re.search(r'\b(?:table|figure|equation|theorem)\b|[图表式定理]\s*\d|[=∑∫∂≤≥]',text,re.I):
                layout.append('formula_figure_or_table_mentions_check_original')
            candidates=[]
            if tables:
                for table in page.find_tables()[:3]:
                    candidates.append({'bbox':list(table.bbox),'cells':table.extract(),
                                       'status':'unverified_table_candidate_not_scientific_data'})
            result['pages'].append({'page':number,'width':float(page.width),'height':float(page.height),
                'quality':quality,'layout_review':layout,'image_objects':len(page.images),
                'object_characters':object_chars,'coverage_fallback_used':fallback,'text':text,
                'blocks':blocks,'table_candidates':candidates})
            page.close()
    if core.sha(path)!=before: raise ValueError('Original changed during inspection')
    return result

def extract_fast(path,expected_script='unknown'):
    """Native text extraction for bounded-memory corpus rebuilding; no text boxes."""
    import pypdfium2
    from importlib import metadata
    path=Path(path).resolve();before=core.sha(path);pages=[]
    pdf=pypdfium2.PdfDocument(path)
    try:
        for number in range(len(pdf)):
            page=pdf[number]
            try:
                tp=page.get_textpage()
                try:text=tp.get_text_range()
                finally:tp.close()
                pages.append({'page':number+1,'text':text,'quality':text_quality(text,expected_script),
                    'layout_review':['native_text_no_block_geometry_check_reading_order_formulas_and_tables'],
                    'coverage_fallback_used':False})
            finally:page.close()
    finally:pdf.close()
    if core.sha(path)!=before:raise ValueError('Original changed during native extraction')
    return {'total_pages':len(pages),'pages':pages,'engine':{'backend':'pypdfium2','version':metadata.version('pypdfium2'),
            'geometry':'not_extracted','scope':'Native machine text only, not verified layout or reading'}}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pdf',required=True);p.add_argument('--pages',type=int,nargs='+')
    p.add_argument('--expected-script',choices=['unknown','cjk'],default='unknown');p.add_argument('--tables',action='store_true');p.add_argument('--output',required=True)
    a=p.parse_args();out=Path(a.output)
    if out.exists():raise FileExistsError('Preserve existing inspection version')
    report=inspect_pdf(a.pdf,a.pages,a.expected_script,a.tables);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(out.resolve()),'source_sha256':report['source_sha256'],'selected_pages':report['selected_pages'],'scope':report['scope']},ensure_ascii=False))

if __name__=='__main__':main()
