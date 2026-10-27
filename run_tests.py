"""Run the self-contained offline suite from any working directory."""
from pathlib import Path
import argparse,json,sys,unittest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report');a=p.parse_args()
    tests=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent/'tests'))
    result=unittest.TextTestRunner(verbosity=2).run(tests)
    report={'passed':result.wasSuccessful(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'scope':'Portable synthetic fixtures and offline tool invariants; no research data, model calls or native model-loop guarantee.'}
    if a.report:Path(a.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
