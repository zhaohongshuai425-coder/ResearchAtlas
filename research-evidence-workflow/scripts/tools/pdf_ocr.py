"""Optional local Windows OCR. Produces a sidecar; never replaces original text."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import tempfile
import core
import pdf_quality

def recognize(path,pages,output,language='zh-Hans-CN'):
    path=Path(path).resolve();output=Path(output).resolve()
    if output.exists():raise FileExistsError('Preserve existing OCR version')
    if not pages or any(isinstance(p,bool) or not isinstance(p,int) or p<1 for p in pages):raise ValueError('Explicit physical pages required')
    powershell=shutil.which('powershell.exe')
    if not powershell:raise ValueError('This optional backend requires Windows PowerShell and a local OCR language pack')
    import pdfplumber
    original=core.sha(path);script=Path(__file__).with_name('windows_ocr.ps1')
    result={'schema':'research-ocr-sidecar/1.0','source_sha256':original,'source_name':path.name,
            'backend_script_sha256':core.sha(script),'language_requested':language,'pages':[],
            'scope':'Local OCR candidates only. Not formula/table reconstruction, whole-paper reading or source truth. Original bytes unchanged.',
            'backend_reference':'https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.recognizeasync'}
    output.parent.mkdir(parents=True,exist_ok=True)
    with pdfplumber.open(path) as pdf:
        if any(p>len(pdf.pages) for p in pages):raise ValueError('Physical page outside PDF')
        with tempfile.TemporaryDirectory(prefix='ocr-render-',dir=output.parent) as tmp:
            for number in sorted(set(pages)):
                page=pdf.pages[number-1];image=page.to_image(resolution=180).original
                # The native engine has a dimension limit. Record any resampling.
                original_size=list(image.size)
                if max(image.size)>2400:image.thumbnail((2400,2400))
                png=Path(tmp)/f'p{number}.png';raw=Path(tmp)/f'p{number}.json';image.save(png)
                try:
                    process=subprocess.run([powershell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script),'-ImagePath',str(png),
                                            '-OutputPath',str(raw),'-Language',language],capture_output=True,timeout=45)
                except subprocess.TimeoutExpired:
                    result['pages'].append({'page':number,'status':'ocr_failed','error':'Local OCR exceeded the 45 second per-page bound'})
                    page.close();continue
                if process.returncode or not raw.is_file():
                    result['pages'].append({'page':number,'status':'ocr_failed','error':process.stderr.decode('utf-8',errors='replace')[:1500]})
                else:
                    value=core.load(raw);value.update(page=number,rendered_original_size=original_size,
                         render_size=list(image.size),render_sha256=core.sha(png),dpi=180,
                         quality=pdf_quality.text_quality(value['text'],'cjk' if language.startswith('zh') else 'unknown'))
                    import re
                    value['search_text']=re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])','',value['text'])
                    result['pages'].append(value)
                page.close()
    if core.sha(path)!=original:raise ValueError('Original changed during OCR')
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pdf',required=True);p.add_argument('--pages',type=int,nargs='+',required=True)
    p.add_argument('--output',required=True);p.add_argument('--language',default='zh-Hans-CN');a=p.parse_args()
    r=recognize(a.pdf,a.pages,a.output,a.language)
    print(json.dumps({'pages':[{'page':p['page'],'status':p['status']} for p in r['pages']],'output':a.output,'scope':r['scope']},ensure_ascii=False))
