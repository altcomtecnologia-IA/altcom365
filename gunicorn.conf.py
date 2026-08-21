import os, base64, json, logging


def pre_request(worker, req):
    log = logging.getLogger('gunicorn.error')
    try:
        hdrs = {(k.decode() if isinstance(k, bytes) else k).lower():
                (v.decode() if isinstance(v, bytes) else v)
                for k, v in req.headers}
        tok = hdrs.get('cf-access-jwt-assertion', '')
        if not tok:
            for part in hdrs.get('cookie', '').split(';'):
                p = part.strip()
                if p.lower().startswith('cf_authorization='):
                    tok = p.split('=', 1)[1]; break
        if tok and tok.count('.') >= 2:
            pl = json.loads(base64.urlsafe_b64decode(tok.split('.')[1] + '=='))
            log.warning('[DBG] aud=%s cfg=%s',
                str(pl.get('aud', '?'))[:80],
                os.environ.get('CF_ACCESS_AUD', '?')[:80])
    except Exception as e:
        log.warning('[DBG_ERR] %s', str(e)[:60])
