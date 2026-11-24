"""Read-only system monitoring + persistent tasks/memories + opt-in AI chat."""
import os
from contextlib import asynccontextmanager

import httpx
import psutil
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    yield


app = FastAPI(title='ASTRA API (prototype local)', version='0.1.0', lifespan=lifespan)
# Development only. In packaged builds, use Tauri IPC or runtime-issued credentials.
app.add_middleware(CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_methods=['GET', 'POST', 'PATCH', 'DELETE'],
    allow_headers=['Content-Type'],
)

class MemoryInput(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    detail: str = Field(default='', max_length=8000)
    category: str = Field(default='general', pattern=r'^[a-zA-Z0-9_-]{1,30}$')

class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=140)

class StatusInput(BaseModel):
    status: str = Field(pattern=r'^(pending|running|done|error)$')

class ChatInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    mode: str = Field(default='local', pattern=r'^(local|cloud)$')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'cloud_enabled': os.getenv('ASTRA_ENABLE_CLOUD') == '1'}


@app.get('/api/system')
def system_status():
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    return {
        'cpu_percent': round(psutil.cpu_percent(interval=0.1), 1),
        'ram_percent': round(memory.percent, 1),
        'disk_percent': round(disk.percent, 1),
        'ram_total_gb': round(memory.total / (1024 ** 3), 1),
    }


@app.get('/api/memories')
def memories():
    return db.rows('SELECT * FROM memories ORDER BY id DESC LIMIT 150')


@app.post('/api/memories', status_code=201)
def create_memory(item: MemoryInput):
    if not item.title.strip():
        raise HTTPException(422, 'Le titre ne peut pas être vide.')
    return db.add_memory(item.title.strip(), item.detail, item.category)


@app.delete('/api/memories/{item_id}', status_code=204)
def remove_memory(item_id: int):
    if not db.delete_memory(item_id):
        raise HTTPException(404, 'Mémoire introuvable')


@app.get('/api/tasks')
def tasks():
    return db.rows('SELECT * FROM tasks ORDER BY id DESC LIMIT 150')


@app.post('/api/tasks', status_code=201)
def create_task(item: TaskInput):
    if not item.title.strip():
        raise HTTPException(422, 'Le titre ne peut pas être vide.')
    return db.add_task(item.title.strip())


@app.patch('/api/tasks/{item_id}')
def update_task(item_id: int, item: StatusInput):
    result = db.set_task_status(item_id, item.status)
    if result is None:
        raise HTTPException(404, 'Tâche introuvable')
    return result


@app.get('/api/events')
def events():
    return db.rows('SELECT * FROM events ORDER BY id DESC LIMIT 200')


@app.delete('/api/events', status_code=204)
def remove_event_history():
    db.clear_events()


@app.post('/api/tools/diagnostic')
def run_diagnostic():
    """A real, explicitly invoked, read-only action. No repair is performed."""
    task = db.add_task('Diagnostic ponctuel du PC')
    db.set_task_status(task['id'], 'running')
    try:
        snapshot = system_status()
        result = (
            f"CPU {snapshot['cpu_percent']} %, RAM {snapshot['ram_percent']} %, "
            f"disque {snapshot['disk_percent']} % — mesure ponctuelle, sans réparation."
        )
        task = db.complete_task(task['id'], result)
        return {'task': task, 'metrics': snapshot}
    except Exception as exc:
        db.set_task_status(task['id'], 'error')
        raise HTTPException(500, 'Diagnostic local indisponible.') from exc


@app.post('/api/chat')
async def chat(item: ChatInput):
    if item.mode == 'local':
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post('http://127.0.0.1:11434/api/chat', json={
                    'model': os.getenv('ASTRA_LOCAL_MODEL', 'llama3.2:3b'),
                    'messages': [{'role': 'user', 'content': item.prompt}],
                    'stream': False,
                })
                response.raise_for_status()
                return {'answer': response.json()['message']['content'], 'mode': 'local'}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(503, 'Ollama local indisponible ou modèle non installé.') from exc

    if os.getenv('ASTRA_ENABLE_CLOUD') != '1':
        raise HTTPException(403, 'Cloud désactivé : autorisation explicite requise.')
    if not os.getenv('OPENAI_API_KEY'):
        raise HTTPException(503, 'OPENAI_API_KEY non configurée côté serveur.')
    try:
        from openai import AsyncOpenAI
        response = await AsyncOpenAI().responses.create(
            model=os.getenv('ASTRA_CLOUD_MODEL', 'gpt-4.1-mini'),
            input=item.prompt,
        )
        return {'answer': response.output_text, 'mode': 'cloud'}
    except Exception as exc:
        # Prevent leaking API keys or provider request metadata into the UI.
        raise HTTPException(502, 'Service IA distant indisponible.') from exc
