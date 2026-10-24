from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from app.main import app
service=app.state.accounts
tokens={email:service.invite(email) for email in ['qa-a@product.test','qa-b@product.test']}
(Path(__file__).parent/'invitations.json').write_text(json.dumps(tokens))
