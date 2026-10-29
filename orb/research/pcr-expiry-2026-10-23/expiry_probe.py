"""Public-only contract metadata probe. No credentials or authentication."""
import argparse
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import requests
from zoneinfo import ZoneInfo
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True, help="Write public contract metadata summary here.")
OUT = parser.parse_args().output

result = {'observed_at_ist':datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),'requires_authentication':False,'masters':{}}
for exch, underlying, master_symbol in [('NFO','NIFTY','NIFTY'), ('BFO','SENSEX','BSXOPT')]:
    url=f'https://api.shoonya.com/{exch}_symbols.txt.zip'
    rec={'url':url,'underlying':underlying,'master_symbol':master_symbol}
    try:
        response=requests.get(url,timeout=(4,20))
        rec['status_code']=response.status_code
        rec['last_modified']=response.headers.get('Last-Modified')
        response.raise_for_status()
        archive=zipfile.ZipFile(io.BytesIO(response.content))
        rawrows=list(csv.DictReader(io.StringIO(archive.read(archive.namelist()[0]).decode('utf-8-sig'))))
        rows=[{k.strip():v.strip() for k,v in row.items() if k} for row in rawrows]
        options=[r for r in rows if r['Symbol']==master_symbol and r['Instrument']=='OPTIDX' and r['OptionType'] in ('CE','PE')]
        rec['total_master_rows']=len(rows)
        rec['underlying_option_rows']=len(options)
        rec['expiries']=[]
        for expiry in sorted({r['Expiry'] for r in options},key=lambda x:datetime.strptime(x,'%d-%b-%Y')):
            matching=[r for r in options if r['Expiry']==expiry]
            rec['expiries'].append({'expiry':expiry,'weekday':datetime.strptime(expiry,'%d-%b-%Y').strftime('%A'),'call_contracts':sum(r['OptionType']=='CE' for r in matching),'put_contracts':sum(r['OptionType']=='PE' for r in matching),'strike_count':len({r['StrikePrice'] for r in matching})})
        rec['first_three_chronological_contracts']=sorted(options,key=lambda r:(datetime.strptime(r['Expiry'],'%d-%b-%Y'),float(r['StrikePrice']),r['OptionType']))[:3]
        rec['futures_expiries']=sorted({r['Expiry'] for r in rows if r['Instrument']=='FUTIDX' and (r['Symbol']==master_symbol or r['TradingSymbol'].startswith(underlying))},key=lambda x:datetime.strptime(x,'%d-%b-%Y'))
        rec['expiry_type_field_present']=any(k.lower() in ('expirytype','expirymonth','weekly','monthly','contracttype') for k in options[0]) if options else False
    except Exception as exc:
        rec['error_type']=type(exc).__name__
    result['masters'][exch]=rec
OUT.write_text(json.dumps(result,indent=2))
for rec in result['masters'].values():
    rec.pop('contracts',None)
print(json.dumps(result,indent=2))
