-- Initial schema for Auto SRE
CREATE TABLE IF NOT EXISTS findings (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    severity VARCHAR(20) NOT NULL DEFAULT 'low',
    service VARCHAR(255),
    title VARCHAR(500) NOT NULL DEFAULT 'Аномалия',
    summary TEXT,
    possible_cause TEXT,
    recommended_action TEXT,
    confidence REAL,
    raw_data TEXT,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_findings_created ON findings(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_findings_service ON findings(service);

CREATE TABLE IF NOT EXISTS blog_posts (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blog_created ON blog_posts(created_at DESC);