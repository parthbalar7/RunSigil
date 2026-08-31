import { FormEvent, useEffect, useMemo, useState } from "react";
import { ApiProblem, api, type ApiConfig } from "./api";
import { ApprovalPanel } from "./components/ApprovalPanel";
import { RunPath, Timeline } from "./components/RunPath";
import type { RunDetail, TraceEvent, WorkspaceContext } from "./types";

type Theme = "light" | "dark";

function Login({ onConnect }: { onConnect: (config: ApiConfig, context: WorkspaceContext) => void }) {
  const [baseUrl, setBaseUrl] = useState("http://localhost:8000");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const config = { baseUrl, apiKey };
      const context = await api.context(config);
      sessionStorage.setItem("runsigil.apiUrl", baseUrl);
      sessionStorage.setItem("runsigil.apiKey", apiKey);
      onConnect(config, context);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Unable to connect to RunSigil.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand large">
          <span className="brand-mark" aria-hidden="true">R</span>
          <div><strong>RunSigil</strong><span>Govern every agent run.</span></div>
        </div>
        <div className="login-copy">
          <p className="eyebrow">Milestone 1 operator access</p>
          <h1 id="login-title">Open the governed action console</h1>
          <p>Connect to your local control plane. The API key stays in this browser tab only.</p>
        </div>
        <form onSubmit={(event) => void connect(event)}>
          <label className="field">
            <span>Control API URL</span>
            <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} inputMode="url" />
          </label>
          <label className="field">
            <span>API key</span>
            <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} type="password" autoComplete="off" />
          </label>
          {error && <div className="error-banner" role="alert">{error}</div>}
          <button className="button primary wide" disabled={!apiKey || busy} type="submit">
            {busy ? "Connecting…" : "Connect securely"}
          </button>
        </form>
        <p className="login-footnote">OIDC login is planned for a later milestone. This slice uses scoped, hashed API keys.</p>
      </section>
    </main>
  );
}

function StartRun({ context, onStart, busy }: { context: WorkspaceContext; onStart: (value: Record<string, unknown>) => Promise<void>; busy: boolean }) {
  const [recipient, setRecipient] = useState("ops@example.test");
  const [amount, setAmount] = useState(4200);
  const [description, setDescription] = useState("Approved invoice notification");
  return (
    <section className="panel start-panel" aria-labelledby="start-title">
      <div className="section-heading"><div><p className="eyebrow">Live example</p><h2 id="start-title">Request a governed action</h2></div><span className="pill status-high">High risk</span></div>
      <p>The production policy will pause this side effect for an exact-content approval.</p>
      <form onSubmit={(event) => { event.preventDefault(); void onStart({
        project_id: context.projects[0].id,
        environment_id: context.environments[0].id,
        agent_id: context.agents[0].id,
        recipient,
        amount_cents: amount,
        description,
        idempotency_key: `web-${crypto.randomUUID()}`,
        simulate_outcome: "committed",
      }); }}>
        <div className="form-grid">
          <label className="field"><span>Recipient</span><input type="email" value={recipient} onChange={(event) => setRecipient(event.target.value)} /></label>
          <label className="field"><span>Amount (cents)</span><input type="number" min="1" max="100000" value={amount} onChange={(event) => setAmount(Number(event.target.value))} /></label>
        </div>
        <label className="field"><span>Description</span><input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <button type="submit" className="button primary" disabled={busy}>{busy ? "Creating durable intent…" : "Start governed run"}</button>
      </form>
    </section>
  );
}

function Console({ config, context, onDisconnect }: { config: ApiConfig; context: WorkspaceContext; onDisconnect: () => void }) {
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("runsigil.theme") as Theme) || "light");
  const [run, setRun] = useState<RunDetail | null>(null);
  const [pendingRun, setPendingRun] = useState<RunDetail | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("runsigil.theme", theme);
  }, [theme]);

  useEffect(() => {
    if (!run || ["completed", "failed", "cancelled"].includes(run.status)) return;
    const timer = window.setInterval(() => {
      void api.getRun(config, run.id).then((next) => {
        const changed = next.trace_events.length !== run.trace_events.length || next.status !== run.status;
        if (!changed) return;
        if (selectedEvent) setPendingRun(next);
        else setRun(next);
      }).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [config, run, selectedEvent]);

  const filteredEvents = useMemo(
    () => (run?.trace_events ?? []).filter((event) => !selectedNode || event.node_id === selectedNode),
    [run, selectedNode],
  );

  async function start(input: Record<string, unknown>) {
    setBusy(true); setError(""); setSelectedNode(null); setSelectedEvent(null);
    try { setRun(await api.startRun(config, input as Parameters<typeof api.startRun>[1])); }
    catch (problem) { setError(problem instanceof ApiProblem ? `${problem.code}: ${problem.message}` : "Unable to start the run."); }
    finally { setBusy(false); }
  }

  async function decide(decision: "approve" | "deny", reason: string) {
    if (!run?.approval) return;
    setBusy(true); setError("");
    try { setRun(await api.decideApproval(config, run.approval.id, { content_digest: run.approval.content_digest, decision, reason })); }
    catch (problem) { setError(problem instanceof ApiProblem ? `${problem.code}: ${problem.message}` : "Unable to record the decision."); }
    finally { setBusy(false); }
  }

  function selectEvent(event: TraceEvent) { setSelectedEvent(event.id); setSelectedNode(event.node_id); }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark" aria-hidden="true">R</span><div><strong>RunSigil</strong><span>Control plane</span></div></div>
        <nav aria-label="Primary navigation">
          <a className="nav-item active" href="#overview" aria-current="page">Overview</a>
          <a className="nav-item" href="#runs">Runs</a>
          <a className="nav-item" href="#approvals">Approvals</a>
          <a className="nav-item" href="#evidence">Evidence</a>
        </nav>
        <div className="milestone-note"><span>Milestone 1</span><p>One complete governed-action slice. Later product areas are intentionally absent.</p></div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <div className="selectors" aria-label="Current scope"><button type="button">{context.organization.name}</button><span>/</span><button type="button">{context.projects[0]?.name}</button><span>/</span><button type="button">{context.environments[0]?.name}</button></div>
          <div className="top-actions"><button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} type="button" aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`}>{theme === "light" ? "Dark" : "Light"}</button><button className="text-button" onClick={onDisconnect} type="button">Disconnect</button></div>
        </header>
        <main className="content" id="overview">
          <div className="page-heading"><div><p className="breadcrumb">Operations / Governed action</p><h1>Run investigation</h1><p>Follow policy, approval, execution, reconciliation, and evidence in one view.</p></div>{run && <span className={`run-state status-${run.status}`}>{run.status.replaceAll("_", " ")}</span>}</div>
          {error && <div className="error-banner" role="alert">{error}</div>}
          {!run ? <StartRun context={context} onStart={start} busy={busy} /> : (
            <>
              {pendingRun && <button type="button" className="new-events" onClick={() => { setRun(pendingRun); setPendingRun(null); setSelectedEvent(null); }}>New events available — update view</button>}
              <div className="metric-strip" aria-label="Run summary">
                <div><span>Run</span><code>{run.id.slice(0, 12)}</code></div><div><span>Active node</span><strong>{run.active_node ?? "Complete"}</strong></div><div><span>Attempts</span><strong>{run.action?.execute_attempts ?? 0}</strong></div><div><span>Evidence</span><strong>{run.evidence_status}</strong></div>
              </div>
              <RunPath run={run} selectedNode={selectedNode} onSelect={(node) => { setSelectedNode(node); setSelectedEvent(null); }} />
              <div className="investigation-grid">
                <Timeline events={filteredEvents} selectedEvent={selectedEvent} onSelect={selectEvent} />
                {run.approval ? <ApprovalPanel approval={run.approval} busy={busy} onDecision={decide} /> : <aside className="panel detail-panel"><p className="eyebrow">Action detail</p><h2>{run.action?.tool_name}</h2><dl className="fact-list"><div><dt>State</dt><dd>{run.action?.state}</dd></div><div><dt>Content digest</dt><dd><code>{run.action?.content_digest}</code></dd></div><div><dt>Evidence</dt><dd>{run.evidence_status}</dd></div></dl></aside>}
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const [config, setConfig] = useState<ApiConfig | null>(null);
  const [context, setContext] = useState<WorkspaceContext | null>(null);
  function disconnect() { sessionStorage.removeItem("runsigil.apiKey"); setConfig(null); setContext(null); }
  return config && context ? <Console config={config} context={context} onDisconnect={disconnect} /> : <Login onConnect={(nextConfig, nextContext) => { setConfig(nextConfig); setContext(nextContext); }} />;
}

