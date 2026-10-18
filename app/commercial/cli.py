"""Run with: python -m app.commercial.cli --help. Never prints configured secrets."""
import argparse
import json
import os
from pathlib import Path
from app.commercial.config import from_env


def main():
    parser=argparse.ArgumentParser(description='Operazioni Iron Coach hosted')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('keygen',help='Genera una nuova chiave privata da conservare nel gestore dei segreti')
    commands.add_parser('check',help='Verifica la configurazione senza mostrare segreti')
    invite=commands.add_parser('invite');invite.add_argument('email');invite.add_argument('--days',type=int,default=7)
    snapshot=commands.add_parser('backup');snapshot.add_argument('--output',required=True)
    recovery=commands.add_parser('restore');recovery.add_argument('--input',required=True);recovery.add_argument('--target',required=True)
    purge=commands.add_parser('purge-deletions',help='A servizio arrestato: rimuove i dati delle richieste scadute');purge.add_argument('--older-than-days',type=int,default=30)
    args=parser.parse_args()
    if args.command=='keygen':
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode());return
    cfg=from_env()
    if not cfg.hosted:parser.error('Imposta IRON_MODE=hosted e la configurazione del servizio.')
    from app.db.database import DATA_DIR
    from app.commercial.accounts import Store
    from app.commercial.operations import backup,restore,purge_deletions
    if args.command in ('backup','restore'):
        key=os.getenv('IRON_BACKUP_KEY','')
        if not key or key==cfg.vault_key:parser.error('Configura IRON_BACKUP_KEY separatamente da IRON_VAULT_KEY.')
        result=backup(DATA_DIR,args.output,key) if args.command=='backup' else restore(args.input,args.target,key)
    elif args.command=='check':result={'mode':cfg.mode,'public_url':cfg.public_url,'legal_ready':cfg.legal_ready,'email_ready':cfg.email_ready,'billing_ready':cfg.billing_ready,'billing_required':cfg.billing_required}
    else:
        service=Store(DATA_DIR,cfg)
        try:
            if args.command=='invite':
                import re
                if not 1<=args.days<=30 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',args.email):parser.error('Indica email valida e durata tra 1 e 30 giorni.')
                token=service.invite(args.email,args.days);result={'invitation_url':cfg.public_url+'/login#invite='+token,'expires_in_days':args.days}
            else:result=purge_deletions(service,DATA_DIR,args.older_than_days)
        finally:service.engine.dispose()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
