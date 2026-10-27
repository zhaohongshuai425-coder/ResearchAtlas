"""Portable historic synthetic regressions, deduplicated by original method identity."""
from pathlib import Path
import importlib,sys,unittest
LEGACY=Path(__file__).resolve().parent/'legacy'
sys.path.insert(0,str(LEGACY))
CASES=[('verify_hardening', 'HardeningTests'), ('verify_query_atomic', 'QueryAtomicTests'), ('verify_literature_tools', 'LiteratureTests'), ('verify_research_flow', 'WorkflowTests'), ('verify_generalization', 'GeneralizationTests'), ('verify_repairs', 'RepairTests')]
def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite();seen=set()
    for module,classname in CASES:
        cls=getattr(importlib.import_module(module),classname)
        for name in loader.getTestCaseNames(cls):
            fn=getattr(cls,name);key=(fn.__code__.co_filename,fn.__code__.co_firstlineno)
            if key not in seen:seen.add(key);suite.addTest(cls(name))
    return suite
