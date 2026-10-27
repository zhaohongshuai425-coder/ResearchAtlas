"""Pinned local Chinese-to-English search-query translation; never answer generation."""
from pathlib import Path
import re
import core


class Translator:
    def __init__(self,folder,threads=2):
        import torch
        from transformers import AutoTokenizer,AutoModelForSeq2SeqLM
        self.folder=Path(folder).resolve();lock=core.load(self.folder/'model_lock.json')
        if lock.get('repository')!='Helsinki-NLP/opus-mt-zh-en' or lock.get('trust_remote_code') is not False:
            raise ValueError('Use the documented pinned query translation model')
        for b in lock['files']:
            if core.sha(core.inside(self.folder,b['path']))!=b['sha256']:raise ValueError('Translation model changed')
        torch.set_num_threads(threads);self.torch=torch
        # SentencePiece's Windows file loader can reject Unicode paths. Read the
        # same verified bytes through Python instead; no changed tokenization.
        from unittest.mock import patch
        import sentencepiece
        def load_bytes(path,sp_model_kwargs):
            return sentencepiece.SentencePieceProcessor(model_proto=Path(path).read_bytes(),**(sp_model_kwargs or {}))
        with patch('transformers.models.marian.tokenization_marian.load_spm',load_bytes):
            self.tokenizer=AutoTokenizer.from_pretrained(self.folder,local_files_only=True,trust_remote_code=False)
        self.model=AutoModelForSeq2SeqLM.from_pretrained(self.folder,local_files_only=True,trust_remote_code=False,
            use_safetensors=False,weights_only=True).eval()
        self.provenance={'repository':lock['repository'],'revision':lock['revision'],
                         'lock_sha256':core.sha(self.folder/'model_lock.json'),'beam_size':4,'max_new_tokens':160,
                         'sampling':False,'scope':'Machine query translation, may mistranslate terms/negation; no source support implied.'}

    def translate(self,question):
        if not re.search(r'[\u3400-\u9fff]',question):return {'input':question,'translated':None,'executed':False}
        inputs=self.tokenizer(question,return_tensors='pt',truncation=False)
        if inputs['input_ids'].shape[1]>480:raise ValueError('Split long question before query translation; no silent truncation')
        with self.torch.inference_mode():
            tokens=self.model.generate(**inputs,num_beams=4,max_new_tokens=160,do_sample=False)
        if tokens.shape[1]>=160:raise ValueError('Translation may be truncated; split the question explicitly')
        return {'input':question,'translated':self.tokenizer.decode(tokens[0],skip_special_tokens=True),
                'executed':True,'model':self.provenance}
