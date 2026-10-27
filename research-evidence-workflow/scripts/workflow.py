"""Run a packaged research helper; no development-machine paths required."""
from pathlib import Path
import argparse
import json
import runpy
import sys

TOOLS = Path(__file__).resolve().parent / 'tools'
COMMANDS = {
    'local-ingest': ('local_ingest.py', 'Resumable offline PDF, folder and ZIP intake into an existing registry'),
    'harness-tools': ('harness_tools.py', 'One-shot JSON tool bridge for an external harness; no model calls'),
    'portable-context': ('portable_context.py', 'Offline DeepSeek chat, agent or API context export'),
    'doctor': ('runtime_check.py', 'Check runtime and optional dependencies without installing them'),
    'brief': ('research_brief.py', 'Bounded summaries of state, library or graph'),
    'research-flow': ('research_flow.py', 'Project state, evidence bindings and handoff'),
    'literature-tools': ('literature_tools.py', 'Literature capture, indexing and source anchors'),
    'core': ('core.py', 'Knowledge graph validation, lookup and comparison'),
    'corpus-tools': ('corpus_tools.py', 'Full-corpus text index and lexical retrieval'),
    'evidence-search': ('evidence_search.py', 'Retrieve evidence candidates and resolve anchors'),
    'evidence-followup': ('evidence_followup.py', 'Bounded follow-up from an existing question'),
    'evidence-decisions': ('evidence_decisions.py', 'Evidence review records and scoped graph relations'),
    'evidence-quality': ('evidence_quality.py', 'Retrieval regression and coverage checks'),
    'research-query': ('research_query.py', 'Query all configured formal source batches'),
    'source-providers': ('source_providers.py', 'Explicit online provider lookup'),
    'semantic-retrieval': ('semantic_retrieval.py', 'Optional local pinned-model retrieval'),
    'pdf-quality': ('pdf_quality.py', 'Inspect PDF extraction quality'),
    'pdf-ocr': ('pdf_ocr.py', 'Optional local OCR'),
    'review-workbench': ('review_workbench.py', 'Create a source-linked review workbench'),
    'receiver-check': ('receiver_check.py', 'Check a received project'),
    'prototype-acceptance': ('prototype_acceptance.py', 'Run explicit local acceptance checks'),
    'adapter-v21': ('adapter_v21.py', 'Adapter for an existing v2.1 graph registry'),
    'adapter-v21-corpus': ('adapter_v21_corpus.py', 'Adapter for an existing v2.1 literature corpus'),
    'run-task': ('run_codex_task.py', 'One explicitly requested native Codex task; uses account quota'),
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__, epilog='Use COMMAND --help for its arguments.')
    parser.add_argument('command', choices=COMMANDS, nargs='?')
    if not argv or argv[0] in ('-h', '--help'):
        parser.print_help()
        for name, (_, description) in COMMANDS.items():
            print(f'  {name:23} {description}')
        return 0
    # The remaining flags belong to the selected helper, including --help.
    parser.parse_args(argv[:1])
    name = argv[0]
    target = TOOLS / COMMANDS[name][0]
    old_argv, old_path = sys.argv, sys.path[:]
    try:
        if sys.version_info < (3, 10):
            raise ValueError('Use Python 3.10 or newer')
        if not target.is_file():
            raise FileNotFoundError('Packaged helper is missing: ' + target.name)
        sys.argv = [str(target), *argv[1:]]
        sys.path.insert(0, str(TOOLS))
        runpy.run_path(str(target), run_name='__main__')
        return 0
    except ModuleNotFoundError as exc:
        print(json.dumps({'passed': False, 'command': name, 'error': str(exc),
            'hint': 'Run workflow.py doctor; see references/runtime.md. No automatic installation was attempted.'}, ensure_ascii=False))
        return 1
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'passed': False, 'command': name, 'error': str(exc)}, ensure_ascii=False))
        return 1
    finally:
        sys.argv, sys.path[:] = old_argv, old_path


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
