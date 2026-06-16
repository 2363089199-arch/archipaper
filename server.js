#!/usr/bin/env node
/**
 * ArchiPaper Server — Node.js + Express 后端
 * 支持多用户、数据隔离、PDF上传、标注存储
 *
 * 启动: node server.js [--port 8080]
 */

const express = require('express');
const multer  = require('multer');
const path    = require('path');
const fs      = require('fs');
const crypto  = require('crypto');
const Database = require('better-sqlite3');

// ========== 配置 ==========
const PORT = process.env.PORT || (process.argv.includes('--port')
    ? parseInt(process.argv[process.argv.indexOf('--port') + 1])
    : 8080);

const BASE_DIR    = __dirname;
const DB_PATH     = path.join(BASE_DIR, 'data.db');
const UPLOADS_DIR = path.join(BASE_DIR, 'uploads');

fs.mkdirSync(UPLOADS_DIR, { recursive: true });

// ========== 数据库 ==========
const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');
db.exec(`
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
        keywords      TEXT DEFAULT '',
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
`);

// ========== 工具函数 ==========
function hashPw(pw) {
    return crypto.createHash('sha256').update(pw).digest('hex');
}

function genToken() {
    return crypto.randomBytes(32).toString('hex');
}

function getUid(req) {
    const auth = req.headers.authorization || '';
    if (!auth.startsWith('Bearer ')) return null;
    const token = auth.slice(7);
    const row = db.prepare('SELECT id FROM users WHERE token=?').get(token);
    return row ? row.id : null;
}

function requireAuth(req, res, next) {
    const uid = getUid(req);
    if (!uid) return res.status(401).json({ error: '未登录' });
    req.uid = uid;
    next();
}

// ========== Express 应用 ==========
const app = express();

// CORS
app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Headers', 'Authorization, Content-Type');
    res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    if (req.method === 'OPTIONS') return res.sendStatus(200);
    next();
});

// JSON body 解析
app.use(express.json({ limit: '50mb' }));

// Multer 用于 PDF 上传
const upload = multer({ dest: UPLOADS_DIR });

// ========== Auth API ==========
app.post('/api/register', (req, res) => {
    const { username, password } = req.body || {};
    if (!username || !password) return res.status(400).json({ error: '用户名和密码不能为空' });
    const token = genToken();
    try {
        db.prepare('INSERT INTO users (username, password, token) VALUES (?, ?, ?)')
            .run(username, hashPw(password), token);
        res.json({ token, username });
    } catch (e) {
        if (e.message.includes('UNIQUE')) return res.status(409).json({ error: '用户名已存在' });
        res.status(500).json({ error: '注册失败' });
    }
});

app.post('/api/login', (req, res) => {
    const { username, password } = req.body || {};
    const row = db.prepare('SELECT id, token FROM users WHERE username=? AND password=?')
        .get(username || '', hashPw(password || ''));
    if (!row) return res.status(401).json({ error: '用户名或密码错误' });
    const token = genToken();
    db.prepare('UPDATE users SET token=? WHERE id=?').run(token, row.id);
    res.json({ token, username });
});

// ========== Papers API ==========
app.get('/api/papers', requireAuth, (req, res) => {
    const rows = db.prepare('SELECT * FROM papers WHERE user_id=? ORDER BY num').all(req.uid);
    res.json(rows.map(r => ({
        id: r.id, num: r.num, title: r.title, title_cn: r.title_cn,
        description: r.description, categories: r.categories, paper_type: r.paper_type,
        journal: r.journal, impact_factor: r.impact_factor, tier: r.tier,
        source_url: r.source_url, has_pdf: r.has_pdf, pdf_filename: r.pdf_filename,
        keywords: r.keywords || ''
    })));
});

app.post('/api/papers', requireAuth, (req, res) => {
    const b = req.body || {};
    const existing = db.prepare('SELECT id FROM papers WHERE user_id=? AND num=?')
        .get(req.uid, b.num || 0);
    if (existing) {
        db.prepare(`UPDATE papers SET title=?, title_cn=?, description=?, categories=?,
            paper_type=?, journal=?, impact_factor=?, tier=?, source_url=?, keywords=?
            WHERE user_id=? AND num=?`).run(
            b.title, b.title_cn, b.description, b.categories, b.paper_type,
            b.journal, b.impact_factor, b.tier, b.source_url,
            JSON.stringify(b.keywords || []), req.uid, b.num
        );
    } else {
        db.prepare(`INSERT INTO papers (user_id, num, title, title_cn, description,
            categories, paper_type, journal, impact_factor, tier, source_url, keywords)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
            req.uid, b.num, b.title, b.title_cn, b.description, b.categories,
            b.paper_type, b.journal, b.impact_factor, b.tier, b.source_url,
            JSON.stringify(b.keywords || [])
        );
    }
    res.json({ ok: true });
});

app.delete('/api/papers/:num', requireAuth, (req, res) => {
    const paper = db.prepare('SELECT id, pdf_filename FROM papers WHERE user_id=? AND num=?')
        .get(req.uid, parseInt(req.params.num));
    if (!paper) return res.status(404).json({ error: '论文不存在' });
    if (paper.pdf_filename) {
        const p = path.join(UPLOADS_DIR, paper.pdf_filename);
        if (fs.existsSync(p)) fs.unlinkSync(p);
    }
    db.prepare('DELETE FROM annotations WHERE paper_id=?').run(paper.id);
    db.prepare('DELETE FROM papers WHERE id=?').run(paper.id);
    res.json({ ok: true });
});

// ========== PDF API ==========
app.get('/api/papers/:num/pdf', requireAuth, (req, res) => {
    const paper = db.prepare('SELECT * FROM papers WHERE user_id=? AND num=?')
        .get(req.uid, parseInt(req.params.num));
    if (!paper || !paper.pdf_filename) return res.status(404).json({ error: 'PDF不存在' });
    const pdfPath = path.join(UPLOADS_DIR, paper.pdf_filename);
    if (!fs.existsSync(pdfPath)) return res.status(404).json({ error: 'PDF文件丢失' });
    res.sendFile(pdfPath);
});

app.post('/api/papers/:num/pdf', requireAuth, upload.single('file'), (req, res) => {
    const paper = db.prepare('SELECT id FROM papers WHERE user_id=? AND num=?')
        .get(req.uid, parseInt(req.params.num));
    if (!paper) return res.status(404).json({ error: '论文不存在' });

    let fileData = null;
    let filename = null;

    if (req.file) {
        // Multer multipart
        fileData = fs.readFileSync(req.file.path);
        fs.unlinkSync(req.file.path); // 清理临时文件
    } else if (req.body && req.body.data) {
        // Base64 JSON
        fileData = Buffer.from(req.body.data, 'base64');
    }

    if (!fileData) return res.status(400).json({ error: '未提供文件' });

    filename = `${req.uid}_${paper.id}.pdf`;
    const filepath = path.join(UPLOADS_DIR, filename);
    fs.writeFileSync(filepath, fileData);

    db.prepare('UPDATE papers SET has_pdf=1, pdf_filename=? WHERE id=?').run(filename, paper.id);
    res.json({ ok: true, filename });
});

// ========== Annotations API ==========
app.get('/api/papers/:num/annotations', requireAuth, (req, res) => {
    const paper = db.prepare('SELECT id FROM papers WHERE user_id=? AND num=?')
        .get(req.uid, parseInt(req.params.num));
    if (!paper) return res.status(404).json({ error: '论文不存在' });
    const ann = db.prepare('SELECT pen_data, hl_data FROM annotations WHERE paper_id=? AND user_id=?')
        .get(paper.id, req.uid);
    if (ann) return res.json({ pen_data: ann.pen_data, hl_data: ann.hl_data });
    res.json({ pen_data: null, hl_data: null });
});

app.post('/api/papers/:num/annotations', requireAuth, (req, res) => {
    const paper = db.prepare('SELECT id FROM papers WHERE user_id=? AND num=?')
        .get(req.uid, parseInt(req.params.num));
    if (!paper) return res.status(404).json({ error: '论文不存在' });
    const b = req.body || {};
    const existing = db.prepare('SELECT id FROM annotations WHERE paper_id=? AND user_id=?')
        .get(paper.id, req.uid);
    if (existing) {
        db.prepare(`UPDATE annotations SET pen_data=?, hl_data=?,
            updated_at=datetime('now','localtime') WHERE paper_id=? AND user_id=?`)
            .run(b.pen_data, b.hl_data, paper.id, req.uid);
    } else {
        db.prepare('INSERT INTO annotations (paper_id, user_id, pen_data, hl_data) VALUES (?,?,?,?)')
            .run(paper.id, req.uid, b.pen_data, b.hl_data);
    }
    res.json({ ok: true });
});

// ========== Export / Import ==========
app.get('/api/export', requireAuth, (req, res) => {
    const rows = db.prepare('SELECT * FROM papers WHERE user_id=? ORDER BY num').all(req.uid);
    res.json(rows.map(r => ({
        num: r.num, title: r.title, title_cn: r.title_cn, description: r.description,
        categories: r.categories, paper_type: r.paper_type, journal: r.journal,
        impact_factor: r.impact_factor, tier: r.tier, source_url: r.source_url,
        has_pdf: r.has_pdf, pdf_filename: r.pdf_filename
    })));
});

app.post('/api/import', requireAuth, (req, res) => {
    const papers = (req.body || {}).papers || [];
    const insertStmt = db.prepare(`INSERT INTO papers (user_id, num, title, title_cn, description,
        categories, paper_type, journal, impact_factor, tier, source_url, keywords)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`);
    const updateStmt = db.prepare(`UPDATE papers SET title=?, title_cn=?, description=?, categories=?,
        paper_type=?, journal=?, impact_factor=?, tier=?, source_url=?, keywords=?
        WHERE user_id=? AND num=?`);

    const runInTx = db.transaction(() => {
        let count = 0;
        for (const p of papers) {
            const existing = db.prepare('SELECT id FROM papers WHERE user_id=? AND num=?')
                .get(req.uid, p.num || 0);
            if (existing) {
                updateStmt.run(p.title, p.title_cn, p.description, p.categories,
                    p.paper_type, p.journal, p.impact_factor, p.tier, p.source_url,
                    JSON.stringify(p.keywords || []), req.uid, p.num);
            } else {
                insertStmt.run(req.uid, p.num, p.title, p.title_cn, p.description,
                    p.categories, p.paper_type, p.journal, p.impact_factor, p.tier,
                    p.source_url, JSON.stringify(p.keywords || []));
            }
            count++;
        }
        return count;
    });

    const count = runInTx();
    res.json({ ok: true, count });
});

// ========== 静态文件 ==========
app.use(express.static(BASE_DIR));

// SPA fallback：所有未匹配路由返回 index.html
app.get('/{*splat}', (req, res) => {
    res.sendFile(path.join(BASE_DIR, 'index.html'));
});

// ========== 启动 ==========
app.listen(PORT, '0.0.0.0', () => {
    console.log(`
╔══════════════════════════════════════╗
║    ArchiPaper Server (Node.js)      ║
║  论文管理后端服务                     ║
╠══════════════════════════════════════╣
║  本机访问: http://localhost:${PORT}     ║
║  局域网:   http://x.x.x.x:${PORT}       ║
╚══════════════════════════════════════╝
`);
});
