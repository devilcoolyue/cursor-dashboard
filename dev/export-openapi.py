"""Generate the API contract without opening a database or reading secrets."""
import json
from pathlib import Path
from types import SimpleNamespace

from cursor_dashboard.api.app import create_app

core = SimpleNamespace(config=SimpleNamespace(mode='server'), identity=SimpleNamespace(initialized=lambda: True))
app = create_app(core, public_origin='https://panel.example.test', web_dir=Path('/nonexistent-openapi-assets'))
target = Path(__file__).resolve().parents[1] / 'frontend' / 'openapi.json'
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
