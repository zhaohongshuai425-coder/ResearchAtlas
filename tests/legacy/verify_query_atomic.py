from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"research-evidence-workflow/scripts/tools"
RUN=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
from pathlib import Path

import argparse

import json

import sys

import unittest

from unittest.mock import patch

from verify_hardening import HardeningTests

import core

import corpus_tools as ct

class QueryAtomicTests(HardeningTests):
    def chinese_manifest(self):
        data=core.load(self.catalog)
        data['documents'][0]['title']='不平衡图模型'
        data['documents'][0]['search_metadata']['title']='不平衡图模型'
        catalog=self.derived/'catalog_zh.json';core.save(catalog,data)
        m=core.load(self.manifest);m['catalog']={'path':'derived/catalog_zh.json','sha256':core.sha(catalog)}
        file=self.derived/'manifest_zh.json';core.save(file,m)
        result=ct.build_search(file,self.derived/'zh.sqlite')
        m['search_index']={'path':'derived/zh.sqlite','sha256':result['sha256']};core.save(file,m)
        return file

    def test_chinese_question_finds_existing_topic(self):
        file=self.chinese_manifest()
        literal=ct.search(file,'不平衡')
        self.assertTrue(literal['hits'])
        result=ct.search(file,'有没有适合极端不平衡的图模型？')
        self.assertTrue(result['hits'])
        self.assertEqual(result['hits'][0]['document_id'],'P1')
        self.assertFalse(result['answer_generated'])
        self.assertIn('不平衡',result['retrieval_terms'])
        self.assertEqual(result['query_expansion'],'cjk_fallback_after_no_literal_matches')

    def test_explicit_terms_preserve_literal_retrieval(self):
        file=self.chinese_manifest()
        result=ct.search(file,'有没有适合极端不平衡的图模型？',terms=['graph'])
        self.assertTrue(result['hits'])
        self.assertEqual(result['query_expansion'],'none')
        self.assertIn('graph',result['declared_terms'])

    def test_fallback_respects_document_filter(self):
        file=self.chinese_manifest()
        result=ct.search(file,'有没有适合极端不平衡的图模型？',alias='NEW1')
        self.assertFalse(result['hits'])

    def test_english_unknown_does_not_trigger_chinese_expansion(self):
        result=ct.search(self.manifest,'unfindabletokenxyz')
        self.assertFalse(result['hits'])
        self.assertEqual(result['query_expansion'],'none')

    def test_failed_build_leaves_no_index_and_can_retry(self):
        data=core.load(self.catalog)
        data['documents'][0]['review_sources']=[{'role':'inherited_deep_reading_card','path':'missing.md','sha256':'not_available'}]
        file=self.derived/'fault_manifest.json';catalog=self.derived/'fault_catalog.json';core.save(catalog,data)
        m=core.load(self.manifest);m['catalog']={'path':'derived/fault_catalog.json','sha256':core.sha(catalog)};core.save(file,m)
        out=self.derived/'retry.sqlite'
        with self.assertRaises(ValueError):ct.build_search(file,out)
        self.assertFalse(out.exists(),'A failed build must not publish a partial SQLite file')
        self.assertEqual(list(self.derived.glob('.corpus-build-*')),[])
        note=self.root/'missing.md';note.write_text('Restored reading note.',encoding='utf-8')
        data['documents'][0]['review_sources'][0]['sha256']=core.sha(note);core.save(catalog,data)
        m['catalog']['sha256']=core.sha(catalog);core.save(file,m)
        result=ct.build_search(file,out)
        self.assertTrue(result['passed']);self.assertTrue(out.is_file())

    def test_competing_publication_keeps_other_file(self):
        out=self.derived/'competing.sqlite'
        def competing_writer(source,target):
            Path(target).write_bytes(b'created by another writer')
            raise FileExistsError('Concurrent writer won')
        with patch.object(ct.os,'link',side_effect=competing_writer):
            with self.assertRaises(FileExistsError):ct.build_search(self.manifest,out)
        self.assertEqual(out.read_bytes(),b'created by another writer')
        self.assertEqual(list(self.derived.glob('.corpus-build-*')),[])

    def test_publication_error_leaves_no_partial_target(self):
        out=self.derived/'publish_error.sqlite'
        with patch.object(ct.os,'link',side_effect=OSError('publication not available')):
            with self.assertRaises(OSError):ct.build_search(self.manifest,out)
        self.assertFalse(out.exists())
        self.assertEqual(list(self.derived.glob('.corpus-build-*')),[])
        self.assertTrue(ct.build_search(self.manifest,out)['passed'])
