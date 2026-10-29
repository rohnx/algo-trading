#!/usr/bin/env python3
"""Read-only Shoonya feasibility probe; output contains only public market data.

Run from repo with its .venv Python. Auth code is exchanged at most once; no
credential, token, account, raw auth response, exception text, or orders persist.
"""
import argparse
import csv
from datetime import datetime, timedelta
import io
import json
import logging
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
import zipfile

import requests
from orb.feeds import _shoonya_api, read_shoonya_credentials
from orb.signals import IST

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True, help="Write a new sanitized JSON observation here.")
OUT = parser.parse_args().output
logging.disable(logging.CRITICAL)
requests_post = requests.post
allowed_posts = {'GenAcsTok', 'SearchScrip', 'GetQuotes', 'GetSecurityInfo', 'GetOptionChain', 'TPSeries'}
request_results = []

def bounded_post(url, *args, **kwargs):
    parsed = urlparse(url)
    route = parsed.path.rstrip('/').rsplit('/', 1)[-1]
    if parsed.hostname != 'api.shoonya.com' or route not in allowed_posts:
        raise RuntimeError('Probe endpoint not allowed')
    kwargs['timeout'] = (4, 15)
    res = requests_post(url, *args, **kwargs)
    record = {'route': route, 'status_code': res.status_code}
    try:
        body = res.json()
        record['response_kind'] = type(body).__name__
        # Errors are categorised, never printed verbatim: providers can echo IDs.
        if isinstance(body, dict):
            record['ok'] = body.get('stat') == 'Ok' or bool(body.get('access_token'))
            record['keys'] = sorted(body)
            if not record['ok']:
                message = str(body.get('emsg', body.get('message', ''))).lower()
                record['error_category'] = next((tag for tag in ('expired', 'invalid', 'unauthorized', 'denied', 'checksum', 'timeout') if tag in message), 'unspecified_provider_error')
    except Exception:
        record['response_kind'] = 'non_json'
    request_results.append(record)
    return res
requests.post = bounded_post

def public_error(exc):
    if isinstance(exc, requests.Timeout): return 'timeout'
    if isinstance(exc, requests.ConnectionError): return 'connection_error'
    if isinstance(exc, requests.HTTPError): return 'http_error'
    if isinstance(exc, (ValueError, json.JSONDecodeError)): return 'parse_error'
    return type(exc).__name__

def safe_call(name, fn):
    try:
        value = fn()
        return value, {'probe': name, 'returned': value is not None}
    except Exception as exc:
        return None, {'probe': name, 'error_category': public_error(exc)}

result = {'probe': 'Shoonya PCR and expiry data cold test',
          'started_at_ist': datetime.now(IST).isoformat(),
          'safety': {'read_only_endpoints': sorted(allowed_posts), 'auth_attempts_max': 1,
                     'sends_orders': False, 'sends_notifications': False,
                     'writes_credentials': False},
          'masters': {}, 'market_probes': []}
contracts = {}
for exchange, underlying in [('NFO', 'NIFTY'), ('BFO', 'SENSEX')]:
    url = f'https://api.shoonya.com/{exchange}_symbols.txt.zip'
    record = {'url': url}
    try:
        response = requests.get(url, timeout=(4, 25))
        record['status_code'] = response.status_code
        record['http_date'] = response.headers.get('Date')
        record['last_modified'] = response.headers.get('Last-Modified')
        record['compressed_bytes'] = len(response.content)
        response.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        record['archive_files'] = names
        text = archive.read(names[0]).decode('utf-8-sig')
        firstline = text.splitlines()[0]
        delimiter = '|' if firstline.count('|') > firstline.count(',') else ','
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
        record['columns'] = list(rows[0]) if rows else []
        record['row_count'] = len(rows)
        selected = []
        for row in rows:
            r = {str(k).strip(): str(v or '').strip() for k,v in row.items() if k}
            symbol = r.get('Symbol', r.get('symbol', ''))
            opt = r.get('OptionType', r.get('optt', ''))
            if symbol == ('BSXOPT' if underlying == 'SENSEX' else underlying) and opt in ('CE', 'PE'):
                selected.append(r)
        contracts[underlying] = selected
        record['option_contract_count'] = len(selected)
        expiries = sorted({r.get('Expiry', '') for r in selected}, key=lambda s: datetime.strptime(s, '%d-%b-%Y'))
        record['expiries'] = []
        for expiry in expiries:
            matching = [r for r in selected if r.get('Expiry') == expiry]
            strikes = sorted({float(r['StrikePrice']) for r in matching})
            record['expiries'].append({'expiry': expiry, 'weekday': datetime.strptime(expiry, '%d-%b-%Y').strftime('%A'), 'contract_count': len(matching), 'calls': sum(r['OptionType']=='CE' for r in matching), 'puts': sum(r['OptionType']=='PE' for r in matching), 'strike_count': len(strikes), 'min_strike': min(strikes), 'max_strike': max(strikes)})
        record['sample_contracts'] = selected[:3]
    except Exception as exc:
        record['error_category'] = public_error(exc)
    result['masters'][exchange] = record
    OUT.write_text(json.dumps(result, indent=2))

credentials = read_shoonya_credentials()
result['credential_presence'] = {key: bool(value) for key,value in credentials.items()}
api = None
if all(result['credential_presence'].values()):
    api = _shoonya_api()
    auth, summary = safe_call('oauth_authenticate_once', lambda: api.getAccessToken(authcode=credentials['SHOONYA_AUTH_CODE'], Secret_Code=credentials['SHOONYA_SECRET_CODE'], client_id=credentials['SHOONYA_CLIENT_ID'], UID=credentials['SHOONYA_USER_ID']))
    result['authentication'] = {**summary, 'attempted': True, 'success': bool(auth and len(auth)==4 and auth[0])}
else:
    result['authentication'] = {'attempted': False, 'success': False, 'error_category': 'missing_credentials'}
# References are cleared; tokens remain only in API memory if successful.
credentials.clear()
if 'auth' in globals(): del auth

if result['authentication']['success']:
    spot, rec = safe_call('NIFTY_spot_positive_control', lambda: api.get_quotes('NSE', '26000'))
    if isinstance(spot, dict): rec['public_fields'] = {k: spot[k] for k in ['stat','exch','token','tsym','lp','ft','ltt','oi','v'] if k in spot}
    result['market_probes'].append(rec)
    try: atm = round(float(spot['lp'])/50)*50
    except Exception: atm = 25000
    pairs = []
    for underlying, exchange, center in [('NIFTY','NFO',atm)]:
        available = contracts.get(underlying, [])
        future = [r for r in available if datetime.strptime(r['Expiry'],'%d-%b-%Y').date() >= datetime.now(IST).date()]
        if not future: continue
        expiry = min({r['Expiry'] for r in future}, key=lambda s: datetime.strptime(s,'%d-%b-%Y'))
        expcontracts = [r for r in future if r['Expiry']==expiry]
        call = min((r for r in expcontracts if r['OptionType']=='CE'), key=lambda r: abs(float(r['StrikePrice'])-center))
        put = next((r for r in expcontracts if r['OptionType']=='PE' and r['StrikePrice']==call['StrikePrice']), None)
        selected = [r for r in [call, put] if r]
        chain, rec = safe_call(f'{underlying}_option_chain_count_2', lambda: api.get_option_chain(exchange, call['TradingSymbol'], float(call['StrikePrice']), count=2))
        if isinstance(chain,dict):
            values = chain.get('values',[])
            rec.update({'expiry':expiry, 'row_count':len(values), 'keys':sorted({k for r in values for k in r}) if values else [], 'rows': [{k:r[k] for k in ['exch','token','tsym','optt','strprc','lp','oi','v','ft'] if k in r} for r in values], 'universe':'partial; count=2 near ATM, not complete expiry'})
        result['market_probes'].append(rec)
        for row in selected:
            token = row['Token']
            pairs.append(f'{exchange}|{token}')
            quote, rec = safe_call(f"{underlying}_{row['OptionType']}_quote",lambda: api.get_quotes(exchange,token))
            if isinstance(quote,dict): rec['public_fields'] = {k:quote[k] for k in ['stat','exch','token','tsym','lp','oi','v','ft','ltt','poi','toi','exd','optt','strprc'] if k in quote}
            result['market_probes'].append(rec)
            security, rec = safe_call(f"{underlying}_{row['OptionType']}_security", lambda: api.get_security_info(exchange, token))
            if isinstance(security,dict): rec['public_fields'] = {k:security[k] for k in ['stat','exch','token','tsym','exd','expd','optt','strprc','ls','instname','dname'] if k in security}
            result['market_probes'].append(rec)
            end = datetime.now(IST)
            start = end.replace(hour=9,minute=15,second=0,microsecond=0)
            history, rec = safe_call(f"{underlying}_{row['OptionType']}_5m_history_today", lambda: api.get_time_price_series(exchange,token,int(start.timestamp()),int(end.timestamp()),'5'))
            if isinstance(history,list):
                rec['row_count'] = len(history)
                rec['keys'] = sorted({k for h in history for k in h})
                fields=['stat','time','ssboe','into','inth','intl','intc','intv','v','intoi','inoi','oi']
                rec['sample_rows'] = [{k:h[k] for k in fields if k in h} for h in history[:3]]
            result['market_probes'].append(rec)
            time.sleep(.3)
        OUT.write_text(json.dumps(result, indent=2))
    if pairs:
        packets=[]
        opened=threading.Event()
        errors=[]
        def on_open():
            opened.set()
            try: api.subscribe(pairs)
            except Exception as exc: errors.append(public_error(exc))
        def on_tick(packet):
            if len(packets)<40:
                packets.append({k:packet[k] for k in ['t','e','tk','ts','lp','v','oi','poi','toi','ft'] if k in packet})
        def on_error(error): errors.append('websocket_error')
        started=time.monotonic()
        try:
            api.start_websocket(subscribe_callback=on_tick, socket_open_callback=on_open, socket_error_callback=on_error)
            while time.monotonic()-started<12 and len(packets)<10:
                time.sleep(.25)
        except Exception as exc: errors.append(public_error(exc))
        finally:
            try: api.close_websocket()
            except Exception as exc: errors.append(public_error(exc))
        result['websocket']={'opened':opened.is_set(),'duration_seconds':round(time.monotonic()-started,2),'subscription_count':len(pairs),'packet_count':len(packets),'public_packets':packets,'errors':errors}
else:
    result['market_probes_blocked'] = 'Authentication did not succeed; availability of authenticated OI/history/streaming remains unverified.'
result['requests'] = request_results
result['finished_at_ist'] = datetime.now(IST).isoformat()
OUT.write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
