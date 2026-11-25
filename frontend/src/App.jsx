import {useEffect, useMemo, useState, useRef } from "react";
import Auth from "./Auth.jsx";
import {
  ArrowUp,
  BarChart3,
  Check,
  Clipboard,
  Download,
  FileText,
  Globe,
  Loader2,
  MessageSquare,
  Play,
  Plus,
  Search,
  Upload,
  X,
  LogOut,
  Pencil,
  Trash2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import mermaid from "mermaid";
const configuredApiUrl =
  import.meta.env.VITE_API_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "";

const API_URL = (
  configuredApiUrl ||
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : "")
).replace(/\/$/, "");

async function apiFetch(path, options = {}) {
  if (!API_URL) {
    throw new Error(
      "Backend URL is not configured. Set VITE_API_URL to your deployed backend URL in Vercel, then redeploy."
    );
  }
   const headers = new Headers(options.headers || {});
  const token = localStorage.getItem("insightlens_access_token");

  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  try {
    return await fetch(`${API_URL}${path}`, { ...options, headers });
  } catch {
    throw new Error(
      `Cannot reach the InsightLens backend at ${API_URL}. Check that the backend is running and that its CORS settings allow this frontend.`
    );
  }
}
const emptyResult = {
  source: null,
  sources: [],
  summary: "",
  key_points: [],
  findings: [],
  concepts: [],
  charts: [],
  chunk_count: 0,
};

function MermaidDiagram({ chart }) {
  const [rendered, setRendered] = useState("");
  const [renderError, setRenderError] = useState("");
  useEffect(() => {
    let cancelled = false;
    mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
    mermaid.render(`insightlens-diagram-${Date.now()}`, chart)
      .then(({ svg }) => { if (!cancelled) setRendered(svg); })
      .catch((err) => { if (!cancelled) setRenderError(err?.message || "Diagram rendering failed"); });
    return () => { cancelled = true; };
  }, [chart]);
  if (renderError) return <pre className="mermaid-source">{chart}</pre>;
  return <div className="mermaid-render" dangerouslySetInnerHTML={{ __html: rendered }} />;
}

function App() {
   // Authentication state
  const [authUser, setAuthUser] = useState(null);
  const [authChecking, setAuthChecking] = useState(true);

  // Verify an existing login session
  useEffect(() => {
    let cancelled = false;

    async function checkSession() {
      const token = localStorage.getItem("insightlens_access_token");

      if (!token) {
        if (!cancelled) setAuthChecking(false);
        return;
      }

      try {
        const response = await apiFetch("/api/auth/me", {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });

        if (!response.ok) {
          localStorage.removeItem("insightlens_access_token");
          if (!cancelled) setAuthUser(null);
          return;
        }

        const user = await response.json();

        if (!cancelled) {
          setAuthUser(user);
        }
      } catch (error) {
        console.error("Session verification failed:", error);
        localStorage.removeItem("insightlens_access_token");

        if (!cancelled) {
          setAuthUser(null);
        }
      } finally {
        if (!cancelled) {
          setAuthChecking(false);
        }
      }
    }

    checkSession();

    return () => {
      cancelled = true;
    };
  }, []);
  const [historyItems, setHistoryItems] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historySearch, setHistorySearch] = useState("");
  const [workspaces, setWorkspaces] = useState([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");

// Load the user's workspaces after authentication.
useEffect(() => {
  if (!authUser) {
    setWorkspaces([]);
    setSelectedWorkspaceId("");
    return;
  }

  let cancelled = false;

  async function loadWorkspaces() {
    setWorkspaceLoading(true);
    setWorkspaceError("");

    try {
      const response = await apiFetch("/api/workspaces");
      const data = await parseResponse(response);
      setSelectedWorkspaceId(data.workspace_id);
      applyResult({
  ...data.result,
  session_id: data.session_id,
});
      const items = Array.isArray(data) ? data : [];

      if (!cancelled) {
        setWorkspaces(items);

        setSelectedWorkspaceId((current) => {
          if (items.some((item) => item.id === current)) {
            return current;
          }

          const defaultWorkspace = items.find(
            (item) => item.name === "My Workspace"
          );

          return defaultWorkspace?.id || items[0]?.id || "";
        });
      }
    } catch (err) {
      if (!cancelled) {
        setWorkspaceError(err.message);
      }
    } finally {
      if (!cancelled) {
        setWorkspaceLoading(false);
      }
    }
  }

  loadWorkspaces();

  return () => {
    cancelled = true;
  };
}, [authUser]);

// Load history belonging to the selected workspace.
useEffect(() => {
  if (!authUser || !selectedWorkspaceId) {
    setHistoryItems([]);
    return;
  }

  let cancelled = false;

  async function loadHistory() {
    setHistoryLoading(true);
    setHistoryError("");

    try {
      const response = await apiFetch(
        `/api/history?workspace_id=${encodeURIComponent(selectedWorkspaceId)}`
      );

      const data = await parseResponse(response);

      if (!cancelled) {
        setHistoryItems(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      if (!cancelled) {
        setHistoryError(err.message);
      }
    } finally {
      if (!cancelled) {
        setHistoryLoading(false);
      }
    }
  }

  loadHistory();

  return () => {
    cancelled = true;
  };
}, [authUser, selectedWorkspaceId]);
  // Existing InsightLens states
  const [mode, setMode] = useState("url");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(emptyResult);
  const [sessionId, setSessionId] = useState("");
  const [question, setQuestion] = useState("");
  const [language, setLanguage] = useState("English");
  const [translating, setTranslating] = useState(false);

async function changeLanguage(nextLanguage) {
  if (!sessionId || !result.summary) {
    setLanguage(nextLanguage);
    return;
  }

  const previousLanguage = language;

  setTranslating(true);
  setError("");

  try {
    const response = await apiFetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        language: nextLanguage,
      }),
    });

    const data = await parseResponse(response);

    setResult((current) => ({
      ...current,
      summary: data.summary,
      key_points: data.key_points,
      findings: data.findings,
      concepts: data.concepts,
    }));

    setLanguage(nextLanguage);
  } catch (err) {
    setLanguage(previousLanguage);
    setError(`Translation failed: ${err.message}`);
  } finally {
    setTranslating(false);
  }
}
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copiedIndex, setCopiedIndex] = useState(null);
  const [studyTool, setStudyTool] = useState("");
  const [studyData, setStudyData] = useState(null);
  const [studyDataSessionId, setStudyDataSessionId] = useState("");
  const studyRequestRef = useRef(0);
  const [studyBusy, setStudyBusy] = useState(false);
  const [studyError, setStudyError] = useState("");
  const [cardIndex, setCardIndex] = useState(0);
  const [cardFlipped, setCardFlipped] = useState(false);
  const [cardStatus, setCardStatus] = useState({});
  const [compareUrls, setCompareUrls] = useState("");
  const [comparison, setComparison] = useState(null);
  const [compareBusy, setCompareBusy] = useState(false);

  function resetStudyTools() {
    studyRequestRef.current += 1;
    setStudyTool("");
    setStudyData(null);
    setStudyDataSessionId("");
    setStudyBusy(false);
    setStudyError("");
    setCardIndex(0);
    setCardFlipped(false);
    setCardStatus({});
  }

  const hasResult = Boolean(sessionId && result.source);

  const filteredHistoryItems = useMemo(() => {
  const search = historySearch.trim().toLowerCase();

  if (!search) return historyItems;

  return historyItems.filter((item) =>
    (item.title || "Untitled analysis")
      .toLowerCase()
      .includes(search)
  );
}, [historyItems, historySearch]);
  const sourceLabel = useMemo(() => {
    if (!result.source) return "No source loaded";
    if (result.sources?.length > 1) return `${result.sources.length} sources`;
    if (result.source.type === "youtube") return "YouTube";
    if (result.source.type === "pdf") return "PDF document";
    return "Web article";
  }, [result]);

  async function parseResponse(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "The server could not complete the request.");
    return body;
  }
  async function refreshHistory() {
    if (!selectedWorkspaceId) return;
  const response = await apiFetch(
    `/api/history?workspace_id=${encodeURIComponent(selectedWorkspaceId)}`);
  const data = await parseResponse(response);

  setHistoryItems(Array.isArray(data) ? data : []);
}

  async function refreshWorkspaces(preferredId = selectedWorkspaceId) {
  const response = await apiFetch("/api/workspaces");
  const data = await parseResponse(response);
  const items = Array.isArray(data) ? data : [];

  setWorkspaces(items);

  setSelectedWorkspaceId((current) => {
    const targetId = preferredId || current;

    if (items.some((item) => item.id === targetId)) {
      return targetId;
    }

    const defaultWorkspace = items.find(
      (item) => item.name === "My Workspace"
    );

    return defaultWorkspace?.id || items[0]?.id || "";
  });
}

async function createWorkspace() {
  const name = window.prompt("Enter a name for the new workspace:");
  if (name === null) return;

  if (!name.trim()) {
    setWorkspaceError("Workspace name cannot be empty.");
    return;
  }

  setWorkspaceError("");

  try {
    const response = await apiFetch("/api/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });

    const data = await parseResponse(response);
    await refreshWorkspaces(data.id);
  } catch (err) {
    setWorkspaceError(err.message || "Could not create workspace.");
  }
}

async function renameWorkspace() {
  const currentWorkspace = workspaces.find(
    (item) => item.id === selectedWorkspaceId
  );

  if (!currentWorkspace) return;

  const name = window.prompt(
    "Enter the new workspace name:",
    currentWorkspace.name
  );

  if (name === null) return;

  if (!name.trim()) {
    setWorkspaceError("Workspace name cannot be empty.");
    return;
  }

  setWorkspaceError("");

  try {
    const response = await apiFetch(
      `/api/workspaces/${encodeURIComponent(selectedWorkspaceId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      }
    );

    await parseResponse(response);
    await refreshWorkspaces(selectedWorkspaceId);
  } catch (err) {
    setWorkspaceError(err.message || "Could not rename workspace.");
  }
}

async function deleteWorkspace() {
  const currentWorkspace = workspaces.find(
    (item) => item.id === selectedWorkspaceId
  );

  if (!currentWorkspace) return;

  if (workspaces.length <= 1) {
    setWorkspaceError("You must keep at least one workspace.");
    return;
  }

  const confirmed = window.confirm(
    `Delete "${currentWorkspace.name}"? Its saved analyses will be moved to another workspace.`
  );

  if (!confirmed) return;

  setWorkspaceError("");

  try {
    const response = await apiFetch(
      `/api/workspaces/${encodeURIComponent(currentWorkspace.id)}`,
      { method: "DELETE" }
    );

    const data = await parseResponse(response);

    await refreshWorkspaces(data.fallback_workspace_id);
    resetWorkspace();
  } catch (err) {
    setWorkspaceError(err.message || "Could not delete workspace.");
  }
}

async function openHistoryItem(item) {
  resetStudyTools();
  setHistoryLoading(true);
  setHistoryError("");
  setError("");

  try {
    const response = await apiFetch(
      `/api/history/${encodeURIComponent(item.id)}`
    );

    const data = await parseResponse(response);
    if (data.workspace_id) {
  setSelectedWorkspaceId(data.workspace_id);
}
    // Restore the saved analysis and its session ID
    applyResult({
      ...(data.result || {}),
      session_id: data.session_id,
    });

    // Restore the previous conversation
    const restoredMessages = (data.qa_history || []).flatMap(
      (entry) => [
        {
          role: "user",
          content: entry.question,
        },
        {
          role: "assistant",
          content: entry.answer,
          evidence: entry.evidence || [],
        },
      ]
    );

    setMessages(restoredMessages);
    setComparison(data.comparison || null);
    setQuestion("");
    setUrl("");
    setFile(null);
    setLanguage("English");

  } catch (err) {
    setHistoryError(err.message || "Could not load saved analysis.");
  } finally {
    setHistoryLoading(false);
  }
}
function handleAuthenticated(data) {
  localStorage.setItem(
    "insightlens_access_token",
    data.access_token
  );

  setAuthUser(data.user);
}

function handleLogout() {
  localStorage.removeItem("insightlens_access_token");
  setAuthUser(null);
  resetWorkspace();
}

function applyResult(data) {
  const insights = data.insights || {};

  const normalizedResult = {
    ...data,
    source: data.source || data.sources?.[0] || null,
    sources: data.sources || (data.source ? [data.source] : []),
    summary: insights.summary || data.summary || "",
    key_points: (insights.key_points || data.key_points || []).map(
      (item) => typeof item === "string" ? { text: item } : item
    ),
    findings: (
      insights.main_findings ||
      data.findings ||
      data.main_findings ||
      []
    ).map(
      (item) => typeof item === "string" ? { text: item } : item
    ),
    concepts: (
      insights.important_concepts ||
      data.concepts ||
      data.important_concepts ||
      []
    ).map(
      (item) => typeof item === "string" ? { text: item } : item
    ),
    charts: data.charts || [],
    chunk_count: data.chunk_count || 0,
  };

  setResult(normalizedResult);

  if (data.session_id) {
    setSessionId(data.session_id);
  }
}

  async function analyze() {
    resetStudyTools();
    setBusy(true);
    setError("");
    setMessages([]);
    setComparison(null);
    setResult(emptyResult);
    setSessionId("");
    try {
      let response;
      if (mode === "url") {
        if (!url.trim()) throw new Error("Enter a URL first.");
        const form = new FormData();
        form.append("url", url.trim());
        if (selectedWorkspaceId) {
        form.append("workspace_id", selectedWorkspaceId);
    }
        response = await apiFetch("/api/analyze/url", { method: "POST", body: form });
      } else {
        if (!file) throw new Error("Choose a file first.");
        const form = new FormData();
        form.append("file", file);
        if (selectedWorkspaceId) {
          form.append("workspace_id", selectedWorkspaceId);
}
        const isPdf = file.name.toLowerCase().endsWith(".pdf");
        response = await apiFetch(
         isPdf ? "/api/analyze/pdf" : "/api/analyze/file",
          { method: "POST", body: form }
        );
      }
      const data = await parseResponse(response);
      if (data.workspace_id) {
          setSelectedWorkspaceId(data.workspace_id);
        }
      applyResult(data);
      await refreshHistory();
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function addSources() {
    if (!sessionId) return;
    const urls = compareUrls.split("\n").map((item) => item.trim()).filter(Boolean);
    if (!urls.length) {
      setError("Add at least one URL to compare.");
      return;
    }
    setCompareBusy(true);
    setError("");
    try {
      const response = await apiFetch("/api/add/url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, urls }),
      });
      applyResult(await parseResponse(response));
      setCompareUrls("");
      setComparison(null);
    } catch (err) {
      setError(err.message || "Could not add the comparison source.");
    } finally {
      setCompareBusy(false);
    }
  }


  async function addFileToWorkspace(event) {
    const selected = event.target.files?.[0];
    event.target.value = "";

    if (!selected || !sessionId) return;

    setCompareBusy(true);
    setError("");

    try {
      const form = new FormData();
      form.append("session_id", sessionId);
      form.append("file", selected);

      const response = await apiFetch("/api/add/file", {
        method: "POST",
        body: form,
      });

      applyResult(await parseResponse(response));
      setComparison(null);
    } catch (err) {
      setError(err.message || "Could not add the file.");
    } finally {
      setCompareBusy(false);
    }
  }

  async function compareSources() {
    if (!sessionId || result.sources?.length < 2) return;
    setCompareBusy(true);
    setError("");
    try {
      const response = await apiFetch("/api/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      setComparison(await parseResponse(response));
    } catch (err) {
      setError(err.message || "Could not compare the sources.");
    } finally {
      setCompareBusy(false);
    }
  }

  async function askQuestion(event) {
    event?.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || !sessionId || busy) return;
    setMessages((current) => [...current, { role: "user", content: trimmed }]);
    setQuestion("");
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, question: trimmed, language: language}),
      });
      const data = await parseResponse(response);
      setMessages((current) => [...current, { role: "assistant", content: data.answer, evidence: data.evidence || [] }]);
    } catch (err) {
      setMessages((current) => [...current, { role: "assistant", content: `I couldn't answer that: ${err.message}`, evidence: [] }]);
    } finally {
      setBusy(false);
    }
  }

  async function exportPdf() {
  if (!sessionId) {
    alert("Analyze a source before exporting.");
    return;
  }

  try {
    const formData = new FormData();
    formData.append("session_id", sessionId);

    const response = await apiFetch("/api/export/pdf", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || "PDF export failed.");
    }

    const pdfBlob = await response.blob();

    if (pdfBlob.type && !pdfBlob.type.includes("pdf")) {
      throw new Error("The server did not return a PDF file.");
    }

    const downloadUrl = window.URL.createObjectURL(pdfBlob);
    const link = document.createElement("a");

    link.href = downloadUrl;
    link.download = "InsightLens-Research-Report.pdf";

    document.body.appendChild(link);
    link.click();
    link.remove();

    window.URL.revokeObjectURL(downloadUrl);
  } catch (error) {
    console.error("PDF export error:", error);
    alert(`Could not export PDF: ${error.message}`);
  }
  }
  async function generateStudyTool(tool) {
    if (!sessionId || studyBusy) return;

    const requestedSessionId = sessionId;
    const requestId = ++studyRequestRef.current;

    setStudyTool(tool);
    setStudyBusy(true);
    setStudyError("");
    setStudyData(null);
    setCardIndex(0);
    setCardFlipped(false);
    setCardStatus({});
    try {
      const response = await apiFetch(`/api/study/${tool}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, language }),
      });
      const data = await parseResponse(response);

      if (requestId === studyRequestRef.current) {
        setStudyData(data);
        setStudyDataSessionId(requestedSessionId);
      }
    } catch (err) {
      if (requestId === studyRequestRef.current) {
        setStudyError(err.message || "Study tool generation failed.");
      }
    } finally {
      if (requestId === studyRequestRef.current) {
        setStudyBusy(false);
      }
    }
  }

  async function copyAnswer(content, index) {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedIndex(index);
      window.setTimeout(() => setCopiedIndex(null), 1400);
    } catch {
      setError("Clipboard access was blocked by the browser.");
    }
  }
  async function loadHistoryItem(analysisId) {
    resetStudyTools();
  setError("");
  setBusy(true);

  try {
    const response = await apiFetch(`/api/history/${analysisId}`);
    const data = await parseResponse(response);
    setSelectedWorkspaceId(data.workspace_id);

    applyResult({
      ...data.result,
      session_id: data.session_id,
    });

    const restoredMessages = (data.qa_history || []).flatMap((entry) => [
      {
        role: "user",
        content: entry.question,
      },
      {
        role: "assistant",
        content: entry.answer,
        evidence: entry.evidence || [],
      },
    ]);

    setMessages(restoredMessages);
    setComparison(data.comparison || null);
    setError("");
  } catch (err) {
    setError(err.message || "Could not load saved analysis.");
  } finally {
    setBusy(false);
  }
}
  async function renameHistoryItem(item) {
  const currentTitle = item.title || "Untitled analysis";
  const newTitle = window.prompt("Enter a new analysis title:", currentTitle);

  if (newTitle === null) return;

  const title = newTitle.trim();

  if (!title) {
    setError("Analysis title cannot be empty.");
    return;
  }

  try {
    const response = await apiFetch(`/api/history/${item.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });

    const data = await parseResponse(response);

    setHistoryItems((current) =>
      current.map((entry) =>
        entry.id === item.id
          ? { ...entry, title: data.title }
          : entry
      )
    );

    setError("");
  } catch (err) {
    setError(`Could not rename analysis: ${err.message}`);
  }
}

async function deleteHistoryItem(item) {
  const title = item.title || "Untitled analysis";

  const confirmed = window.confirm(
    `Permanently delete "${title}" and its saved history? This cannot be undone.`
  );

  if (!confirmed) return;

  try {
    const response = await apiFetch(`/api/history/${item.id}`, {
      method: "DELETE",
    });

    await parseResponse(response);

    setHistoryItems((current) =>
      current.filter((entry) => entry.id !== item.id)
    );

    if (sessionId === item.session_id) {
      resetWorkspace();
    }

    await refreshWorkspaces(selectedWorkspaceId);
    setError("");
  } catch (err) {
    setError(`Could not delete analysis: ${err.message}`);
  }
}
  function resetWorkspace() {
    resetStudyTools();
    setUrl("");
    setFile(null);
    setResult(emptyResult);
    setSessionId("");
    setQuestion("");
    setMessages([]);
    setError("");
    setComparison(null);
    setCompareUrls("");
  }
      // Show a loading screen while checking the login session
  if (authChecking) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <img src="/insightlens-logo.png" alt="InsightLens" className="brand-logo" />
          <h2>InsightLens</h2>
          <p>Checking your session...</p>
        </section>
      </main>
    );
  }

  // Show login/register screen if the user is not authenticated
  if (!authUser) {
    return (
      <Auth
        apiFetch={apiFetch}
        onAuthenticated={handleAuthenticated}
      />
    );
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img src="/insightlens-logo.png" alt="InsightLens" className="brand-logo" />
        </div>
        <button className="new-analysis" onClick={resetWorkspace}><Plus size={17} />New analysis</button>

        <div className="sidebar-section workspace-section">
  <div className="sidebar-label">WORKSPACES</div>

  <select
    className="workspace-select"
    value={selectedWorkspaceId}
    onChange={(event) => {
      setSelectedWorkspaceId(event.target.value);
      resetWorkspace();
    }}
    disabled={workspaceLoading || workspaces.length === 0}
  >
    {workspaces.map((workspace) => (
      <option key={workspace.id} value={workspace.id}>
        {workspace.name}
      </option>
    ))}
  </select>

  <div className="workspace-actions">
    <button onClick={createWorkspace} disabled={workspaceLoading}>
      <Plus size={14} /> New
    </button>

    <button
      onClick={renameWorkspace}
      disabled={!selectedWorkspaceId || workspaceLoading}
    >
      Rename
    </button>

    <button
      onClick={deleteWorkspace}
      disabled={!selectedWorkspaceId || workspaceLoading || workspaces.length <= 1}
    >
      Delete
    </button>
  </div>

  {workspaceError && (
    <div className="history-empty">{workspaceError}</div>
  )}
</div>
        <div className="sidebar-section">
          <div className="sidebar-label">SOURCE</div>
          <button className={`source-option ${mode === "url" ? "active" : ""}`} onClick={() => setMode("url")}><Globe size={17} /><span>URL or YouTube</span></button>
          <button className={`source-option ${mode === "pdf" ? "active" : ""}`} onClick={() => setMode("pdf")}><FileText size={17} /><span>PDF document</span></button>
        </div>

<div className="sidebar-section history-section">
  <div className="sidebar-label">RECENT ANALYSES</div>

  <div className="history-search">
    <Search size={15} />
    <input
      type="text"
      placeholder="Search analyses..."
      value={historySearch}
      onChange={(event) => setHistorySearch(event.target.value)}
      aria-label="Search saved analyses"
    />
    {historySearch && (
      <button
        type="button"
        className="history-search-clear"
        onClick={() => setHistorySearch("")}
        title="Clear search"
      >
        <X size={14} />
      </button>
    )}
  </div>

  {historyLoading && (
    <div className="history-empty">Loading history...</div>
  )}

  {!historyLoading && historyError && (
    <div className="history-empty">{historyError}</div>
  )}

  {!historyLoading && !historyError && historyItems.length === 0 && (
    <div className="history-empty">
      Your saved analyses will appear here.
    </div>
  )}

  {!historyLoading &&
    !historyError &&
    historyItems.length > 0 &&
    filteredHistoryItems.length === 0 && (
      <div className="history-empty">
        No analyses match your search.
      </div>
    )}

  {!historyLoading &&
    !historyError &&
    filteredHistoryItems.map((item) => (
      <div className="history-item-row" key={item.id}>
        <button
          type="button"
          className={`history-item ${
            sessionId === item.session_id ? "active" : ""
          }`}
          onClick={() => loadHistoryItem(item.id)}
          disabled={busy}
          title={item.title || "Untitled analysis"}
        >
          <FileText size={15} />
          <span>{item.title || "Untitled analysis"}</span>
        </button>

        <div className="history-item-actions">
          <button
            type="button"
            onClick={() => renameHistoryItem(item)}
            title="Rename analysis"
            aria-label={`Rename ${item.title || "analysis"}`}
            disabled={busy}
          >
            <Pencil size={14} />
          </button>

          <button
            type="button"
            onClick={() => deleteHistoryItem(item)}
            title="Delete analysis"
            aria-label={`Delete ${item.title || "analysis"}`}
            disabled={busy}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    ))}
</div>
       <div className="sidebar-bottom"><div className="system-note"><div className="system-dot" /><div><strong>InsightLens API</strong><span>RAG + evidence analysis</span></div></div></div>
      </aside>

      <main className="main">

<header className="topbar">
  <div className="mobile-brand">
    <img src="/insightlens-logo.png" alt="InsightLens" className="brand-logo" />
    <strong>InsightLens</strong>
  </div>

  <div className="topbar-actions">
    {hasResult && (
      <>
        <span className="source-pill">
          <span className="pill-dot" />
          {sourceLabel}
        </span>
        <button className="outline-button" onClick={exportPdf}>
          <Download size={16} />
          Export PDF
        </button>
      </>
    )}

    <span className="topbar-status">
      {authUser?.email}
    </span>

    <button className="outline-button" onClick={handleLogout}>
      <LogOut size={16} />
      Sign out
    </button>
  </div>
</header>


        <div className={`workspace ${hasResult ? "has-result" : ""}`}>
          {!hasResult ? (
            <section className="welcome">
              <div className="welcome-copy">
                <div className="eyebrow">MULTI-SOURCE RESEARCH</div>
                <h1>Understand the source.<br />See the evidence.</h1>
                <p>Analyze YouTube videos, web articles, research papers, and PDFs. InsightLens extracts structured findings, keeps source evidence, and answers questions from retrieved context.</p>
              </div>
              <div className="input-card">
                <div className="input-tabs">
                  <button className={mode === "url" ? "selected" : ""} onClick={() => setMode("url")}><Globe size={16} />URL / YouTube</button>
                  <button className={mode === "pdf" ? "selected" : ""} onClick={() => setMode("pdf")}><FileText size={16} />Upload File</button>
                </div>

{mode === "url" ? (
  <div className="url-input-row">
    <div className="input-icon">
      <Search size={18} />
    </div>

    <input
      value={url}
      onChange={(event) => setUrl(event.target.value)}
      onKeyDown={(event) => event.key === "Enter" && analyze()}
      placeholder="Paste a YouTube, article, or research URL"
      autoComplete="off"
    />

    <button className="orange-button" onClick={analyze} disabled={busy}>
      {busy ? (
        <>
          <Loader2 size={16} className="spin" />
          Analyzing
        </>
      ) : (
        <>
          Analyze
          <ArrowUp size={16} />
        </>
      )}
    </button>
  </div>
) : (
  <div className="upload-area">
    <label className="file-picker">
      <Upload size={20} />

      <strong>{file ? file.name : "Choose a File"}</strong>

      <span>
        {file
          ? `${(file.size / 1024 / 1024).toFixed(2)} MB`
          : "PDF, DOCX, TXT, CSV, XLSX, PPTX, Markdown and code files · Up to 20 MB"}
      </span>

      <input
        type="file"
accept=".pdf,.docx,.txt,.md,.xlsx,.pptx,.c,.cpp,.cs,.css,.go,.h,.hpp,.html,.ipynb,.java,.js,.json,.jsx,.kt,.kts,.php,.ps1,.py,.rb,.rs,.scss,.sh,.sql,.swift,.ts,.tsx,.xml,.yaml,.yml"        onChange={(event) => setFile(event.target.files?.[0] || null)}
      />
    </label>

    <button className="orange-button" onClick={analyze} disabled={busy}>
      {busy ? (
        <>
          <Loader2 size={16} className="spin" />
          Analyzing
        </>
      ) : (
        <>
          Analyze File
          <ArrowUp size={16} />
        </>
      )}
    </button>
  </div>
)}
              </div>
              {error && <div className="error-banner">{error}</div>}
            </section>
          ) : (
            <>
              <div className="result-layout">
                <section className="source-column">
                  <div className="source-header"><div><div className="eyebrow">ANALYZED SOURCES</div><h2>{result.sources?.length > 1 ? `${result.sources.length} sources in workspace` : result.source.name}</h2></div><button className="icon-button" onClick={resetWorkspace}><X size={17} /></button></div>

                  {result.source.type === "youtube" && result.source.video_id && <YouTubePanel source={result.source} />}

                  <div className="source-meta"><div className="meta-item"><span>Sources</span><strong>{result.sources?.length || 1}</strong></div><div className="meta-item"><span>Searchable chunks</span><strong>{result.chunk_count}</strong></div></div>

                  <div className="source-list">
                    {(result.sources || [result.source]).map((source, index) => <SourceRow key={`${source.name}-${index}`} source={source} />)}
                  </div>

                  <div className="source-actions"><button className="outline-button" onClick={exportPdf}><Download size={16} />Download full report</button></div>

<ComparisonPanel
  compareUrls={compareUrls}
  setCompareUrls={setCompareUrls}
  addSources={addSources}
  addFileToWorkspace={addFileToWorkspace}
  compareSources={compareSources}
  compareBusy={compareBusy}
  sourceCount={result.sources?.length || 1}
/>                </section>

                <section className="insights-column">

<div className="insights-heading">
  <div>
    <div className="eyebrow">SOURCE INSIGHTS</div>
    <h2>Research overview</h2>
  </div>

  <label className="language-control">
    <span>Output language</span>
    <select
      className="language-select"
      value={language}
      onChange={(event) => changeLanguage(event.target.value)}
      disabled={translating || busy}
    >
      <option value="English">English</option>
      <option value="Telugu">తెలుగు (Telugu)</option>
      <option value="Hindi">हिन्दी (Hindi)</option>
      <option value="Tamil">தமிழ் (Tamil)</option>
      <option value="Kannada">ಕನ್ನಡ (Kannada)</option>
      <option value="Malayalam">മലയാളം (Malayalam)</option>
      <option value="Spanish">Spanish</option>
      <option value="French">French</option>
      <option value="German">German</option>
    </select>
  </label>
</div>

                  <InsightCard title="Executive summary" text={result.summary} />
                  <EvidenceListCard title="Key points" items={result.key_points} />
                  <EvidenceListCard title="Main findings" items={result.findings} confidence />
                  <EvidenceListCard title="Important concepts" items={result.concepts} />
                  {comparison && <ComparisonResult comparison={comparison} />}
                  {error && <div className="error-banner">{error}</div>}
                </section>
              </div>

              <section className="chat-section">
                <div className="chat-heading"><div><div className="eyebrow">GROUNDED Q&A</div><h2>Ask about the evidence</h2></div><MessageSquare size={18} /></div>
                <div className="chat-box">
                  {messages.length === 0 ? <div className="chat-empty"><div className="chat-empty-icon"><MessageSquare size={18} /></div><div><strong>Ask a question about the analyzed sources.</strong><span>Answers include source evidence when the retrieved context supports them.</span></div></div> : <div className="messages">{messages.map((message, index) => <Message key={`${message.role}-${index}`} message={message} index={index} copiedIndex={copiedIndex} copyAnswer={copyAnswer} />)}</div>}
                  <form className="chat-input-row" onSubmit={askQuestion}><input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask anything about the analyzed sources..." disabled={busy} /><button className="send-button" type="submit" disabled={!question.trim() || busy}>{busy ? <Loader2 size={17} className="spin" /> : <ArrowUp size={17} />}</button></form>
                </div>
              </section>

              <section className="study-tools-section">
                <div className="study-tools-heading">
                  <div>
                    <div className="eyebrow">SOURCE-GROUNDED STUDY TOOLS</div>
                    <h2>Study Tools</h2>
                    <p>Generate study material from the sources you analyzed. Each tool runs only when you request it.</p>
                  </div>
                </div>
                <div className="study-tool-buttons">
                  <button className="outline-button" disabled={!sessionId || studyBusy} onClick={() => generateStudyTool("notes")}><FileText size={16}/> Detailed Notes</button>
                  <button className="outline-button" disabled={!sessionId || studyBusy} onClick={() => generateStudyTool("flashcards")}><Clipboard size={16}/> Flashcards</button>
                  <button className="outline-button" disabled={!sessionId || studyBusy} onClick={() => generateStudyTool("diagram")}><BarChart3 size={16}/> Flowchart / Diagram</button>
                </div>
                {studyBusy && <div className="study-loading"><Loader2 size={17} className="spin"/> Generating {studyTool} from analyzed sources...</div>}
                {studyError && <div className="error-banner">{studyError}</div>}
                {studyData && studyDataSessionId === sessionId && studyTool === "notes" && studyData.content && <div className="study-output study-notes"><ReactMarkdown remarkPlugins={[remarkGfm]}>{studyData.content}</ReactMarkdown></div>}
                {studyData && studyDataSessionId === sessionId && studyTool === "flashcards" && Array.isArray(studyData.cards) && studyData.cards.length > 0 && <div className="study-flashcards">
                  <div className="flashcard-count">Card {cardIndex+1} of {studyData.cards.length}</div>
                  <button className="flashcard" onClick={() => setCardFlipped(v=>!v)} aria-label="Flip flashcard">
                    <div className="flashcard-label">{cardFlipped ? "ANSWER" : "QUESTION"}</div>
                    <div className="flashcard-content">{cardFlipped ? studyData.cards[cardIndex].answer : studyData.cards[cardIndex].question}</div>
                    <div className="flashcard-hint">Click to flip</div>
                  </button>
                  <div className="flashcard-actions">
                    <button className="outline-button" disabled={cardIndex===0} onClick={()=>{setCardIndex(i=>i-1);setCardFlipped(false)}}>Previous</button>
                    <button className="outline-button" onClick={()=>setCardStatus(s=>({...s,[cardIndex]:"difficult"}))}>Mark difficult</button>
                    <button className="outline-button" onClick={()=>setCardStatus(s=>({...s,[cardIndex]:"known"}))}>I know this</button>
                    <button className="outline-button" disabled={cardIndex===studyData.cards.length-1} onClick={()=>{setCardIndex(i=>i+1);setCardFlipped(false)}}>Next</button>
                  </div>
                  {cardStatus[cardIndex] && <div className="flashcard-status">Marked {cardStatus[cardIndex]}</div>}
                </div>}
                {studyData && studyDataSessionId === sessionId && studyTool === "diagram" && studyData.mermaid && <div className="study-output"><p>{studyData.description}</p><MermaidDiagram chart={studyData.mermaid} /><details><summary>Diagram source</summary><pre className="mermaid-source">{studyData.mermaid}</pre></details></div>}
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function YouTubePanel({ source }) {
  const videoId = source?.video_id;

  const thumbnailUrl =
    source?.thumbnail_url ||
    (videoId ? `https://img.youtube.com/vi/${videoId}/hqdefault.jpg` : null);

  const watchUrl =
    source?.watch_url ||
    (videoId
      ? `https://www.youtube.com/watch?v=${videoId}`
      : source?.url);

  return (
    <div className="youtube-panel">
      <div className="youtube-preview">
        {thumbnailUrl ? (
          <a
            href={watchUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="youtube-thumbnail-link"
            aria-label="Watch analyzed YouTube video"
          >
            <img
              src={thumbnailUrl}
              alt={source?.title || "Analyzed YouTube video"}
              className="youtube-thumbnail"
              onError={(event) => {
                event.currentTarget.style.display = "none";
              }}
            />

            <span className="youtube-play-button">
              <svg
                viewBox="0 0 24 24"
                aria-hidden="true"
                fill="currentColor"
              >
                <path d="M8 5v14l11-7z" />
              </svg>
            </span>
          </a>
        ) : (
          <div className="youtube-thumbnail-fallback">
            Video preview unavailable
          </div>
        )}
      </div>

      <div className="youtube-panel-footer">
        <div className="youtube-source-info">
          <strong>{source?.title || source?.name || "YouTube source"}</strong>
          <span>
            {source?.channel
              ? `${source.channel} · Transcript processed`
              : "Transcript processed"}
          </span>
        </div>

        {watchUrl && (
          <a
            href={watchUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="youtube-open-link"
          >
            <span>▶</span> Open in YouTube
          </a>
        )}
      </div>
    </div>
  );
}

function SourceRow({ source }) {
  return <div className="source-row"><div className={`source-type ${source.type}`}>{source.type === "youtube" ? <Play size={13} /> : source.type === "pdf" ? <FileText size={13} /> : <Globe size={13} />}</div><div className="source-row-text"><strong>{source.name}</strong><span>{source.type}{source.video_id ? " · timestamp evidence available" : ""}</span></div>{source.url && <a href={source.url} target="_blank" rel="noreferrer">Open</a>}</div>;
}


function ComparisonPanel({
  compareUrls,
  setCompareUrls,
  addSources,
  addFileToWorkspace,
  compareSources,
  compareBusy,
  sourceCount,
}) {
  return (
    <article className="insight-card comparison-panel">
      <div className="card-heading">
        <div>
          <h3>Compare sources</h3>
          <p className="card-subtitle">
            Add another URL or file, then compare the evidence.
          </p>
        </div>
        <span>{sourceCount}</span>
      </div>

      <textarea
        value={compareUrls}
        onChange={(event) => setCompareUrls(event.target.value)}
        placeholder="Paste one or more URLs, one per line"
      />

      <div className="comparison-actions">
        <button
          className="orange-button small"
          onClick={addSources}
          disabled={compareBusy}
        >
          <Plus size={14} />
          Add URLs
        </button>

        <label className="outline-button small-file">
          <Upload size={14} />
          Add File
          <input
            type="file"
accept=".pdf,.docx,.txt,.md,.xlsx,.pptx,.c,.cpp,.cs,.css,.go,.h,.hpp,.html,.ipynb,.java,.js,.json,.jsx,.kt,.kts,.php,.ps1,.py,.rb,.rs,.scss,.sh,.sql,.swift,.ts,.tsx,.xml,.yaml,.yml"            onChange={addFileToWorkspace}
          />
        </label>

        {sourceCount > 1 && (
          <button
            className="outline-button"
            onClick={compareSources}
            disabled={compareBusy}
          >
            {compareBusy ? (
              <Loader2 size={14} className="spin" />
            ) : (
              <BarChart3 size={14} />
            )}
            Compare
          </button>
        )}
      </div>
    </article>
  );
}


function InsightCard({ title, text }) {
  return <article className="insight-card"><div className="card-heading"><h3>{title}</h3></div><div className="summary-text">{text.split("\n").map((paragraph, index) => paragraph.trim() ? <p key={index}>{paragraph}</p> : null)}</div></article>;
}

function EvidenceListCard({ title, items, confidence }) {
  return <article className="insight-card"><div className="card-heading"><h3>{title}</h3><span>{items.length}</span></div>{items.length ? <div className="evidence-list">{items.map((item, index) => <div className="evidence-item" key={`${title}-${index}`}><div className="evidence-text">{item.text}</div>{confidence && item.confidence != null && <div className="confidence">Confidence {Math.round(item.confidence * 100)}%</div>}<EvidenceLinks ids={item.evidence || []} details={item.evidence_details || []} /></div>)}</div> : <p className="muted">No separate items were extracted.</p>}</article>;
}

function EvidenceLinks({ ids, details = [] }) {
  if (!ids.length) return null;
  const detailById = Object.fromEntries(details.map((item) => [item.id, item]));
  return <div className="evidence-links">{ids.map((id) => {
    const detail = detailById[id];
    const href = detail?.timestamp_url || detail?.source;
    const location = detail?.timestamp ? ` · ${detail.timestamp}` : detail?.page != null ? ` · page ${Number(detail.page) + 1}` : "";
    return href ? <a key={id} className="evidence-chip" href={href} target="_blank" rel="noreferrer" title={detail?.excerpt || "Source evidence"}>{id}{location}</a> : <span key={id} className="evidence-chip">{id}</span>;
  })}</div>;
}

function VisualizationCard({ charts }) {
  if (!charts?.length) return <article className="insight-card visualization-card"><div className="card-heading"><h3>Automatic visualization</h3></div><p className="muted">No reliable numeric dataset was detected in this source.</p></article>;
  const chart = charts[0];
  return <article className="insight-card visualization-card"><div className="card-heading"><div><h3>{chart.title}</h3><p className="card-subtitle">Automatically detected from source data</p></div><BarChart3 size={17} /></div><MiniChart chart={chart} /></article>;
}

function MiniChart({ chart }) {
  const values = chart.data.map((item) => item.value);
  const max = Math.max(...values, 1);
  return <div className="mini-chart">{chart.data.map((item) => <div className="chart-row" key={item.label}><span>{item.label}</span><div className="chart-track"><div className="chart-bar" style={{ width: `${Math.max(4, (item.value / max) * 100)}%` }} /></div><strong>{formatNumber(item.value)}</strong></div>)}</div>;
}

function formatNumber(value) {
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function ComparisonResult({ comparison }) {
  return <article className="insight-card comparison-result"><div className="card-heading"><h3>Source comparison</h3></div><p className="summary-text">{comparison.overview}</p><ComparisonSection title="Where sources agree" items={comparison.agreements} /><ComparisonSection title="Key differences" items={comparison.differences} /><ComparisonSection title="Missing information" items={comparison.missing_information} /></article>;
}

function ComparisonSection({ title, items }) {
  if (!items?.length) return null;
  return <div className="comparison-section"><strong>{title}</strong><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
}


function Message({ message, index, copiedIndex, copyAnswer }) {
  return (
    <div className={`message-row ${message.role}`}>
      <div className="message-bubble">
        <div className="message-role">
          {message.role === "user" ? "You" : "InsightLens"}
        </div>

        <div className="message-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {String(message.content ?? "")}
          </ReactMarkdown>
        </div>

        {message.role === "assistant" && (
          <>
            <button
              className="copy-button"
              onClick={() => copyAnswer(message.content, index)}
              title="Copy answer"
            >
              {copiedIndex === index ? (
                <Check size={14} />
              ) : (
                <Clipboard size={14} />
              )}
            </button>

            {message.evidence?.length > 0 && (
              <div className="answer-evidence">
                <span>Evidence</span>
                {message.evidence.map((item) => (
                  <a
                    key={item.id}
                    href={item.timestamp_url || item.source}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {item.id}
                    {item.timestamp
                      ? ` · ${item.timestamp}`
                      : item.page != null
                      ? ` · page ${Number(item.page) + 1}`
                      : ""}
                  </a>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default App;
