"""Loopback authentication and canonical endpoint identities; no model dependencies."""
import hmac
from urllib.parse import urlsplit, urlunsplit

MAX_BODY = 16 * 1024 * 1024


def endpoint_identity(value):
    url = urlsplit(str(value or '').strip())
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Η διεύθυνση πρέπει να είναι HTTP(S), χωρίς κωδικό, query ή fragment.')
    host = url.hostname.casefold()
    if ':' in host: host = '[' + host + ']'
    port = url.port
    if port and port != (443 if url.scheme == 'https' else 80): host += ':' + str(port)
    return urlunsplit((url.scheme.casefold(),host,url.path.rstrip('/'),'',''))


def request_allowed(headers, port, token, api=True, post=False):
    hosts = {'127.0.0.1:'+str(port),'localhost:'+str(port)}
    host = headers.get('Host','').casefold()
    origin = headers.get('Origin')
    if host not in hosts or (origin is not None and origin not in {'http://'+h for h in hosts}):
        return False
    if hasattr(headers,'get_all') and any(len(headers.get_all(k,[])) > 1 for k in ('Host','Origin','X-Akatikito-Token')):
        return False
    supplied = headers.get('X-Akatikito-Token','')
    if api and (not supplied or not hmac.compare_digest(str(supplied).encode('utf-8'),str(token).encode('utf-8'))):
        return False
    if post and headers.get('Content-Type','').split(';')[0].strip().casefold() != 'application/json':
        return False
    return True
