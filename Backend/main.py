import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import date

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "database.db"

# --- 1. ИНИЦИАЛИЗАЦИЯ БД ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Таблица задач
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            reward INTEGER NOT NULL,
            is_completed INTEGER DEFAULT 0,
            completed_date TEXT,
            reward_claimed INTEGER DEFAULT 0
        )
    """)
    
    # Таблица баланса
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_balance (
            id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO user_balance (id, balance) VALUES (1, 0)")
    
    # НОВАЯ ТАБЛИЦА: Товары в магазине
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            price INTEGER NOT NULL
        )
    """)
    
    # Закинем стартовые товары, если магазин пустой
    cursor.execute("SELECT COUNT(*) FROM shop_items")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Поиграть в Xbox 1 час', 40)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Съесть чипсы под сериал', 80)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Заказать пиццу', 250)")
        
    conn.commit()
    conn.close()

init_db()


def process_pending_rewards():
    today_str = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, reward FROM tasks 
        WHERE is_completed = 1 AND reward_claimed = 0 AND completed_date < ?
    """, (today_str,))
    pending = cursor.fetchall()
    
    if pending:
        total = sum(t[1] for t in pending)
        ids = [t[0] for t in pending]
        cursor.execute("UPDATE user_balance SET balance = balance + ? WHERE id = 1", (total,))
        placeholders = ",".join("?" for _ in ids)
        cursor.execute(f"UPDATE tasks SET reward_claimed = 1 WHERE id IN ({placeholders})", ids)
        conn.commit()
    conn.close()


# --- МОДЕЛИ ДАННЫХ ---
class TaskCreate(BaseModel):
    title: str
    reward: int

class ShopItemCreate(BaseModel):
    title: str
    price: int


# --- ЭНДПОИНТЫ ЗАДАЧ ---

@app.get("/balance")
def get_balance():
    process_pending_rewards()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    balance = cursor.fetchone()[0]
    conn.close()
    return {"balance": balance}

@app.get("/tasks")
def get_tasks():
    process_pending_rewards()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, reward, is_completed, completed_date, reward_claimed FROM tasks")
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "reward": r[2], "is_completed": bool(r[3]), "reward_claimed": bool(r[5])} for r in rows]

@app.post("/tasks")
def create_task(task: TaskCreate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (title, reward) VALUES (?, ?)", (task.title, task.reward))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    today_str = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT reward, is_completed, reward_claimed FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Задача не найдена")
        
    reward, is_completed, reward_claimed = task
    
    if is_completed == 0:
        cursor.execute("UPDATE tasks SET is_completed = 1, completed_date = ?, reward_claimed = 0 WHERE id = ?", (today_str, task_id))
    else:
        if reward_claimed == 1:
            cursor.execute("UPDATE user_balance SET balance = balance - ? WHERE id = 1", (reward,))
        cursor.execute("UPDATE tasks SET is_completed = 0, completed_date = NULL, reward_claimed = 0 WHERE id = ?", (task_id,))
            
    conn.commit()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    curr_balance = cursor.fetchone()[0]
    conn.close()
    return {"status": "success", "current_balance": curr_balance}


# НОВОЕ: УДАЛЕНИЕ ЗАДАЧИ
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT reward, is_completed, reward_claimed FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Задача не найдена")
        
    reward, is_completed, reward_claimed = task
    # Если за неё уже выплатили деньги — вычитаем из кошелька при удалении
    if is_completed == 1 and reward_claimed == 1:
        cursor.execute("UPDATE user_balance SET balance = balance - ? WHERE id = 1", (reward,))
        
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}


# --- ЭНДПОИНТЫ МАГАЗИНА ---

@app.get("/shop")
def get_shop_items():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, price FROM shop_items")
    items = [{"id": r[0], "title": r[1], "price": r[2]} for r in cursor.fetchall()]
    conn.close()
    return items

@app.post("/shop/{item_id}/buy")
def buy_item(item_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT title, price FROM shop_items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Товар не найден")
        
    title, price = item
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    balance = cursor.fetchone()[0]
    
    if balance < price:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Не хватает {price - balance} монет!")
        
    # Списываем бабки
    cursor.execute("UPDATE user_balance SET balance = balance - ? WHERE id = 1", (price,))
    conn.commit()
    
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    new_bal = cursor.fetchone()[0]
    conn.close()
    return {"status": "success", "new_balance": new_bal, "bought": title}


# --- ЭНДПОИНТЫ АДМИНКИ ---

@app.post("/shop")
def create_shop_item(item: ShopItemCreate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO shop_items (title, price) VALUES (?, ?)", (item.title, item.price))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/shop/{item_id}")
def delete_shop_item(item_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}