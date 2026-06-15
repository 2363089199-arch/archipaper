#!/usr/bin/env python3
"""
ArchiPaper Server — 零依赖后端（仅用 Python 标准库）
支持多用户、数据隔离、PDF上传、标注存储

启动: python3 server.py [--port 8080]
"""

import http.server
import json
import sqlite3
import os
import re
import uuid
import hashlib
import urllib.parse
import base64
import shutil
import sys
from pathlib import Path

PORT = 8080
HOST = '0.0.0.0'
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'data.db'
UPLOADS_DIR = BASE_DIR / 'uploads'
HTML_FILE = 'AI建筑论文集.html'
# 也在 ArchiPaper 目录中找（如果当前目录没有）
HTML_PATH = BASE_DIR / HTML_FILE
if not HTML_PATH.is_file():
    alt = BASE_DIR.parent / 'ArchiPaper' / HTML_FILE
    if alt.is_file():
        HTML_PATH = alt

# ========== 数据库初始化 ==========
def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            token    TEXT UNIQUE,
            created  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS papers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL REFERENCES users(id),
            num           INTEGER NOT NULL,
            title         TEXT,
            title_cn      TEXT,
            description   TEXT,
            categories    TEXT,
            paper_type    TEXT,
            journal       TEXT,
            impact_factor TEXT,
            tier          TEXT,
            source_url    TEXT,
            has_pdf       INTEGER DEFAULT 0,
            pdf_filename  TEXT,
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS annotations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id   INTEGER NOT NULL REFERENCES papers(id),
            user_id    INTEGER NOT NULL REFERENCES users(id),
            pen_data   TEXT,
            hl_data    TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    db.commit()
    return db

# ========== 工具函数 ==========
def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def gen_token():
    return uuid.uuid4().hex

def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
    handler.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    handler.end_headers()
    handler.wfile.write(body)

def get_user_id(handler, db):
    """从 Authorization header 解析 token，返回 user_id，未认证返回 None"""
    auth = handler.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth[7:]
    row = db.execute('SELECT id FROM users WHERE token=?', [token]).fetchone()
    return row[0] if row else None

def parse_body(handler):
    length = int(handler.headers.get('Content-Length', 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode('utf-8'))

def serve_static(handler, path):
    """Serve static files (HTML, PDFs)"""
    # Security: prevent path traversal
    safe_path = os.path.normpath(path).lstrip('/')
    if safe_path == '' or safe_path == '.' or safe_path == 'index.html':
        full_path = HTML_PATH
    else:
        full_path = BASE_DIR / safe_path
    if not full_path.is_file() or not str(full_path).startswith(str(BASE_DIR)):
        # 也尝试 ArchiPaper 目录
        alt = BASE_DIR.parent / 'ArchiPaper' / safe_path
        if alt.is_file() and str(alt).startswith(str(BASE_DIR.parent)):
            full_path = alt
        else:
            handler.send_error(404)
            return
    
    content_type = 'text/html; charset=utf-8'
    if safe_path.endswith('.pdf'):
        content_type = 'application/pdf'
    elif safe_path.endswith('.js'):
        content_type = 'application/javascript'
    elif safe_path.endswith('.css'):
        content_type = 'text/css'
    elif safe_path.endswith('.json'):
        content_type = 'application/json'
    elif safe_path.endswith('.png'):
        content_type = 'image/png'
    
    with open(full_path, 'rb') as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', len(data))
    handler.end_headers()
    handler.wfile.write(data)

# ========== API 路由 ==========
class ArchiPaperHandler(http.server.BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        # 简化日志
        print(f"[{self.client_address[0]}] {args[0]}")
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.end_headers()
    
    def do_GET(self):
        db = init_db()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        
        try:
            # API 路由
            # GET /api/papers
            if path == '/api/papers':
                uid = get_user_id(self, db)
                if not uid:
                    return json_response(self, {'error': '未登录'}, 401)
                rows = db.execute(
                    'SELECT * FROM papers WHERE user_id=? ORDER BY num', [uid]
                ).fetchall()
                papers = []
                for r in rows:
                    papers.append({
                        'id': r[0], 'num': r[2], 'title': r[3], 'title_cn': r[4],
                        'description': r[5], 'categories': r[6], 'paper_type': r[7],
                        'journal': r[8], 'impact_factor': r[9], 'tier': r[10],
                        'source_url': r[11], 'has_pdf': r[12], 'pdf_filename': r[13]
                    })
                return json_response(self, papers)
            
            # GET /api/papers/:num/pdf
            m = re.match(r'/api/papers/(\d+)/pdf', path)
            if m:
                uid = get_user_id(self, db)
                if not uid:
                    return json_response(self, {'error': '未登录'}, 401)
                paper = db.execute(
                    'SELECT * FROM papers WHERE user_id=? AND num=?', [uid, int(m.group(1))]
                ).fetchone()
                if not paper or not paper[13]:
                    self.send_error(404)
                    return
                pdf_path = UPLOADS_DIR / paper[13]
                if not pdf_path.is_file():
                    self.send_error(404)
                    return
                with open(pdf_path, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', len(data))
                self.end_headers()
                self.wfile.write(data)
                return
            
            # GET /api/papers/:num/annotations
            m = re.match(r'/api/papers/(\d+)/annotations', path)
            if m:
                uid = get_user_id(self, db)
                if not uid:
                    return json_response(self, {'error': '未登录'}, 401)
                paper = db.execute(
                    'SELECT id FROM papers WHERE user_id=? AND num=?', [uid, int(m.group(1))]
                ).fetchone()
                if not paper:
                    return json_response(self, {'error': '论文不存在'}, 404)
                ann = db.execute(
                    'SELECT pen_data, hl_data FROM annotations WHERE paper_id=? AND user_id=?',
                    [paper[0], uid]
                ).fetchone()
                if ann:
                    return json_response(self, {'pen_data': ann[0], 'hl_data': ann[1]})
                return json_response(self, {'pen_data': None, 'hl_data': None})
            
            # GET /api/export
            if path == '/api/export':
                uid = get_user_id(self, db)
                if not uid:
                    return json_response(self, {'error': '未登录'}, 401)
                papers = db.execute(
                    'SELECT * FROM papers WHERE user_id=? ORDER BY num', [uid]
                ).fetchall()
                export = []
                for r in papers:
                    export.append({
                        'num': r[2], 'title': r[3], 'title_cn': r[4], 'description': r[5],
                        'categories': r[6], 'paper_type': r[7], 'journal': r[8],
                        'impact_factor': r[9], 'tier': r[10], 'source_url': r[11],
                        'has_pdf': r[12], 'pdf_filename': r[13]
                    })
                return json_response(self, export)
            
            # 静态文件
            serve_static(self, path.lstrip('/'))
            
        except Exception as e:
            print(f"ERROR: {e}")
            json_response(self, {'error': str(e)}, 500)
        finally:
            db.close()
    
    def do_POST(self):
        db = init_db()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        try:
            # POST /api/register
            if path == '/api/register':
                body = parse_body(self)
                if not body.get('username') or not body.get('password'):
                    return json_response(self, {'error': '用户名和密码不能为空'}, 400)
                try:
                    db.execute(
                        'INSERT INTO users (username, password, token) VALUES (?, ?, ?)',
                        [body['username'], hash_pw(body['password']), gen_token()]
                    )
                    db.commit()
                    token = db.execute(
                        'SELECT token FROM users WHERE username=?', [body['username']]
                    ).fetchone()[0]
                    return json_response(self, {'token': token, 'username': body['username']})
                except sqlite3.IntegrityError:
                    return json_response(self, {'error': '用户名已存在'}, 409)
            
            # POST /api/login
            if path == '/api/login':
                body = parse_body(self)
                row = db.execute(
                    'SELECT id, token FROM users WHERE username=? AND password=?',
                    [body.get('username', ''), hash_pw(body.get('password', ''))]
                ).fetchone()
                if not row:
                    return json_response(self, {'error': '用户名或密码错误'}, 401)
                # 生成新 token
                token = gen_token()
                db.execute('UPDATE users SET token=? WHERE id=?', [token, row[0]])
                db.commit()
                return json_response(self, {'token': token, 'username': body['username']})
            
            uid = get_user_id(self, db)
            if not uid:
                return json_response(self, {'error': '未登录'}, 401)
            
            # POST /api/papers
            if path == '/api/papers':
                body = parse_body(self)
                existing = db.execute(
                    'SELECT id FROM papers WHERE user_id=? AND num=?', [uid, body.get('num', 0)]
                ).fetchone()
                if existing:
                    # 更新
                    db.execute(
                        '''UPDATE papers SET title=?, title_cn=?, description=?, categories=?,
                           paper_type=?, journal=?, impact_factor=?, tier=?, source_url=?
                           WHERE user_id=? AND num=?''',
                        [body.get('title'), body.get('title_cn'), body.get('description'),
                         body.get('categories'), body.get('paper_type'), body.get('journal'),
                         body.get('impact_factor'), body.get('tier'), body.get('source_url'),
                         uid, body.get('num')]
                    )
                else:
                    db.execute(
                        '''INSERT INTO papers (user_id, num, title, title_cn, description,
                           categories, paper_type, journal, impact_factor, tier, source_url)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        [uid, body.get('num'), body.get('title'), body.get('title_cn'),
                         body.get('description'), body.get('categories'), body.get('paper_type'),
                         body.get('journal'), body.get('impact_factor'), body.get('tier'),
                         body.get('source_url')]
                    )
                db.commit()
                return json_response(self, {'ok': True})
            
            # POST /api/papers/:num/pdf
            m = re.match(r'/api/papers/(\d+)/pdf', path)
            if m:
                paper = db.execute(
                    'SELECT id FROM papers WHERE user_id=? AND num=?', [uid, int(m.group(1))]
                ).fetchone()
                if not paper:
                    return json_response(self, {'error': '论文不存在'}, 404)
                
                # 解析 multipart upload (Python 3.13+ 无 cgi 模块，手动解析)
                content_type = self.headers.get('Content-Type', '')
                if 'multipart/form-data' in content_type:
                    os.makedirs(UPLOADS_DIR, exist_ok=True)
                    # 提取 boundary
                    boundary = None
                    for part in content_type.split(';'):
                        part = part.strip()
                        if part.startswith('boundary='):
                            boundary = part[9:].strip('"').encode()
                            break
                    if not boundary:
                        return json_response(self, {'error': '缺少 boundary'}, 400)
                    
                    length = int(self.headers.get('Content-Length', 0))
                    raw = self.rfile.read(length)
                    # 找文件内容（在两个 boundary 之间，跳过 headers）
                    parts = raw.split(b'--' + boundary)
                    filedata = None
                    for part in parts:
                        if b'filename=' in part:
                            # 跳过 headers（找到空行 \r\n\r\n）
                            idx = part.find(b'\r\n\r\n')
                            if idx >= 0:
                                filedata = part[idx+4:]
                                # 去掉末尾的 \r\n
                                if filedata.endswith(b'\r\n'):
                                    filedata = filedata[:-2]
                            break
                    
                    if filedata:
                        filename = f"{uid}_{paper[0]}.pdf"
                        filepath = UPLOADS_DIR / filename
                        with open(filepath, 'wb') as f:
                            f.write(filedata)
                        db.execute(
                            'UPDATE papers SET has_pdf=1, pdf_filename=? WHERE id=?',
                            [filename, paper[0]]
                        )
                        db.commit()
                        return json_response(self, {'ok': True, 'filename': filename})
                    return json_response(self, {'error': '未找到文件内容'}, 400)
                # 或者 base64 body
                body = parse_body(self)
                if body.get('data'):
                    os.makedirs(UPLOADS_DIR, exist_ok=True)
                    filename = f"{uid}_{paper[0]}.pdf"
                    filepath = UPLOADS_DIR / filename
                    with open(filepath, 'wb') as f:
                        f.write(base64.b64decode(body['data']))
                    db.execute(
                        'UPDATE papers SET has_pdf=1, pdf_filename=? WHERE id=?',
                        [filename, paper[0]]
                    )
                    db.commit()
                    return json_response(self, {'ok': True})
                return json_response(self, {'error': '未提供文件'}, 400)
            
            # POST /api/papers/:num/annotations
            m = re.match(r'/api/papers/(\d+)/annotations', path)
            if m:
                paper = db.execute(
                    'SELECT id FROM papers WHERE user_id=? AND num=?', [uid, int(m.group(1))]
                ).fetchone()
                if not paper:
                    return json_response(self, {'error': '论文不存在'}, 404)
                body = parse_body(self)
                existing = db.execute(
                    'SELECT id FROM annotations WHERE paper_id=? AND user_id=?',
                    [paper[0], uid]
                ).fetchone()
                if existing:
                    db.execute(
                        'UPDATE annotations SET pen_data=?, hl_data=?, updated_at=datetime("now","localtime") WHERE paper_id=? AND user_id=?',
                        [body.get('pen_data'), body.get('hl_data'), paper[0], uid]
                    )
                else:
                    db.execute(
                        'INSERT INTO annotations (paper_id, user_id, pen_data, hl_data) VALUES (?,?,?,?)',
                        [paper[0], uid, body.get('pen_data'), body.get('hl_data')]
                    )
                db.commit()
                return json_response(self, {'ok': True})
            
            # POST /api/import
            if path == '/api/import':
                body = parse_body(self)
                papers = body.get('papers', [])
                for p in papers:
                    existing = db.execute(
                        'SELECT id FROM papers WHERE user_id=? AND num=?', [uid, p.get('num', 0)]
                    ).fetchone()
                    if existing:
                        db.execute(
                            '''UPDATE papers SET title=?, title_cn=?, description=?, categories=?,
                               paper_type=?, journal=?, impact_factor=?, tier=?, source_url=?
                               WHERE user_id=? AND num=?''',
                            [p.get('title'), p.get('title_cn'), p.get('description'),
                             p.get('categories'), p.get('paper_type'), p.get('journal'),
                             p.get('impact_factor'), p.get('tier'), p.get('source_url'),
                             uid, p.get('num')]
                        )
                    else:
                        db.execute(
                            '''INSERT INTO papers (user_id, num, title, title_cn, description,
                               categories, paper_type, journal, impact_factor, tier, source_url)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                            [uid, p.get('num'), p.get('title'), p.get('title_cn'),
                             p.get('description'), p.get('categories'), p.get('paper_type'),
                             p.get('journal'), p.get('impact_factor'), p.get('tier'),
                             p.get('source_url')]
                        )
                db.commit()
                return json_response(self, {'ok': True, 'count': len(papers)})
            
            json_response(self, {'error': '未知 API'}, 404)
            
        except Exception as e:
            print(f"ERROR: {e}")
            json_response(self, {'error': str(e)}, 500)
        finally:
            db.close()
    
    def do_DELETE(self):
        db = init_db()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        try:
            uid = get_user_id(self, db)
            if not uid:
                return json_response(self, {'error': '未登录'}, 401)
            
            m = re.match(r'/api/papers/(\d+)', path)
            if m:
                paper = db.execute(
                    'SELECT id, pdf_filename FROM papers WHERE user_id=? AND num=?',
                    [uid, int(m.group(1))]
                ).fetchone()
                if not paper:
                    return json_response(self, {'error': '论文不存在'}, 404)
                # 删除 PDF 文件
                if paper[1]:
                    pdf_path = UPLOADS_DIR / paper[1]
                    if pdf_path.is_file():
                        pdf_path.unlink()
                # 删除标注
                db.execute('DELETE FROM annotations WHERE paper_id=?', [paper[0]])
                # 删除论文
                db.execute('DELETE FROM papers WHERE id=?', [paper[0]])
                db.commit()
                return json_response(self, {'ok': True})
            
            json_response(self, {'error': '未知 API'}, 404)
            
        except Exception as e:
            print(f"ERROR: {e}")
            json_response(self, {'error': str(e)}, 500)
        finally:
            db.close()

if __name__ == '__main__':
    if '--port' in sys.argv:
        idx = sys.argv.index('--port')
        PORT = int(sys.argv[idx+1])
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    init_db()
    print(f"""
╔══════════════════════════════════════╗
║       ArchiPaper Server             ║
║  论文管理后端服务                     ║
╠══════════════════════════════════════╣
║  本机访问: http://localhost:{PORT}     ║
║  局域网:   http://x.x.x.x:{PORT}       ║
║  API 文档: 见 README                 ║
╚══════════════════════════════════════╝
""")
    server = http.server.HTTPServer((HOST, PORT), ArchiPaperHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.shutdown()
