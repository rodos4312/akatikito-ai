"""Validate schema header annotations and mirror values without HTTP injection."""
import base64
import copy
import re

TOKEN=re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
DATA_KEYS={'default','const','enum','examples','description','title'}


def definition(schema):
    clean=copy.deepcopy(schema);annotations=[];names=set();visited=0
    def visit(node,path=(),reachable=True,depth=0):
        nonlocal visited
        visited+=1
        if depth>64 or visited>5000:raise ValueError('Πολύ σύνθετο schema HTTP MCP.')
        if isinstance(node,list):
            for item in node:visit(item,path,False,depth+1)
            return
        if not isinstance(node,dict):return
        if 'x-mcp-header' in node:
            header=node.pop('x-mcp-header');kind=node.get('type')
            types=set(kind) if isinstance(kind,list) else {kind}
            if (not reachable or not path or not isinstance(header,str) or not 1<=len(header)<=80
                    or not TOKEN.fullmatch(header) or header.casefold() in names
                    or len(types-{'null'})!=1 or not types-{'null'}<={'string','integer','boolean'}):
                raise ValueError('Μη έγκυρη δήλωση x-mcp-header.')
            names.add(header.casefold());annotations.append((path,header,(types-{'null'}).pop()))
            if len(annotations)>64:raise ValueError('Υπερβολικά πολλά headers εργαλείου MCP.')
        for key,value in node.items():
            if key in DATA_KEYS:continue
            if key=='properties' and isinstance(value,dict):
                for name,child in value.items():visit(child,path+(name,),reachable,depth+1)
            elif isinstance(value,(dict,list)):visit(value,path,False,depth+1)
    visit(clean)
    return clean,annotations


def encoded(value):
    if type(value) is bool:text='true' if value else 'false'
    elif type(value) is int:
        if not -(2**53-1)<=value<=2**53-1:raise ValueError('Ακέραιος MCP header εκτός ασφαλούς εύρους.')
        text=str(value)
    elif isinstance(value,str):text=value
    else:raise ValueError('Τα MCP headers δέχονται string, integer ή boolean.')
    if (text!=text.strip() or any(ord(c)<32 or ord(c)>126 for c in text)
            or text.startswith('=?base64?') and text.endswith('?=')):
        text='=?base64?'+base64.b64encode(text.encode('utf-8')).decode('ascii')+'?='
    if len(text)>8192:raise ValueError('Πολύ μεγάλη τιμή header MCP.')
    return text


def values(annotations,args):
    result={}
    for path,header,kind in annotations:
        value=args
        for key in path:
            if not isinstance(value,dict) or key not in value:value=None;break
            value=value[key]
        if value is None:continue
        if kind=='integer' and type(value) is float and value.is_integer():value=int(value)
        if (kind=='integer' and type(value) is not int or kind=='boolean' and type(value) is not bool
                or kind=='string' and not isinstance(value,str)):
            raise ValueError('Η τιμή MCP header δεν ταιριάζει στο schema.')
        result['Mcp-Param-'+header]=encoded(value)
    if sum(len(k)+len(v) for k,v in result.items())>32000:raise ValueError('Πολύ μεγάλα συνολικά MCP headers.')
    return result
