"""One-shot local JSON tool bridge. The host supplies project authority, not the model."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import core
import research_brief as brief

SKILL = Path(__file__).resolve().parents[2]
STRING = {'type': 'string', 'minLength': 1}
LIMIT = {'type': 'integer', 'minimum': 1, 'maximum': 10}
OFFSET = {'type': 'integer', 'minimum': 0}
CONTRACTS = {
    'research_status': ('Verify the recorded research stages and evidence bindings.',
                        {'state': STRING}, ['state']),
    'research_library': ('Page through registered literature metadata; does not read full papers.',
                         {'library': STRING, 'offset': OFFSET, 'limit': LIMIT}, ['library']),
    'research_graph': ('Read a bounded graph summary without promoting evidence states.',
                       {'graph': STRING, 'offset': OFFSET, 'limit': LIMIT, 'node': STRING,
                        'verify_sources': {'type': 'boolean'}}, ['graph']),
    'research_reference': ('Read a page of the packaged workflow rules, not arbitrary local files.',
                           {'path': STRING, 'offset': OFFSET,
                            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8000}}, ['path']),
}

def definitions():
    return [{'type': 'function', 'function': {'name': name, 'description': description,
             'parameters': {'type': 'object', 'properties': properties, 'required': required,
                            'additionalProperties': False}}}
            for name, (description, properties, required) in CONTRACTS.items()]

def validate(name, arguments):
    if not isinstance(name,str) or name not in CONTRACTS or not isinstance(arguments, dict):
        raise ValueError('Unknown tool or non-object arguments')
    _, properties, required = CONTRACTS[name]
    if set(arguments) - set(properties) or any(k not in arguments for k in required):
        raise ValueError('Unexpected or missing tool arguments')
    for key, value in arguments.items():
        rule = properties[key]
        if rule['type'] == 'string' and (not isinstance(value, str) or not value.strip()):
            raise ValueError('Use a nonempty string for ' + key)
        if rule['type'] == 'boolean' and type(value) is not bool:
            raise ValueError('Use a boolean for ' + key)
        if rule['type'] == 'integer':
            if type(value) is not int or value < rule['minimum'] or value > rule.get('maximum', 10**9):
                raise ValueError('Invalid bounded integer for ' + key)

def project_path(root, value):
    if Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('Tool file paths must be project-relative without parent traversal')
    return core.inside(root, value)

def execute(name, arguments, root):
    validate(name, arguments)
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('The host must supply an existing project root')
    a = arguments
    if name == 'research_status':
        return brief.status(project_path(root, a['state']), limit=5)
    if name == 'research_library':
        return brief.library(project_path(root, a['library']), a.get('offset', 0), a.get('limit', 5))
    if name == 'research_graph':
        return brief.graph(project_path(root, a['graph']), root, node=a.get('node'),
                           offset=a.get('offset', 0), limit=a.get('limit', 3),
                           verify_sources=a.get('verify_sources', False))
    relative = a['path']
    if not (relative == 'SKILL.md' or relative.startswith(('references/', 'scripts/tools/references/'))):
        raise ValueError('Choose a packaged Markdown reference')
    path = project_path(SKILL, relative)
    if path.suffix.lower() != '.md':
        raise ValueError('Only packaged Markdown references are exposed')
    text = path.read_text(encoding='utf-8-sig')
    offset, limit = a.get('offset', 0), a.get('limit', 6000)
    page = text[offset:offset+limit]
    return {'read_only': True, 'source': relative, 'sha256': core.sha(path), 'characters_total': len(text),
            'offset': offset, 'shown_characters': len(page), 'text': page,
            'next_offset': offset+len(page) if offset+len(page) < len(text) else None,
            'scope': 'Packaged instruction page, not evidence of full-paper reading or completed research.'}

def dispatch(request, root, max_chars=16000):
    if type(max_chars)is not int or not 1000<=max_chars<=64000:raise ValueError('Use a bounded response budget')
    if not isinstance(request, dict) or set(request) - {'id', 'tool', 'arguments'}:
        raise ValueError('Use a tool request object with id, tool and arguments only')
    identity = request.get('id')
    if not valid_id(identity):raise ValueError('Use a request id of at most 256 characters or a JSON-safe integer')
    result = execute(request.get('tool'), request.get('arguments'), root)
    response={'id': identity, 'ok': result.get('passed', True) is not False,
            'result': result, 'network_used': False, 'model_called': False}
    if len(serialize(response))>max_chars:raise ValueError('Complete response exceeds output budget; use a smaller page or exact node')
    return response

def serialize(value):return json.dumps(value,ensure_ascii=False)

def valid_id(value):
    return (value is None or isinstance(value,str) and len(value)<=256 and not any(0xd800<=ord(c)<=0xdfff for c in value)
            or type(value)is int and abs(value)<=9007199254740991)

def error_response(request,exc,max_chars=16000):
    identity=request.get('id') if isinstance(request,dict) else None
    valid=valid_id(identity)
    code=('INVALID_JSON' if isinstance(exc,json.JSONDecodeError) else 'NOT_FOUND' if isinstance(exc,FileNotFoundError)
          else 'ACCESS_DENIED' if isinstance(exc,PermissionError) else 'IO_ERROR' if isinstance(exc,OSError)
          else 'BUDGET_EXCEEDED' if 'budget' in str(exc).lower() else 'INVALID_REQUEST')
    budget=max_chars if type(max_chars)is int and 1000<=max_chars<=64000 else 16000
    message=str(exc).encode('utf-8','backslashreplace').decode('utf-8')[:500]
    response={'id':identity if valid else None,'ok':False,'error':{'code':code,'message':message,
             'retryable':False},'id_rejected':not valid,'network_used':False,'model_called':False}
    while len(serialize(response))>budget and response['error']['message']:
        response['error']['message']=response['error']['message'][:len(response['error']['message'])//2]
        response['error']['message_truncated']=True
    if len(serialize(response))>budget:
        fingerprint=hashlib.sha256(json.dumps(identity,ensure_ascii=True).encode('ascii')).hexdigest()
        response.update(id=None,id_rejected=True,request_id_sha256=fingerprint)
        response['error']={'code':'ID_EXCEEDS_RESPONSE_BUDGET','cause_code':code,'retryable':False,
                           'message':'The complete request id cannot fit the response budget; it was rejected, not shortened.'}
    return response

def handle(request,root,max_chars=16000):
    try:return dispatch(request,root,max_chars)
    except (OSError,ValueError,KeyError,TypeError) as exc:return error_response(request,exc,max_chars)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--list', action='store_true', help='Print function-tool definitions only')
    p.add_argument('--root', help='Project root chosen by the invoking host')
    p.add_argument('--input', help='One JSON request file; absent means one request from stdin')
    a = p.parse_args()
    if a.list:
        print(json.dumps(definitions(), ensure_ascii=False, indent=2)); return 0
    if not a.root:
        p.error('--root is required for tool execution')
    request=None
    try:
        if a.input:
            with Path(a.input).open('r', encoding='utf-8-sig') as f:
                text = f.read(65537)
        else:
            text = sys.stdin.read(65537)
        if len(text) > 65536:
            raise ValueError('One request may contain at most 65536 characters')
        request=json.loads(text)
        value = handle(request,a.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        value=error_response(request,exc)
    print(serialize(value));return 0 if value['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
