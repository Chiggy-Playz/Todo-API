from contextlib import contextmanager
from datetime import datetime
from enum import Enum
import secrets
import sqlite3
from typing import List, Literal, Optional
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(servers=[{"url": "https://todocrud.chiggydoes.tech", "description": "Production Environment"}])

# API key header configuration
API_KEY_HEADER = APIKeyHeader(name="X-API-Key")


# Database setup
def init_db():
    with sqlite3.connect("todo.db") as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                api_key TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending',
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """
        )


# Database connection context manager
@contextmanager
def get_db():
    conn = sqlite3.connect("todo.db")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class TaskStatus(Enum):
    pending = "pending"
    completed = "completed"


# Models
class UserCreate(BaseModel):
    name: str
    email: str


class TodoCreate(BaseModel):
    title: str
    description: Optional[str] = None


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[Literal["pending", "completed"]] = None


class Todo(BaseModel):
    id: int
    title: str
    description: Optional[str]
    status: Literal["pending", "completed"]
    created_at: str


# Helper function to validate API key
async def get_current_user(api_key: str = Depends(API_KEY_HEADER)):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return dict(user)


# Routes
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    with open("register.html", "r") as f:
        return HTMLResponse(content=f.read())


@app.post("/generate-api-key")
async def generate_api_key(user: UserCreate):
    api_key = secrets.token_urlsafe(32)

    with get_db() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users (name, email, api_key) VALUES (?, ?, ?)", (user.name, user.email, api_key)
            )
            conn.commit()
            return {"api_key": api_key}
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Email already registered")


@app.post("/todos/", response_model=Todo)
async def create_todo(todo: TodoCreate, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO todos (title, description, user_id)
            VALUES (?, ?, ?)
            RETURNING *
            """,
            (todo.title, todo.description, current_user["id"]),
        )
        result = dict(cursor.fetchone())
        conn.commit()
        return result


@app.get("/todos/", response_model=List[Todo])
async def read_todos(current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        todos = conn.execute("SELECT * FROM todos WHERE user_id = ?", (current_user["id"],)).fetchall()
        return [dict(todo) for todo in todos]


@app.put("/todos/{todo_id}", response_model=Todo)
async def update_todo(todo_id: int, todo_update: TodoUpdate, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        # Check if todo exists and belongs to user
        todo = conn.execute(
            "SELECT * FROM todos WHERE id = ? AND user_id = ?", (todo_id, current_user["id"])
        ).fetchone()

        if not todo:
            raise HTTPException(status_code=404, detail="Todo not found")

        # Update only provided fields
        update_fields = {k: v for k, v in todo_update.dict().items() if v is not None}
        if update_fields:
            set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
            query = f"UPDATE todos SET {set_clause} WHERE id = ? RETURNING *"
            cursor = conn.execute(query, (*update_fields.values(), todo_id))
            result = dict(cursor.fetchone())
            conn.commit()
            return result
        return dict(todo)


@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: int, current_user: dict = Depends(get_current_user)):
    with get_db() as conn:
        # Check if todo exists and belongs to user
        todo = conn.execute(
            "SELECT * FROM todos WHERE id = ? AND user_id = ?", (todo_id, current_user["id"])
        ).fetchone()

        if not todo:
            raise HTTPException(status_code=404, detail="Todo not found")

        conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()
        return {"message": "Todo deleted successfully"}


# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
