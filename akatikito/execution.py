"""Read-only tool scheduling. All policy and bookkeeping stay on the caller thread."""
from __future__ import annotations
import json
from .network_tools import READ_TOOLS

PARALLEL_READS = frozenset(('list_files', 'read_file', 'search_in_file', 'search_project', 'search_documents', 'file_operation_history', *READ_TOOLS))


def mutation_paths(name, args, result):
    """One mutation protocol for tools, repair, task state, UI and context."""
    if name not in ('edit_file','write_file','create_word_document','create_excel_sheet','create_pdf_document','convert_data_file','apply_file_operations','undo_file_operations'):
        return ()
    reported=getattr(result,'changed_paths',None)
    if reported is not None:
        return tuple(dict.fromkeys(p for p in reported if isinstance(p,str) and p))
    if not str(result).startswith('ΕΓΙΝΕ'):
        return ()
    if name == 'convert_data_file':
        paths=[args.get('output_path')]
    elif isinstance(args.get('files'),list):
        paths=[item.get('path') for item in args['files'] if isinstance(item,dict)]
    else:
        paths=[args.get('path')]
    return tuple(dict.fromkeys(p for p in paths if isinstance(p,str) and p))


def arguments(call):
    raw = call.get('arguments')
    args = json.loads(raw or '{}') if isinstance(raw, str) else raw or {}
    if not isinstance(args, dict):
        raise ValueError('Τα ορίσματα εργαλείου πρέπει να είναι JSON object.')
    return args


def read_resource(ws, call):
    name = call.get('name')
    if name not in PARALLEL_READS:
        return None
    try:
        args = arguments(call)
        if name in ('list_files', 'read_file', 'search_in_file'):
            # Aliases and overlapping reads of the same file stay sequential.
            return ('local', str(ws.resolve(args.get('path') or '.')).casefold())
        if name == 'github_read':
            return json.dumps((name, args.get('owner'), args.get('repo'), args.get('path', ''), args.get('ref', '')), sort_keys=True)
        if name == 'web_fetch':
            return json.dumps((name, args.get('url')))
        return (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
    except (ValueError, TypeError, PermissionError):
        return None
