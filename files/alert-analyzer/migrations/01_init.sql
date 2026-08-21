-- Alert Analysis table for Alertmanager webhook analysis
CREATE TABLE IF NOT EXISTS alert_analysis (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    alert_fingerprint VARCHAR(64) NOT NULL,
    alertname VARCHAR(255) NOT NULL,
    severity VARCHAR(50) NOT NULL,
    cluster VARCHAR(255),
    namespace VARCHAR(255),
    service VARCHAR(255),
    status VARCHAR(20) NOT NULL,
    correlated_group TEXT,
    root_cause TEXT,
    suggested_actions TEXT,
    confidence DOUBLE PRECISION,
    raw_alerts TEXT NOT NULL,
    llm_model VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_alert_analysis_created ON alert_analysis(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_analysis_fingerprint ON alert_analysis(alert_fingerprint);
CREATE INDEX IF NOT EXISTS idx_alert_analysis_alertname ON alert_analysis(alertname);
CREATE INDEX IF NOT EXISTS idx_alert_analysis_status ON alert_analysis(status);